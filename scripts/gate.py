#!/usr/bin/env python3
"""smoothie-gate: run every check on a local Smoothieware repository and write one report.

  smoothie-gate run --repo ~/dev/smoothieware/Smoothieware --base origin/edge --out /media/arthur/tmp/gate-run

Compares --head (default HEAD) with --base. Builds (Release and Debug, base and head), size, simulator step
traces, AI review and the merged report all run here; compiles go to ni-vm through ni-build. Output: JSONL on
stdout, OUT/report/comment.md (the text a pull request comment would carry), OUT/report/report.html and one
OUT/results/**/<stage>.result.json per stage. Exit 1 when the overall status is fail.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
from profiles import EXPECTED_STAGES, PROFILES, TOOLCHAIN_VERSIONS, select_profile  # noqa: E402
from gate_result import make_result  # noqa: E402
import sandbox_dispatch
import sandbox
import build_artifacts
import controller_snapshot
import revision_metadata
from git_inputs import review_diff
from input_snapshot import snapshot_files


def emit(**event):
    print(json.dumps(event), flush=True)


def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def git(repo, *args):
    done = run(["git", "-C", repo, *args])
    if done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def write_review_diff(repo, base, head, destination):
    """Retain the same protected textual diff used by CI, without newline translation."""
    payload = review_diff(repo, base, head, merge_base=True)
    with open(destination, 'xb') as handle:
        handle.write(payload)


def export_tree(repo, ref, destination):
    """A clean copy of the committed tree at ref (git archive), so dirty files never reach a build."""
    os.makedirs(destination)
    archive = subprocess.Popen(["git", "-C", repo, "archive", ref], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", destination], stdin=archive.stdout, check=True)
    if archive.wait():
        raise SystemExit(f"git archive {ref} failed")


def write_revision_metadata(repo, revision, tree):
    """Make an isolated, minimal Git object database for V2's embedded version string."""
    commit = subprocess.check_output(["git", "-C", repo, "cat-file", "commit", revision])
    return revision_metadata.write(tree, revision, commit)


def validate_firmware_tree(tree, profile):
    required = ("makefile", "src/main.cpp") if profile.generation == 1 else ("Firmware/Rakefile", "Firmware/src/main.cpp")
    missing = [path for path in required if not os.path.isfile(os.path.join(tree, path))]
    if missing:
        raise ValueError(f"firmware tree does not match {profile.name}; missing " + ", ".join(missing))


def tracked_simulator_files(source):
    done = run(["git", "-C", source, "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if done.returncode:
        raise ValueError("simulator must be a Git checkout: " + done.stderr.strip())
    excluded_roots = {".codex", ".ilium", ".wrangler", "sim-result.json"}
    return sorted(name for name in done.stdout.split("\0") if name and name.split("/", 1)[0] not in excluded_roots)


def snapshot_simulator(source, destination):
    """Copy source working-tree bytes once and hash the exact files the run will use."""
    names = sorted(tracked_simulator_files(source))
    if not names:
        raise ValueError("simulator has no tracked files")
    return snapshot_files(source, destination, names)


def snapshot_simulator_dependencies(source, frozen_simulator, destination_parent):
    """Freeze the canonical library required by the inspected V2 source key.

    Inspect frozen simulator bytes, then record the separate library content
    identity. Old simulators without this dependency remain supported.
    """
    detector = os.path.join(frozen_simulator, 'tools', 'v2_source_key.py')
    if not os.path.isfile(detector):
        return {}
    with open(detector, 'rb') as handle:
        if b'lib-smoothie' not in handle.read():
            return {}
    library = os.path.join(os.path.dirname(os.path.abspath(source)), 'lib-smoothie')
    required = {'include/smoothie/v2/hal/Pin.h', 'include/smoothie/v2/v2_callback_context.h',
                'include/smoothie/v2/v2_step_timer.h', 'include/smoothie/v2/v2_scheduler.h',
                'src/v2/v2_step_timer.cpp', 'src/v2/v2_scheduler.cpp', 'src/v2/tmr_setup.cpp',
                'tools/stage_v2.py', 'tools/stage_v2_patch.py'}
    names = set()
    for directory, suffix in (('include', '.h'), ('src', '.cpp'), ('tools', '.py')):
        for root, dirs, files in os.walk(os.path.join(library, directory), followlinks=False):
            if any(os.path.islink(os.path.join(root, name)) for name in dirs):
                raise ValueError('shared simulator library directories cannot be symlinks')
            names.update(os.path.relpath(os.path.join(root, name), library)
                         for name in files if name.endswith(suffix))
    missing = required - names
    if missing:
        raise ValueError('missing canonical lib-smoothie dependency: ' + ', '.join(sorted(missing)))
    if os.path.isfile(os.path.join(library, 'extraction-provenance.json')):
        names.add('extraction-provenance.json')
    digest = snapshot_files(library, os.path.join(destination_parent, 'lib-smoothie'), sorted(names))
    return {'lib-smoothie': {'sha256': digest}}


def snapshot_case_tree(source, destination):
    """Freeze case configurations, inputs, references and coverage metadata together."""
    names = []
    for root, dirs, files in os.walk(source, followlinks=False):
        if any(os.path.islink(os.path.join(root, name)) for name in dirs):
            raise ValueError("case directories cannot be symlinks")
        names.extend(os.path.relpath(os.path.join(root, name), source) for name in files)
    if not names:
        raise ValueError("case directory contains no files")
    return snapshot_files(source, destination, sorted(names))


def stage(name, command, **kwargs):
    done = run(command, **kwargs)
    emit(type="progress", stage=name, exit_code=done.returncode)
    return done


def build_one(label, build_type, tree, out, toolchain, profile):
    folder = os.path.join(out, "results", f"build-{label}-{build_type.lower()}")
    os.makedirs(folder)
    command = [sys.executable, "-I", os.path.join(HERE, "build.py"), "--tree", tree, "--out", folder,
               "--build-type", build_type, "--label", label, "--toolchain", toolchain, "--profile", profile.name]
    if build_type == "Debug" and profile.generation == 1:
        command.append("--measure-only")
    if profile.generation == 2:
        command.append("--revision-metadata")
    stage(f"build-{label}-{build_type.lower()}", command)
    return os.path.join(folder, profile.elf)


def copy_simulator_input(source, destination, *, ignore=None):
    """Preserve links without reading their targets, then reject them before dispatch.

    Source trees must remain stable during copying. Preserving links avoids
    importing their target bytes. The resulting private input is never executed
    or dispatched until its complete link check succeeds.
    """
    source = Path(source).absolute()
    if source.resolve() != source or not source.is_dir():
        raise ValueError('simulator input root must be a real directory')
    shutil.copytree(source, destination, symlinks=True, ignore=ignore)
    for root, dirs, files in os.walk(destination, followlinks=False):
        if any(os.path.islink(os.path.join(root, name)) for name in dirs + files):
            raise ValueError('simulator source contains a symbolic link')


def build_simulator(head_tree, sim_dir, work, toolchain, profile, label, five=False):
    """Build smoothie-sim from the head tree as a whole ni-build job; returns the binary path or None."""
    root = os.path.join(work, "sim-job-" + label)
    os.makedirs(root)
    try:
        copy_simulator_input(sim_dir, os.path.join(root, "sim"), ignore=shutil.ignore_patterns(".git", "build", "docs"))
        shared = os.path.join(os.path.dirname(sim_dir), 'lib-smoothie')
        if profile.generation == 2 and os.path.isdir(shared):
            copy_simulator_input(shared, os.path.join(root, 'lib-smoothie'))
        if profile.generation == 2:
            os.mkdir(os.path.join(root, "pr"))
            copy_simulator_input(os.path.join(head_tree, "Firmware"), os.path.join(root, "pr", "Firmware"))
        else:
            copy_simulator_input(head_tree, os.path.join(root, "pr"))
        out = os.path.join(work, "sim-out-" + label)
        os.makedirs(out)
        command = sandbox_dispatch.simulator_command(root, out, profile.generation, five=five)
    except (OSError, ValueError) as error:
        emit(type="error", message="Simulator input preparation failed: " + str(error))
        return None
    done = stage("simulator-build-" + label, command)
    if done.returncode == 0:
        try:
            collected = Path(work) / ("sim-collected-" + label)
            build_artifacts.collect(Path(out) / '.gate-simulator-finals', collected,
                                    {profile.simulator: 48 * 1024**2}, max_total_bytes=48 * 1024**2)
            candidate = collected / profile.simulator
            candidate.chmod(0o500)
            return str(candidate)
        except (OSError, ValueError) as error:
            emit(type="error", message="Simulator final collection failed: " + str(error))
    return None


def replay_simulator(simulators, cases, cases_copy, sim_dir, work, sim_out, profile):
    """Replay on ni-vm as an isolated whole job and retain even a failed check's result."""
    root = os.path.join(work, "replay-job")
    os.makedirs(root)
    shutil.copytree(HERE, os.path.join(root, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(sim_dir, "checkers"), os.path.join(root, "sim", "checkers"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(cases, os.path.join(root, "cases"))
    if profile.generation == 2:
        shutil.copytree(cases_copy, os.path.join(root, "inventory"))
    for label in ("pr", "base", "pr-five", "base-five"):
        if simulators.get(label):
            target = os.path.join(root, "sim-" + label)
            shutil.copy2(simulators[label], target)
            os.chmod(target, 0o755)
    args = ["--sandbox", "--sim", "sim-pr", "--cases", "cases", "--out", "output", "--sim-dir", "sim", "--require-base-sim"]
    if profile.generation == 2:
        args += ["--coverage-manifest", "inventory/v2/coverage.json", "--coverage-baseline", "inventory"]
    if simulators["base"]:
        args += ["--base-sim", "sim-base"]
    if simulators.get("pr-five"):
        args += ["--sim-five", "sim-pr-five"]
    if simulators.get("base-five"):
        args += ["--base-sim-five", "sim-base-five"]
    returned = os.path.join(work, "replay-return")
    os.makedirs(returned)
    limited = sandbox.limit_command(['/usr/bin/python3', '-B', '-I', 'scripts/sim_stage.py', *args])
    command = ["ni-build", "arm", "--toolchain", "gcc12", "--root", root, "--include", ".",
               "--cwd", ".", "--out", returned, "--", "sh", "-c",
               'set -eu; export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus; mkdir output; "$@" || :',
               "gate-replay", *limited]
    done = stage("simulator", command)
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") in ("receipt", "error", "warning"):
            emit(**event)
    remote_result = os.path.join(returned, "output", "sim.result.json")
    if done.returncode == 0 and os.path.isfile(remote_result):
        shutil.copytree(os.path.dirname(remote_result), sim_out, dirs_exist_ok=True)
        return
    result = make_result("sim", "error", "Simulator step traces", "remote replay did not produce a result",
                         details="The whole-job simulator replay failed or returned no result; see its dispatcher receipt.")
    with open(os.path.join(sim_out, "sim.result.json"), "w") as handle:
        json.dump(result, handle, indent=1)


def measure_size(elfs, results, sim_dir, base_rev, toolchain, profile):
    output = os.path.join(results, "size.result.json")
    received = False
    if all(os.path.isfile(path) for path in elfs.values()):
        import size_dispatch
        received = size_dispatch.measure(elfs, results, sim_dir, base_rev, toolchain, profile, stage)
    if not received:
        result = make_result("size", "error", "Flash and RAM", "size comparison could not be completed",
                             details="A required build output is missing or the size checker failed; no size comparison was made.")
        with open(output, "w") as handle:
            json.dump(result, handle, indent=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--repo", required=True, help="Smoothieware git checkout")
    parser.add_argument("--profile", default="v1-lpc1768", choices=PROFILES, help="firmware generation and board")
    parser.add_argument("--base", required=True, help="base ref, for example origin/edge")
    parser.add_argument("--head", default="HEAD", help="ref under test (default HEAD)")
    parser.add_argument("--out", required=True, help="new folder for the run (under a marked scratch folder)")
    parser.add_argument("--sim-dir", default=os.environ.get("SMOOTHIE_SIM", os.path.join(ROOT, "..", "smoothie-sim")))
    parser.add_argument("--toolchain", choices=("gcc48", "gcc12", "gcc10"))
    parser.add_argument("--cases", help="case directory (defaults to the selected firmware's cases)")
    parser.add_argument("--no-model", action="store_true", help="skip the AI review (nothing leaves this machine)")
    parser.add_argument("--controller-sha256", help=argparse.SUPPRESS)
    args = parser.parse_args()

    profile = select_profile(args.profile)
    args.toolchain = args.toolchain or profile.toolchain
    if args.toolchain not in (("gcc48", "gcc12") if profile.generation == 1 else ("gcc10",)):
        parser.error("toolchain does not match the selected firmware profile")
    try:
        controller_sha256 = controller_snapshot.enter(ROOT, args)
    except (OSError, ValueError) as error:
        raise SystemExit(f"controller snapshot: {error}") from error
    repo, sim_dir, out = args.repo, args.sim_dir, args.out
    base_rev, head_rev = git(repo, "rev-parse", args.base), git(repo, "rev-parse", args.head)
    work = os.path.join(out, "work")
    os.makedirs(os.path.join(out, "results"))
    sim_rev = run(["git", "-C", sim_dir, "rev-parse", "HEAD"])
    sim_status = run(["git", "-C", sim_dir, "status", "--porcelain", "--untracked-files=normal"])
    simulator_revision = sim_rev.stdout.strip() if sim_rev.returncode == 0 else "unknown"
    if sim_status.returncode == 0 and sim_status.stdout.strip():
        simulator_revision += "+dirty-or-untracked"
    simulator_copy = os.path.join(work, "simulator-input")
    simulator_sha256 = snapshot_simulator(sim_dir, simulator_copy)
    dependencies = (snapshot_simulator_dependencies(sim_dir, simulator_copy, work)
                    if profile.generation == 2 else {})
    sim_dir = simulator_copy
    cases_copy = os.path.join(work, "case-input")
    baseline_sha256 = snapshot_case_tree(os.path.join(ROOT, "cases"), cases_copy)
    if args.cases:
        cases = os.path.join(work, "custom-case-input")
        references_sha256 = snapshot_case_tree(os.path.abspath(args.cases), cases)
    else:
        cases = os.path.join(cases_copy, "v2") if profile.generation == 2 else cases_copy
        references_sha256 = baseline_sha256
    manifest = {"profile": profile.name, "board": profile.board, "firmware_repository": profile.repository,
                "controller_sha256": controller_sha256,
                "base": base_rev, "head": head_rev,
                "toolchain": args.toolchain, "toolchain_version": TOOLCHAIN_VERSIONS[args.toolchain],
                "simulator_revision": simulator_revision,
                "simulator_sha256": simulator_sha256,
                "simulator_dependencies": dependencies,
                "references_sha256": references_sha256, "case_inventory_sha256": baseline_sha256,
                "expected_stages": list(EXPECTED_STAGES)}
    if profile.generation == 2:
        manifest["firmware_version_source"] = "isolated gate-snapshot branch and exact commit object; firmware embeds abbreviated commit"
    with open(os.path.join(out, "results", "manifest.json"), "w") as handle:
        json.dump(manifest, handle, indent=2)
    emit(type="progress", stage="start", base=base_rev, head=head_rev, profile=profile.name)

    trees = {}
    for label, rev in (("base", base_rev), ("pr", head_rev)):
        for build_type in ("release", "debug"):
            trees[(label, build_type)] = os.path.join(work, f"tree-{label}-{build_type}")
            export_tree(repo, rev, trees[(label, build_type)])
            validate_firmware_tree(trees[(label, build_type)], profile)
            if profile.generation == 2:
                write_revision_metadata(repo, rev, trees[(label, build_type)])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        elfs = {key: pool.submit(build_one, key[0], key[1].capitalize(), tree, out, args.toolchain, profile)
                for key, tree in trees.items()}
        sim_futures = {label: pool.submit(build_simulator, trees[(label, "release")], sim_dir, work,
                                          args.toolchain, profile, label) for label in ("base", "pr")}
        if profile.generation == 2:
            sim_futures.update({label + "-five": pool.submit(build_simulator, trees[(label, "release")], sim_dir,
                                  work, args.toolchain, profile, label + "-five", five=True)
                                for label in ("base", "pr")})
        elfs = {key: future.result() for key, future in elfs.items()}
        simulators = {label: future.result() for label, future in sim_futures.items()}

    results = os.path.join(out, "results")
    measure_size(elfs, results, sim_dir, base_rev, args.toolchain, profile)
    sim_out = os.path.join(results, "sim")
    os.makedirs(sim_out)
    replay_simulator(simulators, cases, cases_copy, sim_dir, work, sim_out, profile)

    diff_path = os.path.join(work, "pr.diff")
    ai_out = os.path.join(results, "ai")
    os.makedirs(ai_out)
    try:
        write_review_diff(repo, base_rev, head_rev, diff_path)
    except (OSError, ValueError) as error:
        result = make_result('ai-review', 'error', 'Developer-rule and AI review',
                             'Committed review diff is unavailable',
                             details='Diff preparation failed; review did not run. See operation diagnostics.')
        with open(os.path.join(ai_out, 'ai-review.result.json'), 'x') as handle:
            json.dump(result, handle)
        emit(type='error', stage='ai-review', message=str(error))
    else:
        log = git(repo, "log", "-1", "--format=title: %s%n%n%b", head_rev)
        pr_text = os.path.join(work, "pr-text.txt")
        with open(pr_text, "w") as handle:
            handle.write(log)
        ai_command = [sys.executable, "-I", os.path.join(HERE, "ai_review.py"), "--diff", diff_path, "--pr-text", pr_text,
                      "--out", ai_out, "--prompts", os.path.join(ROOT, "prompts"), "--sim-dir", sim_dir,
                      "--firmware-version", str(profile.generation)]
        if args.no_model:
            ai_command.append("--no-model")
        stage("ai-review", ai_command)

        guidance_command = [sys.executable, "-I", os.path.join(HERE, "guidance.py"), "--results", results, "--diff", diff_path,
                            "--pr-text", pr_text, "--out", os.path.join(results, "guidance"), "--prompts", os.path.join(ROOT, "prompts"),
                            "--firmware-version", str(profile.generation)]
        if args.no_model:
            guidance_command.append("--no-model")
        stage("guidance", guidance_command)

    report_dir = os.path.join(out, "report")
    os.makedirs(report_dir)
    done = stage("report", [sys.executable, "-I", os.path.join(HERE, "report.py"), "--results", results, "--out", report_dir])
    for line in done.stdout.splitlines():
        print(line)
    emit(type="artifact", comment=os.path.join(report_dir, "comment.md"), html=os.path.join(report_dir, "report.html"))
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
