import sys
import os
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import v2_build_inputs
import build


class CanonicalPaths(unittest.TestCase):
    def test_search_only_parent_directory_does_not_block_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'search-only'; parent.mkdir()
            root = parent / 'tree'; root.mkdir()
            for folder in ('Firmware', 'flashloader'):
                (root / folder).mkdir()
                (root / folder / 'Rakefile').write_text("FFLAGS = 'flags'\n")
            parent.chmod(0o111)
            try:
                v2_build_inputs.normalize_paths(root)
                self.assertIn('/smoothie/Firmware', (root / 'Firmware' / 'Rakefile').read_text())
            finally:
                parent.chmod(0o700)

    def test_untrusted_recipe_links_cannot_change_external_files(self):
        for kind in ('symlink', 'hardlink', 'directory_symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root, outside = Path(tmp) / 'tree', Path(tmp) / 'outside'
                root.mkdir(); outside.mkdir()
                original = "FFLAGS = 'flags'\n"
                canary = outside / 'Rakefile'; canary.write_text(original)
                (root / 'flashloader').mkdir()
                (root / 'flashloader' / 'Rakefile').write_text(original)
                if kind == 'directory_symlink':
                    (root / 'Firmware').symlink_to(outside, target_is_directory=True)
                else:
                    (root / 'Firmware').mkdir()
                    recipe = root / 'Firmware' / 'Rakefile'
                    if kind == 'symlink': recipe.symlink_to(canary)
                    else: os.link(canary, recipe)
                with self.assertRaises((ValueError, OSError)):
                    v2_build_inputs.normalize_paths(root)
                self.assertEqual(canary.read_text(), original)
                self.assertEqual((root / 'flashloader' / 'Rakefile').read_text(), original)

    def test_recipe_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Firmware').mkdir(); (root / 'flashloader').mkdir()
            os.mkfifo(root / 'Firmware' / 'Rakefile')
            (root / 'flashloader' / 'Rakefile').write_text("FFLAGS = 'flags'\n")
            command = [sys.executable, '-B', '-I', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);import v2_build_inputs;v2_build_inputs.normalize_paths(sys.argv[2])',
                str(Path(v2_build_inputs.__file__).parent), str(root)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=2)
            except subprocess.TimeoutExpired:
                self.fail('normalization blocked on an untrusted FIFO')
            self.assertNotEqual(result.returncode, 0)

    def test_oversized_recipe_rejected_before_either_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = "FFLAGS = 'flags'\n"
            for folder in ('Firmware', 'flashloader'):
                (root / folder).mkdir()
                (root / folder / 'Rakefile').write_text(original if folder == 'Firmware' else original + '#' * 262144)
            with self.assertRaises(ValueError):
                v2_build_inputs.normalize_paths(root)
            self.assertEqual((root / 'Firmware' / 'Rakefile').read_text(), original)

    def test_build_rejects_unknown_layout_before_dispatch(self):
        with mock.patch.object(v2_build_inputs, 'normalize_paths', side_effect=ValueError('unknown layout')), \
                mock.patch.object(build.subprocess, 'Popen') as dispatch:
            with self.assertRaises(ValueError):
                build.run_v2_build('/tree', '/out', 'Debug', 'gcc10', 4, 'ni-build')
            dispatch.assert_not_called()

    def test_both_builds_use_stable_distinct_prefixes_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('Firmware', 'flashloader'):
                (root / folder).mkdir()
                (root / folder / 'Rakefile').write_text("FFLAGS = ' -ffunction-sections '\nCFLAGS = FFLAGS\n")
                # A read may update atime; that is not a source mutation.
                os.utime(root / folder / 'Rakefile', (0, 1))
            v2_build_inputs.normalize_paths(root)
            first = [(root / folder / 'Rakefile').read_text() for folder in ('Firmware', 'flashloader')]
            self.assertIn('/smoothie/Firmware', first[0])
            self.assertIn('/smoothie/flashloader', first[1])
            self.assertTrue(all('-ffile-prefix-map=#{File.realpath(Dir.pwd)}=' in text for text in first))
            v2_build_inputs.normalize_paths(root)
            self.assertEqual(first, [(root / folder / 'Rakefile').read_text() for folder in ('Firmware', 'flashloader')])

    def test_unknown_layout_does_not_partially_change_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('Firmware', 'flashloader'):
                (root / folder).mkdir()
                (root / folder / 'Rakefile').write_text("FFLAGS = 'flags'\n" if folder == 'Firmware' else 'unknown\n')
            with self.assertRaises(ValueError):
                v2_build_inputs.normalize_paths(root)
            self.assertEqual((root / 'Firmware' / 'Rakefile').read_text(), "FFLAGS = 'flags'\n")
