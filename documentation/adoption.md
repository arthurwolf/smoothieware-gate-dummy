# Adopting smoothie-gate in Smoothieware

The gate has two workflow files in `.github/workflows/`: `gate-pr.yml` runs builds and tests, and `gate-comment.yml` runs from the default branch after it finishes. An earlier version was tested in a private repository with same-repository pull requests. The current changes have not been tested on GitHub or with a real fork.

**Do not rely on these workflows for untrusted pull requests yet.** Fork build commands can run arbitrary code in the build job and may tamper with the job's results or the gate files. The reporting workflow checks the pull request identity, current revisions and artifact file limits, but those checks do not authenticate the results. The V2 profile adapters have not been tested on GitHub.

Once the execution boundary is fixed and verified, adoption requires:

1. Copy both files into `.github/workflows/` on the default branch (`edge`). The `workflow_run` trigger only works from the default branch.
2. Repository variables (Settings > Secrets and variables > Actions > Variables): `GATE_REPO` and `GATE_REF` (this repository and a pinned commit), `SIM_REPO` and `SIM_REF` (smoothie-sim and a pinned commit), and `FIRMWARE_PROFILE` (`v1-lpc1768` or experimental `v2-prime`; defaults to V1). Optional: `AI_BASE_URL`, `AI_MODELS`.
3. Optional secret `AI_API_KEY`, if your AI endpoint needs a key. For Kilo, configure `AI_PAID_PROXY_URL` as an Actions secret using an HTTP proxy from the paid proxy collection; the workflow only sends public repository content. Without a configured proxy or reachable model the AI review reports unavailable.
4. Pin the actions and toolchain by commit SHA and hash as they are in the files; update them on purpose, not automatically.
5. Open a test pull request from a fork before relying on it.

Adopting it in the Smoothieware project is a maintainer decision; the gate only informs and fails on a broken build, a clear rule violation or an unexplained simulator step mismatch.
