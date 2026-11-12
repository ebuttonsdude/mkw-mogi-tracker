---
title: "Back up your data"
weight: 70
---

# Back up your data

Your sessions are saved on your computer in **`mkw-mogi-tracker/.local/tracker`** by default. The **`.local`** folder is hidden; enable **Show hidden files and folders** in your file explorer to see it.

## Download a session backup

1. Stop capture and finish or cancel video imports/uploads.
2. Open **Settings**, expand **Session backups**, then select **Choose sessions**.
3. Select the sessions you want, across **Mogi Teams**, **FFA** and **Worldwides**.
4. Click **Download selected sessions** and keep the ZIP somewhere safe.

The archive includes the selected sessions’ roster, results, corrections, penalties, recording references, chapter offsets and saved race evidence. It excludes OBS passwords, external model API keys, private connections, API tokens, display settings, themes, course pictures, fonts, model files, video files and unfinished capture queues. It never copies the whole data folder.

**A credential-free backup is not anonymous.** Player names, session text and screenshot evidence remain in the archive. Keep private information out of those fields before sharing. Archives are not encrypted.

## Restore sessions

1. Stop capture and imports/uploads.
2. In **Settings**, expand **Session backups**, choose the ZIP and click **Preview import**.
3. Check the mode, session names, race counts and evidence count.
4. Click **Confirm import as new sessions**.
5. Switch to the matching mode and load the imported folder in **Race history**.

Existing sessions are **never replaced** by a web restore. Even matching names are added as independent copies. Importing the same backup twice creates two copies. Failed validation or import leaves existing sessions intact. The preview expires after ten minutes or when another archive is previewed.

## Archive format and limits

The ZIP contains **`manifest.json`** with format `mkw-session-backup`, version **1**, and a `modes` object keyed by `mogi`, `ffa` and/or `ww`. Each mode contains explicitly allowed session tables: folders, sessions, teams, players, recordings, races, result revisions, placements, penalties and race evidence (including automatic-confirmation provenance when available); Worldwides also includes player names and VR revisions. Identities and relationships are preserved within the document and receive fresh IDs on import.

Referenced PNGs live at `evidence/<mode>/<UUID>.png`. Nothing else is allowed. Import validates the table schema, relationships, twelve-player scored results, WW values and PNG files; it rejects unsupported versions, unexpected files, symbolic links and unsafe paths.

Older version-1 archives without automatic-confirmation provenance are read as ordinary confirmed results. Archives with that provenance require a tracker version supporting it.

Limits: **1,000 sessions** per export, **128 MiB** compressed and expanded archive, **16 MiB** manifest, **16 MiB** per still PNG, and **4,096** archive members. ZIP uploads have a two-minute deadline. If an export is too large, choose fewer sessions. Back up original recordings separately.

## Full local folder copies

For settings, custom themes, course pictures, fonts and uploaded videos as well as sessions, close the tracker with **Ctrl+C**, then copy its whole data directory. Use your custom directory instead if you started with `--data-dir`. Do not run two tracker instances against the same folder.

**A full folder copy may contain your saved OBS password in `obs-connection.json` and an external model API key in `external-vision.json`.** Exclude private connection files from copies you share and re-enter credentials after restoring. Keep it private; do not attach it to an issue or share it publicly. To restore a folder copy, keep the tracker closed and preserve a separate copy of your current data before replacing anything. Original recordings imported by path are not copied into the tracker.

## What does delete mean?

- **Delete race/folder:** removes saved scoring records. There is no dashboard undo.
- **Video library cleanup:** removes app-managed uploaded copies after confirmation.
- **Original video imported by path:** is not deleted by video cleanup.

[Move from mkw-livesplit-vos](migrator.md) · [Storage and credential reference](technical/data-and-backups.md)
