# Updating a reference trace

When a PR changes behaviour on purpose, the simulator stage fails with a step mismatch. Re-record only the cases that should change, from the PR's own simulator build:

```
python3 scripts/sim_stage.py --sim BUILD/smoothie-sim --cases cases --out OUT --only <name> --record
```

Commit the new `expected.trace` in the same pull request and state the reason in the commit message and in `meta.json` (`approved_by`). Never regenerate all references to make a run pass: a mismatch you cannot explain is a bug.
