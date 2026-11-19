# Changelog

## [Unreleased]

### Added
- V2 Prime gate rerun with GCC 10.3-2021.10 now passes (exit 0, warn: planner advice only; all 30 simulator cases match their references; Release flash -16 B).

- V2 simulator references for `arcs-ijk`, `delta-triangle` and `tiny-segments` are now reviewed against independent kinematic timing models and counted as covered; the arc reference records one block that decelerates 17% faster than the configured limit.

- The trusted reporter refuses to publish over a newer publisher-owned `smoothie-gate` check on the same firmware head, writes `report/report.html`, and exits 0 for pass or warning, 1 for a failed gate and 2 for an incomplete one.

- Experimental trusted reporter coordinates bounded artifact validation and firmware-head publication with freshness checks, one-shot write journals and API readback; live publication remains unverified.

- Experimental publication planning derives firmware-head check requests and a bot-owned report update, refusing newer or duplicate reports; it performs no network writes and live acceptance remains unverified.

- Undeployed dual-firmware dispatch and build templates connect authenticated source export, retained inputs, isolated checks and run-bound JSON artifacts; trusted reporting and live workflow acceptance remain unfinished.

- Experimental workflow configuration validates repository, checkout refs, controller digest, simulator digest and canonical compiler pins before selecting runtime inputs; V2 requires an explicit shared-library ref.

- Experimental workflow manifest binding adds authenticated run and policy provenance to complete input metadata, rejecting different firmware revisions or controller identity instead of replacing them.

- Experimental CI worker prepares its JSON-only report artifact only for current results, authenticating before assembly and checking freshness again afterward. A current failed check retains its actual failure code; no upload or publication is performed.

- Experimental CI source-preparation command authenticates both revisions before export, generates a bounded committed diff with external filters disabled, and rejects stale preparation after export; live workflow behavior remains unverified.

- Experimental authenticated worker preparation and freshness confirmation now have bounded command-line adapters for workflow source selection; live GitHub behavior remains unverified.

- Experimental CI source export reads exact bounded commit/blob objects, excludes dirty checkout files and preserves only internal links without running checkout hooks or attribute filters; workflow integration remains pending.

- Experimental CI artifact assembly selects complete bounded stage JSON and authenticated identities, excluding firmware, executables and per-case intermediates; workflow integration remains pending.

- Experimental CI command-line bootstrap verifies the trusted controller digest and restarts from retained code before preparing or executing a request; live workflow acceptance remains pending.

- Experimental complete CI worker composes authenticated input preparation, four firmware builds, simulator builds and reporting with one runtime, and rejects publication when the PR changes during execution; live workflow acceptance remains pending.

- Experimental GitHub worker preparation authenticates the active run attempt and pull-request revisions before source selection, then rejects changed inputs after execution; workflow integration remains pending.

- Experimental CI post-build coordination shares runtime custody across size parsing and simulator replay, runs deterministic developer-rule review without model inference, and produces complete reports and local charts; live execution remains unverified.

- Experimental CI build coordination reuses the firmware and simulator final-file validators with one runtime, retains all firmware stage results and stops new launches after infrastructure uncertainty; live execution remains unverified.

- Experimental CI input preparation checks authenticated revision bindings and retained controller, simulator, case and library bytes before preparing the profile-specific compiler; worker integration remains pending.

- Experimental CI compiler preparation verifies a retained copy of the pinned archive and rejects unsafe paths, escaping links and missing tools before returning an extracted compiler. Both pinned archives passed preparation on ni-vm; workflow integration remains pending.

- Experimental trusted dispatch validates repository, workflow and PR metadata, selects only the default branch, and binds exact returned run URLs and IDs. One-shot transport preserves a recovery journal before POST and refuses duplicate output; live workflow acceptance remains pending.

- Local runs and standalone manifest preparation freeze controller scripts, prompts and built-in cases and restart from that copy. Reports require and display the controller digest; standalone preparation retains simulator and case inputs for later stages. Live workflow acceptance remains pending.

- Experimental artifact preflight binds the reported controller digest to a required trusted-policy pin before extraction; metadata matching does not replace execution isolation.

- A bounded result-archive reader checks the download digest and rejects unsafe ZIP paths, metadata expansion and malformed files before extraction; actual download transport and workflow integration remain unverified.

- Experimental GitHub preflight authenticates run and pull-request metadata, selects and downloads an attempt-specific artifact, and checks freshness, digest and manifest binding before extraction; actual workflow integration and live acceptance remain pending.

- Local size analysis isolates ELF parsers through ni-build, validates bounded bank results and safely formats failure diagnostics. Both firmware profiles match their established optimized/debug measurements; Docker and GitHub acceptance remain pending.

- The build CLI has an experimental Docker CI runner for both firmware profiles, preserving common final-file validation and failure classification; live container execution remains unverified.

- Firmware workers can select the experimental digest-pinned runtime while retaining the shared final-file validator; simulator replay has the same optional execution adapter. Live Docker and workflow verification remain pending.

- An experimental digest-pinned Docker adapter bounds child resources and rejects returned bytes when container cleanup cannot be verified; live Docker behavior and workflow integration remain unverified.

- Complete isolated local V2 execution now covers all four firmware and simulator builds and 30 trace comparisons; three independently unapproved references correctly keep acceptance incomplete.

- A separate dispatched-workflow identity contract distinguishes trusted workflow revisions from firmware PR revisions and binds artifacts to run attempts and execution pins; workflow integration remains pending.

- Local simulator replay now isolates each attempt with three read-only inputs and bounded private output; both inventories matched in a real pilot, with V2 reference review still incomplete.

- Local simulator builds now use bounded private memory and trusted final-file collection; a real V2 simulator build passed, while full adapter confirmation remains pending.

- Local V1 firmware builds use the bounded-memory sandbox with read-only compiler compatibility libraries and private debug measurement adjustment; all four configurations and 26 simulator cases passed.
- Local V2 firmware builds connect the memory sandbox, bounded pipe capture, final-file validation and trusted collection; all four configurations passed before the shared V1 worker refactor.
- A bounded pipe transport validates exact final-file names and lengths and rejects malformed, duplicate, excessive, truncated or trailing data before host files are created; firmware integration remains pending.
- A bounded sandbox process runner preserves failure exits and binary output, stops stdout overflow and timeouts, and retains a capped diagnostic tail; firmware integration remains pending.
- An experimental single-sandbox command provides a tested byte-limited memory workspace without a host output mount; final-file transport and firmware integration remain pending.
- An experimental controller command can mount trusted code read-only; nested namespace creation is blocked in the tested runtime.
- A bounded final-output collector rejects unexpected firmware output, links, special files, oversized files and existing destinations; sandbox build integration remains pending.
- V2 builds normalize source paths to stable firmware and flashloader prefixes and identify the measurement policy in build results; all four base/head optimized/debug builds have passed with this policy.
- The experimental sandbox can expose a separately pinned compiler directory read-only, without exposing host build storage.
- Experimental sandbox execution can be wrapped in a transient user-service cgroup with explicit memory, process, CPU and runtime limits.
- An experimental build-sandbox command adapter separates read-only source from writable output and clears the child environment; workflow integration remains pending.
- Firmware profile and required-stage manifest validation; incomplete manifested runs are reported as errors.
- Simulator inputs and reference cases are snapshotted and identified by content hashes in local reports.
- Size comparisons show remaining capacity and its signed change alongside used bytes.
- Local gate runs dispatch simulator replay as a remote whole job and retain check results when replay detects a failure.
- V2 build snapshots include isolated Git revision metadata for the firmware's embedded version string.
- Experimental Prime profile adapters, STM32 memory-bank reporting, and V2 simulator cases; real firmware builds have been exercised with an isolated dispatcher, while complete simulator coverage remains under review.
- V2 CoreXY and linear-delta single-move references have independent geometric and approximate timing reviews.
- The V2 absolute and relative positioning reference has independent motor-step, junction-speed and timing reviews.
- A V2 inch and millimetre positioning reference, with independently checked unit conversion, steps and approximate timing.
- The V2 CoreXY square and smooth-junction references have independent geometric and approximate timing reviews; the delta-triangle and short-segment references remain pending.
- A V2 Z-speed scenario with an explicit limit and a higher-limit control, independently checked using a continuous timing approximation.
- V2 high-acceleration and feed-override scenarios have independent continuous timing reviews.
- The V2 dwell and synchronization reference has an independent motion and timing approximation review; physical timing remains unverified.
- The V2 Cartesian linear reference has an independent motion and timing approximation review; physical timing remains unverified.
- The V2 sharp-junction reference has an independent Cartesian motion and timing approximation review.
- The overview reflects the expanded V2 case set and corrects the V1 rotary-axis limitation.
- The V2 print-square reference has independent motion, extrusion-step and approximate timing review; fan and physical extrusion behavior remain unverified.
- The V2 coordinated-extrusion, retract and laser-raster motion references have independent step-count and timing reviews; laser PWM remains unverified.
- The V2 two-extruder reference has an independent tool-selection, step-count and timing review.
- The V2 workspace reference has an independent offset, junction-speed and timing review.
- Documented out-of-order V2 planner timing boundaries observed in simulated short-segment, arc and delta motion.
- V2 simulator reports include advisory findings from smoothie-sim's existing per-block planner checker, even when a trace matches its reference.
- The V2 untriggered-homing and untriggered-probing references have independent travel, timing and failure-path reviews.
- A V2 simulated kill-button scenario with an independently reviewed stop position and alarm assertion; physical emergency-stop behavior remains unverified.
- Documented the V2 source and simulator behavior for the unsupported V1 M112 scenario, separately from the simulated kill-input case.
- Simulator cases can require or forbid firmware serial messages, with mismatches retried and unreviewed V2 references kept incomplete.
- Candidate V2 IJK arcs and coordinate-system scenarios remain under review; the equal-arm SCARA reference has independent geometry and approximate timing review.
- A source-reviewed V2 radius-arc rejection case checks the reported error and halt; it does not claim radius-arc motion works.
- The V2 scenario inventory explains why V1 radius-arc motion is excluded for the examined firmware revision.
- V2 milling-motion and pause/resume references have independent motion and timing reviews; spindle output and host cooperation during pause remain unverified.
- Added a reviewed V2 case for motion sent by a host during suspension, and documented the observed return to the saved position on resume.
- The V2 tool-change reference has source-backed and approximate timing review using a five-actuator simulator; the two-extruder reference remains pending.

### Fixed

- Existing manifest provenance rejects float/boolean substitutions for integer identifiers. Local review shares CI's bounded textual diff and retains an error report when diff preparation fails.

- Rule review decodes Git-quoted source paths and keeps header-like added lines in their original file. CI preparation forces bounded textual diffs so contributor binary attributes cannot hide added code. Patch parsing preserves non-LF characters as content.

- Workflow manifests now include the frozen shared simulator library identity, matching local manifests.
- Experimental container execution now separates creation from start, retains recovery identity on uncertain cleanup, and validates capture settings before creating resources.
- Simulator retries and pulse-chart replays retain the selected isolation runtime; an uncertain container lifecycle prevents further launches on that adapter.

- Isolated V2 revision metadata refuses pre-existing directories and links before writing, preventing contributor-controlled paths from redirecting host writes.

- Simulator staging preserves then rejects source links before dispatch, preventing host-file targets from entering untrusted build inputs.
- Resource-limited replay services preserve the dispatcher working directory for relative checker and case paths.

- Malformed simulator argument metadata is reported as a case error before execution instead of aborting the entire replay stage.

- Cap local V2 sandbox builds at four Rake jobs to match their CPU budget and record the actual parallelism in build results.

- Freeze and hash the canonical lib-smoothie dependency required by the current V2 simulator stager, and include it in isolated simulator build inputs.

- Accept traversal-only source parents while rejecting linked, special and oversized V2 recipes before path normalization; use bounded descriptor reads and no-follow directory access to protect host files.

- Preserve bounded compiler diagnostics when the experimental V2 worker rejects malformed final files.
- Sandbox final-file headers enforce UTF-8 instead of accepting JSON encoding autodetection.
- Final-output collection rejects oversized directory inventories incrementally and cleans partial copies after read failures.
- V2 recipe-preparation and dispatch exceptions now produce an infrastructure-error stage result instead of leaving the build report absent.
- Malformed memory-chart rows and metrics are rejected without interrupting report generation.
- V2 remaining flash and RAM capacity accounts for linker alignment gaps instead of subtracting only section sizes.
- V2 memory charts use occupied region extents for free-space bars while retaining section-size changes for warning comparisons.
- V2 remote builds retain the final firmware files while discarding generated intermediate objects before artifact collection, and report dispatcher failures with their diagnostics.
- Firmware result manifests reject incomplete Git revision identifiers of intermediate lengths.
- Kilo Gateway reviews require a configured paid proxy and a public-source attestation; proxy bypass settings cannot route them directly.
- Simulator failures include bounded standard-output diagnostics as well as standard error.
- Gate runs report missing base simulator builds as incomplete instead of silently omitting the base comparison.
- The comment workflow derives its pull request from the trusted workflow event and checks current revisions, rather than trusting a number supplied by the build artifact.
- The comment workflow rejects oversized, unexpected or linked result files before reading an untrusted artifact.
- The pull request workflow builds the specified head revision and reports failed or missing build and simulator stages as a failing check.
- The reporting workflow posts the report before reflecting a failed or incomplete result in its check conclusion.
- Simulator replay reports empty case sets, malformed output and unconfirmed retry mismatches as incomplete, requires reviewed V2 references for claimed coverage, and pulse charts reject different timer periods.
- Missing firmware build outputs now produce an explicit incomplete size result.
- V1 static RAM figures exclude the linker heap reservation and overlapping dummy stack section.
- V2 trace comparisons reject missing initial positions and accept equivalent timer-period notation.
- V2 rule advice remains visible as a warning when the review model is unavailable.
- Partial AI review failures are reported as incomplete advice without failing the pull request.
- Local-run documentation describes the profile selection, actual exit codes and unfinished V2 verification.
- Adoption documentation identifies the remaining untrusted-build integrity gap and GitHub verification limits.
- The overview identifies V2 work in progress and the current workflow security boundary.
- GitHub workflow adapters select V1 or experimental V2 build, size, simulator and review settings through a repository variable; live verification remains outstanding.
- Simulator section: every case whose steps differ is an expandable item with one chart on a shared, labelled time axis: planner blocks, speed per motor (mm/s), position drift (steps the PR is ahead or behind), a zoomed logic-analyzer view of STEP and DIR lanes (reference above this pull request) with a circle on the first difference (a direction difference is described in plain words: which level each side goes to, or how much later one reverses; the circle is on the DIR lanes when the direction signal is what differs), a one-line red verdict above the sentence when a motor moves the opposite way ("Motor 0 turns the opposite way from 221.2 ms"), a plain-language first-difference sentence, the zoom placed first as its evidence, the drift panels replaced by one line when there is no drift, plain lane names (steps / direction) instead of STEP / DIR, move durations in ms, red only on the cells that differ, a numbers table (step counts, direction-change counts, first moved pulse; its sub-table lists the worst differing moves), `up = forward` / `down = reverse` tags (larger), a direction-count cell that says "(same count, flipped)" when a change points the opposite way, on the direction lanes plus a coloured "now forward" / "now reverse" label at the end of a lane whose direction changes and a "same speed in reference and PR" note on speed panels whose curves coincide, a "stays forward/reverse" end label on the unchanged direction lane, a large before/after pair ("Reference: motor 0 turns reverse" / "This PR: motor 0 turns forward") under the verdict line, wider step pulses on taller lanes in the zoom, a violet (was yellow, easily mistaken for the orange pull request) zoom band, a larger, bolder red circle, "high = forward / low = reverse" lane tags, a "colour = differs" note on the planned-moves strip, larger lane names, panel sublabels and "max" labels, a larger, brighter legend and time-axis labels, a legend; table headers say base / PR instead of ref, the drift note sits below the time axis, and an equal direction-change count with different timing reads "(timing differs)"; a motor whose steps and direction are identical is one line in the zoom instead of four lanes (`scripts/trace_chart.py`, `scripts/pulse_data.py`). The reference pulses come from a simulator built from the base firmware (`--base-sim`), used only when it reproduces `expected.trace`; otherwise the chart falls back to planner blocks and says why.
- Size section shows real chart images (dark SVG, drawn by `scripts/charts.py`, published on the `gate-images` branch by the comment workflow): memory used with legend, change against the warning limit, symbols that grew most.

### Changed

- V2 simulator jobs snapshot only the consumed Firmware subtree, excluding bootloader files and their legitimate internal driver link.

- Route local V1 builds through the shared bounded-memory worker and move its measurement-only linker adjustment into the private build copy. Full V1 adapter verification remains pending.

- Run local V2 builds through the bounded-memory sandbox worker, snapshotting trusted helpers separately and collecting only final firmware files. GitHub and V1 sandbox integration remain pending.
- V2 builds normalize compiler source paths to a shared canonical prefix and identify that measurement policy in their result metrics.
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

