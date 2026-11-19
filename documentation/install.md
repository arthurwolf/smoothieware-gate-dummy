# Install

smoothie-gate is Python 3 (standard library only) plus two sibling checkouts.

1. Clone this repository and [smoothie-sim](https://github.com/arthurwolf/smoothie-sim) side by side, so that `../smoothie-sim` exists. Another place works if you set `SMOOTHIE_SIM` or pass `--sim-dir`.
2. Have a Smoothieware git checkout (the repository you want to check).
3. For a local run you need `ni-build`, which sends the ARM and simulator compiles to a remote build machine. The GitHub workflows do not need it: they run plain `make` on the runner.
4. Optional: put `bin/` on your `PATH` to get the `smoothie-gate` command.

Check the install with `python3 -m unittest discover -s tests`; all tests should pass.
