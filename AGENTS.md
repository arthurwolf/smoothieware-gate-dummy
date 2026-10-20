# smoothie-sim

Keep the published example report up to date. `README.md` links to a live example at https://smoothie-sim-example.pages.dev/report.html (Cloudflare Pages project `smoothie-sim-example`), generated from the `pressure-advance` branch (`~/dev/smoothieware/pressure-advance/branch/Smoothieware`) compared with upstream `edge` (what plain `make` clones into `fw/`), so the collapsed "already in the reference" sections show.

Whenever the checkers, the report (`checkers/report.py`), the rules (`rules.json`), the simulator or the output format change in a way a reader of the report would see, regenerate and republish it in the same piece of work:

1. In a scratch folder (rules in `~/.Codex/AGENTS.md`): build the reference with `make -j"$(nproc)" BUILD=<scratch>/b` (clones `edge` into `fw/` the first time; `git -C fw pull` to refresh), build the subject with `make -j"$(nproc)" SMOOTHIE_SRC=<pressure-advance tree> BUILD=<scratch>/pa`, then `BASE_SIM=<scratch>/b/smoothie-sim SUBJECT="Smoothieware pressure-advance branch, compared with upstream edge" ./run_checks.sh <scratch>/pa/smoothie-sim <pressure-advance tree> fw <scratch>/out`.
2. Publish `report.html` as `index.html` and `report.html` with `wrangler pages deploy` (project `smoothie-sim-example`, token `CLOUDFLARE_API_TOKEN` from `~/.Codex/docs/api-keys.md`; never print it).
3. Check the live URL, and update the README screenshot (`assets/report-example.png`) if the report layout changed.
