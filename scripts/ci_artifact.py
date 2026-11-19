"""Assemble bounded report data; never include firmware, logs or executables.

The caller supplies a freshly authenticated binding and PR object. Source results
must remain exclusively owned and stable through validation and copying.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_preflight
import github_worker
from request_json import read_object

from artifact_archive import MAX_ARCHIVE, MAX_FILE
import dispatch_identity
from gate_result import validate_result
from github_preflight import _unique_object
from profiles import EXPECTED_STAGES, validate_manifest


def _read(root, name):
    source = root / name
    if source.resolve() != source:
        raise ValueError('artifact input path contains a link')
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE:
                raise ValueError('artifact input is not a bounded regular file')
            payload = handle.read(MAX_FILE + 1)
    except OSError as error:
        raise ValueError('required artifact input is unavailable: ' + name) from error
    if len(payload) > MAX_FILE:
        raise ValueError('artifact input exceeds byte limit')
    try:
        data = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))
    except (UnicodeError, RecursionError, ValueError) as error:
        raise ValueError('artifact input is invalid JSON: ' + name) from error
    return payload, data


def assemble(results, binding, pr, destination):
    """Validate complete stage data before creating an exclusive artifact root."""
    results = Path(results).absolute()
    destination = Path(destination).absolute()
    if (results.resolve() != results or not results.is_dir() or destination.exists()
            or destination.is_symlink() or not destination.parent.is_dir()
            or destination.parent.resolve() != destination.parent
            or destination == results or destination in results.parents or results in destination.parents):
        raise ValueError('artifact requires real disjoint inputs and a fresh destination')
    files = {}
    files['manifest.json'], manifest = _read(results, 'manifest.json')
    problems = validate_manifest(manifest, EXPECTED_STAGES) + dispatch_identity.binding_problems(manifest, binding)
    if problems:
        raise ValueError('; '.join(problems))
    workflow = binding['workflow']
    if (not isinstance(pr, dict) or type(pr.get('number')) is not int or pr['number'] != workflow['pr']
            or not isinstance(pr.get('base'), dict) or not isinstance(pr.get('head'), dict)
            or pr['base'].get('sha') != binding['base'] or pr['head'].get('sha') != binding['head']):
        raise ValueError('artifact PR data differs from authenticated binding')
    files['pr.json'] = json.dumps(pr, allow_nan=False).encode('utf-8')
    for stage in EXPECTED_STAGES:
        prefix = 'sim/' if stage == 'sim' else 'review/' if stage == 'ai-review' else ''
        name = prefix + stage + '.result.json'
        files[name], record = _read(results, name)
        problems = validate_result(record)
        if problems or record.get('stage') != stage:
            raise ValueError('artifact stage is invalid: ' + stage)
    traces = results / 'sim/trace-samples.json'
    if traces.exists() or traces.is_symlink():
        files['sim/trace-samples.json'], _ = _read(results, 'sim/trace-samples.json')
    if any(len(value) > MAX_FILE for value in files.values()) or sum(map(len, files.values())) > MAX_ARCHIVE:
        raise ValueError('artifact exceeds report data limits')
    destination.mkdir()
    for name, payload in files.items():
        target = destination / name
        target.parent.mkdir(exist_ok=True)
        with target.open('xb') as handle:
            handle.write(payload)
    return sorted(files)


def prepare(policy, execution, api, results, destination):
    """Assemble current data only; this function does not publish or upload it.

    A failed final confirmation leaves a fresh task-owned directory, but never
    returns it as ready. Workflow upload must require successful preparation.
    The reporter independently reauthenticates immediately before publication.
    """
    github_worker.confirm(policy, execution, api)
    number = execution['binding']['workflow']['pr']
    pr = api('repos/' + policy['repository'] + '/pulls/' + str(number))
    dispatch_identity.validate_pr(pr, number, policy)
    files = assemble(results, execution['binding'], pr, destination)
    github_worker.confirm(policy, execution, api)
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('policy', 'execution', 'results', 'out'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args(argv)
    try:
        policy = read_object(args.policy, 65536)
        execution = read_object(args.execution, 65536)
        files = prepare(policy, execution, github_preflight.ApiReader(policy['repository']), args.results, args.out)
        print(json.dumps({'type': 'artifact', 'output': args.out, 'files': files}))
        return 0
    except (OSError, ValueError, RecursionError, KeyError) as error:
        print(json.dumps({'type': 'error', 'operation': 'artifact preparation',
                          'output': args.out, 'message': str(error)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
