#!/usr/bin/env python3
"""Build one Smoothieware tree for the Smoothieboard (LPC1768) on ni-vm and write build.result.json.

The compile runs as a whole ni-build ARM job (`ni-build arm`), never on this machine. The tree is
snapshotted as is, so give it a clean checkout (for example `git archive` output). Output: JSONL on
stdout (progress, result), the ELF under <out>/LPC1768/main.elf, and <out>/build.result.json.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import make_result  # noqa: E402

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


LINKER_SCRIPT = os.path.join("mbed", "src", "vendor", "NXP", "cmsis", "LPC1768", "GCC_ARM", "LPC1768.ld")
FLASH_LINE = "FLASH (rx) : ORIGIN = 16K, LENGTH = (512K - 16K)"
OVERSIZE_FLASH_LINE = "FLASH (rx) : ORIGIN = 16K, LENGTH = (4096K - 16K)"


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


def run_ni_build(tree, out, build_type, toolchain, jobs):
    """Run the remote job; return (exit_code, receipt_path or None, last ni-build error text)."""
    command = ["ni-build", "arm", "--toolchain", toolchain, "--root", tree, "--include", ".", "--cwd", ".",
               "--out", out, "--", "make", f"-j{jobs}", f"BUILD_TYPE={build_type}"]
    receipt, errors = None, []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append(line.rstrip())
            continue
        if event.get("type") == "receipt":
            receipt = event
        elif event.get("type") in ("error", "warning"):
            errors.append(event.get("message", line.rstrip()))
    return process.wait(), receipt, "\n".join(errors[-5:])


def run_make(tree, build_type, jobs):
    """CI runner: plain `make` in the checkout on a GitHub runner (no ni-build there). The ELF stays in the tree."""
    log_path = os.path.join(tree, "gate-make.log")
    with open(log_path, "w") as log:
        code = subprocess.run(["make", f"-j{jobs}", f"BUILD_TYPE={build_type}"], cwd=tree, stdout=log,
                              stderr=subprocess.STDOUT).returncode
    return code, {"stderr": log_path, "stdout": log_path}, ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tree", required=True, help="absolute path of the clean firmware checkout")
    parser.add_argument("--out", required=True, help="existing output folder under /media/arthur/tmp or /media/arthur/build")
    parser.add_argument("--build-type", choices=BUILD_TYPES, required=True)
    parser.add_argument("--label", default="pr", help="name used in the result, for example base or pr")
    parser.add_argument("--toolchain", default="gcc48", choices=("gcc48", "gcc12"),
                        help="gcc48 = the toolchain Smoothieware documents (default)")
    parser.add_argument("--runner", choices=("ni-build", "make"), default="ni-build",
                        help="ni-build = remote job on ni-vm (default); make = local make, for GitHub runners")
    parser.add_argument("--jobs", type=int, default=32)
    parser.add_argument("--measure-only", action="store_true",
                        help="enlarge the FLASH region in the linker script of --tree (edited in place) so an "
                             "oversized image links; the ELF is for size comparison, never for flashing")
    args = parser.parse_args()

    if args.measure_only and not enlarge_flash(args.tree):
        print(json.dumps({"type": "error", "message": "linker script FLASH line not found; cannot enlarge"}))
        return 2
    stage = f"build-{args.label}-{args.build_type.lower()}"
    title = f"Build {args.label} ({args.build_type})"
    if args.runner == "make":
        code, receipt, errors = run_make(args.tree, args.build_type, args.jobs)
        elf = os.path.join(args.tree, "LPC1768", "main.elf")
    else:
        code, receipt, errors = run_ni_build(args.tree, args.out, args.build_type, args.toolchain, args.jobs)
        elf = os.path.join(args.out, "LPC1768", "main.elf")
    if code == 0 and os.path.isfile(elf):
        result = make_result(stage, "pass", title, f"{args.label} compiles in {args.build_type}",
                             metrics={"elf": elf, "toolchain": args.toolchain, "build_type": args.build_type,
                                      "measure_only": str(args.measure_only).lower()})
    elif code == 0:
        result = make_result(stage, "error", title, "build finished but main.elf is missing",
                             details=f"Expected {elf}.")
    else:
        log = failure_excerpt(receipt["stderr"]) if receipt else ""
        log = log or (failure_excerpt(receipt["stdout"]) if receipt else "") or errors
        result = make_result(stage, "fail", title, f"{args.label} does not compile in {args.build_type}",
                             details="Compiler errors, then the last lines of the output:\n\n```\n" + log + "\n```",
                             metrics={"exit_code": code, "toolchain": args.toolchain, "build_type": args.build_type})
    with open(os.path.join(args.out, "build.result.json"), "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": stage, "status": result["status"], "summary": result["summary"]}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
