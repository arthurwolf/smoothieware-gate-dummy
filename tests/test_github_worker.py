"""Worker inputs come from authenticated current API state, before any code execution."""
import copy
import contextlib
import io
import json
import subprocess
import tempfile
from unittest import mock
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_dispatch_identity import fixture


def metadata(profile='v2-prime'):
    policy, repository, workflow, run, pr, _ = fixture()
    policy['profile'] = profile
    run.update(status='in_progress', conclusion=None)
    values = {'repos/owner/firmware': repository,
              'repos/owner/firmware/actions/workflows/51': workflow,
              'repos/owner/firmware/actions/runs/101': run,
              'repos/owner/firmware/pulls/23': pr}
    calls = []
    def api(route):
        calls.append(route)
        if route not in values: raise AssertionError('unexpected API route: ' + route)
        return copy.deepcopy(values[route])
    return policy, run, pr, values, calls, api


class GithubWorkerTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('github_worker'), 'authenticated worker preparation is missing')
        import github_worker
        return github_worker

    def test_both_profiles_bind_current_run_attempt_and_fork_revisions(self):
        module = self.module()
        for profile in ('v1-lpc1768', 'v2-prime'):
            with self.subTest(profile=profile):
                policy, run, pr, _, calls, api = metadata(profile)
                prepared = module.prepare(policy, 101, 2, api)
                self.assertEqual(prepared['binding']['profile'], profile)
                self.assertEqual(prepared['binding']['base'], '2'*40)
                self.assertEqual(prepared['binding']['head'], '3'*40)
                self.assertEqual(prepared['binding']['workflow']['workflow_revision'], '1'*40)
                self.assertEqual(prepared['binding']['workflow']['attempt'], 2)
                self.assertEqual(prepared['base_repository'], 'owner/firmware')
                self.assertEqual(prepared['head_repository'], 'fork/firmware')
                self.assertEqual(len(calls), 4)
                self.assertFalse(any('artifacts' in route for route in calls))

    def test_wrong_attempt_or_inactive_run_is_rejected_before_pr_access(self):
        module = self.module()
        for mutation in ('attempt', 'completed', 'event', 'workflow'):
            with self.subTest(mutation=mutation):
                policy, run, pr, _, calls, api = metadata()
                if mutation == 'attempt': run['run_attempt'] = 3
                elif mutation == 'completed': run['status'] = 'completed'; run['conclusion'] = 'success'
                elif mutation == 'event': run['event'] = 'pull_request'
                else: run['workflow_id'] = 99
                with self.assertRaises(ValueError): module.prepare(policy, 101, 2, api)
                self.assertFalse(any('/pulls/' in route for route in calls))

    def test_current_source_and_attempt_must_still_match_after_work(self):
        module = self.module()
        for mutation in ('head', 'base', 'attempt', 'head-repository', 'closed'):
            with self.subTest(mutation=mutation):
                policy, run, pr, _, calls, api = metadata()
                prepared = module.prepare(policy, 101, 2, api)
                if mutation == 'head': pr['head']['sha'] = '9'*40
                elif mutation == 'base': pr['base']['sha'] = '9'*40
                elif mutation == 'attempt': run['run_attempt'] = 3
                elif mutation == 'head-repository': pr['head']['repo']['full_name'] = 'other/firmware'
                else: pr['state'] = 'closed'
                with self.assertRaises(ValueError): module.confirm(policy, prepared, api)

    def test_unchanged_binding_can_be_confirmed_without_artifact_reads(self):
        module = self.module()
        policy, _, _, _, calls, api = metadata()
        prepared = module.prepare(policy, 101, 2, api)
        self.assertEqual(module.confirm(policy, prepared, api), prepared)
        self.assertEqual(len(calls), 8)

    def test_reporter_rejects_an_active_worker_run_before_pr_or_artifacts(self):
        import github_preflight
        policy, _, _, _, calls, api = metadata()
        with self.assertRaises(ValueError): github_preflight.prepare(policy, 101, api)
        self.assertFalse(any('/pulls/' in route or '/artifacts' in route for route in calls))

    def test_cli_prepare_and_confirm_preserve_authenticated_identity(self):
        module = self.module()
        self.assertTrue(callable(getattr(module, 'main', None)), 'worker preparation requires an executable adapter')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); policy, run, pr, _, calls, api = metadata()
            policy_file = root/'policy.json'; policy_file.write_text(json.dumps(policy))
            output = root/'execution.json'
            with mock.patch('github_preflight.ApiReader', return_value=api), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(['prepare', '--policy', str(policy_file), '--run-id', '101', '--attempt', '2', '--out', str(output)]), 0)
                prepared = json.loads(output.read_text())
                self.assertEqual(prepared['binding']['head'], pr['head']['sha'])
                self.assertEqual(module.main(['confirm', '--policy', str(policy_file), '--execution', str(output)]), 0)
                pr['head']['sha'] = '9'*40
                self.assertEqual(module.main(['confirm', '--policy', str(policy_file), '--execution', str(output)]), 2)
                self.assertEqual(module.main(['prepare', '--policy', str(policy_file), '--run-id', '101', '--attempt', '2', '--out', str(output)]), 2)
            self.assertEqual(json.loads(output.read_text()), prepared)

    def test_isolated_cli_help(self):
        entry = Path(__file__).resolve().parents[1]/'scripts/github_worker.py'
        done = subprocess.run([sys.executable, '-B', '-I', str(entry), '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('prepare', done.stdout)
        self.assertIn('confirm', done.stdout)

    def test_bind_complete_local_manifest_without_overriding_wrong_identity(self):
        module = self.module()
        self.assertTrue(callable(getattr(module, 'bind_manifest', None)), 'workflow manifests need an authenticated binding adapter')
        from profiles import select_profile, TOOLCHAIN_VERSIONS, EXPECTED_STAGES
        for profile_name in ('v1-lpc1768', 'v2-prime'):
            with self.subTest(profile=profile_name), tempfile.TemporaryDirectory() as temporary:
                policy, run, pr, _, calls, api = metadata(profile_name)
                execution = module.prepare(policy, 101, 2, api); profile = select_profile(profile_name)
                manifest = {'profile':profile.name, 'board':profile.board, 'firmware_repository':profile.repository,
                            'base':pr['base']['sha'], 'head':pr['head']['sha'],
                            'controller_sha256':policy['pins']['controller_sha256'], 'toolchain':profile.toolchain,
                            'toolchain_version':TOOLCHAIN_VERSIONS[profile.toolchain],
                            'simulator_revision':policy['pins']['sim_ref'], 'simulator_sha256':'a'*64,
                            'references_sha256':'b'*64, 'case_inventory_sha256':'c'*64,
                            'expected_stages':list(EXPECTED_STAGES)}
                original = copy.deepcopy(manifest)
                bound = module.bind_manifest(policy, execution, manifest, api)
                self.assertEqual(bound['workflow'], execution['binding']['workflow'])
                self.assertEqual(bound['execution_pins'], policy['pins'])
                self.assertEqual(manifest, original)
                for key in ('base', 'head', 'profile', 'controller_sha256'):
                    changed = copy.deepcopy(manifest); changed[key] = 'wrong'
                    with self.assertRaises(ValueError): module.bind_manifest(policy, execution, changed, api)
                for mutation in ('float', 'bool'):
                    with self.subTest(existing_type=mutation):
                        run['run_attempt'] = 1 if mutation == 'bool' else 2
                        active = module.prepare(policy, 101, run['run_attempt'], api)
                        altered = module.bind_manifest(policy, active, manifest, api)
                        altered['workflow']['attempt'] = True if mutation == 'bool' else 2.0
                        with self.assertRaises(ValueError): module.bind_manifest(policy, active, altered, api)
                run['run_attempt'] = 2
                root = Path(temporary)
                for name, data in (('policy', policy), ('execution', execution), ('manifest', manifest)):
                    (root/(name+'.json')).write_text(json.dumps(data))
                with mock.patch('github_preflight.ApiReader', return_value=api), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(['manifest', '--policy', str(root/'policy.json'), '--execution', str(root/'execution.json'), '--manifest', str(root/'manifest.json'), '--out', str(root/'bound.json')]), 0)
                self.assertEqual(json.loads((root/'bound.json').read_text()), bound)
