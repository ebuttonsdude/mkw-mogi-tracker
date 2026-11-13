---
title: "Install the tracker"
weight: 10
---

# Install the tracker

**Goal:** open the tracker in your browser, ready for your first session.

Follow only the section for your operating system. Run one command block at a time. If a command fails, stop there and use [Troubleshooting](troubleshooting.md).

## Before you start

- You do **not** need a GPU. A CPU reader is available.
- For live game capture, install [OBS Studio](https://obsproject.com/download) and make sure your game appears in OBS. Recordings can be imported without OBS.
- Setup asks you to choose a name reader. It is **optional, but highly recommended** for mogi capture.

## Choose your operating system

- [Linux](install/linux.md) — Arch, Fedora, Ubuntu
- [Windows](install/windows.md)
- [WSL on Windows](install/wsl.md)

## Updating

Stop the tracker and [back up your data](data-and-backups.md) first. For a Git
installation made with the commands in these guides, `origin` is the
[GitHub repository](https://github.com/ebuttonsdude/mkw-mogi-tracker).
The clone command selects `main` and sets up tracking automatically.
Open a terminal in your tracker folder and run:

```sh
git pull --ff-only
```

This updates from the configured `origin` without rewriting local changes.
If Git refuses a fast-forward or reports local changes, stop and keep your
existing folder and backups; do not reset, rebase, or delete files to force the
update. Rerun guided setup if the update changes dependencies, then start normally.

For a ZIP installation, download the latest
[GitHub source ZIP](https://github.com/ebuttonsdude/mkw-mogi-tracker/archive/refs/heads/main.zip).
Keep your `.local` data and `.venv` environment intact when replacing source
files. See the [Windows update steps](install/windows.md#updating-and-keeping-your-scores-safe)
for the full ZIP procedure.
