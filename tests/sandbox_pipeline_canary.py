"""Live bounded pipe transport and collection; synthetic bytes, not firmware."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_artifacts
import sandbox
import sandbox_process
import sandbox_transport


def inside(root):
    source = root / 'source'
    contract = {'firmware.bin': 64}
    final_bytes = b'{"firmware.bin":14}\nfirmware-input'

    def execute(code, **limits):
        command = sandbox.memory_command(source, ['/usr/bin/python3', '-I', '-c', code],
                                         workspace_bytes=16777216)
        return sandbox_process.run(command, max_stdout_bytes=4096, max_stderr_bytes=1024,
                                   **limits)

    result = execute("import sys; sys.stderr.buffer.write(b'x'*1048576); "
                     "sys.stdout.buffer.write(" + repr(final_bytes) + ")")
    assert result.returncode == 0 and result.problem is None, result
    assert result.stderr_truncated and len(result.stderr) == 1024
    decoded = sandbox_transport.decode(result.stdout, contract)
    staging = root / 'received'; staging.mkdir()
    for name, data in decoded.items():
        with (staging / name).open('xb') as output:
            output.write(data)
    collected = build_artifacts.collect(staging, root / 'trusted', contract)
    assert collected['firmware.bin']['bytes'] == 14
    assert (root / 'trusted/firmware.bin').read_bytes() == b'firmware-input'

    flood = sandbox.memory_command(source, ['/bin/sh', '-c', 'head -c 65536 /dev/zero'],
                                   workspace_bytes=16777216)
    result = sandbox_process.run(flood, max_stdout_bytes=1024, timeout_seconds=5)
    assert result.problem == 'stdout_limit' and len(result.stdout) <= 1024, result
    result = execute("import sys; sys.stdout.buffer.write(" + repr(final_bytes) + "); sys.exit(7)")
    assert result.returncode == 7 and result.problem is None, result
    result = execute("import sys; sys.stdout.buffer.write(b'{\"build.result.json\":3}\\nabc')")
    assert result.returncode == 0, result
    try:
        sandbox_transport.decode(result.stdout, contract)
    except ValueError:
        pass
    else:
        raise AssertionError('forged result declaration accepted')
    result = execute('import time; time.sleep(10)', timeout_seconds=0.2)
    assert result.problem == 'timeout' and result.returncode != 0, result
    assert (root / 'trusted/firmware.bin').read_bytes() == b'firmware-input'
    assert (root / 'host-canary').read_text() == 'trusted'
    print(json.dumps({'type': 'result', 'status': 'pass', 'synthetic': True, 'checks': [
        'cgroup around trusted runner and sandbox', 'bounded stderr tail', 'pipe decode and collection',
        'stdout flood stopped', 'failure exit preserved', 'forged declaration rejected',
        'timeout stopped', 'trusted bytes and canary unchanged']}))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inside', action='store_true')
parser.add_argument('--root', type=Path, default=Path.cwd())
args = parser.parse_args()
if args.inside:
    inside(args.root)
else:
    root = args.root.resolve()
    (root / 'source').mkdir()
    (root / 'host-canary').write_text('trusted')
    command = sandbox.limit_command(['/usr/bin/python3', '-I', str(Path(__file__).resolve()),
                                     '--inside', '--root', str(root)], memory_bytes=268435456,
                                     tasks=32, cpu_percent=200, runtime_seconds=45)
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    print(result.stdout, end='')
