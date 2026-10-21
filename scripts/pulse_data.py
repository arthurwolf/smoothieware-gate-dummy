"""Per-step data for the simulator charts: step pulses, direction changes, binned speed and position drift.

Input is two smoothie-sim result JSON files for the same case (reference side = the base firmware, produced with
--full-steps; pull request side likewise). Output is a small plain dict (see build()) that travels from the untrusted
build job to the trusted comment job; trace_chart.valid_sample() re-validates every number and size cap there.
Times are absolute simulator ticks (10 us each), pulse times are start_tick + the pulse's offset inside its block.
"""
from bisect import bisect_left, bisect_right

BINS = 120            # speed and drift resolution over the whole case
ZOOM_PULSES = 90      # pulses of the busiest axis the zoom window is sized for (>= 6 px per pulse at 670 px)
MAX_AXES = 3          # axes drawn; the ones that differ come first
MAX_PULSES = 400      # per axis and side inside the zoom window
SHIFT_TICKS = 4       # a pulse moved by more than this many ticks counts as a difference


def _axis_trains(result):
    """{axis: (pulse ticks, [(tick, sign)] direction changes)} from a simulator result."""
    pulses, dirs, level = {}, {}, {}
    for block in result.get("blocks", []):
        start = int(block["start_tick"])
        initial = block.get("step_dirs") or []
        for axis, ticks in enumerate(block.get("step_ticks", [])):
            # absolute DIR level at the block start, so axes that never reverse still draw their real level
            if ticks and axis < len(initial) and int(initial[axis]) != level.get(axis, 0):
                level[axis] = int(initial[axis])
                dirs.setdefault(axis, []).append((start + int(ticks[0]), level[axis]))
            stride = block.get("step_tick_stride", [1] * 99)[axis] if axis < len(block.get("step_tick_stride", [])) else 1
            if stride != 1:
                return None  # thinned list: not a real pulse train
            pulses.setdefault(axis, []).extend(start + int(t) for t in ticks)
            for index, sign in (block.get("dir_changes") or [[]] * (axis + 1))[axis]:
                if index < len(ticks):
                    level[axis] = int(sign)
                    dirs.setdefault(axis, []).append((start + int(ticks[index]), int(sign)))
    return pulses, dirs


def _first_difference(ref, pr):
    """(index, ref tick, pr tick) of the first pulse that moved or exists on one side only, else None."""
    for i in range(min(len(ref), len(pr))):
        if abs(ref[i] - pr[i]) > SHIFT_TICKS:
            return i, ref[i], pr[i]
    if len(ref) != len(pr):
        i = min(len(ref), len(pr))
        return i, ref[i] if i < len(ref) else None, pr[i] if i < len(pr) else None
    return None


def _first_dir_difference(ref, pr):
    for i in range(min(len(ref), len(pr))):
        if ref[i][1] != pr[i][1] or abs(ref[i][0] - pr[i][0]) > SHIFT_TICKS:
            return min(ref[i][0], pr[i][0])
    if len(ref) != len(pr):
        longer = ref if len(ref) > len(pr) else pr
        return longer[min(len(ref), len(pr))][0]
    return None


def _binned(ticks, edges, per_mm, tick_us):
    """Speed in mm/s per bin."""
    out = []
    for lo, hi in zip(edges, edges[1:]):
        n = bisect_right(ticks, hi) - bisect_right(ticks, lo)
        out.append(round(n / per_mm / ((hi - lo) * tick_us * 1e-6), 1) if hi > lo else 0)
    return out


def _in_window(ticks, lo, hi):
    a = bisect_right(ticks, lo - 1)
    return ticks[a:bisect_right(ticks, hi)][:MAX_PULSES]


def build(ref_result, pr_result):
    """Pulse sample for one case, or (None, reason) when the data cannot honestly be drawn."""
    ref_trains, pr_trains = _axis_trains(ref_result), _axis_trains(pr_result)
    if ref_trains is None or pr_trains is None:
        return None, "the simulator thinned the step lists (too many steps in a block)"
    (rp, rd), (pp, pd) = ref_trains, pr_trains
    spm = [float(v) for v in pr_result.get("steps_per_mm", [])]
    tick_us = float(pr_result.get("step_period_us", 10)) or 10.0
    axes = sorted(set(rp) | set(pp))
    axes = [a for a in axes if rp.get(a) or pp.get(a)]
    if not axes:
        return None, "no step pulses on any axis"
    diffs = {}
    for a in axes:
        first = _first_difference(rp.get(a, []), pp.get(a, []))
        dir_tick = _first_dir_difference(rd.get(a, []), pd.get(a, []))
        candidates = []
        if first:
            candidates.append(("pulse", min(t for t in first[1:] if t is not None), first))
        if dir_tick is not None:
            candidates.append(("dir", dir_tick, None))
        if candidates:
            diffs[a] = min(candidates, key=lambda c: c[1])
    end = max([max(v) for v in list(rp.values()) + list(pp.values()) if v] + [1])
    edges = [round(end * i / BINS) for i in range(BINS + 1)]
    shown = sorted(axes, key=lambda a: (a not in diffs, a))[:MAX_AXES]
    shown.sort()
    anchor = None
    if diffs:
        axis, (kind, tick, first) = min(diffs.items(), key=lambda kv: kv[1][1])
        anchor = {"axis": axis, "kind": kind, "tick": tick,
                  "ref": first[1] if first else None, "pr": first[2] if first else None,
                  "index": first[0] if first else None,
                  "ref_count": len(rp.get(axis, [])), "pr_count": len(pp.get(axis, []))}
        if axis not in shown:
            shown = sorted(shown[:MAX_AXES - 1] + [axis])
    # zoom window: sized by the busiest shown axis, around the anchor
    centre = anchor["tick"] if anchor else end // 2
    best_span = None
    for a in shown:
        train = rp.get(a) or pp.get(a)
        i = bisect_right(train, centre)
        lo, hi = max(i - ZOOM_PULSES // 4, 0), min(i + ZOOM_PULSES * 3 // 4, len(train) - 1)
        if hi > lo:
            span = train[hi] - train[lo]
            best_span = span if best_span is None else min(best_span, span)
    span = max(best_span or 200, 40)
    t0 = max(centre - span // 4, 0)
    t1 = t0 + span
    per_axis = []
    for a in shown:
        per_mm = spm[a] if a < len(spm) and spm[a] > 0 else 1.0
        entry = {"axis": a, "per_mm": per_mm, "ref_total": len(rp.get(a, [])), "pr_total": len(pp.get(a, [])),
                 "ref_speed": _binned(rp.get(a, []), edges, per_mm, tick_us),
                 "pr_speed": _binned(pp.get(a, []), edges, per_mm, tick_us),
                 "drift": [bisect_right(pp.get(a, []), e) - bisect_right(rp.get(a, []), e) for e in edges[1:]],
                 "ref_pulses": _in_window(rp.get(a, []), t0, t1), "pr_pulses": _in_window(pp.get(a, []), t0, t1),
                 "ref_i0": bisect_left(rp.get(a, []), t0), "pr_i0": bisect_left(pp.get(a, []), t0)}
        for side, dirs in (("ref", rd), ("pr", pd)):
            seq = dirs.get(a, [])
            before = [s for t, s in seq if t < t0]
            entry[side + "_dir0"] = before[-1] if before else 0
            entry[side + "_dir"] = [[t, s] for t, s in seq if t0 <= t <= t1][:60]
        per_axis.append(entry)
    return {"tick_us": tick_us, "end": end, "edges": edges, "t0": t0, "t1": t1, "anchor": anchor,
            "axes": per_axis, "differing_axes": sorted(diffs)}, None
