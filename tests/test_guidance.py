import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import guidance  # noqa: E402
from gate_result import validate_result  # noqa: E402

ANSWER = {"opening": "Thank you @bob for this!", "verdict": "changes suggested",
          "must_fix": [{"title": "Remove wait()", "explanation": "It blocks the `main loop` <b>x</b>.", "fix": "Use a tick.",
                        "code": "-    wait(0.01F);\n+    // removed ``` oops", "language": "diff"}],
          "nice_to_have": [{"title": "Shorter name", "explanation": "Minor.", "fix": ""}], "closing": "Cheers"}


def write_results(directory, status):
    data = {"stage": "size", "status": status, "title": "Flash and RAM", "summary": "s", "details": "", "metrics": {}}
    json.dump(data, open(os.path.join(directory, "size.result.json"), "w"))


class GuidanceTests(unittest.TestCase):
    def test_render_is_fixed_markdown_without_injection(self):
        text, verdict = guidance.render(ANSWER)
        self.assertEqual(verdict, "changes suggested")
        self.assertNotIn("@bob", text)
        self.assertNotIn("<b>", text)
        self.assertIn("<code>main loop</code>", text)
        self.assertIn("```diff", text.replace("````diff", "```diff"))
        self.assertIn("general directions", text)

    def test_language_name_line_is_dropped_from_code(self):
        answer = dict(ANSWER, must_fix=[dict(ANSWER["must_fix"][0], code="diff\n-a\n+b")])
        self.assertNotIn("\ndiff\n", guidance.render(answer)[0])

    def test_render_rejects_nonsense(self):
        self.assertIsNone(guidance.render("hello"))
        self.assertIsNone(guidance.render({}))

    def run_main(self, status, model_text=None, extra=()):
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        write_results(root, status)
        open(os.path.join(root, "d.diff"), "w").write("diff")
        argv = ["guidance.py", "--results", root, "--diff", os.path.join(root, "d.diff"), "--out", root, *extra]
        client = mock.Mock()
        client.complete.return_value = (model_text, "model") if model_text else (None, "down")
        with mock.patch.object(guidance, "ModelClient", return_value=client), mock.patch.object(sys, "argv", argv):
            guidance.main()
        return json.load(open(os.path.join(root, "guidance.result.json"))), client

    def test_all_pass_needs_no_model(self):
        data, client = self.run_main("pass")
        self.assertEqual(data["status"], "pass")
        self.assertIn("Thank you", data["details"])
        client.complete.assert_not_called()

    def test_warn_asks_the_model(self):
        data, _ = self.run_main("warn", json.dumps(ANSWER))
        self.assertEqual(data["status"], "pass")
        self.assertIn("Remove wait()", data["details"])
        self.assertEqual(validate_result(data), [])

    def test_failing_pr_without_code_is_asked_again(self):
        bare = dict(ANSWER, must_fix=[dict(ANSWER["must_fix"][0], code="")])
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        write_results(root, "fail")
        open(os.path.join(root, "d.diff"), "w").write("diff")
        argv = ["guidance.py", "--results", root, "--diff", os.path.join(root, "d.diff"), "--out", root]
        client = mock.Mock()
        client.complete.side_effect = [(json.dumps(bare), "m"), (json.dumps(ANSWER), "m")]
        with mock.patch.object(guidance, "ModelClient", return_value=client), mock.patch.object(sys, "argv", argv):
            guidance.main()
        data = json.load(open(os.path.join(root, "guidance.result.json")))
        self.assertEqual(client.complete.call_count, 2)
        self.assertIn("```diff", data["details"])

    def test_blocking_removal_sketch_comes_from_the_diff(self):
        diff = "+++ b/src/A.cpp\n@@ -1 +1,2 @@\n+    wait(0.01F);\n+    int x = 1;\n+    sleep(1);\n"
        sketch = guidance.blocking_removal_sketch(diff)
        self.assertIn("-    wait(0.01F);", sketch)
        self.assertIn("-    sleep(1);", sketch)
        self.assertNotIn("int x", sketch)
        self.assertEqual(guidance.blocking_removal_sketch("+++ b/a\n+    await(1);\n"), "")

    def test_sketch_is_added_when_the_model_gave_no_code(self):
        bare = dict(ANSWER, must_fix=[dict(ANSWER["must_fix"][0], code="")])
        root = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        write_results(root, "fail")
        open(os.path.join(root, "d.diff"), "w").write("+++ b/src/A.cpp\n@@ -1 +1 @@\n+    wait(1);\n")
        argv = ["guidance.py", "--results", root, "--diff", os.path.join(root, "d.diff"), "--out", root]
        client = mock.Mock()
        client.complete.return_value = (json.dumps(bare), "m")
        with mock.patch.object(guidance, "ModelClient", return_value=client), mock.patch.object(sys, "argv", argv):
            guidance.main()
        data = json.load(open(os.path.join(root, "guidance.result.json")))
        self.assertIn("```diff", data["details"])
        self.assertIn("-    wait(1);", data["details"])

    def test_model_outage_is_skipped(self):
        data, _ = self.run_main("fail")
        self.assertEqual(data["status"], "skipped")



class JudgementFilterTests(unittest.TestCase):
    BAD = ["Thanks again for the work; the PR is ready to merge!", "This is a great contribution.", "LGTM.",
           "It looks good to me.", "Worth merging.", "I approve this change.", "Nothing you need to change.",
           "The PR is acceptable.", "Excellent, well done."]

    def test_judgement_sentences_are_removed(self):
        for bad in self.BAD:
            self.assertEqual(guidance.without_judgement(bad), "", bad)
        self.assertEqual(guidance.without_judgement("Thanks. This is ready to merge! The wait() call blocks."),
                         "Thanks. The wait() call blocks.")

    def test_neutral_text_passes(self):
        self.assertEqual(guidance.without_judgement("The build failed in Planner.cpp."), "The build failed in Planner.cpp.")
        for text in (guidance.NEUTRAL_OPENING, guidance.NEUTRAL_CLOSING, guidance.NEUTRAL_ALL_PASS):
            self.assertEqual(guidance.without_judgement(text), text)

    def test_render_never_keeps_a_verdict(self):
        answer = {"opening": "Great work, this is ready to merge!", "closing": "Thanks again for the work; the PR is ready to merge!",
                  "must_fix": [{"title": "Blocking call", "explanation": "wait() blocks. It is otherwise a good PR.", "fix": "Use a tick."}]}
        text, verdict = guidance.render(answer)
        self.assertNotRegex(text, guidance.JUDGEMENT)
        self.assertIn(guidance.NEUTRAL_CLOSING, text)
        self.assertIn("wait() blocks.", text)

    def test_prompt_forbids_verdicts(self):
        with open(os.path.join(ROOT, "prompts", "guidance.md")) as handle:
            prompt = handle.read()
        self.assertIn("HARD RULE", prompt)
        self.assertNotIn('"verdict"', prompt)

if __name__ == "__main__":
    unittest.main()
