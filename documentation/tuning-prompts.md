# Tuning the AI review prompts

Prompts are files in `prompts/`: `review-rules.md` (Smoothie developer rules) and `review-general.md` (bugs, leaks, undefined behaviour). `rules.json` is a copy of smoothie-sim's table of forbidden blocking calls; the unit tests fail if it drifts from `../smoothie-sim/rules.json`, so change it in smoothie-sim first.

To test a change, replay old rejected PRs: `python3 scripts/replay_review.py --help`, with the cases in `replay/prs.json`. Compare findings with what maintainers actually said. Keep the uncertainty section in the prompts, and keep the rule that a finding must quote a line of the diff (invented quotes are dropped). Details: [AI review](ai-review.md).
