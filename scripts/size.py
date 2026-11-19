#!/usr/bin/env python3
"""Compare flash and RAM of base and PR firmware, Release and Debug, and write size.result.json.

Uses smoothie-sim's size checker (set SMOOTHIE_SIM to its folder; default ../smoothie-sim). Sizes are
read with the host binutils, which understand ARM ELF files (--tool-prefix '' selects them). Growth
never fails the gate: it warns above the configurable thresholds so maintainers can decide.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gate_result import make_result, worst_status  # noqa: E402
from mdkit import code_block, esc, html_table  # noqa: E402
from memory_layouts import LPC1768_CAPACITY as CAPACITY, STM32H745_CAPACITY as STM32_CAPACITY

DEFAULT_SIM = os.path.join(os.path.dirname(HERE), "..", "smoothie-sim")


def compare(sim_dir, base_elf, pr_elf, tool_prefix, work, architecture="lpc1769", *,
            isolated=False, compiler=None, runtime=None):
    if isolated or runtime is not None:
        import size_sandbox
        return size_sandbox.compare(sim_dir, base_elf, pr_elf, work, architecture=architecture,
                                    compiler=compiler, runtime=runtime)
    out = os.path.join(work, "size.json")
    command = [sys.executable, "-I", os.path.join(sim_dir, "checkers", "size_check.py"), "--ref-elf", base_elf,
               "--cand-elf", pr_elf, "--tool-prefix", tool_prefix, "--architecture", architecture, "--out", out]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with open(out) as handle:
        return json.load(handle)


ICONS = {"Flash": "💾", "RAM": "🧠", "AHB RAM": "🔌"}


def ram_totals(rows):
    banks = [row for row in rows if row["memory"] != "Flash"]
    return (sum(row["reference"] for row in banks), sum(row["candidate"] for row in banks),
            sum(row["delta"] for row in banks))


def kib(value):
    return f"{value / 1024:g} KiB"


def table(label, data, chart_key, architecture="lpc1769"):
    rows = data["rows"]
    changes = []
    capacities = STM32_CAPACITY if architecture == "stm32h745" else CAPACITY
    for row in rows:
        shown = f"<b>{row['delta']:+d}</b>" if row["delta"] else "0"
        capacity = capacities[row["memory"]]
        base_extent = row["reference_footprint"] if architecture == "stm32h745" else row["reference"]
        candidate_extent = row["candidate_footprint"] if architecture == "stm32h745" else row["candidate"]
        remaining_delta = base_extent - candidate_extent
        changes.append([f"{ICONS.get(row['memory'], '🧠')} {row['memory']}", f"{row['reference']:,}",
                        f"{row['candidate']:,}", shown, f"{capacity - base_extent:,}",
                        f"{capacity - candidate_extent:,}", f"{remaining_delta:+,}" if remaining_delta else "0"])
    if any(row["memory"] != "Flash" for row in rows):
        base, candidate, delta = ram_totals(rows)
        total_capacity = sum(capacities[row["memory"]] for row in rows if row["memory"] != "Flash")
        banks = [row for row in rows if row["memory"] != "Flash"]
        base_extent = sum(row["reference_footprint"] for row in banks) if architecture == "stm32h745" else base
        candidate_extent = sum(row["candidate_footprint"] for row in banks) if architecture == "stm32h745" else candidate
        remaining_delta = base_extent - candidate_extent
        changes.append(["Static RAM total", f"{base:,}", f"{candidate:,}",
                        f"<b>{delta:+d}</b>" if delta else "0", f"{total_capacity - base_extent:,}",
                        f"{total_capacity - candidate_extent:,}", f"{remaining_delta:+,}" if remaining_delta else "0"])
    lines = [f"<b>{esc(label)}</b>", "",
             html_table(["Memory", "Base used (bytes)", "PR used (bytes)", "Used change (bytes)",
                         "Base remaining (bytes)", "PR remaining (bytes)", "Remaining change (bytes)"], changes,
                        aligns=[None, "right", "right", "right", "right", "right", "right"]), "", f"<!--chart:{chart_key}-->"]
    if any(row["candidate"] > capacities[row["memory"]] for row in rows):
        lines += ["", "<sub>This build does not fit the chip (-O0 is larger than the flash); the bar is capped "
                      "at the chip size and the measurement only compares base and PR.</sub>"]
    # "$t" and "$d" are ARM mapping symbols (code/data markers), not program symbols.
    grown = [c for c in data["top_symbol_changes"] if c["delta"] > 0 and not c["symbol"].startswith("$")][:8]
    if grown:
        lines += ["", "📈 Symbols that grew most:", ""] + [f"- <code>{esc(c['symbol'])}</code> {c['delta']:+d} B" for c in grown]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for build_type in ("release", "debug"):
        parser.add_argument(f"--base-{build_type}", required=True, help=f"base {build_type} main.elf")
        parser.add_argument(f"--pr-{build_type}", required=True, help=f"PR {build_type} main.elf")
    parser.add_argument("--architecture", choices=("lpc1769", "stm32h745"), default="lpc1769")
    parser.add_argument("--base-rev", default="unknown", help="base revision to name in the report")
    parser.add_argument("--toolchain", default="gcc48")
    parser.add_argument("--tool-prefix", default="", help="binutils prefix; empty = host binutils")
    parser.add_argument("--warn-flash", type=int, default=64, help="warn above this flash growth in bytes")
    parser.add_argument("--warn-ram", type=int, default=4, help="warn above this RAM growth in bytes (RAM + AHB RAM)")
    parser.add_argument("--sim-dir", default=os.environ.get("SMOOTHIE_SIM", DEFAULT_SIM))
    parser.add_argument('--sandbox', action='store_true', help='isolate ELF parsing; caller must provide outer cgroup limits')
    parser.add_argument('--runtime-image', help='isolate ELF parsing in an already provisioned digest-pinned Docker image')
    parser.add_argument('--compiler', help='optional read-only ARM compiler directory for isolated binutils')
    parser.add_argument("--out", required=True, help="path of size.result.json")
    args = parser.parse_args()
    isolated = bool(args.sandbox or args.runtime_image)
    if args.compiler and not isolated:
        parser.error('--compiler requires --sandbox or --runtime-image')
    if isolated and (args.tool_prefix not in ('', 'arm-none-eabi-') or
                     (args.tool_prefix and not args.compiler)):
        parser.error('isolated binutils use --compiler and a standard ARM prefix, or the runtime native tools')
    try:
        runtime = None
        if args.runtime_image:
            from container_runtime import DockerRuntime
            runtime = DockerRuntime(args.runtime_image)
        return produce_report(args, runtime)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = make_result('size', 'error', 'Flash and RAM', 'size comparison could not be completed',
                             details=code_block(str(error)[-8192:]),
                             metrics={'base_revision': args.base_rev, 'toolchain': args.toolchain,
                                      'architecture': args.architecture})
        return finish(args.out, result)


def produce_report(args, runtime):
    metrics, sections, statuses = {"base_revision": args.base_rev, "toolchain": args.toolchain,
                                   "warn_flash": args.warn_flash, "warn_ram": args.warn_ram, "architecture": args.architecture}, [], []
    summary = []
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(args.out))) as work:
        for build_type, label in (("release", "Release (-O2)"), ("debug", "Debug (-O0)")):
            data = compare(args.sim_dir, getattr(args, f"base_{build_type}"), getattr(args, f"pr_{build_type}"),
                           args.tool_prefix, work, args.architecture,
                           **({'isolated': True, 'compiler': args.compiler, 'runtime': runtime}
                              if args.sandbox or runtime is not None else {}))
            flash = data["rows"][0]["delta"]
            ram_base, ram_pr, ram = ram_totals(data["rows"])
            metrics.update({f"flash_delta_{build_type}": flash, f"ram_delta_{build_type}": ram,
                            f"flash_base_{build_type}": data["rows"][0]["reference"],
                            f"flash_pr_{build_type}": data["rows"][0]["candidate"],
                            f"ram_base_{build_type}": ram_base, f"ram_pr_{build_type}": ram_pr})
            statuses.append("warn" if flash > args.warn_flash or ram > args.warn_ram else "pass")
            summary.append(f"{build_type.capitalize()} flash {flash:+d} B, RAM {ram:+d} B")
            sections.append(table(label, data, f"size-{build_type}", args.architecture))
            grown = [[c["symbol"], c["delta"]] for c in data["top_symbol_changes"]
                     if c["delta"] > 0 and not c["symbol"].startswith("$")][:6]
            chart = {"label": label, "rows": [[r["memory"], r["reference"], r["candidate"]] for r in data["rows"]],
                     "symbols": grown, "architecture": args.architecture}
            if args.architecture == "stm32h745":
                chart["usage_rows"] = [[r["memory"], r["reference_footprint"], r["candidate_footprint"]]
                                       for r in data["rows"]]
            metrics[f"chart_{build_type}"] = json.dumps(chart)
    note = (f"Base revision {args.base_rev}, toolchain {args.toolchain}. Heap and stack are not counted. "
            + ("V2 used bytes sum sections; remaining capacity uses each region's occupied extent, including gaps. "
               if args.architecture == "stm32h745" else "Remaining capacity is chip capacity minus measured static allocation. ")
            + "Remaining capacity is not available heap or runtime headroom. "
            f"Warns above +{args.warn_flash} B flash or +{args.warn_ram} B RAM; growth never fails the gate.")
    result = make_result("size", worst_status(statuses), "Flash and RAM", "; ".join(summary),
                         details="\n\n".join(sections + [note]), metrics=metrics)
    return finish(args.out, result)


def finish(output, result):
    with open(output, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": "size", "status": result["status"], "summary": result["summary"]}))
    return 0 if result['status'] in ('pass', 'warn') else 1


if __name__ == "__main__":
    sys.exit(main())
