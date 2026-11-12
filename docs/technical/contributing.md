---
title: 'Contributing reference'
weight: 50
---

# Contributing

[Back to the README](../../README.md)

Read the [design requirements](../../DESIGN.md) before changing UI behavior and the [project instructions](../../AGENTS.md) for scoring, data safety, and verification constraints. Keep changes small and preserve existing patterns. Add dependencies only with explicit approval.

## Repository layout

- [`mkw_tracker/`](../../mkw_tracker/): local HTTP service, SQLite scoring, recognition, capture/import adapters, and report generation.
- [`mkw_tracker/static/`](../../mkw_tracker/static/): dashboard, transparent OBS overlay, and neutral bundled UI assets.
- [`tests/`](../../tests/): unit/API tests, optional browser checks, and compact image fixtures.
- [`tools/`](../../tools/): project-local dependency/model setup, replay tools, and recognition benchmarks.
- [`DESIGN.md`](../../DESIGN.md): UI and interaction requirements.
- [`AGENTS.md`](../../AGENTS.md): project constraints and verification guidance for coding agents.

## Making changes

Contributions are made under the project's [AGPLv3 grant and limited NVIDIA
reader permission](../../COPYING.md). Preserve third-party notices; see the
[licensing guide](../licensing.md) before distributing a fork or installer.
For a modified hosted version, update the dashboard license/source page and
website source links to offer the actual corresponding source of your version.

Add regression coverage for changed behavior. For cleanup or refactoring, write a brief plan and add regression coverage before changing uncovered behavior. Update the README and relevant documentation when behavior changes.

Preserve the scoring and review boundary. A scored race requires exactly 12 distinct players; captured results remain pending until explicitly confirmed. Corrections replace the race's contribution rather than adding it again. Keep player IDs stable and preserve Unicode names.

Use temporary databases, mocked capture/clipboard adapters, and read-only source recordings for tests. Do not restart or reconfigure a running tracker, OBS, camera, GPU recognizer, or real database. Camera discovery must not start a stream. Do not delete originals, evidence, sessions, or scored results as a test shortcut.

Keep loopback as the default HTTP bind address and preserve configured-host/same-origin checks and safe text rendering. Explicit network binding is supported, but it does not add authentication or TLS. Keep OBS WebSocket destinations loopback-only. Never expose OBS passwords through public APIs, logs, or commits. Keep private connection persistence separate from public settings.

## Validation

Run commands from the repository root after [setting up local capture/report dependencies](installation.md#add-capture-and-reports). Tests involving process termination, process groups, fake PIDs, or signal failure paths must run inside a verified separate PID namespace/container/VM. A virtualenv or terminal alone is not isolation. Follow [AGENTS.md](../../AGENTS.md) before running tests:

```sh
.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
node --check mkw_tracker/static/app.js
node --check mkw_tracker/static/overlay.js
node --check mkw_tracker/static/ww.js
uvx ruff check mkw_tracker tests tools
.venv/bin/python -m compileall -q mkw_tracker tests tools
```

Manual-scoring tests use only the standard library. The full suite requires capture/report dependencies; recognition checks also use local OCR data and the bundled [still-image fixtures](../../tests/fixtures/). Tests referencing original recordings skip when those files are absent. Full video files are deliberately not test dependencies checked into Git.

### Browser checks

Optional Chromium acceptance checks cover the dashboard and transparent OBS overlay. Run both when changing the UI. Install Playwright into a local environment with Pillow and the capture dependencies, then run:

```sh
.venv/bin/python -m pip install playwright
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_smoke
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_overlay
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_ww
PLAYWRIGHT_BROWSERS_PATH=.local/browsers .venv/bin/python -m tests.browser_ffa
```

If the environment was created without pip, use `uv pip install --python .venv/bin/python playwright` for the first command. Use another local Python environment if that is where the browser dependencies are installed. Browser tests use temporary databases and mocked capture/clipboard boundaries; they do not start the real camera or overwrite your clipboard.

Mocked browser and OBS protocol checks do not prove live OBS/CEF or capture behavior. Report those validation gaps explicitly.

### Packaging

Build wheel and source packages with `uv build` when changing bundled assets or packaging rules. The app's `capture` and `reports` extras are declared in [`pyproject.toml`](../../pyproject.toml) for package installation.

Run `.venv/bin/python tools/check_package.py dist` after building into a directory containing exactly one wheel and one source archive. It checks bundled assets and README-linked guides, rejects runtime data and video signatures, and installs the wheel into a temporary environment without optional dependencies. The smoke test runs outside the checkout.

## Recordings, fixtures, and private data

Keep recordings outside Git. [`.gitignore`](../../.gitignore) excludes `video-samples/`, uploaded copies, and common video extensions in any directory, including uppercase/mixed-case extensions. Do not commit full recordings, even renamed ones, or bypass exclusions with `git add -f` or Git LFS.

Keep small, intentional still-image regression fixtures and their provenance in [`tests/fixtures/`](../../tests/fixtures/). Do not add game artwork or gameplay screenshots; use synthetic images and preserve source attribution.

Do not commit local environments, model weights, browser downloads, databases, credentials, generated standings, build outputs, or agent runtime state. `.local/` is runtime data, not source.

Before committing, inspect staged paths and sizes:

```sh
git diff --cached --stat
git diff --cached --name-only
```

Check unexpected files with `git check-ignore -v -- path/to/file` and suspected binaries with `file --mime-type path/to/file`. Ignore rules do not remove files that are already tracked. Use `git rm --cached -- path/to/video` to stop tracking one without deleting your local copy. That does not remove it from older commits; coordinate any necessary history cleanup before pushing rather than rewriting published history without agreement.

## Reporting a change

Include what changed, the checks you ran, their results, and any remaining gaps. For recognition issues, describe the input and selected name reader without sharing credentials or committing a recording. You may include a **link to a recording** in an issue, merge request, or discussion. Never upload the full recording to Git. A small still-image fixture with provenance is preferable when it reproduces the issue.

## Reusable CI tools

GitLab's **toolchain** job prepares a reusable Docker image. It contains the Python environment, Chromium and its system dependencies, OCR test data, and checksum-verified documentation archives. Later jobs use that exact image in fresh containers with fresh tracked source. Normal jobs reuse the image instead of reinstalling tools.

Only the recipe and setup scripts belong in Git—not browsers, downloaded archives, environments or image layers. The cache lives in the runner's Docker storage, outside the checkout. Cleaning the checkout does not remove it. A new runner or a removed image needs a cold build; changing the declared toolchain inputs selects a new image automatically.

For a deliberate clean toolchain refresh, run a pipeline with **`CI_TOOLCHAIN_REBUILD=1`**. This rebuilds the project's toolchain with refreshed base/dependencies; it does not prune other images or stop other containers. Refresh periodically for upstream security updates. Warm reuse reduces setup time, not test execution time.

Tests still run in separate container PID namespaces, without mounting the host checkout, credentials, Docker socket or devices. A limited runner account alone is not a substitute for process isolation. Build/package outputs remain ordinary job artifacts; the toolchain is not published as an application download.
