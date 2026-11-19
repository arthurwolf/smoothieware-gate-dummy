# Dual-firmware workflow candidates

The files under `workflow-templates/dual-firmware/` are undeployed installation candidates. They do not replace the legacy workflows under `.github/workflows/`. Neither successful lint nor local tests establish GitHub behavior, fork isolation or correct publication.

`gate-dispatch.yml` handles pull-request metadata with permission to dispatch an exact default-branch worker. It checks out trusted gate code only. Its one-shot dispatch receipt records an uncertain request before sending it; reconcile an uncertain outcome through GitHub before manually retrying. The uploaded receipt contains no full policy or credentials.

`gate-build.yml` accepts only a pull-request number. The authenticated API adapter derives the current firmware repositories and revisions, and verifies the active workflow attempt. Base and head objects are checked out separately without persistent credentials. Source preparation exports exact committed blobs and a bounded textual diff, excluding dirty files and external filters. The head object store can read the trusted base object store through Git's alternate-object mechanism; no contributor command is used to fetch dependencies.

The worker retains and verifies gate, simulator, library and case inputs. Compiler downloads are bounded and their canonical archive hashes are verified before extraction. Compilation, measurement, replay and chart replay share one digest-pinned container runtime with no network, credentials, daemon socket or writable host output. The host's read-only GitHub token remains available to trusted freshness checks. The runner label `ubuntu-24.04` selects an operating-system series; it does not pin an immutable host image. The workflow records the actual host image version in its log.

The worker uploads selected JSON only after successful artifact preparation, including for a current failed check. Missing or stale results cannot become a successful job. Artifact names bind the run and attempt. A separate trusted reporter must authenticate the completed run and bounded archive again before publishing a check on the firmware head or editing a PR comment. That reporter is still under development; these candidates are not a complete deployment.

## Trusted configuration

Repository variables select `GATE_REPO`, full-commit `GATE_REF`, `SIM_REPO`, full-commit `SIM_REF` and `GATE_POLICY_JSON`. The V2 profile also needs `SIM_LIBRARY_REPO`. No model credentials belong in either candidate.

The policy JSON supplies repository name and numeric identity, worker workflow ID and path, default branch, allowed base branches, and `profile` (`v1-lpc1768` or `v2-prime`). Its `pins` supply `gate_ref`, `sim_ref`, `controller_sha256`, `simulator_sha256`, `toolchain_sha256` and `runtime_image` as a repository reference with a SHA-256 digest. V2 additionally requires `shared_library_ref` and `shared_library_sha256`. These values must describe reviewed, reproducible inputs; the helpers reject mismatches rather than choosing new pins automatically.

The worker's installed filename must match the policy's workflow path, and its API workflow ID must match the policy. The dispatcher and worker must use the same trusted policy. Keep the worker run name unchanged: the authenticated API parser uses its strictly bounded pull-request-number format. Policy changes can invalidate outstanding runs, which then need a fresh dispatch.

## Acceptance before adoption

Verify the complete reporter and provision the pinned runtime before installation. Exercise both firmware profiles with known-good and known-bad changes, a real fork, a changed PR during execution, reruns and stale artifacts, missing artifacts, compiler/model outages, cancellation and hostile returned files. Verify the check's head revision, conclusion and single-comment update from actual GitHub API readback. Physical-board behavior remains outside simulator acceptance.
