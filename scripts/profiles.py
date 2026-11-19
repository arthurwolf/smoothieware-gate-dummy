"""Firmware-specific choices and validation of the required run manifest."""
from collections import Counter
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Profile:
    name: str
    generation: int
    board: str
    toolchain: str
    firmware_root: str
    elf: str
    simulator: str
    repository: str


PROFILES = {
    'v1-lpc1768': Profile('v1-lpc1768', 1, 'LPC1768', 'gcc48', '.', 'LPC1768/main.elf', 'smoothie-sim', 'Smoothieware/Smoothieware'),
    'v2-prime': Profile('v2-prime', 2, 'Prime', 'gcc10', 'Firmware', 'Firmware/smoothiev2_Prime/smoothiev2.elf', 'smoothie-sim-v2', 'Smoothieware/SmoothieV2'),
}
EXPECTED_STAGES = ('build-base-release', 'build-base-debug', 'build-pr-release', 'build-pr-debug',
                   'size', 'sim', 'ai-review')
TOOLCHAIN_VERSIONS = {
    'gcc48': 'GNU Tools for ARM Embedded Processors 4.8.3 20140228 (2014q1)',
    'gcc10': 'GNU Arm Embedded Toolchain 10.3-2021.10, 10.3.1 20210824',
    'gcc12': 'Arm GNU Toolchain 12.3.Rel1, 12.3.1 20230626',
}


def select_profile(name):
    try:
        return PROFILES[name]
    except (KeyError, TypeError):
        raise ValueError(f'unknown firmware profile: {name!r}') from None


def validate_manifest(manifest, stages):
    """Return every identity or completeness problem, without trusting artifact metadata."""
    if not isinstance(manifest, dict):
        return ['manifest must be an object']
    try:
        profile = select_profile(manifest.get('profile'))
    except ValueError as error:
        return [str(error)]
    problems = []
    if manifest.get('board') != profile.board:
        problems.append(f'board does not match profile {profile.name}')
    if manifest.get('firmware_repository') != profile.repository:
        problems.append(f'firmware repository does not match profile {profile.name}')
    allowed_tools = ('gcc48', 'gcc12') if profile.generation == 1 else ('gcc10',)
    if manifest.get('toolchain') not in allowed_tools:
        problems.append(f'toolchain does not match profile {profile.name}')
    toolchain = manifest.get('toolchain')
    if not isinstance(toolchain, str) or manifest.get('toolchain_version') != TOOLCHAIN_VERSIONS.get(toolchain):
        problems.append('toolchain_version does not match the selected toolchain')
    for key in ('base', 'head'):
        if not isinstance(manifest.get(key), str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', manifest[key]):
            problems.append(f'{key} must be a full lowercase revision hash')
    if not isinstance(manifest.get('simulator_revision'), str) or not manifest['simulator_revision'].strip():
        problems.append('simulator_revision is missing')
    if not isinstance(manifest.get('controller_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', manifest['controller_sha256']):
        problems.append('controller_sha256 must identify the executed controller inputs')
    if not isinstance(manifest.get('simulator_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', manifest['simulator_sha256']):
        problems.append('simulator_sha256 must identify the exact simulator inputs')
    for key in ('references_sha256', 'case_inventory_sha256'):
        if not isinstance(manifest.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', manifest[key]):
            problems.append(f'{key} must identify the exact case inputs')
    expected = manifest.get('expected_stages')
    if not isinstance(expected, list) or not expected or any(not isinstance(x, str) or not x for x in expected):
        problems.append('expected_stages must be a nonempty list of stage names')
        return problems
    if len(expected) != len(set(expected)):
        problems.append('duplicate expected stage')
    if set(expected) != set(EXPECTED_STAGES):
        problems.append('expected_stages does not match the required checks')
    expected = EXPECTED_STAGES
    counts = Counter(stages)
    for stage in expected:
        if not counts[stage]:
            problems.append(f'missing required stage: {stage}')
        elif counts[stage] > 1:
            problems.append(f'duplicate stage: {stage}')
    return problems
