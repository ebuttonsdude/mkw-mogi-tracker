---
title: 'Limitations reference'
weight: 50
---

# Limitations

[Home](../../README.md) · [User guide](user-guide.md) · [Recognition](recognition.md) · [Troubleshooting](troubleshooting.md)

## Platform and scope

- Arch Linux is the main tested platform. Windows 10 and Windows 11 are tested occasionally. The maintainer has also validated WSL2 mirrored networking with Windows OBS and NVIDIA NF4. WSL NVFP4 remains unvalidated, and Fedora/standalone Ubuntu have not been fully tested. See [platform guides](../install/_index.md).
- The service binds to loopback by default. Remote dashboard and overlay access require an explicit network binding and trusted-network restrictions; there is no login or built-in HTTPS. Read the [network access warnings](user-guide.md#access-from-another-device). Public hosting and cloud processing remain outside its scope.
- This tracks post-race placements, not live in-race positions or the game's cumulative score.
- A scored race requires exactly 12 distinct player identities. Other room sizes are not supported for scoring.

## Recognition is review-only

- Recognition expects an unobstructed, uncropped 16:9 game image and the supported neutral or in-game team-colored 12-player layouts. Testing includes 2560×1440 samples, not every resolution or layout.
- Full-table recognition requires all 12 ranks and finishing-phase `+points` evidence. Partial manual review requires at least three coherent rank/`+points` anchors and independently proven positions; missing rows remain blank. Sliding tables may still be too incomplete or ambiguous, and partial team panels with exclusively dark ranks may be rejected. Consumed point animations, cumulative standings, team summaries and gameplay are not accepted as finishing order.
- The finishing window can be short. The live 250 ms target is a minimum serial cycle interval, not guaranteed four-frame-per-second capture. Screenshot acquisition, OCR, and system load can make a cycle longer. Offline imports default to 0.5-second sampling and can also miss results.
- Names, Japanese text, and similar-looking symbols can be wrong. A generative name reader has no calibrated confidence score. Confirm identity choices against evidence; approximate OCR matches are never silently merged.
- Course recognition uses the supplied 40-course vocabulary. Ambiguous reads stay unresolved and course names remain editable.
- A suspected-results screenshot is unverified evidence only. It does not create placements, review candidates, or scores. It may show a partial table or cumulative standings. Unsampled frames cannot be recovered by this fallback.

See [recognition](recognition.md) for supported frames, model setup, and sampling behavior.

## Timing and chapters

Camera input supplies images, not recording timestamps. Enable the OBS recording clock and start capture before recording for automatic recording-relative markers. Connecting midway, losing the clock, or changing recording files can leave offsets unknown. OBS pauses do not advance the chapter clock; automatic file splits require manual file-local verification.

Intro markers are sampled estimates, not frame-accurate edits. TXT omits unknown offsets. EDL requires a timestamp for every linked race, and targets an exact 60 fps non-drop-frame Resolve timeline starting at 01:00:00:00. Neither export embeds markers in a recording.

## Sessions, history, and evidence

Sessions have independent rosters and scores. Team totals, movement, and reports use current team assignments, not historical membership snapshots. Pending races contribute no points and stop a report's cumulative line until confirmed. Deleting a player does not undo that player's recorded placements.

New captures preserve linked pre-race and result evidence. Older confirmed races without a saved screenshot mapping cannot reliably recover it. Manual races have no captured evidence. Session/race deletion cannot be undone through the dashboard; back up data first.

## Capture resources and storage

Only one import runs at a time, and live capture cannot overlap an import. Stop capture and wait for resources to close before switching jobs. Failed camera release requires restarting the tracker rather than risking overlapping streams.

Browser uploads copy the video to the tracker server, need space for that copy, and are limited to 64 GiB. Import by a path on that server to avoid copying large recordings. Cleanup can remove confirmed app-managed uploads, never originals imported by path.

GPU recognition needs a separate environment and substantial model storage/free GPU memory. It is optional; performance depends on the machine and system load. See [requirements](../getting-started.md#before-you-start).

## Reports and OBS outputs

PNG generation needs Pillow and a Unicode-capable local font for player names. Oversized folders return an error rather than silently omitting content. Labels and player names use the Unicode font.

The browser overlay needs the tracker running at a URL reachable by OBS. The default binding requires OBS on the same machine; explicit network access allows a remote browser source but grants access to the rest of the app as well. OBS WebSocket capture and recording timing still require OBS on the tracker server. It retains last-good standings on connection failure, so a visible score is not proof the connection is current. Browser acceptance tests do not validate real OBS/CEF rendering. The Lounge ID only labels/links a table; it does not submit results.
