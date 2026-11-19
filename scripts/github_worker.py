"""Freeze authenticated worker inputs and reject stale results after execution.

Policy belongs to trusted default-branch configuration. The API reader must be
GitHub-authenticated. These functions neither execute source nor publish data.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_identity
import github_preflight
from request_json import read_object
from github_preflight import authenticate_inputs
from profiles import EXPECTED_STAGES, validate_manifest


def prepare(policy, run_id, attempt, api):
    if type(attempt) is not int or attempt <= 0:
        raise ValueError('worker requires an explicit positive attempt')
    identity, pr, binding = authenticate_inputs(policy, run_id, api, attempt=attempt)
    return {'binding': binding, 'base_repository': policy['repository'],
            'head_repository': pr['head']['repo']['full_name']}


def confirm(policy, prepared, api):
    if not isinstance(prepared, dict) or set(prepared) != {'binding', 'base_repository', 'head_repository'}:
        raise ValueError('worker preparation is malformed')
    binding = prepared['binding']
    dispatch_identity.validate_binding_policy(binding, policy)
    workflow = binding['workflow']
    current = prepare(policy, workflow.get('run_id'), workflow.get('attempt'), api)
    if current != prepared:
        raise ValueError('worker inputs changed while checks were running')
    return current


def bind_manifest(policy, prepared, manifest, api):
    """Bind measured input metadata without silently replacing wrong identity."""
    confirm(policy, prepared, api)
    problems = validate_manifest(manifest, EXPECTED_STAGES)
    binding = prepared['binding']
    if problems:
        raise ValueError('; '.join(problems))
    for key in ('profile', 'base', 'head', 'controller_sha256'):
        if manifest.get(key) != binding[key]:
            raise ValueError('local manifest ' + key + ' differs from authenticated execution')
    if any(key in manifest for key in ('workflow', 'execution_pins')):
        problems = dispatch_identity.binding_problems(manifest, binding)
        if problems:
            raise ValueError('; '.join(problems))
    bound = copy.deepcopy(manifest)
    bound.update(copy.deepcopy(binding))
    problems = dispatch_identity.binding_problems(bound, binding)
    if problems:
        raise ValueError('; '.join(problems))
    return bound


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    creation = commands.add_parser('prepare', help='authenticate current run before source selection')
    creation.add_argument('--policy', required=True)
    creation.add_argument('--run-id', type=int, required=True)
    creation.add_argument('--attempt', type=int, required=True)
    creation.add_argument('--out', required=True)
    confirmation = commands.add_parser('confirm', help='reject changed execution identity')
    confirmation.add_argument('--policy', required=True)
    confirmation.add_argument('--execution', required=True)
    manifest_command = commands.add_parser('manifest', help='bind a complete input manifest to authenticated execution')
    for flag in ('policy', 'execution', 'manifest', 'out'):
        manifest_command.add_argument('--' + flag, required=True)
    args = parser.parse_args(argv)
    try:
        policy = read_object(args.policy, 65536)
        dispatch_identity._validate_policy(policy)
        if args.operation in ('prepare', 'manifest'):
            output = Path(args.out).absolute()
            if (output.exists() or output.is_symlink() or not output.parent.is_dir()
                    or output.parent.resolve() != output.parent):
                raise ValueError('worker preparation requires fresh output with a real parent')
            reader = github_preflight.ApiReader(policy['repository'])
            if args.operation == 'prepare':
                payload = prepare(policy, args.run_id, args.attempt, reader)
            else:
                prepared = read_object(args.execution, 65536)
                manifest = read_object(args.manifest, 1048576)
                payload = bind_manifest(policy, prepared, manifest, reader)
            with output.open('x') as handle:
                json.dump(payload, handle)
            print(json.dumps({'type': 'result', 'operation': args.operation, 'output': str(output)}))
        else:
            prepared = read_object(args.execution, 65536)
            confirm(policy, prepared, github_preflight.ApiReader(policy['repository']))
            print(json.dumps({'type': 'result', 'operation': 'confirm', 'execution': args.execution}))
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'type': 'error', 'operation': args.operation, 'message': str(error)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
