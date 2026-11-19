"""Trusted whole-job entrypoint for isolated native simulator compilation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox_build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--generation', type=int, choices=(1, 2), required=True)
    parser.add_argument('--five', action='store_true')
    parser.add_argument('--runtime-image', help='use an already provisioned Docker image pinned by digest')
    args = parser.parse_args()
    try:
        runtime = None
        if args.runtime_image:
            from container_runtime import DockerRuntime
            runtime = DockerRuntime(args.runtime_image)
        result = sandbox_build.build_simulator(args.tree, args.out, generation=args.generation,
                                               five=args.five, runtime=runtime)
    except (OSError, ValueError) as error:
        print(json.dumps({'type': 'error', 'message': str(error)}))
        return 75
    sys.stderr.buffer.write(result.diagnostics)
    print(json.dumps({'type': 'result', 'exit_code': result.returncode, 'problem': result.problem,
                      'diagnostics_truncated': result.diagnostics_truncated, 'files': result.files}))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
