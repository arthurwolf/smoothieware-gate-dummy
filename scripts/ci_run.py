"""Complete CI worker orchestration, separate from workflow transport/publication.

Inputs are trusted preparation records and private firmware exports. This entry
never grants permission to publish; publication_permitted only records freshness.
The workflow must execute this module from its verified retained controller.
"""
from dataclasses import dataclass
import json
from pathlib import Path

import ci_builds
import ci_checks
import ci_inputs
import container_runtime
from gate_result import make_result
import github_worker
from mdkit import code_block


@dataclass(frozen=True)
class RunRequest:
    policy: dict
    execution: dict
    manifest: dict
    inputs: Path
    archive: Path
    trees: dict
    diff: Path
    output: Path


@dataclass(frozen=True)
class RunResult:
    code: int
    publication_permitted: bool
    error: str = ''


def _output(request):
    output = Path(request.output).absolute()
    if (output.exists() or output.is_symlink() or not output.parent.is_dir() or
            output.parent.resolve() != output.parent):
        raise ValueError('CI run requires fresh output with a real parent')
    if not isinstance(request.trees, dict) or set(request.trees) != {'base', 'pr'}:
        raise ValueError('CI run requires base and pr exports')
    for value in [request.inputs, request.archive, request.diff, *request.trees.values()]:
        source = Path(value).resolve()
        if output == source or output in source.parents or source in output.parents:
            raise ValueError('CI run output overlaps an input')
    return output


def run(request, api, *, runtime_factory=None):
    """Prepare, build, check and reauthenticate using one owned runtime adapter."""
    if not isinstance(request, RunRequest):
        raise ValueError('CI worker requires an explicit run request')
    github_worker.confirm(request.policy, request.execution, api)
    output = _output(request)
    output.mkdir()
    prepared = ci_inputs.prepare(request.policy, request.execution['binding'], request.manifest,
                                 request.inputs, request.archive, output / 'preparation')
    factory = runtime_factory if runtime_factory is not None else container_runtime.DockerRuntime
    runtime = factory(prepared.runtime_image)
    try:
        batch = ci_builds.run(prepared, request.trees, output / 'builds', runtime)
    except (OSError, ValueError) as error:
        # Trusted preparation failures must not make missing stages look green.
        records = [make_result('build-' + label + '-' + configuration, 'error', 'Firmware build',
                               'build preparation could not complete', code_block(str(error)[-8192:]))
                   for label in ('base', 'pr') for configuration in ('release', 'debug')]
        batch = ci_builds.BuildBatch(records=records, stopped=True)
    code = ci_checks.run(prepared, batch, request.manifest, request.diff, output / 'checks', runtime)
    try:
        github_worker.confirm(request.policy, request.execution, api)
    except (OSError, ValueError) as error:
        problem = {'type': 'error', 'publication_permitted': False, 'error': str(error), 'code': 2}
        with (output / 'execution.problem.json').open('x') as handle:
            json.dump(problem, handle)
        return RunResult(2, False, str(error))
    with (output / 'execution.json').open('x') as handle:
        json.dump({'type': 'result', 'code': code, 'publication_permitted': True}, handle)
    return RunResult(code, True)
