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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def emit(**event):
    print(json.dumps(event), flush=True)


def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def git(repo, *args):
    done = run(["git", "-C", repo, *args])
    if done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def export_tree(repo, ref, destination):
    """A clean copy of the committed tree at ref (git archive), so dirty files never reach a build."""
    os.makedirs(destination)
    archive = subprocess.Popen(["git", "-C", repo, "archive", ref], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", destination], stdin=archive.stdout, check=True)
    if archive.wait():
        raise SystemExit(f"git archive {ref} failed")


def stage(name, command, **kwargs):
    done = run(command, **kwargs)
    emit(type="progress", stage=name, exit_code=done.returncode)
    return done


def build_one(label, build_type, tree, out, toolchain):
    folder = os.path.join(out, "results", f"build-{label}-{build_type.lower()}")
    os.makedirs(folder)
    command = [sys.executable, "-I", os.path.join(HERE, "build.py"), "--tree", tree, "--out", folder,
               "--build-type", build_type, "--label", label, "--toolchain", toolchain]
    if build_type == "Debug":
        command.append("--measure-only")
    stage(f"build-{label}-{build_type.lower()}", command)
    return os.path.join(folder, "LPC1768", "main.elf")


def build_simulator(head_tree, sim_dir, work, toolchain):
    """Build smoothie-sim from the head tree as a whole ni-build job; returns the binary path or None."""
    root = os.path.join(work, "sim-job")
    os.makedirs(root)
    shutil.copytree(sim_dir, os.path.join(root, "sim"), ignore=shutil.ignore_patterns(".git", "build", "docs"))
    shutil.copytree(head_tree, os.path.join(root, "pr"))
    out = os.path.join(work, "sim-out")
    os.makedirs(out)
    done = stage("simulator-build", ["ni-build", "arm", "--toolchain", toolchain, "--root", root, "--include", ".",
                                     "--cwd", ".", "--out", out, "--", "make", "-C", "sim", "-j32", "COVERAGE=0",
                                     "SMOOTHIE_SRC=../pr", "BUILD=../simbuild", "../simbuild/smoothie-sim"])
    for candidate in (os.path.join(out, "simbuild", "smoothie-sim"), os.path.join(out, "smoothie-sim")):
        if done.returncode == 0 and os.path.isfile(candidate):
            os.chmod(candidate, 0o755)
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--repo", required=True, help="Smoothieware git checkout")
    parser.add_argument("--base", required=True, help="base ref, for example origin/edge")
    parser.add_argument("--head", default="HEAD", help="ref under test (default HEAD)")
    parser.add_argument("--out", required=True, help="new folder for the run (under a marked scratch folder)")
    parser.add_argument("--sim-dir", default=os.environ.get("SMOOTHIE_SIM", os.path.join(ROOT, "..", "smoothie-sim")))
    parser.add_argument("--toolchain", default="gcc48", choices=("gcc48", "gcc12"))
    parser.add_argument("--no-model", action="store_true", help="skip the AI review (nothing leaves this machine)")
    args = parser.parse_args()

    repo, sim_dir = os.path.abspath(args.repo), os.path.abspath(args.sim_dir)
    out = os.path.abspath(args.out)
    if os.path.exists(out):
        raise SystemExit(f"{out} exists; pass a new folder")
    base_rev, head_rev = git(repo, "rev-parse", args.base), git(repo, "rev-parse", args.head)
    work = os.path.join(out, "work")
    os.makedirs(os.path.join(out, "results"))
    emit(type="progress", stage="start", base=base_rev, head=head_rev)

    trees = {}
    for label, rev in (("base", base_rev), ("pr", head_rev)):
        for build_type in ("release", "debug"):
            trees[(label, build_type)] = os.path.join(work, f"tree-{label}-{build_type}")
            export_tree(repo, rev, trees[(label, build_type)])

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        elfs = {key: pool.submit(build_one, key[0], key[1].capitalize(), tree, out, args.toolchain)
                for key, tree in trees.items()}
        sim_future = pool.submit(build_simulator, trees[("pr", "release")], sim_dir, work, args.toolchain)
        elfs = {key: future.result() for key, future in elfs.items()}
        simulator = sim_future.result()

    results = os.path.join(out, "results")
    if all(os.path.isfile(path) for path in elfs.values()):
        stage("size", [sys.executable, "-I", os.path.join(HERE, "size.py"),
                       "--base-release", elfs[("base", "release")], "--pr-release", elfs[("pr", "release")],
                       "--base-debug", elfs[("base", "debug")], "--pr-debug", elfs[("pr", "debug")],
                       "--base-rev", base_rev, "--toolchain", args.toolchain, "--sim-dir", sim_dir,
                       "--out", os.path.join(results, "size.result.json")])
    if simulator:
        sim_out = os.path.join(results, "sim")
        os.makedirs(sim_out)
        stage("simulator", [sys.executable, "-I", os.path.join(HERE, "sim_stage.py"), "--sim", simulator,
                            "--cases", os.path.join(ROOT, "cases"), "--out", sim_out, "--sim-dir", sim_dir])

    diff_path = os.path.join(work, "pr.diff")
    with open(diff_path, "w") as handle:
        handle.write(git(repo, "diff", f"{base_rev}...{head_rev}") + "\n")
    ai_out = os.path.join(results, "ai")
    os.makedirs(ai_out)
    log = git(repo, "log", "-1", "--format=title: %s%n%n%b", head_rev)
    pr_text = os.path.join(work, "pr-text.txt")
    with open(pr_text, "w") as handle:
        handle.write(log)
    ai_command = [sys.executable, "-I", os.path.join(HERE, "ai_review.py"), "--diff", diff_path, "--pr-text", pr_text,
                  "--out", ai_out, "--prompts", os.path.join(ROOT, "prompts"), "--sim-dir", sim_dir]
    if args.no_model:
        ai_command.append("--no-model")
    stage("ai-review", ai_command)

    report_dir = os.path.join(out, "report")
    os.makedirs(report_dir)
    done = stage("report", [sys.executable, "-I", os.path.join(HERE, "report.py"), "--results", results, "--out", report_dir])
    for line in done.stdout.splitlines():
        print(line)
    emit(type="artifact", comment=os.path.join(report_dir, "comment.md"), html=os.path.join(report_dir, "report.html"))
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
