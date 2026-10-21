# AI review and privacy

The diff, the touched files and the PR title and description are sent to the configured OpenAI-compatible endpoint (default: the Kilo Gateway free models, which may log requests). Do not run the review on private code you cannot share. `--no-model` (local run) or an empty `AI_BASE_URL` plus no reachable endpoint skips it; the pattern scan for blocking calls still runs locally. In CI the key and endpoint are in the comment workflow only, never in the job that runs PR code. Model output is advice: it is validated, sanitised and never executed.
