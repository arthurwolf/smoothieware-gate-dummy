"""Export authenticated committed firmware bytes without running checkout hooks.

Repositories are trusted host-owned object stores. No contributor working-tree
file, Git attribute filter, submodule command or archive extractor is executed.
Failed exports may retain a fresh, task-owned partial destination for diagnosis.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import github_preflight
import github_worker
from profiles import select_profile
from request_json import read_object

import revision_metadata
from git_inputs import read_git, verify_object, review_diff

MAX_FILE = 32 * 1024**2
MAX_TOTAL = 512 * 1024**2


def export(repository, revision, destination, *, generation):
    """Export one exact commit independently, preserving only internal links."""
    if generation not in (1, 2) or type(generation) is not int or not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise ValueError('source export requires a profile generation and full authenticated revision')
    repo = Path(repository).absolute(); output = Path(destination).absolute()
    if (repo.resolve() != repo or not repo.is_dir() or output.exists() or output.is_symlink()
            or not output.parent.is_dir() or output.parent.resolve() != output.parent
            or output == repo or output in repo.parents or repo in output.parents):
        raise ValueError('source export requires real disjoint inputs and a fresh destination')
    commit = read_git(repo, ['cat-file', 'commit', revision], 2 * 1024**2)
    verify_object('commit', commit, revision)
    listing = read_git(repo, ['ls-tree', '-r', '-z', '-l', '--full-tree', revision], 16 * 1024**2)
    entries = []; names = set(); total = 0
    records = listing.split(b'\0')
    if records[-1] != b'' or not 1 < len(records) <= 50001:
        raise ValueError('committed source inventory exceeds count limit or is empty')
    for record in records[:-1]:
        try:
            header, raw_name = record.split(b'\t', 1)
            mode, kind, object_id, length = header.split()
            name = raw_name.decode('utf-8')
            parts = name.split('/')
            size = int(length)
        except (ValueError, UnicodeError) as error:
            raise ValueError('invalid committed source inventory') from error
        if (kind != b'blob' or mode not in (b'100644', b'100755', b'120000')
                or not re.fullmatch(rb'[0-9a-f]{' + str(len(revision)).encode() + rb'}', object_id)
                or len(raw_name) > 4096 or len(parts) > 32 or name in names
                or any(part in ('', '.', '..', '.git', '.gate-git') or '\\' in part or
                       any(ord(character) < 32 for character in part) for part in parts)
                or not 0 <= size <= (4096 if mode == b'120000' else MAX_FILE)):
            raise ValueError('unsupported committed source path, mode or size')
        total += size
        if total > MAX_TOTAL:
            raise ValueError('committed source exceeds total byte limit')
        names.add(name); entries.append((name, mode, object_id.decode(), size))
    for name in names:
        if any('/'.join(name.split('/')[:index]) in names for index in range(1, len(name.split('/')))):
            raise ValueError('committed source file conflicts with parent directory')
    output.mkdir(); links = []
    for name, mode, object_id, size in entries:
        content = read_git(repo, ['cat-file', 'blob', object_id], max(1, size))
        if len(content) != size:
            raise ValueError('committed blob length changed')
        verify_object('blob', content, object_id)
        target = output / name; target.parent.mkdir(parents=True, exist_ok=True)
        if mode == b'120000':
            try:
                link = content.decode('utf-8')
            except UnicodeError as error:
                raise ValueError('committed link is not UTF-8') from error
            if not link or os.path.isabs(link) or '\0' in link:
                raise ValueError('committed link must be internal and relative')
            links.append((target, link))
        else:
            with target.open('xb') as handle: handle.write(content)
            target.chmod(0o755 if mode == b'100755' else 0o644)
    for target, link in links: target.symlink_to(link)
    for target, _ in links:
        try:
            resolved = target.resolve()
        except (OSError, RuntimeError) as error:
            raise ValueError('committed link cannot be resolved') from error
        if output not in resolved.parents:
            raise ValueError('committed link escapes the exported firmware')
    if generation == 2:
        revision_metadata.write(output, revision, commit)
        revision_metadata.validate(output, revision)
    return output


def prepare(policy, execution, api, base_repository, head_repository, output):
    """Authenticate before exporting, derive the exact diff, then confirm freshness.

    The head object store must contain both authenticated commits. Workflow
    checkout/fetch is a separate trusted transport step, never contributor code.
    """
    github_worker.confirm(policy, execution, api)
    root = Path(output).absolute()
    repositories = [Path(value).absolute() for value in (base_repository, head_repository)]
    if (root.exists() or root.is_symlink() or not root.parent.is_dir()
            or root.parent.resolve() != root.parent):
        raise ValueError('source preparation requires fresh output with a real parent')
    for repo in repositories:
        if (repo.resolve() != repo or not repo.is_dir() or root == repo
                or root in repo.parents or repo in root.parents):
            raise ValueError('source preparation overlaps or aliases a repository')
    binding = execution['binding']
    profile = select_profile(policy['profile'])
    patch = review_diff(repositories[1], binding['base'], binding['head'])
    root.mkdir()
    base = export(repositories[0], binding['base'], root/'base', generation=profile.generation)
    head = export(repositories[1], binding['head'], root/'pr', generation=profile.generation)
    with (root/'diff.patch').open('xb') as handle:
        handle.write(patch)
    github_worker.confirm(policy, execution, api)
    return {'base': base, 'pr': head, 'diff': root/'diff.patch'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('policy', 'execution', 'base-repository', 'head-repository', 'out'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args(argv)
    try:
        policy = read_object(args.policy, 65536)
        execution = read_object(args.execution, 65536)
        # confirm performs full policy validation before API access or writes.
        paths = prepare(policy, execution, github_preflight.ApiReader(policy['repository']),
                        args.base_repository, args.head_repository, args.out)
        print(json.dumps({'type': 'result', 'operation': 'source preparation',
                          **{name: str(value) for name, value in paths.items()}}))
        return 0
    except (OSError, ValueError, RecursionError, KeyError) as error:
        print(json.dumps({'type': 'error', 'operation': 'source preparation',
                          'output': args.out, 'message': str(error)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
