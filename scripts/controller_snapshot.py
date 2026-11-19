"""Retained controller source inventory; independent of firmware and simulator inputs."""
import hashlib
import os
from pathlib import Path
import re
import sys

from input_snapshot import identity_entry, read_input, snapshot_files


def inventory(source):
    source = Path(source)
    names = ['bin/smoothie-gate']
    if source.is_symlink():
        raise ValueError('controller source cannot be a symlink')
    for directory in ('scripts', 'prompts', 'cases'):
        base = source / directory
        if base.is_symlink() or not base.is_dir():
            raise ValueError(f'missing or linked controller directory: {directory}')
        found = []
        for root, dirs, files in os.walk(base, followlinks=False):
            # A cache is never a consumed source input, even when it is a link.
            dirs[:] = [name for name in dirs if name != '__pycache__']
            if any((Path(root) / name).is_symlink() for name in dirs):
                raise ValueError('controller directories cannot be symlinks')
            for name in files:
                path = Path(root) / name
                if directory == 'scripts' and path.suffix != '.py':
                    continue
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f'unsupported controller input: {path}')
                found.append(path.relative_to(source).as_posix())
        if not found:
            raise ValueError(f'empty controller directory: {directory}')
        names.extend(found)
    if 'scripts/gate.py' not in names:
        raise ValueError('controller entry point is missing')
    return sorted(names)


def freeze(source, destination):
    """Create an exclusive snapshot, returning the identity of its copied bytes."""
    return snapshot_files(source, destination, inventory(source))


def verify(source, expected):
    """Reject a missing, changed or extra execution input before restarting."""
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('invalid controller digest')
    digest = hashlib.sha256()
    for name in inventory(source):
        mode, content = read_input(source, name)
        digest.update(identity_entry(name, mode, content))
    if digest.hexdigest() != expected:
        raise ValueError('controller snapshot does not match its digest')


def enter(source, args):
    """Restart from copied code, or validate the restart before any stage runs."""
    source = Path(source).absolute()
    for name in ('repo', 'sim_dir', 'out', 'cases'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, os.path.abspath(value))
    out = Path(args.out)
    frozen = out / 'work/controller-input'
    if args.controller_sha256 is not None:
        if source != frozen or out.is_symlink() or (out / 'work').is_symlink():
            raise ValueError('controller restart must execute the run-owned snapshot')
        verify(frozen, args.controller_sha256)
        if set(path.name for path in out.iterdir()) != {'work'} or set(
                path.name for path in (out / 'work').iterdir()) != {'controller-input'}:
            raise ValueError('controller restart requires an unused run directory')
        return args.controller_sha256
    out.mkdir()  # Exclusive ownership; never reuse another run or a dangling link.
    (out / 'work').mkdir()
    digest = freeze(source, frozen)
    command = [sys.executable, '-B', '-I', str(frozen / 'scripts/gate.py'), 'run']
    for flag, value in (('--repo', args.repo), ('--sim-dir', args.sim_dir), ('--out', args.out),
                        ('--profile', args.profile), ('--toolchain', args.toolchain),
                        ('--base', args.base), ('--head', args.head), ('--cases', args.cases)):
        if value is not None:
            command.extend((flag, value))
    if args.no_model:
        command.append('--no-model')
    command.extend(('--controller-sha256', digest))
    os.execv(sys.executable, command)
    raise RuntimeError('controller restart unexpectedly returned')
