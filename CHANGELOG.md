# Changelog

## [Unreleased]

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
