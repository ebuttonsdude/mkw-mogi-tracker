---
title: 'Worldwides tracking reference'
weight: 50
---

# Worldwides tracking

[Home](../../README.md) · [User guide](user-guide.md) · [Recognition](recognition.md)

Select **WW** in the dashboard's tracker-mode selector to track the highlighted local player in worldwide races. Mogi remains the twelve-player, review-first team scoring mode. Stop capture and finish or cancel imports/uploads before switching modes. Switching never starts capture.

WW has its own sessions, history and database under the tracker's `ww/` data directory. Switching back restores the selected Mogi session. The selected mode survives application restart. Back up the complete data directory to include both modes.

## Capture and imports

Use the same OBS screenshot, Linux camera or video-import controls as Mogi. WW reads the highlighted player's finishing-position glyph and rating with CPU Tesseract; it does not load a player-name model. The player name in Standings changes the displayed name in the OBS overlay and reports. It does not change which player the tracker follows.

Clear, stable WW results update automatically. Compare saved screenshots in Race history and correct mistakes there. This does not change Mogi's requirement to confirm all twelve identities before points count.

Live OBS and camera capture assume no gaps within a session. Each new live result uses the previous recorded final VR as its starting VR and calculates `change = current final VR - previous final VR`. The baseline stays within the same session and survives capture restarts. Correcting or deleting an earlier result updates dependent live changes. The first live result still needs its own observed starting VR/change; without that evidence it remains unknown. A manual correction overrides automatic derivation for that result. Existing saved results are not retroactively marked for derivation.

The game first shows the previous VR and a signed change, then animates the rating. A settled final reading needs corroboration from another final reading or repeated rating-plus-change totals from the same results screen. WW never saves an animated rating as final. A highlighted player can be pinned below the visible top twelve; the row number is not their finishing position.

Video imports do not make the live no-gaps assumption. A cut can hide the starting rating or change; a proven final VR and position can still be saved while the missing values remain **Unknown**, not zero. Imports never subtract adjacent races' ratings to invent a change across a potentially missed race.

Keep the capture image uncropped, unobstructed and 16:9. Start video imports at a 0.5-second interval. Slower sampling can miss a short change or result screen. Unrecognized screens need manual entry.

## Results and statistics

- Add a missing race in Race history, then enter final VR and position. Positions support 1 through 24 and display as a single number, without a lobby count.
- Leave unknown starting VR and change blank. If entering both starting VR and change, they must agree with final VR.
- Correcting a result replaces its contribution. A newer correction cannot be overwritten by an older edit or in-flight capture.
- Standings shows current, starting, peak and lowest observed VR. Peak and lowest include known before/after observations. A missing starting value stays unknown.
- The graph breaks at missing results or unproven transitions. A later known rating remains visible without claiming continuity through missing races.
- Course statistics show race count, known-change count, mean known VR change and mean finishing position. Choose the current session or all WW sessions. Tables in the dashboard and PNG reports rank courses by mean finishing position, best first, with course-name ties. Averages do not treat missing changes as zero, and raw finishing positions are not normalized for lobby size.

The total session change is shown only when every logged race has a known change. A pending race or missing change prevents a complete total, but does not hide the latest observed VR.

For imported recordings, you can manually confirm that two results are consecutive, enter the previous final VR as the current race's starting VR and leave its unknown change blank. Saving calculates the change from those values. Live capture makes this continuity assumption automatically; imports and PNG export do not.

## Overlay and PNG

The same `/overlay` browser-source URL follows the selected mode. In WW, Settings provides separate switches for **Current VR**, **Peak VR**, **Lowest VR**, the **VR graph**, **Last 10 races VR change** and **Theme icon**. The last-10 change uses the latest up to ten recorded results in the current session and labels the number used. Pending races are excluded; an empty window or any unknown change displays **Unknown**. Corrections and deletions recalculate it. **Show header** controls the title and details independently of the WW theme icon, so either can remain visible alone. **Current track: ...** appears opposite the player name when live capture has detected a course for the active session, including its prerace screen. It disappears when that detection clears; the overlay does not substitute the last historical course. Shared size, theme, opacity, visibility and motion preferences still apply. The background remains transparent, and connection failures retain the last good display.

Each WW history folder offers PNG preview and download. The WW report contains the player's rating history, observed extrema, finishing positions and course statistics rather than team points. Its race table shows course, place, VR change and final VR, without a lobby-size column. Both tables show course names without bundled thumbnails. The graph and averages helper text and player-tracking footer are omitted. Exporting an unloaded folder uses that folder's own player label and results. Mogi PNG reports remain unchanged.

Lobby size is no longer shown or requested in the WW dashboard. Existing saved counts remain intact on ordinary edits. A position correction that exceeds an old hidden lobby count clears that count instead of blocking the correction; the earlier revision is retained.

## Chapters

Course names and recording-relative timestamps remain editable. For imported recordings, timestamps refer to the imported file, including its cuts. Without a verified OBS recording clock, offsets remain unknown rather than using elapsed capture time. In **Timestamps**, TXT exports known offsets; Resolve EDL requires a timestamp for every race linked to that recording. Inspect the recording before relying on a marker for editing.
