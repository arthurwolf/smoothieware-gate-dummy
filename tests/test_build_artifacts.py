"""Host collection must not follow or accept files chosen by firmware code."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import tracemalloc
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_artifacts


class BuildArtifactCollection(unittest.TestCase):
    def test_large_unexpected_directory_does_not_allocate_all_entry_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'finals'; source.mkdir()
            for index in range(1024):
                (source / (str(index) + '-' + 'x' * 180)).touch()
            tracemalloc.start()
            try:
                with self.assertRaises(ValueError):
                    build_artifacts.collect(source, root / 'trusted', {'firmware.bin': 10})
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 128 * 1024, 'unexpected output enumeration must use bounded memory')
            self.assertFalse((root / 'trusted').exists())

    def test_copy_failure_removes_only_its_own_partial_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'finals'; source.mkdir()
            (source / 'first.bin').write_bytes(b'first')
            second = source / 'second.bin'; second.write_bytes(b'second')
            canary = root / 'user-data'; canary.write_text('keep')
            original_read = os.read
            second_inode = second.stat().st_ino
            def fail_second(fd, count):
                if os.fstat(fd).st_ino == second_inode:
                    raise OSError('injected second-file read failure')
                return original_read(fd, count)
            with mock.patch.object(build_artifacts.os, 'read', side_effect=fail_second):
                with self.assertRaisesRegex(ValueError, 'injected second-file read failure'):
                    build_artifacts.collect(source, root / 'trusted', {'first.bin': 10, 'second.bin': 10})
            self.assertFalse((root / 'trusted').exists())
            self.assertEqual((source / 'first.bin').read_bytes(), b'first')
            self.assertEqual(second.read_bytes(), b'second')
            self.assertEqual(canary.read_text(), 'keep')

    def test_collects_only_complete_expected_finals_with_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'finals'; source.mkdir()
            (source / 'firmware.elf').write_bytes(b'elf-fixture')
            (source / 'firmware.bin').write_bytes(b'bin')
            output = root / 'trusted'
            result = build_artifacts.collect(source, output,
                                             {'firmware.elf': 20, 'firmware.bin': 20}, max_total_bytes=30)
            self.assertEqual((output / 'firmware.elf').read_bytes(), b'elf-fixture')
            self.assertEqual(result['firmware.bin'],
                             {'bytes': 3, 'sha256': hashlib.sha256(b'bin').hexdigest()})

    def test_rejects_forged_results_missing_files_and_oversize_before_publication(self):
        for problem in ('extra', 'missing', 'large', 'total'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source = root / 'finals'; source.mkdir()
                (source / 'firmware.elf').write_bytes(b'1234')
                if problem == 'extra': (source / 'build.result.json').write_text('{"status":"pass"}')
                if problem == 'missing': (source / 'firmware.elf').unlink()
                if problem == 'large': (source / 'firmware.elf').write_bytes(b'12345')
                with self.assertRaises(ValueError):
                    build_artifacts.collect(source, root / 'trusted', {'firmware.elf': 4},
                                            max_total_bytes=3 if problem == 'total' else 4)
                self.assertFalse((root / 'trusted').exists())

    def test_rejects_links_and_special_files_without_reading_them(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source = root / 'finals'; source.mkdir()
                external = root / 'secret'; external.write_bytes(b'private')
                target = source / 'firmware.elf'
                if kind == 'symlink': target.symlink_to(external)
                elif kind == 'hardlink': os.link(external, target)
                elif kind == 'fifo': os.mkfifo(target)
                else: target.mkdir()
                with self.assertRaises(ValueError):
                    build_artifacts.collect(source, root / 'trusted', {'firmware.elf': 100})
                self.assertFalse((root / 'trusted').exists())
                self.assertEqual(external.read_bytes(), b'private')

    def test_rejects_unsafe_names_and_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'finals'; source.mkdir()
            (source / 'firmware.elf').write_bytes(b'elf')
            output = root / 'trusted'; output.mkdir(); (output / 'canary').write_text('keep')
            for name in ('../escape', '/absolute', 'nested/file', '', 'a\\b'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    build_artifacts.collect(source, root / 'unused', {name: 10})
            with self.assertRaises(ValueError):
                build_artifacts.collect(source, output, {'firmware.elf': 10})
            self.assertEqual((output / 'canary').read_text(), 'keep')
