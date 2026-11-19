"""Bounded object-only JSON files shared by trusted workflow adapters."""
import json
from github_preflight import _unique_object


def read_object(filename, limit):
    with open(filename, 'rb') as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('CI request JSON exceeds byte limit')
    value = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError('CI request JSON must be an object')
    return value
