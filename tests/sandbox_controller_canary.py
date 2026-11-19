"""Live nested namespace/storage pilot, through ni-build ARM in a fresh cwd."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_artifacts
import sandbox


def inside():
    diagnostics = {}
    for name in ('attr/current', 'uid_map', 'status'):
        try:
            value = Path('/proc/self/' + name).read_text()
            if name == 'status':
                value = '\n'.join(line for line in value.splitlines() if line.startswith(('Cap', 'NoNewPrivs', 'Seccomp')))
            diagnostics[name] = value
        except OSError as error:
            diagnostics[name] = str(error)
    print(json.dumps({'type': 'progress', 'boundary': 'trusted-controller', 'diagnostics': diagnostics}), flush=True)
    space = __import__('os').statvfs('/workspace')
    assert space.f_blocks * space.f_frsize == 16777216, 'workspace is not byte bounded'
    payload = '''set -eu
test ! -e /controller
test ! -e /proc/1/root/controller
test ! -e /output/canary
test ! -e /proc/1/root/output/canary
mkdir /output/finals
cat /source/input > /output/finals/firmware.bin
if dd if=/dev/zero of=/output/oversize bs=1048576 count=32 2>/dev/null; then exit 11; fi
rm /output/oversize
(sleep 1; printf leaked > /output/late) &
'''
    child = sandbox.build_command('/source', '/workspace', ['/bin/sh', '-c', payload])
    result = subprocess.run(child, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    subprocess.run(['/bin/sleep', '2'], check=True)
    assert not Path('/workspace/late').exists(), 'descendant survived the inner PID namespace'
    assert Path('/output/canary').read_text() == 'trusted'
    result = build_artifacts.collect('/workspace/finals', '/output/trusted', {'firmware.bin': 64})
    assert result['firmware.bin']['bytes'] == 14
    print(json.dumps({'type': 'result', 'status': 'pass', 'checks': [
        'nested namespaces', 'hidden controller and host output', '16 MiB workspace exhaustion',
        'descendant termination', 'bounded final collection']}))


def outside():
    root = Path.cwd()
    source, output, controller = (root / name for name in ('source', 'returned', 'controller'))
    for path in (source, output, controller): path.mkdir()
    (source / 'input').write_bytes(b'firmware-input')
    (output / 'canary').write_text('trusted')
    scripts = Path(sandbox.__file__).parent
    for name in ('sandbox.py', 'build_artifacts.py'):
        shutil.copyfile(scripts / name, controller / name)
    shutil.copyfile(__file__, controller / 'nested.py')
    command = sandbox.controller_command(source, output, controller,
        ['/usr/bin/python3', '-I', '/controller/nested.py', '--inside'], workspace_bytes=16777216)
    command = sandbox.limit_command(command, memory_bytes=268435456, tasks=64,
                                    cpu_percent=200, runtime_seconds=30)
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert (output / 'trusted/firmware.bin').read_bytes() == b'firmware-input'
    assert (output / 'canary').read_text() == 'trusted'
    print(result.stdout, end='')


if __name__ == '__main__':
    inside() if '--inside' in sys.argv else outside()
