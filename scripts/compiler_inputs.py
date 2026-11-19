"""Prepare copied, pinned compiler bytes without executing toolchain programs."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile
import bz2

from sandbox_inputs import copy_input

MAX_ARCHIVE_BYTES = 256 * 1024**2
MAX_EXTRACTED_BYTES = 2 * 1024**3
MAX_ENTRIES = 50000
MAX_METADATA_BYTES = 65536
MAX_TAR_BYTES = MAX_EXTRACTED_BYTES + MAX_ENTRIES * 1024
REQUIRED = ('arm-none-eabi-gcc', 'arm-none-eabi-g++', 'arm-none-eabi-size', 'arm-none-eabi-nm')


def _raw_preflight(copied, plain):
    """Bound bzip2 expansion and hidden tar headers before tarfile consumes them."""
    total = 0
    try:
        with bz2.open(copied, 'rb') as source, plain.open('xb') as output:
            while block := source.read(1024**2):
                total += len(block)
                if total > MAX_TAR_BYTES:
                    raise ValueError('compiler archive decompression exceeds byte limit')
                output.write(block)
    except (OSError, EOFError) as error:
        raise ValueError('compiler archive must be valid bzip2') from error
    count, payload_bytes, extensions = 0, 0, 0
    # PAX size overrides change the parser's next-header offset; global PAX
    # also accumulates metadata across members. Neither is needed here.
    metadata = (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
    ordinary = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE)
    with plain.open('rb') as source:
        while header := source.read(512):
            if header == b'\0' * 512:
                while block := source.read(1024**2):
                    if any(block):
                        raise ValueError('compiler archive has data after its end marker')
                return
            if len(header) != 512:
                raise ValueError('truncated compiler tar header')
            entry = tarfile.TarInfo.frombuf(header, 'utf-8', 'surrogateescape')
            count += 1
            limit = MAX_METADATA_BYTES if entry.type in metadata else MAX_ARCHIVE_BYTES
            if count > MAX_ENTRIES or not 0 <= entry.size <= limit:
                raise ValueError('compiler tar metadata or member exceeds limit')
            if entry.type not in (*ordinary, *metadata):
                raise ValueError('unsupported compiler tar record')
            if entry.type in ordinary and not entry.isfile() and entry.size:
                raise ValueError('non-file compiler tar record has payload')
            payload_bytes += entry.size
            if payload_bytes > MAX_EXTRACTED_BYTES:
                raise ValueError('compiler tar payload exceeds byte limit')
            padded = (entry.size + 511) // 512 * 512
            if source.tell() + padded > total:
                raise ValueError('truncated compiler tar payload')
            if entry.type in metadata:
                extensions += 1
                if extensions > 8:
                    raise ValueError('compiler tar metadata chain exceeds limit')
                data = source.read(padded)
                if b'GNU.sparse' in data:
                    raise ValueError('sparse compiler tar metadata is unsupported')
            else:
                extensions = 0
                source.seek(padded, os.SEEK_CUR)


def _members(archive):
    members, names, roots, total = [], set(), set(), 0
    for entry in archive:
        name = entry.name
        path = PurePosixPath(name)
        if (len(members) >= MAX_ENTRIES or not name or len(name) > 1024 or
                path.is_absolute() or '..' in path.parts or '\\' in name or
                any(ord(character) < 32 for character in name) or not path.parts):
            raise ValueError('unsafe compiler archive path or entry count')
        canonical = path.as_posix()
        if canonical in names:
            raise ValueError('duplicate compiler archive path')
        names.add(canonical)
        roots.add(path.parts[0])
        if len(roots) != 1 or not (entry.isdir() or entry.isfile() or entry.issym() or entry.islnk()):
            raise ValueError('compiler archive requires one root and ordinary files or internal links')
        if entry.size < 0 or entry.size > MAX_ARCHIVE_BYTES:
            raise ValueError('compiler archive member exceeds byte limit')
        total += entry.size
        if total > MAX_EXTRACTED_BYTES:
            raise ValueError('compiler archive exceeds expanded byte limit')
        if entry.issym() or entry.islnk():
            link = entry.linkname
            target = posixpath.normpath(posixpath.join(posixpath.dirname(canonical), link)
                                       if entry.issym() else link)
            if (not link or '\\' in link or PurePosixPath(link).is_absolute() or
                    not PurePosixPath(target).parts or PurePosixPath(target).parts[0] != path.parts[0]):
                raise ValueError('compiler archive link escapes toolchain root')
        members.append(entry)
    if not members:
        raise ValueError('compiler archive is empty')
    return members, next(iter(roots))


def prepare(source, sha256, destination):
    """Return the extracted toolchain only after copied-byte and path validation.

    An unsuccessful preparation leaves its exclusive destination for recovery;
    it cannot be reused or accepted as a ready compiler.
    """
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('invalid trusted compiler archive digest')
    destination = Path(destination).absolute()
    if destination.parent.resolve() != destination.parent or not destination.parent.is_dir():
        raise ValueError('compiler preparation requires a real parent directory')
    destination.mkdir()
    copied = destination / 'compiler.archive'
    copy_input(source, copied, MAX_ARCHIVE_BYTES, 0o400, minimum=1)
    digest = hashlib.sha256()
    with copied.open('rb') as handle:
        for block in iter(lambda: handle.read(1024**2), b''):
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise ValueError('copied compiler archive does not match trusted digest')
    extracted = destination / 'extracted'
    plain = destination / 'compiler.tar'
    try:
        _raw_preflight(copied, plain)
        with tarfile.open(plain, mode='r:') as archive:
            members, root = _members(archive)
            extracted.mkdir()
            archive.extractall(extracted, members=members, filter='data')
    except (tarfile.TarError, OSError) as error:
        raise ValueError('compiler archive extraction failed: ' + str(error)) from error
    compiler = extracted / root
    for name in REQUIRED:
        path = compiler / 'bin' / name
        if not path.is_file() or not os.access(path, os.X_OK) or not path.resolve().is_relative_to(compiler):
            raise ValueError('compiler archive lacks required executable: ' + name)
    plain.unlink()  # This owned intermediate has no remaining consumer.
    return compiler
