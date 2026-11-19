"""Live collector check; run through ni-build ARM in a new working directory."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_artifacts
import sandbox

root = Path.cwd()
source, output = root / 'source', root / 'output'
source.mkdir(); output.mkdir()
(source / 'input').write_bytes(b'firmware-input')
payload = '''set -eu
cat /source/input > /output/firmware.bin
printf '{"status":"pass"}' > /output/build.result.json
'''
command = sandbox.limit_command(sandbox.build_command(source, output, ['/bin/sh', '-c', payload]),
                                memory_bytes=268435456, tasks=32, cpu_percent=200, runtime_seconds=15)
result = subprocess.run(command, capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result.stderr
try:
    build_artifacts.collect(output, root / 'rejected', {'firmware.bin': 64})
except ValueError:
    pass
else:
    raise AssertionError('forged stage result was accepted')
assert not (root / 'rejected').exists()
(output / 'build.result.json').unlink()
collected = build_artifacts.collect(output, root / 'trusted', {'firmware.bin': 64})
assert (root / 'trusted/firmware.bin').read_bytes() == b'firmware-input'
assert collected['firmware.bin']['bytes'] == 14
failure = sandbox.limit_command(sandbox.build_command(source, output, ['/bin/sh', '-c',
    'printf \'{"status":"pass"}\' > /output/build.result.json; exit 7']),
    memory_bytes=268435456, tasks=32, cpu_percent=200, runtime_seconds=15)
result = subprocess.run(failure, capture_output=True, text=True, timeout=30)
assert result.returncode != 0, 'untrusted passing JSON concealed process failure'
assert (root / 'trusted/firmware.bin').read_bytes() == b'firmware-input'
print(json.dumps({'type': 'result', 'status': 'pass', 'checks': [
    'forged result rejected', 'fresh bounded copy', 'failure remains failure', 'trusted copy unchanged']}))
