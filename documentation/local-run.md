# Run the gate locally

```
smoothie-gate run --repo ~/Smoothieware --base origin/edge --out /path/to/new-folder
```

- `--base` is the branch you would merge into. `--head` is the commit under test (default `HEAD`).
- `--out` must not exist yet. Put it in a scratch location; the run writes about 1 GB.
- `--no-model` skips the AI review, so nothing leaves your machine.
- `--sim-dir` and `--toolchain` (`gcc48` default) override the smoothie-sim folder and the compiler.

Only committed files are checked: the run exports `--base` and `--head` with `git archive`. Commit first.

It builds base and head in Release and Debug (four builds) and the simulator, in parallel; compares sizes; replays the 26 simulator cases; reviews the diff; and merges everything. Progress is printed as JSON lines. The exit code is 1 only if the overall status is `fail`.

Results: `OUT/report/comment.md` (the text a PR comment would carry), `OUT/report/report.html`, and `OUT/results/` with one `*.result.json` per stage ([format](result-schema.md)). `OUT/work` holds the exported trees and can be deleted.
