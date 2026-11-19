"""Authenticate metadata before any dispatched-run artifact access.

The injected API reader must return authenticated GitHub REST objects from
fixed API routes. Download and extraction require a second metadata check; this module never publishes results.
"""
import argparse
from dataclasses import asdict
import sys
import os
import json
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox_process

import dispatch_identity
import artifact_archive


def authenticate_inputs(policy, run_id, api, *, attempt=None):
    """Read current API-owned run and PR identity before any artifact or worker input.

    A supplied attempt selects worker preparation and requires its active run.
    Reporter preparation instead requires a terminal run.
    """
    dispatch_identity._validate_policy(policy)
    if type(run_id) is not int or run_id <= 0:
        raise ValueError('invalid requested workflow run ID')
    if attempt is not None and (type(attempt) is not int or attempt <= 0):
        raise ValueError('invalid requested workflow attempt')
    route = 'repos/' + policy['repository']
    repository = api(route)
    workflow = api(route + '/actions/workflows/' + str(policy['workflow_id']))
    run = api(route + '/actions/runs/' + str(run_id))
    identity = dispatch_identity.authenticate_run(run, repository, workflow, policy,
                                                   require_completed=attempt is None)
    if identity.run_id != run_id:
        raise ValueError('API returned a different workflow run')
    if attempt is not None and (identity.attempt != attempt or run.get('status') != 'in_progress'):
        raise ValueError('worker must belong to the current active run attempt')
    pr = api(route + '/pulls/' + str(identity.pr))
    binding = dispatch_identity.freeze_pr(identity, pr, policy)
    return identity, pr, binding


def prepare(policy, run_id, api):
    identity, pr, binding = authenticate_inputs(policy, run_id, api)
    route = 'repos/' + policy['repository']
    # Do not request even the inventory before provenance and PR validation.
    # Require one complete response; large listings fail closed for now.
    listing = api(route + '/actions/runs/' + str(run_id) + '/artifacts?per_page=100&page=1')
    artifact = dispatch_identity.select_artifact(identity, listing)
    return {'manifest_binding': binding, 'artifact': asdict(artifact)}


def download_results(policy, run_id, reader, destination):
    prepared = prepare(policy, run_id, reader)
    selected = prepared['artifact']
    payload = reader.download(selected['id'])
    if len(payload) != selected['size_bytes']:
        raise ValueError('artifact download length differs from authenticated metadata')
    latest = prepare(policy, run_id, reader)
    if latest != prepared:
        raise ValueError('workflow, pull request or artifact changed during download')

    def validate(files):
        if 'manifest.json' not in files:
            raise ValueError('result artifact has no root manifest')
        try:
            manifest = json.loads(files['manifest.json'].decode('utf-8'), object_pairs_hook=_unique_object)
        except (UnicodeError, RecursionError, ValueError) as error:
            raise ValueError('artifact manifest is not valid JSON') from error
        problems = dispatch_identity.binding_problems(manifest, prepared['manifest_binding'])
        if problems:
            raise ValueError('; '.join(problems))

    artifact_archive.extract(payload, selected['sha256'], destination, validate=validate)
    return prepared


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate GitHub API object key')
        result[key] = value
    return result


class ApiReader:
    """Read only fixed metadata routes on GitHub, with bounded capture/time."""
    def __init__(self, repository):
        if not dispatch_identity._match(dispatch_identity.REPOSITORY, repository):
            raise ValueError('invalid GitHub repository')
        self.repository = repository
        self.route = re.compile(re.escape('repos/' + repository) +
            r'(?:/(?:pulls/[1-9][0-9]*|actions/(?:workflows/[1-9][0-9]*|'
            r'runs/[1-9][0-9]*(?:/artifacts\?per_page=100&page=1)?)))?')

    def __call__(self, route):
        if not isinstance(route, str) or not self.route.fullmatch(route):
            raise ValueError('GitHub metadata route is outside the configured repository')
        payload = self._request(route, 2 * 1024**2, 30)
        try:
            data = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
        except (UnicodeError, RecursionError, ValueError) as error:
            raise ValueError('GitHub metadata response is not a valid JSON object') from error
        if not isinstance(data, dict):
            raise ValueError('GitHub metadata response must be an object')
        return data


    def download(self, artifact_id):
        if type(artifact_id) is not int or artifact_id <= 0:
            raise ValueError('invalid selected artifact ID')
        route = 'repos/' + self.repository + '/actions/artifacts/' + str(artifact_id) + '/zip'
        return self._request(route, 25 * 1024**2, 120)

    def _request(self, route, byte_limit, timeout):
        captured = sandbox_process.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
            '-H', 'Accept: application/vnd.github+json', '-H', 'X-GitHub-Api-Version: 2026-03-10', route],
            max_stdout_bytes=byte_limit, max_stderr_bytes=8192, timeout_seconds=timeout)
        if captured.returncode or captured.problem:
            raise ValueError('GitHub API request failed for ' + route + ': ' +
                             (captured.problem or str(captured.returncode)))
        return captured.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, help='trusted dispatch policy JSON')
    parser.add_argument('--run-id', required=True, type=int)
    parser.add_argument('--out', required=True, help='new authenticated binding JSON file')
    parser.add_argument('--download-to', help='new result directory; verifies archive and manifest before writing')
    args = parser.parse_args()
    try:
        if os.path.lexists(args.out):
            raise ValueError('authenticated binding output already exists')
        with open(args.policy, 'rb') as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError('trusted dispatch policy exceeds byte limit')
        policy = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
        dispatch_identity._validate_policy(policy)
        reader = ApiReader(policy['repository'])
        value = (download_results(policy, args.run_id, reader, args.download_to) if args.download_to
                 else prepare(policy, args.run_id, reader))
        with open(args.out, 'x') as handle:
            json.dump(value, handle, indent=1)
        print(json.dumps({'type': 'result', 'status': 'downloaded-and-bound' if args.download_to else 'authenticated', 'output': args.out,
                          'repository': policy['repository'], 'run_id': args.run_id,
                          'artifact_id': value['artifact']['id']}))
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'type': 'error', 'operation': 'GitHub publication preflight',
                          'message': str(error)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
