# Reading the report

The top line is the worst status of all checks: pass, skipped (ignored when other checks ran), warn, error, fail.

- **fail**: a build does not compile, the diff adds a blocking call listed in `prompts/rules.json`, or a simulator case emits different steps than its reference. Fix it or explain it.
- **warn**: advice. Size growth above the threshold, an AI finding. A maintainer decides.
- **skipped**: a check could not run, for example the AI model was unreachable. The reason is in the details.
- **error**: the gate itself broke (a stage produced no valid result). Not a verdict on the PR.

Size tables show base and PR bytes and the signed change for Release (-O2) and Debug (-O0). Debug images are linked with an enlarged flash region just to be measured; they do not fit the real chip and must never be flashed. AI findings quote the line they are about; treat them as questions, not verdicts.

## Memory map and legend

Under each size table a row of squares shows the chip: 🟦 used before and after, 🟧 added by the pull request, 🟩 freed, ⬛ free. One square is 16 KiB of flash or 1 KiB of RAM; any change shows as at least one square. Warnings start above +64 B flash or +4 B RAM.

## Message to the contributor

At the bottom, an AI-written message thanks the contributor and lists what must change and what is nice to have, with short suggested code or diffs. The suggestions can be naive and are only general directions. It is advice: it never changes the overall status, and it is left out when the model is unavailable.

## Step charts (simulator section)

Each case with differing steps expands to one chart. All panels of the "Whole case" part share one time axis (ms from the start of the case). Blue is the reference (base firmware), orange is this pull request, red marks a difference. "Speed" is mm/s per motor, "Position drift" is the number of steps this PR is ahead (+) or behind (-). The zoom shows STEP pulses and the DIR level as a logic analyzer would, with a circle on the first pulse that moved; the violet band in the top part shows where the zoom sits. The numbers table repeats the totals and the worst blocks.
