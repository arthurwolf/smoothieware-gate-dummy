"""Run only in a fresh task-owned working directory through ni-build ARM.

Creates source, returned and collector directories; never overwrites existing ones.
Requires bubblewrap and the merged-/usr Linux runtime tested on ni-vm.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bounded', action='store_true')
args = parser.parse_args()
root=Path.cwd()
source=root/'source'; output=root/'returned'; collector=root/'collector'
for path in (source,output,collector): path.mkdir()
(source/'input').write_text('original')
(collector/'result.json').write_text('trusted')
os.environ['GATE_TEST_SECRET']='collector-only'
payload="""set -eu
test "$(cat /source/input)" = original
if printf changed > /source/input 2>/dev/null; then exit 10; fi
test ! -e /collector
test -z "${GATE_TEST_SECRET:-}"
test ! -e /proc/1/root/data/ni-build-service
printf forged > /output/result.json
printf payload > /output/artifact.bin
"""
command = sandbox.build_command(source,output,['/bin/sh','-c',payload])
if args.bounded:
    command = sandbox.limit_command(command, memory_bytes=268435456, tasks=32,
                                    cpu_percent=200, runtime_seconds=15)
result=subprocess.run(command,capture_output=True,text=True,timeout=30)
assert result.returncode==0, (result.returncode,result.stderr)
assert (source/'input').read_text()=='original'
assert (collector/'result.json').read_text()=='trusted'
assert (output/'artifact.bin').read_text()=='payload'
if args.bounded:
    timed = sandbox.limit_command(sandbox.build_command(source,output,['/bin/sleep','10']),
                                  runtime_seconds=1)
    result = subprocess.run(timed,capture_output=True,text=True,timeout=15)
    assert result.returncode != 0, result
    assert 'result: timeout' in result.stderr, result.stderr
print(json.dumps({'type':'result','status':'pass','checks':['read-only source','collector integrity','cleared environment','hidden host storage','returned output']}))
