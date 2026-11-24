# Contributing to STORM

Thanks for helping. STORM is used in the field, so changes are judged first
on whether they keep working on a laptop with a weak connection.

## Getting set up

```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
python setup.py                  # builds the `storm` conda environment
conda activate storm
python -m pytest -q              # about 30 seconds
```

`cp storm.example.toml storm.toml` gives you a working setup without a
broker (VIEWER and ARCHIVE launch). [docs/configuration.md](docs/configuration.md)
has every setting.

## Making a change

- Keep the generic app generic. Nothing outside `providers/nssl/` (and its
  tests) imports NSSL's package, and STORM must run with it missing
  (`tests/test_provider_boundary.py` checks both); STORM reaches providers
  only through the hooks in `settings/code_providers.py`. Anything that only
  makes sense for one team goes in that team's profile, `[[sources]]` tables
  or code provider ([docs/providers.md](docs/providers.md)).
- Update the docs in the same change: [STORM_USER_GUIDE.md](STORM_USER_GUIDE.md)
  for anything a user sees, and the reference in [docs/](docs/) for settings,
  topics and file formats.
- Add or update tests under `tests/`. The suite runs in CI on macOS, Linux
  and Windows.
- Never commit secrets: keys and passwords go in `.env`, certificates in
  `certs/` (both git-ignored). The NSSL viewer certificate in
  `providers/nssl/certs/viewer/` is read-only and public on purpose.
- Add a line to [CHANGELOG.md](CHANGELOG.md) for anything user-visible.

## Adding your team's data

Most data doesn't need a code change: see [docs/data-sources.md](docs/data-sources.md).
If you use an AI coding agent, point it at [AGENTS.md](AGENTS.md).

## Reporting problems

Open an issue with what you did, what you expected and what happened. The
error log (*View > Error Log*, or `storm_errors.log`) helps a lot. For
security problems, see [SECURITY.md](SECURITY.md) instead.
