"""Dispatch trusted size orchestration; hostile ELF parsers stay in a sandbox."""
import os
from pathlib import Path
import shutil
import tempfile

import sandbox

SCRIPTS = Path(__file__).resolve().parent


def measure(elfs, results, sim_dir, base_rev, toolchain, profile, stage):
    paths = [SCRIPTS, Path(sim_dir).absolute() / 'checkers/size_check.py',
             *(Path(path).absolute() for path in elfs.values())]
    root = Path(os.path.commonpath([str(path.parent) for path in paths]))
    relative = lambda path: str(Path(path).absolute().relative_to(root))
    arguments = []
    for label, flag in [('base', 'base'), ('pr', 'pr')]:
        for kind in ('release', 'debug'):
            arguments += [f'--{flag}-{kind}', relative(elfs[label, kind])]
    arguments += ['--sandbox', '--base-rev', base_rev, '--toolchain', toolchain,
                  '--sim-dir', relative(sim_dir), '--architecture',
                  'stm32h745' if profile.generation == 2 else 'lpc1769',
                  '--out', 'size-output/size.result.json']
    # The selected firmware toolchain remains report provenance; GCC 12 binutils
    # independently measure both generations without executing their ELF code.
    limited = sandbox.limit_command(['/usr/bin/python3', '-B', '-I',
                                     relative(SCRIPTS / 'size.py'), *arguments])
    script = ('set -eu; export XDG_RUNTIME_DIR=/run/user/1000 '
              'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus; '
              'compiler=$(readlink -f "$(dirname "$(dirname "$(command -v arm-none-eabi-gcc)")")"); '
              'mkdir size-output; "$@" --compiler "$compiler" || :')
    with tempfile.TemporaryDirectory(prefix='size-return-', dir=results) as returned:
        command = ['ni-build', 'arm', '--toolchain', 'gcc12', '--root', str(root)]
        for path in paths:
            command += ['--include', relative(path)]
        command += ['--cwd', '.', '--out', returned, '--', 'sh', '-c', script,
                    'gate-size', *limited]
        done = stage('size', command)
        result = Path(returned) / 'size-output/size.result.json'
        if done.returncode == 0 and result.is_file():
            shutil.copyfile(result, Path(results) / 'size.result.json')
            return True
    return False
