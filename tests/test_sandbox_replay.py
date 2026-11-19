"""A simulator cannot publish bytes after failure or see controller output."""
from pathlib import Path
import sys,tempfile,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from sandbox_process import ProcessOutput

class ReplayBoundary(unittest.TestCase):
    def test_selected_runtime_receives_only_copied_inputs_and_bounded_private_output(self):
        import sandbox_replay
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('sim','config','gcode'):(root/name).write_bytes(name.encode())
            def execute(source, arguments, **options):
                self.assertEqual(sorted(p.name for p in source.iterdir()),['config.txt','input.gcode','simulator'])
                self.assertEqual((source/'config.txt').read_bytes(),b'config')
                self.assertEqual(options['workspace_bytes'],134217728)
                self.assertEqual(options['max_stdout_bytes'],67108864)
                return ProcessOutput(7,b'forged',b'failed',False,None)
            runtime=mock.Mock();runtime.run.side_effect=execute
            result=sandbox_replay.run(root/'sim',root/'config',root/'gcode',[],timeout=5,scratch_parent=root,runtime=runtime)
            self.assertEqual(result.returncode,7)
            self.assertEqual(result.stdout,b'')

    def test_failure_preserves_exit_and_cannot_return_forged_json(self):
        import sandbox_replay
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('sim','config','gcode'):(root/name).write_bytes(b'fixture')
            with mock.patch.object(sandbox_replay.sandbox_process,'run',return_value=ProcessOutput(7,b'{"pass":true}',b'failed',False,None)):
                result=sandbox_replay.run(root/'sim',root/'config',root/'gcode',[],timeout=5,scratch_parent=root)
            self.assertEqual(result.returncode,7)
            self.assertEqual(result.stdout,b'')
            self.assertEqual(result.stderr,b'failed')
            self.assertEqual(sorted(p.name for p in root.iterdir()),['config','gcode','sim'])

    def test_capture_error_discards_partial_result(self):
        import sandbox_replay
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('sim','config','gcode'):(root/name).write_bytes(b'fixture')
            with mock.patch.object(sandbox_replay.sandbox_process,'run',return_value=ProcessOutput(-9,b'partial',b'tail',False,'stdout_limit')):
                result=sandbox_replay.run(root/'sim',root/'config',root/'gcode',[],timeout=5,scratch_parent=root)
            self.assertEqual(result.stdout,b'')
            self.assertEqual(result.problem,'stdout_limit')

    def test_input_link_is_rejected_without_following_it(self):
        import sandbox_replay
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'sim').write_bytes(b'fixture');(root/'gcode').write_bytes(b'fixture')
            (root/'config').symlink_to(root/'gcode')
            with self.assertRaises(ValueError):
                sandbox_replay.run(root/'sim',root/'config',root/'gcode',[],timeout=5,scratch_parent=root)
