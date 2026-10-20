# Changelog

## [Unreleased]

### Added
- Result record schema, validator (`scripts/gate_result.py`), tests, GPL-3.0 `LICENSE` and project skeleton.
- Build stage (`scripts/build.py`, remote ARM build via ni-build) and size stage (`scripts/size.py`, Release and Debug flash/RAM comparison).
- Simulator stage (`scripts/sim_stage.py`) and 26 step-trace reference cases in `cases/`.
- Reporter (`scripts/report.py`) and workflows `gate-pr.yml`, `gate-comment.yml` (untested on GitHub), build `--runner make` for CI.
- AI review stage (`scripts/ai_review.py`, `prompts/`, `replay/`, `scripts/replay_review.py`) and `documentation/ai-review.md` with replay results and provider limits.
- README.md and CLAUDE.md describing the planned gate: build, size, AI review and simulator checks for Smoothieware pull requests.
