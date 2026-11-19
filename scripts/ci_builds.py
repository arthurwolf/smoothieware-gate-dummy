"""Sequential CI build orchestration using one caller-owned isolation runtime.

Firmware trees must be private exports of the authenticated exact revisions.
The caller owns runtime provisioning and must keep all source snapshots stable.
No child-supplied stage result is accepted; only validated final bytes are used.
"""
from dataclasses import dataclass, field
import json
import os
import stat
from pathlib import Path
import shutil

from ci_inputs import tree_digest
from gate_result import make_result
from mdkit import code_block
import revision_metadata
import sandbox_build
import v2_build_inputs


@dataclass
class BuildBatch:
    records: list = field(default_factory=list)
    elfs: dict = field(default_factory=dict)
    simulators: dict = field(default_factory=dict)
    simulator_errors: dict = field(default_factory=dict)
    stopped: bool = False


def _record(stage, status, summary, directory, diagnostics=b''):
    record = make_result(stage, status, 'Firmware build', summary,
                         details=code_block(diagnostics[-262144:].decode('utf-8', errors='replace')) if diagnostics else '')
    directory.mkdir(exist_ok=True)
    with (directory / (stage + '.result.json')).open('x') as handle:
        json.dump(record, handle)
    return record


def _validate(prepared, trees, output):
    if not isinstance(trees, dict) or set(trees) != {'base', 'pr'}:
        raise ValueError('CI builds require exact base and pr source exports')
    sources = [Path(value).absolute() for value in trees.values()]
    sources += [Path(prepared.compiler).absolute(), Path(prepared.simulator).absolute()]
    sources += [Path(prepared.controller).absolute(), Path(prepared.cases).absolute(),
                Path(prepared.retained_root).absolute()]
    if prepared.profile.generation == 2:
        sources.append(Path(prepared.simulator).absolute().parent / 'lib-smoothie')
    if (output.exists() or output.is_symlink() or not output.parent.is_dir() or
            output.parent.resolve() != output.parent):
        raise ValueError('CI builds require a fresh output with a real parent')
    for source in sources:
        if source.resolve() != source or not source.is_dir():
            raise ValueError('CI build inputs must be real directories')
        if output == source or output in source.parents or source in output.parents:
            raise ValueError('CI build output overlaps an input')
    if sources[0] == sources[1] or sources[0] in sources[1].parents or sources[1] in sources[0].parents:
        raise ValueError('firmware exports must be independent directories')
    for source in sources[:2]:
        _firmware_paths(source)
    # Compiler archives contain reviewed internal links; retained controller,
    # cases, shim and library snapshots remain strictly ordinary files.
    for source in sources[3:]:
        if source != Path(prepared.retained_root).absolute():
            tree_digest(source)
    if prepared.profile.generation == 2:
        if not isinstance(prepared.revisions, dict) or set(prepared.revisions) != {'base', 'pr'}:
            raise ValueError('missing authenticated firmware revisions')
        for label, tree in trees.items():
            revision_metadata.validate(tree, prepared.revisions[label])



def _firmware_paths(root):
    """Keep internal source links, while rejecting escape and special files."""
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            item = Path(directory) / name
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode):
                try:
                    target = item.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise ValueError('invalid firmware source link') from error
                if not target.is_relative_to(root) or not (target.is_file() or target.is_dir()):
                    raise ValueError('firmware source link escapes its export')
            elif not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
                raise ValueError('firmware source contains a special file')


def _simulator_source(prepared, tree, destination):
    destination.mkdir()
    shutil.copytree(prepared.simulator, destination / 'sim', symlinks=True)
    tree_digest(destination / 'sim')
    if prepared.profile.generation == 2:
        shutil.copytree(Path(prepared.simulator).parent / 'lib-smoothie',
                        destination / 'lib-smoothie', symlinks=True)
        tree_digest(destination / 'lib-smoothie')
    # Preserve the complete private export so internal relative links retain
    # their targets. The shim still selects Firmware for V2 compilation.
    shutil.copytree(tree, destination / 'pr', symlinks=True)
    _firmware_paths(destination / 'pr')



def run(prepared, trees, output, runtime):
    """Build four firmware configurations and all required simulator variants.

    Verified compiler failures are failures of that build. Infrastructure or
    final-validation problems stop every subsequent launch, preserving complete
    error records. The same runtime is passed to every trusted worker.
    """
    if runtime is None or not callable(getattr(runtime, 'run', None)):
        raise ValueError('CI builds require an explicit isolation runtime')
    output = Path(output).absolute()
    _validate(prepared, trees, output)
    trees = {label: Path(tree).absolute() for label, tree in trees.items()}
    output.mkdir()
    batch = BuildBatch()
    for label in ('base', 'pr'):
        for configuration in ('release', 'debug'):
            key = label + '-' + configuration
            stage = 'build-' + key
            directory = output / stage
            if batch.stopped:
                batch.records.append(_record(stage, 'error', 'not run after infrastructure failure', directory))
                continue
            directory.mkdir()
            try:
                options = {'debug': configuration == 'debug', 'jobs': 4, 'runtime': runtime}
                if prepared.profile.generation == 2:
                    v2_build_inputs.normalize_paths(trees[label])
                    result = sandbox_build.build_v2(trees[label], directory / 'finals', prepared.compiler,
                                                    revision_metadata=True, **options)
                    elf = 'smoothiev2.elf'
                else:
                    result = sandbox_build.build_v1(trees[label], directory / 'finals', prepared.compiler,
                                                    measure_only=options['debug'], compiler_profile='gcc48', **options)
                    elf = 'main.elf'
                batch.stopped = result.problem is not None or result.returncode == 75
                status = 'error' if batch.stopped else ('fail' if result.returncode else 'pass')
                summary = result.problem or ('build infrastructure failed' if batch.stopped else 'compiler failed' if result.returncode else 'build completed')
                batch.records.append(_record(stage, status, summary, directory, result.diagnostics))
                if status == 'pass':
                    batch.elfs[key] = directory / 'finals' / elf
            except (OSError, ValueError) as error:
                batch.stopped = True
                batch.records.append(_record(stage, 'error', 'build preparation failed', directory,
                                             str(error).encode('utf-8')))
    work = output / 'simulator-work'
    work.mkdir()
    variants = ((False, True) if prepared.profile.generation == 2 else (False,))
    for label in ('base', 'pr'):
        for five in variants:
            key = label + ('-five' if five else '')
            if batch.stopped:
                batch.simulator_errors[key] = 'not run after infrastructure failure'
                continue
            try:
                source = work / label
                if not source.exists():
                    _simulator_source(prepared, trees[label], source)
                result = sandbox_build.build_simulator(source, output / ('simulator-' + key),
                                                       generation=prepared.profile.generation, five=five, runtime=runtime)
                batch.stopped = result.problem is not None or result.returncode == 75
                if result.problem or result.returncode:
                    message = result.problem or ('simulator infrastructure failed' if batch.stopped else 'simulator compiler failed')
                    batch.simulator_errors[key] = message + '\n' + code_block(
                        result.diagnostics[-262144:].decode('utf-8', errors='replace'))
                else:
                    binary = output / ('simulator-' + key) / prepared.profile.simulator
                    binary.chmod(0o500)
                    batch.simulators[key] = binary
            except (OSError, ValueError) as error:
                batch.stopped = True
                batch.simulator_errors[key] = 'simulator preparation failed: ' + str(error)
    return batch
