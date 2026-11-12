---
title: 'FAQ reference'
weight: 50
---

# FAQ

[Home](../../README.md) · [User guide](user-guide.md) · [Limitations](limitations.md) · [Troubleshooting](troubleshooting.md)

## Do I need OBS or a GPU?

Neither is needed for manual scoring. OBS is an option for live screenshots and recording timing; camera input and local recording imports are also supported. GPU name recognition is optional. See [requirements](../getting-started.md#before-you-start) and [name readers](recognition.md#name-readers).

## How automatic is scoring?

The tracker detects finishing tables, reads player names, and calculates placement points. In normal use, you just check the detected result, correct anything it missed, and confirm it. You do not need to enter each race by hand.

Results stay pending until you confirm all 12 distinct player identities in finishing order. The game's cumulative score is never the scoring source. [Corrections](user-guide.md#correct-a-course-view-evidence-or-add-penalties) replace a race's contribution instead of counting it again.

## What happens when a player disconnects?

Points are based on finishing positions, so the placement points remain correct when a player disconnects. You may need to select the disconnected player's identity manually in the results review if their name is missing or misread. Confirm the recorded finishing row against the evidence and use the existing player rather than creating a duplicate. See [player disconnects](user-guide.md#player-disconnects).

## How are points and gaps calculated?

Places 1 through 12 earn `15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1`, totaling 82 per race. Twelve complete races total 984 gross room points. Penalties reduce net player/team totals, not finishing positions or gross room progress. Totals are not capped at twelve races.

In PNG reports and the overlay, `-x / -y` means points behind the immediately higher team and points behind first. For totals of 100, 90, and 85, third place shows `-5 / -15`. Ties use competition ranks. Movement arrows compare current net standings with standings before the latest first-confirmed race, using the current roster. The first race has no arrow.

## Can I use teams other than 2v2?

Yes. Assign the 12 players to your teams under Standings. Reports support other team sizes and unassigned players. Team totals always use current roster assignments, not historical membership snapshots. The first confirmed captured roster in an untouched session can create editable first-character teams. See [reviewing results](user-guide.md#review-captured-results).

## Can I reuse players in a new session?

Each history folder is an independent session with its own roster and results. Create a new session for a fresh room; load an old folder to restore its roster and scores. Races do not move between sessions. See [sessions and history](user-guide.md#sessions-and-history).

## Does a Lounge ID submit my results?

No. It labels the PNG and links to the corresponding MKCentral table. It does not read, edit, or submit an external Lounge table.

## Does it upload my recordings or use cloud OCR?

Recognition runs locally. Browser uploads copy a recording into the tracker server's data directory, not to a cloud service. With a remote browser, that copy travels over your network connection. Importing by a path on the server avoids that copy. Setup scripts download dependencies and optional model weights. See [data and backups](data-and-backups.md).

## Can I import chapters directly into DaVinci Resolve?

The app exports a timestamp/course text sidecar. It does not embed markers in recordings or create Resolve timeline markers automatically. Use the timestamps as references and adjust them after editing the video. See [chapters](user-guide.md#automatic-timestamp-creation).
