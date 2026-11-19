"""Bounded collection of a stopped sandbox's flat final-output directory.

Call only after the entire untrusted process group has terminated. This copies
bytes, not semantic evidence: callers must still validate ELF/trace contents in
a restricted parser and must create stage results themselves outside this tree.
"""
import hashlib
import os
from pathlib import Path
import re
import stat


def validate_contract(expected, max_total_bytes):
    """Validate the shared flat filename/byte limits before any I/O or parsing."""
    if (not isinstance(expected, dict) or not 1 <= len(expected) <= 16 or
            type(max_total_bytes) is not int or max_total_bytes <= 0):
        raise ValueError('invalid final-output contract')
    for name, limit in expected.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or type(limit) is not int or limit <= 0):
            raise ValueError('invalid final-output filename or size limit')


def collect(source, destination, expected, *, max_total_bytes=64 * 1024 * 1024):
    """Copy an exact filename-to-byte-limit contract; return sizes and hashes.

    No overwrite or nested paths are supported. Open descriptors and O_NOFOLLOW
    prevent substitution of symlinks between inspection and copying. Metadata
    checks catch changes during collection; process custody remains required.
    """
    validate_contract(expected, max_total_bytes)
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.resolve() != source or not source.is_dir():
        raise ValueError('final-output source must be a real directory')
    if (destination.exists() or destination.is_symlink() or
            destination.parent.resolve() != destination.parent or not destination.parent.is_dir()
            or source == destination or source in destination.parents):
        raise ValueError('collection requires a fresh destination outside source')
    descriptors, created = [], []
    directory = None
    made_destination = False
    try:
        directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        found = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name not in expected or entry.name in found or len(found) >= len(expected):
                    raise ValueError('final outputs do not match the expected file set')
                found.add(entry.name)
        if found != set(expected):
            raise ValueError('final outputs do not match the expected file set')
        total = 0
        for name, limit in expected.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            descriptors.append((name, fd, os.fstat(fd), limit))
            info = descriptors[-1][2]
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('final output must be a regular file without hardlinks: ' + name)
            if not 0 < info.st_size <= limit:
                raise ValueError('final output is empty or exceeds its byte limit: ' + name)
            total += info.st_size
        if total > max_total_bytes:
            raise ValueError('final outputs exceed their total byte limit')
        destination.mkdir(mode=0o700)
        made_destination = True
        results = {}
        for name, fd, before, limit in descriptors:
            target = destination / name
            written, digest = 0, hashlib.sha256()
            with target.open('xb') as output:
                created.append(target)
                while True:
                    data = os.read(fd, min(1024 * 1024, limit - written + 1))
                    if not data:
                        break
                    written += len(data)
                    if written > limit or written > before.st_size:
                        raise ValueError('final output grew during collection: ' + name)
                    output.write(data)
                    digest.update(data)
            after = os.fstat(fd)
            if (written != before.st_size or
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink) !=
                    (before.st_size, before.st_mtime_ns, before.st_ctime_ns, 1)):
                raise ValueError('final output changed during collection: ' + name)
            results[name] = {'bytes': written, 'sha256': digest.hexdigest()}
        return results
    except (OSError, ValueError) as error:
        for path in created:
            path.unlink()
        if made_destination:
            destination.rmdir()
        raise ValueError('cannot collect sandbox final outputs: ' + str(error)) from error
    finally:
        for _, fd, _, _ in descriptors:
            os.close(fd)
        if directory is not None:
            os.close(directory)
