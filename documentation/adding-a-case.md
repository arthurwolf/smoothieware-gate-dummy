# Adding a simulator case

A case is a folder `cases/<name>/` with:

- `config.txt`: the machine configuration.
- `input.gcode`: the program.
- `meta.json`: `name`, `covers`, `why`, `sim_args` (extra simulator options), `reference_status`, `approved_by`.
- `expected.trace`: the reference step trace, recorded by the next step.

Record it from a firmware build you trust (a simulator binary built from the base branch):

```
python3 scripts/sim_stage.py --sim BUILD/smoothie-sim --cases cases --out OUT --only <name> --record
```

Read the trace and check it makes sense: it is the current behaviour, not proof of correctness. Then set `approved_by` and commit. Cases should stay varied (machine kind and use) and run in seconds.
