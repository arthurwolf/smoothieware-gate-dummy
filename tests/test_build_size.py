import json, os, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build  # noqa: E402
import size  # noqa: E402
from gate_result import validate_result  # noqa: E402


class BuildTests(unittest.TestCase):
    def test_container_build_runner_preserves_results_and_failures_for_both_profiles(self):
        import container_runtime
        from sandbox_process import ProcessOutput
        for profile,toolchain,names in [('v1-lpc1768','gcc48',('main.elf','main.bin')),
                                        ('v2-prime','gcc10',('smoothiev2.elf','smoothiev2.bin'))]:
            for exit_code,problem,wanted in [(0,None,'pass'),(7,None,'fail'),(75,'container_cleanup_unverified','error')]:
                with self.subTest(profile=profile,exit_code=exit_code),tempfile.TemporaryDirectory() as tmp:
                    tree=os.path.join(tmp,'tree');out=os.path.join(tmp,'out');compiler=os.path.join(tmp,'compiler')
                    for path in (tree,out,compiler):os.mkdir(path)
                    elf=bytearray(52);elf[:7]=b'\x7fELF\x01\x01\x01';elf[16:24]=b'\x02\0\x28\0\x01\0\0\0'
                    payload=('{'+'"'+names[0]+'":52,"'+names[1]+'":3}\n').encode()+bytes(elf)+b'bin'
                    runtime=mock.Mock();runtime.run.return_value=ProcessOutput(exit_code,payload,b'compiler failed',False,problem)
                    argv=['build.py','--tree',tree,'--out',out,'--build-type','Release','--profile',profile,
                          '--toolchain',toolchain,'--runner','docker','--compiler',compiler,
                          '--runtime-image','registry.invalid/gate@sha256:'+'a'*64]
                    with mock.patch.object(sys,'argv',argv),mock.patch.object(container_runtime,'DockerRuntime',return_value=runtime), \
                         mock.patch.object(build.v2_build_inputs,'normalize_paths'):
                        code=build.main()
                    with open(os.path.join(out,'build.result.json')) as handle:report=json.load(handle)
                    self.assertEqual(report['status'],wanted)
                    self.assertEqual(code,0 if wanted=='pass' else 1)
                    self.assertEqual(os.path.isfile(os.path.join(out,build.PROFILES[profile].elf)),wanted=='pass')
                    self.assertEqual(report['metrics']['build_jobs'],4)

    def test_v1_measurement_patch_is_deferred_to_private_build_copy(self):
        with tempfile.TemporaryDirectory() as out:
            argv = ['build.py', '--tree', '/untrusted', '--out', out, '--build-type', 'Debug', '--measure-only']
            with mock.patch.object(build, 'enlarge_flash') as patch_host, \
                    mock.patch.object(build, 'run_ni_build', return_value=(75, None, 'dispatcher unavailable')) as dispatch, \
                    mock.patch.object(sys, 'argv', argv):
                self.assertEqual(build.main(), 1)
            patch_host.assert_not_called()
            self.assertTrue(dispatch.call_args.kwargs['measure_only'])

    def test_v2_dispatch_uses_trusted_worker_and_collects_only_final_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree, out = os.path.join(tmp, 'source'), os.path.join(tmp, 'out')
            os.mkdir(tree); os.mkdir(out)
            finals = os.path.join(out, '.gate-build-finals'); os.mkdir(finals)
            for name, data in [('smoothiev2.elf', b'fixture elf'), ('smoothiev2.bin', b'fixture bin')]:
                with open(os.path.join(finals, name), 'wb') as handle:
                    handle.write(data)
            process = mock.Mock()
            process.stdout = [json.dumps({'type': 'receipt', 'exit_code': 0}) + '\n']
            process.wait.return_value = 0
            with mock.patch.object(build.subprocess, 'Popen', return_value=process) as popen, \
                    mock.patch.object(build.v2_build_inputs, 'normalize_paths'):
                code, _, _ = build.run_v2_build(tree, out, 'Debug', 'gcc10', 4, 'ni-build', True)
            self.assertEqual(code, 0)
            argv = popen.call_args.args[0]
            self.assertIn('sandbox_build.py', ' '.join(argv))
            self.assertIn('systemd-run', ' '.join(argv))
            for name, data in [('smoothiev2.elf', b'fixture elf'), ('smoothiev2.bin', b'fixture bin')]:
                with open(os.path.join(out, 'Firmware', 'smoothiev2_Prime', name), 'rb') as handle:
                    self.assertEqual(handle.read(), data)

    def test_failure_excerpt_puts_compiler_errors_before_the_tail(self):
        path = os.path.join(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), "make.log")
        open(path, "w").write("Compiling a.cpp\nKernel.cpp:403:1: error: 'this' does not name a type\n" + "Compiling b.c\n" * 40)
        excerpt = build.failure_excerpt(path)
        self.assertTrue(excerpt.startswith("Kernel.cpp:403:1: error"))
        self.assertIn("...", excerpt)

    def make_tree(self, line):
        tree = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        path = os.path.join(tree, build.LINKER_SCRIPT)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("MEMORY\n{\n  " + line + "\n}\n")
        return tree, path

    def test_enlarge_flash(self):
        tree, path = self.make_tree(build.FLASH_LINE)
        self.assertTrue(build.enlarge_flash(tree))
        self.assertIn("4096K", open(path).read())

    def test_enlarge_flash_missing_line(self):
        tree, _ = self.make_tree("FLASH (rx) : ORIGIN = 0, LENGTH = 256K")
        self.assertFalse(build.enlarge_flash(tree))

    def run_main(self, exit_code, make_elf):
        out = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        if make_elf:
            os.makedirs(os.path.join(out, "LPC1768"))
            open(os.path.join(out, "LPC1768", "main.elf"), "w").close()
        argv = ["build.py", "--tree", "/x", "--out", out, "--build-type", "Release", "--label", "pr"]
        with mock.patch.object(build, "run_ni_build", return_value=(exit_code, None, "boom")), \
                mock.patch.object(sys, "argv", argv):
            code = build.main()
        return code, json.load(open(os.path.join(out, "build.result.json")))

    def test_v2_build_uses_prime_elf_and_distinguishes_infrastructure_error(self):
        out = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        argv = ["build.py", "--tree", "/x", "--out", out, "--build-type", "Release",
                "--profile", "v2-prime", "--toolchain", "gcc10"]
        with mock.patch.object(build, "run_v2_build", return_value=(75, None, "dispatcher unavailable")), \
                mock.patch.object(sys, "argv", argv):
            code = build.main()
        with open(os.path.join(out, "build.result.json")) as handle:
            result = json.load(handle)
        self.assertEqual((code, result["status"]), (1, "error"))
        self.assertIn("dispatcher unavailable", result["details"])

    def test_v2_unknown_recipe_still_writes_stage_result(self):
        with tempfile.TemporaryDirectory() as out:
            argv = ['build.py', '--tree', '/tree', '--out', out, '--build-type', 'Debug',
                    '--profile', 'v2-prime', '--toolchain', 'gcc10']
            with mock.patch.object(build, 'run_v2_build', side_effect=ValueError('unsupported recipe')), \
                    mock.patch.object(sys, 'argv', argv):
                self.assertEqual(build.main(), 1)
            with open(os.path.join(out, 'build.result.json')) as handle:
                result = json.load(handle)
            self.assertEqual(result['status'], 'error')
            self.assertIn('unsupported recipe', result['details'])

    def test_v2_dispatcher_failure_preserves_terminal_diagnostic(self):
        process = mock.Mock()
        process.stdout = [json.dumps({"type": "result", "status": "error",
                                      "message": "new files exceed the return size cap"}) + "\n"]
        process.wait.return_value = 75
        with mock.patch.object(build.subprocess, "Popen", return_value=process), \
                mock.patch.object(build.v2_build_inputs, "normalize_paths") as normalize:
            code, _, details = build.run_v2_build("/tree", "/out", "Debug", "gcc10", 16, "ni-build")
        normalize.assert_called_once_with("/tree")
        self.assertEqual(code, 75)
        self.assertIn("return size cap", details)

    def test_v2_success_requires_both_elf_and_binary(self):
        out = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        elf = os.path.join(out, build.PROFILES["v2-prime"].elf)
        os.makedirs(os.path.dirname(elf))
        with open(elf, "w"):
            pass
        argv = ["build.py", "--tree", "/x", "--out", out, "--build-type", "Release",
                "--profile", "v2-prime", "--toolchain", "gcc10"]
        with mock.patch.object(build, "run_v2_build", return_value=(0, {}, "")), mock.patch.object(sys, "argv", argv):
            code = build.main()
        with open(os.path.join(out, "build.result.json")) as handle:
            result = json.load(handle)
        self.assertEqual((code, result["status"]), (1, "error"))

    def test_compile_failure_is_fail(self):
        code, result = self.run_main(2, False)
        self.assertEqual((code, result["status"]), (1, "fail"))
        self.assertEqual(validate_result(result), [])

    def test_success_is_pass(self):
        code, result = self.run_main(0, True)
        self.assertEqual((code, result["status"]), (0, "pass"))
        self.assertEqual(validate_result(result), [])

    def test_missing_elf_is_error(self):
        _, result = self.run_main(0, False)
        self.assertEqual(result["status"], "error")


class SizeTests(unittest.TestCase):
    def test_isolated_size_cli_uses_one_runtime_and_ignores_child_status(self):
        import container_runtime
        from sandbox_process import ProcessOutput
        data={'rows':[{'memory':'Flash','reference':100,'candidate':104,'delta':4},
                      {'memory':'RAM','reference':20,'candidate':19,'delta':-1},
                      {'memory':'AHB RAM','reference':0,'candidate':0,'delta':0}],
              'top_symbol_changes':[],'status':'fail'}
        for problem in (None,'container_cleanup_unverified'):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as tmp:
                sim=os.path.join(tmp,'sim');os.makedirs(os.path.join(sim,'checkers'))
                with open(os.path.join(sim,'checkers','size_check.py'),'w') as handle:handle.write('# checker fixture')
                compiler=os.path.join(tmp,'compiler');os.mkdir(compiler)
                elf=os.path.join(tmp,'input.elf');open(elf,'wb').write(b'ELF fixture')
                out=os.path.join(tmp,'size.result.json')
                runtime=mock.Mock()
                runtime.run.return_value=ProcessOutput(75 if problem else 0,json.dumps(data).encode(),
                    b'# forged success\n````\nUnverified owned container: gate-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',False,problem)
                argv=['size.py','--base-release',elf,'--pr-release',elf,'--base-debug',elf,'--pr-debug',elf,
                      '--sim-dir',sim,'--out',out,'--compiler',compiler,
                      '--runtime-image','registry.invalid/gate@sha256:'+'a'*64]
                with mock.patch.object(sys,'argv',argv),mock.patch.object(container_runtime,'DockerRuntime',return_value=runtime) as factory:
                    code=size.main()
                with open(out) as handle:report=json.load(handle)
                self.assertEqual(factory.call_count,1)
                self.assertEqual(report['status'],'error' if problem else 'pass')
                self.assertEqual(code,1 if problem else 0)
                self.assertEqual(runtime.run.call_count,1 if problem else 2)
                if problem:
                    from mdkit import _walk_fences
                    self.assertIn('gate-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',report['details'])
                    states=[inside for line,inside in _walk_fences(report['details']) if line=='# forged success']
                    self.assertEqual(len(states),1)
                    self.assertTrue(states[0], 'untrusted diagnostic became an unfenced heading')
                else:self.assertEqual(report['metrics']['flash_delta_release'],4)

    DATA = {"rows": [{"memory": "Flash", "reference": 10, "candidate": 15, "delta": 5}],
            "top_symbol_changes": [{"symbol": "$t", "delta": 5}, {"symbol": "foo()", "delta": 5}, {"symbol": "bar", "delta": -2}]}

    def test_size_table_reports_signed_change_in_remaining_capacity(self):
        text = size.table("Release", self.DATA, "size-release")
        self.assertIn("Remaining change (bytes)", text)
        self.assertIn("-5", text)

    def test_table_hides_mapping_symbols_and_shrinkage(self):
        text = size.table("Release", self.DATA, "size-release")
        self.assertIn('<td align="right"><b>+5</b></td>', text)
        self.assertIn("<code>foo()</code> +5 B", text)
        self.assertNotIn("$t", text)
        self.assertNotIn("bar", text)

    def test_table_is_full_width_and_has_chart_placeholder_not_emoji_squares(self):
        text = size.table("Release", self.DATA, "size-release")
        self.assertIn('<table width="100%">', text)
        self.assertIn("<!--chart:size-release-->", text)
        for square in "🟦🟧🟩⬛":
            self.assertNotIn(square, text)

    def test_v2_memory_banks_have_correct_totals(self):
        rows = [{"memory": "Flash", "reference": 100, "candidate": 110, "delta": 10},
                {"memory": "ITCM", "reference": 10, "candidate": 12, "delta": 2},
                {"memory": "DTCM", "reference": 20, "candidate": 19, "delta": -1},
                {"memory": "AXI SRAM", "reference": 30, "candidate": 31, "delta": 1}]
        for row in rows:
            row.update(reference_footprint=row['reference'], candidate_footprint=row['candidate'])
        self.assertEqual(size.ram_totals(rows), (60, 62, 2))
        rendered = size.table("V2", {"rows": rows, "top_symbol_changes": []}, "size-release", "stm32h745")
        self.assertIn("ITCM", rendered)
        self.assertIn("AXI SRAM", rendered)
        self.assertIn("Static RAM total", rendered)
        self.assertIn(">60<", rendered)
        self.assertIn(">62<", rendered)

    def test_v2_remaining_capacity_includes_alignment_gaps(self):
        rows = [{"memory": "Flash", "reference": 16, "candidate": 24, "delta": 8,
                 "reference_footprint": 32, "candidate_footprint": 48}]
        text = size.table("V2", {"rows": rows, "top_symbol_changes": []}, "size", "stm32h745")
        self.assertIn(f"{size.STM32_CAPACITY['Flash'] - 32:,}", text)
        self.assertIn(f"{size.STM32_CAPACITY['Flash'] - 48:,}", text)
        self.assertIn("-16", text)

    def test_default_warning_limits_are_strict(self):
        defaults = {a.dest: a.default for a in size.argparse.ArgumentParser()._actions}  # noqa: F841
        import inspect
        source = inspect.getsource(size.main)
        self.assertIn('"--warn-flash", type=int, default=64', source)
        self.assertIn('"--warn-ram", type=int, default=4', source)


if __name__ == "__main__":
    unittest.main()
