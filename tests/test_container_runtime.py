"""A daemon-backed child must terminate before any returned bytes are accepted."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sandbox_process import ProcessOutput

IMAGE = 'registry.invalid/gate@sha256:' + 'a' * 64
CREATED = ProcessOutput(0, b'b'*64 + b'\n', b'', False, None)


class ContainerRuntimeTests(unittest.TestCase):
    def test_uncertain_lifecycle_prevents_new_execution_on_same_runtime(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            runtime=container_runtime.DockerRuntime(IMAGE)
            with mock.patch.object(container_runtime.sandbox_process,'run',return_value=ProcessOutput(-9,b'',b'create timeout',False,'timeout')) as execute, \
                 mock.patch.object(container_runtime.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
                first=runtime.run(Path(tmp),['/bin/true'])
                second=runtime.run(Path(tmp),['/bin/true'])
            self.assertEqual(first.problem,'container_creation_indeterminate')
            self.assertEqual(second,first)
            self.assertEqual(execute.call_count,1)

    def test_command_exposes_only_readonly_inputs_and_bounded_private_storage(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();compiler=Path(tmp)/'compiler';compiler.mkdir()
            runtime=container_runtime.DockerRuntime(IMAGE)
            command=runtime.command(source,['/bin/true'],name='gate-fixture',toolchain=compiler)
            self.assertEqual(command[:4],['/usr/bin/docker','--host','unix:///var/run/docker.sock','run'])
            for flag in ('--read-only','--rm','--init'):
                self.assertIn(flag,command)
            for flag,value in [('--network','none'),('--pull','never'),('--log-driver','none'),
                               ('--cap-drop','ALL'),('--security-opt','no-new-privileges'),('--pids-limit','256')]:
                self.assertEqual(command[command.index(flag)+1],value)
            mounts=[command[i+1] for i,x in enumerate(command) if x=='--mount']
            self.assertEqual(len(mounts),2)
            self.assertTrue(all(x.endswith(',readonly') for x in mounts))
            self.assertFalse(any('/var/run/docker.sock' in x for x in mounts))
            self.assertIn(IMAGE,command)

    def test_output_is_discarded_when_container_termination_cannot_be_verified(self):
        import container_runtime
        for status,remaining in ((0,b'container-id\n'),(1,b'')):
            with self.subTest(status=status,remaining=remaining),tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(container_runtime.sandbox_process,'run',side_effect=[CREATED,ProcessOutput(0,b'forged finals',b'tail',False,None)]), \
                     mock.patch.object(container_runtime.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],status,remaining,b'')]):
                    result=container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/true'])
                self.assertEqual(result.stdout,b'')
                self.assertEqual(result.problem,'container_cleanup_unverified')

    def test_failed_child_cannot_be_accepted_after_successful_cleanup(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(container_runtime.sandbox_process,'run',side_effect=[CREATED,ProcessOutput(7,b'forged finals',b'failed',False,None)]), \
                 mock.patch.object(container_runtime.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
                result=container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/false'])
            self.assertEqual(result.returncode,7)
            self.assertEqual(result.stdout,b'')
            self.assertEqual(result.stderr,b'failed')

    def test_unpinned_images_and_mount_option_injection_are_rejected(self):
        import container_runtime
        with self.assertRaises(ValueError):container_runtime.DockerRuntime('registry.invalid/gate:latest')
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source,src=other';source.mkdir()
            with self.assertRaises(ValueError):container_runtime.DockerRuntime(IMAGE).command(source,['/bin/true'],name='gate-fixture')

    def test_cleanup_timeout_retains_owned_identity_without_accepting_output(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(container_runtime.uuid, 'uuid4', return_value=mock.Mock(hex='a'*32)), \
                 mock.patch.object(container_runtime.sandbox_process, 'run', side_effect=[CREATED,ProcessOutput(0,b'forged',b'tail',False,None)]), \
                 mock.patch.object(container_runtime.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker',15)):
                result=container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/true'])
            self.assertEqual(result.stdout,b'')
            self.assertEqual(result.problem,'container_cleanup_unverified')
            self.assertIn(b'gate-' + b'a'*32,result.stderr)

    def test_indeterminate_creation_cannot_start_or_claim_cleanup_from_empty_listing(self):
        import container_runtime
        for created in (ProcessOutput(-9,b'',b'create timeout',False,'timeout'),
                        ProcessOutput(0,b'not-an-id',b'',False,None)):
            with self.subTest(created=created),tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(container_runtime.sandbox_process,'run',return_value=created) as execute, \
                     mock.patch.object(container_runtime.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
                    result=container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/true'])
                self.assertEqual(result.problem,'container_creation_indeterminate')
                self.assertEqual(result.stdout,b'')
                self.assertEqual(execute.call_count,1)
                self.assertEqual(execute.call_args.args[0][3],'create')
                self.assertIn(b'Unverified owned container: gate-',result.stderr)

    def test_execution_and_removal_use_acknowledged_container_id(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(container_runtime.sandbox_process,'run',side_effect=[CREATED,ProcessOutput(0,b'finals',b'',False,None)]) as execute, \
                 mock.patch.object(container_runtime.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as cleanup:
                result=container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/true'])
            self.assertEqual(result.stdout,b'finals')
            self.assertEqual(execute.call_args_list[1].args[0][-3:],['start','--attach','b'*64])
            self.assertEqual(cleanup.call_args_list[0].args[0][-3:],['rm','--force','b'*64])

    def test_invalid_capture_options_do_not_create_container(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(container_runtime.sandbox_process,'run') as execute:
                with self.assertRaises(ValueError):
                    container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/true'],max_stdout_bytes=0)
                self.assertEqual(execute.call_count,0)

    def test_execution_exception_with_failed_cleanup_retains_recovery_identity(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(container_runtime.sandbox_process,'run',side_effect=[CREATED,ValueError('capture failure')]), \
                 mock.patch.object(container_runtime.subprocess,'run',side_effect=OSError('daemon unavailable')):
                result=container_runtime.DockerRuntime(IMAGE).run(Path(tmp),['/bin/true'])
            self.assertEqual(result.stdout,b'')
            self.assertEqual(result.problem,'container_cleanup_unverified')
            self.assertIn(b'Unverified owned container: gate-',result.stderr)
