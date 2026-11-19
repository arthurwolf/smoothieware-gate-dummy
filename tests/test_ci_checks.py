"""Post-build CI stages consume bounded runtime data and preserve complete reports."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from profiles import PROFILES, TOOLCHAIN_VERSIONS, EXPECTED_STAGES
from sandbox_process import ProcessOutput
from test_sim_stage import result as motion_result, step_trace, SIM_DIR
from test_size_sandbox import DATA
from gate_result import make_result

ROOT = Path(__file__).resolve().parents[1]


def motion_data():
    data = motion_result()
    data['blocks'][0].update(steps_event_count=800, nominal_speed=20, nominal_rate=1600,
                             millimeters=10, acceleration=100, accelerate_until=100,
                             decelerate_after=200, total_move_ticks=20000, max_entry_speed=0, is_g123=True)
    return data


class Runtime:
    def __init__(self, generation, broken=False):
        self.generation, self.broken, self.calls = generation, broken, []

    def run(self, source, arguments, **options):
        self.calls.append((source, arguments, options))
        if self.broken:
            return ProcessOutput(75, b'', b'Unverified owned container: gate-synthetic', False,
                                 'container_cleanup_unverified')
        if 'gate-size' in arguments:
            if self.generation == 1: data = copy.deepcopy(DATA)
            else:
                data = {'rows': [{'memory': name, 'reference': 0, 'candidate': 0, 'delta': 0,
                                  'reference_footprint': 0, 'candidate_footprint': 0}
                                 for name in ('Flash', 'ITCM', 'DTCM', 'AXI SRAM', 'SRAM1', 'SRAM2', 'SRAM3', 'SRAM4')],
                        'top_symbol_changes': []}
            return ProcessOutput(0, json.dumps(data).encode(), b'', False, None)
        if 'gate-replay' not in arguments: raise AssertionError('unexpected runtime operation')
        return ProcessOutput(0, json.dumps(motion_data()).encode(), b'', False, None)


class CiChecksTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ci_checks'), 'CI post-build coordinator is missing')
        import ci_checks
        return ci_checks

    def fixture(self, root, generation=1):
        profile = PROFILES['v2-prime' if generation == 2 else 'v1-lpc1768']
        cases = root / 'cases'; cases.mkdir()
        selected = cases / 'v2' if generation == 2 else cases
        case = selected / 'one'; case.mkdir(parents=True)
        for name, data in {'config.txt': '# synthetic', 'input.gcode': 'G0 X10',
                           'expected.trace': step_trace.record(motion_data()),
                           'meta.json': json.dumps({'reference_status': 'reviewed', 'approved_by': 'synthetic reviewer',
                                                    'review': 'Labelled unit fixture, not a real reference.'})}.items():
            (case / name).write_text(data)
        if generation == 2:
            baseline = cases / 'one'; baseline.mkdir(); (baseline / 'config.txt').write_text('# synthetic baseline')
            (selected / 'coverage.json').write_text(json.dumps({'required_scenarios': ['one'],
                'scenarios': {'one': {'status': 'covered', 'case': 'one'}}}))
        compiler = root / 'compiler'; compiler.mkdir()
        retained_root = root / 'retained'; retained_root.mkdir()
        for name in ('lib-smoothie', 'case-inventory'): (retained_root / name).mkdir()
        prepared = SimpleNamespace(retained_root=retained_root, profile=profile, compiler=compiler, simulator=Path(SIM_DIR),
                                   controller=ROOT, cases=cases, revisions={'base': '2'*40, 'pr': '3'*40})
        elfs = {}
        records = []
        for key in ('base-release', 'base-debug', 'pr-release', 'pr-debug'):
            path = root / (key + '.elf'); path.write_bytes(b'Labelled synthetic ELF transport fixture'); elfs[key] = path
            records.append(make_result('build-' + key, 'pass', 'Firmware build', 'synthetic fixture'))
        simulators = {}
        for key in ('base', 'pr', 'base-five', 'pr-five'):
            path = root / ('sim-' + key); path.write_bytes(b'Labelled synthetic executable'); path.chmod(0o500)
            simulators[key] = path
        batch = SimpleNamespace(stopped=False, elfs=elfs, simulators=simulators, records=records, simulator_errors={})
        manifest = {'profile': profile.name, 'board': profile.board, 'firmware_repository': profile.repository,
                    'toolchain': profile.toolchain, 'toolchain_version': TOOLCHAIN_VERSIONS[profile.toolchain],
                    'base': '2'*40, 'head': '3'*40, 'controller_sha256': '4'*64, 'simulator_revision': '5'*40,
                    'simulator_sha256': '6'*64, 'references_sha256': '7'*64, 'case_inventory_sha256': '7'*64,
                    'expected_stages': list(EXPECTED_STAGES)}
        diff = root / 'pr.diff'; diff.write_text('diff --git a/src/Example.cpp b/src/Example.cpp\n+++ b/src/Example.cpp\n@@ -0,0 +1 @@\n+int synthetic_example = 0;\n')
        return prepared, batch, manifest, diff

    def test_both_profiles_produce_complete_reports_without_model_network(self):
        module = self.module()
        import ai_review
        for generation in (1, 2):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, batch, manifest, diff = self.fixture(root, generation)
                runtime = Runtime(generation)
                with mock.patch.object(ai_review.ModelClient, 'complete', side_effect=AssertionError('model network forbidden')):
                    code = module.run(prepared, batch, manifest, diff, root / 'checks', runtime)
                self.assertEqual(code, 0)
                records = [json.loads(path.read_text()) for path in (root / 'checks/results').rglob('*.result.json')]
                self.assertEqual({record['stage'] for record in records}, set(EXPECTED_STAGES))
                self.assertTrue((root / 'checks/report/comment.md').is_file())
                self.assertEqual(len(runtime.calls), 3)
                self.assertTrue((root / 'checks/report/charts/size-release.svg').is_file())
                review = next(record for record in records if record['stage'] == 'ai-review')
                self.assertEqual(review['metrics']['requests_expected'], 0)

    def test_size_cleanup_uncertainty_prevents_replay_and_keeps_complete_error_report(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, batch, manifest, diff = self.fixture(root)
            runtime = Runtime(1, broken=True)
            code = module.run(prepared, batch, manifest, diff, root / 'checks', runtime)
            self.assertEqual(code, 2)
            self.assertEqual(len(runtime.calls), 1)
            records = {record['stage']: record for record in
                       (json.loads(path.read_text()) for path in (root / 'checks/results').rglob('*.result.json'))}
            self.assertEqual(set(records), set(EXPECTED_STAGES))
            self.assertEqual(records['size']['status'], 'error')
            self.assertEqual(records['sim']['status'], 'error')
            self.assertIn('gate-synthetic', records['size']['details'])

    def test_missing_builds_do_not_claim_size_or_simulator_success(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, batch, manifest, diff = self.fixture(root)
            batch.stopped = True; batch.elfs.clear(); batch.simulators.clear()
            batch.records[0] = make_result('build-base-release', 'error', 'Firmware build', 'synthetic infrastructure problem')
            runtime = Runtime(1)
            code = module.run(prepared, batch, manifest, diff, root / 'checks', runtime)
            self.assertEqual(code, 2)
            self.assertEqual(runtime.calls, [])

    def test_missing_runtime_rejects_before_output_or_execution(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared, batch, manifest, diff = self.fixture(root)
            output = root / 'checks'
            with mock.patch('sim_stage.subprocess.run', side_effect=AssertionError('host execution forbidden')):
                with self.assertRaises(ValueError): module.run(prepared, batch, manifest, diff, output, None)
            self.assertFalse(output.exists())

    def test_output_preserves_all_retained_sibling_inputs(self):
        module = self.module()
        for name in ('lib-smoothie', 'case-inventory'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, batch, manifest, diff = self.fixture(root)
                output = prepared.retained_root / name / 'new-checks'
                with self.assertRaises(ValueError): module.run(prepared, batch, manifest, diff, output, Runtime(1))
                self.assertFalse(output.exists())

    def test_hostile_simulator_data_keeps_all_stages_and_report(self):
        module = self.module()
        infinite = motion_data(); infinite['final_steps'][0] = float('inf')
        for payload in (b'[' * 2000 + b'0' + b']' * 2000, json.dumps(infinite).encode()):
            with self.subTest(payload=payload[:30]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, batch, manifest, diff = self.fixture(root)
                class Hostile(Runtime):
                    def run(self, source, arguments, **options):
                        if 'gate-size' in arguments: return super().run(source, arguments, **options)
                        return ProcessOutput(0, payload, b'', False, None)
                code = module.run(prepared, batch, manifest, diff, root / 'checks', Hostile(1))
                self.assertEqual(code, 2)
                records = [json.loads(path.read_text()) for path in (root / 'checks/results').rglob('*.result.json')]
                self.assertEqual({record['stage'] for record in records}, set(EXPECTED_STAGES))
                self.assertTrue((root / 'checks/report/comment.md').is_file())

    def test_retry_and_chart_cleanup_uncertainty_preserve_owned_container(self):
        module = self.module()
        for failure_at in (2, 3):
            with self.subTest(failure_at=failure_at), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared, batch, manifest, diff = self.fixture(root)
                class Uncertain(Runtime):
                    replay_count = 0
                    def run(self, source, arguments, **options):
                        if 'gate-size' in arguments: return super().run(source, arguments, **options)
                        self.replay_count += 1
                        if self.replay_count >= failure_at:
                            return ProcessOutput(75, b'', b'Unverified owned container: gate-synthetic', False,
                                                 'container_cleanup_unverified')
                        data = motion_data(); data['final_steps'][0] = 801
                        data['blocks'][0]['steps'][0] = 801; data['blocks'][0]['step_counts'][0] = 801
                        return ProcessOutput(0, json.dumps(data).encode(), b'', False, None)
                runtime = Uncertain(1)
                module.run(prepared, batch, manifest, diff, root / 'checks', runtime)
                sim = json.loads((root / 'checks/results/sim/sim.result.json').read_text())
                self.assertIn('gate-synthetic', sim['details'])
                self.assertEqual(runtime.replay_count, failure_at)
