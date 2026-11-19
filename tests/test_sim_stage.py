import json, os, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sim_stage  # noqa: E402
from gate_result import validate_result  # noqa: E402

SIM_DIR = os.environ.get("SMOOTHIE_SIM", os.path.join(ROOT, "..", "smoothie-sim"))
step_trace = sim_stage.load_step_trace(SIM_DIR)


def fake_binary():
    """An executable file standing in for the simulator, so the stage's binary check passes while run_case is mocked."""
    path = os.path.join(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), "sim")
    open(path, "w").write("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return path


def result(steps=800, ticks=20000, entry=0.0, exit_speed=19.03):
    return {"n_actuators": 3, "steps_per_mm": [80, 80, 400], "final_steps": [steps, 0, 0],
            "blocks": [{"index": 0, "gcode_line": 3, "steps": [steps, 0, 0], "step_counts": [steps, 0, 0],
                        "direction_bits": 0, "step_dirs": [0, 0, 0], "dir_changes": [[[0, 1]], [], []],
                        "start_tick": 30, "ticks_run": ticks, "entry_speed": entry, "exit_speed": exit_speed}]}


class StepTraceTests(unittest.TestCase):
    def test_identical_traces_pass(self):
        trace = step_trace.record(result())
        self.assertEqual(step_trace.compare(trace, trace)["status"], "pass")

    def test_off_by_one_step_fails_exactly(self):
        outcome = step_trace.compare(step_trace.record(result()), step_trace.record(result(steps=801)))
        self.assertEqual(outcome["status"], "fail")
        self.assertTrue(any("counts" in line for line in outcome["exact"]))

    def test_small_timing_drift_passes(self):
        outcome = step_trace.compare(step_trace.record(result()), step_trace.record(result(ticks=20050)))
        self.assertEqual(outcome["status"], "pass")

    def test_large_timing_drift_fails_as_timing(self):
        outcome = step_trace.compare(step_trace.record(result()), step_trace.record(result(ticks=21000)))
        self.assertEqual((outcome["status"], outcome["exact"]), ("fail", []))
        self.assertTrue(outcome["timing"])

    def test_missing_block_fails(self):
        shorter = result()
        shorter["blocks"] = []
        outcome = step_trace.compare(step_trace.record(result()), step_trace.record(shorter))
        self.assertTrue(any("block count" in line for line in outcome["exact"]))


class StageTests(unittest.TestCase):
    def test_covered_scenario_requires_reviewed_reference(self):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        os.mkdir(os.path.join(root, "one"))
        with open(os.path.join(root, "one", "meta.json"), "w") as handle:
            json.dump({"approved_by": None}, handle)
        inventory = {"required_scenarios": ["square"], "scenarios": {
            "square": {"status": "covered", "case": "one"}}}
        self.assertTrue(sim_stage.coverage_problems(inventory, ["one"], root))
        with open(os.path.join(root, "one", "meta.json"), "w") as handle:
            json.dump({"reference_status": "reviewed", "approved_by": "reviewer",
                       "review": "Counts and timing independently checked."}, handle)
        self.assertEqual(sim_stage.coverage_problems(inventory, ["one"], root), [])

    def test_incomplete_v2_inventory_cannot_pass(self):
        inventory = {"required_scenarios": ["square", "arc"], "scenarios": {
            "square": {"status": "covered", "case": "square"},
            "arc": {"status": "pending"}}}
        problems = sim_stage.coverage_problems(inventory, ["square"])
        self.assertTrue(any("arc" in problem for problem in problems))

    def test_v2_inventory_cannot_omit_a_baseline_scenario(self):
        inventory = {"required_scenarios": ["square"], "scenarios": {}}
        self.assertTrue(any("arc" in problem for problem in
                            sim_stage.coverage_problems(inventory, [], baseline_names=["square", "arc"])))

    def test_v2_exclusion_requires_textual_reason_and_review(self):
        inventory = {"required_scenarios": ["arc"], "scenarios": {
            "arc": {"status": "excluded", "reason": True, "review": 1}}}
        self.assertTrue(sim_stage.coverage_problems(inventory, []))

    def run_stage(self, expected_text, coverage=False, meta=None, serial_out=None, simulator_data=None, reviewed=False):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "cases", "one")
        os.makedirs(case)
        for name, text in (("config.txt", ""), ("input.gcode", ""), ("meta.json", "{}")):
            open(os.path.join(case, name), "w").write(text)
        if meta is not None:
            with open(os.path.join(case, "meta.json"), "w") as handle:
                json.dump(meta, handle)
        if expected_text is not None:
            open(os.path.join(case, "expected.trace"), "w").write(expected_text)
        out = os.path.join(root, "out")
        os.makedirs(out)

        def fake_run(sim, case_dir, out_dir, timeout, attempts=2):
            path = os.path.join(out_dir, "one.json")
            data = simulator_data if simulator_data is not None else result()
            if serial_out is not None:
                data["serial_out"] = serial_out
            json.dump(data, open(path, "w"))
            return path, None
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", os.path.join(root, "cases"), "--out", out, "--sim-dir", SIM_DIR]
        if coverage:
            inventory = os.path.join(root, "coverage.json")
            with open(inventory, "w") as handle:
                scenarios = {"one": {"status": "covered", "case": "one"}} if reviewed else {}
                json.dump({"required_scenarios": ["one"], "scenarios": scenarios}, handle)
            argv += ["--coverage-manifest", inventory]
        with mock.patch.object(sim_stage, "run_case", fake_run), mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        return code, json.load(open(os.path.join(out, "sim.result.json")))

    def test_v2_matching_trace_reports_invalid_planner_timing_as_advice(self):
        data = result(exit_speed=0)
        data["blocks"][0].update({"steps_event_count": 800, "nominal_speed": 10, "nominal_rate": 800,
                                  "millimeters": 10, "acceleration": 100, "accelerate_until": 4294967295,
                                  "decelerate_after": 50, "total_move_ticks": 100, "max_entry_speed": 0,
                                  "is_g123": True})
        meta = {"reference_status": "reviewed", "approved_by": "reviewer", "review": "Independently reviewed."}
        code, report = self.run_stage(step_trace.record(data), coverage=True, meta=meta,
                                      simulator_data=data, reviewed=True)
        self.assertEqual((code, report["status"]), (0, "warn"))
        self.assertIn("trapezoid ticks out of order", report["details"])
        self.assertIn("not necessarily", report["details"])

    def test_unreviewed_v2_coverage_still_errors_with_planner_advice(self):
        data = result(exit_speed=0)
        data["blocks"][0].update({"steps_event_count": 800, "nominal_speed": 10, "nominal_rate": 800,
                                  "millimeters": 10, "acceleration": 100, "accelerate_until": 3,
                                  "decelerate_after": 2, "total_move_ticks": 100, "max_entry_speed": 0,
                                  "is_g123": True})
        _, report = self.run_stage(step_trace.record(data), coverage=True, simulator_data=data)
        self.assertEqual(report["status"], "error")
        self.assertIn("trapezoid ticks out of order", report["details"])

    def test_empty_case_set_is_an_error(self):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        out = os.path.join(root, "out")
        os.mkdir(out)
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", root, "--out", out, "--sim-dir", SIM_DIR]
        with mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        with open(os.path.join(out, "sim.result.json")) as handle:
            data = json.load(handle)
        self.assertNotEqual(code, 0)
        self.assertEqual(data["status"], "error")

    def test_missing_case_directory_is_an_error_result(self):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", os.path.join(root, "missing"),
                "--out", root, "--sim-dir", SIM_DIR]
        with mock.patch.object(sys, "argv", argv):
            self.assertNotEqual(sim_stage.main(), 0)
        with open(os.path.join(root, "sim.result.json")) as handle:
            self.assertEqual(json.load(handle)["status"], "error")

    def test_malformed_simulator_output_is_an_error_result(self):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "cases", "one")
        os.makedirs(case)
        for name in ("config.txt", "input.gcode", "meta.json"):
            open(os.path.join(case, name), "w").write("{}")
        output = os.path.join(root, "broken.json")
        open(output, "w").write("{")
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", os.path.join(root, "cases"),
                "--out", root, "--sim-dir", SIM_DIR]
        with mock.patch.object(sim_stage, "run_case", return_value=(output, None)), mock.patch.object(sys, "argv", argv):
            self.assertNotEqual(sim_stage.main(), 0)
        with open(os.path.join(root, "sim.result.json")) as handle:
            self.assertEqual(json.load(handle)["status"], "error")

    def test_matching_reference_passes(self):
        code, data = self.run_stage(step_trace.record(result()))
        self.assertEqual((code, data["status"]), (0, "pass"))
        self.assertEqual(validate_result(data), [])

    def test_expected_serial_message_cannot_be_silently_missing(self):
        code, data = self.run_stage(step_trace.record(result()),
                                    meta={"expected_serial_contains": ["Homing cycle failed"]}, serial_out="ok\n")
        self.assertNotEqual(code, 0)
        self.assertEqual(data["status"], "fail")
        self.assertIn("message", data["summary"])

    def test_expected_serial_message_can_match(self):
        code, data = self.run_stage(step_trace.record(result()),
                                    meta={"expected_serial_contains": ["Homing cycle failed"]},
                                    serial_out="ERROR: Homing cycle failed\n")
        self.assertEqual((code, data["status"]), (0, "pass"))

    def test_missing_serial_output_is_a_check_error(self):
        _, data = self.run_stage(step_trace.record(result()),
                                 meta={"expected_serial_contains": ["Homing cycle failed"]})
        self.assertEqual(data["status"], "error")

    def test_unreviewed_serial_mismatch_is_not_a_pr_failure(self):
        _, data = self.run_stage(step_trace.record(result()), coverage=True,
                                 meta={"expected_serial_contains": ["Homing cycle failed"]}, serial_out="ok\n")
        self.assertEqual(data["status"], "error")

    def test_forbidden_serial_error_cannot_be_silently_accepted(self):
        _, data = self.run_stage(step_trace.record(result()),
                                 meta={"forbidden_serial_contains": ["Error:"]}, serial_out="Error: unsupported arc\n")
        self.assertEqual(data["status"], "fail")

    def test_absent_forbidden_serial_error_passes(self):
        _, data = self.run_stage(step_trace.record(result()),
                                 meta={"forbidden_serial_contains": ["Error:"]}, serial_out="ok\n")
        self.assertEqual(data["status"], "pass")

    def test_changed_reference_fails(self):
        code, data = self.run_stage(step_trace.record(result(steps=799)))
        self.assertEqual((code, data["status"]), (1, "fail"))
        self.assertEqual(validate_result(data), [])

    def test_unreviewed_v2_reference_cannot_fail_the_pull_request(self):
        code, data = self.run_stage(step_trace.record(result(steps=799)), coverage=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(data["status"], "error")

    def test_missing_reference_is_error(self):
        _, data = self.run_stage(None)
        self.assertEqual(data["status"], "error")


class FlakyTests(unittest.TestCase):
    def run_stage(self, runs, meta=None):
        """runs: the traces (as result dicts) the fake simulator returns on successive calls."""
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "cases", "one")
        os.makedirs(case)
        for name, text in (("config.txt", ""), ("input.gcode", ""), ("meta.json", "{}")):
            open(os.path.join(case, name), "w").write(text)
        if meta is not None:
            with open(os.path.join(case, "meta.json"), "w") as handle:
                json.dump(meta, handle)
        open(os.path.join(case, "expected.trace"), "w").write(step_trace.record(result()))
        out = os.path.join(root, "out")
        os.makedirs(out)
        sim = os.path.join(root, "sim")
        open(sim, "w").write("#!/bin/sh\n")
        os.chmod(sim, 0o755)
        calls = iter(runs)

        def fake_run(sim_path, case_dir, out_dir, timeout, attempts=2):
            path = os.path.join(out_dir, "one.json")
            current = next(calls)
            if isinstance(current, tuple):
                return None, current
            json.dump(current, open(path, "w"))
            return path, None
        argv = ["sim_stage.py", "--sim", sim, "--cases", os.path.join(root, "cases"), "--out", out, "--sim-dir", SIM_DIR]
        with mock.patch.object(sim_stage, "run_case", fake_run), mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        return code, json.load(open(os.path.join(out, "sim.result.json")))

    def test_mismatch_confirmed_by_second_run_fails(self):
        _, data = self.run_stage([result(steps=801), result(steps=801)])
        self.assertEqual(data["status"], "fail")

    def test_one_off_wrong_run_is_flaky_not_a_failure(self):
        code, data = self.run_stage([result(steps=801), result()])
        self.assertEqual((code, data["status"]), (0, "warn"))
        self.assertIn("second run", data["summary"])

    def test_two_different_wrong_runs_are_an_error_of_the_check(self):
        _, data = self.run_stage([result(steps=801), result(steps=802)])
        self.assertEqual(data["status"], "error")
        self.assertIn("non-deterministic", data["details"])

    def test_failed_retry_cannot_confirm_a_mismatch(self):
        _, data = self.run_stage([result(steps=801), ("error", "simulator failed")])
        self.assertEqual(data["status"], "error")

    def test_serial_message_that_appears_on_retry_is_flaky(self):
        first, second = result(), result()
        first["serial_out"], second["serial_out"] = "ok", "Homing cycle failed"
        _, data = self.run_stage([first, second], {"expected_serial_contains": ["Homing cycle failed"]})
        self.assertEqual(data["status"], "warn")

    def test_missing_simulator_binary_is_a_clear_error(self):
        out = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        argv = ["sim_stage.py", "--sim", os.path.join(out, "nope"), "--cases", out, "--out", out, "--sim-dir", SIM_DIR]
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(sim_stage.main(), 1)
        self.assertEqual(json.load(open(os.path.join(out, "sim.result.json")))["status"], "error")


class RealProcessTests(unittest.TestCase):
    def test_relative_paths_work_because_each_case_runs_in_the_output_folder(self):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "cases", "one")
        os.makedirs(case)
        for name, text in (("config.txt", "c"), ("input.gcode", "g"), ("meta.json", "{}")):
            open(os.path.join(case, name), "w").write(text)
        sim = os.path.join(root, "sim.sh")  # fails unless it can read --config and --gcode and write --out
        with open(sim, "w") as handle:
            handle.write(f"#!{sys.executable}\nimport sys\n"
                         "open(sys.argv[2]).read(); open(sys.argv[4]).read()\n"
                         "open(sys.argv[6], 'w').write('{}')\n")
        os.chmod(sim, 0o755)
        out = os.path.join(root, "out")
        os.makedirs(out)
        previous = os.getcwd()
        os.chdir(root)
        try:
            path, problem = sim_stage.run_case("sim.sh", "cases/one", "out", 10)
        finally:
            os.chdir(previous)
        self.assertIsNone(problem)
        self.assertTrue(os.path.isfile(path))


class CrashTests(unittest.TestCase):
    def make_case(self):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "one")
        os.makedirs(case)
        for name, text in (("config.txt", ""), ("input.gcode", ""), ("meta.json", "{}")):
            open(os.path.join(case, name), "w").write(text)
        return root, case

    def test_signal_names(self):
        self.assertIn("SIGSEGV", sim_stage.signal_name(-11))
        self.assertIn("SIGSEGV", sim_stage.signal_name(139))

    def test_five_actuator_case_requires_matching_simulators(self):
        root, case = self.make_case()
        with open(os.path.join(case, "meta.json"), "w") as handle:
            json.dump({"simulator_variant": "five-actuators"}, handle)
        self.assertEqual(sim_stage.simulators_for_case(case, "head4", "base4", "head5", "base5"),
                         ("head5", "base5"))
        with self.assertRaises(ValueError):
            sim_stage.simulators_for_case(case, "head4", "base4", None, "base5")
        with open(os.path.join(case, "meta.json"), "w") as handle:
            json.dump({"simulator_variant": "unknown"}, handle)
        with self.assertRaises(ValueError):
            sim_stage.simulators_for_case(case, "head4", "base4", "head5", "base5")

    def test_pulse_chart_uses_selected_simulator_variant(self):
        root, case = self.make_case()
        args = mock.Mock(out=root, timeout=5)
        base_path = os.path.join(root, "base-chart.json")
        with open(base_path, "w") as handle:
            json.dump(result(), handle)
        with mock.patch.object(sim_stage.os, "access", return_value=True), \
                mock.patch.object(sim_stage, "run_case", side_effect=[(base_path, None), (None, ("error", "missing"))]) as run:
            pulses, reason = sim_stage.pulse_view(args, step_trace, case, "one", "trace", "head5", "base5")
        self.assertIsNone(pulses)
        self.assertIn("missing", reason)
        self.assertEqual([call.args[0] for call in run.call_args_list], ["base5", "head5"])

    def test_failed_simulator_preserves_stdout_diagnostic(self):
        root, case = self.make_case()
        completed = mock.Mock(returncode=2, stderr="configuration failed", stdout="ERROR: second motor missing")
        with mock.patch.object(sim_stage.subprocess, "run", return_value=completed):
            _, problem = sim_stage.run_case("sim", case, root, 5)
        self.assertEqual(problem[0], "error")
        self.assertIn("second motor missing", problem[1])
        self.assertIn("configuration failed", problem[1])

    def test_crash_is_retried_once_then_reported_as_crash(self):
        root, case = self.make_case()
        completed = mock.Mock(returncode=-11, stderr="")
        with mock.patch.object(sim_stage.subprocess, "run", return_value=completed) as run:
            path, problem = sim_stage.run_case("sim", case, root, 5)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(problem[0], "crash")
        self.assertIn("SIGSEGV", problem[1])

    def test_one_off_crash_then_success_passes(self):
        root, case = self.make_case()
        replies = [mock.Mock(returncode=-11, stderr=""), mock.Mock(returncode=0, stderr="")]
        def run(command, **kwargs):
            reply = replies.pop(0)
            if reply.returncode == 0:
                with open(command[command.index("--out") + 1], "w") as handle:
                    handle.write("{}")
            return reply
        with mock.patch.object(sim_stage.subprocess, "run", side_effect=run):
            path, problem = sim_stage.run_case("sim", case, root, 5)
        self.assertIsNone(problem)

    def test_success_without_a_new_result_cannot_reuse_a_crashed_attempt(self):
        root, case = self.make_case()
        count = 0
        def run(command, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                with open(command[command.index("--out") + 1], "w") as handle:
                    handle.write("{}")
                return mock.Mock(returncode=-11, stderr="")
            return mock.Mock(returncode=0, stderr="")
        with mock.patch.object(sim_stage.subprocess, "run", side_effect=run):
            path, problem = sim_stage.run_case("sim", case, root, 5)
        self.assertIsNone(path)
        self.assertEqual(problem[0], "error")

    def test_crash_makes_the_stage_fail_not_error(self):
        root, case = self.make_case()
        out = os.path.join(root, "out")
        os.makedirs(out)
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", root, "--out", out, "--sim-dir", SIM_DIR]
        with mock.patch.object(sim_stage, "run_case", return_value=(None, ("crash", "the simulator crashed with SIGSEGV"))), \
                mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        data = json.load(open(os.path.join(out, "sim.result.json")))
        self.assertEqual((code, data["status"]), (1, "fail"))
        self.assertIn("crash", data["summary"])
        self.assertEqual(validate_result(data), [])

    def test_configuration_diagnostic_is_in_stage_details(self):
        root, case = self.make_case()
        out = os.path.join(root, "out")
        os.makedirs(out)
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", root, "--out", out, "--sim-dir", SIM_DIR]
        error = ("error", "the simulator exited with code 2\nstdout: ERROR: second motor missing")
        with mock.patch.object(sim_stage, "run_case", return_value=(None, error)), \
                mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        data = json.load(open(os.path.join(out, "sim.result.json")))
        self.assertEqual((code, data["status"]), (1, "error"))
        self.assertIn("second motor missing", data["details"])

    def test_required_base_simulator_cannot_be_silently_missing(self):
        root, case = self.make_case()
        out = os.path.join(root, "out")
        os.makedirs(out)
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", root, "--out", out,
                "--sim-dir", SIM_DIR, "--require-base-sim"]
        with mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        data = json.load(open(os.path.join(out, "sim.result.json")))
        self.assertEqual((code, data["status"]), (1, "error"))
        self.assertIn("base", data["summary"].lower())

    def test_required_five_actuator_base_is_checked_per_case(self):
        root, case = self.make_case()
        with open(os.path.join(case, "meta.json"), "w") as handle:
            json.dump({"simulator_variant": "five-actuators"}, handle)
        out = os.path.join(root, "out")
        os.makedirs(out)
        argv = ["sim_stage.py", "--sim", fake_binary(), "--base-sim", fake_binary(),
                "--sim-five", fake_binary(), "--require-base-sim", "--cases", root, "--out", out, "--sim-dir", SIM_DIR]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sim_stage, "run_case") as run:
            code = sim_stage.main()
        data = json.load(open(os.path.join(out, "sim.result.json")))
        self.assertEqual((code, data["status"]), (1, "error"))
        self.assertIn("matching base simulator", data["details"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class IsolatedReplayTests(unittest.TestCase):
    def test_uncertain_container_identity_survives_in_bounded_stage_report(self):
        from sandbox_process import ProcessOutput
        with tempfile.TemporaryDirectory() as root:
            cases=os.path.join(root,'cases');os.mkdir(cases)
            case=os.path.join(cases,'one');os.mkdir(case)
            out=os.path.join(root,'out');os.mkdir(out)
            for name in ('config.txt','input.gcode','expected.trace'):
                with open(os.path.join(case,name),'w') as handle:handle.write('')
            with open(os.path.join(case,'meta.json'),'w') as handle:json.dump({},handle)
            executable=os.path.join(root,'sim');open(executable,'w').close();os.chmod(executable,0o500)
            recovery=b'Unverified owned container: gate-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            captured=ProcessOutput(75,b'',b'x'*262144+b'\n'+recovery,True,'container_cleanup_unverified')
            argv=['sim_stage.py','--sim',executable,'--cases',cases,'--out',out,'--sim-dir',SIM_DIR,'--sandbox']
            with mock.patch.object(sys,'argv',argv),mock.patch('sandbox_replay.run',return_value=captured):
                self.assertEqual(sim_stage.main(),1)
            with open(os.path.join(out,'sim.result.json')) as handle:report=json.load(handle)
            self.assertEqual(report['status'],'error')
            self.assertIn(recovery.decode(),report['details'])
            self.assertLess(len(report['details']),6000)

    def test_selected_runtime_forces_isolation_and_is_kept_for_retry(self):
        from sandbox_process import ProcessOutput
        runtime=object()
        with tempfile.TemporaryDirectory() as root:
            case=os.path.join(root,'one');os.mkdir(case)
            with open(os.path.join(case,'meta.json'),'w') as f:json.dump({},f)
            with mock.patch('sandbox_replay.run',side_effect=[ProcessOutput(-9,b'partial',b'',False,'timeout'),ProcessOutput(0,b'{}',b'',False,None)]) as replay:
                path,problem=sim_stage.run_case('sim',case,root,5,runtime=runtime)
            self.assertIsNone(problem)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(len(replay.call_args_list),2)
            self.assertTrue(all(call.kwargs['runtime'] is runtime for call in replay.call_args_list))

    def test_pulse_replay_keeps_selected_runtime(self):
        from types import SimpleNamespace
        runtime=object()
        with tempfile.TemporaryDirectory() as root:
            executable=os.path.join(root,'base-simulator');open(executable,'w').close();os.chmod(executable,0o500)
            args=SimpleNamespace(out=root,timeout=5,sandbox=False,runtime=runtime)
            with mock.patch.object(sim_stage,'run_case',return_value=(None,('error','fixture error'))) as replay:
                data,problem=sim_stage.pulse_view(args,None,root,'case',[],executable,executable)
            self.assertIsNone(data)
            self.assertIsNotNone(problem)
            self.assertTrue(all(call.kwargs.get('runtime') is runtime for call in replay.call_args_list))

    def test_isolated_success_publishes_only_captured_result_bytes(self):
        from sandbox_process import ProcessOutput
        with tempfile.TemporaryDirectory() as root:
            case=os.path.join(root,'one');os.mkdir(case)
            with open(os.path.join(case,'meta.json'),'w') as f:json.dump({},f)
            with mock.patch('sandbox_replay.run',return_value=ProcessOutput(0,b'{"actual":true}',b'',False,None)):
                path,problem=sim_stage.run_case('sim',case,root,5,isolated=True)
            self.assertIsNone(problem)
            with open(path) as f:self.assertEqual(json.load(f),{'actual':True})

    def test_isolated_timeout_retries_without_publishing_partial_result(self):
        from sandbox_process import ProcessOutput
        with tempfile.TemporaryDirectory() as root:
            case=os.path.join(root,'one');os.mkdir(case)
            with open(os.path.join(case,'meta.json'),'w') as f:json.dump({},f)
            with mock.patch('sandbox_replay.run',return_value=ProcessOutput(-9,b'partial',b'tail',False,'timeout')):
                path,problem=sim_stage.run_case('sim',case,root,5,isolated=True)
            self.assertIsNone(path)
            self.assertEqual(problem[0],'timeout')
            self.assertEqual(os.listdir(root),['one'])


class InvalidSimulatorArguments(unittest.TestCase):
    def test_malformed_metadata_returns_case_error_without_execution(self):
        for metadata in ({'sim_args':None},{'sim_args':'--full-steps'},{'sim_args':{}},
                         {'sim_args':[17]},{'sim_args':['bad\0arg']},[],None):
            with self.subTest(metadata=metadata),tempfile.TemporaryDirectory() as root:
                case=os.path.join(root,'one');os.mkdir(case)
                with open(os.path.join(case,'meta.json'),'w') as f:json.dump(metadata,f)
                with mock.patch('sandbox_replay.run',side_effect=AssertionError('invalid metadata executed')):
                    path,problem=sim_stage.run_case('sim',case,root,5,isolated=True)
                self.assertIsNone(path)
                self.assertEqual(problem[0],'error')
                self.assertIn('metadata',problem[1])
