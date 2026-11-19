"""Run a prepared CI request from its verified retained controller snapshot."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller_snapshot
import dispatch_identity
import github_preflight
from request_json import read_object

ROOT = Path(__file__).absolute().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('policy', 'execution', 'manifest', 'inputs', 'archive', 'base-tree', 'pr-tree', 'diff', 'out'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--controller-sha256', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        for name in ('policy', 'execution', 'manifest', 'inputs', 'archive', 'base_tree', 'pr_tree', 'diff', 'out'):
            setattr(args, name, os.path.abspath(getattr(args, name)))
        policy = read_object(args.policy, 65536)
        dispatch_identity._validate_policy(policy)
        frozen = Path(args.inputs) / 'controller-input'
        if frozen.resolve() != frozen:
            raise ValueError('CI controller snapshot requires real input ancestors')
        digest = policy['pins']['controller_sha256']
        # Only Python sources may be importable beside retained entrypoints.
        # -B prevents writes, but Python can still read existing bytecode.
        for directory, directories, files in os.walk(frozen / 'scripts', followlinks=False):
            if '__pycache__' in directories or any(
                    (Path(directory) / name).is_symlink() for name in directories):
                raise ValueError('CI controller contains an untracked import directory')
            if any(not name.endswith('.py') or not (Path(directory) / name).is_file()
                   or (Path(directory) / name).is_symlink() for name in files):
                raise ValueError('CI controller contains a non-source import file')
        controller_snapshot.verify(frozen, digest)
        if args.controller_sha256 is None:
            command = [sys.executable, '-B', '-I', str(frozen / 'scripts/ci_worker.py')]
            for flag in ('policy', 'execution', 'manifest', 'inputs', 'archive', 'base-tree', 'pr-tree', 'diff', 'out'):
                command.extend(('--' + flag, getattr(args, flag.replace('-', '_'))))
            command.extend(('--controller-sha256', digest))
            os.execv(sys.executable, command)
            raise RuntimeError('CI controller restart unexpectedly returned')
        if ROOT != frozen or args.controller_sha256 != digest:
            raise ValueError('CI worker must execute its verified retained controller')
        # Import orchestration only after verifying the process execution root.
        import ci_run
        execution = read_object(args.execution, 65536)
        manifest = read_object(args.manifest, 1048576)
        request = ci_run.RunRequest(policy, execution, manifest, Path(args.inputs), Path(args.archive),
                                    {'base': Path(args.base_tree), 'pr': Path(args.pr_tree)},
                                    Path(args.diff), Path(args.out))
        reader = github_preflight.ApiReader(policy['repository'])
        result = ci_run.run(request, reader)
        artifact = None
        if result.publication_permitted:
            import ci_artifact
            artifact = Path(args.out) / 'artifact'
            ci_artifact.prepare(policy, execution, reader, Path(args.out) / 'checks/results', artifact)
        print(json.dumps({'type': 'result', 'code': result.code, 'output': args.out,
                          'publication_permitted': result.publication_permitted,
                          'artifact': str(artifact) if artifact is not None else None, 'error': result.error}))
        return result.code
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'type': 'error', 'operation': 'CI worker', 'message': str(error),
                          'output': args.out, 'publication_permitted': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
