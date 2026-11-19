"""The build sandbox exposes only deliberate runtime, source and output mounts."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox


class SandboxContract(unittest.TestCase):
    def test_memory_workspace_mounts_only_explicit_legacy_compiler_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; source.mkdir()
            runtime = root / 'i386'; runtime.mkdir()
            names = ('ld-linux.so.2', 'libc.so.6', 'libdl.so.2', 'libm.so.6')
            for name in names: (runtime / name).write_bytes(b'fixture')
            command = sandbox.memory_command(source, ['/bin/true'], i386_runtime=runtime)
            groups = [command[i:i+3] for i in range(len(command))]
            for name in names:
                self.assertIn(['--ro-bind', str(runtime / name), '/lib/' + name], groups)
            self.assertIn(['--ro-bind', '/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu'], groups)
            self.assertNotIn(['--symlink', 'usr/lib', '/lib'], groups)
            self.assertNotIn('--bind', command)
            (runtime / 'libm.so.6').unlink()
            with self.assertRaises(ValueError):
                sandbox.memory_command(source, ['/bin/true'], i386_runtime=runtime)
            with self.assertRaises(ValueError):
                sandbox.memory_command(source, ['/bin/true'], i386_runtime=source)

    def test_memory_workspace_exposes_no_host_output_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'; source.mkdir()
            command = sandbox.memory_command(source, ['/bin/true'], workspace_bytes=16777216)
            self.assertIn(['--size', '16777216', '--tmpfs'],
                          [command[i:i+3] for i in range(len(command))])
            self.assertIn(['--tmpfs', '/output'], [command[i:i+2] for i in range(len(command))])
            self.assertNotIn('--bind', command)
            self.assertEqual(command[-2:], ['--', '/bin/true'])
            for size in (True, 0, -1, 8 * 1024**3 + 1):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    sandbox.memory_command(source, ['/bin/true'], workspace_bytes=size)

    def test_controller_has_private_bounded_workspace_and_read_only_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, controller = (root / name for name in ('source', 'output', 'controller'))
            for path in (source, output, controller): path.mkdir()
            command = sandbox.controller_command(source, output, controller,
                ['/usr/bin/python3', '-I', '/controller/driver.py'], workspace_bytes=16777216)
            groups = [command[i:i+3] for i in range(len(command))]
            self.assertIn(['--ro-bind', str(controller), '/controller'], groups)
            self.assertIn(['--size', '16777216', '--tmpfs'], groups)
            self.assertIn(['--tmpfs', '/workspace'], [command[i:i+2] for i in range(len(command))])
            self.assertEqual(command[-3:], ['/usr/bin/python3', '-I', '/controller/driver.py'])

    def test_controller_rejects_overlaps_aliases_and_invalid_workspace_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, controller = (root / name for name in ('source', 'output', 'controller'))
            for path in (source, output, controller): path.mkdir()
            alias = root / 'alias'; alias.symlink_to(controller)
            for code, size in ((source, 1024), (alias, 1024), (controller, True),
                               (controller, 0), (controller, 8 * 1024**3 + 1)):
                with self.subTest(code=code, size=size), self.assertRaises(ValueError):
                    sandbox.controller_command(source, output, code, ['/bin/true'], workspace_bytes=size)

    def test_pinned_toolchain_is_mounted_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, compiler = (root / name for name in ('src', 'out', 'compiler'))
            for path in (source, output, compiler):
                path.mkdir()
            command = sandbox.build_command(source, output, ['/bin/true'], toolchain=compiler)
            self.assertIn(['--ro-bind', str(compiler), '/toolchain'],
                          [command[i:i+3] for i in range(len(command))])
            self.assertIn('/toolchain/bin:/usr/bin:/bin', command)
            with self.assertRaises(ValueError):
                sandbox.build_command(source, output, ['/bin/true'], toolchain=output)

    def test_resource_wrapper_uses_whole_service_limits(self):
        command = sandbox.limit_command(['/usr/bin/bwrap', '--help'],
                                        memory_bytes=268435456, tasks=32,
                                        cpu_percent=200, runtime_seconds=30)
        self.assertEqual(command[:5], ['/usr/bin/systemd-run', '--user', '--wait', '--pipe', '--collect'])
        self.assertIn('--same-dir', command)  # Relative checker/case paths must use the dispatcher snapshot.
        for setting in ('MemoryMax=268435456', 'TasksMax=32', 'CPUQuota=200%', 'RuntimeMaxSec=30'):
            self.assertIn('--property=' + setting, command)
        self.assertEqual(command[-3:], ['--', '/usr/bin/bwrap', '--help'])

    def test_resource_limits_reject_unbounded_or_invalid_values(self):
        for limits in ({'memory_bytes': 0}, {'tasks': True}, {'cpu_percent': 1601}, {'runtime_seconds': -1}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                sandbox.limit_command(['/bin/true'], **limits)

    def test_command_keeps_collector_and_environment_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'; source.mkdir()
            output = Path(tmp) / 'output'; output.mkdir()
            command = sandbox.build_command(source, output, ['/usr/bin/make', '-j2'])
            self.assertEqual(command[0], '/usr/bin/bwrap')
            self.assertIn('--unshare-all', command)
            self.assertIn('--clearenv', command)
            self.assertIn('--cap-drop', command)
            self.assertEqual(command[-2:], ['/usr/bin/make', '-j2'])
            self.assertIn(['--ro-bind', str(source), '/source'],
                          [command[i:i+3] for i in range(len(command))])
            self.assertIn(['--bind', str(output), '/output'],
                          [command[i:i+3] for i in range(len(command))])
            home = os.environ.get('HOME', '/home')
            self.assertNotIn(['--ro-bind', home, home],
                             [command[i:i+3] for i in range(len(command))])
            self.assertNotIn(['--bind', home, home],
                             [command[i:i+3] for i in range(len(command))])

    def test_overlapping_mounts_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp); output = source / 'out'; output.mkdir()
            with self.assertRaises(ValueError):
                sandbox.build_command(source, output, ['/bin/true'])

    def test_symlink_mount_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; source.mkdir()
            alias = root / 'alias'; alias.symlink_to(source)
            output = root / 'out'; output.mkdir()
            with self.assertRaises(ValueError):
                sandbox.build_command(alias, output, ['/bin/true'])
