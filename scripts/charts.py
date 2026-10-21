#!/usr/bin/env python3
"""Draw the flash and RAM charts of the size stage as SVG images.

  charts.py --results DIR --out DIR

Reads DIR/**/size.result.json and writes OUT/size-release.svg, OUT/size-debug.svg and OUT/charts.json
(key -> {file, alt}). The charts are drawn here, in the trusted comment job, from numbers only: every value is
checked to be an integer and every name is XML-escaped, so nothing the pull request controls can end up as markup.
Standard library only. A missing or malformed size result produces no chart and exit code 0 (the report then shows
its tables without images).
"""
import argparse
import glob
import json
import os
import re
import sys
from xml.sax.saxutils import escape

CAPACITY = {"Flash": 512 * 1024, "RAM": 32 * 1024, "AHB RAM": 32 * 1024}  # LPC1768
UNIT = {"Flash": "flash", "RAM": "RAM", "AHB RAM": "AHB RAM"}

# GitHub dark theme palette
BG, BORDER, TRACK = "#0d1117", "#30363d", "#21262d"
TEXT, MUTED = "#e6edf3", "#8b949e"
BLUE, ORANGE, GREEN, RED, YELLOW = "#388bfd", "#db6d28", "#3fb950", "#f85149", "#d29922"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

WIDTH, LEFT, BAR_X, BAR_W, RIGHT_X = 760, 24, 130, 440, 584
MAX_SYMBOLS, MAX_NAME = 6, 40


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 10


def parse_chart(raw):
    """Validate the JSON string a size result carries; return (label, rows, symbols) or None."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else None
        label = str(data["label"])[:40]
        rows = [(r[0], r[1], r[2]) for r in data["rows"]]
        symbols = [(str(s[0])[:200], s[1]) for s in data.get("symbols", [])]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not rows or any(name not in CAPACITY or not is_count(ref) or not is_count(cand) for name, ref, cand in rows):
        return None
    symbols = [(name, delta) for name, delta in symbols if isinstance(delta, int) and not isinstance(delta, bool) and delta > 0]
    return label, rows, symbols[:MAX_SYMBOLS]


def text(x, y, content, size=12, fill=TEXT, anchor="start", weight="normal", family=FONT):
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}">{escape(str(content))}</text>')


def rect(x, y, w, h, fill, rx=3):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"/>'


def signed(value):
    return f"{value:+,d} B" if value else "0 B"


def segments(ref, cand, capacity):
    """Pixel widths of kept, added, freed and free parts of a bar; any non-zero part is at least 2 px wide."""
    kept, added, freed = min(ref, cand), max(cand - ref, 0), max(ref - cand, 0)
    scale = BAR_W / capacity
    widths = {"kept": kept * scale, "added": added * scale, "freed": freed * scale}
    for key, value in list(widths.items()):
        if value > 0:
            widths[key] = max(value, 2.0)
    used = widths["kept"] + widths["added"] + widths["freed"]
    if used > BAR_W:  # does not fit the chip (-O0 can be larger than the flash): shrink to the bar
        factor = BAR_W / used
        widths = {key: value * factor for key, value in widths.items()}
        used = BAR_W
    widths["free"] = BAR_W - used
    return widths


def usage_section(rows, y):
    parts = [text(LEFT, y, "Memory used", 13, TEXT, weight="600")]
    y += 14
    for name, ref, cand in rows:
        capacity = CAPACITY[name]
        widths = segments(ref, cand, capacity)
        parts.append(text(LEFT, y + 14, name, 12, MUTED))
        x = BAR_X
        parts.append(rect(BAR_X, y, BAR_W, 18, TRACK))
        for key, colour in (("kept", BLUE), ("added", ORANGE), ("freed", GREEN)):
            if widths[key] > 0:
                parts.append(rect(x, y, widths[key], 18, colour, rx=2))
                x += widths[key]
        fits = cand <= capacity
        label = f"{cand:,} B · {100.0 * cand / capacity:.1f} %" if fits else f"{cand:,} B · over the chip"
        parts.append(text(RIGHT_X, y + 14, label, 12, TEXT if fits else RED))
        y += 28
    legend_x = BAR_X
    for colour, label in ((BLUE, "used before and after"), (ORANGE, "added by this PR"), (GREEN, "freed by this PR"),
                          (TRACK, "free")):
        parts.append(rect(legend_x, y + 2, 11, 11, colour, rx=2))
        parts.append(text(legend_x + 16, y + 12, label, 11, MUTED))
        legend_x += 24 + 7 * len(label)
    return parts, y + 30


def change_section(rows, y, warn_flash, warn_ram):
    """Signed change per memory against the warning limit; RAM and AHB RAM are summed because the limit is."""
    by_name = {name: (ref, cand) for name, ref, cand in rows}
    flash_ref, flash_cand = by_name.get("Flash", (0, 0))
    ram_delta = sum(cand - ref for name, (ref, cand) in by_name.items() if name != "Flash")
    entries = [("Flash", flash_cand - flash_ref, warn_flash), ("RAM", ram_delta, warn_ram)]
    center, half = BAR_X + BAR_W / 2, BAR_W / 2 - 10
    parts = [text(LEFT, y, "Change from the base revision", 13, TEXT, weight="600")]
    y += 14
    for name, delta, limit in entries:
        unit = max(limit, 1) * 2  # the bar spans twice the warning limit before it is cut off
        length = min(abs(delta) / unit * half, half)
        if delta:
            length = max(length, 2.0)
        colour = RED if delta > limit else YELLOW if delta > 0 else GREEN
        parts.append(text(LEFT, y + 14, name, 12, MUTED))
        parts.append(rect(BAR_X, y, BAR_W, 18, TRACK))
        if delta:
            parts.append(rect(center if delta > 0 else center - length, y, length, 18, colour, rx=2))
        marker = center + max(limit, 1) / unit * half
        parts.append(f'<line x1="{center:.1f}" y1="{y - 3}" x2="{center:.1f}" y2="{y + 21}" stroke="{MUTED}" stroke-width="1"/>')
        parts.append(f'<line x1="{marker:.1f}" y1="{y - 3}" x2="{marker:.1f}" y2="{y + 21}" stroke="{MUTED}" '
                     f'stroke-width="1" stroke-dasharray="3,2"/>')
        verdict = "over the warning limit" if delta > limit else "within the limit" if delta else "no change"
        parts.append(text(RIGHT_X, y + 9, signed(delta), 12, colour if delta else TEXT, weight="600"))
        parts.append(text(RIGHT_X, y + 22, f"{verdict} (+{limit} B)" if delta else verdict, 10, MUTED))
        y += 34
    parts.append(f'<line x1="{BAR_X}" y1="{y - 2}" x2="{BAR_X + 22}" y2="{y - 2}" stroke="{MUTED}" stroke-dasharray="3,2"/>')
    parts.append(text(BAR_X + 28, y + 2, "warning limit, the scale ends at twice the limit; growth never fails the gate", 11, MUTED))
    return parts, y + 22


def symbol_section(symbols, y):
    if not symbols:
        return [], y
    biggest = max(delta for _, delta in symbols)
    parts = [text(LEFT, y, "Symbols that grew most", 13, TEXT, weight="600")]
    y += 14
    bar_x, bar_w = 330, 300
    for name, delta in symbols:
        shown = name if len(name) <= MAX_NAME else name[:MAX_NAME - 1] + "…"
        parts.append(text(bar_x - 8, y + 13, shown, 11, TEXT, anchor="end", family=MONO))
        parts.append(rect(bar_x, y + 1, max(delta / biggest * bar_w, 2.0), 15, ORANGE, rx=2))
        parts.append(text(bar_x + max(delta / biggest * bar_w, 2.0) + 8, y + 13, f"+{delta:,} B", 11, MUTED))
        y += 24
    return parts, y + 6


def build_svg(label, rows, symbols, base_rev, warn_flash, warn_ram):
    """One chart image: memory used, change against the warning limit, and the symbols that grew most."""
    body, y = [text(LEFT, 30, label, 15, TEXT, weight="600"),
               text(WIDTH - LEFT, 30, f"compared with {base_rev}", 12, MUTED, anchor="end")], 58
    for section in (lambda yy: usage_section(rows, yy), lambda yy: change_section(rows, yy, warn_flash, warn_ram),
                    lambda yy: symbol_section(symbols, yy)):
        parts, y = section(y)
        body += parts
    height = y + 4
    title = f"{label}: memory used, change from the base revision and symbols that grew most"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
            f'role="img" aria-label="{escape(title, {chr(34): "&quot;"})}">'
            f'<title>{escape(title)}</title>'
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="8" fill="{BG}" stroke="{BORDER}"/>'
            + "".join(body) + "</svg>\n")


def alt_text(label, rows):
    pieces = [f"{name} {cand - ref:+d} B" for name, ref, cand in rows]
    return f"{label}: change from base " + ", ".join(pieces)


def sim_charts(args):
    """One chart per simulator case whose steps differ; the samples travel as JSON in the sim result's metrics."""
    import trace_chart
    made = {}
    for path in sorted(glob.glob(os.path.join(args.results, "**", "sim.result.json"), recursive=True)):
        try:
            with open(path) as handle:
                metrics = json.load(handle).get("metrics", {})
            samples, names = json.loads(metrics.get("trace_samples", "{}")), json.loads(metrics.get("trace_names", "{}"))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        for key, sample in samples.items():
            if not re.fullmatch(r"sim-[a-z0-9-]{1,80}", key):
                continue
            try:
                svg = trace_chart.draw_case(str(names.get(key, key))[:80], sample)
            except (TypeError, ValueError, KeyError):
                svg = None
            if not svg:
                continue
            os.makedirs(args.out, exist_ok=True)
            with open(os.path.join(args.out, key + ".svg"), "w") as handle:
                handle.write(svg)
            made[key] = {"file": key + ".svg", "alt": f"Step blocks of case {names.get(key, key)}: reference against this pull request"}
    return made


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    charts = {}
    for path in sorted(glob.glob(os.path.join(args.results, "**", "size.result.json"), recursive=True)):
        try:
            with open(path) as handle:
                metrics = json.load(handle).get("metrics", {})
        except (OSError, ValueError, AttributeError):
            print(json.dumps({"type": "warning", "message": f"{path}: unreadable"}))
            continue
        warn_flash, warn_ram = metrics.get("warn_flash"), metrics.get("warn_ram")
        base_rev = str(metrics.get("base_revision", "base"))[:40]
        if not (is_count(warn_flash) and is_count(warn_ram)):
            continue
        for build_type in ("release", "debug"):
            parsed = parse_chart(metrics.get(f"chart_{build_type}"))
            if not parsed:
                print(json.dumps({"type": "warning", "message": f"no valid {build_type} chart data in {path}"}))
                continue
            label, rows, symbols = parsed
            os.makedirs(args.out, exist_ok=True)
            name = f"size-{build_type}.svg"
            with open(os.path.join(args.out, name), "w") as handle:
                handle.write(build_svg(label, rows, symbols, base_rev, warn_flash, warn_ram))
            charts[f"size-{build_type}"] = {"file": name, "alt": alt_text(label, rows)}
    charts.update(sim_charts(args))
    if charts:
        with open(os.path.join(args.out, "charts.json"), "w") as handle:
            json.dump(charts, handle, indent=1)
    print(json.dumps({"type": "summary", "charts": sorted(charts)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
