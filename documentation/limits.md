# Limits

- The simulator has no A/B/C rotary axes, no endstop or probe hardware, no laser PWM or spindle model, and M112 does not flush a move that is already running. Reference traces record current behaviour and are pending human review (`approved_by` is empty).
- Size is measured for the Smoothieboard (LPC1768) only. Debug images are linked with an enlarged flash region.
- Only a pull request from the same repository was tested on GitHub; fork behaviour relies on GitHub's documented rules for `pull_request` (read-only token, no secrets).
- The AI review gives advice and can be wrong or unavailable.
- Local runs use ni-build; GitHub runs use plain `make` on a pinned gcc 4.8 2014q1.
