"""Untrusted final-file bytes cannot choose host paths or bypass size limits."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox_transport


class FinalFileTransport(unittest.TestCase):
    def test_decodes_exact_named_files_in_trusted_contract_order(self):
        payload = b'{"firmware.bin":3,"firmware.elf":4}\nELF!bin'
        self.assertEqual(sandbox_transport.decode(payload, {'firmware.elf': 10, 'firmware.bin': 10}),
                         {'firmware.elf': b'ELF!', 'firmware.bin': b'bin'})

    def test_rejects_unexpected_duplicate_and_invalid_file_declarations(self):
        for payload in (b'{"../escape":3}\nabc', b'{"build.result.json":3}\nabc',
                        b'{"firmware.bin":3,"firmware.bin":3}\nabc',
                        b'{"firmware.bin":true}\nx', b'{"firmware.bin":-1}\n',
                        b'{"firmware.bin":0}\n', b'{"firmware.bin":5}\nabcde',
                        b'{"firmware.bin":"3"}\nabc', b'[]\n', b'{"firmware.bin":{}}\n'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                sandbox_transport.decode(payload, {'firmware.bin': 4})

    def test_rejects_truncated_extra_oversize_and_malformed_transport(self):
        for payload in (b'{"firmware.bin":4}\nabc', b'{"firmware.bin":3}\nabcd',
                        b'{"firmware.bin":3}', b'\xff\nabc', b'no json\nabc',
                        b'x' * 4097 + b'\n', b'[' * 2000 + b']' * 2000 + b'\n'):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                sandbox_transport.decode(payload, {'firmware.bin': 4})
        with self.assertRaises(ValueError):
            sandbox_transport.decode(b'{"a":3,"b":3}\nabcdef', {'a': 4, 'b': 4}, max_total_bytes=5)

    def test_invalid_trusted_contracts_are_rejected(self):
        for contract in ({}, {'../escape': 4}, {'a': True}, {'a': 0}):
            with self.subTest(contract=contract), self.assertRaises(ValueError):
                sandbox_transport.decode(b'{}\n', contract)

    def test_header_must_use_utf8_rather_than_autodetected_json_encoding(self):
        for encoding in ('utf-16', 'utf-32'):
            payload = '{"firmware.bin":3}'.encode(encoding) + b'\nabc'
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                sandbox_transport.decode(payload, {'firmware.bin': 4})
