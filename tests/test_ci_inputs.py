"""Prepared CI inputs must match API binding and copied trusted bytes before work."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_dispatch_identity import fixture

PINS = {'gcc48': 'ce92859550819d4a3d1a6e2672ea64882b30afa2c08cf67fa8e1d93788c2c577',
        'gcc10': '97dbb4f019ad1650b732faffcc881689cedc14e2b7ee863d390e0a41ef16c9a3'}


def digest(root):
    result = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file():
            result.update((json.dumps([path.relative_to(root).as_posix(), path.stat().st_mode & 0o111,
                                      hashlib.sha256(path.read_bytes()).hexdigest()],
                                     ensure_ascii=True, separators=(',', ':')) + '\n').encode())
    return result.hexdigest()


def inputs(directory, generation=2):
    import controller_snapshot
    from profiles import PROFILES, TOOLCHAIN_VERSIONS, EXPECTED_STAGES
    root = directory / 'inputs'; root.mkdir()
    controller = root / 'controller-input'
    for name, content in {'bin/smoothie-gate': '# synthetic launcher', 'scripts/gate.py': '# synthetic controller',
                          'prompts/review.md': 'synthetic prompt', 'cases/v2/coverage.json': '{}'} .items():
        path = controller / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    for name in ('cases', 'case-inventory'):
        path = root / name / 'v2/coverage.json'; path.parent.mkdir(parents=True); path.write_text('{}')
    simulator = root / 'sim'; simulator.mkdir(); (simulator / 'source.cpp').write_text('synthetic shim')
    library = root / 'lib-smoothie'; library.mkdir(); (library / 'header.h').write_text('synthetic shared input')
    policy, _, _, _, _, manifest = fixture()
    profile = PROFILES['v1-lpc1768' if generation == 1 else 'v2-prime']
    policy['profile'] = profile.name
    policy['pins'].update(controller_sha256=digest(controller), simulator_sha256=digest(simulator),
                          toolchain_sha256=PINS[profile.toolchain], shared_library_sha256=digest(library))
    controller_snapshot.verify(controller, policy['pins']['controller_sha256'])
    manifest.update(profile=profile.name, board=profile.board, firmware_repository=profile.repository,
                    toolchain=profile.toolchain, toolchain_version=TOOLCHAIN_VERSIONS[profile.toolchain],
                    controller_sha256=policy['pins']['controller_sha256'], simulator_revision='b' * 40,
                    simulator_sha256=digest(simulator), references_sha256=digest(root / 'cases'),
                    case_inventory_sha256=digest(root / 'case-inventory'), expected_stages=list(EXPECTED_STAGES),
                    execution_pins=copy.deepcopy(policy['pins']))
    manifest['simulator_dependencies'] = ({'lib-smoothie': {'sha256': digest(library)}} if generation == 2 else {})
    binding = {key: copy.deepcopy(manifest[key]) for key in
               ('workflow', 'profile', 'base', 'head', 'controller_sha256', 'execution_pins')}
    archive = directory / 'compiler.tar.bz2'; archive.write_bytes(b'synthetic transport input')
    return policy, binding, manifest, root, archive


class CiInputsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ci_inputs'), 'prepared CI input contract is missing')
        import ci_inputs
        return ci_inputs

    def test_valid_inputs_select_correct_profile_and_verified_compiler_boundary(self):
        module = self.module()
        for generation in (1, 2):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                policy, binding, manifest, root, archive = inputs(Path(directory), generation)
                output = Path(directory) / 'run'
                def compiler(source, pin, destination):
                    self.assertEqual(source, archive)
                    self.assertEqual(pin, PINS[manifest['toolchain']])
                    destination.mkdir(); path = destination / 'tool'; path.mkdir(); return path
                with mock.patch('compiler_inputs.prepare', side_effect=compiler):
                    prepared = module.prepare(policy, binding, manifest, root, archive, output)
                self.assertEqual(prepared.profile.name, manifest['profile'])
                self.assertEqual(prepared.compiler, output / 'compiler/tool')
                self.assertEqual(prepared.controller, root / 'controller-input')
                self.assertEqual(prepared.runtime_image, policy['pins']['runtime_image'])

    def test_stale_or_untrusted_inputs_fail_before_compiler_preparation(self):
        module = self.module()
        for mutation in ('head', 'controller', 'simulator', 'dirty-simulator', 'cases', 'library', 'compiler-pin'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                policy, binding, manifest, root, archive = inputs(Path(directory))
                if mutation == 'head': manifest['head'] = '9' * 40
                elif mutation == 'controller': (root / 'controller-input/prompts/review.md').write_text('changed')
                elif mutation == 'simulator': (root / 'sim/source.cpp').write_text('changed')
                elif mutation == 'dirty-simulator': manifest['simulator_revision'] += '+dirty-or-untracked'
                elif mutation == 'cases': (root / 'cases/v2/coverage.json').write_text('changed')
                elif mutation == 'library': (root / 'lib-smoothie/header.h').unlink()
                else:
                    for value in (policy['pins'], binding['execution_pins'], manifest['execution_pins']):
                        value['toolchain_sha256'] = '9' * 64
                output = Path(directory) / 'run'
                with mock.patch('compiler_inputs.prepare') as compiler:
                    with self.assertRaises(ValueError):
                        module.prepare(policy, binding, manifest, root, archive, output)
                    compiler.assert_not_called()
                self.assertFalse(output.exists())

    def test_overlapping_and_occupied_output_are_rejected(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            policy, binding, manifest, root, archive = inputs(Path(directory))
            for output in (root / 'new-output', root):
                with mock.patch('compiler_inputs.prepare') as compiler:
                    with self.assertRaises((ValueError, FileExistsError)):
                        module.prepare(policy, binding, manifest, root, archive, output)
                    compiler.assert_not_called()

    def test_binding_cannot_be_reused_under_another_current_policy(self):
        module = self.module()
        for field, value in (('repository', 'other/firmware'), ('repository_id', 18),
                             ('workflow_id', 52), ('workflow_path', '.github/workflows/other.yml'),
                             ('default_branch', 'edge'), ('allowed_base_branches', ['release'])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                policy, binding, manifest, root, archive = inputs(Path(directory))
                policy[field] = value
                output = Path(directory) / 'run'
                with mock.patch('compiler_inputs.prepare') as compiler:
                    with self.assertRaises(ValueError):
                        module.prepare(policy, binding, manifest, root, archive, output)
                    compiler.assert_not_called()
                self.assertFalse(output.exists())

    def test_linked_ancestors_and_malformed_dependencies_reject_before_creation(self):
        module = self.module()
        for mutation in ('root-alias', 'output-alias', 'dependencies-null', 'dependencies-list',
                         'library-null', 'library-list'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                policy, binding, manifest, root, archive = inputs(directory)
                output = directory / 'run'
                if mutation == 'root-alias':
                    alias = directory / 'alias'; alias.symlink_to(directory, target_is_directory=True)
                    root = alias / 'inputs'
                elif mutation == 'output-alias':
                    alias = directory / 'alias'; alias.symlink_to(directory, target_is_directory=True)
                    output = alias / 'run'
                elif mutation.startswith('dependencies'):
                    manifest['simulator_dependencies'] = None if mutation.endswith('null') else []
                else:
                    manifest['simulator_dependencies']['lib-smoothie'] = None if mutation.endswith('null') else []
                with mock.patch('compiler_inputs.prepare') as compiler:
                    with self.assertRaises(ValueError):
                        module.prepare(policy, binding, manifest, root, archive, output)
                    compiler.assert_not_called()
                self.assertFalse(output.exists())
