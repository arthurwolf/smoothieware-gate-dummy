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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_result import validate_result, worst_status  # noqa: E402
from mdkit import clip_markdown, esc, html_table, neutralize_mentions  # noqa: E402

MARKER = "<!-- smoothie-gate-report -->"
STAGE_ORDER = ["build", "sim", "size", "ai-review"]  # a prefix match: build-pr-release counts as "build"
STAGE_ICON = {"build": "🔨", "sim": "🎛️", "size": "📏", "ai-review": "🤖", "guidance": "💌"}
VERDICT = {"pass": "✅ Every check passed.",
           "warn": "⚠️ Nothing blocks this pull request, but a few things deserve a look.",
           "fail": "❌ At least one check failed; the details below say which.",
           "error": "🛑 A check could not run properly, so the result is incomplete.",
           "skipped": "⏭️ No check produced a verdict."}
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skipped": "⏭️", "error": "🛑"}
MAX_DETAILS = 12000


def overall_status(results, problems):
    """A skipped check (for example a model outage) must not hide that the other checks passed."""
    statuses = [r["status"] for r in results] + (["error"] if problems else [])
    ran = [status for status in statuses if status != "skipped"]
    return worst_status(ran or statuses)


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
    results.sort(key=lambda r: (stage_rank(r["stage"]), r["title"]))
    return results, problems


def stage_kind(stage):
    return stage.split("-")[0] if stage.startswith("build-") else stage


def stage_rank(stage):
    kind = stage_kind(stage)
    return STAGE_ORDER.index(kind) if kind in STAGE_ORDER else 99


def place_charts(text, charts, image_base):
    """Swap <!--chart:KEY--> tokens for <img> tags; without an image URL or a chart the token is dropped."""
    def swap(match):
        chart = charts.get(match.group(1))
        if not chart or not image_base:
            return ""
        return (f'<img src="{html.escape(image_base.rstrip("/") + "/" + chart["file"], quote=True)}" '
                f'alt="{html.escape(chart["alt"], quote=True)}" width="760">')
    return re.sub(r"<!--chart:([a-z0-9-]+)-->", swap, text)


def render_comment(results, problems, run_url, charts=None, image_base=""):
    """One comment: headline, a full-width summary table, one section per stage, then the message to the contributor.
    Stage details are Markdown the stages built (their own fences and escapes); mentions are defused again here."""
    checks = [r for r in results if r["stage"] != "guidance"]
    guidance = next((r for r in results if r["stage"] == "guidance" and r["details"].strip()), None)
    overall = overall_status(checks, problems)
    lines = [MARKER, f"## 🛡️ smoothie-gate · {ICON[overall]} {overall}", "", VERDICT[overall], ""]
    if not checks and not problems:
        lines.append("No stage produced a result.")
    rows = []
    for r in checks:
        icon = STAGE_ICON.get(stage_kind(r["stage"]), "▫️")
        status = f"{ICON[r['status']]} <b>{r['status']}</b>" if r["status"] in ("fail", "error") else f"{ICON[r['status']]} {r['status']}"
        rows.append([f"{icon} {esc(r['title'])}", status, esc(r["summary"])])
    for problem in problems:
        rows.append(["📄 result file", f"{ICON['error']} <b>error</b>", esc(problem)])
    lines += [html_table(["Check", "Status", "Summary"], rows, widths=["30%", "14%", "56%"])]
    for r in checks:
        if not r["details"].strip():
            continue
        icon = STAGE_ICON.get(stage_kind(r["stage"]), "▫️")
        is_open = " open" if r["stage"] == "size" or (r["status"] != "pass" and len(r["details"]) < 4000) else ""
        lines += ["", f"<details{is_open}><summary>{icon} <b>{esc(r['title'])}</b> · {esc(r['summary'])}</summary>", "",
                  place_charts(neutralize_mentions(clip_markdown(r["details"], MAX_DETAILS)), charts or {}, image_base), "", "</details>"]
    if guidance:
        lines += ["", "---", "", "## 💌 A message for the contributor", "",
                  neutralize_mentions(clip_markdown(guidance["details"], MAX_DETAILS))]
    lines += ["", "---", "", "<sub>Size growth, review findings and warnings are advice for maintainers. "
              "Only a failed build, a forbidden blocking call, a simulator crash or an unexplained step mismatch "
              "fails the run.</sub>"]
    if run_url:
        lines.append(f"<sub>🔗 Full logs and artifacts: {run_url}</sub>")
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
    parser.add_argument("--charts", default="", help="charts.json written by charts.py")
    parser.add_argument("--image-base", default="", help="URL folder that holds the chart images")
    args = parser.parse_args()
    results, problems = load_results(args.results)
    charts = {}
    if args.charts and os.path.exists(args.charts):
        try:
            with open(args.charts) as handle:
                charts = json.load(handle)
        except (OSError, ValueError):
            charts = {}
    comment = render_comment(results, problems, args.run_url, charts, args.image_base)
    os.makedirs(args.out, exist_ok=True)
    for name, text in (("comment.md", comment), ("summary.md", comment.replace(MARKER + "\n", "")),
                       ("report.html", render_html(comment))):
        with open(os.path.join(args.out, name), "w") as handle:
            handle.write(text)
    overall = overall_status(results, problems)
    print(json.dumps({"type": "result", "status": overall, "stages": len(results), "invalid": len(problems),
                      "comment": os.path.join(args.out, "comment.md")}))
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
