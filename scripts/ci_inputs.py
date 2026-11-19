"""Validate retained CI inputs before preparing any executable toolchain.

The binding is supplied by authenticated API preflight, never by an artifact.
This boundary prepares data; it does not establish runtime isolation or execute it.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

import compiler_inputs
import controller_snapshot
from dispatch_identity import validate_binding_policy, binding_problems
from input_snapshot import identity_entry, read_input
from profiles import EXPECTED_STAGES, Profile, select_profile, validate_manifest

TOOLCHAIN_SHA256 = {
    'gcc48': 'ce92859550819d4a3d1a6e2672ea64882b30afa2c08cf67fa8e1d93788c2c577',
    'gcc10': '97dbb4f019ad1650b732faffcc881689cedc14e2b7ee863d390e0a41ef16c9a3',
}


@dataclass(frozen=True)
class PreparedInputs:
    retained_root: Path
    profile: Profile
    compiler: Path
    controller: Path
    simulator: Path
    cases: Path
    runtime_image: str
    revisions: dict


def tree_digest(root):
    """Identify every retained regular file; links and special files are invalid."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('retained input must be a real directory')
    names = []
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            item = Path(directory) / name
            mode = item.lstat().st_mode
            if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                raise ValueError('retained inputs cannot contain links or special files')
        names.extend((Path(directory) / name).relative_to(root).as_posix() for name in files)
    if not names:
        raise ValueError('retained input is empty')
    digest = hashlib.sha256()
    for name in sorted(names):
        mode, content = read_input(root, name)
        digest.update(identity_entry(name, mode, content))
    return digest.hexdigest()


def prepare(policy, binding, manifest, root, archive, output):
    """Validate exclusive task-owned snapshots kept stable by the caller.

    Untrusted execution must never receive writable access to these inputs.
    This does not protect against arbitrary concurrent same-user host mutation.
    """
    validate_binding_policy(binding, policy)
    profile = select_profile(policy['profile'])
    problems = validate_manifest(manifest, EXPECTED_STAGES)
    try:
        problems.extend(binding_problems(manifest, binding))
    except (KeyError, TypeError) as error:
        raise ValueError('incomplete authenticated binding') from error
    if problems or manifest['profile'] != profile.name:
        raise ValueError('; '.join(problems) or 'profile does not match policy')
    pins = policy['pins']
    if (manifest['toolchain'] != profile.toolchain or
            pins['toolchain_sha256'] != TOOLCHAIN_SHA256[profile.toolchain]):
        raise ValueError('CI compiler does not match the canonical profile archive')
    if manifest['simulator_revision'] != pins['sim_ref']:
        raise ValueError('CI simulator revision must match its clean pinned revision')
    root, output, archive = Path(root).absolute(), Path(output).absolute(), Path(archive).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError('retained input root is missing or linked')
    if output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError('CI output requires a real existing parent')
    if output.exists() or output.is_symlink():
        raise FileExistsError('CI output must be fresh')
    # Resolve before overlap comparisons, including parent-directory links.
    resolved_output = output.resolve()
    for source in (root.resolve(), archive.resolve()):
        if resolved_output == source or resolved_output in source.parents or source in resolved_output.parents:
            raise ValueError('CI output overlaps retained inputs')
    controller = root / 'controller-input'
    controller_snapshot.verify(controller, pins['controller_sha256'])
    simulator_digest = pins.get('simulator_sha256')
    if (not isinstance(simulator_digest, str) or not re.fullmatch('[0-9a-f]{64}', simulator_digest) or
            tree_digest(root / 'sim') != simulator_digest or manifest['simulator_sha256'] != simulator_digest):
        raise ValueError('retained simulator bytes do not match trusted policy')
    cases_digest = tree_digest(controller / 'cases')
    if (tree_digest(root / 'cases') != cases_digest or tree_digest(root / 'case-inventory') != cases_digest or
            manifest['references_sha256'] != cases_digest or manifest['case_inventory_sha256'] != cases_digest):
        raise ValueError('retained cases do not match the pinned controller cases')
    if profile.generation == 2:
        library_digest = tree_digest(root / 'lib-smoothie')
        dependencies = manifest.get('simulator_dependencies')
        if not isinstance(dependencies, dict) or not isinstance(dependencies.get('lib-smoothie'), dict):
            raise ValueError('shared simulator dependency must be an object')
        dependency = dependencies['lib-smoothie']
        if dependency.get('sha256') != library_digest or pins['shared_library_sha256'] != library_digest:
            raise ValueError('shared simulator library does not match trusted policy')
    output.mkdir()
    compiler = compiler_inputs.prepare(archive, pins['toolchain_sha256'], output / 'compiler')
    return PreparedInputs(root, profile, compiler, controller, root / 'sim', root / 'cases', pins['runtime_image'],
                          {'base': manifest['base'], 'pr': manifest['head']})
