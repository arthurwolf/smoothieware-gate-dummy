import json
import os
import sys
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest import mock
from argparse import Namespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import make_manifest
from profiles import EXPECTED_STAGES, validate_manifest


class ManifestTests(unittest.TestCase):
    def test_preparation_executes_frozen_controller_and_retains_consumable_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'gate'
            for name in ('scripts', 'prompts', 'cases', 'bin', 'sim'):
                (source / name).mkdir(parents=True)
            for path in (Path(__file__).resolve().parents[1] / 'scripts').glob('*.py'):
                shutil.copyfile(path, source / 'scripts' / path.name)
            # Only Git metadata enumeration is replaced; snapshot and reexec are real.
            with (source / 'scripts/gate.py').open('a') as handle:
                handle.write('\ndef git(repo, *args):\n    return "c" * 40 if args[0] == "rev-parse" else ""\n'
                             '\ndef tracked_simulator_files(source):\n    return ["source.cpp"]\n')
            (source / 'prompts/review.md').write_text('original prompt')
            (source / 'cases/config').write_text('original case')
            (source / 'bin/smoothie-gate').write_text('#!/bin/sh\n')
            (source / 'sim/source.cpp').write_text('original simulator')
            command = [sys.executable, '-B', '-I', str(source / 'scripts/make_manifest.py'),
                       '--profile', 'v2-prime', '--base', 'a' * 40, '--head', 'b' * 40,
                       '--toolchain', 'gcc10', '--sim-dir', str(source / 'sim'),
                       '--cases', str(source / 'cases'), '--inputs', str(root / 'inputs'),
                       '--out', str(root / 'manifest.json')]
            nested_output = command.copy()
            nested_output[-1] = str(root / 'inputs/controller-input/prompts/generated.md')
            rejected = subprocess.run(nested_output, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0, 'manifest cannot mutate its hashed controller')
            self.assertFalse((root / 'inputs').exists(), 'reject conflicting output before preparation')
            done = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(validate_manifest(manifest, EXPECTED_STAGES), [])
            import controller_snapshot
            controller_snapshot.verify(root / 'inputs/controller-input', manifest['controller_sha256'])
            (source / 'sim/source.cpp').write_text('changed live simulator')
            (source / 'cases/config').write_text('changed live case')
            self.assertEqual((root / 'inputs/sim/source.cpp').read_text(), 'original simulator')
            self.assertEqual((root / 'inputs/cases/config').read_text(), 'original case')
            before = (root / 'manifest.json').read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual((root / 'manifest.json').read_bytes(), before)

    def test_workflow_manifest_has_profile_and_complete_input_identity(self):
        with tempfile.TemporaryDirectory() as root:
            output = os.path.join(root, "manifest.json")
            args = Namespace(profile='v2-prime', base='a'*40, head='b'*40, toolchain='gcc10',
                             sim_dir='sim', cases='cases', out=output, inputs=os.path.join(root, 'inputs'))
            os.mkdir(args.inputs)
            with mock.patch.object(make_manifest.gate, "git", side_effect=["c" * 40, ""]), \
                    mock.patch.object(make_manifest.gate, "snapshot_simulator", return_value="d" * 64), \
                    mock.patch.object(make_manifest.gate, "snapshot_simulator_dependencies", return_value={"lib-smoothie": {"sha256": "f" * 64}}), \
                    mock.patch.object(make_manifest.gate, "snapshot_case_tree", return_value="e" * 64):
                self.assertEqual(make_manifest.write_manifest(args, '1' * 64), 0)
            with open(output) as handle:
                manifest = json.load(handle)
            self.assertEqual(validate_manifest(manifest, EXPECTED_STAGES), [])
            self.assertEqual(manifest.get('simulator_dependencies'), {'lib-smoothie': {'sha256': 'f' * 64}})


if __name__ == "__main__":
    unittest.main()
