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
from mdkit import esc, html_table  # noqa: E402

DEFAULT_SIM = os.path.join(os.path.dirname(HERE), "..", "smoothie-sim")


def compare(sim_dir, base_elf, pr_elf, tool_prefix, work):
    out = os.path.join(work, "size.json")
    command = [sys.executable, "-I", os.path.join(sim_dir, "checkers", "size_check.py"), "--ref-elf", base_elf,
               "--cand-elf", pr_elf, "--tool-prefix", tool_prefix, "--out", out]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with open(out) as handle:
        return json.load(handle)


CAPACITY = {"Flash": 512 * 1024, "RAM": 32 * 1024, "AHB RAM": 32 * 1024}  # LPC1768
ICONS = {"Flash": "💾", "RAM": "🧠", "AHB RAM": "🔌"}


def kib(value):
    return f"{value / 1024:g} KiB"


def table(label, data, chart_key):
    rows = data["rows"]
    changes = []
    for row in rows:
        shown = f"<b>{row['delta']:+d}</b>" if row["delta"] else "0"
        changes.append([f"{ICONS[row['memory']]} {row['memory']}", f"{row['reference']:,}", f"{row['candidate']:,}", shown])
    lines = [f"<b>{esc(label)}</b>", "",
             html_table(["Memory", "Base (bytes)", "PR (bytes)", "Change (bytes)"], changes,
                        aligns=[None, "right", "right", "right"]), "", f"<!--chart:{chart_key}-->"]
    if any(row["candidate"] > CAPACITY[row["memory"]] for row in rows):
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
    parser.add_argument("--base-rev", default="unknown", help="base revision to name in the report")
    parser.add_argument("--toolchain", default="gcc48")
    parser.add_argument("--tool-prefix", default="", help="binutils prefix; empty = host binutils")
    parser.add_argument("--warn-flash", type=int, default=64, help="warn above this flash growth in bytes")
    parser.add_argument("--warn-ram", type=int, default=4, help="warn above this RAM growth in bytes (RAM + AHB RAM)")
    parser.add_argument("--sim-dir", default=os.environ.get("SMOOTHIE_SIM", DEFAULT_SIM))
    parser.add_argument("--out", required=True, help="path of size.result.json")
    args = parser.parse_args()

    metrics, sections, statuses = {"base_revision": args.base_rev, "toolchain": args.toolchain,
                                   "warn_flash": args.warn_flash, "warn_ram": args.warn_ram}, [], []
    summary = []
    with tempfile.TemporaryDirectory() as work:
        for build_type, label in (("release", "Release (-O2)"), ("debug", "Debug (-O0)")):
            data = compare(args.sim_dir, getattr(args, f"base_{build_type}"), getattr(args, f"pr_{build_type}"),
                           args.tool_prefix, work)
            flash = data["rows"][0]["delta"]
            ram = data["rows"][1]["delta"] + data["rows"][2]["delta"]
            metrics.update({f"flash_delta_{build_type}": flash, f"ram_delta_{build_type}": ram,
                            f"flash_base_{build_type}": data["rows"][0]["reference"],
                            f"flash_pr_{build_type}": data["rows"][0]["candidate"]})
            statuses.append("warn" if flash > args.warn_flash or ram > args.warn_ram else "pass")
            summary.append(f"{build_type.capitalize()} flash {flash:+d} B, RAM {ram:+d} B")
            sections.append(table(label, data, f"size-{build_type}"))
            grown = [[c["symbol"], c["delta"]] for c in data["top_symbol_changes"]
                     if c["delta"] > 0 and not c["symbol"].startswith("$")][:6]
            chart = {"label": label, "rows": [[r["memory"], r["reference"], r["candidate"]] for r in data["rows"]],
                     "symbols": grown}
            metrics[f"chart_{build_type}"] = json.dumps(chart)
    note = (f"Base revision {args.base_rev}, toolchain {args.toolchain}. Heap and stack are not counted. "
            f"Warns above +{args.warn_flash} B flash or +{args.warn_ram} B RAM; growth never fails the gate.")
    result = make_result("size", worst_status(statuses), "Flash and RAM", "; ".join(summary),
                         details="\n\n".join(sections + [note]), metrics=metrics)
    with open(args.out, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": "size", "status": result["status"], "summary": result["summary"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
