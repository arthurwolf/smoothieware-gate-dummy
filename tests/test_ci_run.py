"""The complete CI worker composes real validation, builds and reports with synthetic transport."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_ci_builds as build_fixture
from test_ci_builds import payload
import test_ci_checks as check_fixture
from test_ci_checks import Runtime as CheckRuntime, ROOT
from test_ci_inputs import digest, PINS
from test_github_worker import metadata
from sandbox_process import ProcessOutput
import github_worker


class Runtime(CheckRuntime):
    def run(self, source, arguments, **options):
        if any(value in arguments for value in ('gate-v1-build', 'gate-v2-build', 'gate-simulator-build')):
            self.calls.append((source, arguments, options))
            return ProcessOutput(0, payload('gate-simulator-build' in arguments, self.generation), b'synthetic build', False, None)
        return super().run(source, arguments, **options)


class CiRunTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ci_run'), 'complete CI worker entrypoint is missing')
        import ci_run
        return ci_run

    def fixture(self, root, generation):
        builds = root / 'build-inputs'; builds.mkdir()
        build_prepared, trees = build_fixture.CiBuildTests().fixture(builds, generation)
        checks = root / 'check-inputs'; checks.mkdir()
        check_prepared, _, manifest, diff = check_fixture.CiChecksTests().fixture(checks, generation)
        inputs = root / 'inputs'; inputs.mkdir()
        controller = inputs / 'controller-input'
        for name in ('bin', 'scripts'): (controller / name).mkdir(parents=True, exist_ok=True)
        (controller / 'bin/smoothie-gate').write_text('# labelled synthetic controller')
        (controller / 'scripts/gate.py').write_text('# labelled synthetic controller')
        shutil.copytree(ROOT / 'prompts', controller / 'prompts')
        shutil.copytree(check_prepared.cases, controller / 'cases')
        shutil.copytree(check_prepared.cases, inputs / 'cases')
        shutil.copytree(check_prepared.cases, inputs / 'case-inventory')
        simulator = inputs / 'sim'; simulator.mkdir()
        shutil.copytree(check_prepared.simulator / 'checkers', simulator / 'checkers', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(check_prepared.simulator / 'rules.json', simulator / 'rules.json')
        (simulator / 'Makefile').write_text('# labelled synthetic simulator build input')
        shutil.copytree(builds / 'lib-smoothie', inputs / 'lib-smoothie')
        policy, run, pr, values, calls, api = metadata(build_prepared.profile.name)
        policy['pins'].update(controller_sha256=digest(controller), simulator_sha256=digest(simulator),
                              shared_library_sha256=digest(inputs / 'lib-smoothie'),
                              toolchain_sha256=PINS[build_prepared.profile.toolchain])
        pr['base']['sha'] = build_prepared.revisions['base']; pr['head']['sha'] = build_prepared.revisions['pr']
        execution = github_worker.prepare(policy, 101, 2, api)
        manifest.update(execution['binding'], simulator_revision=policy['pins']['sim_ref'],
                        simulator_sha256=digest(simulator), references_sha256=digest(inputs / 'cases'),
                        case_inventory_sha256=digest(inputs / 'case-inventory'),
                        simulator_dependencies={'lib-smoothie': {'sha256': digest(inputs / 'lib-smoothie')}} if generation == 2 else {})
        archive = root / 'compiler.tar.bz2'; archive.write_bytes(b'labelled synthetic compiler transport')
        return dict(policy=policy, execution=execution, manifest=manifest, inputs=inputs, archive=archive,
                    trees=trees, diff=diff, output=root / 'run'), api, pr

    def compiler(self, source, pin, destination):
        destination.mkdir(); compiler = destination / 'compiler'; compiler.mkdir(); return compiler

    def test_both_profiles_execute_all_components_and_allow_current_report(self):
        module = self.module()
        for generation, count in ((1, 9), (2, 11)):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                arguments, api, pr = self.fixture(Path(directory), generation)
                runtime = Runtime(generation); selected = []
                def factory(image): selected.append(image); return runtime
                with mock.patch('compiler_inputs.prepare', side_effect=self.compiler):
                    result = module.run(module.RunRequest(**arguments), api, runtime_factory=factory)
                self.assertEqual(result.code, 0)
                self.assertTrue(result.publication_permitted)
                self.assertEqual(selected, [arguments['policy']['pins']['runtime_image']])
                self.assertEqual(len(runtime.calls), count)
                self.assertTrue((arguments['output'] / 'checks/report/comment.md').is_file())

    def test_changed_pr_after_execution_refuses_publication_and_records_reason(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            arguments, api, pr = self.fixture(Path(directory), 1)
            class Changing(Runtime):
                def run(self, source, command, **options):
                    result = super().run(source, command, **options)
                    if 'gate-replay' in command: pr['head']['sha'] = '9'*40
                    return result
            with mock.patch('compiler_inputs.prepare', side_effect=self.compiler):
                result = module.run(module.RunRequest(**arguments), api, runtime_factory=lambda image: Changing(1))
            self.assertEqual(result.code, 2)
            self.assertFalse(result.publication_permitted)
            problem = json.loads((arguments['output'] / 'execution.problem.json').read_text())
            self.assertFalse(problem['publication_permitted'])
            self.assertIn('changed', problem['error'])

    def test_invalid_manifest_never_constructs_a_runtime(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            arguments, api, pr = self.fixture(Path(directory), 1)
            arguments['manifest']['head'] = '9'*40
            with mock.patch('compiler_inputs.prepare') as compiler, mock.patch('container_runtime.DockerRuntime') as runtime:
                with self.assertRaises(ValueError): module.run(module.RunRequest(**arguments), api)
                compiler.assert_not_called(); runtime.assert_not_called()

    def test_final_api_transport_failure_explicitly_refuses_publication(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            arguments, api, pr = self.fixture(Path(directory), 1)
            finished = []
            class Finishing(Runtime):
                def run(self, source, command, **options):
                    result = super().run(source, command, **options)
                    if 'gate-replay' in command: finished.append(True)
                    return result
            def unavailable_after_checks(route):
                if finished: raise OSError('synthetic gh launcher unavailable')
                return api(route)
            with mock.patch('compiler_inputs.prepare', side_effect=self.compiler):
                result = module.run(module.RunRequest(**arguments), unavailable_after_checks,
                                    runtime_factory=lambda image: Finishing(1))
            self.assertEqual(result.code, 2)
            self.assertFalse(result.publication_permitted)
            problem = json.loads((arguments['output'] / 'execution.problem.json').read_text())
            self.assertIn('launcher unavailable', problem['error'])
            self.assertFalse(problem['publication_permitted'])
