"""A controller copy must keep executing its frozen helpers and prompt bytes."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest
from argparse import Namespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class ControllerSnapshotTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('controller_snapshot'),
                             'controller execution inputs need a retained snapshot boundary')
        return importlib.import_module('controller_snapshot')

    def fixture(self, root):
        source = root / 'live'
        for name in ('scripts', 'prompts', 'cases', 'bin'):
            (source / name).mkdir(parents=True)
        (source / 'scripts/gate.py').write_text('from pathlib import Path\nprint((Path(__file__).parents[1]/"prompts/review.md").read_text())\n')
        (source / 'scripts/helper.py').write_text('VALUE = 1\n')
        (source / 'prompts/review.md').write_text('original prompt')
        (source / 'cases/config').write_text('original case')
        (source / 'bin/smoothie-gate').write_text('#!/bin/sh\n')
        (source / 'bin/smoothie-gate').chmod(0o755)
        return source

    def test_frozen_execution_is_independent_of_live_edits(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); frozen = root / 'frozen'
            digest = api.freeze(source, frozen)
            (source / 'prompts/review.md').write_text('mutated prompt')
            (source / 'scripts/gate.py').write_text('raise RuntimeError("live script")')
            done = subprocess.run([sys.executable, '-B', '-I', str(frozen / 'scripts/gate.py')],
                                  capture_output=True, text=True, check=True)
            self.assertEqual(done.stdout.strip(), 'original prompt')
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
            api.verify(frozen, digest)

    def test_identity_detects_bytes_names_and_executable_modes(self):
        api = self.api()
        for mutation in ('bytes', 'name', 'mode', 'extra'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); source = self.fixture(root); frozen = root / 'frozen'
                digest = api.freeze(source, frozen)
                path = frozen / 'scripts/helper.py'
                if mutation == 'bytes': path.write_text('VALUE = 2\n')
                elif mutation == 'name': path.rename(path.with_name('other.py'))
                elif mutation == 'mode': path.chmod(0o755)
                else: (frozen / 'scripts/extra.py').write_text('VALUE = 3\n')
                with self.assertRaises(ValueError): api.verify(frozen, digest)

    def test_cache_is_excluded_and_links_are_rejected(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root)
            cache = source / 'scripts/__pycache__'; cache.mkdir(); (cache / 'gate.pyc').write_bytes(b'cache')
            api.freeze(source, root / 'frozen')
            self.assertFalse((root / 'frozen/scripts/__pycache__').exists())
            (source / 'prompts/link').symlink_to(source / 'prompts/review.md')
            with self.assertRaises(ValueError): api.freeze(source, root / 'rejected')

    def test_occupied_destination_is_preserved(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); destination = root / 'existing'
            destination.mkdir(); marker = destination / 'owner'; marker.write_text('preserve')
            with self.assertRaises(FileExistsError): api.freeze(source, destination)
            self.assertEqual(marker.read_text(), 'preserve')

    def test_restart_uses_frozen_entry_point_and_original_absolute_paths(self):
        api = self.api()
        self.assertTrue(callable(getattr(api, 'enter', None)), 'run entry needs a frozen-process restart')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); out = root / 'output'
            args = Namespace(repo='relative-repo', sim_dir='relative-sim', cases='relative-cases',
                             out=str(out), profile='v2-prime', toolchain='gcc10', base='BASE',
                             head='HEAD', no_model=True, controller_sha256=None)
            with mock.patch.object(os, 'execv', side_effect=RuntimeError('intercept restart')) as execute:
                with self.assertRaisesRegex(RuntimeError, 'intercept restart'): api.enter(source, args)
            executable, command = execute.call_args.args
            self.assertEqual((executable, command[:3]), (sys.executable, [sys.executable, '-B', '-I']))
            self.assertEqual(command[3], str(out / 'work/controller-input/scripts/gate.py'))
            for flag, value in (('--repo', 'relative-repo'), ('--sim-dir', 'relative-sim'), ('--cases', 'relative-cases')):
                self.assertEqual(command[command.index(flag) + 1], os.path.abspath(value))
            self.assertIn('--no-model', command)
            args.controller_sha256 = command[command.index('--controller-sha256') + 1]
            frozen = out / 'work/controller-input'
            self.assertEqual(api.enter(frozen, args), args.controller_sha256)
            (source / 'prompts/review.md').write_text('live mutation')
            done = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(done.stdout.strip(), 'original prompt')
            (frozen / 'prompts/review.md').write_text('frozen mutation')
            with self.assertRaises(ValueError): api.enter(frozen, args)

    def test_restart_rejects_existing_run_and_forged_location(self):
        api = self.api()
        self.assertTrue(callable(getattr(api, 'enter', None)), 'run entry needs a frozen-process restart')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = self.fixture(root); out = root / 'existing'; out.mkdir()
            args = Namespace(repo='repo', sim_dir='sim', cases=None, out=str(out), profile='v1-lpc1768',
                             toolchain='gcc48', base='BASE', head='HEAD', no_model=False, controller_sha256=None)
            with self.assertRaises(FileExistsError): api.enter(source, args)
            args.controller_sha256 = 'a' * 64
            with self.assertRaises(ValueError): api.enter(source, args)

    def test_production_bootstrap_freezes_before_firmware_work_for_both_profiles(self):
        # Removing enter() from real gate.main must fail this control. Only the
        # expensive firmware boundary is replaced inside an isolated fixture.
        for profile, toolchain in (('v1-lpc1768', 'gcc48'), ('v2-prime', 'gcc10')):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); source = self.fixture(root)
                scripts = Path(__file__).resolve().parents[1] / 'scripts'
                for path in scripts.glob('*.py'):
                    shutil.copyfile(path, source / 'scripts' / path.name)
                entry = source / 'scripts/gate.py'
                interception = '''
def git(repo, *refs):
    print(json.dumps({'root': ROOT, 'repo': repo, 'argv': sys.argv}))
    raise SystemExit(0)

'''
                entry.write_text(entry.read_text().replace('if __name__ == "__main__":', interception + 'if __name__ == "__main__":'))
                environment = os.environ.copy(); environment.pop('SMOOTHIE_SIM', None)
                command = [sys.executable, '-B', '-I', str(entry), 'run', '--repo', 'firmware',
                           '--base', 'BASE', '--profile', profile, '--out', 'output', '--no-model']
                done = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, check=True)
                observed = json.loads(done.stdout)
                self.assertEqual(observed['root'], str(root / 'output/work/controller-input'))
                self.assertEqual(observed['repo'], str(root / 'firmware'))
                arguments = observed['argv']
                self.assertEqual(arguments[arguments.index('--sim-dir') + 1], str(root / 'smoothie-sim'))
                self.assertEqual(arguments[arguments.index('--toolchain') + 1], toolchain)
                self.api().verify(observed['root'], arguments[arguments.index('--controller-sha256') + 1])
                # Deliberately bypass production bootstrap in this fixture: the
                # same observation must expose execution from the live tree.
                entry.write_text(entry.read_text().replace(
                    'controller_sha256 = controller_snapshot.enter(ROOT, args)',
                    "controller_sha256 = '0' * 64"))
                command[command.index('--out') + 1] = 'bypassed-output'
                bypassed = subprocess.run(command, cwd=root, env=environment,
                                          capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(bypassed.stdout)['root'], str(source))
                self.assertFalse((root / 'bypassed-output/work/controller-input').exists())
