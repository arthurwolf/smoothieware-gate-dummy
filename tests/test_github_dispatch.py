"""Catch contributor-selected dispatch targets and ambiguous API receipts."""
import copy
import importlib.util
from pathlib import Path
import sys
import json
import tempfile
import os
import stat
import io
import contextlib
import subprocess
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_dispatch_identity import fixture


class GithubDispatchTests(unittest.TestCase):
    def test_direct_isolated_entrypoints_load_without_pythonpath(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        for name in ('github_dispatch.py', 'github_preflight.py'):
            with self.subTest(script=name):
                done = subprocess.run([sys.executable, '-B', '-I', str(scripts / name), '--help'],
                                      capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn('--policy', done.stdout)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('github_dispatch'),
                             'trusted dispatch contract is not implemented')
        import github_dispatch
        return github_dispatch

    def test_request_uses_only_trusted_workflow_branch_and_pr_number(self):
        module = self.module()
        policy, repository, workflow, _, pr, _ = fixture()
        pr.update(title='$(touch injected)', body='ref: attacker', ref='attacker')
        self.assertEqual(module.request(policy, repository, workflow, pr), {
            'route': 'repos/owner/firmware/actions/workflows/51/dispatches',
            'body': {'ref': 'master', 'inputs': {'pr_number': '23'}}})

    def test_invalid_api_identity_never_produces_a_request(self):
        module = self.module()
        original = fixture()
        for index, key, value in ((1, 'id', 18), (1, 'default_branch', 'attacker'),
                                  (2, 'id', 52), (2, 'state', 'disabled_manually'),
                                  (2, 'path', '.github/workflows/attacker.yml'),
                                  (4, 'number', True), (4, 'number', 0),
                                  (4, 'number', 10000000000), (4, 'state', 'closed')):
            with self.subTest(index=index, key=key, value=value):
                policy, repository, workflow, _, pr, _ = copy.deepcopy(original)
                (policy, repository, workflow, None, pr)[index][key] = value
                with self.assertRaises(ValueError):
                    module.request(policy, repository, workflow, pr)
        policy, repository, workflow, _, pr, _ = fixture()
        pr['base']['ref'] = 'unapproved'
        with self.assertRaises(ValueError):
            module.request(policy, repository, workflow, pr)

    def test_receipt_binds_exact_run_and_profile_without_search(self):
        module = self.module()
        for profile in ('v1-lpc1768', 'v2-prime'):
            policy, *_ = fixture()
            policy['profile'] = profile
            response = {'workflow_run_id': 107,
                        'run_url': 'https://api.github.com/repos/owner/firmware/actions/runs/107',
                        'html_url': 'https://github.com/owner/firmware/actions/runs/107'}
            self.assertEqual(module.receipt(policy, 23, response), {
                'repository': 'owner/firmware', 'repository_id': 17,
                'workflow_id': 51, 'workflow_path': '.github/workflows/gate-pr.yml',
                'default_branch': 'master', 'profile': profile, 'pr': 23, 'run_id': 107,
                'run_url': response['run_url'], 'html_url': response['html_url']})

    def test_missing_or_conflicting_dispatch_response_is_uncertain(self):
        module = self.module()
        policy, *_ = fixture()
        good = {'workflow_run_id': 107,
                'run_url': 'https://api.github.com/repos/owner/firmware/actions/runs/107',
                'html_url': 'https://github.com/owner/firmware/actions/runs/107'}
        for key, value in (('workflow_run_id', None), ('workflow_run_id', True),
                           ('workflow_run_id', '107'), ('workflow_run_id', 0),
                           ('run_url', 'https://api.github.com/repos/fork/firmware/actions/runs/107'),
                           ('html_url', 'https://github.com/owner/firmware/actions/runs/108'),
                           ('html_url', 'https://github.com.evil/owner/firmware/actions/runs/107')):
            with self.subTest(key=key, value=value):
                response = dict(good, **{key: value})
                with self.assertRaises(ValueError):
                    module.receipt(policy, 23, response)
        for response in (None, {}, [], good):
            with self.assertRaises(ValueError):
                module.receipt(policy, True, response)

    def test_one_shot_dispatch_persists_exact_receipt_and_refuses_duplicate(self):
        module = self.module()
        from sandbox_process import ProcessOutput
        policy, repository, workflow, _, pr, _ = fixture()
        data = {'workflow_run_id': 107,
                'run_url': 'https://api.github.com/repos/owner/firmware/actions/runs/107',
                'html_url': 'https://github.com/owner/firmware/actions/runs/107'}
        self.assertTrue(hasattr(module, 'dispatch_once'), 'one-shot dispatch is missing')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'receipt.json'
            def transport(command, **options):
                self.assertEqual(json.loads(output.read_text())['status'], 'outcome-uncertain')
                self.assertEqual(command[-5:], ['repos/owner/firmware/actions/workflows/51/dispatches',
                                                '-f', 'ref=master', '-f', 'inputs[pr_number]=23'])
                return ProcessOutput(0, json.dumps(data).encode(), b'', False, None)
            with mock.patch('sandbox_process.run', side_effect=transport) as call:
                result = module.dispatch_once(policy, repository, workflow, pr, output)
                self.assertEqual(result['status'], 'dispatched')
                self.assertEqual(result['receipt']['run_id'], 107)
                self.assertEqual(json.loads(output.read_text()), result)
                with self.assertRaises(FileExistsError):
                    module.dispatch_once(policy, repository, workflow, pr, output)
                self.assertEqual(call.call_count, 1)

    def test_lost_dispatch_response_retains_uncertainty_without_retry(self):
        module = self.module()
        from sandbox_process import ProcessOutput
        policy, repository, workflow, _, pr, _ = fixture()
        self.assertTrue(hasattr(module, 'dispatch_once'), 'one-shot dispatch is missing')
        outcomes = (ProcessOutput(1, b'', b'network lost', False, 'timeout'),
                    ProcessOutput(0, b'{}', b'', False, None),
                    ProcessOutput(0, b'{"workflow_run_id":107,"workflow_run_id":108}', b'', False, None),
                    OSError('missing transport'))
        for outcome in outcomes:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'receipt.json'
                options = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
                with mock.patch('sandbox_process.run', **options) as call:
                    result = module.dispatch_once(policy, repository, workflow, pr, output)
                    self.assertEqual(result['status'], 'outcome-uncertain')
                    self.assertEqual(result['repository'], 'owner/firmware')
                    self.assertEqual(result['pr'], 23)
                    self.assertEqual(json.loads(output.read_text()), result)
                    self.assertEqual(call.call_count, 1)

    def test_initial_journal_directory_is_durable_before_post(self):
        module = self.module()
        from sandbox_process import ProcessOutput
        policy, repository, workflow, _, pr, _ = fixture()
        flushed_directory = []
        real_fsync = os.fsync
        def flush(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                flushed_directory.append(True)
            real_fsync(descriptor)
        def transport(*args, **kwargs):
            self.assertTrue(flushed_directory, 'journal directory was not flushed before POST')
            return ProcessOutput(1, b'', b'', False, 'timeout')
        with tempfile.TemporaryDirectory() as directory, mock.patch('os.fsync', side_effect=flush), \
                mock.patch('sandbox_process.run', side_effect=transport):
            module.dispatch_once(policy, repository, workflow, pr, Path(directory) / 'receipt.json')

    def test_final_persistence_failure_preserves_complete_uncertainty_record(self):
        module = self.module()
        from sandbox_process import ProcessOutput
        policy, repository, workflow, _, pr, _ = fixture()
        response = {'workflow_run_id': 107,
                    'run_url': 'https://api.github.com/repos/owner/firmware/actions/runs/107',
                    'html_url': 'https://github.com/owner/firmware/actions/runs/107'}
        captured = ProcessOutput(0, json.dumps(response).encode(), b'', False, None)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'receipt.json'
            with mock.patch('sandbox_process.run', return_value=captured), \
                    mock.patch('os.replace', side_effect=OSError('disk failure')) as replace:
                try:
                    module.dispatch_once(policy, repository, workflow, pr, output)
                except OSError:
                    pass
                self.assertEqual(replace.call_count, 1, 'final receipt must replace atomically')
                journal = json.loads(output.read_text())
                self.assertEqual(journal['status'], 'outcome-uncertain')
                self.assertEqual(journal['pr'], 23)
                self.assertEqual(journal['repository'], 'owner/firmware')

    def test_cli_reads_authenticated_metadata_and_reports_exact_run(self):
        module = self.module()
        from sandbox_process import ProcessOutput
        self.assertTrue(hasattr(module, 'main'), 'dispatch CLI is missing')
        policy, repository, workflow, _, pr, _ = fixture()
        values = {'repos/owner/firmware': repository,
                  'repos/owner/firmware/actions/workflows/51': workflow,
                  'repos/owner/firmware/pulls/23': pr}
        response = {'workflow_run_id': 107,
                    'run_url': 'https://api.github.com/repos/owner/firmware/actions/runs/107',
                    'html_url': 'https://github.com/owner/firmware/actions/runs/107'}
        with tempfile.TemporaryDirectory() as directory:
            config, output = Path(directory) / 'policy.json', Path(directory) / 'receipt.json'
            config.write_text(json.dumps(policy))
            stream = io.StringIO()
            with mock.patch('github_preflight.ApiReader', return_value=values.__getitem__), \
                    mock.patch('sandbox_process.run', return_value=ProcessOutput(
                        0, json.dumps(response).encode(), b'', False, None)), contextlib.redirect_stdout(stream):
                code = module.main(['--policy', str(config), '--pr-number', '23', '--out', str(output)])
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in stream.getvalue().splitlines()]
            self.assertEqual(events[-1]['status'], 'dispatched')
            self.assertEqual(events[-1]['run_id'], 107)
            self.assertEqual(json.loads(output.read_text())['receipt']['run_id'], 107)

    def test_cli_rejects_occupied_output_and_bad_pr_before_network(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'main'), 'dispatch CLI is missing')
        policy, *_ = fixture()
        with tempfile.TemporaryDirectory() as directory:
            config, output = Path(directory) / 'policy.json', Path(directory) / 'receipt.json'
            config.write_text(json.dumps(policy))
            output.write_text('preserve')
            with mock.patch('github_preflight.ApiReader') as network, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(['--policy', str(config), '--pr-number', '23', '--out', str(output)]), 1)
                output.unlink()
                self.assertEqual(module.main(['--policy', str(config), '--pr-number', '0', '--out', str(output)]), 1)
                network.assert_not_called()
            self.assertFalse(output.exists())
