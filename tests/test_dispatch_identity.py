"""Dispatched workflow provenance is distinct from the firmware PR revision."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


def fixture():
    policy = {'repository': 'owner/firmware', 'repository_id': 17, 'default_branch': 'master',
              'workflow_id': 51, 'workflow_path': '.github/workflows/gate-pr.yml',
              'allowed_base_branches': ['master', 'edge'], 'profile': 'v2-prime',
              'pins': {'gate_ref': 'a' * 40, 'sim_ref': 'b' * 40,
                       'shared_library_sha256': 'c' * 64, 'runtime_image': 'registry.invalid/gate@sha256:' + 'd' * 64,
                       'toolchain_sha256': 'e' * 64, 'controller_sha256': '6' * 64}}
    repository = {'id': 17, 'full_name': 'owner/firmware', 'default_branch': 'master'}
    workflow = {'id': 51, 'path': '.github/workflows/gate-pr.yml', 'state': 'active'}
    run = {'id': 101, 'run_attempt': 2, 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'failure',
           'repository': {'id': 17, 'full_name': 'owner/firmware'},
           'head_repository': {'id': 17, 'full_name': 'owner/firmware'},
           'workflow_id': 51, 'path': '.github/workflows/gate-pr.yml@master',
           'head_branch': 'master', 'head_sha': '1' * 40, 'display_title': 'Smoothie gate PR 23'}
    pr = {'number': 23, 'state': 'open', 'base': {'sha': '2' * 40, 'ref': 'edge', 'repo': repository},
          'head': {'sha': '3' * 40, 'repo': {'id': 19, 'full_name': 'fork/firmware'}}}
    binding = {'repository': 'owner/firmware', 'repository_id': 17, 'pr': 23, 'run_id': 101, 'attempt': 2,
               'workflow_id': 51, 'workflow_path': '.github/workflows/gate-pr.yml', 'default_branch': 'master',
               'workflow_revision': '1' * 40, 'head_repository_id': 19, 'base_branch': 'edge'}
    manifest = {'profile': 'v2-prime', 'base': '2' * 40, 'head': '3' * 40,
                'controller_sha256': '6' * 64,
                'workflow': binding, 'execution_pins': copy.deepcopy(policy['pins'])}
    return policy, repository, workflow, run, pr, manifest


class DispatchIdentityTests(unittest.TestCase):
    def test_controller_digest_is_required_and_bound_to_trusted_policy(self):
        import dispatch_identity
        policy, repository, workflow, run, pr, manifest = fixture()
        policy['pins']['controller_sha256'] = '6' * 64
        manifest['execution_pins']['controller_sha256'] = '6' * 64
        identity = dispatch_identity.authenticate_run(run, repository, workflow, policy)
        for observed in (None, '7' * 64, '6' * 63, True):
            with self.subTest(observed=observed):
                manifest['controller_sha256'] = observed
                self.assertTrue(dispatch_identity.artifact_problems(identity, pr, manifest, policy))
        manifest['controller_sha256'] = '6' * 64
        self.assertEqual(dispatch_identity.artifact_problems(identity, pr, manifest, policy), [])
        del policy['pins']['controller_sha256']
        with self.assertRaises(ValueError):
            dispatch_identity.authenticate_run(run, repository, workflow, policy)

    def test_authenticates_failed_run_without_confusing_workflow_and_pr_commits(self):
        import dispatch_identity
        policy, repository, workflow, run, pr, manifest = fixture()
        identity = dispatch_identity.authenticate_run(run, repository, workflow, policy)
        self.assertEqual(identity.pr, 23)
        self.assertEqual(identity.workflow_revision, '1' * 40)
        self.assertEqual(dispatch_identity.artifact_problems(identity, pr, manifest, policy), [])

    def test_run_provenance_is_required_before_artifact_access(self):
        import dispatch_identity
        mutations = [('event', 'pull_request'), ('run_attempt', True), ('id', 0), ('workflow_id', 52),
                     ('head_branch', 'contributor'), ('head_sha', 'short'),
                     ('path', '.github/workflows/other.yml@master'), ('status', 'in_progress'),
                     ('display_title', 'Smoothie gate PR 23; command'), ('display_title', 'Smoothie gate PR 0')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                policy, repository, workflow, run, pr, manifest = fixture()
                run[field] = value
                with self.assertRaises(ValueError):
                    dispatch_identity.authenticate_run(run, repository, workflow, policy)
        for source in ('repository', 'head_repository'):
            policy, repository, workflow, run, pr, manifest = fixture()
            run[source]['id'] = 999
            with self.assertRaises(ValueError):
                dispatch_identity.authenticate_run(run, repository, workflow, policy)

    def test_stale_forged_or_wrong_attempt_artifacts_are_rejected(self):
        import dispatch_identity
        for section, field, value in [('workflow', 'attempt', 1), ('workflow', 'run_id', 100),
                                     ('workflow', 'workflow_revision', '3' * 40), ('workflow', 'pr', 24),
                                     ('workflow', 'head_repository_id', 20),
                                     ('execution_pins', 'gate_ref', 'f' * 40),
                                     ('execution_pins', 'runtime_image', 'registry.invalid/gate:latest'),
                                     (None, 'head', '4' * 40), (None, 'profile', 'v1-lpc1768')]:
            with self.subTest(section=section, field=field):
                policy, repository, workflow, run, pr, manifest = fixture()
                identity = dispatch_identity.authenticate_run(run, repository, workflow, policy)
                (manifest if section is None else manifest[section])[field] = value
                self.assertTrue(dispatch_identity.artifact_problems(identity, pr, manifest, policy))
        for section, field, value in [('head', 'sha', '4' * 40), ('base', 'sha', '5' * 40),
                                     ('base', 'ref', 'other'), (None, 'state', 'closed')]:
            policy, repository, workflow, run, pr, manifest = fixture()
            identity = dispatch_identity.authenticate_run(run, repository, workflow, policy)
            (pr if section is None else pr[section])[field] = value
            self.assertTrue(dispatch_identity.artifact_problems(identity, pr, manifest, policy))


class DispatchPolicyBindingTests(unittest.TestCase):
    def test_policy_cannot_change_after_run_authentication(self):
        import dispatch_identity
        for field, value in [('workflow_id', 52), ('workflow_path', '.github/workflows/other.yml'),
                             ('default_branch', 'edge')]:
            with self.subTest(field=field):
                policy, repository, workflow, run, pr, manifest = fixture()
                identity = dispatch_identity.authenticate_run(run, repository, workflow, policy)
                policy[field] = value
                self.assertTrue(dispatch_identity.artifact_problems(identity, pr, manifest, policy))

    def test_malformed_policy_fails_as_validation_error(self):
        import dispatch_identity
        policy, repository, workflow, run, pr, manifest = fixture()
        policy['profile'] = []
        with self.assertRaises(ValueError):
            dispatch_identity.authenticate_run(run, repository, workflow, policy)


class ArtifactSelectionTests(unittest.TestCase):
    def listing(self, identity):
        return {'total_count':1,'artifacts':[{'id':501,'name':'gate-results-101-2','expired':False,
                'size_in_bytes':1234,'digest':'sha256:'+'f'*64,
                'workflow_run':{'id':101,'repository_id':17,'head_repository_id':17,
                                'head_branch':'master','head_sha':'1'*40}}]}

    def test_selects_only_current_attempt_without_using_download_urls(self):
        import dispatch_identity
        policy,repository,workflow,run,_,_=fixture()
        identity=dispatch_identity.authenticate_run(run,repository,workflow,policy)
        listing=self.listing(identity)
        listing['artifacts'][0]['archive_download_url']='https://attacker.invalid/token-steal'
        selected=dispatch_identity.select_artifact(identity,listing)
        self.assertEqual((selected.id,selected.name,selected.size_bytes,selected.sha256),
                         (501,'gate-results-101-2',1234,'f'*64))
        self.assertFalse(hasattr(selected,'url'))

    def test_rejects_stale_duplicate_expired_truncated_or_forged_metadata(self):
        import dispatch_identity
        policy,repository,workflow,run,_,_=fixture()
        identity=dispatch_identity.authenticate_run(run,repository,workflow,policy)
        mutations=[(None,'name','gate-results-101-1'),(None,'expired',True),
                   (None,'expired',0),(None,'id',True),(None,'size_in_bytes',True),
                   (None,'size_in_bytes',25*1024**2+1),(None,'digest','sha256:short'),
                   ('workflow_run','id',102),('workflow_run','repository_id',18),
                   ('workflow_run','head_repository_id',19),('workflow_run','head_sha','3'*40),
                   ('workflow_run','head_branch','contributor')]
        for section,key,value in mutations:
            with self.subTest(section=section,key=key,value=value):
                listing=self.listing(identity);artifact=listing['artifacts'][0]
                (artifact if section is None else artifact[section])[key]=value
                with self.assertRaises(ValueError):dispatch_identity.select_artifact(identity,listing)
        for mode in ('duplicate','truncated','missing'):
            listing=self.listing(identity)
            if mode=='duplicate':
                listing['artifacts'].append(copy.deepcopy(listing['artifacts'][0]));listing['total_count']=2
            elif mode=='truncated':listing['total_count']=2
            else:listing={'total_count':0,'artifacts':[]}
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                dispatch_identity.select_artifact(identity,listing)
