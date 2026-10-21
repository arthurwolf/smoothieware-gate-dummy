"""Sampling and drawing of step-trace differences (the simulator stage's expandable per-case charts).

sample_case(expected_text, actual_text) picks a small, representative set of differing blocks (the first one, the worst
one, the last one, each with a little context) from two step-trace texts and returns plain numbers. draw_case(sample)
turns that into a dark SVG: a time lane per side (reference above, this pull request below, block widths proportional to
their duration) and the steps each axis emitted per block. The trace holds one line per planner block, not per step
pulse, so blocks are the finest unit that can honestly be drawn. Standard library only; the sample is the only thing
that travels from the build job to the trusted comment job, and drawing validates it again.
"""
from xml.sax.saxutils import escape

from charts import BG, BORDER, BLUE, FONT, MONO, MUTED, ORANGE, RED, TEXT, TRACK, rect, text

TICK_US = 10  # the simulator's step tick
MAX_WINDOWS, CONTEXT_BEFORE, CONTEXT_AFTER, MAX_AXES = 3, 1, 2, 6
WIDTH, LEFT, PLOT_W = 760, 70, 670


def _ints(value):
    try:
        return [int(v) for v in str(value).split(",")]
    except ValueError:
        return []


def _block(raw):
    if raw is None:
        return None
    try:
        return {"index": int(raw["index"]), "line": int(raw.get("line", 0)), "counts": _ints(raw.get("counts", ""))[:MAX_AXES],
                "start": int(float(raw["start"])), "ticks": int(float(raw["ticks"])),
                "entry": float(raw["entry"]), "exit": float(raw["exit"])}
    except (KeyError, ValueError):
        return None


def _score(e, a):
    """How far two blocks are apart; counts count most, then relative timing."""
    score = sum(abs(x - y) for x, y in zip(e["counts"], a["counts"])) * 10 if e and a else 0
    if e and a:
        score += abs(e["ticks"] - a["ticks"]) / max(e["ticks"], a["ticks"], 1) * 1000 + abs(e["start"] - a["start"]) / max(e["start"], a["start"], 1) * 1000
    return score


def sample_case(expected, actual, differing):
    """expected/actual are step_trace.parse() results; differing is the set of block positions the comparer flagged."""
    exp, act = expected["blocks"], actual["blocks"]
    count = max(len(exp), len(act))
    bad = sorted(i for i in range(count) if i in differing or i >= min(len(exp), len(act)))
    if not bad:
        return None
    pairs = {i: (_block(exp[i]) if i < len(exp) else None, _block(act[i]) if i < len(act) else None) for i in range(count)}
    worst = max(bad, key=lambda i: _score(*pairs[i]) if all(pairs[i]) else 10 ** 9)
    anchors = []
    for anchor in (bad[0], worst, bad[-1], bad[len(bad) // 2]):
        if all(abs(anchor - other) > CONTEXT_AFTER + CONTEXT_BEFORE for other in anchors):
            anchors.append(anchor)
    windows = []
    for anchor in sorted(anchors)[:MAX_WINDOWS]:
        low, high = max(anchor - CONTEXT_BEFORE, 0), min(anchor + CONTEXT_AFTER, count - 1)
        if windows and low <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], high)
        else:
            windows.append((low, high))
    shown = []
    for low, high in windows:
        shown.append({"blocks": [{"differs": i in bad, "ref": pairs[i][0], "pr": pairs[i][1], "position": i}
                                 for i in range(low, high + 1)]})
    return {"total": count, "differing": len(bad), "windows": shown}


def valid_sample(sample):
    try:
        ok = isinstance(sample["total"], int) and isinstance(sample["differing"], int) and sample["windows"]
        for window in sample["windows"]:
            for block in window["blocks"]:
                for side in ("ref", "pr"):
                    item = block[side]
                    if item is not None:
                        item["index"], item["start"], item["ticks"], item["counts"]
                        if not all(isinstance(v, int) for v in item["counts"]) or not all(isinstance(item[k], (int, float)) for k in ("start", "ticks", "entry", "exit")):
                            return False
        return bool(ok)
    except (KeyError, TypeError):
        return False


def _window(window, y, number):
    blocks = window["blocks"]
    items = [b[s] for b in blocks for s in ("ref", "pr") if b[s]]
    first = min(i["index"] for i in items) if items else 0
    last = max(i["index"] for i in items) if items else 0
    t0 = min(i["start"] for i in items) if items else 0
    t1 = max(i["start"] + i["ticks"] for i in items) if items else 1
    span = max(t1 - t0, 1)
    parts = [text(10, y, f"Blocks {first} to {last}", 13, TEXT, weight="600"),
             text(WIDTH - 10, y, f"{span * TICK_US / 1000:,.1f} ms shown", 11, MUTED, anchor="end")]
    y += 12
    for lane, key, label in ((0, "ref", "Reference"), (1, "pr", "This PR")):
        top = y + lane * 30
        parts.append(text(LEFT - 8, top + 17, label, 11, MUTED, anchor="end"))
        parts.append(rect(LEFT, top, PLOT_W, 22, TRACK, rx=2))
        for b in blocks:
            item = b[key]
            if not item:
                if b[("pr" if key == "ref" else "ref")]:
                    other = b["ref" if key == "pr" else "pr"]
                    x = LEFT + (other["start"] - t0) / span * PLOT_W
                    parts.append(f'<rect x="{x:.1f}" y="{top}" width="{max(other["ticks"] / span * PLOT_W, 3):.1f}" height="22" rx="2" fill="none" stroke="{RED}" stroke-dasharray="3,2"/>')
                continue
            x = LEFT + (item["start"] - t0) / span * PLOT_W
            w = max(item["ticks"] / span * PLOT_W, 3.0)
            colour = (ORANGE if key == "pr" else "#6e7681") if b["differs"] else BLUE
            parts.append(rect(x, top, w, 22, colour, rx=2))
            parts.append(f'<rect x="{x:.1f}" y="{top}" width="{w:.1f}" height="22" rx="2" fill="none" stroke="{BG}" stroke-width="1"/>')
            if w > 26:
                parts.append(text(x + w / 2, top + 15, f"#{item['index']}", 10, "#ffffff", anchor="middle", family=MONO))
    y += 66
    # steps per axis: for every axis one row, per block a reference bar and a pull request bar
    axes = max((len(i["counts"]) for i in items), default=0)
    slot = PLOT_W / max(len(blocks), 1)
    parts.append(text(10, y, "Steps emitted per axis (reference | this PR)", 11, MUTED))
    y += 8
    for axis in range(axes):
        peak = max((i["counts"][axis] for i in items if axis < len(i["counts"])), default=0) or 1
        parts.append(text(LEFT - 8, y + 18, f"axis {axis}", 11, MUTED, anchor="end"))
        for n, b in enumerate(blocks):
            x0 = LEFT + n * slot
            parts.append(rect(x0 + 1, y, slot - 2, 26, TRACK, rx=2))
            for k, key in enumerate(("ref", "pr")):
                item = b[key]
                if not item or axis >= len(item["counts"]):
                    continue
                height = max(item["counts"][axis] / peak * 24, 1.0 if item["counts"][axis] else 0)
                differs = b["ref"] and b["pr"] and axis < len(b["ref"]["counts"]) and axis < len(b["pr"]["counts"]) \
                    and b["ref"]["counts"][axis] != b["pr"]["counts"][axis]
                colour = (ORANGE if key == "pr" else "#6e7681") if differs else BLUE
                bar_w = (slot - 6) / 2
                parts.append(rect(x0 + 2 + k * (bar_w + 2), y + 25 - height, bar_w, height, colour, rx=1))
            if b["ref"] and b["pr"] and axis < len(b["ref"]["counts"]) and axis < len(b["pr"]["counts"]) \
                    and b["ref"]["counts"][axis] != b["pr"]["counts"][axis] and slot > 60:
                parts.append(text(x0 + slot / 2, y + 36, f"{b['ref']['counts'][axis]:,} | {b['pr']['counts'][axis]:,}", 9, ORANGE,
                                  anchor="middle", family=MONO))
        y += 40
    return parts, y + 14


def draw_case(name, sample):
    """SVG text for one case, or None when the sample is not valid."""
    if not valid_sample(sample):
        return None
    shown_blocks = sum(len(w["blocks"]) for w in sample["windows"])
    parts = [text(10, 24, name, 14, TEXT, weight="600", family=MONO),
             text(WIDTH - 10, 24, f"{sample['differing']} of {sample['total']} blocks differ; {shown_blocks} shown", 12, MUTED, anchor="end")]
    y = 52
    for number, window in enumerate(sample["windows"]):
        if number:
            parts.append(text(WIDTH / 2, y - 6, "· · ·  other blocks left out  · · ·", 11, MUTED, anchor="middle"))
            y += 12
        more, y = _window(window, y, number)
        parts += more
    legend = y
    for x, colour, label in ((LEFT, BLUE, "block, same in both"), (LEFT + 170, "#6e7681", "reference block that differs"),
                             (LEFT + 380, ORANGE, "this PR's block that differs"), (LEFT + 580, RED, "missing")):
        parts.append(rect(x, legend, 11, 11, colour, rx=2))
        parts.append(text(x + 16, legend + 10, label, 11, MUTED))
    height = legend + 28
    title = f"Step blocks of case {name}: reference against this pull request"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" role="img" '
            f'aria-label="{escape(title, {chr(34): "&quot;"})}"><title>{escape(title)}</title>'
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="8" fill="{BG}" stroke="{BORDER}"/>'
            + "".join(parts) + "</svg>\n")
