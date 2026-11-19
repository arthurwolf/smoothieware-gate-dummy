"""Trusted firmware worker for a whole ni-build job, not a workflow entrypoint yet.

Run this worker itself under cgroup limits. Only its direct bubblewrap child
executes firmware code; no host output directory enters that namespace. Header
checks below identify ARM executable bytes, not complete ELF semantic validity.
"""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_artifacts
import sandbox
import sandbox_process
import sandbox_transport

FINALS = {'smoothiev2.elf': 48 * 1024**2, 'smoothiev2.bin': 4 * 1024**2}
V1_FINALS = {'main.elf': 48 * 1024**2, 'main.bin': 4 * 1024**2}
TOTAL_BYTES = sum(FINALS.values())
V1_LINKER_SCRIPT = 'mbed/src/vendor/NXP/cmsis/LPC1768/GCC_ARM/LPC1768.ld'
V1_FLASH_LINE = 'FLASH (rx) : ORIGIN = 16K, LENGTH = (512K - 16K)'
V1_OVERSIZE_FLASH_LINE = 'FLASH (rx) : ORIGIN = 16K, LENGTH = (4096K - 16K)'
V1_LEGACY_BANNER = 'arm-none-eabi-gcc (GNU Tools for ARM Embedded Processors) 4.8.3 20140228 (release) [ARM/embedded-4_8-branch revision 208322]'
V1_MODERN_BANNER = 'arm-none-eabi-gcc (Arm GNU Toolchain 12.3.Rel1 (Build arm-12.35)) 12.3.1 20230626'
MEASURE_V1 = ('from pathlib import Path\nimport sys\n'
              'path=Path(' + repr(V1_LINKER_SCRIPT) + ')\ntext=path.read_text()\n'
              'old=' + repr(V1_FLASH_LINE) + '\nnew=' + repr(V1_OVERSIZE_FLASH_LINE) + '\n'
              'if text.count(old)!=1:\n print("unsupported V1 measurement linker layout",file=sys.stderr);sys.exit(75)\n'
              'path.write_text(text.replace(old,new,1))\n')

# This exporter runs inside the untrusted namespace. Its entire output is still
# untrusted; the host independently bounds capture and validates declarations.
EXPORT_TEMPLATE = '''import json,os,stat,sys
files=[]
try:
 for path,limit in zip(sys.argv[1:],__FINAL_LIMITS__,strict=True):
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  files.append((path,fd,os.fstat(fd)))
  info=files[-1][2]
  if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=limit:
   raise ValueError('invalid firmware final file')
 header={os.path.basename(path):info.st_size for path,fd,info in files}
 sys.stdout.buffer.write(json.dumps(header).encode('utf-8')+b'\\n')
 for path,fd,info in files:
  remaining=info.st_size
  while remaining:
   data=os.read(fd,min(remaining,1048576))
   if not data: raise ValueError('firmware final file truncated')
   sys.stdout.buffer.write(data);remaining-=len(data)
finally:
 for path,fd,info in files: os.close(fd)
'''
EXPORT = EXPORT_TEMPLATE.replace('__FINAL_LIMITS__', '(50331648,4194304)')

BUILD_SIMULATOR = '''set -eu
cp -a /source /output/build
cd /output/build
make -C sim -j4 COVERAGE=0 BUILD=../simbuild "$@" >&2
'''

BUILD = '''set -eu
cp -a /source /output/firmware
cd /output/firmware/Firmware
export ARMTOOLS=/toolchain/bin target=Prime debug="$1"
if [ "$3" = 1 ]; then export GIT_DIR=../.gate-git; fi
rake -m -j "$2" >&2
exec /usr/bin/python3 -I -c "$4" smoothiev2_Prime/smoothiev2.elf smoothiev2_Prime/smoothiev2.bin
'''
BUILD_V1 = '''set -eu
banner=$(/toolchain/bin/arm-none-eabi-gcc --version | /usr/bin/head -n 1)
test "$banner" = "$5" || { echo 'V1 compiler version mismatch' >&2; exit 75; }
cp -a /source /output/firmware
cd /output/firmware
if [ "$3" = 1 ]; then /usr/bin/python3 -I -c "$6" >&2 || exit 75; fi
make -j "$2" BUILD_TYPE="$1" >&2
exec /usr/bin/python3 -I -c "$4" LPC1768/main.elf LPC1768/main.bin
'''


@dataclass(frozen=True)
class BuildOutput:
    returncode: int
    diagnostics: bytes
    diagnostics_truncated: bool
    problem: str | None
    files: dict


def build_v2(source, output, toolchain, *, debug=False, jobs=4, revision_metadata=True, runtime=None):
    """Compile inside bounded tmpfs, then validate and collect exact final bytes."""
    if type(jobs) is not int or not 1 <= jobs <= 16 or type(debug) is not bool or type(revision_metadata) is not bool:
        raise ValueError('invalid V2 build configuration')
    child = ['/bin/sh', '-c', BUILD, 'gate-v2-build',
             '1' if debug else '0', str(jobs), '1' if revision_metadata else '0', EXPORT]
    return _build(source, output, toolchain, child, FINALS, runtime=runtime)


def build_v1(source, output, toolchain, i386_runtime=None, *, debug=False, jobs=4, measure_only=False,
             runtime=None, compiler_profile=None):
    """Build the existing V1 make recipe with its private compiler runtime."""
    if (type(jobs) is not int or not 1 <= jobs <= 16 or type(debug) is not bool
            or type(measure_only) is not bool):
        raise ValueError('invalid V1 build configuration')
    compiler_profile = compiler_profile or ('gcc48' if i386_runtime is not None else 'gcc12')
    if (compiler_profile not in ('gcc48', 'gcc12') or
            (i386_runtime is not None and compiler_profile != 'gcc48')):
        raise ValueError('invalid V1 compiler profile')
    child = ['/bin/sh', '-c', BUILD_V1, 'gate-v1-build',
             'Debug' if debug else 'Release', str(jobs), '1' if measure_only else '0', EXPORT,
             V1_LEGACY_BANNER if compiler_profile == 'gcc48' else V1_MODERN_BANNER, MEASURE_V1]
    return _build(source, output, toolchain, child, V1_FINALS, i386_runtime=i386_runtime, runtime=runtime)


def build_simulator(source, output, *, generation, five=False, runtime=None):
    """Compile the frozen shim, optional shared library and firmware privately."""
    if type(generation) is not int or generation not in (1, 2) or type(five) is not bool or (five and generation != 2):
        raise ValueError('invalid simulator build configuration')
    name = 'smoothie-sim-v2' if generation == 2 else 'smoothie-sim'
    make = (['-f', 'Makefile.v2', 'BOARD=PRIME', 'SOURCE=../pr/Firmware']
            if generation == 2 else ['SMOOTHIE_SRC=../pr'])
    if five:
        make.append('CXX=g++ -DMAX_ROBOT_ACTUATORS=5')
    make.append('../simbuild/' + name)
    exporter = EXPORT_TEMPLATE.replace('__FINAL_LIMITS__', '(50331648,)')
    script = 'exporter=$1; name=$2; shift 2\n' + BUILD_SIMULATOR + 'exec /usr/bin/python3 -I -c "$exporter" "simbuild/$name"\n'
    child = ['/bin/sh', '-c', script, 'gate-simulator-build', exporter, name, *make]
    return _build(source, output, None, child, {name: 48 * 1024**2}, elf_kind='host', runtime=runtime)


def _build(source, output, toolchain, child, finals, *, i386_runtime=None, elf_kind='arm', runtime=None):
    """One bounded capture, validation and collection path for both generations."""
    source, output = (Path(path).absolute() for path in (source, output))
    toolchain = Path(toolchain).absolute() if toolchain is not None else None
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or output.parent.resolve() != output.parent):
        raise ValueError('build output requires a fresh path with a real parent')
    if any(output == path or output in path.parents or path in output.parents for path in (source, toolchain) if path is not None):
        raise ValueError('build output must not overlap source or compiler')
    total_bytes = sum(finals.values())
    if runtime is None:
        command = sandbox.memory_command(source, child, toolchain=toolchain, i386_runtime=i386_runtime)
        captured = sandbox_process.run(command, max_stdout_bytes=total_bytes + sandbox_transport.HEADER_BYTES)
    else:
        if i386_runtime is not None:
            raise ValueError('selected runtime must provide its own legacy compiler libraries')
        captured = runtime.run(source, child, toolchain=toolchain,
                               max_stdout_bytes=total_bytes + sandbox_transport.HEADER_BYTES)
    if captured.problem or captured.returncode:
        return BuildOutput(75 if captured.problem else captured.returncode, captured.stderr,
                           captured.stderr_truncated, captured.problem, {})
    try:
        decoded = sandbox_transport.decode(captured.stdout, finals, max_total_bytes=total_bytes)
        elf = decoded[next(iter(finals))]
        if elf_kind == 'arm':
            valid = (len(elf) >= 52 and elf[:7] == b'\x7fELF\x01\x01\x01' and
                     elf[16:24] == b'\x02\0\x28\0\x01\0\0\0')
        else:
            valid = (len(elf) >= 64 and elf[:7] == b'\x7fELF\x02\x01\x01' and
                     elf[16:18] in (b'\x02\0', b'\x03\0') and elf[18:24] == b'\x3e\0\x01\0\0\0')
        if not valid:
            raise ValueError('build final has an unsupported ELF identity: ' + elf_kind)
    except ValueError as error:
        diagnostic = captured.stderr + b'\nFinal validation: ' + str(error).encode('utf-8') + b'\n'
        limit = 256 * 1024
        return BuildOutput(75, diagnostic[-limit:],
                           captured.stderr_truncated or len(diagnostic) > limit, 'final_validation', {})
    with tempfile.TemporaryDirectory(prefix='gate-final-', dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, data in decoded.items():
            with (staging / name).open('xb') as handle:
                handle.write(data)
        files = build_artifacts.collect(staging, output, finals, max_total_bytes=total_bytes)
    return BuildOutput(0, captured.stderr, captured.stderr_truncated, None, files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--compiler', required=True)
    parser.add_argument('--profile', choices=('v1-lpc1768', 'v2-prime'), default='v2-prime')
    parser.add_argument('--i386-runtime')
    parser.add_argument('--compiler-profile', choices=('gcc48', 'gcc12'), help='V1 compiler identity; defaults to the selected library setup')
    parser.add_argument('--runtime-image', help='use an already provisioned Docker image pinned by digest')
    parser.add_argument('--measure-only', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--no-revision-metadata', action='store_true')
    args = parser.parse_args()
    if args.profile != 'v1-lpc1768' and (args.i386_runtime or args.measure_only or args.compiler_profile):
        parser.error('only V1 supports a private i386 runtime or measurement linker adjustment')
    try:
        runtime = None
        if args.runtime_image:
            from container_runtime import DockerRuntime
            runtime = DockerRuntime(args.runtime_image)
        if args.profile == 'v1-lpc1768':
            result = build_v1(args.tree, args.out, args.compiler, args.i386_runtime,
                              debug=args.debug, jobs=args.jobs, measure_only=args.measure_only,
                              runtime=runtime, compiler_profile=args.compiler_profile)
        else:
            result = build_v2(args.tree, args.out, args.compiler, debug=args.debug, jobs=args.jobs,
                              revision_metadata=not args.no_revision_metadata, runtime=runtime)
    except (OSError, ValueError) as error:
        print(json.dumps({'type': 'error', 'message': str(error)}))
        return 75
    sys.stderr.buffer.write(result.diagnostics)
    print(json.dumps({'type': 'result', 'status': 'pass' if result.returncode == 0 else 'error',
                      'exit_code': result.returncode, 'problem': result.problem,
                      'diagnostics_truncated': result.diagnostics_truncated, 'files': result.files}))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
