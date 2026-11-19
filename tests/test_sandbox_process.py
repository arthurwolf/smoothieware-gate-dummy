"""Exercise bounded process capture using actual isolated child processes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox_process


class BoundedProcess(unittest.TestCase):
    def run_child(self, source, **limits):
        return sandbox_process.run([sys.executable, '-I', '-c', source], **limits)

    def test_preserves_binary_output_diagnostics_and_failure_exit(self):
        result = self.run_child("import os; os.write(1,b'\\x00\\xfffinal'); os.write(2,b'compiler error'); raise SystemExit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b'\x00\xfffinal')
        self.assertEqual(result.stderr, b'compiler error')
        self.assertIsNone(result.problem)

    def test_stdout_flood_is_stopped_at_the_bound(self):
        result = self.run_child("import os,time; os.write(1,b'x'*1048576); time.sleep(30)",
                                max_stdout_bytes=1024, timeout_seconds=2)
        self.assertEqual(result.problem, 'stdout_limit')
        self.assertNotEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout), 1024)

    def test_stderr_flood_keeps_only_a_bounded_tail(self):
        result = self.run_child("import os; os.write(2,b'x'*1048576+b'END'); os.write(1,b'final')",
                                max_stderr_bytes=1024)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'final')
        self.assertEqual(len(result.stderr), 1024)
        self.assertTrue(result.stderr.endswith(b'END'))
        self.assertTrue(result.stderr_truncated)

    def test_timeout_stops_the_owned_process_group(self):
        result = self.run_child('import time; time.sleep(30)', timeout_seconds=0.1)
        self.assertEqual(result.problem, 'timeout')
        self.assertNotEqual(result.returncode, 0)

    def test_timeout_stops_descendants_and_preserves_an_independent_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaked, protected = root / 'leaked', root / 'protected'
            independent_code = ('import time; from pathlib import Path; time.sleep(1.5); '
                                'Path(' + repr(str(protected)) + ').write_text("alive")')
            independent = subprocess.Popen([sys.executable, '-I', '-c', independent_code],
                                           start_new_session=True)
            try:
                descendant_code = ('import time; from pathlib import Path; time.sleep(1); '
                                   'Path(' + repr(str(leaked)) + ').write_text("escaped")')
                parent_code = ('import subprocess,sys,time; subprocess.Popen([sys.executable,"-I","-c",'
                               + repr(descendant_code) + ']); time.sleep(30)')
                result = self.run_child(parent_code, timeout_seconds=0.3)
                self.assertEqual(result.problem, 'timeout')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(independent.wait(timeout=5), 0)
                self.assertEqual(protected.read_text(), 'alive')
                self.assertFalse(leaked.exists(), 'sandbox descendant survived its process group')
            finally:
                if independent.poll() is None:
                    independent.kill()
                    independent.wait()

    def test_invalid_limits_and_commands_are_rejected_before_launch(self):
        for limits in ({'max_stdout_bytes': True}, {'max_stderr_bytes': 0},
                       {'timeout_seconds': float('nan')}, {'timeout_seconds': -1}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.run_child('pass', **limits)
        for command in ([], 'echo unsafe', ['/bin/true', None]):
            with self.subTest(command=command), self.assertRaises(ValueError):
                sandbox_process.run(command)
