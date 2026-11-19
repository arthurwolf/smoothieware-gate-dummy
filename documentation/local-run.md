# Run the gate locally

```
smoothie-gate run --repo ~/Smoothieware --base origin/edge --out /path/to/new-folder
```

- `--base` is the branch you would merge into. `--head` is the commit under test (default `HEAD`).
- `--out` must not exist yet. Put it in a scratch location; the run writes about 1 GB.
- `--no-model` skips the model request. Builds and simulator replay still run on the configured remote build machine.
- `--sim-dir` overrides the smoothie-sim folder. `--profile` selects `v1-lpc1768` (the default) or the experimental `v2-prime` profile; `--toolchain` overrides the profile's compiler selection.

Only committed files are checked: the run exports `--base` and `--head` with `git archive`. Commit first.

For V1, the gate builds base and head in Release and Debug (four builds), builds the base and head simulators, compares sizes, replays the 26 simulator cases, reviews the diff, and combines the results. It freezes the simulator and case inputs and records their hashes in the manifest. Progress is printed as JSON lines. The exit code is 0 for pass or advisory warnings, 1 for a failed check, and 2 for an incomplete or infrastructure-error result.

V2 Prime support is under development. The local adapter selects the Prime build and STM32 memory checker and replays the experimental V2 simulator. Real base and head builds and their size comparisons have been exercised in both configurations using an isolated GCC 10 dispatcher; the standard dispatcher still requires GCC 10 integration. The current simulator also requires its sibling `lib-smoothie` project; the gate freezes and hashes that dependency before building. Three simulator references (short segments, IJK arcs, delta) have scripted analytical timing reviews but not physical or human measurement; rerun the gate to confirm complete coverage. A local run does not validate a physical board.

Size analysis also runs on the remote build machine. The trusted report stage invokes smoothie-sim’s checker inside a bounded sandbox with read-only inputs and compiler tools. Firmware provenance remains GCC 4.8 or GCC 10; these comparisons use GCC 12 binutils to measure the returned ELF files. Oversized debug measurements remain advisory and do not imply that the image fits the board.

Results: `OUT/report/comment.md` (the text a PR comment would carry), `OUT/report/report.html`, and `OUT/results/` with one `*.result.json` per stage ([format](result-schema.md)). `OUT/work` holds the exported trees and can be deleted.
