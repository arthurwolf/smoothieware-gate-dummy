import json, os, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from gate_result import make_result, validate_result, worst_status  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def load(name):
    with open(os.path.join(FIX, name)) as handle:
        return json.load(handle)


class ResultTests(unittest.TestCase):
    def test_sample_valid(self):
        self.assertEqual(validate_result(load("sample.result.json")), [])

    def test_malformed_rejected(self):
        problems = validate_result(load("malformed.result.json"))
        for text in ("missing field 'details'", "status 'great'", "'title' must not be empty", "metric 'a'", "unknown field 'extra'"):
            self.assertTrue(any(text in p for p in problems), text)

    def test_non_object(self):
        self.assertEqual(validate_result([]), ["result must be a JSON object"])

    def test_make_result_rejects_bad_status(self):
        with self.assertRaises(ValueError):
            make_result("size", "bogus", "t", "s")

    def test_worst_status(self):
        self.assertEqual(worst_status(["pass", "warn", "skipped"]), "warn")
        self.assertEqual(worst_status(["pass", "fail", "error"]), "fail")
        self.assertEqual(worst_status([]), "pass")

    def test_cli_exit_codes(self):
        cli = os.path.join(ROOT, "scripts", "gate_result.py")
        good = subprocess.run([sys.executable, "-I", cli, os.path.join(FIX, "sample.result.json")], capture_output=True, text=True)
        bad = subprocess.run([sys.executable, "-I", cli, os.path.join(FIX, "malformed.result.json")], capture_output=True, text=True)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
