# Smoothieware developer rules used by the AI review

Every rule below is taken from a written source. The review prompt (`review-rules.md`) lists the rule ids; the model may only use these ids or `general`. Nothing here is invented: if a rule has no source line, it does not belong in this file.

Sources:

- **CS** = Coding Standards, https://smoothieware.org/coding-standards (read 2026-10-19)
- **PR** = pull request template of the firmware repository, `PULL_REQUEST_TEMPLATE.md` (read 2026-10-19; the wiki "Contribution guidelines" page carries the same list)
- **M-n** = a public review comment of the maintainer on pull request n (links in smoothie-sim `cases/pr-replay/evidence.json`)
- **rules.json** = `prompts/rules.json`, a copy of smoothie-sim `rules.json` (the table of blocking calls). `tests/test_ai_review.py` fails when the copy differs from smoothie-sim's.

| id | rule | source | checked by |
| --- | --- | --- | --- |
| `blocking-wait` | No `wait`, `wait_ms`, `wait_us`, `sleep` anywhere; they stop all processing (and USB drops). Only `THECONVEYOR->wait_for_idle()` is allowed. | CS "Do not use blocking wait loops"; M-620, M-633, M-955, M-1219, M-1227, M-1447, M-1488; rules.json | pattern scan (can fail) and model |
| `safe-delay` | `safe_delay_ms/us` keeps idle handlers running but still blocks the caller and is inaccurate; the maintainer has not ruled on it. Ask, never block. | CS (allowed for "several milliseconds"); rules.json `ask-maintainer` | pattern scan (warning) and model |
| `busy-loop` | No busy loop on the microsecond timer (`us_ticker_read`); use a hardware timer for accurate timing. | M-633 "use a timer"; CS | pattern scan (can fail) and model |
| `idle-starvation` | A loop in a handler must let idle events run (call `on_idle`) or be rewritten to loop through `on_idle`. | M-955, M-620 | model |
| `header-includes` | A header does not include other headers unless needed to define something in it; forward-declare classes and include in the .cpp. | CS | model |
| `header-code` | No method bodies in headers unless time-critical code that must be inlined. | CS | model |
| `ifdef` | No `#ifdef` in the code (only the makefile and main.cpp use it to remove modules). | CS | model |
| `cpp-practice` | Class fields private, getters/setters; non-external methods private; avoid multiple inheritance; destructors virtual; STL and C++11 where appropriate. | CS | model |
| `formatting` | 4 spaces per indent, no tabs; mostly Linux kernel bracket style. | CS | model |
| `warnings` | The code must compile with no warnings. | CS | model (obvious cases) and the build stage |
| `scope` | One feature, fix or refactor per pull request; no gratuitous restyling of existing code; comment and typo fixes in separate pull requests. | CS "Keep pull requests small"; PR | model |
| `compatibility` | Do not break existing config files; a breaking change needs a new config option to enable it. | PR | model |
| `binary-files` | No change to `FirmwareBin` or other binary files. | PR | model |
| `setup-specific` | No changes specific to one setup or company, no company-specific messages. | PR | model |
| `config-samples` | New config entries are exemplified in `ConfigSamples/Snippets`; existing samples are not reformatted or changed unless absolutely necessary. | PR | model |
| `language` | Comments, code and function names are in English. | PR | model |
| `pr-description` | The pull request has a detailed description (why, use case, wiki link) and states which tests were done. | PR | model, from the PR text only |

Not covered because no source exists: any rule about heap use, interrupt priorities or stack depth. The general review prompt may mention such problems as ordinary defects with evidence, never as project rules.
