#!/usr/bin/env python3
"""Run every cases/<name>/ through a smoothie-sim binary and compare the step trace with expected.trace.

  sim_stage.py --sim BUILD/smoothie-sim --cases cases --out OUT            compare; writes OUT/sim.result.json
  sim_stage.py --sim BUILD/smoothie-sim --cases cases --out OUT --record   (re)write each expected.trace from this run

A case folder holds config.txt, input.gcode, expected.trace and meta.json (sim_args = extra simulator options).
The recorder and comparer live in smoothie-sim (checkers/step_trace.py, found through --sim-dir or $SMOOTHIE_SIM).
Output: JSONL on stdout, OUT/<case>.trace (this run) and OUT/sim.result.json.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import make_result  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SIM_DIR = os.environ.get("SMOOTHIE_SIM", os.path.join(HERE, "..", "..", "smoothie-sim"))


def load_step_trace(sim_dir):
    path = os.path.join(sim_dir, "checkers", "step_trace.py")
    spec = importlib.util.spec_from_file_location("step_trace", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_case(sim, case_dir, out_dir, timeout):
    """Run one case; return (trace_text or None, error text or None)."""
    with open(os.path.join(case_dir, "meta.json")) as handle:
        sim_args = json.load(handle).get("sim_args", [])
    result_path = os.path.join(out_dir, os.path.basename(case_dir) + ".json")
    command = [sim, "--config", os.path.join(case_dir, "config.txt"), "--gcode", os.path.join(case_dir, "input.gcode"),
               "--out", result_path] + sim_args
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"simulator timed out after {timeout} s"
    if completed.returncode != 0:
        return None, f"simulator exited {completed.returncode}: {completed.stderr.strip()[-300:]}"
    with open(result_path) as handle:
        return result_path, None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", required=True, help="smoothie-sim binary built from the firmware under test")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", required=True, help="existing folder for this run's traces and the result")
    parser.add_argument("--sim-dir", default=DEFAULT_SIM_DIR, help="smoothie-sim checkout (for checkers/step_trace.py)")
    parser.add_argument("--record", action="store_true", help="write expected.trace instead of comparing")
    parser.add_argument("--only", action="append", default=[], help="run only this case (repeatable)")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    step_trace = load_step_trace(args.sim_dir)
    names = sorted(n for n in os.listdir(args.cases) if os.path.isfile(os.path.join(args.cases, n, "config.txt")))
    names = [n for n in names if not args.only or n in args.only]
    failed, errored, passed = [], [], []
    lines = []
    for name in names:
        case_dir = os.path.join(args.cases, name)
        result_path, error = run_case(args.sim, case_dir, args.out, args.timeout)
        if error:
            errored.append(name)
            lines.append(f"- `{name}`: {error}")
            continue
        with open(result_path) as handle:
            trace = step_trace.record(json.load(handle))
        with open(os.path.join(args.out, name + ".trace"), "w") as handle:
            handle.write(trace)
        expected_path = os.path.join(case_dir, "expected.trace")
        if args.record:
            with open(expected_path, "w") as handle:
                handle.write(trace)
            passed.append(name)
            continue
        if not os.path.isfile(expected_path):
            errored.append(name)
            lines.append(f"- `{name}`: no expected.trace (record one and review it)")
            continue
        with open(expected_path) as handle:
            outcome = step_trace.compare(handle.read(), trace)
        if outcome["status"] == "pass":
            passed.append(name)
            continue
        failed.append(name)
        shown = (outcome["exact"] + outcome["timing"])[:3]
        more = len(outcome["exact"]) + len(outcome["timing"]) - len(shown)
        lines.append(f"- `{name}`: " + "; ".join(shown) + (f"; and {more} more" if more > 0 else ""))
        print(json.dumps({"type": "warning", "case": name, "exact": len(outcome["exact"]), "timing": len(outcome["timing"])}))

    total = len(names)
    metrics = {"cases": total, "passed": len(passed), "failed": len(failed), "errors": len(errored)}
    title = "Simulator step traces"
    if args.record:
        result = make_result("sim", "pass", title, f"recorded {len(passed)} of {total} reference traces", metrics=metrics,
                             details="\n".join(lines))
    elif failed:
        result = make_result("sim", "fail", title, f"{len(failed)} of {total} cases emit different steps",
                             details="Cases whose steps differ from expected.trace:\n\n" + "\n".join(lines), metrics=metrics)
    elif errored:
        result = make_result("sim", "error", title, f"{len(errored)} of {total} cases could not run",
                             details="\n".join(lines), metrics=metrics)
    else:
        result = make_result("sim", "pass", title, f"all {total} cases emit the reference steps", metrics=metrics)
    with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": "sim", "status": result["status"], "summary": result["summary"]}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
