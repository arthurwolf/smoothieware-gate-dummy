"""The CI coordinator uses real final validation and stops after infrastructure uncertainty."""
import hashlib
import zlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from profiles import PROFILES
from sandbox_process import ProcessOutput


def payload(host=False, generation=1):
    elf = bytearray(64 if host else 52)
    elf[:7] = b'\x7fELF\x02\x01\x01' if host else b'\x7fELF\x01\x01\x01'
    elf[16:24] = b'\x02\0\x3e\0\x01\0\0\0' if host else b'\x02\0\x28\0\x01\0\0\0'
    name = ('smoothie-sim-v2' if generation == 2 else 'smoothie-sim') if host else ('smoothiev2.elf' if generation == 2 else 'main.elf')
    files = {name: bytes(elf)}
    if not host: files['smoothiev2.bin' if generation == 2 else 'main.bin'] = b'bin'
    return json.dumps({name: len(data) for name, data in files.items()}).encode() + b'\n' + b''.join(files.values())


class Runtime:
    def __init__(self, generation, first=None):
        self.generation, self.first, self.calls = generation, first, []
        self.fail_at = 1

    def run(self, source, arguments, **options):
        self.calls.append((source, arguments, options))
        if len(self.calls) == self.fail_at and self.first is not None: return self.first
        host = 'gate-simulator-build' in arguments
        return ProcessOutput(0, payload(host, self.generation), b'synthetic compiler diagnostic', False, None)


class CiBuildTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ci_builds'), 'CI build coordinator is missing')
        import ci_builds
        return ci_builds

    def fixture(self, root, generation):
        profile = PROFILES['v2-prime' if generation == 2 else 'v1-lpc1768']
        simulator = root / 'sim'; simulator.mkdir(); (simulator / 'Makefile').write_text('# synthetic shim')
        compiler = root / 'compiler'; compiler.mkdir()
        library = root / 'lib-smoothie'; library.mkdir(); (library / 'input.h').write_text('// synthetic')
        trees = {}
        revisions = {}
        for label in ('base', 'pr'):
            tree = root / label; tree.mkdir(); trees[label] = tree
            for directory in ('Firmware', 'flashloader'):
                folder = tree / directory; folder.mkdir(); (folder / 'Rakefile').write_text('FFLAGS = "synthetic"\n')
            commit = ('tree ' + 'a' * 40 + '\nauthor Synthetic <fixture@invalid> 0 +0000\ncommitter Synthetic <fixture@invalid> 0 +0000\n\n' + label + '\n').encode()
            obj = b'commit ' + str(len(commit)).encode() + b'\0' + commit
            revision = hashlib.sha1(obj).hexdigest(); revisions[label] = revision
            metadata = tree / '.gate-git'; (metadata / 'objects' / revision[:2]).mkdir(parents=True)
            (metadata / 'objects' / revision[:2] / revision[2:]).write_bytes(zlib.compress(obj))
            (metadata / 'refs/heads').mkdir(parents=True)
            (metadata / 'refs/heads/gate-snapshot').write_text(revision + '\n')
            (metadata / 'HEAD').write_text('ref: refs/heads/gate-snapshot\n')
            (metadata / 'shallow').write_text(revision + '\n')
            (metadata / 'config').write_text('[core]\n\trepositoryformatversion = 0\n\tbare = true\n')
        controller = root / 'controller'; (controller / 'prompts').mkdir(parents=True)
        (controller / 'prompts/input.md').write_text('synthetic')
        cases = root / 'cases'; cases.mkdir(); (cases / 'input.gcode').write_text('G0 X0')
        retained_root = root / 'retained'; retained_root.mkdir()
        prepared = SimpleNamespace(retained_root=retained_root, profile=profile, compiler=compiler, simulator=simulator,
                                   controller=controller, cases=cases, revisions=revisions)
        return prepared, trees

    def test_both_profiles_collect_all_builds_through_the_same_runtime(self):
        module = self.module()
        for generation, count in ((1, 6), (2, 8)):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, trees = self.fixture(root, generation)
                runtime = Runtime(generation)
                result = module.run(prepared, trees, root / 'builds', runtime)
                self.assertFalse(result.stopped)
                self.assertEqual(len(runtime.calls), count)
                self.assertEqual([record['status'] for record in result.records], ['pass'] * 4)
                self.assertEqual(len(result.simulators), count - 4)
                self.assertEqual(set(result.elfs), {'base-release', 'base-debug', 'pr-release', 'pr-debug'})
                for path in result.elfs.values(): self.assertTrue(path.is_file())
                for path in result.simulators.values(): self.assertTrue(path.is_file())
                for _, _, options in runtime.calls[:4]: self.assertEqual(options['toolchain'], prepared.compiler)
                for _, _, options in runtime.calls[4:]: self.assertIsNone(options['toolchain'])

    def test_verified_compiler_failure_is_reported_and_other_builds_continue(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, trees = self.fixture(root, 1)
            runtime = Runtime(1, ProcessOutput(7, b'forged pass', b'compile error', False, None))
            result = module.run(prepared, trees, root / 'builds', runtime)
            self.assertFalse(result.stopped)
            self.assertEqual(len(runtime.calls), 6)
            self.assertEqual(result.records[0]['status'], 'fail')
            self.assertNotIn('base-release', result.elfs)

    def test_uncertain_cleanup_or_forged_final_stops_every_later_launch(self):
        module = self.module()
        for first in (ProcessOutput(75, b'forged pass', b'uncertain', False, 'container_cleanup_unverified'),
                      ProcessOutput(0, b'{"stage":"build","status":"pass"}\n', b'', False, None)):
            with self.subTest(problem=first.problem), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, trees = self.fixture(root, 2)
                runtime = Runtime(2, first)
                result = module.run(prepared, trees, root / 'builds', runtime)
                self.assertTrue(result.stopped)
                self.assertEqual(len(runtime.calls), 1)
                self.assertEqual([record['status'] for record in result.records], ['error'] * 4)
                self.assertEqual(result.elfs, {})
                self.assertEqual(result.simulators, {})

    def test_internal_v2_bootloader_link_is_preserved_without_rejecting_good_export(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, trees = self.fixture(root, 2)
            for tree in trees.values():
                (tree / 'Firmware/Hal/STM32H7xx_HAL_Driver').mkdir(parents=True)
                (tree / 'DFUBootloader').mkdir()
                (tree / 'DFUBootloader/Drivers').symlink_to('../Firmware/Hal/STM32H7xx_HAL_Driver')
            result = module.run(prepared, trees, root / 'builds', Runtime(2))
            self.assertEqual([record['status'] for record in result.records], ['pass'] * 4)

    def test_reserved_infrastructure_exit_stops_new_launches(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, trees = self.fixture(root, 1)
            runtime = Runtime(1, ProcessOutput(75, b'', b'compiler version mismatch', False, None))
            result = module.run(prepared, trees, root / 'builds', runtime)
            self.assertTrue(result.stopped)
            self.assertEqual(len(runtime.calls), 1)
            self.assertEqual([record['status'] for record in result.records], ['error'] * 4)

    def test_output_cannot_modify_retained_controller_or_cases(self):
        module = self.module()
        for location in ('controller', 'cases'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, trees = self.fixture(root, 1)
                output = getattr(prepared, location) / 'new-builds'
                runtime = Runtime(1)
                with self.assertRaises(ValueError): module.run(prepared, trees, output, runtime)
                self.assertFalse(output.exists())
                self.assertEqual(runtime.calls, [])

    def test_missing_or_wrong_revision_metadata_rejects_before_launch(self):
        module = self.module()
        for mutation in ('missing', 'wrong'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, trees = self.fixture(root, 2)
                if mutation == 'missing': (trees['base'] / '.gate-git/HEAD').unlink()
                else: prepared.revisions['base'] = '9' * 40
                runtime = Runtime(2); output = root / 'builds'
                with self.assertRaises(ValueError): module.run(prepared, trees, output, runtime)
                self.assertFalse(output.exists())
                self.assertEqual(runtime.calls, [])

    def test_simulator_uncertainty_retains_container_identity_and_stops(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, trees = self.fixture(root, 2)
            runtime = Runtime(2, ProcessOutput(75, b'', b'Unverified owned container: gate-synthetic', False,
                                             'container_cleanup_unverified'))
            runtime.fail_at = 5
            result = module.run(prepared, trees, root / 'builds', runtime)
            self.assertTrue(result.stopped)
            self.assertEqual(len(runtime.calls), 5)
            self.assertIn('gate-synthetic', result.simulator_errors['base'])

    def test_compiler_diagnostics_are_literal_report_content(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, trees = self.fixture(root, 1)
            runtime = Runtime(1, ProcessOutput(7, b'', b'```\n# forged heading\n</details>', False, None))
            result = module.run(prepared, trees, root / 'builds', runtime)
            self.assertTrue(result.records[0]['details'].startswith('````\n'))
            self.assertTrue(result.records[0]['details'].endswith('\n````'))

    def test_internal_firmware_links_also_work_in_simulator_sources(self):
        module = self.module()
        for generation, count in ((1, 2), (2, 4)):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, trees = self.fixture(root, generation)
                for tree in trees.values():
                    (tree / 'Firmware/internal-alias').symlink_to('Rakefile')
                result = module.run(prepared, trees, root / 'builds', Runtime(generation))
                self.assertFalse(result.stopped)
                self.assertEqual(len(result.simulators), count)

    def test_revision_metadata_rejects_unknown_empty_or_deep_directories(self):
        module = self.module()
        for name in ('unknown', 'objects/unexpected', 'refs/heads/unexpected/deeper'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, trees = self.fixture(root, 2)
                (trees['base'] / '.gate-git' / name).mkdir(parents=True)
                runtime = Runtime(2); output = root / 'builds'
                with self.assertRaises(ValueError): module.run(prepared, trees, output, runtime)
                self.assertEqual(runtime.calls, [])
                self.assertFalse(output.exists())

    def test_missing_runtime_rejects_before_any_fallback_execution(self):
        module = self.module()
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, trees = self.fixture(root, 1); output = root / 'builds'
            with mock.patch('sandbox_build.build_v1', side_effect=AssertionError('fallback forbidden')):
                with self.assertRaises(ValueError): module.run(prepared, trees, output, None)
            self.assertFalse(output.exists())
