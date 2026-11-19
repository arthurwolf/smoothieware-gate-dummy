"""Live byte-bounded workspace check through ni-build ARM in a fresh cwd."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox

root = Path.cwd()
source = root / 'source'; source.mkdir()
(source / 'input').write_bytes(b'firmware-input')
canary = root / 'host-canary'; canary.write_text('trusted')
payload = '''set -eu
test ! -e /controller
test ! -e /proc/1/root/data/ni-build-service
test ! -e /host-canary
test "$(stat -f -c %T /output)" = tmpfs
if dd if=/dev/zero of=/output/oversize bs=1048576 count=32 2>/dev/null; then exit 11; fi
rm /output/oversize
cat /source/input > /output/firmware.bin
(sleep 1; printf descendant-leaked >&2) &
cat /output/firmware.bin
'''
command = sandbox.memory_command(source, ['/bin/sh', '-c', payload], workspace_bytes=16777216)
command = sandbox.limit_command(command, memory_bytes=268435456, tasks=32,
                                cpu_percent=200, runtime_seconds=15)
result = subprocess.run(command, capture_output=True, timeout=30)
assert result.returncode == 0, (result.returncode, result.stderr)
assert result.stdout == b'firmware-input', result.stdout
assert b'descendant-leaked' not in result.stderr, result.stderr
assert canary.read_text() == 'trusted'
print(json.dumps({'type': 'result', 'status': 'pass', 'checks': [
    'tmpfs workspace', '16 MiB storage exhaustion', 'no host output mount',
    'pipe-only final bytes', 'descendant termination', 'host canary unchanged']}))
