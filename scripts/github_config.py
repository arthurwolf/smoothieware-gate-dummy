"""Validate trusted workflow configuration before source selection or execution."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller_snapshot
from ci_inputs import TOOLCHAIN_SHA256
import dispatch_identity
from profiles import select_profile
from request_json import read_object

TOOLCHAIN_URLS = {
    'gcc48': 'https://launchpadlibrarian.net/170926605/gcc-arm-none-eabi-4_8-2014q1-20140314-linux.tar.bz2',
    'gcc10': 'https://developer.arm.com/-/media/Files/downloads/gnu-rm/10.3-2021.10/gcc-arm-none-eabi-10.3-2021.10-x86_64-linux.tar.bz2',
}


def configuration(policy, controller, repository, gate_ref, sim_ref):
    dispatch_identity._validate_policy(policy)
    pins = policy['pins']
    if repository != policy['repository'] or gate_ref != pins['gate_ref'] or sim_ref != pins['sim_ref']:
        raise ValueError('workflow repository or checkout refs differ from trusted policy')
    profile = select_profile(policy['profile'])
    if pins['toolchain_sha256'] != TOOLCHAIN_SHA256[profile.toolchain]:
        raise ValueError('workflow compiler differs from the canonical profile archive')
    if not isinstance(pins.get('simulator_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', pins['simulator_sha256']):
        raise ValueError('workflow requires an exact simulator input digest')
    library_ref = pins.get('shared_library_ref') if profile.generation == 2 else None
    if profile.generation == 2 and not dispatch_identity._match(dispatch_identity.REVISION, library_ref):
        raise ValueError('V2 workflow requires a pinned shared library checkout ref')
    controller = Path(controller).absolute()
    if controller.resolve() != controller:
        raise ValueError('workflow controller must have real ancestors')
    controller_snapshot.verify(controller, pins['controller_sha256'])
    return {'profile': profile.name, 'toolchain': profile.toolchain,
            'toolchain_sha256': pins['toolchain_sha256'], 'toolchain_url': TOOLCHAIN_URLS[profile.toolchain],
            'runtime_image': pins['runtime_image'], 'gate_ref': gate_ref, 'sim_ref': sim_ref,
            'shared_library_ref': library_ref}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('policy', 'controller', 'repository', 'gate-ref', 'sim-ref', 'out'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args(argv)
    try:
        output = Path(args.out).absolute(); controller = Path(args.controller).resolve()
        if (output.exists() or output.is_symlink() or not output.parent.is_dir()
                or output.parent.resolve() != output.parent or output == controller
                or controller in output.parents or output in controller.parents):
            raise ValueError('workflow configuration output must be fresh and outside the controller')
        policy = read_object(args.policy, 65536)
        result = configuration(policy, args.controller, args.repository, args.gate_ref, args.sim_ref)
        with output.open('x') as handle:
            json.dump(result, handle)
        print(json.dumps({'type': 'result', 'operation': 'workflow configuration', 'output': str(output)}))
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(json.dumps({'type': 'error', 'operation': 'workflow configuration', 'message': str(error), 'output': args.out}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
