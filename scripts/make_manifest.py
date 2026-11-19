#!/usr/bin/env python3
"""Snapshot case and simulator inputs and write a firmware run manifest."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402
import controller_snapshot
from profiles import EXPECTED_STAGES, PROFILES, TOOLCHAIN_VERSIONS, select_profile  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--toolchain", required=True, choices=TOOLCHAIN_VERSIONS)
    parser.add_argument("--sim-dir", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--inputs", required=True, help="new retained input directory used by later stages")
    parser.add_argument("--controller-sha256", help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ('out', 'inputs', 'sim_dir', 'cases'):
        setattr(args, name, os.path.abspath(getattr(args, name)))
    if os.path.lexists(args.out):
        parser.error('manifest output already exists')
    inputs = Path(args.inputs)
    if Path(args.out).resolve().is_relative_to(inputs.resolve()):
        parser.error('manifest output must be outside the retained input directory')
    frozen = inputs / 'controller-input'
    if args.controller_sha256 is None:
        inputs.mkdir()
        digest = controller_snapshot.freeze(gate.ROOT, frozen)
        command = [sys.executable, '-B', '-I', str(frozen / 'scripts/make_manifest.py')]
        for flag, value in (('--profile', args.profile), ('--base', args.base), ('--head', args.head),
                            ('--toolchain', args.toolchain), ('--sim-dir', args.sim_dir),
                            ('--cases', args.cases), ('--inputs', args.inputs), ('--out', args.out),
                            ('--controller-sha256', digest)):
            command.extend((flag, value))
        os.execv(sys.executable, command)
        raise RuntimeError('manifest preparation restart unexpectedly returned')
    if Path(gate.ROOT) != frozen or inputs.is_symlink():
        parser.error('manifest preparation must execute its retained controller')
    controller_snapshot.verify(frozen, args.controller_sha256)
    if set(path.name for path in inputs.iterdir()) != {'controller-input'}:
        parser.error('manifest preparation requires unused input directory')
    return write_manifest(args, args.controller_sha256)


def write_manifest(args, controller_sha256):
    """Prepare retained stage inputs from the already verified controller process."""
    profile = select_profile(args.profile)
    revision = gate.git(args.sim_dir, "rev-parse", "HEAD")
    status = gate.git(args.sim_dir, "status", "--porcelain", "--untracked-files=normal")
    if status:
        revision += "+dirty-or-untracked"
    frozen_sim = os.path.join(args.inputs, "sim")
    sim_hash = gate.snapshot_simulator(args.sim_dir, frozen_sim)
    dependencies = (gate.snapshot_simulator_dependencies(args.sim_dir, frozen_sim, args.inputs)
                    if profile.generation == 2 else {})
    case_hash = gate.snapshot_case_tree(args.cases, os.path.join(args.inputs, "cases"))
    inventory_hash = gate.snapshot_case_tree(os.path.join(gate.ROOT, 'cases'),
                                              os.path.join(args.inputs, 'case-inventory'))
    manifest = {"profile": profile.name, "firmware_repository": profile.repository, "board": profile.board,
                "controller_sha256": controller_sha256,
                "base": args.base, "head": args.head, "toolchain": args.toolchain,
                "toolchain_version": TOOLCHAIN_VERSIONS[args.toolchain],
                "simulator_revision": revision, "simulator_sha256": sim_hash,
                "simulator_dependencies": dependencies,
                "references_sha256": case_hash, "case_inventory_sha256": inventory_hash,
                "expected_stages": list(EXPECTED_STAGES)}
    with open(args.out, "x") as handle:
        json.dump(manifest, handle, indent=1)
    print(json.dumps({"type": "result", "manifest": args.out, "inputs": args.inputs,
                      "controller": os.path.join(args.inputs, 'controller-input'),
                      "simulator": frozen_sim, "cases": os.path.join(args.inputs, 'cases'),
                      "case_inventory": os.path.join(args.inputs, 'case-inventory')}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
