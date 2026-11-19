"""Run an untrusted host simulator without mounting controller output.

The trusted caller must run under a resource-limited service. Each attempt gets
only three read-only inputs and a fresh bounded tmpfs. Returned JSON bytes remain
untrusted and need the existing simulator checker; no child stage result is used.
"""
import math
from pathlib import Path
import tempfile

import sandbox
import sandbox_process
from sandbox_inputs import copy_input
from sandbox_process import ProcessOutput

RESULT_BYTES = 64 * 1024**2
SCRIPT = '''set -eu
/source/simulator --config /source/config.txt --gcode /source/input.gcode --out /output/result.json "$@" >&2
exec /usr/bin/cat /output/result.json
'''


def validate_arguments(arguments):
    """Share the argument contract between metadata parsing and execution."""
    if (not isinstance(arguments, (list, tuple)) or len(arguments) > 64 or
            any(not isinstance(arg, str) or '\0' in arg or len(arg) > 4096 for arg in arguments)):
        raise ValueError('invalid simulator argument list')


def run(simulator, config, gcode, arguments, *, timeout, scratch_parent, runtime=None):
    """Return bounded output and the actual exit; never retain bytes on failure."""
    validate_arguments(arguments)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError('invalid simulator timeout')
    parent = Path(scratch_parent).absolute()
    if not parent.is_dir() or parent.resolve() != parent:
        raise ValueError('simulator scratch parent must be a real directory')
    with tempfile.TemporaryDirectory(prefix='gate-replay-', dir=parent) as temporary:
        source = Path(temporary)
        try:
            copy_input(simulator, source / 'simulator', 48 * 1024**2, 0o500, minimum=1)
            copy_input(config, source / 'config.txt', 1024**2, 0o400)
            copy_input(gcode, source / 'input.gcode', 16 * 1024**2, 0o400)
        except OSError as error:
            raise ValueError('unable to copy simulator input safely: ' + str(error)) from error
        child = ['/bin/sh', '-c', SCRIPT, 'gate-replay', *arguments]
        if runtime is None:
            command = sandbox.memory_command(source, child, workspace_bytes=128 * 1024**2)
            result = sandbox_process.run(command, max_stdout_bytes=RESULT_BYTES, timeout_seconds=timeout)
        else:
            result = runtime.run(source, child, workspace_bytes=128 * 1024**2,
                                 max_stdout_bytes=RESULT_BYTES, timeout_seconds=timeout)
        if result.returncode or result.problem:
            return ProcessOutput(result.returncode, b'', result.stderr, result.stderr_truncated, result.problem)
        return result
