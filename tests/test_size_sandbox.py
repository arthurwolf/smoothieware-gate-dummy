"""Host formatting consumes bounded data, never an ELF parser or child status."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sandbox_process import ProcessOutput

DATA = {'rows': [
    {'memory': 'Flash', 'reference': 600000, 'candidate': 600004, 'delta': 4},
    {'memory': 'RAM', 'reference': 100, 'candidate': 99, 'delta': -1},
    {'memory': 'AHB RAM', 'reference': 20, 'candidate': 20, 'delta': 0}],
    'top_symbol_changes': [], 'status': 'fail', 'summary': 'untrusted claim'}


class SizeParserBoundary(unittest.TestCase):
    def test_alternate_encoding_cannot_bypass_nesting_guard(self):
        import size_sandbox
        nested = 0
        for _ in range(33): nested = [nested]
        data = {'quoted': '"', 'deep': nested, **DATA}
        for encoding in ('utf-16', 'utf-16-le', 'utf-32'):
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                size_sandbox.decode_result(json.dumps(data).encode(encoding), 'lpc1769')

    def test_parser_recursion_failure_is_a_validation_error(self):
        import size_sandbox
        with mock.patch.object(size_sandbox.json, 'loads', side_effect=RecursionError('parser recursion')):
            with self.assertRaises(ValueError):
                size_sandbox.decode_result(json.dumps(DATA).encode(), 'lpc1769')

    def test_oversized_debug_counts_survive_but_child_status_is_discarded(self):
        import size_sandbox
        result = size_sandbox.decode_result(json.dumps(DATA).encode(), 'lpc1769')
        self.assertEqual(result['rows'][0], {'memory': 'Flash', 'reference': 600000, 'candidate': 600004, 'delta': 4})
        self.assertNotIn('status', result)
        self.assertNotIn('summary', result)

    def test_malformed_banks_counts_deltas_and_symbols_are_rejected(self):
        import size_sandbox
        mutations = []
        changed = copy.deepcopy(DATA); changed['rows'].reverse(); mutations.append(changed)
        changed = copy.deepcopy(DATA); changed['rows'].pop(); mutations.append(changed)
        changed = copy.deepcopy(DATA); changed['rows'][0].update(reference=0, candidate=True, delta=1); mutations.append(changed)
        changed = copy.deepcopy(DATA); changed['rows'][0]['delta'] = 3; mutations.append(changed)
        changed = copy.deepcopy(DATA); changed['rows'][0].update(reference=2**60, candidate=2**60+4); mutations.append(changed)
        changed = copy.deepcopy(DATA); changed['rows'][0].update(reference=-1, candidate=3); mutations.append(changed)
        for symbol in ('x' * 8193, '\ud800', 'bad\0name'):
            changed = copy.deepcopy(DATA)
            changed['top_symbol_changes'] = [{'symbol': symbol, 'reference': 1, 'candidate': 2, 'delta': 1}]
            mutations.append(changed)
        changed = copy.deepcopy(DATA)
        changed['top_symbol_changes'] = [{'symbol': str(i), 'reference': 1, 'candidate': 2, 'delta': 1} for i in range(16)]
        mutations.append(changed)
        for data in mutations:
            with self.subTest(data=str(data)[:120]), self.assertRaises(ValueError):
                size_sandbox.decode_result(json.dumps(data).encode(), 'lpc1769')

    def test_duplicate_keys_nonfinite_numbers_and_excess_nesting_are_rejected(self):
        import size_sandbox
        tail = json.dumps(DATA).encode()[1:]
        for prefix in (b'{"ignored":1,"ignored":2,', b'{"ignored":NaN,', b'{"ignored":1e999,',
                       b'{"ignored":' + b'[' * 33 + b'0' + b']' * 33 + b','):
            with self.subTest(prefix=prefix[:80]), self.assertRaises(ValueError):
                size_sandbox.decode_result(prefix + tail, 'lpc1769')
        with self.assertRaises(ValueError):
            size_sandbox.decode_result(b' ' * (4 * 1024**2 + 1), 'lpc1769')

    def test_v2_requires_each_bank_and_occupied_extent(self):
        import size_sandbox
        names = ['Flash', 'ITCM', 'DTCM', 'AXI SRAM', 'SRAM1', 'SRAM2', 'SRAM3', 'SRAM4']
        data = {'rows': [{'memory': name, 'reference': 0, 'candidate': 0, 'delta': 0,
                          'reference_footprint': 0, 'candidate_footprint': 0} for name in names],
                'top_symbol_changes': []}
        result = size_sandbox.decode_result(json.dumps(data).encode(), 'stm32h745')
        self.assertEqual([row['memory'] for row in result['rows']], names)
        del data['rows'][3]['candidate_footprint']
        with self.assertRaises(ValueError):
            size_sandbox.decode_result(json.dumps(data).encode(), 'stm32h745')

    def fixture(self, root):
        sim = root / 'sim'; (sim / 'checkers').mkdir(parents=True)
        (sim / 'checkers' / 'size_check.py').write_bytes(b'# trusted checker fixture')
        for name in ('base.elf', 'head.elf'):
            (root / name).write_bytes(b'ELF fixture')
        compiler = root / 'compiler'; compiler.mkdir()
        return sim, compiler

    def test_runtime_receives_only_checker_and_elf_inputs(self):
        import size_sandbox
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sim, compiler = self.fixture(root)
            def execute(source, arguments, **options):
                self.assertEqual(sorted(path.name for path in source.iterdir()), ['base.elf', 'head.elf', 'size_check.py'])
                self.assertEqual((source / 'base.elf').read_bytes(), b'ELF fixture')
                self.assertEqual(options['workspace_bytes'], 134217728)
                self.assertEqual(options['max_stdout_bytes'], 4194304)
                self.assertEqual(options['toolchain'], compiler)
                return ProcessOutput(0, json.dumps(DATA).encode(), b'tail', False, None)
            runtime = mock.Mock(); runtime.run.side_effect = execute
            result = size_sandbox.compare(sim, root / 'base.elf', root / 'head.elf', root,
                                          architecture='lpc1769', compiler=compiler, runtime=runtime)
            self.assertEqual(result['rows'][0]['delta'], 4)

    def test_runtime_failure_ignores_forged_json_and_retains_recovery_identity(self):
        import size_sandbox
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sim, compiler = self.fixture(root)
            runtime = mock.Mock()
            runtime.run.return_value = ProcessOutput(75, json.dumps(DATA).encode(),
                b'Unverified owned container: gate-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', False, 'container_cleanup_unverified')
            with self.assertRaisesRegex(ValueError, 'gate-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'):
                size_sandbox.compare(sim, root / 'base.elf', root / 'head.elf', root,
                                     architecture='lpc1769', compiler=compiler, runtime=runtime)

    def test_linked_elf_is_rejected_before_execution(self):
        import size_sandbox
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sim, compiler = self.fixture(root)
            (root / 'base.elf').unlink(); (root / 'base.elf').symlink_to(root / 'head.elf')
            runtime = mock.Mock()
            with self.assertRaises(ValueError):
                size_sandbox.compare(sim, root / 'base.elf', root / 'head.elf', root,
                                     architecture='lpc1769', compiler=compiler, runtime=runtime)
            self.assertEqual(runtime.run.call_count, 0)
