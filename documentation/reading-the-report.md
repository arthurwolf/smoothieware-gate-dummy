# Reading the report

The top line is the worst status of all checks: pass, skipped (ignored when other checks ran), warn, error, fail.

- **fail**: a build does not compile, the diff adds a blocking call listed in `prompts/rules.json`, or a simulator case emits different steps than its reference. Fix it or explain it.
- **warn**: advice. Size growth above the threshold, an AI finding. A maintainer decides.
- **skipped**: a check could not run, for example the AI model was unreachable. The reason is in the details.
- **error**: the gate itself broke (a stage produced no valid result). Not a verdict on the PR.

Size tables show base and PR bytes and the signed change for Release (-O2) and Debug (-O0). Debug images are linked with an enlarged flash region just to be measured; they do not fit the real chip and must never be flashed. AI findings quote the line they are about; treat them as questions, not verdicts.
