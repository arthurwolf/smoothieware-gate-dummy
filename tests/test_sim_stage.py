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
    def run_stage(self, expected_text):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "cases", "one")
        os.makedirs(case)
        for name, text in (("config.txt", ""), ("input.gcode", ""), ("meta.json", "{}")):
            open(os.path.join(case, name), "w").write(text)
        if expected_text is not None:
            open(os.path.join(case, "expected.trace"), "w").write(expected_text)
        out = os.path.join(root, "out")
        os.makedirs(out)

        def fake_run(sim, case_dir, out_dir, timeout, attempts=2):
            path = os.path.join(out_dir, "one.json")
            json.dump(result(), open(path, "w"))
            return path, None
        argv = ["sim_stage.py", "--sim", fake_binary(), "--cases", os.path.join(root, "cases"), "--out", out, "--sim-dir", SIM_DIR]
        with mock.patch.object(sim_stage, "run_case", fake_run), mock.patch.object(sys, "argv", argv):
            code = sim_stage.main()
        return code, json.load(open(os.path.join(out, "sim.result.json")))

    def test_matching_reference_passes(self):
        code, data = self.run_stage(step_trace.record(result()))
        self.assertEqual((code, data["status"]), (0, "pass"))
        self.assertEqual(validate_result(data), [])

    def test_changed_reference_fails(self):
        code, data = self.run_stage(step_trace.record(result(steps=799)))
        self.assertEqual((code, data["status"]), (1, "fail"))
        self.assertEqual(validate_result(data), [])

    def test_missing_reference_is_error(self):
        _, data = self.run_stage(None)
        self.assertEqual(data["status"], "error")


class FlakyTests(unittest.TestCase):
    def run_stage(self, runs):
        """runs: the traces (as result dicts) the fake simulator returns on successive calls."""
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        case = os.path.join(root, "cases", "one")
        os.makedirs(case)
        for name, text in (("config.txt", ""), ("input.gcode", ""), ("meta.json", "{}")):
            open(os.path.join(case, name), "w").write(text)
        open(os.path.join(case, "expected.trace"), "w").write(step_trace.record(result()))
        out = os.path.join(root, "out")
        os.makedirs(out)
        sim = os.path.join(root, "sim")
        open(sim, "w").write("#!/bin/sh\n")
        os.chmod(sim, 0o755)
        calls = iter(runs)

        def fake_run(sim_path, case_dir, out_dir, timeout, attempts=2):
            path = os.path.join(out_dir, "one.json")
            json.dump(next(calls), open(path, "w"))
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
        open(sim, "w").write('#!/bin/sh\ncat "$2" "$4" >/dev/null || exit 2\necho {} > "$6"\n')
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
        with mock.patch.object(sim_stage.subprocess, "run", side_effect=replies):
            path, problem = sim_stage.run_case("sim", case, root, 5)
        self.assertIsNone(problem)

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


if __name__ == "__main__":
    unittest.main()
