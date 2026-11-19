"""Synthetic compiler archives catch escaped or unverified CI toolchain inputs."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import bz2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:bz2') as handle:
        for name, payload, kind in entries:
            entry = tarfile.TarInfo(name)
            entry.mode = 0o755
            if kind == 'link':
                entry.type, entry.linkname = tarfile.SYMTYPE, payload
                handle.addfile(entry)
            else:
                entry.size = len(payload)
                handle.addfile(entry, io.BytesIO(payload))
    return output.getvalue()


GOOD = [('tool/bin/arm-none-eabi-gcc', b'synthetic compiler', 'file'),
        ('tool/bin/arm-none-eabi-g++', 'arm-none-eabi-gcc', 'link'),
        ('tool/bin/arm-none-eabi-size', b'synthetic size', 'file'),
        ('tool/bin/arm-none-eabi-nm', b'synthetic nm', 'file')]


class CompilerInputsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('compiler_inputs'),
                             'verified compiler preparation is missing')
        import compiler_inputs
        return compiler_inputs

    def test_extracts_exact_verified_copy_and_preserves_safe_internal_link(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = archive(GOOD)
            source = root / 'source.tar.bz2'; source.write_bytes(payload)
            output = root / 'prepared'
            compiler = module.prepare(source, hashlib.sha256(payload).hexdigest(), output)
            source.write_bytes(b'changed original')
            self.assertEqual(compiler, output / 'extracted/tool')
            self.assertEqual((compiler / 'bin/arm-none-eabi-gcc').read_bytes(), b'synthetic compiler')
            self.assertEqual((compiler / 'bin/arm-none-eabi-g++').read_bytes(), b'synthetic compiler')
            self.assertEqual((output / 'compiler.archive').read_bytes(), payload)
            with self.assertRaises(FileExistsError):
                module.prepare(source, hashlib.sha256(payload).hexdigest(), output)

    def test_wrong_digest_or_unsafe_archive_never_returns_a_toolchain(self):
        module = self.module()
        entries = (GOOD + [('../escape', b'bad', 'file')],
                   GOOD + [('/absolute', b'bad', 'file')],
                   GOOD + [('tool/escape', '../../escape', 'link')],
                   GOOD + [('tool', '.', 'link')],
                   GOOD + [('other/file', b'bad', 'file')],
                   GOOD + [('tool/bin/arm-none-eabi-gcc', b'duplicate', 'file')],
                   [('tool/README', b'no compiler', 'file')])
        for names in entries:
            with self.subTest(entries=names), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); payload = archive(names)
                source = root / 'source.tar.bz2'; source.write_bytes(payload)
                with self.assertRaises(ValueError):
                    module.prepare(source, hashlib.sha256(payload).hexdigest(), root / 'out')
                self.assertFalse((root / 'escape').exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source.tar.bz2'
            source.write_bytes(archive(GOOD))
            with self.assertRaises(ValueError):
                module.prepare(source, '0' * 64, root / 'out')
            self.assertFalse((root / 'out/extracted').exists())

    def test_oversized_hidden_metadata_is_rejected_before_tarfile_reads_it(self):
        module = self.module()
        header = tarfile.TarInfo('././@LongLink')
        header.type = tarfile.GNUTYPE_LONGNAME
        header.size = 65537
        payload = bz2.compress(header.tobuf() + b'x' * 65537 + b'\0' * 2048)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'archive'; source.write_bytes(payload)
            with mock.patch('tarfile.open', side_effect=AssertionError('tar parser reached hidden metadata')) as parser:
                with self.assertRaises(ValueError):
                    module.prepare(source, hashlib.sha256(payload).hexdigest(), root / 'out')
                parser.assert_not_called()

    def test_pax_layout_and_global_metadata_are_rejected_before_python_parser(self):
        module = self.module()
        for kind, body in ((tarfile.XHDTYPE, b'9 size=0\n'), (tarfile.XGLTYPE, b'6 a=b\n')):
            header = tarfile.TarInfo('pax')
            header.type, header.size = kind, len(body)
            payload = bz2.compress(header.tobuf() + body + b'\0' * (512 - len(body)) +
                                   bz2.decompress(archive(GOOD)))
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root / 'archive'; source.write_bytes(payload)
                with mock.patch('tarfile.open', side_effect=AssertionError('PAX reached Python parser')) as parser:
                    with self.assertRaises(ValueError):
                        module.prepare(source, hashlib.sha256(payload).hexdigest(), root / 'out')
                    parser.assert_not_called()
