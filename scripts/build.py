#!/usr/bin/env python3
"""Build one Smoothieware tree for the Smoothieboard (LPC1768) on ni-vm and write build.result.json.

The compile runs as a whole ni-build ARM job (`ni-build arm`), never on this machine. The tree is
snapshotted as is, so give it a clean checkout (for example `git archive` output). Output: JSONL on
stdout (progress, result), the ELF under <out>/LPC1768/main.elf, and <out>/build.result.json.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import make_result  # noqa: E402
from mdkit import code_block  # noqa: E402
from profiles import PROFILES, select_profile  # noqa: E402
import v2_build_inputs  # noqa: E402
import sandbox_dispatch  # noqa: E402
import sandbox_build  # noqa: E402
import build_artifacts  # noqa: E402

BUILD_TYPES = ("Release", "Debug")  # Release = -O2, Debug = -O0 (src/makefile BUILD_TYPE)


def failure_excerpt(path, error_lines=15, tail_lines=15):
    """Compiler errors first (a parallel make buries them under 'Compiling' lines), then the last lines."""
    try:
        with open(path, errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ""
    markers = (" error", "Error ", "***", "undefined reference", "multiple definition", "will not fit", "overflowed")
    errors = [line for line in lines if any(marker in line for marker in markers)][:error_lines]
    tail = lines[-tail_lines:]
    return "\n".join(errors + (["..."] if errors else []) + tail)


LINKER_SCRIPT = sandbox_build.V1_LINKER_SCRIPT
FLASH_LINE = sandbox_build.V1_FLASH_LINE
OVERSIZE_FLASH_LINE = sandbox_build.V1_OVERSIZE_FLASH_LINE


def enlarge_flash(tree):
    """Measurement builds only: grow the linker's FLASH region so an oversized -O0 image still links.

    The unoptimized firmware does not fit the LPC1768's 512 KiB; sizes are still comparable.
    Returns False when the linker script does not have the expected line.
    """
    path = os.path.join(tree, LINKER_SCRIPT)
    with open(path) as handle:
        text = handle.read()
    if FLASH_LINE not in text:
        return False
    with open(path, "w") as handle:
        handle.write(text.replace(FLASH_LINE, OVERSIZE_FLASH_LINE, 1))
    return True


def run_ni_build(tree, out, build_type, toolchain, jobs, *, measure_only=False):
    """Build V1 through the same isolated worker and trusted collector as V2."""
    return run_sandbox_build(tree, out, build_type, toolchain, jobs, 'v1-lpc1768',
                             measure_only=measure_only)


def run_v2_build(tree, out, build_type, toolchain, jobs, runner, revision_metadata=False):
    """Build one isolated V2 Prime tree; the remote dispatcher owns compilation."""
    v2_build_inputs.normalize_paths(tree)
    if runner == "make":
        log_path = os.path.join(out, "gate-rake.log")
        env = dict(os.environ, target="Prime", debug="1" if build_type == "Debug" else "0")
        if revision_metadata:
            env["GIT_DIR"] = os.path.join(tree, ".gate-git")
        env["ARMTOOLS"] = os.path.dirname(shutil.which("arm-none-eabi-gcc") or "/usr/bin/arm-none-eabi-gcc")
        with open(log_path, "w") as log:
            code = subprocess.run(["rake", "-m", "-j", str(min(jobs, 16))], cwd=os.path.join(tree, "Firmware"),
                                  env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        return code, {"stdout": log_path, "stderr": log_path}, ""
    return run_sandbox_build(tree, out, build_type, toolchain, jobs, 'v2-prime',
                             revision_metadata=revision_metadata)


def run_sandbox_build(tree, out, build_type, toolchain, jobs, profile, *,
                      revision_metadata=False, measure_only=False):
    """Dispatch trusted controller inputs separately and publish exact finals."""
    command = sandbox_dispatch.command(tree, out, build_type, toolchain, min(jobs, 16), revision_metadata,
                                       profile=profile, measure_only=measure_only)
    receipt, errors = None, []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append(line.rstrip())
            continue
        if event.get("type") == "progress":
            print(json.dumps(event), flush=True)
        if event.get("type") == "receipt":
            receipt = event
        elif event.get("type") in ("error", "warning") or (event.get("type") == "result" and event.get("status") == "error"):
            errors.append(event.get("message", line.rstrip()))
    code = process.wait()
    if code == 0:
        destination = os.path.join(out, os.path.dirname(select_profile(profile).elf))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        finals = sandbox_build.V1_FINALS if profile == 'v1-lpc1768' else sandbox_build.FINALS
        build_artifacts.collect(os.path.join(out, sandbox_dispatch.FINALS), destination,
                                finals, max_total_bytes=sum(finals.values()))
    return (75 if code and receipt is None else code), receipt, "\n".join(errors[-5:])


def run_make(tree, build_type, jobs):
    """CI runner: plain `make` in the checkout on a GitHub runner (no ni-build there). The ELF stays in the tree."""
    log_path = os.path.join(tree, "gate-make.log")
    with open(log_path, "w") as log:
        code = subprocess.run(["make", f"-j{jobs}", f"BUILD_TYPE={build_type}"], cwd=tree, stdout=log,
                              stderr=subprocess.STDOUT).returncode
    return code, {"stderr": log_path, "stdout": log_path}, ""


def run_container_build(tree, out, build_type, toolchain, jobs, profile, compiler, image, *,
                        revision_metadata=False, measure_only=False):
    """CI adapter: change execution only, retaining the common trusted worker."""
    from container_runtime import DockerRuntime
    runtime = DockerRuntime(image)
    selected = select_profile(profile)
    destination = os.path.join(out, os.path.dirname(selected.elf))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    options = {'debug': build_type == 'Debug', 'jobs': min(jobs, sandbox_dispatch.BUILD_JOBS),
               'runtime': runtime}
    if selected.generation == 2:
        v2_build_inputs.normalize_paths(tree)
        result = sandbox_build.build_v2(tree, destination, compiler,
                                        revision_metadata=revision_metadata, **options)
    else:
        result = sandbox_build.build_v1(tree, destination, compiler,
                                        measure_only=measure_only, compiler_profile=toolchain, **options)
    log_path = os.path.join(out, 'gate-container.log')
    with open(log_path, 'xb') as handle:
        handle.write(result.diagnostics)
    return result.returncode, {'stdout': log_path, 'stderr': log_path}, result.problem or ''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=PROFILES, default="v1-lpc1768")
    parser.add_argument("--tree", required=True, help="absolute path of the clean firmware checkout")
    parser.add_argument("--out", required=True, help="existing output folder under /media/arthur/tmp or /media/arthur/build")
    parser.add_argument("--build-type", choices=BUILD_TYPES, required=True)
    parser.add_argument("--label", default="pr", help="name used in the result, for example base or pr")
    parser.add_argument("--toolchain", default="gcc48", choices=("gcc48", "gcc12", "gcc10"),
                        help="gcc48 = the toolchain Smoothieware documents (default)")
    parser.add_argument("--runner", choices=("ni-build", "make", "docker"), default="ni-build",
                        help="ni-build = remote job (default); docker = isolated CI worker; make = legacy direct CI")
    parser.add_argument('--compiler', help='verified compiler directory for the Docker CI runner')
    parser.add_argument('--runtime-image', help='already provisioned digest-pinned image for the Docker CI runner')
    parser.add_argument("--jobs", type=int, default=32)
    parser.add_argument("--measure-only", action="store_true",
                        help="enlarge the FLASH region in the linker script of --tree (edited in place) so an "
                             "oversized image links; the ELF is for size comparison, never for flashing")
    parser.add_argument("--revision-metadata", action="store_true", help="use the gate's isolated V2 Git version metadata")
    args = parser.parse_args()
    if args.runner == 'docker' and not (args.compiler and args.runtime_image):
        parser.error('Docker CI builds require --compiler and --runtime-image')
    if args.runner != 'docker' and (args.compiler or args.runtime_image):
        parser.error('--compiler and --runtime-image require --runner docker')

    profile = select_profile(args.profile)
    if profile.generation == 2 and (args.toolchain != "gcc10" or args.measure_only):
        parser.error("V2 Prime requires gcc10 and does not support the V1 measurement linker patch")
    if profile.generation == 1 and args.toolchain == "gcc10":
        parser.error("gcc10 is not a V1 toolchain")
    if args.measure_only and args.runner == "make" and not enlarge_flash(args.tree):
        print(json.dumps({"type": "error", "message": "linker script FLASH line not found; cannot enlarge"}))
        return 2
    stage = f"build-{args.label}-{args.build_type.lower()}"
    title = f"Build {args.label} ({args.build_type})"
    if args.runner == 'docker':
        try:
            code, receipt, errors = run_container_build(args.tree, args.out, args.build_type, args.toolchain,
                                                       args.jobs, args.profile, args.compiler, args.runtime_image,
                                                       revision_metadata=args.revision_metadata,
                                                       measure_only=args.measure_only)
        except (ValueError, OSError) as error:
            code, receipt, errors = 75, None, 'Container build preparation or execution failed: ' + str(error)
        elf = os.path.join(args.out, profile.elf)
    elif profile.generation == 2:
        try:
            code, receipt, errors = run_v2_build(args.tree, args.out, args.build_type, args.toolchain, args.jobs, args.runner, args.revision_metadata)
        except (ValueError, OSError) as error:
            code, receipt, errors = 75, None, 'V2 build preparation or dispatch failed: ' + str(error)
        elf = os.path.join(args.tree if args.runner == "make" else args.out, profile.elf)
    elif args.runner == "make":
        code, receipt, errors = run_make(args.tree, args.build_type, args.jobs)
        elf = os.path.join(args.tree, "LPC1768", "main.elf")
    else:
        try:
            code, receipt, errors = run_ni_build(args.tree, args.out, args.build_type, args.toolchain, args.jobs,
                                                measure_only=args.measure_only)
        except (ValueError, OSError) as error:
            code, receipt, errors = 75, None, 'V1 build preparation or dispatch failed: ' + str(error)
        elf = os.path.join(args.out, "LPC1768", "main.elf")
    binary = os.path.splitext(elf)[0] + ".bin" if profile.generation == 2 else None
    missing = [path for path in (elf, binary) if path and not os.path.isfile(path)]
    if code == 0 and not missing:
        result = make_result(stage, "pass", title, f"{args.label} compiles in {args.build_type}",
                             metrics={"elf": elf, "toolchain": args.toolchain, "build_type": args.build_type,
                                      "measure_only": str(args.measure_only).lower()})
    elif code == 0:
        result = make_result(stage, "error", title, "build finished but required output is missing",
                             details="Expected: " + ", ".join(missing) + ".")
    elif code == 75:
        result = make_result(stage, "error", title, "build infrastructure failed", details=errors or "Dispatcher failed before producing a build result.")
    else:
        log = failure_excerpt(receipt["stderr"]) if receipt else ""
        log = log or (failure_excerpt(receipt["stdout"]) if receipt else "") or errors
        result = make_result(stage, "fail", title, f"{args.label} does not compile in {args.build_type}",
                             details="🔨 The compiler errors, then the last lines of the build output:\n\n" + code_block(log),
                             metrics={"exit_code": code, "toolchain": args.toolchain, "build_type": args.build_type})
    if profile.generation == 2:
        result.setdefault("metrics", {})["source_path_policy"] = v2_build_inputs.PATH_POLICY
    if args.runner in ('ni-build', 'docker'):
        result.setdefault('metrics', {})['build_jobs'] = min(args.jobs, sandbox_dispatch.BUILD_JOBS)
    with open(os.path.join(args.out, "build.result.json"), "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": stage, "status": result["status"], "summary": result["summary"]}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
