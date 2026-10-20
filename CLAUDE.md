# smoothie-gate

Automatic checks for Smoothieware pull requests, run as GitHub Actions. Output is one PR comment plus a run artifact. See `README.md` for the user-facing overview; this file holds agent rules.

## Status

Design only. No workflow, script or reference data exists. Do not describe anything as working until it has run; the README marks unbuilt parts **Planned**, remove that tag only after the part is verified.

## Purpose and principles

- The gate informs maintainers. Size growth, review findings and warnings are advice. Only a failed build, a clear developer-rule violation or an unexplained simulator step mismatch fails the run.
- Four checks: build, size (flash and RAM, optimized and unoptimized builds, base vs PR, signed byte difference), AI review (Smoothie developer rules plus general bug, leak and safety review), simulator replay (smoothie-sim, step traces compared with stored references).
- Reuse smoothie-sim (`../smoothie-sim`: `run_checks.sh`, `checkers/`, `rules.json`, the existing `workflow_dispatch` workflow described in its `documentation/github-actions.md`). Extend it or call it; do not duplicate its checkers. If a gate need belongs in smoothie-sim, change smoothie-sim.
- Size reporting follows the rule in `../CLAUDE.md`: same board, toolchain and build settings for base and PR, both optimized (default) and unoptimized builds, flash and RAM totals with signed byte differences, base revision named.

## Layout (planned, create when needed)

- `.github/workflows/` workflow files. Inputs reach the shell through environment variables, never interpolated into scripts.
- `cases/<name>/` simulator case: G-code, machine config, expected step trace. Cases must be varied: machine kinds (Cartesian, delta, CoreXY, SCARA, rotary) and uses (print, mill, laser, probing, homing, arcs, tool change).
- `prompts/` AI review prompts: one for developer rules, one for general code issues. Prompts are files, versioned, with persona, tagged sections and explicit uncertainty output.
- `scripts/` Python or shell helpers; JSONL stdout, defined flags.
- `documentation/` public markdown documentation, linked from `README.md` and committed (the README stays the short overview).
- `docs/` internal research and working guidance, git-ignored (`.gitignore`), never committed; nothing a fresh clone needs goes there.

## Rules specific to this project

- Security: PRs come from forks. Never run PR code with secrets available. Use `pull_request_target` only to comment, check out untrusted code in a job without secrets. Treat PR title, branch name, diff text and file content as untrusted input, including in AI prompts (prompt injection): the model output is advice and must not drive shell commands.
- AI inference: GitHub Models was retired on 2026-07-30; use an OpenAI-compatible endpoint, default the Kilo Gateway free tier (`documentation/ai-review.md`). Handle rate limits and failure by posting "AI review unavailable" and continuing, never failing the run on it. Set the maximum output tokens to the model limit and control length through the prompt.
- Report: one comment per PR, edited in place (marker comment), size table, collapsed details, link to the full artifact. Never post secrets or full logs.
- Step references: an intended behaviour change updates the reference trace in the same PR with a stated reason. Never regenerate references automatically to make a run pass.
- Determinism: pin the toolchain version, runner image and simulator revision; name them in the report.
- Dev rules source: `../smoothie-sim/rules.json` (forbidden blocking calls and `ask-maintainer` items). Do not invent rules; cite the maintainer statement or file that defines each.

## Verification

- Test the workflow on a fork or a test repository with a real PR before claiming it works; actionlint does not prove behaviour.
- A check is verified when it has caught a known-bad PR and passed a known-good one. Old rejected Smoothieware PRs are the bad set (see smoothie-sim `documentation/pr-replay.md`).
- Report what ran and what did not; local runs do not prove GitHub runner behaviour.

## Conventions

- README: follow `~/.claude/docs/readme-file-guide.md`; style model is `~/dev/ai/ilium/README.md`. No unshipped feature stated as present. Verify every claim and link.
- Every user-visible change adds an `[Unreleased]` entry to `CHANGELOG.md`.
- Compilation of firmware locally goes through ni-build per the global instructions; CI builds run on GitHub runners.
- Temporary files go in scratch folders per global rules, not in this tree.
- This folder is not a git repository yet. Do not run `git init` or commit without being asked.
