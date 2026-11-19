<h1 align="center">smoothie-gate</h1>
<p align="center"><strong>Someone opened a Smoothieware pull request and you need to know what it costs before you merge it?</strong><br>An automatic check that builds the PR, measures flash and RAM, reviews the code with AI and replays G-code through the simulator.</p>
<p align="center"><sub>GitHub Actions · C++ firmware · Python · GPL-3.0</sub></p>
<p align="center"><a href="#status">Status</a> · <a href="#what-the-gate-checks">Checks</a> · <a href="#how-it-reports">Report</a> · <a href="#setup">Setup</a> · <a href="#limits">Limits</a> · <a href="CLAUDE.md">Contributor notes</a></p>

> This README is the short version. Full documentation: [documentation/](documentation/README.md).

smoothie-gate checks [Smoothieware](https://github.com/Smoothieware/Smoothieware) changes. It builds the firmware, compares its size with the base revision, reviews the diff and replays motion through [smoothie-sim](../smoothie-sim). The local command produces a report; the GitHub workflows are under development. Maintainers decide what to do with the results.

## Status

The V1 local gate has run with four builds and 26 simulator cases. V2 Prime support is experimental: four real firmware builds and size comparisons have run using an isolated compiler dispatcher, but the standard dispatcher remains unintegrated; the three previously pending V2 simulator references now have scripted independent timing reviews, but the full V2 gate has not been rerun since. An earlier GitHub workflow was tested with same-repository pull requests; the current workflows and real forks remain unverified. The workflows are not ready for untrusted contributions because build commands can tamper with job results. See [adoption](documentation/adoption.md) and [limits](documentation/limits.md).

## What the gate checks

| Check | What it does | Fails the run? |
| --- | --- | --- |
| **Build** | Compile the PR with the standard ARM toolchain. | Yes, if it does not compile. |
| **Size** | Build the PR and its base twice, optimized (default) and unoptimized (debugging). Report flash and RAM totals and the signed byte difference for both. Small growth is acceptable; the report shows the symbols that grew. | No. Informs. |
| **AI review** | Send the diff and the touched files to a language model with two prompts: Smoothie's developer rules (no blocking waits, no heap allocation in interrupts, and so on) and general code problems (bugs, memory leaks, undefined behaviour, missed error paths). | Only on a clear rule violation. |
| **Simulator** | Run the firmware through smoothie-sim with a set of G-code programs, check that it executes as expected and compare the generated steps with a stored reference. | Yes, on a step mismatch not marked as intended. |

### Simulator coverage

The V1 reference set covers Cartesian, delta, CoreXY and SCARA machines and uses including printing, milling, laser motion, tool changes, homing, probing and arcs. Each case has a G-code file, machine configuration and recorded step trace. V2 has a separate case set; some references have been reviewed, while scenario coverage remains incomplete. Neither simulator validates physical hardware.

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

Local use: [install](documentation/install.md) and [local run](documentation/local-run.md). For the unfinished GitHub integration, see [adoption](documentation/adoption.md). It needs:

- Permission to comment on pull requests.
- Access to an OpenAI-compatible AI endpoint. GitHub Models, the first plan, was retired on 2026-07-30; the default is the Kilo Gateway free tier (see [AI review](documentation/ai-review.md)).
- Separate untrusted build and trusted reporting jobs, with an execution boundary that prevents pull request code from tampering with the results. The current workflows need further security work and a real fork test.

## Limits

- The AI review gives advice and makes mistakes. A clean review does not mean the code is correct, and a flagged line is a question for a human, not a verdict.
- The simulator does not model SD, USB, network, spindle or panel hardware, and its step timing is approximate. See the [smoothie-sim limits](../smoothie-sim/documentation/limits.md).
- Size numbers are only comparable when base and PR use the same toolchain version and build settings.
- The diff and file contents are sent to a third-party model service. Do not run the gate on private code without checking where it goes.

## Project, help, and licence

Report problems in this repository's issue tracker with the PR link and the run log. Documentation: [documentation/](documentation/README.md). Contributor and agent notes: [CLAUDE.md](CLAUDE.md). Changes: [CHANGELOG.md](CHANGELOG.md).

smoothie-gate is [GPL-3.0-or-later](LICENSE), matching Smoothieware and smoothie-sim.
