"""Reproducibility adjustments to task-owned exported V2 build inputs."""
from pathlib import Path
from contextlib import ExitStack
import os
import re
import stat

PATH_POLICY = 'canonical-file-prefix-v1'
MAX_RECIPE_BYTES = 256 * 1024


def _identity(info):
    # Reads legitimately update atime. Identity and content metadata must stay
    # stable, including subsecond timestamps and link count.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(stack, parent, name):
    descriptor = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stack.callback(os.close, descriptor)
    return descriptor


def _recipe(stack, directory):
    descriptor = os.open('Rakefile', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    stack.callback(os.close, descriptor)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_RECIPE_BYTES:
        raise ValueError('V2 recipe must be a bounded regular file without hardlinks')
    data = bytearray()
    while len(data) <= MAX_RECIPE_BYTES:
        chunk = os.read(descriptor, min(65536, MAX_RECIPE_BYTES + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    if len(data) != info.st_size or _identity(os.fstat(descriptor)) != _identity(info):
        raise ValueError('V2 recipe changed while being read')
    return descriptor, info, bytes(data).decode('utf-8')


def normalize_paths(tree):
    """Normalize debug info and macro source paths equally for both revisions.

    Validate both upstream recipes before writing either. This intentionally
    supports the inspected FFLAGS boundary, rather than guessing at new layouts.
    """
    with ExitStack() as stack:
        # Walk every directory by descriptor so no contributor-controlled parent
        # or recipe symlink can redirect a host write outside the exported tree.
        root = _directory(stack, None, '/')
        for part in Path(tree).absolute().parts[1:]:
            if part == '..':
                raise ValueError('V2 source path must not contain parent traversal')
            root = _directory(stack, root, part)
        pending = []
        for folder in ('Firmware', 'flashloader'):
            directory = _directory(stack, root, folder)
            descriptor, info, text = _recipe(stack, directory)
            addition = ('# smoothie-gate ' + PATH_POLICY + '\n' +
                        'FFLAGS += " -ffile-prefix-map=#{File.realpath(Dir.pwd)}=/smoothie/' + folder + ' "\n')
            if addition in text:
                continue
            matches = list(re.finditer(r'^FFLAGS = [^\n]*\n', text, re.MULTILINE))
            if len(matches) != 1 or PATH_POLICY in text:
                raise ValueError('unsupported V2 compiler-flag layout: ' + folder)
            end = matches[0].end()
            pending.append((descriptor, info, (text[:end] + addition + text[end:]).encode('utf-8')))
        if any(_identity(os.fstat(descriptor)) != _identity(info) for descriptor, info, data in pending):
            raise ValueError('V2 recipe changed before normalization')
        for descriptor, info, data in pending:
            os.lseek(descriptor, 0, os.SEEK_SET)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if not written:
                    raise OSError('V2 recipe write made no progress')
                view = view[written:]
            os.ftruncate(descriptor, len(data))
