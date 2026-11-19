# Adding a simulator case

A case is a folder `cases/<name>/` with:

- `config.txt`: the machine configuration.
- `input.gcode`: the program.
- `meta.json`: `name`, `covers`, `why`, `sim_args` (extra simulator options), `reference_status`, `approved_by`.
- `expected.trace`: the reference step trace, recorded by the next step.

When a simulator result must include a firmware message, add an optional
`expected_serial_contains` array of nonempty strings to `meta.json`. Every
string must appear in the simulator's `serial_out`. To reject messages such as
an unexpected firmware error, add a `forbidden_serial_contains` array. A missing
output field is an incomplete check; a message mismatch is retried just like a step mismatch.
V2 cases remain incomplete until their references are reviewed.

For a V2 case that needs two extruders, set `simulator_variant` to
`five-actuators`. The local gate and experimental workflow build a separate
simulator with `MAX_ROBOT_ACTUATORS=5` for those cases; the default simulator
continues to serve the four-actuator references. When running the stage directly,
provide that binary with `--sim-five` and, for comparison charts, the matching
base binary with `--base-sim-five`. A missing required variant is an incomplete
check. The five-actuator references and workflow still need independent review
and live GitHub verification.

Gate runs require a matching base simulator as well as the simulator under test.
For direct stage runs, `--require-base-sim` applies the same check; it remains
optional when recording a new candidate with a single simulator.

Record it from a firmware build you trust (a simulator binary built from the base branch):

```
python3 scripts/sim_stage.py --sim BUILD/smoothie-sim --cases cases --out OUT --only <name> --record
```

Read the trace and check it makes sense: it is the current behaviour, not proof of correctness. Then set `approved_by` and commit. Cases should stay varied (machine kind and use) and run in seconds.
