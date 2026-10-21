<h1 align="center">smoothie-gate</h1>
<p align="center"><strong>Someone opened a Smoothieware pull request and you need to know what it costs before you merge it?</strong><br>An automatic check that builds the PR, measures flash and RAM, reviews the code with AI and replays G-code through the simulator.</p>
<p align="center"><sub>GitHub Actions · C++ firmware · Python · GPL-3.0</sub></p>
<p align="center"><a href="#status">Status</a> · <a href="#what-the-gate-checks">Checks</a> · <a href="#how-it-reports">Report</a> · <a href="#setup">Setup</a> · <a href="#limits">Limits</a> · <a href="CLAUDE.md">Contributor notes</a></p>

> This README is the short version. Full documentation: [documentation/](documentation/README.md).

smoothie-gate runs on every new or updated [Smoothieware](https://github.com/Smoothieware/Smoothieware) pull request. It builds the PR, compares its size with the base branch, has an AI model review the diff, runs the firmware through [smoothie-sim](../smoothie-sim) and posts one comment with the results. It informs; maintainers decide. A check that finds something warns, and only a build failure or a clear rule violation fails the run.

## Status

The four checks, the reporter, the local `smoothie-gate run` command and both GitHub workflows are built and have run: locally against a test Smoothieware repository and on GitHub in a private test repository (same-repository pull requests; a real fork pull request is untested). Not adopted in the Smoothieware repository yet: see [adoption](documentation/adoption.md). Known limits: [limits](documentation/limits.md).

## What the gate checks

| Check | What it does | Fails the run? |
| --- | --- | --- |
| **Build** | Compile the PR with the standard ARM toolchain. | Yes, if it does not compile. |
| **Size** | Build the PR and its base twice, optimized (default) and unoptimized (debugging). Report flash and RAM totals and the signed byte difference for both. Small growth is acceptable; the report shows the symbols that grew. | No. Informs. |
| **AI review** | Send the diff and the touched files to a language model with two prompts: Smoothie's developer rules (no blocking waits, no heap allocation in interrupts, and so on) and general code problems (bugs, memory leaks, undefined behaviour, missed error paths). | Only on a clear rule violation. |
| **Simulator** | Run the firmware through smoothie-sim with a set of G-code programs, check that it executes as expected and compare the generated steps with a stored reference. | Yes, on a step mismatch not marked as intended. |

### Simulator coverage

The reference set is new and written for the gate. It covers different machines (Cartesian, delta, CoreXY, SCARA, rotary axes) and different uses (3D printing, milling, laser, tool changes, homing, probing, arcs, feed-rate changes, emergency stops). Each case is a G-code file, a machine configuration and a recorded step trace. An intended change in behaviour updates the trace in the same PR, with a reviewer sign-off.

### What it reuses

smoothie-sim already builds the real Smoothieware core for the PC, checks planner blocks and step timing, finds blocking waits, compares RAM and flash, and has a manual GitHub Actions workflow with a PR comment option. smoothie-gate calls those pieces instead of reimplementing them. See the [smoothie-sim README](../smoothie-sim/README.md).

## How it reports

One comment per PR, edited in place on every push, not a new comment each time. Each check gets a status line (pass, warn, fail), then details under a collapsed section. The full report is attached to the run as an artifact. The size table looks like this:

| Build | Flash (bytes) | RAM (bytes) |
| --- | --- | --- |
| Optimized, base | measured | measured |
| Optimized, PR | measured | measured |
| Change | `+N` or `-N` | `+N` or `-N` |

The same table is produced for the unoptimized build.

## Setup

Local use: [install](documentation/install.md) and [local run](documentation/local-run.md). On GitHub: [adoption](documentation/adoption.md). The gate will be one workflow file in the Smoothieware repository. It needs:

- Permission to comment on pull requests.
- Access to an OpenAI-compatible AI endpoint. GitHub Models, the first plan, was retired on 2026-07-30; the default is the Kilo Gateway free tier (see [AI review](documentation/ai-review.md)).
- A trigger on `pull_request_target` so that PRs from forks can be commented on. The workflow checks out PR code but never runs it with access to secrets. This design needs a security review before use.

## Limits

- The AI review gives advice and makes mistakes. A clean review does not mean the code is correct, and a flagged line is a question for a human, not a verdict.
- The simulator does not model SD, USB, network, spindle or panel hardware, and its step timing is approximate. See the [smoothie-sim limits](../smoothie-sim/documentation/limits.md).
- Size numbers are only comparable when base and PR use the same toolchain version and build settings.
- The diff and file contents are sent to a third-party model service. Do not run the gate on private code without checking where it goes.

## Project, help, and licence

Report problems in this repository's issue tracker with the PR link and the run log. Documentation: [documentation/](documentation/README.md). Contributor and agent notes: [CLAUDE.md](CLAUDE.md). Changes: [CHANGELOG.md](CHANGELOG.md).

smoothie-gate is [GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html), matching Smoothieware and smoothie-sim. A `LICENSE` file is not in this folder yet.
