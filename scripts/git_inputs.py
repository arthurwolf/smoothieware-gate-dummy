"""Bounded reads of exact Git objects and source-review patches, without hooks."""
import hashlib
import re
import sandbox_process


def read_git(repo, arguments, limit):
    result = sandbox_process.run(['git', '--no-pager', '--no-replace-objects', '-C', str(repo),
                                  *arguments], max_stdout_bytes=limit, timeout_seconds=120)
    if result.problem or result.returncode:
        raise ValueError('committed source read failed: ' + (result.problem or result.stderr.decode('utf-8', 'replace')[-2048:]))
    return result.stdout


def verify_object(kind, content, revision):
    payload = kind.encode() + b' ' + str(len(content)).encode() + b'\0' + content
    actual = hashlib.sha1(payload).hexdigest() if len(revision) == 40 else hashlib.sha256(payload).hexdigest()
    if actual != revision:
        raise ValueError('committed source object differs from authenticated identity')


def review_diff(repository, base, head, *, merge_base=False):
    """Return bounded textual patch bytes; preserve local merge-base semantics."""
    for revision in (base, head):
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
            raise ValueError('review diff requires full revisions')
        commit = read_git(repository, ['cat-file', 'commit', revision], 2 * 1024**2)
        verify_object('commit', commit, revision)
    revisions = [base + '...' + head] if merge_base else [base, head]
    return read_git(repository, ['diff', '--text', '--no-ext-diff', '--no-textconv', '--no-renames',
                                 '--no-color', '--ignore-submodules=all', '--src-prefix=a/',
                                 '--dst-prefix=b/', *revisions, '--'], 8 * 1024**2)
