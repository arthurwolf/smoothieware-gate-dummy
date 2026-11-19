"""Authenticate a dispatched workflow before artifacts, then bind firmware data.

Inputs named run/repository/workflow/PR must come from GitHub's authenticated API.
Policy comes from trusted configuration. Artifact fields never select those API
resources. This contract does not make an unisolated worker trustworthy.
"""
from dataclasses import dataclass
import re

from profiles import PROFILES

REVISION = r'(?:[0-9a-f]{40}|[0-9a-f]{64})'
REPOSITORY = r'(?!\.{1,2}/)[A-Za-z0-9_.-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+'


def _match(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _positive(value):
    return type(value) is int and value > 0


def _same_repository(value, policy):
    return (isinstance(value, dict) and type(value.get('id')) is int and
            value['id'] == policy['repository_id'] and value.get('full_name') == policy['repository'])


def _validate_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError('dispatch policy must be an object')
    if (not _match(REPOSITORY, policy.get('repository')) or not _positive(policy.get('repository_id')) or
            not _positive(policy.get('workflow_id')) or not isinstance(policy.get('profile'), str) or
            policy['profile'] not in PROFILES or
            not _match(r'[A-Za-z0-9._/-]{1,200}', policy.get('default_branch')) or
            not _match(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', policy.get('workflow_path'))):
        raise ValueError('invalid trusted dispatch policy identity')
    branches = policy.get('allowed_base_branches')
    if (not isinstance(branches, list) or not branches or
            any(not _match(r'[A-Za-z0-9._/-]{1,200}', branch) for branch in branches)):
        raise ValueError('invalid allowed base branches')
    pins = policy.get('pins')
    if (not isinstance(pins, dict) or not _match(REVISION, pins.get('gate_ref')) or
            not _match(REVISION, pins.get('sim_ref')) or
            not _match(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', pins.get('runtime_image')) or
            not _match(r'[0-9a-f]{64}', pins.get('toolchain_sha256')) or
            not _match(r'[0-9a-f]{64}', pins.get('controller_sha256'))):
        raise ValueError('missing or invalid trusted execution pins')
    shared = pins.get('shared_library_sha256')
    if ((policy['profile'] == 'v2-prime' or shared is not None) and
            not _match(r'[0-9a-f]{64}', shared)):
        raise ValueError('missing or invalid shared simulator library pin')
    if any(not isinstance(key, str) or (value is not None and not isinstance(value, str))
           for key, value in pins.items()):
        raise ValueError('execution pins must contain strings or explicit null values')


@dataclass(frozen=True)
class RunIdentity:
    repository: str
    repository_id: int
    pr: int
    run_id: int
    attempt: int
    workflow_id: int
    workflow_path: str
    default_branch: str
    workflow_revision: str


def validate_target(repository, workflow, policy):
    """Validate the API-owned repository and workflow before dispatch or receipt access."""
    _validate_policy(policy)
    if (not _same_repository(repository, policy) or
            repository.get('default_branch') != policy['default_branch']):
        raise ValueError('workflow repository does not match trusted policy')
    if (not isinstance(workflow, dict) or type(workflow.get('id')) is not int or
            workflow['id'] != policy['workflow_id'] or workflow.get('path') != policy['workflow_path'] or
            workflow.get('state') != 'active'):
        raise ValueError('workflow identity does not match trusted policy')


def authenticate_run(run, repository, workflow, policy, *, require_completed=True):
    """Authenticate metadata without reading any PR artifact or trusting its number."""
    validate_target(repository, workflow, policy)
    if (not isinstance(run, dict) or not _same_repository(run.get('repository'), policy) or
            not _same_repository(run.get('head_repository'), policy)):
        raise ValueError('workflow repository does not match trusted policy')
    if (type(run.get('workflow_id')) is not int or
            run['workflow_id'] != policy['workflow_id']):
        raise ValueError('workflow identity does not match trusted policy')
    path = policy['workflow_path']
    branch = policy['default_branch']
    if (run.get('event') != 'workflow_dispatch' or run.get('head_branch') != branch or
            run.get('path') not in (path, path + '@' + branch, path + '@refs/heads/' + branch) or
            not _match(REVISION, run.get('head_sha')) or
            not _positive(run.get('id')) or not _positive(run.get('run_attempt'))):
        raise ValueError('run does not have trusted default-branch dispatch provenance')
    if type(require_completed) is not bool:
        raise ValueError('invalid completion requirement')
    if require_completed and (run.get('status') != 'completed' or run.get('conclusion') not in
                              ('success', 'failure', 'neutral', 'cancelled', 'timed_out',
                               'action_required', 'skipped', 'stale', 'startup_failure')):
        raise ValueError('workflow run is not terminal')
    title = run.get('display_title')
    if not _match(r'Smoothie gate PR [1-9][0-9]{0,9}', title):
        raise ValueError('run name has no unique trusted PR number')
    number = int(title.rsplit(' ', 1)[1])
    return RunIdentity(policy['repository'], policy['repository_id'], number, run['id'],
                       run['run_attempt'], policy['workflow_id'], path, branch, run['head_sha'])


def freeze_pr(identity, pr, policy):
    """Freeze API PR identity; its revision is independent of the workflow commit."""
    _validate_policy(policy)
    if (not isinstance(identity, RunIdentity) or identity.repository != policy['repository'] or
            identity.repository_id != policy['repository_id'] or identity.workflow_id != policy['workflow_id'] or
            identity.workflow_path != policy['workflow_path'] or identity.default_branch != policy['default_branch']):
        raise ValueError('authenticated run identity no longer matches trusted policy')
    validate_pr(pr, identity.pr, policy)
    base, head = pr['base'], pr['head']
    binding = {'repository': identity.repository, 'repository_id': identity.repository_id,
               'pr': identity.pr, 'run_id': identity.run_id, 'attempt': identity.attempt,
               'workflow_id': identity.workflow_id, 'workflow_path': identity.workflow_path,
               'default_branch': identity.default_branch,
               'workflow_revision': identity.workflow_revision,
               'head_repository_id': head['repo']['id'], 'base_branch': base['ref']}
    return {'workflow': binding, 'profile': policy['profile'], 'base': base['sha'], 'head': head['sha'],
            'controller_sha256': policy['pins']['controller_sha256'],
            'execution_pins': dict(policy['pins'])}


def validate_pr(pr, number, policy):
    """Validate an API PR against trusted repository and branch policy."""
    _validate_policy(policy)
    if (not _positive(number) or number > 9999999999 or
            not isinstance(pr, dict) or type(pr.get('number')) is not int or pr['number'] != number or
            pr.get('state') != 'open'):
        raise ValueError('pull request is missing, closed or has the wrong number')
    base, head = pr.get('base'), pr.get('head')
    if (not isinstance(base, dict) or not isinstance(head, dict) or
            not _same_repository(base.get('repo'), policy) or
            base.get('ref') not in policy['allowed_base_branches'] or
            not _match(REVISION, base.get('sha')) or not _match(REVISION, head.get('sha')) or
            not isinstance(head.get('repo'), dict) or not _positive(head['repo'].get('id')) or
            not _match(REPOSITORY, head['repo'].get('full_name'))):
        raise ValueError('pull request repositories, branch or revisions are invalid')


def artifact_problems(identity, pr, manifest, policy):
    """Reject stale or forged bindings; the caller separately validates stage data."""
    try:
        expected = freeze_pr(identity, pr, policy)
    except ValueError as error:
        return [str(error)]
    return binding_problems(manifest, expected)



def validate_binding_policy(binding, policy):
    """Reject reuse of an authenticated frozen binding under another policy."""
    _validate_policy(policy)
    if (not isinstance(binding, dict) or binding.get('profile') != policy['profile'] or
            binding.get('execution_pins') != policy['pins']):
        raise ValueError('authenticated binding does not match execution policy')
    workflow = binding.get('workflow')
    fields = ('repository', 'repository_id', 'workflow_id', 'workflow_path', 'default_branch')
    if (not isinstance(workflow, dict) or
            any(type(workflow.get(key)) is not type(policy[key]) or workflow[key] != policy[key]
                for key in fields) or workflow.get('base_branch') not in policy['allowed_base_branches']):
        raise ValueError('authenticated binding no longer matches trusted workflow policy')


def binding_problems(manifest, expected):
    """Compare artifact data with an already authenticated frozen binding."""
    if not isinstance(manifest, dict):
        return ['manifest must be an object']
    problems = []
    for key in ('profile', 'base', 'head'):
        if manifest.get(key) != expected[key]:
            problems.append('manifest ' + key + ' does not match the current PR policy')
    if manifest.get('controller_sha256') != expected['execution_pins']['controller_sha256']:
        problems.append('manifest controller_sha256 does not match trusted execution policy')
    for section in ('workflow', 'execution_pins'):
        actual = manifest.get(section)
        if (not isinstance(actual, dict) or set(actual) != set(expected[section]) or
                any(type(actual.get(key)) is not type(value) or actual.get(key) != value
                    for key, value in expected[section].items())):
            problems.append('manifest ' + section + ' does not match authenticated run or policy')
    return problems


@dataclass(frozen=True)
class ArtifactIdentity:
    id: int
    name: str
    size_bytes: int
    sha256: str


def select_artifact(identity, listing):
    """Select API metadata only after run authentication, before downloading bytes.

    Caller must provide the complete run-scoped listing. Download routes are
    constructed from repository and returned numeric ID, never artifact URLs.
    The attempt belongs in the name because artifact run metadata omits it.
    """
    if not isinstance(identity, RunIdentity) or not isinstance(listing, dict):
        raise ValueError('artifact selection requires an authenticated run and API listing')
    artifacts = listing.get('artifacts')
    count = listing.get('total_count')
    if (type(count) is not int or not isinstance(artifacts, list) or
            not 0 <= count <= 1000 or count != len(artifacts) or
            any(not isinstance(item, dict) for item in artifacts)):
        raise ValueError('artifact inventory is malformed, incomplete or excessive')
    name = f'gate-results-{identity.run_id}-{identity.attempt}'
    matches = [item for item in artifacts if item.get('name') == name]
    if len(matches) != 1:
        raise ValueError('current run attempt must have exactly one result artifact')
    artifact = matches[0]
    size = artifact.get('size_in_bytes')
    digest = artifact.get('digest')
    if (not _positive(artifact.get('id')) or artifact.get('expired') is not False or
            type(size) is not int or not 0 < size <= 25 * 1024**2 or
            not _match(r'sha256:[0-9a-f]{64}', digest)):
        raise ValueError('result artifact identity, expiry, size or digest is invalid')
    run = artifact.get('workflow_run')
    expected = {'id': identity.run_id, 'repository_id': identity.repository_id,
                'head_repository_id': identity.repository_id,
                'head_branch': identity.default_branch, 'head_sha': identity.workflow_revision}
    if (not isinstance(run, dict) or any(type(run.get(key)) is not type(value) or
                                       run.get(key) != value for key, value in expected.items())):
        raise ValueError('result artifact does not belong to the authenticated workflow run')
    return ArtifactIdentity(artifact['id'], name, size, digest.removeprefix('sha256:'))
