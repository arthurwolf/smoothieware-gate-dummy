"""Validate the minimal commit database used by V2's embedded version string."""
import hashlib
import os
import stat
from pathlib import Path
import re
import zlib

from input_snapshot import read_input


def write(tree, revision, commit):
    """Write a fresh minimal database from already authenticated commit bytes."""
    if (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision)
            or not isinstance(commit, bytes) or len(commit) > 2 * 1024**2):
        raise ValueError('revision metadata requires bounded authenticated commit bytes')
    obj = b'commit ' + str(len(commit)).encode('ascii') + b'\0' + commit
    digest = hashlib.sha1(obj).hexdigest() if len(revision) == 40 else hashlib.sha256(obj).hexdigest()
    if digest != revision:
        raise ValueError('firmware commit object does not match its revision')
    root = Path(tree) / '.gate-git'
    root.mkdir()  # Exclusive: reject contributor-controlled directories or links.
    (root / 'objects' / revision[:2]).mkdir(parents=True)
    (root / 'refs/heads').mkdir(parents=True)
    files = {
        'objects/' + revision[:2] + '/' + revision[2:]: zlib.compress(obj),
        'HEAD': b'ref: refs/heads/gate-snapshot\n',
        'refs/heads/gate-snapshot': (revision + '\n').encode(),
        'shallow': (revision + '\n').encode(),
        'config': ('[core]\n\trepositoryformatversion = ' + ('1' if len(revision) == 64 else '0') +
                   '\n\tbare = true\n' + ('[extensions]\n\tobjectformat = sha256\n' if len(revision) == 64 else '')).encode(),
    }
    for name, content in files.items():
        with (root / name).open('xb') as handle:
            handle.write(content)
    return str(root)


def validate(tree, revision):
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise ValueError('revision metadata requires an authenticated full revision')
    root = Path(tree) / '.gate-git'
    expected = {
        'HEAD': b'ref: refs/heads/gate-snapshot\n',
        'refs/heads/gate-snapshot': (revision + '\n').encode(),
        'shallow': (revision + '\n').encode(),
        'config': ('[core]\n\trepositoryformatversion = ' + ('1' if len(revision) == 64 else '0') +
                   '\n\tbare = true\n' + ('[extensions]\n\tobjectformat = sha256\n' if len(revision) == 64 else '')).encode(),
    }
    object_name = 'objects/' + revision[:2] + '/' + revision[2:]
    if root.is_symlink() or not root.is_dir():
        raise ValueError('missing private revision metadata')
    allowed_files = set(expected) | {object_name}
    allowed_directories = {'objects', 'objects/' + revision[:2], 'refs', 'refs/heads'}
    names, directories, pending = set(), set(), ['']
    count = 0
    while pending:
        relative = pending.pop()
        with os.scandir(root / relative) as entries:
            for entry in entries:
                count += 1
                name = (relative + '/' if relative else '') + entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if count > len(allowed_files) + len(allowed_directories):
                    raise ValueError('revision metadata has excessive entries')
                if stat.S_ISDIR(mode) and name in allowed_directories:
                    directories.add(name)
                    pending.append(name)
                elif stat.S_ISREG(mode) and name in allowed_files:
                    if entry.stat(follow_symlinks=False).st_size > 2 * 1024**2:
                        raise ValueError('revision metadata exceeds its byte limit')
                    names.add(name)
                else:
                    raise ValueError('revision metadata contains an unexpected entry')
    if names != allowed_files or directories != allowed_directories:
        raise ValueError('revision metadata inventory does not match authenticated commit')
    for name, content in expected.items():
        if read_input(root, name)[1] != content:
            raise ValueError('revision metadata does not match authenticated commit')
    compressed = read_input(root, object_name)[1]
    parser = zlib.decompressobj()
    try:
        content = parser.decompress(compressed, 2 * 1024**2 + 1)
    except zlib.error as error:
        raise ValueError('invalid revision commit object') from error
    if len(content) > 2 * 1024**2 or not parser.eof or parser.unused_data or parser.unconsumed_tail:
        raise ValueError('revision commit object exceeds its bound or has trailing bytes')
    header, separator, body = content.partition(b'\0')
    if not separator or header != b'commit ' + str(len(body)).encode():
        raise ValueError('invalid revision commit object header')
    digest = hashlib.sha1(content) if len(revision) == 40 else hashlib.sha256(content)
    if digest.hexdigest() != revision:
        raise ValueError('revision commit object does not match authenticated revision')
