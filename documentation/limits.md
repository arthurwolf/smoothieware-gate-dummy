# Limits

- The simulator has no A/B/C rotary axes, no endstop or probe hardware, no laser PWM or spindle model, and M112 does not flush a move that is already running. Reference traces record current behaviour and are pending human review (`approved_by` is empty).
- Size is measured for the Smoothieboard (LPC1768) only. Debug images are linked with an enlarged flash region.
- Only a pull request from the same repository was tested on GitHub; fork behaviour relies on GitHub's documented rules for `pull_request` (read-only token, no secrets).
- The AI review gives advice and can be wrong or unavailable.
- Local runs use ni-build; GitHub runs use plain `make` on a pinned gcc 4.8 2014q1.

## Simulator reliability

The simulator is deterministic, so a case that differs from its reference is run a second time. If the second run matches the reference, the case counts as passed and the stage says it was flaky (status warn). If the two runs differ from each other, the stage reports a check error, not a failure of the pull request. A crash (signal) or timeout is retried once; a crash that repeats is a failure of the pull request. Each case runs in its own working directory without stdin.

## Suggested fixes

The model writes the suggestions and may return none. For a blocking call the gate adds a removal diff from the pull request's own added lines, so that case never depends on the model. Other kinds of problem (build errors, leaks) depend on the model.
- The step charts draw pulse-level data only when the base firmware's simulator builds and reproduces the stored reference trace itself; if not, the chart shows planner blocks and says "Pulse view unavailable". Pulses over 400 per motor are not drawn in the zoom, which covers about 90 pulses around the first difference. Pulse shifts of 4 ticks (40 µs) or less are not marked.
