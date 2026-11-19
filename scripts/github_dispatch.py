"""Trusted one-shot workflow dispatch with an uncertainty record before POST."""
import json
import os
from pathlib import Path
import argparse
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_identity
import github_preflight
import sandbox_process


def request(policy, repository, workflow, pr):
    dispatch_identity.validate_target(repository, workflow, policy)
    number = pr.get('number') if isinstance(pr, dict) else None
    dispatch_identity.validate_pr(pr, number, policy)
    return {'route': 'repos/' + policy['repository'] + '/actions/workflows/' +
            str(policy['workflow_id']) + '/dispatches',
            'body': {'ref': policy['default_branch'], 'inputs': {'pr_number': str(number)}}}


def receipt(policy, pr_number, response):
    dispatch_identity._validate_policy(policy)
    if not dispatch_identity._positive(pr_number) or pr_number > 9999999999:
        raise ValueError('invalid dispatch PR number')
    if not isinstance(response, dict) or not dispatch_identity._positive(response.get('workflow_run_id')):
        raise ValueError('dispatch outcome uncertain: response has no exact run ID; do not retry')
    run_id = response['workflow_run_id']
    api_url = 'https://api.github.com/repos/' + policy['repository'] + '/actions/runs/' + str(run_id)
    html_url = 'https://github.com/' + policy['repository'] + '/actions/runs/' + str(run_id)
    if response.get('run_url') != api_url or response.get('html_url') != html_url:
        raise ValueError('dispatch outcome uncertain: response URLs do not match run ID; do not retry')
    return {key: policy[key] for key in ('repository', 'repository_id', 'workflow_id',
            'workflow_path', 'default_branch', 'profile')} | {
                'pr': pr_number, 'run_id': run_id, 'run_url': api_url, 'html_url': html_url}


def dispatch_once(policy, repository, workflow, pr, output):
    """Persist intent before one POST; occupied output prevents duplicate dispatch.

    Metadata arguments must be authenticated API responses. A crash or invalid
    response leaves outcome-uncertain, never permission to retry automatically.
    The caller retains this file until the remote outcome has been reconciled.
    """
    prepared = request(policy, repository, workflow, pr)
    result = {'status': 'outcome-uncertain', 'repository': policy['repository'],
              'repository_id': policy['repository_id'], 'workflow_id': policy['workflow_id'],
              'default_branch': policy['default_branch'], 'pr': pr['number'],
              'message': 'Dispatch may have started; reconcile the remote outcome before any retry.'}

    def persist(handle, value):
        json.dump(value, handle, indent=1)
        handle.flush()
        os.fsync(handle.fileno())

    output = Path(output).absolute()
    def flush_directory():
        descriptor = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    with open(output, 'x') as handle:
        persist(handle, result)
        flush_directory()
        command = ['gh', 'api', '--hostname', 'github.com', '--method', 'POST',
                   '-H', 'Accept: application/vnd.github+json',
                   '-H', 'X-GitHub-Api-Version: 2026-03-10', prepared['route'],
                   '-f', 'ref=' + prepared['body']['ref'],
                   '-f', 'inputs[pr_number]=' + prepared['body']['inputs']['pr_number']]
        try:
            captured = sandbox_process.run(command, max_stdout_bytes=65536,
                                           max_stderr_bytes=8192, timeout_seconds=30)
            if captured.returncode or captured.problem:
                return result
            response = json.loads(captured.stdout.decode('utf-8'),
                                  object_pairs_hook=github_preflight._unique_object)
            accepted = receipt(policy, pr['number'], response)
        except (OSError, ValueError, RecursionError):
            return result
        result = {'status': 'dispatched', 'receipt': accepted}
        # Keep the complete durable uncertainty record until replacement is
        # ready. A failed final write retains it and any owned recovery file.
        completed = output.with_name(output.name + '.completed')
        with open(completed, 'x') as final:
            persist(final, result)
        os.replace(completed, output)
        flush_directory()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, help='trusted dispatch policy JSON')
    parser.add_argument('--pr-number', required=True, type=int)
    parser.add_argument('--out', required=True, help='new durable dispatch receipt file')
    args = parser.parse_args(argv)
    try:
        if os.path.lexists(args.out):
            raise ValueError('dispatch receipt already exists; reconcile it before any retry')
        if not dispatch_identity._positive(args.pr_number) or args.pr_number > 9999999999:
            raise ValueError('invalid dispatch PR number')
        with open(args.policy, 'rb') as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError('trusted dispatch policy exceeds byte limit')
        policy = json.loads(raw.decode('utf-8'), object_pairs_hook=github_preflight._unique_object)
        dispatch_identity._validate_policy(policy)
        reader = github_preflight.ApiReader(policy['repository'])
        route = 'repos/' + policy['repository']
        print(json.dumps({'type': 'progress', 'operation': 'validate trusted workflow dispatch',
                          'repository': policy['repository'], 'pr': args.pr_number}), flush=True)
        repository = reader(route)
        workflow = reader(route + '/actions/workflows/' + str(policy['workflow_id']))
        dispatch_identity.validate_target(repository, workflow, policy)
        pr = reader(route + '/pulls/' + str(args.pr_number))
        dispatch_identity.validate_pr(pr, args.pr_number, policy)
        result = dispatch_once(policy, repository, workflow, pr, args.out)
        event = {'type': 'result', 'status': result['status'], 'output': args.out,
                 'repository': policy['repository'], 'pr': args.pr_number}
        if result['status'] == 'dispatched':
            event['run_id'] = result['receipt']['run_id']
        else:
            event['message'] = result['message']
        print(json.dumps(event))
        return 0 if result['status'] == 'dispatched' else 2
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'type': 'error', 'operation': 'trusted workflow dispatch',
                          'output': args.out, 'message': str(error),
                          'recovery': 'If a receipt exists, reconcile its remote outcome before retrying.'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
