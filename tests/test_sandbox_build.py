"""Trusted orchestration never accepts child-declared passing stage results."""
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox_build
from sandbox_process import ProcessOutput


class SandboxedV2Build(unittest.TestCase):
    def test_native_simulator_cli_selects_digest_runtime(self):
        import container_runtime
        import sandbox_simulator_build
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            elf = bytearray(64); elf[:7] = b'\x7fELF\x02\x01\x01'
            elf[16:24] = b'\x02\0\x3e\0\x01\0\0\0'
            runtime = mock.Mock()
            runtime.run.return_value = ProcessOutput(0,b'{"smoothie-sim-v2":64}\n'+bytes(elf),b'',False,None)
            arguments=['sandbox_simulator_build.py','--tree',str(source),'--out',str(output),'--generation','2',
                       '--runtime-image','registry.invalid/gate@sha256:'+'a'*64]
            with mock.patch.object(sys,'argv',arguments),mock.patch.object(container_runtime,'DockerRuntime',return_value=runtime):
                self.assertEqual(sandbox_simulator_build.main(),0)
            self.assertEqual((output/'smoothie-sim-v2').read_bytes(),bytes(elf))

    def test_firmware_cli_selects_digest_runtime_and_legacy_compiler(self):
        import container_runtime
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            elf = bytearray(52); elf[:7] = b'\x7fELF\x01\x01\x01'
            elf[16:24] = b'\x02\0\x28\0\x01\0\0\0'
            runtime = mock.Mock()
            runtime.run.return_value = ProcessOutput(0,b'{"main.elf":52,"main.bin":3}\n'+bytes(elf)+b'bin',b'',False,None)
            image = 'registry.invalid/gate@sha256:' + 'a'*64
            arguments = ['sandbox_build.py','--tree',str(source),'--out',str(output),'--compiler',str(compiler),
                         '--profile','v1-lpc1768','--runtime-image',image,'--compiler-profile','gcc48']
            with mock.patch.object(sys,'argv',arguments), mock.patch.object(container_runtime,'DockerRuntime',return_value=runtime):
                self.assertEqual(sandbox_build.main(),0)
            self.assertEqual((output/'main.bin').read_bytes(),b'bin')
            self.assertIn(sandbox_build.V1_LEGACY_BANNER,runtime.run.call_args.args[1])

    def test_selected_runtime_uses_same_final_validator_for_both_firmware_generations(self):
        for generation in (1, 2):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as tmp:
                source, compiler, output = self.fixture(Path(tmp))
                elf = bytearray(52); elf[:7] = b'\x7fELF\x01\x01\x01'
                elf[16:24] = b'\x02\0\x28\0\x01\0\0\0'
                names = ('main.elf', 'main.bin') if generation == 1 else ('smoothiev2.elf', 'smoothiev2.bin')
                payload = ('{"' + names[0] + '":52,"' + names[1] + '":3}\n').encode() + bytes(elf) + b'bin'
                runtime = mock.Mock()
                runtime.run.return_value = ProcessOutput(0, payload, b'compiler tail', False, None)
                if generation == 1:
                    result = sandbox_build.build_v1(source, output, compiler, runtime=runtime, compiler_profile='gcc48')
                else:
                    result = sandbox_build.build_v2(source, output, compiler, runtime=runtime)
                self.assertEqual(result.returncode, 0)
                self.assertEqual((output / names[1]).read_bytes(), b'bin')
                self.assertEqual(runtime.run.call_args.args[0], source)
                self.assertEqual(runtime.run.call_args.kwargs['toolchain'], compiler)
                if generation == 1:
                    self.assertIn(sandbox_build.V1_LEGACY_BANNER, runtime.run.call_args.args[1])

    def test_runtime_failure_never_creates_host_finals(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            runtime = mock.Mock()
            runtime.run.return_value = ProcessOutput(75, b'forged', b'uncertain container', False, 'container_cleanup_unverified')
            result = sandbox_build.build_v2(source, output, compiler, runtime=runtime)
            self.assertEqual(result.problem, 'container_cleanup_unverified')
            self.assertFalse(output.exists())

    def test_host_simulator_can_use_selected_runtime_without_compiler_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            runtime = mock.Mock()
            runtime.run.return_value = ProcessOutput(7, b'forged', b'build failed', False, None)
            result = sandbox_build.build_simulator(source, output, generation=2, runtime=runtime)
            self.assertEqual(result.returncode, 7)
            self.assertIsNone(runtime.run.call_args.kwargs['toolchain'])
            self.assertFalse(output.exists())

    def test_v1_measurement_recipe_validates_before_modifying_private_copy(self):
        for original, code in (('FLASH (rx) : ORIGIN = 16K, LENGTH = (512K - 16K)', 0),
                               ('unknown linker layout', 75)):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'mbed/src/vendor/NXP/cmsis/LPC1768/GCC_ARM/LPC1768.ld'
                path.parent.mkdir(parents=True); path.write_text(original)
                result = subprocess.run([sys.executable, '-B', '-I', '-c', sandbox_build.MEASURE_V1],
                                        cwd=tmp, capture_output=True)
                self.assertEqual(result.returncode, code)
                self.assertEqual(path.read_text(), original.replace('512K', '4096K') if code == 0 else original)

    def test_v1_collects_its_own_named_finals_with_legacy_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, compiler, output = self.fixture(root)
            runtime = root / 'i386'; runtime.mkdir()
            for name in ('ld-linux.so.2', 'libc.so.6', 'libdl.so.2', 'libm.so.6'):
                (runtime / name).write_bytes(b'fixture')
            elf = bytearray(52); elf[:7] = b'\x7fELF\x01\x01\x01'
            elf[16:24] = b'\x02\0\x28\0\x01\0\0\0'
            data = b'{"main.elf":52,"main.bin":3}\n' + bytes(elf) + b'bin'
            with mock.patch.object(sandbox_build.sandbox_process, 'run',
                                   return_value=ProcessOutput(0, data, b'tail', False, None)) as run:
                result = sandbox_build.build_v1(source, output, compiler, runtime, debug=True, jobs=4)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((output / 'main.bin').read_bytes(), b'bin')
            self.assertEqual(set(result.files), {'main.elf', 'main.bin'})
            self.assertIn('/lib/ld-linux.so.2', run.call_args.args[0])
            self.assertIn('BUILD_TYPE="$1"', ' '.join(run.call_args.args[0]))

    def test_host_simulator_collects_only_x86_64_executable(self):
        self.assertTrue(hasattr(sandbox_build, 'build_simulator'), 'missing isolated simulator build worker')
        for identity, success in ((b'\x02\0\x3e\0', True), (b'\x03\0\x3e\0', True),
                                  (b'\x02\0\x28\0', False)):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source, compiler, output = self.fixture(root)
                elf = bytearray(64); elf[:7] = b'\x7fELF\x02\x01\x01'
                elf[16:24] = identity + b'\x01\0\0\0'
                data = b'{"smoothie-sim-v2":64}\n' + bytes(elf)
                with mock.patch.object(sandbox_build.sandbox_process, 'run',
                                       return_value=ProcessOutput(0, data, b'build tail', False, None)):
                    result = sandbox_build.build_simulator(source, output, generation=2)
                self.assertEqual(result.returncode == 0, success)
                self.assertEqual(output.exists(), success)
                if success:
                    self.assertEqual((output / 'smoothie-sim-v2').read_bytes(), bytes(elf))

    def test_failed_simulator_build_cannot_publish_forged_final(self):
        self.assertTrue(hasattr(sandbox_build, 'build_simulator'), 'missing isolated simulator build worker')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, compiler, output = self.fixture(root)
            with mock.patch.object(sandbox_build.sandbox_process, 'run',
                                   return_value=ProcessOutput(9, b'forged bytes', b'make failed', False, None)):
                result = sandbox_build.build_simulator(source, output, generation=1)
            self.assertEqual(result.returncode, 9)
            self.assertFalse(output.exists())
            self.assertEqual(result.diagnostics, b'make failed')

    def fixture(self, root):
        source, compiler = root / 'source', root / 'compiler'
        source.mkdir(); compiler.mkdir()
        return source, compiler, root / 'finals'

    def test_nonzero_or_capture_problem_cannot_publish_forged_finals(self):
        for code, problem in ((7, None), (0, 'stdout_limit'), (0, 'timeout')):
            with self.subTest(code=code, problem=problem), tempfile.TemporaryDirectory() as tmp:
                source, compiler, output = self.fixture(Path(tmp))
                captured = ProcessOutput(code, b'{"status":"pass"}\n', b'compiler failed', False, problem)
                with mock.patch.object(sandbox_build.sandbox_process, 'run', return_value=captured):
                    result = sandbox_build.build_v2(source, output, compiler)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertEqual(result.diagnostics, b'compiler failed')

    def test_successful_capture_requires_arm_elf_before_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            data = b'{"smoothiev2.elf":4,"smoothiev2.bin":3}\nfakebin'
            with mock.patch.object(sandbox_build.sandbox_process, 'run',
                                   return_value=ProcessOutput(0, data, b'', False, None)):
                result = sandbox_build.build_v2(source, output, compiler)
            self.assertEqual(result.returncode, 75)
            self.assertEqual(result.problem, 'final_validation')
            self.assertFalse(output.exists())

    def test_valid_final_bytes_are_collected_with_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            elf = bytearray(52)
            elf[:7] = b'\x7fELF\x01\x01\x01'
            elf[16:24] = b'\x02\0\x28\0\x01\0\0\0'
            data = b'{"smoothiev2.elf":52,"smoothiev2.bin":3}\n' + bytes(elf) + b'bin'
            with mock.patch.object(sandbox_build.sandbox_process, 'run',
                                   return_value=ProcessOutput(0, data, b'tail', True, None)):
                result = sandbox_build.build_v2(source, output, compiler, debug=True, jobs=4)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.diagnostics_truncated)
            self.assertEqual((output / 'smoothiev2.elf').read_bytes(), bytes(elf))
            self.assertEqual(result.files['smoothiev2.bin']['bytes'], 3)
            self.assertEqual(len(result.files['smoothiev2.bin']['sha256']), 64)

    def test_malformed_finals_retain_bounded_compiler_diagnostics(self):
        for data in (b'not a header\n', b'{"smoothiev2.elf":4,"smoothiev2.bin":3}\nfakebin'):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                source, compiler, output = self.fixture(Path(tmp))
                captured = ProcessOutput(0, data, b'compiler diagnostic tail', True, None)
                with mock.patch.object(sandbox_build.sandbox_process, 'run', return_value=captured):
                    result = sandbox_build.build_v2(source, output, compiler)
                self.assertEqual(result.returncode, 75)
                self.assertEqual(result.problem, 'final_validation')
                self.assertIn(b'compiler diagnostic tail', result.diagnostics)
                self.assertTrue(result.diagnostics_truncated)
                self.assertFalse(output.exists())

    def test_invalid_jobs_and_existing_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, compiler, output = self.fixture(Path(tmp))
            for jobs in (True, 0, 17):
                with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                    sandbox_build.build_v2(source, output, compiler, jobs=jobs)
            output.mkdir(); (output / 'canary').write_text('keep')
            with self.assertRaises(ValueError):
                sandbox_build.build_v2(source, output, compiler)
            self.assertEqual((output / 'canary').read_text(), 'keep')
