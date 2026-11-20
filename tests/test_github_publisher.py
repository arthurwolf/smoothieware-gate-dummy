"""Publication coordinator uses synthetic API state; no real API writes."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import test_ci_artifact as artifact_fixture
from test_github_worker import metadata
import ci_artifact


class SyntheticApi:
    def __init__(self, root, failure=None):
        source, binding, original_pr = artifact_fixture.CiArtifactTests().fixture(root)
        self.policy, run, self.pr, self.values, _, self.metadata = metadata()
        self.policy['pins'] = copy.deepcopy(binding['execution_pins'])
        self.pr.update(original_pr); run.update(status='completed', conclusion='success')
        if failure == 'bad-check':
            record=json.loads((source/'sim/sim.result.json').read_text());record['status']='fail'
            (source/'sim/sim.result.json').write_text(json.dumps(record))
        ci_artifact.assemble(source,binding,self.pr,root/'artifact')
        payload=io.BytesIO()
        with zipfile.ZipFile(payload,'w') as archive:
            for path in (root/'artifact').rglob('*'):
                if path.is_file(): archive.writestr(path.relative_to(root/'artifact').as_posix(),path.read_bytes())
        self.payload=payload.getvalue();self.failure=failure;self.writes=[];self.comments=[];self.checks=[]
        artifact={'id':501,'name':'gate-results-101-2','size_in_bytes':len(self.payload),'expired':False,
                  'digest':'sha256:'+hashlib.sha256(self.payload).hexdigest(),
                  'workflow_run':{'id':101,'repository_id':17,'head_repository_id':17,
                                  'head_branch':'master','head_sha':'1'*40}}
        self.values['repos/owner/firmware/actions/runs/101/artifacts?per_page=100&page=1']={
            'total_count':0 if failure=='missing' else 1,'artifacts':[] if failure=='missing' else [artifact]}

    def __call__(self, route):
        if route=='repos/owner/firmware/commits/'+self.pr['head']['sha']+'/check-runs?per_page=100&page=1&filter=all':
            return {'total_count':len(self.checks),'check_runs':copy.deepcopy(self.checks)}
        if route=='repos/owner/firmware/issues/23/comments?per_page=100&page=1':
            if self.failure=='stale':self.pr['head']['sha']='9'*40
            return copy.deepcopy(self.comments)
        if route in self.values:return copy.deepcopy(self.values[route])
        raise AssertionError('unexpected synthetic API route: '+route)

    def download(self, artifact_id):
        if artifact_id!=501:raise AssertionError('wrong artifact selected')
        return self.payload

    def write(self, method, route, payload, journal):
        # The retained uncertainty record must precede the external mutation.
        state=json.loads(Path(journal).read_text())
        if state['status']!='outcome-uncertain':raise AssertionError('write without uncertainty journal')
        self.writes.append((method,route,copy.deepcopy(payload)))
        if self.failure=='network':raise OSError('synthetic write outcome unknown')
        if route=='repos/owner/firmware/check-runs':
            response=dict(payload,id=601,app={'id':77},url='https://api.github.com/repos/owner/firmware/check-runs/601')
            if self.failure=='readback':response['head_sha']='9'*40
            if self.failure=='github-link':response['details_url']='https://github.com/owner/firmware/runs/601'
            self.values['repos/owner/firmware/check-runs/601']=response
            self.checks.append(response)
        elif route=='repos/owner/firmware/issues/23/comments':
            if self.failure=='comment-network':raise OSError('synthetic comment outcome unknown')
            response=dict(payload,id=701,user={'id':42,'login':'gate[bot]','type':'Bot'},
                          issue_url='https://api.github.com/repos/owner/firmware/issues/23')
            self.values['repos/owner/firmware/issues/comments/701']=response
            self.comments.append(response)
        else:raise AssertionError('unexpected synthetic API write: '+route)
        return copy.deepcopy(response)


class PublisherTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('github_publisher'),'trusted publication coordinator is missing')
        import github_publisher
        return github_publisher

    def test_current_good_bad_and_missing_artifact_publish_correct_head_and_conclusion(self):
        module=self.module()
        for failure,expected in [(None,'success'),('bad-check','failure'),('missing','failure'),('github-link','success')]:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);api=SyntheticApi(root,failure)
                result=module.publish(api.policy,101,api,root/'publication',{'id':42,'login':'gate[bot]'})
                self.assertEqual(result['status'],'published')
                self.assertEqual(len(api.writes),2)
                self.assertEqual(api.writes[0][2]['head_sha'],api.pr['head']['sha'])
                self.assertEqual(api.writes[0][2]['conclusion'],expected)
                self.assertEqual(json.loads((root/'publication/check.json').read_text())['status'],'published')
                self.assertEqual(json.loads((root/'publication/comment.json').read_text())['status'],'published')
                self.assertIn(api.pr['head']['sha'],(root/'publication/report/report.html').read_text())

    def test_stale_pr_prevents_any_write(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);api=SyntheticApi(root,'stale')
            with self.assertRaises(ValueError):module.publish(api.policy,101,api,root/'publication',{'id':42,'login':'gate[bot]'})
            self.assertEqual(api.writes,[])

    def test_uncertain_write_or_wrong_readback_stops_without_retry_or_comment(self):
        module=self.module()
        for failure in ('network','readback'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);api=SyntheticApi(root,failure);out=root/'publication'
                result=module.publish(api.policy,101,api,out,{'id':42,'login':'gate[bot]'})
                self.assertEqual(result['status'],'outcome-uncertain')
                self.assertEqual(len(api.writes),1)
                self.assertEqual(json.loads((out/'check.json').read_text())['status'],'outcome-uncertain')
                self.assertFalse((out/'comment.json').exists())
                with self.assertRaises(ValueError):module.publish(api.policy,101,api,out,{'id':42,'login':'gate[bot]'})
                self.assertEqual(len(api.writes),1)

    def test_newer_failed_check_without_comment_blocks_older_success(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);api=SyntheticApi(root,'bad-check');api.failure='comment-network'
            result=module.publish(api.policy,101,api,root/'newer',{'id':42,'login':'gate[bot]','app_id':77})
            self.assertEqual(result['status'],'outcome-uncertain')
            self.assertEqual(api.writes[0][2]['conclusion'],'failure')
            self.assertEqual(api.comments,[])
            older=copy.deepcopy(api);older.failure=None;older.writes=[]
            run=older.values.pop('repos/owner/firmware/actions/runs/101');run['id']=100
            older.values['repos/owner/firmware/actions/runs/100']=run
            listing=older.values.pop('repos/owner/firmware/actions/runs/101/artifacts?per_page=100&page=1')
            artifact=listing['artifacts'][0];artifact['name']='gate-results-100-2';artifact['workflow_run']['id']=100
            content=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(older.payload)) as original,zipfile.ZipFile(content,'w') as rewritten:
                for entry in original.infolist():
                    data=original.read(entry)
                    if entry.filename=='manifest.json':
                        value=json.loads(data);value['workflow']['run_id']=100;data=json.dumps(value).encode()
                    if entry.filename=='sim/sim.result.json':
                        value=json.loads(data);value['status']='pass';data=json.dumps(value).encode()
                    rewritten.writestr(entry.filename,data)
            older.payload=content.getvalue();artifact['digest']='sha256:'+hashlib.sha256(older.payload).hexdigest()
            artifact['size_in_bytes']=len(older.payload)
            older.values['repos/owner/firmware/actions/runs/100/artifacts?per_page=100&page=1']=listing
            with self.assertRaises(ValueError):module.publish(older.policy,100,older,root/'older',{'id':42,'login':'gate[bot]','app_id':77})
            self.assertEqual(older.writes,[])

    def test_cli_published_failure_and_incomplete_do_not_become_green(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);policy=root/'policy.json';policy.write_text('{"repository":"owner/firmware"}')
            for verdict,expected in [('pass',0),('warn',0),('fail',1),('error',2)]:
                with self.subTest(verdict=verdict),mock.patch.object(module,'publish',return_value={'status':'published','verdict':verdict}),contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(['--policy',str(policy),'--run-id','101','--out',str(root/'out')]),expected)
