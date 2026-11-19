import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from artifact_safety import inspect  # noqa: E402


class ArtifactSafetyTests(unittest.TestCase):
    def test_regular_result_is_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "sim.result.json"), "w") as handle:
                handle.write("{}")
            self.assertEqual(inspect(root), [])

    def test_symlinks_and_oversized_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "large.result.json"), "w") as handle:
                handle.write("1234")
            os.symlink("large.result.json", os.path.join(root, "link.result.json"))
            problems = inspect(root, max_file_bytes=3)
            self.assertTrue(any("large.result.json" in x for x in problems))
            self.assertTrue(any("link.result.json" in x for x in problems))

    def test_unexpected_names_and_empty_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(inspect(root))
            with open(os.path.join(root, "unexpected.sh"), "w") as handle:
                handle.write("echo x")
            self.assertTrue(inspect(root))
