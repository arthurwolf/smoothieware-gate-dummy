"""Trusted post-build CI checks; untrusted native execution uses one runtime.

The caller supplies already validated, stable inputs and authenticated manifest.
Model inference belongs to separate trusted reporting; this worker only scans
source-backed developer rules and never requests an inference endpoint.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import ai_review
import charts
from gate_result import make_result
from mdkit import code_block
import report
import sim_stage
import size


def _write(directory, stage, summary, details=''):
    result = make_result(stage, 'error', stage, summary, details=code_block(details) if details else '')
    with (directory / (stage + '.result.json')).open('x') as handle:
        json.dump(result, handle)


def run(prepared, batch, manifest, diff, output, runtime):
    """Produce every required stage and a report, including infrastructure errors."""
    if runtime is None or not callable(getattr(runtime, 'run', None)):
        raise ValueError('CI checks require an explicit isolation runtime')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError('CI checks require a fresh output with a real parent')
    inputs = [prepared.retained_root, prepared.controller, prepared.cases, prepared.simulator, prepared.compiler, diff]
    inputs += list(batch.elfs.values()) + list(batch.simulators.values())
    for value in inputs:
        source = Path(value).resolve()
        if output == source or output in source.parents or source in output.parents:
            raise ValueError('CI checks output overlaps an input')
    output.mkdir()
    results = output / 'results'; results.mkdir()
    with (results / 'manifest.json').open('x') as handle:
        json.dump(manifest, handle)
    for record in batch.records:
        stage = record['stage']
        if stage not in ('build-base-release', 'build-base-debug', 'build-pr-release', 'build-pr-debug'):
            raise ValueError('unexpected firmware build stage')
        with (results / (stage + '.result.json')).open('x') as handle:
            json.dump(record, handle)
    stopped = batch.stopped
    size_path = results / 'size.result.json'
    if stopped or set(batch.elfs) != {'base-release', 'base-debug', 'pr-release', 'pr-debug'}:
        _write(results, 'size', 'required firmware builds are unavailable')
    else:
        arguments = SimpleNamespace(
            out=str(size_path), sim_dir=str(prepared.simulator), compiler=str(prepared.compiler),
            sandbox=True, tool_prefix='arm-none-eabi-', base_rev=manifest['base'],
            toolchain=prepared.profile.toolchain, warn_flash=64, warn_ram=4,
            architecture='stm32h745' if prepared.profile.generation == 2 else 'lpc1769',
            **{key.replace('-', '_'): str(value) for key, value in batch.elfs.items()})
        try:
            size.produce_report(arguments, runtime)
        except (OSError, ValueError) as error:
            stopped = True
            _write(results, 'size', 'isolated size comparison could not complete', str(error)[-8192:])
    sim_output = results / 'sim'; sim_output.mkdir()
    required_simulators = {'base', 'pr'} | ({'base-five', 'pr-five'} if prepared.profile.generation == 2 else set())
    if stopped or not required_simulators.issubset(batch.simulators):
        details = '\n'.join(str(value) for value in batch.simulator_errors.values())[-8192:]
        _write(sim_output, 'sim', 'required simulator builds or isolation are unavailable', details)
    else:
        cases = Path(prepared.cases) / 'v2' if prepared.profile.generation == 2 else Path(prepared.cases)
        arguments = ['--sandbox', '--sim', str(batch.simulators['pr']), '--base-sim', str(batch.simulators['base']),
                     '--cases', str(cases), '--out', str(sim_output), '--sim-dir', str(prepared.simulator),
                     '--require-base-sim']
        if prepared.profile.generation == 2:
            arguments += ['--sim-five', str(batch.simulators['pr-five']), '--base-sim-five',
                          str(batch.simulators['base-five']), '--coverage-manifest', str(cases / 'coverage.json'),
                          '--coverage-baseline', str(prepared.cases)]
        try:
            sim_stage.main(arguments, runtime=runtime)
        except (OSError, ValueError, ImportError, RecursionError, OverflowError) as error:
            if not (sim_output / 'sim.result.json').exists():
                _write(sim_output, 'sim', 'isolated replay could not complete', str(error)[-8192:])
    review_output = results / 'review'; review_output.mkdir()
    try:
        ai_review.main(['--diff', str(diff), '--out', str(review_output), '--no-model',
                        '--prompts', str(Path(prepared.controller) / 'prompts'), '--sim-dir', str(prepared.simulator),
                        '--firmware-version', str(prepared.profile.generation)])
    except (OSError, ValueError, ImportError, RecursionError, OverflowError) as error:
        if not (review_output / 'ai-review.result.json').exists():
            _write(review_output, 'ai-review', 'developer-rule scan could not complete', str(error)[-8192:])
    report_output = output / 'report'; report_output.mkdir()
    chart_output = report_output / 'charts'; chart_output.mkdir()
    charts.main(['--results', str(results), '--out', str(chart_output)])
    return report.main(['--results', str(results), '--out', str(report_output),
                        '--charts', str(chart_output / 'charts.json'), '--image-base', 'charts'])
