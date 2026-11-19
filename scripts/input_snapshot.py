"""Copy named inputs and identify the exact copied bytes and executable modes."""
import hashlib
import json
import os
import stat
from pathlib import Path


def identity_entry(name, mode, content):
    entry = json.dumps([name, mode, hashlib.sha256(content).hexdigest()],
                       ensure_ascii=True, separators=(',', ':'))
    return entry.encode('ascii') + b'\n'


def read_input(source, name):
    if os.path.isabs(name) or any(part in ('', '.', '..') for part in name.split('/')):
        raise ValueError('invalid snapshot path')
    path = Path(source)
    if path.is_symlink():
        raise ValueError('snapshot source cannot be a symlink')
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError(f'snapshot inputs cannot be symlinks: {name}')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f'snapshot input must be a regular file: {name}')
        return metadata.st_mode & 0o111, handle.read()


def snapshot_files(source, destination, names):
    digest = hashlib.sha256()
    os.makedirs(destination)
    for name in names:
        mode, content = read_input(source, name)
        target = Path(destination) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(content)
        target.chmod(0o644 | mode)
        digest.update(identity_entry(name, mode, content))
    return digest.hexdigest()
