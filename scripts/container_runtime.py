"""Daemon-backed isolation adapter; accept bytes only after confirmed cleanup.

The caller supplies an already provisioned, trusted image pinned by digest.
No source-controlled program receives a daemon socket or writable host mount.
"""
from pathlib import Path
import os
import re
import subprocess
import threading
import uuid

import sandbox_process
from sandbox_process import ProcessOutput

DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']


def _mount_path(value):
    path = Path(value).absolute()
    if (not path.is_dir() or path.resolve() != path or
            any(character in str(path) for character in (',', '\n', '\r', '\0'))):
        raise ValueError('container input must be a real directory with a safe mount path')
    return path


class DockerRuntime:
    def __init__(self, image):
        if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}', image):
            raise ValueError('container image must be pinned by SHA-256 digest')
        self.image = image
        self._lock = threading.Lock()
        self._unverified = None

    def command(self, source, arguments, *, name, toolchain=None,
                workspace_bytes=2 * 1024**3):
        source = _mount_path(source)
        if not isinstance(name, str) or not re.fullmatch(r'gate-[a-z0-9-]{1,64}', name):
            raise ValueError('invalid owned container name')
        if (not isinstance(arguments, (list, tuple)) or not arguments or
                any(not isinstance(item, str) or '\0' in item for item in arguments)):
            raise ValueError('container command must be a nonempty argument list')
        if type(workspace_bytes) is not int or not 0 < workspace_bytes <= 8 * 1024**3:
            raise ValueError('invalid private workspace size')
        mounts = ['--mount', f'type=bind,src={source},dst=/source,readonly']
        if toolchain is not None:
            compiler = _mount_path(toolchain)
            if source == compiler or source in compiler.parents or compiler in source.parents:
                raise ValueError('compiler and source must not overlap')
            mounts += ['--mount', f'type=bind,src={compiler},dst=/toolchain,readonly']
        return [*DOCKER, 'run', '--name', name, '--read-only', '--rm', '--init',
                '--network', 'none', '--pull', 'never', '--log-driver', 'none',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '256', '--memory', '4294967296',
                '--memory-swap', '4294967296', '--cpus', '4',
                '--user', f'{os.getuid()}:{os.getgid()}', '--no-healthcheck',
                '--tmpfs', f'/output:rw,nosuid,nodev,size={workspace_bytes},mode=1777',
                '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777',
                '--workdir', '/output', *mounts, '--entrypoint', '/usr/bin/env',
                self.image, '-i', 'PATH=/toolchain/bin:/usr/bin:/bin', *arguments]

    def run(self, source, arguments, *, toolchain=None,
            workspace_bytes=2 * 1024**3, **capture_options):
        # One adapter owns one lifecycle at a time. Uncertainty quarantines it;
        # subsequent cases must not silently launch more possibly leaked work.
        with self._lock:
            if self._unverified is not None:
                return self._unverified
            result = self._execute(source, arguments, toolchain=toolchain,
                                   workspace_bytes=workspace_bytes, **capture_options)
            if result.problem in ('container_creation_indeterminate', 'container_cleanup_unverified'):
                self._unverified = result
            return result

    def _execute(self, source, arguments, *, toolchain, workspace_bytes, **capture_options):
        name = 'gate-' + uuid.uuid4().hex
        command = self.command(source, arguments, name=name, toolchain=toolchain,
                               workspace_bytes=workspace_bytes)
        sandbox_process.validate_options(command, **capture_options)
        command[3] = 'create'
        container_id = None
        creation_uncertain = False
        cleaned = False
        try:
            created = sandbox_process.run(command, max_stdout_bytes=4096, timeout_seconds=30)
            identifier = created.stdout.strip()
            if created.returncode or created.problem or not re.fullmatch(b'[0-9a-f]{64}', identifier):
                creation_uncertain = True
                result = ProcessOutput(75, b'', created.stderr, created.stderr_truncated,
                                       'container_creation_indeterminate')
            else:
                container_id = identifier.decode('ascii')
                result = sandbox_process.run([*DOCKER, 'start', '--attach', container_id],
                                             **capture_options)
        except Exception as error:
            creation_uncertain = container_id is None
            result = ProcessOutput(75, b'', str(error).encode('utf-8')[-262144:], False,
                                   'container_launch_failed')
        finally:
            # The CLI process group cannot kill a daemon-owned container. Remove
            # only our unique name, then independently confirm its absence.
            try:
                subprocess.run([*DOCKER, 'rm', '--force', container_id or name], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15, check=False)
                check = subprocess.run([*DOCKER, 'ps', '--all', '--quiet', '--filter',
                                        f'name=^/{name}$'], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       timeout=15, check=False)
                cleaned = not creation_uncertain and check.returncode == 0 and not check.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                cleaned = False
        if not cleaned:
            diagnostic = result.stderr + ('\nUnverified owned container: ' + name + '\n').encode('ascii')
            return ProcessOutput(75, b'', diagnostic[-262144:],
                                 result.stderr_truncated or len(diagnostic) > 262144,
                                 'container_creation_indeterminate' if creation_uncertain else 'container_cleanup_unverified')
        if result.returncode or result.problem:
            return ProcessOutput(result.returncode, b'', result.stderr,
                                 result.stderr_truncated, result.problem)
        return result
