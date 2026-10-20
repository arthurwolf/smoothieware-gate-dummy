import json, os, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build  # noqa: E402
import size  # noqa: E402
from gate_result import validate_result  # noqa: E402


class BuildTests(unittest.TestCase):
    def test_failure_excerpt_puts_compiler_errors_before_the_tail(self):
        path = os.path.join(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), "make.log")
        open(path, "w").write("Compiling a.cpp\nKernel.cpp:403:1: error: 'this' does not name a type\n" + "Compiling b.c\n" * 40)
        excerpt = build.failure_excerpt(path)
        self.assertTrue(excerpt.startswith("Kernel.cpp:403:1: error"))
        self.assertIn("...", excerpt)

    def make_tree(self, line):
        tree = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        path = os.path.join(tree, build.LINKER_SCRIPT)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("MEMORY\n{\n  " + line + "\n}\n")
        return tree, path

    def test_enlarge_flash(self):
        tree, path = self.make_tree(build.FLASH_LINE)
        self.assertTrue(build.enlarge_flash(tree))
        self.assertIn("4096K", open(path).read())

    def test_enlarge_flash_missing_line(self):
        tree, _ = self.make_tree("FLASH (rx) : ORIGIN = 0, LENGTH = 256K")
        self.assertFalse(build.enlarge_flash(tree))

    def run_main(self, exit_code, make_elf):
        out = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        if make_elf:
            os.makedirs(os.path.join(out, "LPC1768"))
            open(os.path.join(out, "LPC1768", "main.elf"), "w").close()
        argv = ["build.py", "--tree", "/x", "--out", out, "--build-type", "Release", "--label", "pr"]
        with mock.patch.object(build, "run_ni_build", return_value=(exit_code, None, "boom")), \
                mock.patch.object(sys, "argv", argv):
            code = build.main()
        return code, json.load(open(os.path.join(out, "build.result.json")))

    def test_compile_failure_is_fail(self):
        code, result = self.run_main(2, False)
        self.assertEqual((code, result["status"]), (1, "fail"))
        self.assertEqual(validate_result(result), [])

    def test_success_is_pass(self):
        code, result = self.run_main(0, True)
        self.assertEqual((code, result["status"]), (0, "pass"))
        self.assertEqual(validate_result(result), [])

    def test_missing_elf_is_error(self):
        _, result = self.run_main(0, False)
        self.assertEqual(result["status"], "error")


class SizeTests(unittest.TestCase):
    DATA = {"rows": [{"memory": "Flash", "reference": 10, "candidate": 15, "delta": 5}],
            "top_symbol_changes": [{"symbol": "$t", "delta": 5}, {"symbol": "foo()", "delta": 5}, {"symbol": "bar", "delta": -2}]}

    def test_table_hides_mapping_symbols_and_shrinkage(self):
        text = size.table("Release", self.DATA)
        self.assertIn("| Flash | 10 | 15 | +5 |", text)
        self.assertIn("`foo()` +5 B", text)
        self.assertNotIn("`$t`", text)
        self.assertNotIn("bar", text)


if __name__ == "__main__":
    unittest.main()
