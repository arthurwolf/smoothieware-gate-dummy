"""Copy bounded regular input bytes without following the final path component."""
import os
from pathlib import Path
import stat


def copy_input(source, destination, maximum, mode, minimum=0):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not minimum <= before.st_size <= maximum:
            raise ValueError('sandbox input must be a bounded regular file')
        destination = Path(destination)
        with destination.open('xb') as output:
            remaining = before.st_size
            while remaining:
                data = os.read(fd, min(remaining, 1024**2))
                if not data:
                    raise ValueError('sandbox input changed while copying')
                output.write(data)
                remaining -= len(data)
        after = os.fstat(fd)
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('sandbox input changed while copying')
        destination.chmod(mode)
    finally:
        os.close(fd)
