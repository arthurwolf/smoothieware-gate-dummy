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
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import make_result  # noqa: E402
from mdkit import code_block, esc  # noqa: E402
import sandbox_replay  # noqa: E402
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


def load_planner_checker(sim_dir):
    path = os.path.join(sim_dir, "checkers", "gcode_to_blocks.py")
    spec = importlib.util.spec_from_file_location("gate_gcode_to_blocks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def planner_advice(checker, simulator_result):
    """Reuse the simulator's per-block invariant check without judging the base revision."""
    class Collector:
        def __init__(self):
            self.checks = []

        def expect(self, check, ok, detail_ok="", detail_bad="", warn_only=False):
            self.checks.append((check, ok, detail_bad))

    result = Collector()
    checker.check_invariants(result, simulator_result, None, False)
    for check, ok, detail in result.checks:
        if check == "per-block planner invariants (rates, limits, trapezoid)":
            return "" if ok else detail
    raise ValueError("simulator planner checker did not return its per-block result")


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


def reference_reviewed(case_dir):
    try:
        with open(os.path.join(case_dir, "meta.json")) as handle:
            meta = json.load(handle)
        return (meta.get("reference_status") == "reviewed" and
                isinstance(meta.get("approved_by"), str) and bool(meta["approved_by"].strip()) and
                isinstance(meta.get("review"), str) and bool(meta["review"].strip()))
    except (OSError, ValueError, AttributeError):
        return False


def serial_observation(case_dir, result):
    """Return message mismatches; reject malformed requirements or missing serial output."""
    with open(os.path.join(case_dir, "meta.json")) as handle:
        meta = json.load(handle)
    if not isinstance(meta, dict) or not isinstance(result, dict):
        raise ValueError("case metadata and simulator output must be objects")
    expected = meta.get("expected_serial_contains", [])
    forbidden = meta.get("forbidden_serial_contains", [])
    for key, values in (("expected_serial_contains", expected), ("forbidden_serial_contains", forbidden)):
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise ValueError(f"{key} must be a list of nonempty strings")
    if not expected and not forbidden:
        return []
    serial = result.get("serial_out")
    if not isinstance(serial, str):
        raise ValueError("simulator did not provide serial output")
    return ([f"expected serial message missing: {item}" for item in expected if item not in serial] +
            [f"unexpected serial message present: {item}" for item in forbidden if item in serial])


def simulators_for_case(case_dir, head, base, head_five, base_five):
    with open(os.path.join(case_dir, "meta.json")) as handle:
        meta = json.load(handle)
    if not isinstance(meta, dict):
        raise ValueError("case metadata must be an object")
    variant = meta.get("simulator_variant", "default")
    if variant == "default":
        return head, base
    if variant == "five-actuators":
        if not head_five:
            raise ValueError("five-actuator simulator was not built")
        return head_five, base_five
    raise ValueError("unknown simulator variant")


def coverage_problems(inventory, case_names, case_root=None, baseline_names=None):
    """An incomplete scenario inventory cannot be presented as complete coverage."""
    if not isinstance(inventory, dict) or not isinstance(inventory.get("required_scenarios"), list) or not isinstance(inventory.get("scenarios"), dict):
        return ["V2 coverage inventory is malformed"]
    required = inventory["required_scenarios"]
    if not required or any(not isinstance(name, str) or not name for name in required) or len(set(required)) != len(required):
        return ["V2 coverage inventory has invalid required scenarios"]
    problems = []
    if baseline_names is not None:
        for name in sorted(set(baseline_names) - set(required)):
            problems.append(f"{name}: baseline scenario is absent from the inventory")
    for name in required:
        item = inventory["scenarios"].get(name)
        if not isinstance(item, dict):
            problems.append(f"{name}: no disposition")
        elif item.get("status") == "covered":
            case = item.get("case")
            if case not in case_names:
                problems.append(f"{name}: reviewed case is missing")
            elif case_root and not reference_reviewed(os.path.join(case_root, case)):
                problems.append(f"{name}: reference review is incomplete")
        elif item.get("status") == "excluded":
            if any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("reason", "review")):
                problems.append(f"{name}: exclusion needs a reason and review")
        else:
            problems.append(f"{name}: coverage is pending")
    return problems


def run_case(sim, case_dir, out_dir, timeout, attempts=2, extra=(), tag="", *, isolated=False, runtime=None):
    """Run one case; return (result path or None, problem or None). A problem is (kind, text), kind being
    "crash" (the simulator died on a signal: the change under test crashes the firmware code), "timeout" or "error".
    A crash or timeout is retried once so a one-off hiccup of the runner is not blamed on the pull request."""
    sim, case_dir, out_dir = os.path.abspath(sim), os.path.abspath(case_dir), os.path.abspath(out_dir)  # the run changes directory
    try:
        with open(os.path.join(case_dir, "meta.json")) as handle:
            metadata = json.load(handle)
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        sim_args = metadata.get("sim_args", [])
        if not isinstance(sim_args, list):
            raise ValueError("sim_args must be a list")
        sandbox_replay.validate_arguments(sim_args + list(extra))
    except (OSError, ValueError, TypeError) as error:
        return None, ("error", "invalid simulator metadata: " + str(error))
    problem = None
    for _ in range(attempts):
        result_path = os.path.join(out_dir, os.path.basename(case_dir) + tag + "." + uuid.uuid4().hex + ".json")
        command = [sim, "--config", os.path.join(case_dir, "config.txt"), "--gcode", os.path.join(case_dir, "input.gcode"),
                   "--out", result_path] + sim_args + list(extra)
        try:
            if isolated or runtime is not None:
                runtime_options = {'runtime': runtime} if runtime is not None else {}
                captured = sandbox_replay.run(sim, os.path.join(case_dir, "config.txt"),
                                              os.path.join(case_dir, "input.gcode"), sim_args + list(extra),
                                              timeout=timeout, scratch_parent=out_dir, **runtime_options)
                if captured.problem == 'timeout':
                    problem = ("timeout", f"the simulator did not finish within {timeout} s")
                    continue
                if captured.problem:
                    tail = captured.stderr[-4096:].decode('utf-8', errors='replace')
                    return None, ("error", "isolated simulator capture failed: " + captured.problem
                                  + ("\n" + tail if tail else ""))
                completed = subprocess.CompletedProcess(command, captured.returncode, '',
                                                        captured.stderr.decode('utf-8', errors='replace'))
                if captured.returncode == 0:
                    with open(result_path, 'xb') as handle:
                        handle.write(captured.stdout)
            else:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                           cwd=out_dir)  # own cwd, no stdin for the direct compatibility route
        except subprocess.TimeoutExpired:
            problem = ("timeout", f"the simulator did not finish within {timeout} s")
            continue
        except (OSError, ValueError) as error:
            return None, ("error", f"the simulator could not start: {error}")
        if completed.returncode == 0:
            if os.path.isfile(result_path):
                return result_path, None
            return None, ("error", "the simulator returned success without producing a result")
        stdout = completed.stdout if isinstance(completed.stdout, str) else ""
        stderr = completed.stderr if isinstance(completed.stderr, str) else ""
        tail = "\n".join(f"{name}: {value.strip()[-600:]}" for name, value in
                         (("stdout", stdout), ("stderr", stderr)) if value.strip())
        if completed.returncode < 0 or completed.returncode > 128:
            problem = ("crash", f"the simulator crashed with {signal_name(completed.returncode)}" + (f"\n{tail}" if tail else ""))
            continue
        return None, ("error", f"the simulator exited with code {completed.returncode}" + (f"\n{tail}" if tail else ""))
    return None, problem


def replay_options(args):
    runtime = getattr(args, 'runtime', None)
    options = {'isolated': True} if getattr(args, 'sandbox', False) or runtime is not None else {}
    if runtime is not None:
        options['runtime'] = runtime
    return options


def pulse_view(args, step_trace, case_dir, name, expected, head_sim, base_sim):
    """(pulse data, None) or (None, reason). The reference pulses come from the base firmware's own simulator run, and
    only when that run still reproduces expected.trace: a chart must never label something "reference" that is not."""
    if not base_sim:
        return None, "no simulator of the base firmware was provided"
    if not os.access(base_sim, os.X_OK):
        return None, "the simulator of the base firmware could not be built"
    base_dir = os.path.join(args.out, "base")
    os.makedirs(base_dir, exist_ok=True)
    runner_options = replay_options(args)
    base_path, base_error = run_case(base_sim, case_dir, base_dir, args.timeout, attempts=1, extra=["--full-steps"], **runner_options)
    if not base_path:
        detail = base_error[1] if base_error else 'no result'
        return None, 'base full-step replay failed: ' + detail[-8192:]
    pr_path, pr_error = run_case(head_sim, case_dir, args.out, args.timeout, attempts=1, extra=["--full-steps"], tag=".full", **runner_options)
    if not pr_path:
        detail = pr_error[1] if pr_error else 'no result'
        return None, 'PR full-step replay failed: ' + detail[-8192:]
    try:
        with open(base_path) as handle:
            base = json.load(handle)
        with open(pr_path) as handle:
            pr = json.load(handle)
        if step_trace.compare(expected, step_trace.record(base))["status"] != "pass":
            return None, "the base firmware does not reproduce expected.trace itself, so it cannot stand for the reference"
        return pulse_data.build(base, pr)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as error:
        return None, f"the step lists could not be read ({type(error).__name__})"


def main(argv=None, *, runtime=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", required=True, help="smoothie-sim binary built from the firmware under test")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", required=True, help="existing folder for this run's traces and the result")
    parser.add_argument("--sim-dir", default=DEFAULT_SIM_DIR, help="smoothie-sim checkout (for checkers/step_trace.py)")
    parser.add_argument("--base-sim", help="smoothie-sim binary built from the base firmware; its pulses draw the reference side of the charts")
    parser.add_argument("--sim-five", help="head simulator built with five actuators")
    parser.add_argument("--base-sim-five", help="base simulator built with five actuators")
    parser.add_argument("--require-base-sim", action="store_true", help="treat missing base simulators as incomplete")
    parser.add_argument("--record", action="store_true", help="write expected.trace instead of comparing")
    parser.add_argument("--only", action="append", default=[], help="run only this case (repeatable)")
    parser.add_argument("--coverage-manifest", help="required scenario dispositions for this firmware")
    parser.add_argument("--coverage-baseline", help="baseline case directory whose scenarios must be inventoried")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--sandbox", action="store_true", help="isolate each simulator attempt; caller must provide cgroup limits")
    parser.add_argument("--runtime-image", help="isolate each attempt with an already provisioned digest-pinned Docker image")
    args = parser.parse_args(argv)
    args.runtime = runtime
    if args.runtime_image and runtime is not None:
        parser.error('provide a runtime object or an image, not both')
    if args.runtime_image:
        from container_runtime import DockerRuntime
        try:
            args.runtime = DockerRuntime(args.runtime_image)
        except ValueError as error:
            parser.error(str(error))
    runner_options = replay_options(args)

    if not (os.path.isfile(args.sim) and os.access(args.sim, os.X_OK)):
        print(json.dumps({"type": "error", "message": f"simulator binary missing or not executable: {args.sim}"}))
        result = make_result("sim", "error", "Simulator step traces", "the simulator binary was not built",
                             details="The build of the simulator produced no executable, so no case could run.")
        with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
            json.dump(result, handle, indent=1)
        return 1
    if args.require_base_sim and not (args.base_sim and os.path.isfile(args.base_sim) and os.access(args.base_sim, os.X_OK)):
        result = make_result("sim", "error", "Simulator step traces", "the base simulator was not built",
                             details="A base simulator is required for this gate run; its build produced no executable.")
        with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
            json.dump(result, handle, indent=1)
        return 1
    if not os.path.isdir(args.cases):
        result = make_result("sim", "error", "Simulator step traces", "the simulator case directory is missing",
                             details=f"No cases were run: {args.cases}")
        with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
            json.dump(result, handle, indent=1)
        return 1
    step_trace = load_step_trace(args.sim_dir)
    names = sorted(n for n in os.listdir(args.cases) if os.path.isfile(os.path.join(args.cases, n, "config.txt")))
    names = [n for n in names if not args.only or n in args.only]
    coverage_errors = []
    planner_checker = None
    if args.coverage_manifest:
        try:
            planner_checker = load_planner_checker(args.sim_dir)
        except (OSError, ImportError, AttributeError) as error:
            coverage_errors.append(f"simulator planner checker could not be loaded: {error}")
        try:
            baseline = None
            if args.coverage_baseline:
                baseline = [n for n in os.listdir(args.coverage_baseline)
                            if os.path.isfile(os.path.join(args.coverage_baseline, n, "config.txt"))]
                if not baseline:
                    raise ValueError("baseline has no simulator cases")
            with open(args.coverage_manifest) as handle:
                coverage_errors.extend(coverage_problems(json.load(handle), names, args.cases, baseline))
        except (OSError, ValueError) as error:
            coverage_errors.append(f"coverage inventory could not be read: {error}")
        for name in names:
            if not reference_reviewed(os.path.join(args.cases, name)):
                coverage_errors.append(f"{name}: reference review is incomplete")
    failed, crashed, errored, passed, flaky = [], [], [], [], []
    lines, crash_notes, case_lines, samples = [], [], {}, {}
    planner_notes = []
    for name in names:
        case_dir = os.path.join(args.cases, name)
        try:
            head_sim, base_sim = simulators_for_case(case_dir, args.sim, args.base_sim, args.sim_five, args.base_sim_five)
        except (OSError, ValueError, TypeError) as error:
            errored.append(name)
            lines.append(f"- 🛑 `{name}`: simulator selection failed ({esc(str(error))})")
            continue
        if args.require_base_sim and not (base_sim and os.path.isfile(base_sim) and os.access(base_sim, os.X_OK)):
            errored.append(name)
            lines.append(f"- 🛑 `{name}`: the matching base simulator was not built")
            continue
        result_path, error = run_case(head_sim, case_dir, args.out, args.timeout, **runner_options)
        if error:
            kind, text = error
            first, _, rest = text.partition("\n")
            if kind == "crash" and not (args.coverage_manifest and not reference_reviewed(case_dir)):
                crashed.append(name)
                crash_notes.append(f"💥 <code>{esc(name)}</code>: {esc(first)}" + ("\n\n" + code_block(rest) if rest else ""))
            else:
                errored.append(name)
                note = " (reference is not reviewed)" if kind == "crash" else ""
                lines.append(f"- 🛑 `{name}`: {first}{note}" + ("\n\n" + code_block(rest) if rest else ""))
            continue
        try:
            with open(result_path) as handle:
                simulator_result = json.load(handle)
            trace = step_trace.record(simulator_result)
            serial_mismatches = serial_observation(case_dir, simulator_result)
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError, OverflowError) as error:
            errored.append(name)
            lines.append(f"- 🛑 `{name}`: simulator output is invalid ({type(error).__name__})")
            continue
        if planner_checker:
            try:
                advice = planner_advice(planner_checker, simulator_result)
                if advice:
                    planner_notes.append(f"<code>{esc(name)}</code>: {esc(advice)}")
            except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, RecursionError, OverflowError) as error:
                coverage_errors.append(f"{name}: simulator planner check could not inspect the output ({type(error).__name__})")
        with open(os.path.join(args.out, name + ".trace"), "w") as handle:
            handle.write(trace)
        expected_path = os.path.join(case_dir, "expected.trace")
        if args.record:
            if serial_mismatches:
                errored.append(name)
                lines.append(f"- 🛑 `{name}`: " + "; ".join(serial_mismatches))
                continue
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
        outcome["exact"].extend(serial_mismatches)
        if serial_mismatches:
            outcome["status"] = "fail"
        if outcome["status"] == "pass":
            passed.append(name)
            continue
        if args.coverage_manifest and not reference_reviewed(case_dir):
            errored.append(name)
            lines.append(f"- 🛑 `{name}`: the reference is not reviewed, so a mismatch cannot fail the pull request")
            continue
        # A mismatch is confirmed by a second run. The simulator is deterministic, so a run that differs from the
        # first one is a flaky check, never evidence against the pull request.
        again_path, again_error = run_case(head_sim, case_dir, args.out, args.timeout, attempts=1, **runner_options)
        if not again_path:
            errored.append(name)
            detail = again_error[1] if again_error else 'no retry result'
            lines.append(f"- 🛑 `{name}`: the retry failed, so the initial mismatch could not be confirmed\n\n"
                         + code_block(detail[-8192:]))
            continue
        if again_path:
            try:
                with open(again_path) as handle:
                    again_result = json.load(handle)
                trace_again = step_trace.record(again_result)
                serial_again = serial_observation(case_dir, again_result)
            except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError, OverflowError):
                errored.append(name)
                lines.append(f"- 🛑 `{name}`: retry output was invalid, so the mismatch could not be confirmed")
                continue
            if trace_again != trace or serial_again != serial_mismatches:
                if step_trace.compare(expected, trace_again)["status"] == "pass" and not serial_again:
                    flaky.append(name)
                    passed.append(name)
                else:
                    errored.append(name)
                    lines.append(f"- 🛑 `{name}`: two runs of the same input gave different steps or messages (non-deterministic), "
                                 "so the case proves nothing about the pull request")
                continue
        failed.append(name)
        shown = (outcome["exact"] + outcome["timing"])[:3]
        more = len(outcome["exact"]) + len(outcome["timing"]) - len(shown)
        lines.append(f"- ❌ `{name}`: " + "; ".join(shown) + (f"; and {more} more" if more > 0 else ""))
        case_lines[name] = "; ".join(shown) + (f"; and {more} more" if more > 0 else "")
        pulses, reason = pulse_view(args, step_trace, case_dir, name, expected, head_sim, base_sim)
        if reason:
            case_lines[name] += '\n\nChart replay unavailable: ' + reason[-8192:]
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
        parts.append("The simulator, built from this pull request, died repeatedly on these inputs. "
                     "The base firmware's behavior is not established by this result alone.\n\n"
                     + "\n\n".join(crash_notes))
    if failed:
        blocks = []
        for name in failed:
            summary = f"❌ <code>{esc(name)}</code>: {esc(case_lines[name][:140])}"
            blocks.append(f"<details><summary>{summary}</summary>\n\n<!--chart:{chart_key(name)}-->\n\n"
                          f"{esc(case_lines[name])}\n\n</details>")
        parts.append("Cases whose steps or required messages differ from the reference (open one to see the reference and the produced "
                     "blocks side by side):\n\n" + "\n\n".join(blocks))
    if errored:
        parts.append("Cases that could not run (a problem of the check, not necessarily of the pull request):\n\n"
                     + "\n".join(l for l in lines if l.startswith("- 🛑")))
    details = "\n\n".join(parts)
    if coverage_errors:
        parts.append("V2 scenario coverage is incomplete:\n\n" + "\n".join("- " + esc(p) for p in coverage_errors))
        details = "\n\n".join(parts)
    if flaky:
        parts.append("🎲 Cases that differed once and matched on the second run (a flaky run, not blamed on the pull "
                     "request): " + ", ".join(f"<code>{esc(n)}</code>" for n in flaky))
        details = "\n\n".join(parts)
    if planner_notes:
        parts.append("Simulator planner invariants flagged these head results. This is advice, not necessarily a "
                     "pull-request regression; the base behavior is not established by this check:\n\n- "
                     + "\n- ".join(planner_notes))
        details = "\n\n".join(parts)
    if not total:
        result = make_result("sim", "error", title, "no simulator cases were found", metrics=metrics)
    elif args.record and (errored or crashed or failed):
        result = make_result("sim", "error", title, f"could not record all {total} reference traces", metrics=metrics,
                             details=details)
    elif args.record:
        result = make_result("sim", "pass", title, f"recorded {len(passed)} of {total} reference traces", metrics=metrics,
                             details=details)
    elif crashed:
        result = make_result("sim", "fail", title, f"{len(crashed)} of {total} cases crash the simulator"
                             + (f", {len(failed)} have different steps or messages" if failed else ""), details=details, metrics=metrics)
    elif failed:
        result = make_result("sim", "fail", title, f"{len(failed)} of {total} cases have different steps or messages",
                             details=details, metrics=metrics)
    elif errored:
        result = make_result("sim", "error", title, f"{len(errored)} of {total} cases could not run",
                             details=details, metrics=metrics)
    elif coverage_errors:
        result = make_result("sim", "error", title, f"{len(coverage_errors)} V2 coverage or reference checks are incomplete",
                             details=details, metrics=metrics)
    elif flaky:
        result = make_result("sim", "warn", title, f"all {total} cases pass, {len(flaky)} only on the second run",
                             details=details, metrics=metrics)
    elif planner_notes:
        result = make_result("sim", "warn", title, f"all {total} cases emit the reference steps; planner advice is available",
                             details=details, metrics=metrics)
    else:
        result = make_result("sim", "pass", title, f"all {total} cases emit the reference steps", metrics=metrics)
    with open(os.path.join(args.out, "sim.result.json"), "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"type": "result", "stage": "sim", "status": result["status"], "summary": result["summary"]}))
    return 0 if result["status"] in ("pass", "warn") else 1


if __name__ == "__main__":
    sys.exit(main())
