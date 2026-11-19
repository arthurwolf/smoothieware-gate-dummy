"""Construct the experimental Linux build boundary; orchestration stays outside.

This module does not execute commands or authenticate returned artifacts. Callers
must enforce resource limits and validate outputs before using them.
"""
from pathlib import Path


def controller_command(source, output, controller, command, *, toolchain=None,
                       workspace_bytes=2 * 1024 ** 3):
    """Give trusted orchestration a limited tmpfs for a nested untrusted child.

    The child must mount only /workspace as output, never the controller's host
    /output or /controller mounts. Resource limits wrap this entire command.
    """
    if type(workspace_bytes) is not int or not 0 < workspace_bytes <= 8 * 1024 ** 3:
        raise ValueError('workspace size must be a positive byte count at most 8 GiB')
    controller = Path(controller).absolute()
    if not controller.is_dir() or controller.resolve() != controller:
        raise ValueError('controller must be a real directory without symlink components')
    mounts = [Path(source).absolute(), Path(output).absolute()]
    if toolchain is not None:
        mounts.append(Path(toolchain).absolute())
    if any(controller == path or controller in path.parents or path in controller.parents for path in mounts):
        raise ValueError('controller must not overlap source, output or toolchain')
    base = build_command(source, output, command, toolchain=toolchain)
    split = base.index('--')
    return [*base[:split], '--ro-bind', str(controller), '/controller',
            '--size', str(workspace_bytes), '--tmpfs', '/workspace', *base[split:]]


def limit_command(command, *, memory_bytes=4 * 1024 ** 3, tasks=256,
                  cpu_percent=400, runtime_seconds=1800):
    """Run a sandbox in a disposable user-service cgroup, outside its namespace."""
    for value in (memory_bytes, tasks, cpu_percent, runtime_seconds):
        if type(value) is not int or value <= 0:
            raise ValueError('resource limits must be positive integers')
    if cpu_percent > 1600:
        raise ValueError('CPU limit exceeds the remote 16-CPU policy')
    if not isinstance(command, (list, tuple)) or not command or any(
            not isinstance(arg, str) or '\0' in arg for arg in command):
        raise ValueError('sandbox command must be a nonempty argument list')
    return ['/usr/bin/systemd-run', '--user', '--wait', '--pipe', '--collect', '--same-dir',
            '--property=MemoryMax=' + str(memory_bytes),
            '--property=TasksMax=' + str(tasks),
            '--property=CPUQuota=' + str(cpu_percent) + '%',
            '--property=CPUQuotaPeriodSec=100ms',
            '--property=RuntimeMaxSec=' + str(runtime_seconds),
            '--property=TimeoutStopSec=5', '--property=KillMode=control-group',
            '--', *command]


def memory_command(source, command, *, toolchain=None, workspace_bytes=2 * 1024 ** 3, i386_runtime=None):
    """Use only a byte-limited tmpfs for child output; finals return over a pipe."""
    if type(workspace_bytes) is not int or not 0 < workspace_bytes <= 8 * 1024 ** 3:
        raise ValueError('workspace size must be a positive byte count at most 8 GiB')
    source = Path(source).absolute()
    if not source.is_dir() or source.resolve() != source:
        raise ValueError('sandbox source must be a real directory without symlink components')
    compiler_mount = _compiler_mount(toolchain, [source])
    libraries = None
    if i386_runtime is not None:
        runtime = Path(i386_runtime).absolute()
        if (not runtime.is_dir() or runtime.resolve() != runtime or runtime == source
                or runtime in source.parents or source in runtime.parents):
            raise ValueError('legacy runtime must be a separate real directory')
        libraries = ['--dir', '/lib', '--ro-bind', '/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu']
        for name in ('ld-linux.so.2', 'libc.so.6', 'libdl.so.2', 'libm.so.6'):
            path = runtime / name
            if not path.is_file() or not path.resolve().is_relative_to(runtime):
                raise ValueError('missing or escaping legacy compiler library: ' + name)
            libraries += ['--ro-bind', str(path), '/lib/' + name]
    return _runtime_command(source, ['--size', str(workspace_bytes), '--tmpfs', '/output'],
                            compiler_mount, command, libraries=libraries)


def build_command(source, output, command, *, toolchain=None):
    """Expose read-only source and a separate writable output, with no host env."""
    paths = [Path(source).absolute(), Path(output).absolute()]
    for path in paths:
        if not path.is_dir() or path.resolve() != path:
            raise ValueError('sandbox mounts must be real directories without symlink components')
    source, output = paths
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('sandbox source and output must not overlap')
    return _runtime_command(source, ['--bind', str(output), '/output'],
                            _compiler_mount(toolchain, paths), command)


def _compiler_mount(toolchain, paths):
    if toolchain is not None:
        toolchain = Path(toolchain).absolute()
        if not toolchain.is_dir() or toolchain.resolve() != toolchain:
            raise ValueError('toolchain must be a real directory without symlink components')
        if any(toolchain == path or toolchain in path.parents or path in toolchain.parents for path in paths):
            raise ValueError('toolchain must not overlap source or output')
        return ['--ro-bind', str(toolchain), '/toolchain']
    return []


def _runtime_command(source, output_mount, compiler_mount, command, *, libraries=None):
    if not command or any(not isinstance(arg, str) or '\0' in arg for arg in command):
        raise ValueError('sandbox command must be a nonempty argument list')
    return [
        '/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session',
        '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr',
        '--symlink', 'usr/bin', '/bin', *(libraries or ['--symlink', 'usr/lib', '/lib']),
        '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--ro-bind', str(source), '/source',
        *output_mount, '--chdir', '/output',
        *compiler_mount, '--clearenv', '--setenv', 'PATH',
        '/toolchain/bin:/usr/bin:/bin' if compiler_mount else '/usr/bin:/bin', '--', *command,
    ]
