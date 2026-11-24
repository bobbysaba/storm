# For AI agents

If you're helping a team connect its own data to STORM (weather stations,
map layers, soundings, its own archive), start with
[docs/agent-setup.md](docs/agent-setup.md). It has a questionnaire to work
through with the person, and says when to write `[[sources]]` tables in
their `storm.toml` and when to write a code provider.

Setup references are in [docs/](docs/). The app's user guide is
[STORM_USER_GUIDE.md](STORM_USER_GUIDE.md).

Tests: `python -m pytest -q` in the `storm` conda environment
(`envs/storm.yml`).
