"""CI builds consume exact committed bytes, never dirty checkout contents."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import revision_metadata
import github_worker
import ai_review
from test_github_worker import metadata

class CiSourcesTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ci_sources'), 'authenticated CI source exporter is missing')
        import ci_sources
        return ci_sources

    def repository(self, root, malicious=None):
        repo = root/'repo'; repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()
        git('init', '-q'); git('config', 'user.name', 'Synthetic Fixture'); git('config', 'user.email', 'fixture@example.invalid')
        (repo/'Firmware').mkdir(); (repo/'Firmware/source.cpp').write_text('committed base')
        (repo/'Firmware/link').symlink_to('source.cpp' if malicious is None else malicious)
        git('add', '.'); git('commit', '-qm', 'labelled synthetic base'); base = git('rev-parse', 'HEAD')
        (repo/'Firmware/source.cpp').write_text('committed head'); git('commit', '-qam', 'labelled synthetic head')
        head = git('rev-parse', 'HEAD')
        (repo/'Firmware/source.cpp').write_text('dirty input must remain untouched')
        (repo/'untracked').write_text('untracked input must remain untouched')
        return repo, base, head

    def test_exact_independent_committed_exports_and_version_metadata(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo, base, head = self.repository(root)
            for label, revision, expected in (('base', base, 'committed base'), ('pr', head, 'committed head')):
                output = root/label
                module.export(repo, revision, output, generation=2)
                self.assertEqual((output/'Firmware/source.cpp').read_text(), expected)
                self.assertTrue((output/'Firmware/link').is_symlink())
                self.assertEqual((output/'Firmware/link').read_text(), expected)
                self.assertFalse((output/'untracked').exists())
                revision_metadata.validate(output, revision)
            self.assertEqual((repo/'Firmware/source.cpp').read_text(), 'dirty input must remain untouched')
            self.assertTrue((repo/'untracked').exists())

    def test_wrong_revision_and_escaping_link_are_rejected(self):
        module = self.module()
        for mutation in ('revision', 'link', 'occupied'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); repo, base, head = self.repository(root, '../../outside' if mutation == 'link' else None)
                output = root/'export'
                if mutation == 'occupied': output.mkdir(); (output/'owner').write_text('preserve')
                with self.assertRaises(ValueError): module.export(repo, '9'*40 if mutation == 'revision' else head, output, generation=1)
                if mutation == 'occupied': self.assertEqual((output/'owner').read_text(), 'preserve')

    def test_v2_metadata_uses_authenticated_commit_despite_replacement_refs(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo, base, head = self.repository(root)
            subprocess.run(['git', '-C', str(repo), 'replace', base, head], check=True)
            output = root/'export'
            module.export(repo, base, output, generation=2)
            self.assertEqual((output/'Firmware/source.cpp').read_text(), 'committed base')
            revision_metadata.validate(output, base)

    def test_authenticated_preparation_exports_sources_and_committed_diff(self):
        module = self.module()
        self.assertTrue(callable(getattr(module, 'prepare', None)), 'source preparation must authenticate before selection')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo, base, head = self.repository(root)
            policy, run, pr, _, calls, api = metadata()
            pr['base']['sha'] = base; pr['head']['sha'] = head
            execution = github_worker.prepare(policy, 101, 2, api)
            paths = module.prepare(policy, execution, api, repo, repo, root/'prepared')
            self.assertEqual((paths['base']/'Firmware/source.cpp').read_text(), 'committed base')
            self.assertEqual((paths['pr']/'Firmware/source.cpp').read_text(), 'committed head')
            patch = paths['diff'].read_text()
            self.assertIn('--- a/Firmware/source.cpp', patch)
            self.assertIn('-committed base', patch)
            self.assertIn('+committed head', patch)
            self.assertNotIn('dirty input', patch)
            self.assertNotIn('.gate-git', patch)
            pr['head']['sha'] = '9'*40
            with self.assertRaises(ValueError): module.prepare(policy, execution, api, repo, repo, root/'stale')
            self.assertFalse((root/'stale').exists())

    def test_isolated_source_preparation_help(self):
        entry = Path(__file__).resolve().parents[1]/'scripts/ci_sources.py'
        done = subprocess.run([sys.executable, '-B', '-I', str(entry), '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('--execution', done.stdout)

    def test_attributes_and_quoted_names_cannot_hide_forbidden_additions(self):
        module = self.module()
        for name, binary in (('source.cpp', True), ('moteur-é.cpp', False), ('motor"quoted.cpp', False), ('prefix.cpp', False)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); repo, base, head = self.repository(root)
                (repo/'Firmware'/name).write_text(('++ ignored;\n' if name == 'prefix.cpp' else '') + 'wait(5);\n')
                if binary: (repo/'.gitattributes').write_text('*.cpp -diff\n')
                subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
                subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'synthetic forbidden addition'], check=True)
                head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
                policy, run, pr, _, calls, api = metadata('v1-lpc1768')
                pr['base']['sha'] = base; pr['head']['sha'] = head
                execution = github_worker.prepare(policy, 101, 2, api)
                paths = module.prepare(policy, execution, api, repo, repo, root/'prepared')
                sim = Path(__file__).resolve().parents[2]/'smoothie-sim'
                hits = ai_review.scan_added(ai_review.parse_diff(paths['diff'].read_text()),
                                            ai_review.load_generation_rules(sim, 1), ai_review.load_rule_for(sim))
                self.assertTrue(any(hit['file'] == 'Firmware/'+name and hit['blocking'] for hit in hits), hits)

    def test_record_separators_cannot_hide_forbidden_additions(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo, base, head = self.repository(root)
            names = []
            for label, separator in (('ff', '\f'), ('cr', '\r'), ('vt', '\v'), ('nel', '\x85'), ('ls', '\u2028'), ('ps', '\u2029')):
                name = 'Firmware/separator-'+label+'.cpp'; names.append(name)
                (repo/name).write_text('/*'+separator+'diff --git a/ignored b/ignored\n*/\nwait(5);\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'synthetic separator controls'], check=True)
            head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
            policy, run, pr, _, calls, api = metadata('v1-lpc1768')
            pr['base']['sha'] = base; pr['head']['sha'] = head
            paths = module.prepare(policy, github_worker.prepare(policy, 101, 2, api), api, repo, repo, root/'prepared')
            review = root/'review'; review.mkdir()
            sim = Path(__file__).resolve().parents[2]/'smoothie-sim'
            with contextlib.redirect_stdout(io.StringIO()):
                code = ai_review.main(['--diff', str(paths['diff']), '--out', str(review), '--no-model', '--sim-dir', str(sim)])
            findings = json.loads((review/'ai-review.findings.json').read_text())
            self.assertEqual({hit['file'] for hit in findings['scan'] if hit['blocking']}, set(names))
            self.assertEqual(code, 1)

    def test_local_diff_uses_same_safe_text_boundary(self):
        import gate
        self.assertTrue(callable(getattr(gate, 'write_review_diff', None)), 'local review needs the same safe textual diff boundary')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo, base, head = self.repository(root)
            (repo/'.gitattributes').write_text('*.cpp -diff\n')
            (repo/'Firmware/source.cpp').write_text('wait(5);\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'synthetic local review control'], check=True)
            head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
            patch = root/'local.patch'; gate.write_review_diff(repo, base, head, patch)
            sim = Path(__file__).resolve().parents[2]/'smoothie-sim'
            with patch.open(newline='') as handle:
                hits = ai_review.scan_added(ai_review.parse_diff(handle.read()), ai_review.load_generation_rules(sim, 1), ai_review.load_rule_for(sim))
            self.assertTrue(any(hit['file'] == 'Firmware/source.cpp' and hit['blocking'] for hit in hits), hits)
