"""Validate pipe-only sandbox finals without accepting untrusted host paths.

Transport: one UTF-8 JSON line maps exact filenames to positive byte lengths,
then raw file bytes in the trusted contract's iteration order. No archive parser,
link types, compression, nested paths or trailing bytes are supported. Only use
after bounded process capture has completed with a successful exit.
"""
import json

from build_artifacts import validate_contract

HEADER_BYTES = 4096


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('duplicate final-file declaration')
        result[name] = value
    return result


def decode(payload, expected, *, max_total_bytes=64 * 1024**2):
    """Return exact named bytes only after validating the entire transport."""
    validate_contract(expected, max_total_bytes)
    if max_total_bytes > 128 * 1024**2:
        raise ValueError('final-file transport limit exceeds 128 MiB')
    if not isinstance(payload, bytes) or len(payload) > max_total_bytes + HEADER_BYTES:
        raise ValueError('final-file transport is not bounded bytes')
    end = payload.find(b'\n', 0, HEADER_BYTES)
    if end < 0:
        raise ValueError('missing or excessive final-file header')
    try:
        sizes = json.loads(payload[:end].decode('utf-8'), object_pairs_hook=_unique_fields)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('invalid final-file header') from error
    if not isinstance(sizes, dict) or set(sizes) != set(expected):
        raise ValueError('final-file declarations do not match the expected contract')
    for name, limit in expected.items():
        if type(sizes[name]) is not int or not 0 < sizes[name] <= limit:
            raise ValueError('invalid final-file byte length: ' + name)
    total = sum(sizes.values())
    if total > max_total_bytes or len(payload) != end + 1 + total:
        raise ValueError('final-file transport is excessive, truncated or has trailing bytes')
    files, offset = {}, end + 1
    for name in expected:
        files[name] = payload[offset:offset + sizes[name]]
        offset += sizes[name]
    return files
