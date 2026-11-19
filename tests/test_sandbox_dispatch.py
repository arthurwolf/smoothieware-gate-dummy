"""The remote command snapshots trusted helpers separately from firmware."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox_dispatch


class SandboxDispatch(unittest.TestCase):
    def test_v1_dispatch_uses_host_runtime_without_nesting_legacy_wrapper(self):
        command = sandbox_dispatch.command('/tree', '/out', 'Debug', 'gcc48', 4, False,
                                           controller='/trusted/scripts', profile='v1-lpc1768', measure_only=True)
        self.assertEqual(command[command.index('--toolchain') + 1], 'gcc12')
        script = command[command.index('-c') + 1]
        self.assertIn('--profile v1-lpc1768', script)
        self.assertIn('--i386-runtime', script)
        self.assertIn('--measure-only', script)
        self.assertIn('gcc-arm-none-eabi-4_8-2014q1/tool', script)

    def test_compile_parallelism_fits_four_cpu_sandbox_budget(self):
        for requested, actual in ((1, '1'), (4, '4'), (16, '4')):
            with self.subTest(requested=requested):
                command = sandbox_dispatch.command('/tree', '/out', 'Release', 'gcc10', requested, True,
                                                   controller='/trusted/scripts')
                self.assertEqual(command[-1], actual)

    def test_untrusted_paths_are_positional_and_only_required_inputs_are_sent(self):
        command = sandbox_dispatch.command('/firmware/tree; touch injected', '/results',
                                           'Debug', 'gcc10', 4, False,
                                           controller='/trusted/scripts')
        includes = [command[i + 1] for i, arg in enumerate(command) if arg == '--include']
        self.assertEqual(includes, ['firmware/tree; touch injected',
            'trusted/scripts/sandbox_build.py', 'trusted/scripts/sandbox.py',
            'trusted/scripts/sandbox_process.py', 'trusted/scripts/sandbox_transport.py',
            'trusted/scripts/build_artifacts.py'])
        shell = command[command.index('-c') + 1]
        self.assertNotIn('touch injected', shell)
        self.assertIn('MemoryMax=4294967296', shell)
        self.assertIn('--debug', shell)
        self.assertIn('--no-revision-metadata', shell)
        self.assertEqual(command[-3:], ['trusted/scripts', 'firmware/tree; touch injected', '4'])

    def test_cannot_place_trusted_controller_inside_firmware_snapshot(self):
        with self.assertRaises(ValueError):
            sandbox_dispatch.command('/tree', '/out', 'Release', 'gcc10', 4, True,
                                     controller='/tree/scripts')

    def test_simulator_dispatch_keeps_source_paths_out_of_shell(self):
        command = sandbox_dispatch.simulator_command('/source; touch injected', '/out', 2, five=True,
                                                      controller='/trusted/scripts')
        self.assertEqual(command[command.index('--toolchain') + 1], 'gcc12')
        shell = command[command.index('-c') + 1]
        self.assertNotIn('touch injected', shell)
        self.assertEqual(command[-2:], ['trusted/scripts', 'source; touch injected'])
        self.assertIn('--generation 2', shell)
        self.assertIn('--five', shell)
        self.assertIn('--property=MemoryMax=4294967296', shell)
        inputs = [command[i + 1] for i, arg in enumerate(command) if arg == '--include']
        self.assertIn('trusted/scripts/sandbox_simulator_build.py', inputs)
        self.assertNotIn('.', inputs)

    def test_invalid_configurations_fail_before_dispatch(self):
        for kind, toolchain, jobs in [('Other', 'gcc10', 4), ('Release', 'gcc48', 4),
                                      ('Release', 'gcc10', True), ('Release', 'gcc10', 0)]:
            with self.subTest(kind=kind, toolchain=toolchain, jobs=jobs), self.assertRaises(ValueError):
                sandbox_dispatch.command('/tree', '/out', kind, toolchain, jobs, True,
                                         controller='/trusted/scripts')
