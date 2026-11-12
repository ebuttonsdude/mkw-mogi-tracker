---
title: 'Data and backups reference'
weight: 50
---

# Data and backups

[Home](../../README.md) · [User guide](user-guide.md) · [OBS](obs.md) · [FAQ](faq.md) · [Troubleshooting](troubleshooting.md)

## Local storage and backups

The server defaults to `127.0.0.1`. Runtime screenshots, names, scores, and recording references are stored on the server, with no cloud processing unless an external vision endpoint is explicitly configured and selected. That reader sends name crops only. An explicit network binding makes dashboard data and controls available to every client that can reach the port; there is no login or built-in HTTPS. See [network access](user-guide.md#access-from-another-device) before enabling it. Back up the data directory (excluding private `external-vision.json`) while the app is stopped, including `tracker.sqlite3`, `capture.json`, `imports.json`, `settings.json`, `evidence/`, and any `uploads/` videos. Local-path imports reference the original video instead of copying it, so back that up separately. The optional `obs-connection.json` backup contains a plaintext password; protect it like a secret. `.venv/`, `.local/`, source videos and the credential file are ignored by Git. Only explicitly curated still-image fixtures belong in the repository.

## Credential-free session archives

[Settings → Session backups](../data-and-backups.md) exports selected sessions and referenced PNG evidence across all modes. It uses a versioned JSON/PNG ZIP allowlist, not a database or directory copy. Preview and explicit confirmation add fresh session identities without replacing existing sessions; saved connections, preferences, models and videos are excluded. Full folder copies described above remain private because they can contain credentials.

## Credentials

The optional `DATA_DIR/obs-connection.json` stores OBS connection details separately from public settings, with owner-only `0600` permissions. Its password is plaintext on disk and never returned by the public API. Leave the UI password blank to reuse it, or select **Forget saved password** to remove it. Protect backups containing this file.

The optional `DATA_DIR/external-vision.json` stores external vision configuration and its plaintext API key using owner-only permissions (protected owner DACL on Windows). Public APIs omit the key. **Forget saved API key / revoke consent** clears it and invalidates existing readers; URL/model edits do not carry a saved key to the new destination. Exclude this file and interrupted `.external-vision-*.tmp` writes from exports, shared backups and logs. Re-enter the configuration after restoring.

## Deleting videos or sessions

[Upload cleanup](user-guide.md#clean-up-uploaded-videos) deletes only app-managed uploaded copies after confirmation, not original videos imported by path. [Session/race deletion](user-guide.md#sessions-and-history) changes saved scoring data and cannot be undone in the dashboard. It does not delete original recordings. Keep backups before destructive changes.

Uploaded course pictures are stored in `course-images.json` in the shared tracker data directory. They are used across Mogi Teams, FFA and WW, excluded from session ZIPs and application packages, and included when copying the whole data directory.
