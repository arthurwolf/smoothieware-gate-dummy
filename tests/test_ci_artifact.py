"""Only complete bounded result data may cross into trusted reporting."""
import importlib.util
import json
import copy
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_ci_run as run_fixture
from gate_result import make_result
from profiles import EXPECTED_STAGES
import artifact_safety
import github_worker
from test_github_worker import metadata

class CiArtifactTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ci_artifact'), 'CI result artifact assembler is missing')
        import ci_artifact
        return ci_artifact

    def fixture(self, root, generation=2):
        args, api, pr = run_fixture.CiRunTests().fixture(root, generation)
        results = root/'results'; results.mkdir()
        (results/'manifest.json').write_text(json.dumps(args['manifest']))
        for stage in EXPECTED_STAGES:
            folder = results / ('sim' if stage == 'sim' else 'review' if stage == 'ai-review' else '')
            folder.mkdir(exist_ok=True)
            (folder/(stage+'.result.json')).write_text(json.dumps(make_result(stage, 'pass', stage, 'synthetic pass')))
        (results/'sim/trace-samples.json').write_text('{}')
        (results/'sim/per-case.json').write_text('unselected synthetic case')
        (results/'hostile.elf').write_bytes(b'unselected synthetic executable')
        return results, args['execution']['binding'], pr

    def test_complete_both_profiles_copy_only_report_data(self):
        module = self.module()
        for generation in (1, 2):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source, binding, pr = self.fixture(root, generation)
                output = root/'artifact'
                module.assemble(source, binding, pr, output)
                self.assertEqual(artifact_safety.inspect(output), [])
                names = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
                self.assertEqual(len(names), 10)
                self.assertNotIn('hostile.elf', names)
                self.assertNotIn('sim/per-case.json', names)
                self.assertEqual(json.loads((output/'pr.json').read_text()), pr)

    def test_rejects_invalid_or_incomplete_before_creating_output(self):
        module = self.module()
        for mutation in ('missing', 'stage', 'head', 'pr', 'link', 'oversize'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source, binding, pr = self.fixture(root)
                target = source/'size.result.json'
                if mutation == 'missing': target.unlink()
                elif mutation == 'stage': target.write_text(json.dumps(make_result('sim', 'pass', 'wrong', 'wrong')))
                elif mutation == 'head':
                    data = json.loads((source/'manifest.json').read_text()); data['head'] = '9'*40
                    (source/'manifest.json').write_text(json.dumps(data))
                elif mutation == 'pr': pr['number'] += 1
                elif mutation == 'link': target.unlink(); target.symlink_to(source/'sim/sim.result.json')
                else: (source/'sim/trace-samples.json').write_bytes(b' ' * (5*1024**2+1))
                with self.assertRaises(ValueError): module.assemble(source, binding, pr, root/'artifact')
                self.assertFalse((root/'artifact').exists())

    def test_authenticated_artifact_preparation_rechecks_current_pr(self):
        module = self.module()
        self.assertTrue(callable(getattr(module, 'prepare', None)), 'artifact preparation requires current API identity')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source, binding, original_pr = self.fixture(root)
            policy, run, pr, _, calls, api = metadata(binding['profile'])
            policy['pins'] = copy.deepcopy(binding['execution_pins']); pr.update(copy.deepcopy(original_pr))
            execution = github_worker.prepare(policy, 101, 2, api)
            self.assertEqual(execution['binding'], binding)
            module.prepare(policy, execution, api, source, root/'artifact')
            self.assertEqual(artifact_safety.inspect(root/'artifact'), [])
            pr['head']['sha'] = '9'*40
            with self.assertRaises(ValueError): module.prepare(policy, execution, api, source, root/'stale')
            self.assertFalse((root/'stale').exists())

    def test_isolated_artifact_help(self):
        entry = Path(__file__).resolve().parents[1]/'scripts/ci_artifact.py'
        done = subprocess.run([sys.executable, '-B', '-I', str(entry), '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('--execution', done.stdout)

    def test_final_confirmation_refuses_changed_pr_and_transport_outage(self):
        module = self.module()
        for failure in ('identity', 'transport'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); source, binding, original_pr = self.fixture(root)
                policy, run, pr, _, calls, api = metadata(binding['profile'])
                policy['pins'] = copy.deepcopy(binding['execution_pins']); pr.update(copy.deepcopy(original_pr))
                execution = github_worker.prepare(policy, 101, 2, api)
                pr_reads = 0
                def changing(route):
                    nonlocal pr_reads
                    if '/pulls/' in route:
                        pr_reads += 1
                        if pr_reads == 3:
                            if failure == 'transport': raise OSError('synthetic final API outage')
                            pr['head']['sha'] = '9'*40
                    return api(route)
                with self.assertRaises((ValueError, OSError)):
                    module.prepare(policy, execution, changing, source, root/'artifact')
                self.assertTrue((root/'artifact/manifest.json').is_file())
