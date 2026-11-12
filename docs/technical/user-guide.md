---
title: 'User guide reference'
weight: 50
---

# User guide

[Home](../../README.md) · [User guide](user-guide.md) · [OBS](obs.md) · [FAQ](faq.md) · [Troubleshooting](troubleshooting.md)

Use this guide for scoring, sessions, video imports, reports, and chapters. See [OBS setup](obs.md) for live input and stream output, and [recognition](recognition.md) for name readers and capture behavior. For step-by-step live capture and video import walkthroughs, start with [Intended workflow](intended-workflow.md). Commands assume the repository root.

## On this page

- [Start the app](#start-the-app)
- [Access from another device](#access-from-another-device)
- [Import a video](#import-a-video-from-the-dashboard)
- [Review captured results](#review-captured-results)
- [Player disconnects](#player-disconnects)
- [Sessions and history](#sessions-and-history)
- [Corrections, evidence, and penalties](#correct-a-course-view-evidence-or-add-penalties)
- [Character themes](#character-themes)
- [PNG reports and Lounge ID](#folder-png-reports-and-lounge-id)
- [Clean up uploads](#clean-up-uploaded-videos)
- [Automatic timestamp creation](#automatic-timestamp-creation)

## Start the app

Complete the [installation guide](../getting-started.md) to create the project environment, then run from the repository root:

```sh
.venv/bin/python -m mkw_tracker
```

Open **http://127.0.0.1:8765**. Data is saved in `.local/tracker/`. Add players and teams, assign team membership, create a race, and enter its finishing order. The app requires 12 distinct players per scored race.

The tabbed navigation groups Standings, Capture & review, Videos, Race history, Chapters, and Settings. Only the selected view is shown; the browser remembers your tab. Use arrow keys or Home/End to move between tabs. Opening a race editor selects Race history automatically. Team standings show the gap to the immediately higher team and the room's confirmed points out of **984**, the maximum for twelve 82-point races. Room points include confirmed placements for deleted players; deleting a player does not undo their recorded race. Totals are not capped if you record more than twelve races.

For a temporary demonstration using the two manually verified sample results:

```sh
.venv/bin/python -m mkw_tracker --demo
```

Demo names include explicitly marked spelling hints. Demo data is discarded when the server stops. This mode does not run recognition.

## Access from another device

Keep the tracker bound to its default loopback address. It has no login or built-in HTTPS. Anyone who can reach its port can access dashboard data and controls, including session deletion, capture, and imports. The overlay is not a separate read-only security boundary.

Do not expose the tracker through router forwarding or public hosting. A private VPN or SSH tunnel still requires an explicit security design; it is not part of the beginner installation. Do not change host/origin checks or the loopback-only OBS connection policy as a workaround.

Saved OBS credentials have owner-only file protection and are not returned by the API, but a reachable dashboard client could still request capture using those credentials. Entering a password over unencrypted remote HTTP would expose it in transit.

Capture devices, import paths, saved data, and credentials belong to the computer running the tracker, not the browser. Browser uploads copy files to that computer. Keep the default local-only workflow unless a separately reviewed deployment provides an adequate trust boundary.

## Import a video from the dashboard

Open **Videos** and use **Import video**. If live capture is active, stop it in **Capture & review** and wait for shutdown first. Choose a destination folder and enter the video's path on the computer running the tracker, for example:

```text
/path/to/your/recording.mkv
```

Alternatively, choose a file with the browser picker. That copies the video into the data directory's `uploads/` folder, so the local-path option is preferable for large recordings. Browser uploads are limited to 64 GiB and require enough free disk space for the copy. Uploads stay on the tracker server until you remove them from the video library.

Select the name reader and sample interval, normally **Automatic** and **0.5 seconds**, then import. Progress, completion, errors, and **Cancel import** appear below the form. Captured results go into the normal review queue; imports never count points automatically. The import uses the destination folder's own session, even if you load or create another session while it runs. The UI labels the recommended sample interval **default 0.5**. This provides more opportunities to catch the roughly one-second finishing table seen in the sample; shorter sampling costs more processing and is not a guarantee. Cancelling retains already captured chapters and review cards. Restarting the application marks unfinished imports interrupted rather than silently resuming them.

Auto refresh updates import progress for both local paths and browser uploads, including while a form has focus, without replacing drafts or changing the page scroll position. Job cards show the engine selected by Automatic and any fallback. Video decoding overlaps recognition with one prefetched sample; recognition and saved results stay in timestamp order. The speed improvement depends on decoding and OCR costs, and uses no additional model instance.

Only one import runs at a time. Imports are rejected while live OBS capture is running, to avoid competing capture state and model memory usage. Use **Capture & review → Stop capture** and wait for shutdown to finish before importing a recording. Use the [installation guide](../getting-started.md) first if OCR dependencies/data are missing.

## Review captured results

Every captured result requires confirmation. Japanese names, special characters, and Latin names can still have OCR errors, including visually similar symbols. The review card shows the original screenshot, editable name candidates, and existing-player selectors. Correct names and select the same player identity across races. The app does not silently merge similar names.

Player identity lists are alphabetical. A unique exact name match, ignoring surrounding whitespace, is selected automatically. Other queued cards update after you enroll players from an earlier card. Editing a candidate name can also find its match. Explicit identity choices stay unchanged, and duplicate-name matches remain manual. This preselection does not bypass result confirmation.

For an untouched session, confirming the first captured roster also creates editable teams from the first character of each **reviewed** player name, ignoring case: `Alice` and `amy` join `A`, while `Bob` and `ben` join `B`. Symbols, numbers and Japanese characters are supported too: `☆Star` and `☆Moon` join `☆`, and `7Seven` joins `7`. Teams and players are not created from unconfirmed OCR. Existing manual setup, including previously deleted players or teams, disables this initial grouping. Later reviews do not regroup players. Rename teams or change assignments under Standings as usual.

Capture & review also shows the current pending race with its course thumbnail, timestamp status, evidence and chapter controls. When finishing scores arrive, that card is replaced by the standard identity review. Pending previews never add points. Confirming a review adds points. Dismissing it adds none. Results entered later for a race that began in another session remain in that session. Use the result editor to correct a scored race; revisions replace its contribution instead of adding points twice.

If a frame resembles a results table but fails strict detection, the tracker keeps the first **suspected results** screenshot for the current known pending race. It appears in **View evidence**, the manual result editor and the current-race card; History marks it **Suspected screenshot available**. At most one such fallback is kept per race, including across restarts, and a later valid results screenshot is shown first. The fallback never creates a race, review candidate, player, team or score, and never pre-fills finishing positions.

Suspected evidence can be partial or reordered cumulative standings. It is visibly marked **finishing order unverified**: verify the original recording before manually entering positions. Gameplay without sufficient table evidence is ignored. This cannot recover a frame that was never sampled, and it does not retroactively add evidence to earlier missed races.

Team totals are sums of their members' current-session points. Reassigning a player updates those totals immediately. Players retain earned points when absent from later races. Duplicate display names can represent different players; their internal identities are separate.

## Player disconnects

Scores come from finishing positions, not the game's cumulative totals, so a disconnect does not change the points awarded for each position. In the results review, you may need to manually select the disconnected player's existing identity for their recorded finishing row. Do not create a new player just because OCR read a disconnect label or missed the name.

Confirm the finishing order and all 12 distinct identities before counting the race. If the table is incomplete, use the original recording or other verified evidence to enter the missing information manually; do not guess the order.

## Sessions and history

Every history folder is a session with its own teams, players, races, penalties, and Lounge ID. **Create new session** starts an empty named session without modifying earlier ones. Expand an old folder and select **Load session** to restore that room's roster, scores, and OBS output. A held results screen is not scored again merely because you change sessions or restart. The old reset button has been removed.

If another tab changes the selected session while you enter a player, team, or manual race, submitting the old draft is rejected rather than saving it to the wrong folder. The draft stays intact. **Refresh** preserves its original session too. Load the original session to finish it, or reload the page and re-enter it for the newly selected session.

**Delete folder** permanently removes that session's database results, roster, and penalties after confirmation. You can delete a folder without loading it; the selected session stays unchanged. Deleting the selected session creates a fresh empty replacement. Deletion is blocked while capture, import, or upload is running. Original recording files, uploaded videos, and shared recording references are not deleted. Evidence files are retained.

**Delete race** removes that race and all its result revisions, deductions, and evidence links after confirmation. Standings, counters, chapters, and the PNG chart recalculate from the remaining races. The session's teams and players remain. This also works inside an unloaded folder. Pending, unconfirmed races can be deleted during capture or import to remove a false OCR start; their pending review and in-flight recognition are discarded. Use the remaining race's chapter editor and result review to correct its information. Confirmed-race deletion requires stopping capture/import and waiting for uploads to finish. Video and evidence files are not deleted.

Existing databases are upgraded transactionally. A pre-change backup is kept beside the database as `tracker.sqlite3.pre-session-migration.bak`. Legacy moved races belong to their visible folder; shared old player/team identities are split by session without discarding placement revisions. Older versions recorded resets and manual removals with identical flags, so deleted historical roster entries may reappear when restored. Current-session deleted entries stay excluded; the old migration-warning banner is no longer displayed.

Use **Delete player** or **Delete team** to remove individual mistakes from the active lists. Deleting a team unassigns its active players. Historical race placements remain readable. Rename controls are also available if you only need to correct spelling.

Race history is grouped into collapsible folders, newest-created first. The browser remembers which folders you collapsed. Create or rename a session, filter by folder, and sort races by date, course, recording offset, or result status in either direction. Course names use the supplied track icons when known. Races cannot move between independent sessions. The team panel shows logged races and confirmed results; player rows are numbered and show the recorded roster count.

## Correct a course, view evidence, or add penalties

Use **Edit course / chapter** in race history to correct the course name while retaining its recording and timestamp. **View evidence** displays captured screenshots. Evidence also appears automatically when reviewing/correcting results. New captures save both the pre-race course/roster and finishing table, with durable database links that remain after confirmation and restart. Surviving older pending cards are backfilled; already-confirmed legacy screenshots with no saved race mapping cannot be reliably recovered and are shown as unavailable. Manual races have no captured evidence.

Use a race's **Penalties** control to choose one of its confirmed participants, enter the positive number of points to deduct, and optionally enter a reason. The deduction belongs to that race and player, reduces player/team net standings and exported totals, and can be removed. Multiple deductions are allowed. Finishing positions and base placement points stay unchanged. Gross room progress still reaches 984 for twelve complete races; net points/deductions are shown separately. Before correcting a result to remove a penalized player, remove that player's penalty first.

## Character themes

Select a built-in palette under Settings or use the custom theme editor. Upload your own PNG/JPEG/WebP icon, request editable image-color suggestions, adjust UI/standings colors, and save a named theme. Built-in presets remain unchanged; save a copy to customize. Custom themes and icons survive restart and apply to dashboard, OBS and PNG reports. No icon is supplied by default. Theme edits do not change scores.

## Folder PNG reports and Lounge ID

Expand a history folder and choose **View folder PNG** to preview it without downloading. Select **Copy image** to paste it into another app. If browser clipboard access is unavailable or denied, right-click the preview and choose **Copy image**, or use **Open full-size PNG**. Close the preview with its Close button or Escape. **Download folder PNG** remains available to save a file. Each colored line shows a team's cumulative net points: R1 contains its first-race points, R2 contains R1+R2, and so on. The Y axis scales to the data, including negative totals after penalties. The X axis shows each race, course name, in created order. A pending race stops the line there and for later races until its result is confirmed; unknown points are not treated as zero. An absent team earns zero in a confirmed race. Penalties reduce the total starting at their linked race. Below the graph, color-coded team cards group all team members. Each card shows the team rank and total, plus each player's individual net score and overall player rank. The title bar contains the uploaded theme icon, session name, optional Lounge ID, and gross room points out of 984. Team gaps use `-x / -y`: points behind the team immediately above, then points behind first place. For example, with team totals 100, 90, 85, third place shows `-5 / -15`. Ties show zero. The confirmed-race subtitle and membership footer are omitted. Team identities retain matching colors across the dashboard, graph, cards and OBS overlay as standings change. Unassigned players and teams with more or fewer than two members are supported. Reports use the selected session's active roster and current team assignments at export time, even when that session is not loaded. They include active players and teams with zero points, but exclude deleted players and teams from standings. Historical placements and gross room points remain intact; reports do not reconstruct historical team membership.

PNG exports default to the highlighted team selected in overlay Settings. Its players' finishing places appear in a compact grid with course thumbnails below the graph and standings. In the preview, **Race placements for team** selects another team from that folder or **None**. Changing the preview selection loads a new image without changing overlay Settings. Copy and full-size actions use the currently displayed snapshot. Pending and unknown places remain labeled; deductions never change finishing positions.

Enter the optional numeric **Lounge ID** and select **Save Lounge ID**. The ID appears on the PNG, and its browser link opens `https://lounge.mkcentral.com/mkworld/TableDetails/ID`, for example [table 1146183](https://lounge.mkcentral.com/mkworld/TableDetails/1146183). Clear the field and save to remove the ID. This does not read, update, or submit any external Lounge table.

PNG rendering uses a locally installed Unicode-capable font such as Noto Sans CJK for labels and names. Install Pillow using the reports extra. Oversized reports fail explicitly rather than silently dropping content.

## Clean up uploaded videos

**Videos → Video library** lists app-managed copies in `DATA_DIR/uploads/`, their sizes, and whether they are in use. Delete an individual upload or use **Clean up uploaded videos**. The default age is 30 days, measured from the uploaded copy's last modification time. Set the age to 0 to remove all inactive uploads. Every deletion requires browser confirmation, and partial failures show which files could not be removed.

Cleanup only removes direct, regular video files from the app's upload directory. Uploading or importing files are protected. Original files imported by local path outside that directory, symlinks, subdirectories, session results, and captured evidence are never removed by video cleanup. Import job history remains available, but you cannot re-import a deleted copy unless you upload it again or provide an existing local path. Back up any uploaded copies you want to keep before deleting them.

## Automatic timestamp creation

During live capture or video imports, the tracker proposes an intro timestamp when the pre-race roster/course screen transitions into the cinematic intro. Markers are sampled estimates, not frame-accurate detection. The history editor lets you correct the course, recording, and offset against the video.

Open **Chapters**, choose a recording, and click **Download chapters** to save its timestamp text file. Lines contain millisecond offsets and course names, for example:

```text
00:00:56.000 Whistlestop Summit
00:03:00.000 Wario Stadium
```

Use these as recording-relative references when placing markers or chapters in your video editor. If you trim or rearrange the recording, adjust offsets to the resulting timeline. This version exports a sidecar; it does not embed markers into OBS recordings or create timeline markers in a video editor automatically.

A race can have a chapter even if its finishing result is absent. Unknown offsets are omitted from the download until corrected. Changing sessions does not reset recording timestamps.

See [data and backups](data-and-backups.md) before deleting files or moving an installation.

## Uploaded icons and custom themes

Use **Settings → Custom theme editor** to start from a built-in palette or a saved custom theme. Choose your own still PNG/JPEG/WebP (up to 4 MiB; maximum 4096 px per side and 16 megapixels). Images are decoded and normalized to a metadata-free PNG up to 512 px. SVG and animated images are rejected.

Upload does not replace your colors automatically: request suggestions, review the suggested palette, then apply it explicitly. Edit UI colors and twelve standings colors, give the preset a name, and save it. Built-ins cannot be overwritten; custom themes support update and deletion. Select a different theme before deletion; the editor can switch to the default for an active-theme deletion. The selected theme is shared by the dashboard, OBS overlay and future PNG exports in all modes.

Themes and their icons are stored together in `custom-themes.json` in the configured tracker data directory. Include that file and `settings.json` in local backups. Keep Pillow installed when reopening a data directory containing uploaded icons: persisted icons are validated during startup, even for manual scoring. Do not commit it. There is no external image upload service, and theme/icon export/import packs are not part of this version. User images must be images you have permission to use.
