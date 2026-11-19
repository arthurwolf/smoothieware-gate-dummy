"""Workflow bootstrap binds configured refs and compiler inputs before execution."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from test_ci_inputs import inputs

class GithubConfigTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('github_config'), 'workflow configuration adapter is missing')
        import github_config
        return github_config

    def test_profiles_select_only_canonical_compiler_and_pinned_sources(self):
        module = self.module()
        for generation, compiler in ((1, 'gcc48'), (2, 'gcc10')):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                policy, binding, manifest, root, archive = inputs(Path(directory), generation)
                if generation == 2: policy['pins']['shared_library_ref'] = '9'*40
                config = module.configuration(policy, root/'controller-input', policy['repository'], policy['pins']['gate_ref'], policy['pins']['sim_ref'])
                self.assertEqual(config['toolchain'], compiler)
                self.assertEqual(config['profile'], policy['profile'])
                self.assertEqual(config['runtime_image'], policy['pins']['runtime_image'])
                self.assertEqual(config['toolchain_sha256'], policy['pins']['toolchain_sha256'])
                self.assertTrue(config['toolchain_url'].startswith('https://'))
                self.assertEqual(config['shared_library_ref'], '9'*40 if generation == 2 else None)

    def test_wrong_repository_refs_and_incomplete_pins_are_rejected(self):
        module = self.module()
        for mutation in ('repository', 'gate-ref', 'sim-ref', 'compiler', 'simulator', 'library-ref', 'controller'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                policy, binding, manifest, root, archive = inputs(Path(directory), 2)
                policy['pins']['shared_library_ref'] = '9'*40
                repository = policy['repository']; gate_ref = policy['pins']['gate_ref']; sim_ref = policy['pins']['sim_ref']
                if mutation == 'repository': repository = 'other/firmware'
                elif mutation == 'gate-ref': gate_ref = '9'*40
                elif mutation == 'sim-ref': sim_ref = '9'*40
                elif mutation == 'compiler': policy['pins']['toolchain_sha256'] = '9'*64
                elif mutation == 'simulator': policy['pins'].pop('simulator_sha256')
                elif mutation == 'library-ref': policy['pins'].pop('shared_library_ref')
                else: (root/'controller-input/prompts/review.md').write_text('changed controller')
                with self.assertRaises(ValueError): module.configuration(policy, root/'controller-input', repository, gate_ref, sim_ref)

    def test_isolated_help(self):
        entry = Path(__file__).resolve().parents[1]/'scripts/github_config.py'
        done = subprocess.run([sys.executable, '-B', '-I', str(entry), '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('--gate-ref', done.stdout)
