"""Trusted command contract for dispatching the isolated firmware worker.

Only explicit firmware and controller inputs are snapshotted, even when their
common ancestor is the filesystem root. Paths reach the remote shell as data.
"""
import os
from pathlib import Path

HELPERS = ('sandbox_build.py', 'sandbox.py', 'sandbox_process.py',
           'sandbox_transport.py', 'build_artifacts.py')
FINALS = '.gate-build-finals'
BUILD_JOBS = 4


def command(tree, out, build_type, toolchain, jobs, revision_metadata, *, controller=None,
            profile='v2-prime', measure_only=False):
    valid_toolchains = {'v1-lpc1768': ('gcc48', 'gcc12'), 'v2-prime': ('gcc10',)}
    if (build_type not in ('Release', 'Debug') or toolchain not in valid_toolchains.get(profile, ())
            or type(jobs) is not int or not 1 <= jobs <= 16
            or type(revision_metadata) is not bool or type(measure_only) is not bool
            or (measure_only and profile != 'v1-lpc1768')):
        raise ValueError('invalid sandbox dispatch configuration')
    tree = Path(tree).absolute()
    controller = Path(controller or Path(__file__).resolve().parent).absolute()
    root = Path(os.path.commonpath((tree, controller)))
    if root == tree or controller == root:
        raise ValueError('firmware and trusted controller must have separate snapshot directories')
    source = tree.relative_to(root).as_posix()
    helper = controller.relative_to(root).as_posix()
    script = '''set -eu
export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
compiler=$(readlink -f "$(dirname "$(dirname "$(command -v arm-none-eabi-gcc)")")")
exec systemd-run --user --wait --pipe --collect --property=MemoryMax=4294967296 --property=TasksMax=256 --property=CPUQuota=400% --property=CPUQuotaPeriodSec=100ms --property=RuntimeMaxSec=1800 --property=TimeoutStopSec=5 --property=KillMode=control-group -- /usr/bin/python3 -B -I "$PWD/$1/sandbox_build.py" --tree "$PWD/$2" --out "$PWD/.gate-build-finals" --compiler "$compiler" --jobs "$3"'''
    if build_type == 'Debug':
        script += ' --debug'
    if profile == 'v1-lpc1768':
        script += ' --profile v1-lpc1768'
        if toolchain == 'gcc48':
            support = '/data/ni-build-service/support/gcc-arm-none-eabi-4_8-2014q1'
            script = script.replace('compiler=$(readlink -f "$(dirname "$(dirname "$(command -v arm-none-eabi-gcc)")")")',
                                    'compiler=' + support + '/tool')
            script += ' --i386-runtime ' + support + '/i386lib'
        if measure_only:
            script += ' --measure-only'
    elif not revision_metadata:
        script += ' --no-revision-metadata'
    # GCC 4.8's dispatcher wrapper already creates a legacy-library namespace.
    # Use the normal host dispatcher and let our one sandbox mount those libs.
    host_toolchain = 'gcc12' if profile == 'v1-lpc1768' else toolchain
    arguments = ['ni-build', 'arm', '--toolchain', host_toolchain, '--root', str(root),
                 '--include', source]
    for name in HELPERS:
        arguments += ['--include', helper + '/' + name]
    return arguments + ['--cwd', '.', '--out', str(out), '--', 'sh', '-c', script,
                        'gate-sandbox-build', helper, source, str(min(jobs, BUILD_JOBS))]


def simulator_command(source, out, generation, *, five=False, controller=None):
    """Dispatch trusted orchestration separately from simulator/firmware input."""
    if (type(generation) is not int or generation not in (1, 2) or type(five) is not bool
            or (five and generation != 2)):
        raise ValueError('invalid simulator dispatch configuration')
    source = Path(source).absolute()
    controller = Path(controller or Path(__file__).resolve().parent).absolute()
    root = Path(os.path.commonpath((source, controller)))
    if root == source or root == controller:
        raise ValueError('simulator source and trusted controller must be separate')
    source_relative = source.relative_to(root).as_posix()
    helper = controller.relative_to(root).as_posix()
    script = '''set -eu
export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
exec systemd-run --user --wait --pipe --collect --property=MemoryMax=4294967296 --property=TasksMax=256 --property=CPUQuota=400% --property=CPUQuotaPeriodSec=100ms --property=RuntimeMaxSec=1800 --property=TimeoutStopSec=5 --property=KillMode=control-group -- /usr/bin/python3 -B -I "$PWD/$1/sandbox_simulator_build.py" --tree "$PWD/$2" --out "$PWD/.gate-simulator-finals"'''
    script += ' --generation ' + str(generation)
    if five:
        script += ' --five'
    command = ['ni-build', 'arm', '--toolchain', 'gcc12', '--root', str(root), '--include', source_relative]
    for name in (*HELPERS, 'sandbox_simulator_build.py'):
        command += ['--include', helper + '/' + name]
    return command + ['--cwd', '.', '--out', str(out), '--', 'sh', '-c', script,
                      'gate-simulator-build', helper, source_relative]
