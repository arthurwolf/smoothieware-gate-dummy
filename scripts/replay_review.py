#!/usr/bin/env python3
"""Replay historical Smoothieware pull requests through the AI review stage and score it.

  replay_review.py --out OUT [--prs replay/prs.json] [--only N ...] [--no-model] [-- ai_review options]

For each pull request in replay/prs.json the diff and text are fetched with `gh` (cached in OUT/diffs), ai_review.py
runs on it, and OUT/replay.json plus OUT/replay.md record the outcome. A bad pull request is "caught" when a finding
(pattern scan or model with confidence >= 0.5) carries one of its expected rules. A good (merged) pull request is
"clean" when the stage reports no finding; any finding is listed for manual judgement.
Progress: OUT/progress.json {done, total, current}, rewritten after every pull request.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def fetch(repo, number, out):
    diff_path = os.path.join(out, "diffs", f"{number}.diff")
    text_path = os.path.join(out, "diffs", f"{number}.json")
    if not os.path.isfile(diff_path):
        diff = subprocess.run(["gh", "pr", "diff", str(number), "-R", repo], capture_output=True, text=True, check=True)
        with open(diff_path, "w") as handle:
            handle.write(diff.stdout)
    if not os.path.isfile(text_path):
        view = subprocess.run(["gh", "pr", "view", str(number), "-R", repo, "--json", "title,headRefName,body"],
                              capture_output=True, text=True, check=True)
        data = json.loads(view.stdout)
        with open(text_path, "w") as handle:
            json.dump({"title": data["title"], "branch": data["headRefName"], "body": data["body"]}, handle)
    return diff_path, text_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="existing scratch folder")
    parser.add_argument("--prs", default=os.path.join(ROOT, "replay", "prs.json"))
    parser.add_argument("--only", type=int, action="append", default=[])
    parser.add_argument("--no-model", action="store_true")
    args, passthrough = parser.parse_known_args()
    passthrough = [a for a in passthrough if a != "--"]
    with open(args.prs) as handle:
        spec = json.load(handle)
    jobs = [("bad", item["pr"], item["expect"], item["why"]) for item in spec["bad"]]
    jobs += [("good", number, [], "merged into edge") for number in spec["good"]]
    jobs = [job for job in jobs if not args.only or job[1] in args.only]
    os.makedirs(os.path.join(args.out, "diffs"), exist_ok=True)
    rows = []
    for index, (kind, number, expect, why) in enumerate(jobs):
        with open(os.path.join(args.out, "progress.json"), "w") as handle:
            json.dump({"done": index, "total": len(jobs), "current": f"{kind} PR {number}"}, handle)
        diff_path, text_path = fetch(spec["repo"], number, args.out)
        run_dir = os.path.join(args.out, f"run-{number}")
        os.makedirs(run_dir, exist_ok=True)
        command = [sys.executable, "-I", os.path.join(HERE, "ai_review.py"), "--diff", diff_path, "--pr-text", text_path,
                   "--out", run_dir] + (["--no-model"] if args.no_model else []) + passthrough
        subprocess.run(command, capture_output=True, text=True)
        with open(os.path.join(run_dir, "ai-review.result.json")) as handle:
            result = json.load(handle)
        with open(os.path.join(run_dir, "ai-review.findings.json")) as handle:
            findings = json.load(handle)
        sure = [f for f in findings["scan"] + findings["model"] if f["confidence"] >= 0.5]
        rules = sorted({f["rule"] for f in sure})
        caught = bool(set(rules) & set(expect)) if kind == "bad" else None
        rows.append({"pr": number, "set": kind, "why": why, "status": result["status"], "summary": result["summary"],
                     "expected_rules": expect, "rules_found": rules, "caught": caught,
                     "findings": [{"file": f["file"], "line": f["line"], "rule": f["rule"], "severity": f["severity"],
                                   "confidence": f["confidence"], "source": f["source"], "message": f["message"][:300],
                                   "evidence": f["evidence"][:160]} for f in sure],
                     "dropped": len(findings["dropped"]), "errors": findings["errors"], "models": result["metrics"]["models"]})
    with open(os.path.join(args.out, "progress.json"), "w") as handle:
        json.dump({"done": len(jobs), "total": len(jobs), "current": "finished"}, handle)
    with open(os.path.join(args.out, "replay.json"), "w") as handle:
        json.dump(rows, handle, indent=1)
    bad = [r for r in rows if r["set"] == "bad"]
    good = [r for r in rows if r["set"] == "good"]
    lines = ["| PR | set | stage status | expected rule | rules found | caught |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['pr']} | {r['set']} | {r['status']} | {', '.join(r['expected_rules']) or '-'} | "
                     f"{', '.join(r['rules_found']) or '-'} | {'yes' if r['caught'] else ('no' if r['caught'] is False else '-')} |")
    summary = {"type": "result", "bad_total": len(bad), "bad_caught": sum(1 for r in bad if r["caught"]),
               "good_total": len(good), "good_with_findings": sum(1 for r in good if r["findings"]),
               "good_findings": sum(len(r["findings"]) for r in good), "skipped": sum(1 for r in rows if r["status"] == "skipped")}
    with open(os.path.join(args.out, "replay.md"), "w") as handle:
        handle.write("\n".join(lines) + "\n")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
