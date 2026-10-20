# Result schema

Every smoothie-gate stage writes one `<stage>.result.json` file. The reporter reads only these files.

| Field | Type | Meaning |
| --- | --- | --- |
| `stage` | string | Stage name: `build`, `size`, `simulator`, `ai-review`. |
| `status` | string | `pass`, `warn`, `fail`, `skipped` (could not run, for example the model was unavailable) or `error` (the stage itself broke). |
| `title` | string | Short heading shown in the report. |
| `summary` | string | One line shown next to the status. |
| `details` | string | Markdown shown in a collapsed section. May be empty. |
| `metrics` | object | Flat map of names to numbers or strings, for example `flash_delta_release`. |

No other fields are allowed. Validate files with:

```sh
python3 -I scripts/gate_result.py build.result.json size.result.json
```

The command prints one JSON line per problem and per file, and exits 1 if any file is invalid. Only `fail` fails the gate run; `warn`, `skipped` and `error` are shown to maintainers.
