"""Validate immutable downloaded bytes before creating a fresh result directory."""
import hashlib
import io
from pathlib import Path
import re
import stat
import struct
import zipfile
import zlib

from artifact_safety import allowed_result_name

MAX_ARCHIVE = 25 * 1024**2
MAX_FILE = 5 * 1024**2



def _check_directory(payload):
    # Bound actual metadata records before ZipFile allocates ZipInfo objects.
    # The gate's <=25 MiB/256-file contract never requires ZIP64 or spanning.
    end = payload.rfind(b'PK\x05\x06', max(0, len(payload) - 65557))
    if end < 0 or end + 22 > len(payload):
        raise ValueError('artifact ZIP has no bounded end record')
    if end >= 20 and payload[end-20:end-16] == b'PK\x06\x07':
        raise ValueError('ZIP64 artifact directory is unsupported')
    _, disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2LH', payload, end)
    if (disk or start_disk or disk_count != count or not 1 <= count <= 1024 or
            end + 22 + comment != len(payload) or offset + size != end):
        raise ValueError('artifact ZIP directory metadata is invalid or unsupported')
    position, actual = offset, 0
    while position < end:
        if position + 46 > end or payload[position:position+4] != b'PK\x01\x02':
            raise ValueError('invalid artifact central-directory record')
        name, extra, note = struct.unpack_from('<HHH', payload, position + 28)
        actual += 1
        if actual > 1024 or not 0 < name <= 2303:
            raise ValueError('artifact central-directory count or name exceeds limit')
        position += 46 + name + extra + note
        if position > end:
            raise ValueError('truncated artifact central-directory record')
    if position != end or actual != count:
        raise ValueError('artifact central-directory count does not match')


def _read_files(payload):
    _check_directory(payload)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= 1024:
                raise ValueError('artifact archive has an invalid entry count')
            seen, files, total = set(), [], 0
            for entry in entries:
                name = entry.filename[:-1] if entry.is_dir() else entry.filename
                parts = name.split('/')
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (entry.orig_filename != entry.filename or
                        len(parts) > (8 if entry.is_dir() else 9) or
                        any(len(part) > 255 or part in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in parts) or
                        name in seen or entry.flag_bits & 1 or
                        entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                        mode not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG)):
                    raise ValueError('artifact archive has an unsafe or duplicate entry')
                seen.add(name)
                if entry.is_dir():
                    if entry.file_size:
                        raise ValueError('artifact directory has content')
                    continue
                if not allowed_result_name(parts[-1]) or not 0 <= entry.file_size <= MAX_FILE:
                    raise ValueError('artifact file name or length is invalid')
                total += entry.file_size
                if len(files) >= 256 or total > MAX_ARCHIVE:
                    raise ValueError('artifact expanded size or file count exceeds limit')
                with archive.open(entry) as source:
                    content = source.read(MAX_FILE + 1)
                if len(content) != entry.file_size:
                    raise ValueError('artifact file length does not match metadata')
                files.append((parts, content))
            if not files:
                raise ValueError('artifact archive contains no result files')
            file_names = {'/'.join(parts) for parts, _ in files}
            for parts, _ in files:
                if any('/'.join(parts[:index]) in file_names for index in range(1, len(parts))):
                    raise ValueError('artifact file conflicts with a parent directory')
            return files
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as error:
        raise ValueError('invalid artifact ZIP archive') from error


def extract(payload, sha256, destination, *, validate=None):
    if (not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_ARCHIVE or
            not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', sha256) or
            hashlib.sha256(payload).hexdigest() != sha256):
        raise ValueError('artifact archive length or SHA-256 digest does not match')
    files = _read_files(payload)
    if validate is not None:
        validate({'/'.join(parts): content for parts, content in files})
    destination = Path(destination).absolute()
    if destination.parent.resolve() != destination.parent:
        raise ValueError('artifact destination parent must not contain symlinks')
    destination.mkdir(mode=0o700)  # Exclusive creation protects existing user data.
    for parts, content in files:
        target = destination.joinpath(*parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(content)
    return len(files)
