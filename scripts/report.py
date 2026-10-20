#!/usr/bin/env python3
"""Merge stage results into one pull request comment, a job summary and a report page.

  report.py --results DIR --out DIR [--run-url URL] [--pr-number N]

Reads every DIR/**/*.result.json (schema: documentation/result-schema.md). Writes OUT/comment.md (starts with the
hidden marker, so the workflow edits one comment in place), OUT/summary.md and OUT/report.html.
Output: JSONL on stdout. Invalid result files are reported as an error row, never skipped silently.
"""
import argparse
import glob
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import validate_result, worst_status  # noqa: E402

MARKER = "<!-- smoothie-gate-report -->"
STAGE_ORDER = ["build", "size", "ai-review", "sim"]
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skipped": "⏭️", "error": "🛑"}
MAX_DETAILS = 6000


def load_results(results_dir):
    """Return (valid results, problems). Duplicated stage names are kept: build runs per build type."""
    results, problems = [], []
    for path in sorted(glob.glob(os.path.join(results_dir, "**", "*.result.json"), recursive=True)):
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            problems.append(f"{os.path.basename(path)}: unreadable ({error})")
            continue
        issues = validate_result(data)
        if issues:
            problems.append(f"{os.path.basename(path)}: " + "; ".join(issues))
            continue
        results.append(data)
    results.sort(key=lambda r: (STAGE_ORDER.index(r["stage"]) if r["stage"] in STAGE_ORDER else 99, r["title"]))
    return results, problems


def clip(text):
    return text if len(text) <= MAX_DETAILS else text[:MAX_DETAILS] + "\n… (truncated, see the run artifact)"


def fence_safe(text):
    """Details come from PR-controlled output: neutralise code fences and mentions before embedding."""
    return text.replace("```", "ʼʼʼ").replace("@", "@​")


def render_comment(results, problems, run_url):
    overall = worst_status([r["status"] for r in results] + (["error"] if problems else []))
    lines = [MARKER, f"## smoothie-gate {ICON[overall]} {overall}", ""]
    if not results and not problems:
        lines.append("No stage produced a result.")
    lines += ["| Check | Status | Summary |", "| --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r['title']} | {ICON[r['status']]} {r['status']} | {r['summary'].replace('|', '/')} |")
    for problem in problems:
        lines.append(f"| result file | {ICON['error']} error | {problem.replace('|', '/')} |")
    for r in results:
        if r["details"].strip():
            lines += ["", f"<details><summary>{html.escape(r['title'])}</summary>", "",
                      fence_safe(clip(r["details"])), "", "</details>"]
    lines += ["", "Size growth, review findings and warnings are advice for maintainers. "
              "Only a failed build, a forbidden blocking call or an unexplained simulator step mismatch fails the run."]
    if run_url:
        lines.append(f"Full logs and artifacts: {run_url}")
    return "\n".join(lines) + "\n"


def render_html(comment):
    return ("<!doctype html><meta charset=utf-8><title>smoothie-gate report</title>"
            "<style>body{font:15px sans-serif;max-width:60em;margin:2em auto;padding:0 1em}"
            "@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}}pre{white-space:pre-wrap}</style>"
            f"<pre>{html.escape(comment)}</pre>\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    results, problems = load_results(args.results)
    comment = render_comment(results, problems, args.run_url)
    os.makedirs(args.out, exist_ok=True)
    for name, text in (("comment.md", comment), ("summary.md", comment.replace(MARKER + "\n", "")),
                       ("report.html", render_html(comment))):
        with open(os.path.join(args.out, name), "w") as handle:
            handle.write(text)
    overall = worst_status([r["status"] for r in results] + (["error"] if problems else []))
    print(json.dumps({"type": "result", "status": overall, "stages": len(results), "invalid": len(problems),
                      "comment": os.path.join(args.out, "comment.md")}))
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
