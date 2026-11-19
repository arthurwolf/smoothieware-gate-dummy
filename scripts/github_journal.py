"""Durably retain uncertain GitHub mutations before one external attempt."""
import json
import os
from pathlib import Path


def run_once(output, intent, operation):
    output = Path(output).absolute()
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or output.parent.resolve() != output.parent or intent.get('status') != 'outcome-uncertain'):
        raise ValueError('write journal requires a fresh path and an explicit uncertain intent')

    def persist(handle, data):
        json.dump(data, handle, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())

    def flush_directory():
        descriptor = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    with output.open('x') as handle:
        persist(handle, intent)
    flush_directory()
    try:
        result = operation()
        if not isinstance(result, dict) or result.get('status') not in ('published','dispatched'):
            raise ValueError('external operation has no verified completion receipt')
    except (OSError, ValueError, RecursionError):
        return intent
    completed = output.with_name(output.name + '.completed')
    with completed.open('x') as handle:
        persist(handle, result)
    os.replace(completed, output)
    flush_directory()
    return result
