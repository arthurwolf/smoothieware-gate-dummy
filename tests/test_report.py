import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import report  # noqa: E402


def write(folder, name, **fields):
    data = {"stage": "build", "status": "pass", "title": "Build", "summary": "ok", "details": "", "metrics": {}}
    data.update(fields)
    with open(os.path.join(folder, name), "w") as handle:
        json.dump(data, handle)


class ReportTest(unittest.TestCase):
    def test_skipped_check_does_not_hide_passes(self):
        self.assertEqual(report.overall_status([{"status": "pass"}, {"status": "skipped"}], []), "pass")
        self.assertEqual(report.overall_status([{"status": "skipped"}], []), "skipped")
        self.assertEqual(report.overall_status([{"status": "fail"}, {"status": "skipped"}], []), "fail")

    def test_comment_has_marker_and_worst_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "a.result.json")
            write(tmp, "b.result.json", stage="sim", status="fail", title="Sim", summary="2 differ", details="x")
            results, problems = report.load_results(tmp)
            text = report.render_comment(results, problems, "https://example.invalid/run")
        self.assertTrue(text.startswith(report.MARKER))
        self.assertIn("❌ fail", text.splitlines()[1])
        self.assertIn("<details>", text)

    def test_invalid_file_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.result.json"), "w") as handle:
                handle.write('{"stage": "x"}')
            results, problems = report.load_results(tmp)
            text = report.render_comment(results, problems, "")
        self.assertEqual(len(problems), 1)
        self.assertIn("error", text)

    def test_details_cannot_break_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "a.result.json", details="```\n@maintainer")
            results, problems = report.load_results(tmp)
            text = report.render_comment(results, problems, "")
        self.assertNotIn("```", text)
        self.assertNotIn("@maintainer", text)

    def test_cli_exit_code_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "a.result.json", status="skipped", stage="ai-review", title="AI review")
            out = os.path.join(tmp, "out")
            done = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "report.py"), "--results", tmp,
                                   "--out", out], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0)
            for name in ("comment.md", "summary.md", "report.html"):
                self.assertTrue(os.path.isfile(os.path.join(out, name)))


if __name__ == "__main__":
    unittest.main()
