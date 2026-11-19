"""Metadata authentication must finish before any artifact is requested."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_dispatch_identity import fixture


class PreflightTests(unittest.TestCase):
    def test_api_metadata_authenticates_before_artifact_selection(self):
        import github_preflight
        policy,repository,workflow,run,pr,manifest=fixture()
        artifact={'id':501,'name':'gate-results-101-2','size_in_bytes':1000,'expired':False,
                  'digest':'sha256:'+'f'*64,'workflow_run':{'id':101,'repository_id':17,
                  'head_repository_id':17,'head_branch':'master','head_sha':'1'*40}}
        responses={'repos/owner/firmware':repository,
                   'repos/owner/firmware/actions/runs/101':run,
                   'repos/owner/firmware/actions/workflows/51':workflow,
                   'repos/owner/firmware/pulls/23':pr,
                   'repos/owner/firmware/actions/runs/101/artifacts?per_page=100&page=1':
                       {'total_count':1,'artifacts':[artifact]}}
        calls=[]
        def api(path):
            calls.append(path)
            return copy.deepcopy(responses[path])
        result=github_preflight.prepare(policy,101,api)
        self.assertEqual(result['manifest_binding'],manifest)
        self.assertEqual(result['artifact']['id'],501)
        self.assertTrue(calls[-1].endswith('artifacts?per_page=100&page=1'))
        for field,value in [('event','pull_request'),('head_branch','contributor')]:
            run[field]=value;calls.clear()
            with self.assertRaises(ValueError):github_preflight.prepare(policy,101,api)
            self.assertFalse(any('artifacts?' in path for path in calls))
            run[field]= 'workflow_dispatch' if field=='event' else 'master'
        pr['head']['sha']='invalid';calls.clear()
        with self.assertRaises(ValueError):github_preflight.prepare(policy,101,api)
        self.assertFalse(any('artifacts?' in path for path in calls))

    def test_invalid_policy_and_run_id_cannot_choose_an_api_route(self):
        import github_preflight
        policy,*_=fixture()
        def api(path):raise AssertionError('invalid identity reached API: '+path)
        for run_id in (True,0,'101/../../secrets'):
            with self.assertRaises(ValueError):github_preflight.prepare(policy,run_id,api)
        policy['repository']='../attacker'
        with self.assertRaises(ValueError):github_preflight.prepare(policy,101,api)


class ApiReaderTests(unittest.TestCase):
    def test_reader_uses_fixed_host_get_version_and_bounded_capture(self):
        import github_preflight
        from sandbox_process import ProcessOutput
        from unittest import mock
        with mock.patch.object(github_preflight.sandbox_process,'run',return_value=ProcessOutput(0,b'{"id":17}',b'',False,None)) as execute:
            read=github_preflight.ApiReader('owner/firmware')
            self.assertEqual(read('repos/owner/firmware/actions/runs/101'),{'id':17})
        command=execute.call_args.args[0]
        self.assertEqual(command[:2],['gh','api'])
        self.assertEqual(command[command.index('--hostname')+1],'github.com')
        self.assertEqual(command[command.index('--method')+1],'GET')
        self.assertIn('X-GitHub-Api-Version: 2026-03-10',command)
        self.assertEqual(execute.call_args.kwargs['max_stdout_bytes'],2*1024**2)
        self.assertEqual(execute.call_args.kwargs['timeout_seconds'],30)

    def test_reader_rejects_other_routes_failures_and_malformed_objects(self):
        import github_preflight
        from sandbox_process import ProcessOutput
        from unittest import mock
        read=github_preflight.ApiReader('owner/firmware')
        for route in ['https://attacker.invalid','repos/other/firmware','repos/owner/firmware/../secrets',
                      'repos/owner/firmware/actions/runs/101?token=leak','graphql','repos/owner/firmware/actions/runs/101/dispatches']:
            with self.subTest(route=route),mock.patch.object(github_preflight.sandbox_process,'run') as execute:
                with self.assertRaises(ValueError):read(route)
                execute.assert_not_called()
        for output in [ProcessOutput(7,b'{"id":17}',b'API failure',False,None),
                       ProcessOutput(0,b'{"id":17}',b'',False,'stdout_overflow'),
                       ProcessOutput(0,b'[]',b'',False,None),ProcessOutput(0,b'{',b'',False,None),
                       ProcessOutput(0,b'{"id":1,"id":2}',b'',False,None)]:
            with self.subTest(output=output),mock.patch.object(github_preflight.sandbox_process,'run',return_value=output):
                with self.assertRaises(ValueError):read('repos/owner/firmware')


class PreflightCliTests(unittest.TestCase):
    def test_cli_writes_binding_only_after_success_and_refuses_existing_output(self):
        import github_preflight
        import json,tempfile
        from unittest import mock
        policy,*_=fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'policy.json';source.write_text(json.dumps(policy));out=root/'prepared.json'
            argv=['github_preflight.py','--policy',str(source),'--run-id','101','--out',str(out)]
            value={'manifest_binding':{'head':'a'*40},'artifact':{'id':501}}
            with mock.patch.object(sys,'argv',argv),mock.patch.object(github_preflight,'prepare',return_value=value):
                self.assertEqual(github_preflight.main(),0)
                self.assertEqual(json.loads(out.read_text()),value)
                self.assertEqual(github_preflight.main(),1)
                self.assertEqual(json.loads(out.read_text()),value)
            missing=root/'failed.json';argv[-1]=str(missing)
            with mock.patch.object(sys,'argv',argv),mock.patch.object(github_preflight,'prepare',side_effect=ValueError('untrusted workflow')):
                self.assertEqual(github_preflight.main(),1)
                self.assertFalse(missing.exists())


class ArtifactDownloadTests(unittest.TestCase):
    def test_download_uses_numeric_repository_route_and_bounded_binary_capture(self):
        import github_preflight
        from sandbox_process import ProcessOutput
        from unittest import mock
        with mock.patch.object(github_preflight.sandbox_process,'run',return_value=ProcessOutput(0,b'ZIP fixture',b'',False,None)) as execute:
            read=github_preflight.ApiReader('owner/firmware')
            self.assertEqual(read.download(501),b'ZIP fixture')
        command=execute.call_args.args[0]
        self.assertEqual(command[-1],'repos/owner/firmware/actions/artifacts/501/zip')
        self.assertEqual(command[command.index('--hostname')+1],'github.com')
        self.assertEqual(command[command.index('--method')+1],'GET')
        self.assertEqual(execute.call_args.kwargs['max_stdout_bytes'],25*1024**2)
        self.assertEqual(execute.call_args.kwargs['timeout_seconds'],120)

    def test_download_rejects_invalid_identity_and_failure_despite_returned_bytes(self):
        import github_preflight
        from sandbox_process import ProcessOutput
        from unittest import mock
        read=github_preflight.ApiReader('owner/firmware')
        for artifact_id in [True,0,'501/../../secrets']:
            with mock.patch.object(github_preflight.sandbox_process,'run') as execute:
                with self.assertRaises(ValueError):read.download(artifact_id)
                execute.assert_not_called()
        for output in [ProcessOutput(7,b'forged success',b'',False,None),
                       ProcessOutput(0,b'truncated zip',b'',False,'stdout_overflow')]:
            with mock.patch.object(github_preflight.sandbox_process,'run',return_value=output):
                with self.assertRaises(ValueError):read.download(501)


class ResultsDownloadTests(unittest.TestCase):
    def test_download_verifies_digest_binding_and_freshness_before_writing(self):
        import github_preflight
        import hashlib,json,tempfile
        from unittest import mock
        from test_artifact_archive import archive
        policy,_,_,_,_,binding=fixture()
        for problem in (None,'digest','manifest','stale'):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as temporary:
                manifest=copy.deepcopy(binding)
                if problem=='manifest':manifest['head']='9'*40
                payload=archive([('manifest.json',json.dumps(manifest).encode())])
                prepared={'manifest_binding':binding,'artifact':{'id':501,'name':'gate-results-101-2',
                          'size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}}
                if problem=='digest':prepared['artifact']['sha256']='0'*64
                latest=copy.deepcopy(prepared)
                if problem=='stale':latest['manifest_binding']['head']='8'*40
                reader=mock.Mock();reader.download.return_value=payload
                destination=Path(temporary)/'results'
                with mock.patch.object(github_preflight,'prepare',side_effect=[prepared,latest]):
                    if problem:
                        with self.assertRaises(ValueError):github_preflight.download_results(policy,101,reader,destination)
                        self.assertFalse(destination.exists())
                    else:
                        result=github_preflight.download_results(policy,101,reader,destination)
                        self.assertEqual(result,prepared)
                        self.assertEqual(json.loads((destination/'manifest.json').read_text()),binding)
                reader.download.assert_called_once_with(501)


class DownloadCliTests(unittest.TestCase):
    def test_cli_downloads_to_fresh_directory_before_saving_binding(self):
        import github_preflight
        import json,tempfile
        from unittest import mock
        policy,*_=fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'policy.json';source.write_text(json.dumps(policy))
            output=root/'prepared.json';destination=root/'results'
            argv=['github_preflight.py','--policy',str(source),'--run-id','101','--out',str(output),
                  '--download-to',str(destination)]
            value={'manifest_binding':{'head':'a'*40},'artifact':{'id':501}}
            with mock.patch.object(sys,'argv',argv),mock.patch.object(github_preflight,'download_results',return_value=value) as download:
                self.assertEqual(github_preflight.main(),0)
                self.assertEqual(download.call_args.args[:2],(policy,101))
                self.assertEqual(str(download.call_args.args[3]),str(destination))
                self.assertEqual(json.loads(output.read_text()),value)
                download.reset_mock()
                self.assertEqual(github_preflight.main(),1)
                download.assert_not_called()
