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
    def test_provenance_is_visible_in_the_report(self):
        manifest = {"profile": "v2-prime", "board": "Prime", "toolchain": "gcc10", "firmware_repository": "Smoothieware/SmoothieV2",
                    "toolchain_version": "GNU Arm Embedded Toolchain 10.3-2021.10, 10.3.1 20210824",
                    "base": "a" * 40, "head": "b" * 40, "simulator_revision": "c" * 40,
                    "controller_sha256": "1" * 64,
                    "simulator_sha256": "d" * 64, "references_sha256": "e" * 64,
                    "case_inventory_sha256": "f" * 64, "expected_stages": list(report.EXPECTED_STAGES)}
        text = report.render_comment([], [], "", manifest=manifest)
        for field in ("v2-prime", "Prime", "gcc10", "a" * 40, "b" * 40, "d" * 64, "1" * 64):
            self.assertIn(field, text)

    def test_empty_report_is_incomplete(self):
        self.assertEqual(report.overall_status([], []), "error")

    def test_skipped_check_does_not_hide_passes(self):
        self.assertEqual(report.overall_status([{"status": "pass"}, {"status": "skipped"}], []), "pass")
        self.assertEqual(report.overall_status([{"status": "skipped"}], []), "skipped")
        self.assertEqual(report.overall_status([{"status": "fail"}, {"status": "skipped"}], []), "fail")

    def test_skipped_simulator_is_incomplete_but_ai_outage_is_not(self):
        self.assertEqual(report.overall_status([{"stage": "sim", "status": "skipped"}], []), "error")
        self.assertEqual(report.overall_status([{"stage": "build", "status": "pass"},
                                                {"stage": "ai-review", "status": "skipped"}], []), "pass")
        self.assertEqual(report.overall_status([{"stage": "build-base-release", "status": "skipped"},
                                                {"stage": "ai-review", "status": "pass"}], []), "error")

    def test_comment_has_marker_and_worst_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "a.result.json")
            write(tmp, "b.result.json", stage="sim", status="fail", title="Sim", summary="2 differ", details="x")
            results, problems = report.load_results(tmp)
            text = report.render_comment(results, problems, "https://example.invalid/run")
        self.assertTrue(text.startswith(report.MARKER))
        self.assertIn("❌ fail", text.splitlines()[1])
        self.assertIn("<details open>", text)

    def test_invalid_file_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.result.json"), "w") as handle:
                handle.write('{"stage": "x"}')
            results, problems = report.load_results(tmp)
            text = report.render_comment(results, problems, "")
        self.assertEqual(len(problems), 1)
        self.assertIn("error", text)

    def test_manifest_missing_required_stage_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "only.result.json", stage="sim")
            manifest = {"profile": "v2-prime", "board": "Prime", "toolchain": "gcc10", "firmware_repository": "Smoothieware/SmoothieV2",
                        "toolchain_version": "GNU Arm Embedded Toolchain 10.3-2021.10, 10.3.1 20210824",
                        "base": "a" * 40, "head": "b" * 40, "simulator_revision": "c" * 40,
                        "controller_sha256": "1" * 64, "simulator_sha256": "d" * 64,
                        "references_sha256": "e" * 64, "case_inventory_sha256": "f" * 64,
                        "expected_stages": list(report.EXPECTED_STAGES)}
            with open(os.path.join(tmp, "manifest.json"), "w") as handle:
                json.dump(manifest, handle)
            results, problems = report.load_results(tmp)
        self.assertTrue(any("missing required stage: size" in p for p in problems))
        self.assertEqual(report.overall_status(results, problems), "error")


    def test_details_cannot_break_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "a.result.json", details="hi @outside\n```\n@maintainer")
            results, problems = report.load_results(tmp)
            text = report.render_comment(results, problems, "")
        self.assertEqual(text.count("```"), 2)  # the unterminated fence is closed, so it cannot swallow the rest
        self.assertNotIn("@outside", text)  # a mention inside a closed fence cannot ping, one outside is defused
        self.assertIn("</details>", text.split("```")[-1])

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
