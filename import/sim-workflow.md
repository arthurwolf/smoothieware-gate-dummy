# GitHub Actions

`.github/workflows/sim.yml` runs the simulator and the checkers on demand (`workflow_dispatch`).

| Input | Default | Meaning |
| --- | --- | --- |
| `pr_number` | empty | Firmware pull request number. Empty uses `ref`. |
| `ref` | `edge` | Branch or commit of `Smoothieware/Smoothieware`. |
| `base_ref` | `edge` | Reference for the RAM, flash and new-wait comparisons. |
| `post_comment` | `false` | Post the short summary as a comment on the PR. |

The job clones the firmware, builds the simulator, builds base and candidate firmware for the RAM and flash comparison (`gcc-arm-none-eabi`), runs `run_checks.sh`, writes the job summary
and uploads the `simulator-report` artifact (`report.html` and the result JSON). Commenting on the PR needs the secret `FIRMWARE_PR_TOKEN` (a token with write access to the firmware repository);
without it the comment step uses `github.token`, which can only comment on this repository.

Inputs reach the shell through environment variables, never interpolated into the script.

Status: the workflow has not yet been run on GitHub. The steps it runs are the ones documented in [Getting started](getting-started.md).
