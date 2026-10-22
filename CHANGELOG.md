# Changelog

## [Unreleased]

### Added
- Simulator section: every case whose steps differ is an expandable item with one chart on a shared, labelled time axis: planner blocks, speed per motor (mm/s), position drift (steps the PR is ahead or behind), a zoomed logic-analyzer view of STEP and DIR lanes (reference above this pull request) with a circle on the first difference (a direction difference is described in plain words: which level each side goes to, or how much later one reverses; the circle is on the DIR lanes when the direction signal is what differs), a one-line red verdict above the sentence when a motor moves the wrong way ("Motor 0 moves the wrong way from 221.2 ms"), a plain-language first-difference sentence, the zoom placed first as its evidence, the drift panels replaced by one line when there is no drift, plain lane names (steps / direction) instead of STEP / DIR, move durations in ms, red only on the cells that differ, a numbers table (step counts, direction-change counts, first moved pulse; its sub-table lists the worst differing moves), `up = forward` / `down = reverse` tags (larger), a direction-count cell that says "(wrong way)" when a change points the wrong way, on the direction lanes plus a coloured "now forward" / "now reverse" label at the end of a lane whose direction changes and a "same speed in reference and PR" note on speed panels whose curves coincide, a large before/after pair ("Reference: motor 0 turns reverse" / "This PR: motor 0 turns forward") under the verdict line, a larger, brighter legend and time-axis labels, a legend; table headers say base / PR instead of ref, the drift note sits below the time axis, and an equal direction-change count with different timing reads "(timing differs)"; a motor whose steps and direction are identical is one line in the zoom instead of four lanes (`scripts/trace_chart.py`, `scripts/pulse_data.py`). The reference pulses come from a simulator built from the base firmware (`--base-sim`), used only when it reproduces `expected.trace`; otherwise the chart falls back to planner blocks and says why.
- Size section shows real chart images (dark SVG, drawn by `scripts/charts.py`, published on the `gate-images` branch by the comment workflow): memory used with legend, change against the warning limit, symbols that grew most.

### Changed
- The contributor message never states or implies that a pull request is ready, good, acceptable or worth merging: hard rule in `prompts/guidance.md`, plus a sentence filter in `scripts/guidance.py` that deletes such text from the model's answer. The `verdict` field is gone.
- The comment workflow needs `contents: write` to push chart images.

### Changed
- Report: full-width tables, stage icons, and under each flash/RAM table a memory map of coloured squares (🟦 kept, 🟧 added, 🟩 freed, ⬛ free) with a legend.
- Size warnings are stricter: above +64 B flash or +4 B RAM (were 512 B and 64 B).
- AI review findings carry severity, rule and source icons.
- A simulator crash is a failure (signal named, one retry), no longer a generic error.

### Added
- Simulator stage reliability: a mismatch is confirmed by a second run (a run that matches on retry is reported as flaky with status warn, two different wrong runs as a check error, never as a failure of the pull request); each case runs in its own working directory without stdin; a missing simulator binary gives a clear error.
- Contributor message: when the pull request adds a blocking call, a removal diff built from its own lines is always included, even if the model gives no code.
- `scripts/guidance.py`, `prompts/guidance.md`: a polite closing message that tells the contributor what to change, with short suggested code or diffs and a note that they may be naive. Advice only, never changes the status.
- `scripts/mdkit.py`: shared Markdown helpers (dynamic code fences, fence-aware clipping, mention defusing, square bars).

### Fixed
- Code blocks in stage details rendered as literal backticks; fences are now kept and closed when a log is clipped.

### Added
- `smoothie-gate run` (`bin/smoothie-gate`, `scripts/gate.py`): all stages on a local repository; documentation pages in `documentation/`.
- Result record schema, validator (`scripts/gate_result.py`), tests, GPL-3.0 `LICENSE` and project skeleton.
- Build stage (`scripts/build.py`, remote ARM build via ni-build) and size stage (`scripts/size.py`, Release and Debug flash/RAM comparison).
- Simulator stage (`scripts/sim_stage.py`) and 26 step-trace reference cases in `cases/`.
- Reporter (`scripts/report.py`) and workflows `gate-pr.yml`, `gate-comment.yml` (run on a private test repository: comment, in-place update, failed build, model outage), build `--runner make` for CI.
- Fixed: empty `AI_MODELS`/`AI_BASE_URL` variables fall back to the defaults; build failures quote the compiler errors first; a skipped check no longer turns the whole report to `skipped`.
- AI review stage (`scripts/ai_review.py`, `prompts/`, `replay/`, `scripts/replay_review.py`) and `documentation/ai-review.md` with replay results and provider limits.
- README.md and CLAUDE.md describing the planned gate: build, size, AI review and simulator checks for Smoothieware pull requests.
