"""Sampling and drawing of step-trace differences (the simulator stage's expandable per-case charts).

sample_case() condenses one failing case into plain numbers: the planner blocks of both sides, a few block rows that
differ, and (when both simulators ran with full step lists) the pulse data from pulse_data.build(). draw_case() turns
that into one dark SVG with a single shared time axis per section:

  whole case   planner blocks, speed per axis (mm/s), position drift (steps the pull request is ahead or behind)
  zoom         logic-analyzer lanes (STEP and DIR, reference above this pull request) around the first difference
  numbers      steps, first moved pulse and the differing blocks in figures

The sample is the only thing that travels from the untrusted build job to the trusted comment job, so valid_sample()
checks every type and size before anything is drawn. Standard library only.
"""
from xml.sax.saxutils import escape

from charts import BG, BORDER, BLUE, FONT, MONO, MUTED, ORANGE, RED, TEXT, TRACK, rect, text

MAX_BLOCKS, MAX_ROWS, MAX_AXES, MAX_PULSES, MAX_DIRS, BINS = 300, 4, 3, 400, 60, 120
WIDTH, LEFT, RIGHT = 760, 112, 16
PLOT_W = WIDTH - LEFT - RIGHT
GRID, FAINT = "#30363d", "#484f58"


def _ints(value):
    try:
        return [int(v) for v in str(value).split(",")]
    except ValueError:
        return []


def _side(raw):
    if raw is None:
        return None
    try:
        return [int(float(raw["start"])), int(float(raw["ticks"])), round(float(raw["entry"]), 2), round(float(raw["exit"]), 2),
                int(raw.get("line", 0)), _ints(raw.get("counts", ""))[:MAX_AXES + 3], int(raw["index"])]
    except (KeyError, ValueError):
        return None


def _score(e, a):
    if not (e and a):
        return 10 ** 9
    return sum(abs(x - y) for x, y in zip(e[5], a[5])) * 10 + abs(e[1] - a[1]) / max(e[1], a[1], 1) * 1000 \
        + abs(e[0] - a[0]) / max(e[0], a[0], 1) * 1000 + abs(e[3] - a[3])


def sample_case(expected, actual, differing, pulses=None, reason=None):
    """expected/actual are step_trace.parse() results; differing is the set of block positions the comparer flagged."""
    exp, act = expected["blocks"], actual["blocks"]
    count = max(len(exp), len(act))
    bad = sorted(i for i in range(count) if i in differing or i >= min(len(exp), len(act)))
    if not bad:
        return None
    pairs = [(_side(exp[i]) if i < len(exp) else None, _side(act[i]) if i < len(act) else None) for i in range(count)]
    blocks = [{"r": pairs[i][0], "p": pairs[i][1], "d": 1 if i in bad else 0} for i in range(min(count, MAX_BLOCKS))]
    worst = max(bad, key=lambda i: _score(*pairs[i]))
    picks = []
    for i in (bad[0], worst, bad[-1], bad[len(bad) // 2]):
        if i not in picks:
            picks.append(i)
    rows = [{"r": pairs[i][0], "p": pairs[i][1]} for i in sorted(picks)[:MAX_ROWS]]
    sample = {"total": count, "differing": len(bad), "blocks": blocks, "rows": rows, "shown_blocks": len(blocks),
              "reason": reason, "pulses": pulses}
    return sample


# ---------------------------------------------------------------- validation

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15


def _nums(v, cap):
    return isinstance(v, list) and len(v) <= cap and all(_num(x) for x in v)


def _valid_side(item):
    return item is None or (isinstance(item, list) and len(item) == 7 and all(_num(x) for x in item[:5]) and _nums(item[5], MAX_AXES + 3)
                            and _num(item[6]))


def _valid_pulses(p):
    if p is None:
        return True
    if not isinstance(p, dict) or not all(_num(p.get(k)) for k in ("tick_us", "end", "t0", "t1")) or p["t1"] <= p["t0"] or p["end"] <= 0:
        return False
    if not _nums(p.get("edges"), BINS + 1) or len(p["edges"]) != BINS + 1 or not isinstance(p.get("axes"), list) or len(p["axes"]) > MAX_AXES:
        return False
    a = p.get("anchor")
    if a is not None and not (isinstance(a, dict) and all(a.get(k) is None or _num(a.get(k)) for k in
                              ("axis", "tick", "ref", "pr", "index", "ref_count", "pr_count")) and a.get("kind") in ("pulse", "dir")):
        return False
    for ax in p["axes"]:
        if not isinstance(ax, dict) or not all(_num(ax.get(k)) for k in ("axis", "per_mm", "ref_total", "pr_total", "ref_dir0", "pr_dir0", "ref_i0", "pr_i0")):
            return False
        if not all(_nums(ax.get(k), BINS) and len(ax[k]) == BINS for k in ("ref_speed", "pr_speed", "drift")):
            return False
        if not all(_nums(ax.get(k), MAX_PULSES) for k in ("ref_pulses", "pr_pulses")):
            return False
        for k in ("ref_dir", "pr_dir"):
            if not (isinstance(ax.get(k), list) and len(ax[k]) <= MAX_DIRS and all(isinstance(x, list) and len(x) == 2 and all(_num(y) for y in x) for x in ax[k])):
                return False
    return _nums(p.get("differing_axes"), MAX_AXES + 3)


def valid_sample(sample):
    try:
        if not (isinstance(sample["total"], int) and isinstance(sample["differing"], int)):
            return False
        blocks, rows = sample["blocks"], sample["rows"]
        if not (isinstance(blocks, list) and len(blocks) <= MAX_BLOCKS and isinstance(rows, list) and len(rows) <= MAX_ROWS):
            return False
        if not all(_valid_side(b["r"]) and _valid_side(b["p"]) for b in blocks + rows):
            return False
        if sample.get("reason") is not None and not (isinstance(sample["reason"], str) and len(sample["reason"]) <= 200):
            return False
        return _valid_pulses(sample.get("pulses"))
    except (KeyError, TypeError):
        return False


# ---------------------------------------------------------------- drawing

class _Axis:
    """Maps simulator ticks to x, and draws the vertical grid and tick labels."""

    def __init__(self, t0, t1, tick_us, origin=0):
        self.t0, self.t1, self.tick_us, self.origin = t0, t1, tick_us, origin

    def x(self, tick):
        return LEFT + (tick - self.t0) / max(self.t1 - self.t0, 1) * PLOT_W

    def label(self, tick):
        us = (tick - self.origin) * self.tick_us
        span = (self.t1 - self.t0) * self.tick_us
        if span < 5000:
            return f"{us:,.0f} µs"
        decimals = 0 if span >= 50000 else 1
        return f"{us / 1000:,.{decimals}f} ms"

    def grid(self, top, bottom, parts, labels_at=None, n=6):
        for i in range(n + 1):
            tick = self.t0 + (self.t1 - self.t0) * i / n
            x = self.x(tick)
            parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="{GRID}" stroke-width="0.6" stroke-dasharray="2,3"/>')
            if labels_at is not None:
                anchor = "start" if i == 0 else "end" if i == n else "middle"
                parts.append(text(f"{x:.1f}", labels_at, self.label(tick), 9.5, MUTED, anchor=anchor, family=MONO))


def _line(points, colour, width, dash=""):
    if len(points) < 2:
        return ""
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<polyline points="{d}" fill="none" stroke="{colour}" stroke-width="{width}" stroke-linejoin="round"{extra}/>'


def _fmt(v):
    return f"{v:,.1f}" if abs(v) < 100 else f"{v:,.0f}"


def _panel(parts, y, h, label, sub):
    parts.append(f'<rect x="{LEFT}" y="{y}" width="{PLOT_W}" height="{h}" fill="{TRACK}" opacity="0.55"/>')
    parts.append(text(LEFT - 8, y + 13, label, 11, TEXT, anchor="end", weight="600"))
    parts.append(text(LEFT - 8, y + 25, sub, 9.5, MUTED, anchor="end"))


def _section_title(parts, y, title, note):
    parts.append(text(10, y, title, 13, TEXT, weight="600"))
    parts.append(text(WIDTH - RIGHT, y, note, 10.5, MUTED, anchor="end"))


def _headline(p, blocks_differ):
    a = p["anchor"] if p else None
    if not a:
        return "Pulse timing is the same within 4 ticks; the differences are in block planning figures (see the table)."
    axis, us = int(a["axis"]), p["tick_us"]
    if a["kind"] == "dir":
        return f"First difference: the direction signal of motor {axis} changes differently, around {a['tick'] * us / 1000:,.2f} ms."
    if a["ref"] is None:
        return f"First difference: motor {axis} step {int(a['index']) + 1:,} fires only in this PR, at {a['pr'] * us / 1000:,.2f} ms (the reference stopped after {int(a['ref_count']):,} steps)."
    if a["pr"] is None:
        return f"First difference: motor {axis} step {int(a['index']) + 1:,} exists only in the reference, at {a['ref'] * us / 1000:,.2f} ms (this PR stopped after {int(a['pr_count']):,} steps)."
    if a["ref_count"] != a["pr_count"]:
        diff = int(a["pr_count"]) - int(a["ref_count"])
        return (f"First difference: motor {axis} stops matching at step {int(a['index']) + 1:,}, around {min(a['ref'], a['pr']) * us / 1000:,.2f} ms. "
                f"In total this PR emits {abs(diff):,} {'more' if diff > 0 else 'fewer'} steps on that motor ({int(a['pr_count']):,} against {int(a['ref_count']):,}).")
    delta = (a["pr"] - a["ref"]) * us
    word = "later" if delta > 0 else "earlier"
    return (f"First difference: motor {axis} step {int(a['index']) + 1:,} fires at {a['ref'] * us / 1000:,.2f} ms in the reference "
            f"and {a['pr'] * us / 1000:,.2f} ms in this PR, {abs(delta):,.0f} µs {word}.")


def _wrap(s, width):
    words, lines, line = s.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return lines + [line] if line else lines


def _block_strip(parts, y, blocks, ax):
    _panel(parts, y, 40, "Planner blocks", "one cell = one block")
    for lane, key, colour in ((0, "r", BLUE), (1, "p", ORANGE)):
        top = y + 4 + lane * 17
        parts.append(text(LEFT + 3, top + 10.5, "reference" if lane == 0 else "this PR", 8.5, MUTED))
        for b in blocks:
            item = b[key]
            if not item:
                continue
            x0, x1 = ax.x(item[0]), ax.x(item[0] + item[1])
            fill = colour if b["d"] else FAINT
            parts.append(f'<rect x="{x0:.1f}" y="{top + 1}" width="{max(x1 - x0 - 0.6, 0.8):.1f}" height="11" fill="{fill}"/>')
    return y + 40


def _speed_rows(parts, y, p, ax):
    for a in p["axes"]:
        peak = max(max(a["ref_speed"]), max(a["pr_speed"]), 1e-9)
        h = 54
        _panel(parts, y, h, f"Motor {int(a['axis'])}", "speed, mm/s")
        edges = p["edges"]
        def pts(vals):
            return [((ax.x(edges[i]) + ax.x(edges[i + 1])) / 2, y + h - 4 - v / peak * (h - 12)) for i, v in enumerate(vals)]
        parts.append(_line(pts(a["ref_speed"]), BLUE, 2.6))
        parts.append(_line(pts(a["pr_speed"]), ORANGE, 1.4))
        parts.append(text(LEFT - 8, y + 37, f"max {_fmt(peak)}", 9.5, MUTED, anchor="end", family=MONO))
        y += h + 4
    return y


def _drift_rows(parts, y, p, ax):
    for a in p["axes"]:
        peak = max(max(abs(v) for v in a["drift"]), 1)
        h = 50
        _panel(parts, y, h, f"Motor {int(a['axis'])}", "PR minus ref")
        top = y + 14  # the top 14 px hold the label, so the trace never crosses it
        mid = top + (h - 14) / 2
        parts.append(f'<line x1="{LEFT}" y1="{mid}" x2="{LEFT + PLOT_W}" y2="{mid}" stroke="{FAINT}" stroke-width="0.8"/>')
        edges = p["edges"]
        pts = [(ax.x(edges[i + 1]), mid - v / peak * ((h - 14) / 2 - 3)) for i, v in enumerate(a["drift"])]
        colour = RED if any(a["drift"]) else MUTED
        parts.append(_line(pts, colour, 1.8))
        last = a["drift"][-1]
        parts.append(text(LEFT + 3, y + 10, f"±{peak:,} steps · ends {last:+,d}" if last else f"±{peak:,} steps · ends at 0", 9, colour if last else MUTED, family=MONO))
        y += h + 4
    return y


def _logic_rows(parts, y, p, ax):
    anchor = p["anchor"]
    us = p["tick_us"]
    for a in p["axes"]:
        lane_h = 15
        group_h = lane_h * 4 + 6
        parts.append(text(LEFT - 8, y + 10, f"Motor {int(a['axis'])}", 11, TEXT, anchor="end", weight="600"))
        y += 14
        for n, (side, colour, name) in enumerate((("ref", BLUE, "STEP reference"), ("pr", ORANGE, "STEP this PR"))):
            ly = y + n * lane_h
            parts.append(f'<rect x="{LEFT}" y="{ly}" width="{PLOT_W}" height="{lane_h - 2}" fill="{TRACK}" opacity="0.55"/>')
            parts.append(text(LEFT - 8, ly + 10, name, 8.5, MUTED, anchor="end"))
            base, high = ly + lane_h - 4, ly + 2
            path = f"M{LEFT},{base}"
            for t in a[side + "_pulses"]:
                x = ax.x(t)
                path += f" H{x:.1f} V{high} h1.6 V{base}"
            parts.append(f'<path d="{path} H{LEFT + PLOT_W}" fill="none" stroke="{colour}" stroke-width="1.3"/>')
        for n, (side, colour, name) in enumerate((("ref", BLUE, "DIR reference"), ("pr", ORANGE, "DIR this PR"))):
            ly = y + (2 + n) * lane_h
            parts.append(f'<rect x="{LEFT}" y="{ly}" width="{PLOT_W}" height="{lane_h - 2}" fill="{TRACK}" opacity="0.55"/>')
            parts.append(text(LEFT - 8, ly + 10, name, 8.5, MUTED, anchor="end"))
            level = a[side + "_dir0"]
            lo, hi = ly + lane_h - 4, ly + 2
            yy = hi if level > 0 else lo
            path = f"M{LEFT},{yy}"
            for t, sign in a[side + "_dir"]:
                path += f" H{ax.x(t):.1f} V{hi if sign > 0 else lo}"
            parts.append(f'<path d="{path} H{LEFT + PLOT_W}" fill="none" stroke="{colour}" stroke-width="1.3"/>')
            parts.append(text(LEFT + 3, lo - 1 if level > 0 else hi + 7, "high" if level > 0 else "low", 7.5, MUTED, family=MONO))
        # connectors between matching pulses that moved, and the circle on the first difference
        if a["ref_i0"] == a["pr_i0"] and a["ref_total"] == a["pr_total"]:
            for r, q in zip(a["ref_pulses"], a["pr_pulses"]):
                if abs(r - q) > 4:
                    parts.append(f'<line x1="{ax.x(r):.1f}" y1="{y + lane_h - 3}" x2="{ax.x(q):.1f}" y2="{y + lane_h + 1}" stroke="{RED}" stroke-width="1" opacity="0.8"/>')
        if anchor and int(anchor["axis"]) == int(a["axis"]):
            ticks = [t for t in (anchor["ref"], anchor["pr"]) if t is not None] if anchor["kind"] == "pulse" else [anchor["tick"]]
            x0, x1 = ax.x(min(ticks)), ax.x(max(ticks))
            parts.append(f'<rect x="{x0 - 3:.1f}" y="{y - 2}" width="{x1 - x0 + 8:.1f}" height="{group_h - 2}" fill="{RED}" opacity="0.14"/>')
            note_y = y - 4
            cx, cy = (x0 + x1) / 2 + 1, y + lane_h
            rx = max((x1 - x0) / 2 + 9, 11)
            parts.append(f'<ellipse cx="{cx:.1f}" cy="{cy}" rx="{rx:.1f}" ry="{lane_h + 3}" fill="none" stroke="{RED}" stroke-width="1.8"/>')
            if anchor["kind"] == "pulse" and anchor["ref"] is not None and anchor["pr"] is not None:
                note = f"{(anchor['pr'] - anchor['ref']) * us:+,.0f} µs"
                right = cx + rx + 4 + 60 < WIDTH
                parts.append(text(f"{cx:.1f}", note_y, ("this pulse moved " if a["ref_total"] == a["pr_total"] else "counts differ, first gap ") + note, 10.5, RED, anchor="middle", weight="600", family=MONO))
        y += group_h + 4
    return y


def _table(parts, y, sample):
    p = sample["pulses"]
    parts.append(text(10, y, "In numbers", 13, TEXT, weight="600"))
    y += 8
    cols = (LEFT - 96, LEFT + 30, LEFT + 120, LEFT + 205, LEFT + 335)
    def row(cells, colour=TEXT, weight="normal", fills=None):
        nonlocal y
        y += 14
        for x, c in zip(cols, cells):
            parts.append(text(x + (88 if x == cols[0] else 0), y, c, 10, colour, weight=weight, family=MONO, anchor="end" if x == cols[0] else "start"))
    if p:
        row(("motor", "steps ref", "steps PR", "difference", "first moved pulse"), MUTED, "600")
        for a in p["axes"]:
            diff = int(a["pr_total"]) - int(a["ref_total"])
            moved = "-"
            an = p["anchor"]
            if an and int(an["axis"]) == int(a["axis"]) and an["kind"] == "pulse":
                moved = f"#{int(an['index']) + 1:,}" + (f" {(an['pr'] - an['ref']) * p['tick_us']:+,.0f} µs" if an["ref"] is not None and an["pr"] is not None and a["ref_total"] == a["pr_total"] else " (counts differ)")
            row((f"{int(a['axis'])}", f"{int(a['ref_total']):,}", f"{int(a['pr_total']):,}", f"{diff:+,d}" if diff else "same", moved), RED if diff else TEXT)
        y += 6
    if sample["rows"]:
        row(("worst blocks", "start ref", "start PR", "ticks ref → PR", "exit mm/s ref → PR"), MUTED, "600")
        for r in sample["rows"]:
            e, q = r["r"], r["p"]
            label = f"#{(e or q)[6]}"
            if e and q:
                row((label, f"{e[0]:,}", f"{q[0]:,}", f"{e[1]:,} → {q[1]:,}", f"{e[3]:g} → {q[3]:g}"))
            else:
                row((label, "-", "-", "block exists only in the " + ("reference" if e else "PR"), ""), RED)
    return y + 10


def draw_case(name, sample):
    """SVG text for one case, or None when the sample is not valid."""
    if not valid_sample(sample):
        return None
    p = sample["pulses"]
    parts = [text(10, 24, name, 14, TEXT, weight="600", family=MONO),
             text(WIDTH - RIGHT, 24, f"{sample['differing']:,} of {sample['total']:,} planner blocks differ", 12, MUTED, anchor="end")]
    y = 38
    if p:
        for line in _wrap(_headline(p, sample["differing"]), 108):
            y += 15
            parts.append(text(10, y, line, 11.5, TEXT))
    else:
        for line in _wrap("Pulse view unavailable: " + (sample.get("reason") or "no per-step data") + ". Showing the planner blocks only.", 108):
            y += 15
            parts.append(text(10, y, line, 11.5, MUTED))
    y += 14
    blocks = sample["blocks"]
    last = max([b[k][0] + b[k][1] for b in blocks for k in ("r", "p") if b[k]] + [1])
    ax = _Axis(0, p["end"] if p else last, p["tick_us"] if p else 10)
    top = y
    _section_title(parts, y + 8, "Whole case", "one time axis for every panel; the yellow band marks the zoom below")
    y += 18
    sec_top = y
    y = _block_strip(parts, y, blocks, ax) + 4
    if p:
        y = _speed_rows(parts, y, p, ax)
        y += 2
        parts.append(text(10, y + 9, "Position drift: steps the PR is ahead (+) or behind (-) the reference", 10, MUTED))
        y += 14
        y = _drift_rows(parts, y, p, ax)
    ax.grid(sec_top, y, parts, labels_at=y + 11)
    if p:
        if p["anchor"]:
            x = ax.x(p["anchor"]["tick"])
            parts.append(f'<line x1="{x:.1f}" y1="{sec_top}" x2="{x:.1f}" y2="{y}" stroke="{RED}" stroke-width="1.4"/>')
            parts.append(f'<path d="M{x - 5:.1f},{sec_top - 7} L{x + 5:.1f},{sec_top - 7} L{x:.1f},{sec_top - 1} Z" fill="{RED}"/>')
        x0, x1 = ax.x(p["t0"]), max(ax.x(p["t1"]), ax.x(p["t0"]) + 7)
        parts.append(f'<rect x="{x0:.1f}" y="{sec_top - 2}" width="{x1 - x0:.1f}" height="{y - sec_top + 4}" fill="#d29922" opacity="0.16" stroke="#d29922" stroke-width="1"/>')
    y += 26
    if p:
        zoom = _Axis(p["t0"], p["t1"], p["tick_us"])
        span_us = (p["t1"] - p["t0"]) * p["tick_us"]
        _section_title(parts, y, "Zoom on the first difference", f"{span_us / 1000:,.2f} ms wide; STEP pulse width not to scale")
        y += 12
        z_top = y
        y = _logic_rows(parts, y, p, zoom)
        zoom.grid(z_top, y, parts, labels_at=y + 10)
        y += 28
    y = _table(parts, y, sample)
    legend = y + 4
    items = [(BLUE, "reference (base firmware)"), (ORANGE, "this pull request"), (RED, "difference"), ("#d29922", "zoom region"), (FAINT, "block that matches")]
    x = 10
    for colour, label in items:
        parts.append(rect(x, legend, 11, 11, colour, rx=2))
        parts.append(text(x + 16, legend + 10, label, 10.5, MUTED))
        x += 24 + len(label) * 5.9
    height = legend + 28
    title = f"Step comparison of case {name}: reference against this pull request"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" width="{WIDTH}" height="{height:.0f}" role="img" '
            f'aria-label="{escape(title, {chr(34): "&quot;"})}"><title>{escape(title)}</title>'
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1:.0f}" rx="8" fill="{BG}" stroke="{BORDER}"/>'
            + "".join(parts) + "</svg>\n")
