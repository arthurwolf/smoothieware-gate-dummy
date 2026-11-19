"""Synthetic publication requests must preserve firmware and report identity."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import test_ci_artifact as artifact_fixture
from test_dispatch_identity import fixture
from gate_result import make_result
from profiles import EXPECTED_STAGES


class PublicationPlanTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('github_publication'), 'trusted publication planning is missing')
        import github_publication
        return github_publication

    def data(self, root):
        source, binding, _ = artifact_fixture.CiArtifactTests().fixture(root)
        policy = fixture()[0]; policy['pins'] = copy.deepcopy(binding['execution_pins'])
        manifest = json.loads((source/'manifest.json').read_text())
        # The source fixture builds real commits; publication controls use
        # fixed synthetic revisions distinct from the workflow's 1... commit.
        binding['base'] = manifest['base'] = '2'*40
        binding['head'] = manifest['head'] = '3'*40
        records = [make_result(stage, 'pass', stage, 'synthetic pass') for stage in EXPECTED_STAGES]
        return policy, binding, manifest, records

    def test_check_targets_firmware_head_and_preserves_failure_or_incomplete(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            policy, binding, manifest, records = self.data(Path(temporary))
            for status, expected in [('pass','success'),('warn','success'),('fail','failure'),('error','failure')]:
                with self.subTest(status=status):
                    changed = copy.deepcopy(records); changed[0]['status'] = status
                    plan = module.plan(policy, binding, manifest, changed, [], {'id':42,'login':'gate[bot]'})
                    self.assertEqual(plan['check']['head_sha'], '3'*40)
                    self.assertEqual(plan['check']['conclusion'], expected)
                    self.assertEqual(plan['check']['status'], 'completed')
                    self.assertEqual(plan['comment_route'], 'repos/owner/firmware/issues/23/comments')
                    self.assertEqual(plan['comment_method'], 'POST')

    def test_model_outage_is_nonfatal_but_missing_or_duplicate_stage_is_incomplete(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            args = list(self.data(Path(temporary)))
            args[3][-1]['status'] = 'skipped'
            # Identify by stage rather than relying on the registry's ordering.
            for record in args[3]:
                record['status'] = 'skipped' if record['stage']=='ai-review' else 'pass'
            result = module.plan(*args, [], {'id':42,'login':'gate[bot]'})
            self.assertEqual(result['check']['conclusion'], 'success')
            for records in (args[3][1:], args[3]+[args[3][0]]):
                result = module.plan(*args[:3], records, [], {'id':42,'login':'gate[bot]'})
                self.assertEqual(result['check']['conclusion'], 'failure')

    def test_only_expected_bot_comment_is_selected_and_newer_provenance_is_protected(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            args = self.data(Path(temporary)); bot = {'id':42,'login':'gate[bot]'}
            original = module.plan(*args, [], bot)
            comment = {'id':88, 'user':{'id':42,'login':'gate[bot]','type':'Bot'},
                       'body':original['comment']['body']}
            forged = copy.deepcopy(comment); forged['id']=89; forged['user']['id']=99
            selected = module.plan(*args, [forged,comment], bot)
            self.assertEqual(selected['comment_method'], 'PATCH')
            self.assertEqual(selected['comment_route'], 'repos/owner/firmware/issues/comments/88')
            with self.assertRaises(ValueError): module.plan(*args,[comment,copy.deepcopy(comment)],bot)
            newer = copy.deepcopy(args[1]); newer['workflow']['run_id']=102
            newer_manifest=copy.deepcopy(args[2]);newer_manifest['workflow']['run_id']=102
            comment['body']=module.plan(args[0],newer,newer_manifest,args[3],[],bot)['comment']['body']
            with self.assertRaises(ValueError):module.plan(*args,[comment],bot)

    def test_wrong_manifest_identity_and_partial_comment_inventory_are_rejected(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            args=list(self.data(Path(temporary)));bot={'id':42,'login':'gate[bot]'}
            args[2]['head']='9'*40
            with self.assertRaises(ValueError):module.plan(*args,[],bot)
            args[2]['head']=args[1]['head']
            with self.assertRaises(ValueError):module.plan(*args,[{}]*100,bot)
