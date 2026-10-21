# Adopting smoothie-gate in Smoothieware

The gate is two workflow files that live in `.github/workflows/` of this repository: `gate-pr.yml` (builds and tests the pull request with a read-only token and no secrets) and `gate-comment.yml` (runs from the default branch after `gate-pr`, never runs PR code, posts or edits the one comment). Tested only on a private repository with same-repository pull requests.

To use them in the Smoothieware repository:

1. Copy both files into `.github/workflows/` on the default branch (`edge`). The `workflow_run` trigger only works from the default branch.
2. Repository variables (Settings > Secrets and variables > Actions > Variables): `GATE_REPO` and `GATE_REF` (this repository and a pinned commit), `SIM_REPO` and `SIM_REF` (smoothie-sim and a pinned commit). Optional: `AI_BASE_URL`, `AI_MODELS`.
3. Optional secret `AI_API_KEY`, if your AI endpoint needs a key. Without a reachable model the AI row shows "skipped".
4. Pin the actions and toolchain by commit SHA and hash as they are in the files; update them on purpose, not automatically.
5. Open a test pull request from a fork before relying on it.

Adopting it in the Smoothieware project is a maintainer decision; the gate only informs and fails on a broken build, a clear rule violation or an unexplained simulator step mismatch.
