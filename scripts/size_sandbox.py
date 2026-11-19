"""Isolate all ELF parsers; validate only bounded data for host reporting.

Default execution needs the caller's outer cgroup, like simulator replay.
The Docker adapter supplies its own limits and must be shared across comparisons.
The child cannot choose report status, warning policy or firmware identity.
"""
import json
import math
from pathlib import Path
import tempfile

from memory_layouts import CAPACITIES
import sandbox
import sandbox_inputs
import sandbox_process

RESULT_BYTES = 4 * 1024**2
MAX_COUNT = 2**48
SCRIPT = '''set -eu
/usr/bin/python3 -B -I /source/size_check.py --ref-elf /source/base.elf --cand-elf /source/head.elf --tool-prefix "$1" --architecture "$2" --out /output/size.json >&2
exec /usr/bin/cat /output/size.json
'''


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate size result key')
        result[key] = value
    return result


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite size result number')
    return result


def _constant(value):
    raise ValueError('nonfinite size result constant: ' + value)


def _check_depth(payload):
    depth, quoted, escaped = 0, False, False
    for value in payload:
        if quoted:
            if escaped:
                escaped = False
            elif value == '\\':
                escaped = True
            elif value == '"':
                quoted = False
        elif value == '"':
            quoted = True
        elif value in '[{':
            depth += 1
            if depth > 16:
                raise ValueError('size result nesting exceeds 16 levels')
        elif value in ']}':
            depth -= 1
            if depth < 0:
                raise ValueError('invalid size result nesting')


def _counts(value):
    if not isinstance(value, dict):
        raise ValueError('size result entry must be an object')
    for key in ('reference', 'candidate', 'delta'):
        number = value.get(key)
        minimum = -MAX_COUNT if key == 'delta' else 0
        if type(number) is not int or not minimum <= number <= MAX_COUNT:
            raise ValueError('invalid size result integer: ' + key)
    if value['delta'] != value['candidate'] - value['reference']:
        raise ValueError('inconsistent size result delta')
    return {key: value[key] for key in ('reference', 'candidate', 'delta')}


def decode_result(payload, architecture):
    if architecture not in CAPACITIES:
        raise ValueError('unsupported size architecture')
    if not isinstance(payload, bytes) or not 0 < len(payload) <= RESULT_BYTES:
        raise ValueError('invalid size result byte length')
    text = payload.decode('utf-8')
    _check_depth(text)
    try:
        data = json.loads(text, object_pairs_hook=_unique, parse_float=_finite, parse_constant=_constant)
    except RecursionError as error:
        raise ValueError('size result parser exceeded its recursion limit') from error
    banks = list(CAPACITIES[architecture])
    rows = data.get('rows') if isinstance(data, dict) else None
    if (not isinstance(rows, list) or len(rows) != len(banks) or
            any(not isinstance(row, dict) or row.get('memory') != name for row, name in zip(rows, banks))):
        raise ValueError('size result banks are missing, duplicated or out of order')
    normalized = []
    for row in rows:
        result = {'memory': row['memory'], **_counts(row)}
        if architecture == 'stm32h745':
            for key in ('reference_footprint', 'candidate_footprint'):
                value = row.get(key)
                if type(value) is not int or not 0 <= value <= MAX_COUNT:
                    raise ValueError('invalid size result occupied extent')
                result[key] = value
        normalized.append(result)
    changes = data.get('top_symbol_changes')
    if not isinstance(changes, list) or len(changes) > 15:
        raise ValueError('invalid size result symbol count')
    symbols = []
    for change in changes:
        counts = _counts(change)
        name = change.get('symbol')
        if (not isinstance(name, str) or not 0 < len(name) <= 8192 or
                any(ord(character) == 0 or 0xD800 <= ord(character) <= 0xDFFF for character in name)):
            raise ValueError('invalid size result symbol name')
        symbols.append({'symbol': name, **counts})
    return {'rows': normalized, 'top_symbol_changes': symbols}


def compare(sim_dir, base_elf, head_elf, scratch_parent, *, architecture, compiler=None, runtime=None):
    if architecture not in CAPACITIES:
        raise ValueError('unsupported size architecture')
    parent = Path(scratch_parent).absolute()
    if not parent.is_dir() or parent.resolve() != parent:
        raise ValueError('size scratch parent must be a real directory')
    compiler = Path(compiler).absolute() if compiler is not None else None
    with tempfile.TemporaryDirectory(prefix='gate-size-', dir=parent) as temporary:
        source = Path(temporary)
        try:
            sandbox_inputs.copy_input(Path(sim_dir) / 'checkers/size_check.py', source / 'size_check.py', 256 * 1024, 0o400, minimum=1)
            sandbox_inputs.copy_input(base_elf, source / 'base.elf', 48 * 1024**2, 0o400, minimum=1)
            sandbox_inputs.copy_input(head_elf, source / 'head.elf', 48 * 1024**2, 0o400, minimum=1)
        except OSError as error:
            raise ValueError('unable to copy size input safely: ' + str(error)) from error
        child = ['/bin/sh', '-c', SCRIPT, 'gate-size',
                 '/toolchain/bin/arm-none-eabi-' if compiler is not None else '', architecture]
        if runtime is None:
            command = sandbox.memory_command(source, child, toolchain=compiler, workspace_bytes=128 * 1024**2)
            captured = sandbox_process.run(command, max_stdout_bytes=RESULT_BYTES, timeout_seconds=120)
        else:
            captured = runtime.run(source, child, toolchain=compiler, workspace_bytes=128 * 1024**2,
                                   max_stdout_bytes=RESULT_BYTES, timeout_seconds=120)
        if captured.returncode or captured.problem:
            tail = captured.stderr[-4096:].decode('utf-8', errors='replace')
            raise ValueError('isolated size checker failed: ' + (captured.problem or str(captured.returncode))
                             + ('\n' + tail if tail else ''))
        return decode_result(captured.stdout, architecture)
