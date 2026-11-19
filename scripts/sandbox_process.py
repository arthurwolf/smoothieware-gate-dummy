"""Bound pipes and execution time for a task-owned Linux sandbox process group.

The caller wraps this trusted runner and its sandbox in resource limits. Do not
pass systemd-run here: a service belongs to a different process group. A sandbox
PID namespace must prevent untrusted descendants from escaping group cleanup.
"""
from dataclasses import dataclass
import math
import os
import selectors
import signal
import subprocess
import time


@dataclass(frozen=True)
class ProcessOutput:
    returncode: int
    stdout: bytes
    stderr: bytes
    stderr_truncated: bool
    problem: str | None


def validate_options(command, *, max_stdout_bytes=64 * 1024**2,
                     max_stderr_bytes=256 * 1024, timeout_seconds=1800):
    """Validate capture policy before any external resource is created."""
    if (not isinstance(command, (list, tuple)) or not command or
            any(not isinstance(arg, str) or '\0' in arg for arg in command)):
        raise ValueError('command must be a nonempty argument list')
    for value, cap in ((max_stdout_bytes, 128 * 1024**2), (max_stderr_bytes, 4 * 1024**2)):
        if type(value) is not int or not 0 < value <= cap:
            raise ValueError('invalid process output byte limit')
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 3600):
        raise ValueError('invalid process timeout')


def run(command, *, max_stdout_bytes=64 * 1024**2, max_stderr_bytes=256 * 1024,
        timeout_seconds=1800):
    """Capture bounded final bytes and a diagnostic tail, preserving exit code.

    Stdout overflow or timeout kills only the new session created here. Stderr
    overflow discards old diagnostics while draining the pipe to avoid deadlock.
    """
    validate_options(command, max_stdout_bytes=max_stdout_bytes,
                     max_stderr_bytes=max_stderr_bytes, timeout_seconds=timeout_seconds)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    stdout, stderr = bytearray(), bytearray()
    problem, truncated, waited = None, False, False
    deadline = time.monotonic() + timeout_seconds

    def stop():
        # No poll/wait has reaped the leader, so its PID cannot be reused here.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    problem = problem or 'timeout'
                    stop()
                    break
                for key, _ in selector.select(remaining):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'stderr':
                        stderr.extend(data)
                        if len(stderr) > max_stderr_bytes:
                            del stderr[:-max_stderr_bytes]
                            truncated = True
                    else:
                        available = max_stdout_bytes - len(stdout)
                        stdout.extend(data[:available])
                        if len(data) > available:
                            problem = 'stdout_limit'
                            stop()
                            break
                if problem:
                    break
        if problem:
            stop()
        try:
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            problem = problem or 'timeout'
            stop()
            code = process.wait()
        waited = True
        return ProcessOutput(code, bytes(stdout), bytes(stderr), truncated, problem)
    finally:
        if not waited:
            stop()
            process.wait()
        process.stdout.close()
        process.stderr.close()
