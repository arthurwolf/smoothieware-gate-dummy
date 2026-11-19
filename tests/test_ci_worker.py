"""Executable CI bootstrap must select only a verified retained controller."""
import importlib.util
import contextlib
import io
from unittest import mock
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import controller_snapshot
from test_dispatch_identity import fixture

ENTRY = Path(__file__).resolve().parents[1] / 'scripts/ci_worker.py'

class CiWorkerTests(unittest.TestCase):
    def test_isolated_help(self):
        result = subprocess.run([sys.executable, '-B', '-I', str(ENTRY), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--execution', result.stdout)

    def test_bootstrap_executes_verified_snapshot_and_rejects_changed_inputs(self):
        for mutation in ('none', 'changed', 'alias', 'cache', 'extension'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); inputs = root/'inputs'; frozen = inputs/'controller-input'
                for directory in ('scripts', 'prompts', 'cases', 'bin'):
                    (frozen/directory).mkdir(parents=True, exist_ok=True)
                (frozen/'scripts/gate.py').write_text('# synthetic fixture\n')
                (frozen/'scripts/ci_worker.py').write_text('import json,sys\nprint(json.dumps({"type":"result","frozen":True,"args":sys.argv[1:]}))\n')
                (frozen/'prompts/prompt').write_text('synthetic')
                (frozen/'cases/case').write_text('synthetic')
                (frozen/'bin/smoothie-gate').write_text('# synthetic')
                snapshot = root/'snapshot'
                digest = controller_snapshot.freeze(frozen, snapshot)
                policy = fixture()[0]; policy['pins']['controller_sha256'] = digest
                (root/'policy.json').write_text(json.dumps(policy))
                if mutation == 'changed': (frozen/'prompts/prompt').write_text('modified')
                if mutation == 'cache':
                    (frozen/'scripts/__pycache__').mkdir()
                    (frozen/'scripts/__pycache__/ci_run.pyc').write_bytes(b'synthetic unchecked executable')
                if mutation == 'extension': (frozen/'scripts/ci_run.so').write_bytes(b'synthetic unchecked executable')
                selected = inputs
                if mutation == 'alias':
                    selected = root/'alias'; selected.symlink_to(inputs, target_is_directory=True)
                command = [sys.executable, '-B', '-I', str(ENTRY)]
                for flag, value in {'policy':root/'policy.json', 'execution':root/'execution.json',
                                    'manifest':root/'manifest.json', 'inputs':selected,
                                    'archive':root/'archive', 'base-tree':root/'base',
                                    'pr-tree':root/'pr', 'diff':root/'diff', 'out':root/'output'}.items():
                    command.extend(('--'+flag, str(value)))
                result = subprocess.run(command, capture_output=True, text=True)
                if mutation == 'none':
                    self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                    event = json.loads(result.stdout)
                    self.assertTrue(event['frozen'])
                    self.assertIn('--controller-sha256', event['args'])
                    self.assertIn(digest, event['args'])
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((root/'output').exists())
                    self.assertEqual(json.loads(result.stdout)['type'], 'error')

    def test_production_retained_entrypoint_reaches_inner_request_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
            digest = controller_snapshot.freeze(ENTRY.parents[1], inputs/'controller-input')
            policy = fixture()[0]; policy['pins']['controller_sha256'] = digest
            (root/'policy.json').write_text(json.dumps(policy))
            (root/'execution.json').write_text('{}')
            (root/'manifest.json').write_text('{}')
            command = [sys.executable, '-B', '-I', str(ENTRY)]
            for flag, value in {'policy':root/'policy.json', 'execution':root/'execution.json',
                                'manifest':root/'manifest.json', 'inputs':inputs,
                                'archive':root/'archive', 'base-tree':root/'base',
                                'pr-tree':root/'pr', 'diff':root/'diff', 'out':root/'output'}.items():
                command.extend(('--'+flag, str(value)))
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            event = json.loads(result.stdout)
            self.assertEqual(event['message'], 'worker preparation is malformed')
            self.assertFalse(event['publication_permitted'])
            self.assertFalse((root/'output').exists())

    def test_worker_prepares_artifact_only_for_current_results(self):
        import ci_worker
        import ci_run
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
            digest = controller_snapshot.freeze(ENTRY.parents[1], inputs/'controller-input')
            policy = fixture()[0]; policy['pins']['controller_sha256'] = digest
            for name, value in (('policy', policy), ('execution', {}), ('manifest', {})):
                (root/(name+'.json')).write_text(json.dumps(value))
            command = []
            for flag, value in {'policy':root/'policy.json', 'execution':root/'execution.json',
                                'manifest':root/'manifest.json', 'inputs':inputs,
                                'archive':root/'archive', 'base-tree':root/'base',
                                'pr-tree':root/'pr', 'diff':root/'diff', 'out':root/'output',
                                'controller-sha256':digest}.items():
                command.extend(('--'+flag, str(value)))
            for result in (ci_run.RunResult(1, True), ci_run.RunResult(2, False, 'stale')):
                with self.subTest(current=result.publication_permitted), mock.patch.object(ci_worker, 'ROOT', inputs/'controller-input'), mock.patch('ci_run.run', return_value=result), mock.patch('ci_artifact.prepare', create=True) as prepare, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(ci_worker.main(command), result.code)
                    self.assertEqual(prepare.call_count, int(result.publication_permitted))

    def test_artifact_failure_never_advertises_ready_path(self):
        import ci_worker
        import ci_run
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); inputs = root/'inputs'; inputs.mkdir()
            digest = controller_snapshot.freeze(ENTRY.parents[1], inputs/'controller-input')
            policy = fixture()[0]; policy['pins']['controller_sha256'] = digest
            for name, value in (('policy', policy), ('execution', {}), ('manifest', {})):
                (root/(name+'.json')).write_text(json.dumps(value))
            command = []
            for flag, value in {'policy':root/'policy.json', 'execution':root/'execution.json',
                                'manifest':root/'manifest.json', 'inputs':inputs,
                                'archive':root/'archive', 'base-tree':root/'base',
                                'pr-tree':root/'pr', 'diff':root/'diff', 'out':root/'output',
                                'controller-sha256':digest}.items():
                command.extend(('--'+flag, str(value)))
            for error in (ValueError('synthetic stale artifact'), OSError('synthetic final API outage')):
                stream = io.StringIO()
                with self.subTest(error=str(error)), mock.patch.object(ci_worker, 'ROOT', inputs/'controller-input'), mock.patch('ci_run.run', return_value=ci_run.RunResult(0, True)), mock.patch('ci_artifact.prepare', side_effect=error), contextlib.redirect_stdout(stream):
                    self.assertEqual(ci_worker.main(command), 2)
                event = json.loads(stream.getvalue())
                self.assertFalse(event['publication_permitted'])
                self.assertNotIn('artifact', event)
