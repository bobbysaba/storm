# For AI agents

If you're helping a team connect its own data to STORM (weather stations,
map layers, soundings, its own archive), start with
[the agent setup guide](https://bobbysaba.com/docs/storm/extend/agent-setup/). It has a questionnaire to work
through with the person, and says when to write `[[sources]]` tables in
their `storm.toml` and when to write a code provider.

The full documentation, including the setup references and the user
guide, is at <https://bobbysaba.com/docs/storm/>.

Tests: `python -m pytest -q` in the `storm` conda environment
(`envs/storm.yml`).
