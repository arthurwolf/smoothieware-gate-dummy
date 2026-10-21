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
import re
import signal
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import make_result  # noqa: E402
from mdkit import code_block, esc  # noqa: E402
import pulse_data  # noqa: E402
import trace_chart  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SIM_DIR = os.environ.get("SMOOTHIE_SIM", os.path.join(HERE, "..", "..", "smoothie-sim"))


def load_step_trace(sim_dir):
    path = os.path.join(sim_dir, "checkers", "step_trace.py")
    spec = importlib.util.spec_from_file_location("step_trace", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def differing_blocks(step_trace, expected, actual):
    """Positions of the blocks whose exact fields differ or whose timing is outside the comparer's tolerance."""
    out = set()
    for position, (e, a) in enumerate(zip(expected["blocks"], actual["blocks"])):
        exact = any(e.get(f) != a.get(f) for f in ("steps", "counts", "dir", "dirs", "changes"))
        timing = any(not step_trace._close(f, e[f], a[f]) for f in ("start", "ticks", "entry", "exit"))
        if exact or timing:
            out.add(position)
    return out


def chart_key(name):
    return "sim-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def signal_name(code):
    """Shell style (128+n) and Python style (-n) exit codes of a killed process -> 'SIGSEGV (segmentation fault)'."""
    number = -code if code < 0 else code - 128
    try:
        name = signal.Signals(number).name
    except ValueError:
        return f"signal {number}"
    return f"{name} ({signal.strsignal(number).lower()})"


def run_case(sim, case_dir, out_dir, timeout, attempts=2, extra=(), tag=""):
    """Run one case; return (result path or None, problem or None). A problem is (kind, text), kind being
    "crash" (the simulator died on a signal: the change under test crashes the firmware code), "timeout" or "error".
    A crash or timeout is retried once so a one-off hiccup of the runner is not blamed on the pull request."""
    sim, case_dir, out_dir = os.path.abspath(sim), os.path.abspath(case_dir), os.path.abspath(out_dir)  # the run changes directory
    with open(os.path.join(case_dir, "meta.json")) as handle:
        sim_args = json.load(handle).get("sim_args", [])
    result_path = os.path.join(out_dir, os.path.basename(case_dir) + tag + ".json")
    command = [sim, "--config", os.path.join(case_dir, "config.txt"), "--gcode", os.path.join(case_dir, "input.gcode"),
               "--out", result_path] + sim_args + list(extra)
    problem = None
    for _ in range(attempts):
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                       cwd=out_dir)  # own cwd, no stdin: cases cannot read or write each other's files
        except subprocess.TimeoutExpired:
            problem = ("timeout", f"the simulator did not finish within {timeout} s")
            continue
        if completed.returncode == 0:
            return result_path, None
        tail = completed.stderr.strip()[-600:]
        if completed.returncode < 0 or completed.returncode > 128:
            problem = ("crash", f"the simulator crashed with {signal_name(completed.returncode)}" + (f"\n{tail}" if tail else ""))
            continue
        return None, ("error", f"the simulator exited with code {completed.returncode}" + (f"\n{tail}" if tail else ""))
    return None, problem


def pulse_view(args, step_trace, case_dir, name, expected):
    """(pulse data, None) or (None, reason). The reference pulses come from the base firmware's own simulator run, and
    only when that run still reproduces expected.trace: a chart must never label something "reference" that is not."""
    if not args.base_sim:
        return None, "no simulator of the base firmware was provided"
    if not os.access(args.base_sim, os.X_OK):
        return None, "the simulator of the base firmware could not be built"
    base_dir = os.path.join(args.out, "base")
    os.makedirs(base_dir, exist_ok=True)
    base_path, base_error = run_case(args.base_sim, case_dir, base_dir, args.timeout, attempts=1, extra=["--full-steps"])
    pr_path, pr_error = run_case(args.sim, case_dir, args.out, args.timeout, attempts=1, extra=["--full-steps"], tag=".full")
    if not base_path or not pr_path:
        return None, "the base or the pull request simulator failed to rerun the case with full step lists"
    try:
        with open(base_path) as handle:
            base = json.load(handle)
        with open(pr_path) as handle:
            pr = json.load(handle)
        if step_trace.compare(expected, step_trace.record(base))["status"] != "pass":
            return None, "the base firmware does not reproduce expected.trace itself, so it cannot stand for the reference"
        return pulse_data.build(base, pr)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, f"the step lists could not be read ({type(error).__name__})"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", required=True, help="smoothie-sim binary built from the firmware under test")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", required=True, help="existing folder for this run's traces and the result")
    parser.add_argument("--sim-dir", default=DEFAULT_SIM_DIR, help="smoothie-sim checkout (for checkers/step_trace.py)")
    parser.add_argument("--base-sim", help="smoothie-sim binary built from the base firmware; its pulses draw the reference side of the charts")
    parser.add_argument("--record", action="store_true", help="write expected.trace instead of comparing")
    parser.add_argument("--only", action="append", default=[], help="run only this case (repeatable)")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    if not (os.path.isfile(args.sim) and os.access(args.sim, os.X_OK)):
        print(json.dumps({"type": "error", "message": f"simulator binary missing or not executable: {args.sim}"}))
        result = make_result("sim", "error", "Simulator step traces", "the simulator binary was not built",
                             details="The build of the simulator produced no executable, so no case could run.")
        with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
            json.dump(result, handle, indent=1)
        return 1
    step_trace = load_step_trace(args.sim_dir)
    names = sorted(n for n in os.listdir(args.cases) if os.path.isfile(os.path.join(args.cases, n, "config.txt")))
    names = [n for n in names if not args.only or n in args.only]
    failed, crashed, errored, passed, flaky = [], [], [], [], []
    lines, crash_notes, case_lines, samples = [], [], {}, {}
    for name in names:
        case_dir = os.path.join(args.cases, name)
        result_path, error = run_case(args.sim, case_dir, args.out, args.timeout)
        if error:
            kind, text = error
            first, _, rest = text.partition("\n")
            if kind == "crash":
                crashed.append(name)
                crash_notes.append(f"💥 <code>{esc(name)}</code>: {esc(first)}" + ("\n\n" + code_block(rest) if rest else ""))
            else:
                errored.append(name)
                lines.append(f"- 🛑 `{name}`: {first}")
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
            lines.append(f"- 🛑 `{name}`: no expected.trace (record one and review it)")
            continue
        with open(expected_path) as handle:
            expected = handle.read()
        outcome = step_trace.compare(expected, trace)
        if outcome["status"] == "pass":
            passed.append(name)
            continue
        # A mismatch is confirmed by a second run. The simulator is deterministic, so a run that differs from the
        # first one is a flaky check, never evidence against the pull request.
        again_path, again_error = run_case(args.sim, case_dir, args.out, args.timeout, attempts=1)
        if again_path:
            with open(again_path) as handle:
                trace_again = step_trace.record(json.load(handle))
            if trace_again != trace:
                if step_trace.compare(expected, trace_again)["status"] == "pass":
                    flaky.append(name)
                    passed.append(name)
                else:
                    errored.append(name)
                    lines.append(f"- 🛑 `{name}`: two runs of the same input gave different steps (non-deterministic), "
                                 "so the case proves nothing about the pull request")
                continue
        failed.append(name)
        shown = (outcome["exact"] + outcome["timing"])[:3]
        more = len(outcome["exact"]) + len(outcome["timing"]) - len(shown)
        lines.append(f"- ❌ `{name}`: " + "; ".join(shown) + (f"; and {more} more" if more > 0 else ""))
        case_lines[name] = "; ".join(shown) + (f"; and {more} more" if more > 0 else "")
        pulses, reason = pulse_view(args, step_trace, case_dir, name, expected)
        sample = trace_chart.sample_case(step_trace.parse(expected), step_trace.parse(trace),
                                         differing_blocks(step_trace, step_trace.parse(expected), step_trace.parse(trace)),
                                         pulses, reason)
        if sample:
            samples[name] = sample
        print(json.dumps({"type": "warning", "case": name, "exact": len(outcome["exact"]), "timing": len(outcome["timing"])}))

    total = len(names)
    metrics = {"cases": total, "passed": len(passed), "failed": len(failed), "crashed": len(crashed),
               "errors": len(errored), "flaky": len(flaky)}
    if samples:
        with open(os.path.join(args.out, "trace-samples.json"), "w") as handle:
            json.dump({chart_key(n): {"name": n, "sample": v} for n, v in samples.items()}, handle, separators=(",", ":"))
    title = "Simulator step traces"
    parts = []
    if crash_notes:
        parts.append("The simulator, built from this pull request, died on these inputs. A crash on a plain G-code "
                     "file is a bug in the firmware code of the pull request (the same case runs fine on the base).\n\n"
                     + "\n\n".join(crash_notes))
    if failed:
        blocks = []
        for name in failed:
            summary = f"❌ <code>{esc(name)}</code>: {esc(case_lines[name][:140])}"
            blocks.append(f"<details><summary>{summary}</summary>\n\n<!--chart:{chart_key(name)}-->\n\n"
                          f"{esc(case_lines[name])}\n\n</details>")
        parts.append("Cases whose steps differ from expected.trace (open one to see the reference and the produced "
                     "blocks side by side):\n\n" + "\n\n".join(blocks))
    if errored:
        parts.append("Cases that could not run (a problem of the check, not necessarily of the pull request):\n\n"
                     + "\n".join(l for l in lines if l.startswith("- 🛑")))
    details = "\n\n".join(parts)
    if flaky:
        parts.append("🎲 Cases that differed once and matched on the second run (a flaky run, not blamed on the pull "
                     "request): " + ", ".join(f"<code>{esc(n)}</code>" for n in flaky))
        details = "\n\n".join(parts)
    if args.record:
        result = make_result("sim", "pass", title, f"recorded {len(passed)} of {total} reference traces", metrics=metrics,
                             details=details)
    elif crashed:
        result = make_result("sim", "fail", title, f"{len(crashed)} of {total} cases crash the simulator"
                             + (f", {len(failed)} emit different steps" if failed else ""), details=details, metrics=metrics)
    elif failed:
        result = make_result("sim", "fail", title, f"{len(failed)} of {total} cases emit different steps",
                             details=details, metrics=metrics)
    elif errored:
        result = make_result("sim", "error", title, f"{len(errored)} of {total} cases could not run",
                             details=details, metrics=metrics)
    elif flaky:
        result = make_result("sim", "warn", title, f"all {total} cases pass, {len(flaky)} only on the second run",
                             details=details, metrics=metrics)
    else:
        result = make_result("sim", "pass", title, f"all {total} cases emit the reference steps", metrics=metrics)
    with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": "sim", "status": result["status"], "summary": result["summary"]}))
    return 0 if result["status"] in ("pass", "warn") else 1


if __name__ == "__main__":
    sys.exit(main())
