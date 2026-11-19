import os
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import gate


class GateProfileTests(unittest.TestCase):
    def test_missing_build_output_produces_explicit_size_error(self):
        with tempfile.TemporaryDirectory() as root:
            elfs = {(label, kind): os.path.join(root, label + kind) for label in ("base", "pr")
                    for kind in ("release", "debug")}
            gate.measure_size(elfs, root, "/sim", "a" * 40, "gcc10", gate.select_profile("v2-prime"))
            with open(os.path.join(root, "size.result.json")) as handle:
                data = json.load(handle)
            self.assertEqual((data["stage"], data["status"]), ("size", "error"))

    def test_size_dispatch_is_isolated_and_preserves_complete_stage_errors(self):
        from pathlib import Path
        for remote_status in ('pass', 'error', None):
            with self.subTest(remote_status=remote_status), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);sim=root/'sim';(sim/'checkers').mkdir(parents=True)
                (sim/'checkers/size_check.py').write_text('# trusted checker fixture')
                elfs={}
                for label in ('base','pr'):
                    for kind in ('release','debug'):
                        path=root/(label+kind+'.elf');path.write_bytes(b'ELF fixture');elfs[label,kind]=str(path)
                def dispatch(name, command):
                    self.assertEqual(command[:2], ['ni-build','arm'])
                    self.assertIn('--sandbox',command)
                    self.assertIn('--property=MemoryMax=4294967296',command)
                    self.assertIn('--property=KillMode=control-group',command)
                    self.assertIn('stm32h745',command)
                    self.assertNotIn('--tool-prefix',command)
                    if remote_status:
                        returned=Path(command[command.index('--out')+1])/'size-output'
                        returned.mkdir()
                        (returned/'size.result.json').write_text(json.dumps({'stage':'size','status':remote_status,'details':'preserved diagnostic'}))
                    return mock.Mock(returncode=0 if remote_status else 75,stdout='')
                with mock.patch.object(gate,'stage',side_effect=dispatch):
                    gate.measure_size(elfs,str(root),str(sim),'a'*40,'gcc10',gate.select_profile('v2-prime'))
                if remote_status is None:
                    (root/'size.result.json').write_text(json.dumps({'stage':'size','status':'pass','details':'stale success'}))
                    with mock.patch.object(gate,'stage',side_effect=dispatch):
                        gate.measure_size(elfs,str(root),str(sim),'a'*40,'gcc10',gate.select_profile('v2-prime'))
                result=json.loads((root/'size.result.json').read_text())
                self.assertEqual(result['status'],remote_status or 'error')
                if remote_status:self.assertEqual(result['details'],'preserved diagnostic')

    def test_remote_replay_failure_creates_error_result(self):
        with tempfile.TemporaryDirectory() as root:
            cases = os.path.join(root, "cases")
            sim = os.path.join(root, "sim")
            out = os.path.join(root, "out")
            os.makedirs(cases)
            os.makedirs(os.path.join(sim, "checkers"))
            os.makedirs(out)
            with mock.patch.object(gate, "stage", return_value=mock.Mock(returncode=75, stdout="")) as stage:
                gate.replay_simulator({"pr": None, "base": None}, cases, cases, sim, root, out,
                                      gate.select_profile("v1-lpc1768"))
            self.assertEqual(stage.call_args.args[1][:2], ["ni-build", "arm"])
            with open(os.path.join(out, "sim.result.json")) as handle:
                self.assertEqual(json.load(handle)["status"], "error")

    def test_v2_inventory_accounts_for_all_v1_cases(self):
        cases = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cases")
        names = {name for name in os.listdir(cases) if os.path.isfile(os.path.join(cases, name, "meta.json"))}
        with open(os.path.join(cases, "v2", "coverage.json")) as handle:
            inventory = json.load(handle)
        self.assertEqual(set(inventory["required_scenarios"]), names)

    def test_wrong_firmware_tree_cannot_be_mislabeled(self):
        with tempfile.TemporaryDirectory() as tree:
            os.makedirs(os.path.join(tree, "src"))
            with open(os.path.join(tree, "src", "main.cpp"), "w"):
                pass
            with self.assertRaises(ValueError):
                gate.validate_firmware_tree(tree, gate.select_profile("v2-prime"))

    def test_prime_build_selects_prime_profile_and_does_not_patch_v1_linker(self):
        with tempfile.TemporaryDirectory() as out, mock.patch.object(gate, "stage") as stage:
            elf = gate.build_one("base", "Debug", "/firmware", out, "gcc10", gate.select_profile("v2-prime"))
        command = stage.call_args.args[1]
        self.assertIn("v2-prime", command)
        self.assertNotIn("--measure-only", command)
        self.assertTrue(elf.endswith("Firmware/smoothiev2_Prime/smoothiev2.elf"))

    def test_firmware_links_cannot_import_external_bytes_before_sandboxing(self):
        for directory in (False, True):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as root:
                from pathlib import Path
                folder=Path(root);tree=folder/'tree';tree.mkdir();sim=folder/'sim';sim.mkdir();work=folder/'work';work.mkdir()
                secret=folder/'external'
                if directory:
                    secret.mkdir();(secret/'secret').write_bytes(b'host-only-canary')
                else:
                    secret.write_bytes(b'host-only-canary')
                (tree/'Firmware').mkdir()
                (tree/'Firmware/leak').symlink_to(secret,target_is_directory=directory)
                with mock.patch.object(gate,'stage',side_effect=AssertionError('unsafe source dispatched')):
                    result=gate.build_simulator(str(tree),str(sim),str(work),'gcc10',gate.select_profile('v2-prime'),'pr')
                self.assertIsNone(result)
                copied=work/'sim-job-pr/pr/Firmware/leak'
                self.assertTrue(copied.is_symlink() or not copied.exists())

    def test_v2_simulator_stages_only_consumed_firmware_subtree(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root);tree=folder/'tree';sim=folder/'sim';work=folder/'work'
            (tree/'Firmware/Hal').mkdir(parents=True);sim.mkdir();work.mkdir()
            (tree/'Firmware/Hal/fixture.cc').write_text('firmware input')
            (tree/'DFUBootloader').mkdir()
            (tree/'DFUBootloader/Drivers').symlink_to('../Firmware/Hal',target_is_directory=True)
            with mock.patch.object(gate,'stage',return_value=mock.Mock(returncode=1)) as stage:
                gate.build_simulator(str(tree),str(sim),str(work),'gcc10',gate.select_profile('v2-prime'),'pr')
            self.assertIsNotNone(stage.call_args)
            self.assertEqual((work/'sim-job-pr/pr/Firmware/Hal/fixture.cc').read_text(),'firmware input')
            self.assertFalse((work/'sim-job-pr/pr/DFUBootloader').exists())

    def test_five_actuator_simulator_uses_explicit_compile_setting(self):
        with tempfile.TemporaryDirectory() as root:
            tree = os.path.join(root, "tree")
            sim = os.path.join(root, "sim")
            work = os.path.join(root, "work")
            os.makedirs(os.path.join(tree, "Firmware"))
            os.makedirs(sim)
            os.makedirs(work)
            with mock.patch.object(gate, "stage", return_value=mock.Mock(returncode=1)) as stage:
                gate.build_simulator(tree, sim, work, "gcc10", gate.select_profile("v2-prime"), "pr-five", five=True)
            self.assertIn("--five", stage.call_args.args[1][stage.call_args.args[1].index("-c") + 1])


if __name__ == "__main__":
    unittest.main()
