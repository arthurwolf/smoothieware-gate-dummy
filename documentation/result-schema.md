# Result schema

Every smoothie-gate stage writes one `<stage>.result.json` file. A local run also writes `manifest.json`, which identifies its inputs and required stages.

| Field | Type | Meaning |
| --- | --- | --- |
| `stage` | string | Stage name, such as `build-base-release`, `build-pr-debug`, `size`, `sim` or `ai-review`. |
| `status` | string | `pass`, `warn`, `fail`, `skipped` (could not run, for example the model was unavailable) or `error` (the stage itself broke). |
| `title` | string | Short heading shown in the report. |
| `summary` | string | One line shown next to the status. |
| `details` | string | Markdown shown in a collapsed section. May be empty. |
| `metrics` | object | Flat map of names to numbers or strings, for example `flash_delta_release`. |

No other fields are allowed. Validate files with:

```sh
python3 -I scripts/gate_result.py build.result.json size.result.json
```

The command prints one JSON line per problem and per file, and exits 1 if any file is invalid. The local reporter exits 1 for a failed check and 2 for an incomplete or broken check. Model unavailability may be `skipped` without failing the run; a skipped build, size comparison or simulator replay is incomplete.

The manifest records the firmware profile and upstream profile, board, full base and head revisions, selected compiler version, simulator revision and source hash, reference and case-inventory hashes, and the required stages. When present, it is checked for missing or duplicate required stage results. For current V2 simulator inputs, `simulator_dependencies.lib-smoothie.sha256` separately identifies the frozen canonical shared headers, source and staging tools. The selected compiler version is not proof that a build ran. Hashes identify the snapshots used for a local run; they do not establish that the contents are independently reviewed or that a GitHub artifact is trustworthy.

`controller_sha256` is required: it identifies copied Python scripts, prompts, built-in cases and launcher by relative path, executable mode and content. Local runs restart in an isolated Python process from `work/controller-input` before firmware work. Changing the original checkout afterward does not change later helpers or prompts. This source digest does not identify the interpreter, compiler, dispatcher, runtime image or model service.

The experimental GitHub preflight also requires `pins.controller_sha256` in trusted policy and compares the artifact's top-level controller digest with it before extraction. Establish that pin from the reviewed trusted checkout; never adopt the expected value from the downloaded artifact. Matching metadata does not authenticate an unisolated build's results.

The experimental dispatch helper validates API repository, workflow and PR metadata before selecting the trusted default branch. It sends only the PR number. An exclusive recovery journal is flushed before the one POST; an unavailable or malformed response leaves `outcome-uncertain` and must be reconciled before retrying. A successful response must supply one positive run ID and matching canonical API and HTML URLs. Final receipt replacement preserves the uncertainty record until the complete replacement is ready. This helper is not yet wired into the workflows and has no live dispatch acceptance.

Standalone `scripts/make_manifest.py` requires `--inputs NEW_DIRECTORY` as well as `--out NEW_MANIFEST`. It restarts from `NEW_DIRECTORY/controller-input` and retains `sim`, `cases`, `case-inventory` and any required `lib-smoothie` sibling there. Later stages must consume those retained paths. Both output locations must be fresh; preparation refuses reuse. The manifest must be outside the retained input directory to avoid changing a snapshot after hashing it. The legacy workflow template now selects these paths, but its untrusted execution boundary and real GitHub behavior remain unverified; a digest alone does not authenticate its results.
