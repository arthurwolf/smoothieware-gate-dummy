import os
import sys
import tempfile
import unittest
import hashlib
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import gate


class SimulatorProvenanceTests(unittest.TestCase):
    def test_canonical_dependency_is_frozen_and_identified_independently(self):
        required = ('include/smoothie/v2/hal/Pin.h', 'include/smoothie/v2/v2_callback_context.h',
                    'include/smoothie/v2/v2_step_timer.h', 'include/smoothie/v2/v2_scheduler.h',
                    'src/v2/v2_step_timer.cpp', 'src/v2/v2_scheduler.cpp', 'src/v2/tmr_setup.cpp',
                    'tools/stage_v2.py', 'tools/stage_v2_patch.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); original = root / 'source' / 'sim'; frozen = root / 'frozen' / 'sim'
            original.mkdir(parents=True); (frozen / 'tools').mkdir(parents=True)
            (frozen / 'tools' / 'v2_source_key.py').write_text("shared = 'lib-smoothie'\n")
            library = original.parent / 'lib-smoothie'
            for name in required:
                path = library / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('original')
            one = gate.snapshot_simulator_dependencies(str(original), str(frozen), str(root / 'first'))
            (library / required[0]).write_text('changed')
            two = gate.snapshot_simulator_dependencies(str(original), str(frozen), str(root / 'second'))
            self.assertNotEqual(one['lib-smoothie']['sha256'], two['lib-smoothie']['sha256'])
            self.assertEqual((root / 'first' / 'lib-smoothie' / required[0]).read_text(), 'original')
            (library / required[0]).unlink()
            with self.assertRaises(ValueError):
                gate.snapshot_simulator_dependencies(str(original), str(frozen), str(root / 'missing'))

    def test_simulator_without_shared_dependency_remains_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gate.snapshot_simulator_dependencies(tmp, tmp, tmp), {})

    def test_exported_firmware_can_report_its_exact_revision(self):
        commit = (b"tree " + b"0" * 40 + b"\nauthor Test <test@example.invalid> 1 +0000\n"
                  b"committer Test <test@example.invalid> 1 +0000\n\nTest\n")
        revision = hashlib.sha1(b"commit " + str(len(commit)).encode() + b"\0" + commit).hexdigest()
        with tempfile.TemporaryDirectory() as root, mock.patch.object(gate.subprocess, "check_output", return_value=commit):
            metadata = gate.write_revision_metadata("/source", revision, root)
            with open(os.path.join(metadata, "objects", revision[:2], revision[2:]), "rb") as handle:
                self.assertEqual(zlib.decompress(handle.read()), b"commit " + str(len(commit)).encode() + b"\0" + commit)
            with open(os.path.join(metadata, "refs", "heads", "gate-snapshot")) as handle:
                self.assertEqual(handle.read().strip(), revision)
            with open(os.path.join(metadata, "shallow")) as handle:
                self.assertEqual(handle.read().strip(), revision)

    def test_revision_metadata_refuses_existing_source_link_before_writes(self):
        commit=b'fixture commit\n'
        revision=hashlib.sha1(b'commit 15\0'+commit).hexdigest()
        for link in (False,True):
            with self.subTest(link=link), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);tree=root/'tree';tree.mkdir();external=root/'external';external.mkdir()
                (external/'canary').write_bytes(b'preserve')
                if link:(tree/'.gate-git').symlink_to(external,target_is_directory=True)
                else:(tree/'.gate-git').mkdir()
                with mock.patch.object(gate.subprocess,'check_output',return_value=commit):
                    with self.assertRaises(FileExistsError):
                        gate.write_revision_metadata('/source',revision,str(tree))
                self.assertEqual(sorted(p.name for p in external.iterdir()),['canary'])
                self.assertEqual((external/'canary').read_bytes(),b'preserve')

    def test_case_snapshot_identifies_and_freezes_reference_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "cases")
            os.mkdir(source)
            with open(os.path.join(source, "expected.trace"), "w") as handle:
                handle.write("first")
            first = gate.snapshot_case_tree(source, os.path.join(root, "copy"))
            with open(os.path.join(source, "expected.trace"), "w") as handle:
                handle.write("second")
            second = gate.snapshot_case_tree(source, os.path.join(root, "copy2"))
            self.assertNotEqual(first, second)
            with open(os.path.join(root, "copy", "expected.trace")) as handle:
                self.assertEqual(handle.read(), "first")

    def test_snapshot_hash_tracks_modified_bytes_and_excludes_unlisted_files(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            os.mkdir(source)
            with open(os.path.join(source, "tracked"), "w") as handle:
                handle.write("one")
            with open(os.path.join(source, "untracked"), "w") as handle:
                handle.write("not an input")
            with mock.patch.object(gate, "tracked_simulator_files", return_value=["tracked"]):
                first = gate.snapshot_simulator(source, os.path.join(root, "first"))
                with open(os.path.join(source, "tracked"), "w") as handle:
                    handle.write("two")
                second = gate.snapshot_simulator(source, os.path.join(root, "second"))
            self.assertNotEqual(first, second)
            self.assertFalse(os.path.exists(os.path.join(root, "first", "untracked")))
            with open(os.path.join(root, "first", "tracked")) as handle:
                self.assertEqual(handle.read(), "one")

    def test_untracked_source_is_included_but_runtime_state_is_not(self):
        response = mock.Mock(returncode=0, stderr="", stdout="checkers/a.py\0tests/new.py\0.ilium/lock\0sim-result.json\0")
        with mock.patch.object(gate, "run", return_value=response):
            self.assertEqual(gate.tracked_simulator_files("/sim"), ["checkers/a.py", "tests/new.py"])

    def test_snapshot_rejects_escaping_paths(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(gate, "tracked_simulator_files", return_value=["../escape"]):
            with self.assertRaises(ValueError):
                gate.snapshot_simulator(root, os.path.join(root, "copy"))


if __name__ == "__main__":
    unittest.main()
