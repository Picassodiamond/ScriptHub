# Contributing

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate                 # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver --noreload  # or: python manage.py desktop
```

Requirements: Python 3.10+ and Django 5. Nothing else — no database, no Node, no
build step. Please keep it that way: new runtime dependencies need a very good reason,
because the app is meant to run on locked-down machines.

## Tests

```bash
python manage.py test runner     # the whole suite (a few tests are skipped on Windows)
node tools/test_highlight.js     # syntax highlighter (only if you touched static/js/highlight.js)
```

* Add tests with every change; they run on Windows and Linux in CI.
* The suite must leave ScriptHub's data folder (`runtime/executions/`, `runtime/presets/`,
  `runtime/sequences/`) untouched and change nothing under `sample_scripts/` or `sample_projects/`.
* After changing a sample script, run `python tools/build_sample_projects.py`.
* Write script fixtures with `write_bytes`, not `write_text` (avoids CRLF on Windows),
  and give every started run its own submit token.
* SSH is tested against a fake `ssh` (see `FakeSshRemoteTests`), never a real host.

## Code guidelines

* Read `CLAUDE.md` for the architecture and the rules that matter (no shell, ids instead
  of paths, atomic writes through `runner/fsutil.py`, features that can be switched off
  must return 403 when off).
* Vanilla JavaScript and plain CSS (`static/css/workbench.css`); no frameworks.
* Keep user-facing text short and plain.

## Pull requests

1. One topic per pull request, with a short description of what and why.
2. Tests pass locally.
3. Update `README.md` when behaviour or settings change.

By contributing you agree that your contribution is licensed under the MIT licence of
this project (see `LICENSE`).

## Security issues

Please report them privately — see `SECURITY.md`.
