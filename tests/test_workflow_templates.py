"""Execute workflow shell boundaries with synthetic worker outcomes."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def shell_step(name):
    text = (ROOT/'workflow-templates/dual-firmware/gate-build.yml').read_text()
    section = text.split('      - name: ' + name + '\n', 1)[1].split('\n      - name:', 1)[0]
    return textwrap.dedent(section.split('        run: |\n', 1)[1])


class WorkflowShellTests(unittest.TestCase):
    def execute(self, code, ready=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'gate/scripts').mkdir(parents=True)
            # The expensive isolated worker is the replaced boundary. Execute
            # the candidate's actual Bash capture and readiness parser unchanged.
            (root/'gate/scripts/ci_worker.py').write_text(
                'import json, os, sys\n'
                'print(json.dumps({"type":"result", "code":' + str(code) + ', '
                '"publication_permitted":' + repr(ready) + ', '
                '"artifact":os.environ["TASK_ROOT"]+"/run/artifact" if ' + repr(ready) + ' else None}))\n'
                'sys.exit(' + str(code) + ')\n')
            output = root/'outputs'
            environment = dict(os.environ, TASK_ROOT=str(root), GITHUB_OUTPUT=str(output))
            captured = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c',
                shell_step('Execute isolated checks and retain the actual verdict')],
                cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(captured.returncode, 0, captured.stderr)
            values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(values, {'code': str(code), 'ready': str(ready).lower()})
            verdict = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c',
                shell_step('Apply the actual check conclusion')],
                env=dict(environment, CHECK_CODE=values['code'], ARTIFACT_READY=values['ready']),
                capture_output=True, text=True)
            return verdict.returncode

    def test_current_failure_keeps_artifact_and_failed_conclusion(self):
        self.assertEqual(self.execute(1), 1)

    def test_incomplete_check_keeps_artifact_and_incomplete_conclusion(self):
        self.assertEqual(self.execute(2), 2)

    def test_current_success_keeps_success_conclusion(self):
        self.assertEqual(self.execute(0), 0)

    def test_stale_success_cannot_produce_successful_job(self):
        self.assertEqual(self.execute(0, ready=False), 2)
