# AI review

The AI review stage reads the pull request diff and the PR text and reports findings. It informs maintainers. It fails a run in one case only: a verbatim forbidden call (`wait`, `wait_ms`, `wait_us`, `sleep`, or a busy loop on `us_ticker_read`) found by the pattern scan, using the rules in `prompts/rules.json`. A model outage gives the status `skipped`.

## How it works

1. `scripts/ai_review.py` parses the diff and scans the added lines for the forbidden calls.
2. The diff is packed into chunks and sent twice: once with `prompts/review-rules.md` (Smoothie developer rules, listed with their sources in `prompts/rules.md`) and once with `prompts/review-general.md` (bugs, leaks, safety).
3. PR title, description, branch name and diff are untrusted. They are wrapped in a random marker and the model is told to treat them as data.
4. Each finding carries file, line, severity, rule, quoted evidence and confidence. A finding is dropped unless its evidence is a changed line of that file. Findings below confidence 0.5 are listed as low confidence. Model findings on a line the scan already flagged are dropped.

## Provider and limits

GitHub Models was the first choice in the plan. It no longer exists: the GitHub docs state "As of July 30, 2026, GitHub Models has been fully retired. The playground, model catalog, inference API, and bring your own key (BYOK) are no longer available to any customer." (`content/github-models/index.md` in the `github/docs` repository, read 2026-10-20.) There are no GitHub Models limits to record.

The default provider is therefore any OpenAI-compatible chat endpoint, set with `--base-url` / `AI_BASE_URL`, `--models` / `AI_MODELS` and `AI_API_KEY`. The default is the Kilo Gateway free tier (`https://api.kilo.ai/api/gateway`, no key). Free models seen in the model scan of 2026-10-19:

| Model | Context (tokens) | Max output (tokens) |
| --- | --- | --- |
| `dots-studio/dots-3-note-preview:free` | 512000 | 262144 accepted (460800 advertised, rejected) |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | 1000000 | 65536 |
| `nvidia/nemotron-3.5-lightning:free` | 1000000 | 65536 |
| `cohere/north-mini-code:free` | 256000 | 64000 |

The script tries the models in this order and falls back on HTTP errors, empty content or an `error` object in a 200 reply. Rate limits were not measured; during the replay below no request was skipped. A free gateway can change or disappear, so a run that cannot reach any model reports `skipped`.

## Replay results (2026-10-20)

The replay (`scripts/replay_review.py`, PR list in `replay/prs.json`) ran the stage on 8 rejected and 15 merged Smoothieware PRs with the models `dots-3` then `nemotron-3-ultra`. No run was skipped.

Rejected PRs (the maintainer's objections are in smoothie-sim `cases/pr-replay/evidence.json`):

| PR | Result |
| --- | --- |
| 564, 620, 633, 1227, 1447, 1488 | flagged with the rule I had expected |
| 955 | flagged as `busy-loop` (scan: `us_ticker_read` polling loop), not `blocking-wait` as I had labelled it |
| 1219 | scan flagged `wait_us(1)` as `safe-delay`, an ask-maintainer item, not `blocking-wait` |

Strict count: 6 of 8 under the expected rule. All 8 were flagged for the problem the maintainer named. The two label differences are my own reading after seeing the results; the stored expectations in `replay/prs.json` were not changed.

Merged PRs (15): 3 had no finding. 12 had findings, 32 in total. By kind:

- `pr-description` (missing wiki link or stated tests), `formatting`, `scope` and `header-code` findings follow written rules and can be checked from the diff text. Merged PRs do break these rules, so they are true by the rule but not blocking in practice. They are advice only.
- 14 findings are code concerns (units, overflow, ISR sharing, idle starvation). I checked none of them against the full source. Counts: not confirmed, not refuted. PR 1455 (`M48` probe loop without idle calls) matches the maintainer's idle-starvation rule.

No finding on a merged PR is a verified false positive, and none is a verified true positive. The review therefore cannot be called precise yet; it is a source of questions for a human reviewer.

## Tuning

No prompt change was made after the replay, because every rejected PR was flagged. Changing prompts to fit these 23 PRs would overfit them. To tune, edit `prompts/*.md`, rerun `replay_review.py`, and compare the table above.

## Privacy

The diff and PR text are sent to the configured endpoint. The default free gateway may log them. Do not run the stage on private code with it.
