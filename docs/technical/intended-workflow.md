---
title: 'Intended workflow reference'
weight: 50
---

# Intended workflow

[Home](../../README.md) · [User guide](user-guide.md) · [OBS](obs.md) · [Troubleshooting](troubleshooting.md)

Follow either workflow after completing the [installation guide](../getting-started.md). Start the tracker with `.venv/bin/python -m mkw_tracker` and keep it running. Capture inputs are configured in the dashboard.

- [Display live mogi positions in OBS](#display-live-mogi-positions-in-obs)
- [Generate timestamps and results from a video](#generate-timestamps-and-results-from-a-video)

## Display live mogi positions in OBS

The overlay shows player/team standings after you confirm each race, not live positions during a race. Recording is optional unless you want recording-relative timestamps.

### Set up OBS and the session

1. Start OBS and add your capture card as a source. Note its exact source name. Keep the game image unobstructed, uncropped, and 16:9.
2. In OBS, open **Tools → WebSocket Server Settings** and enable the server. Note the local URL, normally `ws://127.0.0.1:4455`, and the password if authentication is enabled.
3. Open your browser to **http://127.0.0.1:8765** and select **Capture & review**.
4. Set **Capture input** to **OBS WebSocket screenshots**. Fill in **OBS source or scene name** with the capture card source name, then enter the **OBS WebSocket URL** and **OBS WebSocket password**. Leave the password blank to reuse a saved one, or if OBS authentication is disabled. Choose **Automatic** under **Name recognition** unless you want a specific installed reader.
5. Under **Sessions**, enter a **New session name** and click **Create new session**. Start with an empty session so this mogi's roster and scores stay separate from earlier events.
6. Optionally, open **Settings → Transparent browser overlay** and copy the **Browser source URL**. Add a **Browser** source in OBS, paste the URL, and match its width and height to Settings. Use the capture card source for recognition, not a scene containing the standings overlay.
7. Return to **Capture & review** and click **Start capture**. Wait for capture status to report that it is connected and reading your source, rather than still loading or showing an error.
8. If you want to record, start the recording in OBS now, before the first race. Starting capture first lets the tracker observe the recording start and establish its clock. Without recording, you can still capture results and display standings.

### Play and confirm results

1. Play the first race. Once the tracker detects the course/roster screen, **Capture review** should show the current track and a pending race. No points count yet.
2. After the race, watch **Capture & review** for a results review card. Compare the screenshot with the finishing order, correct player names, and confirm all 12 player identities. Click **Confirm identities and count points**.
3. Open **Standings**. Players and points should now be filled out. In an untouched session, the first confirmation also creates editable teams from the first character of each reviewed name, ignoring case. Symbols, numbers and Japanese characters also form teams. Rename the teams and adjust player assignments here to match your mogi.
4. To highlight your team, open **Settings → Highlight team**, choose it, and click **Save overlay settings**. The selection also supplies the default team placement grid for PNG reports.
5. Open **Race history** and expand the session folder. If you are recording and timing is verified, the race should have a recording offset. Click **View evidence** to inspect the course/roster and results screenshots. Use **Edit course / chapter** to correct the course or timestamp if needed.
6. Continue playing and confirming results in **Capture & review** after each race. Reuse the same player identities; pending results never add points. The OBS overlay updates as results are confirmed.

### Finish the mogi

1. Confirm the final race, then click **Stop capture** and wait for capture to stop. End your recording in OBS if you started one.
2. Open **Race history**, expand the session folder, and choose **View folder PNG** or **Download folder PNG**. Check the confirmed race count and room points. A complete 12-race event has 984 gross placement points; penalties reduce net standings separately.
3. If you recorded, open **Timestamps**, select the recording, and click **Download Timestamps** to save its course timestamps. Verify them against the recording before editing your video.

If a race is missed, use its evidence or the original recording to enter the finishing order manually. A suspected screenshot is not verified finishing order. See [reviewing results](user-guide.md#review-captured-results) and [troubleshooting](troubleshooting.md#a-result-was-missed-or-remains-pending).

## Generate timestamps and results from a video

Use a local recording containing the course/roster screens and initial finishing-order tables. OBS does not need to be running. Timestamps refer to the imported file, so an edited video has its own offsets rather than those of the original recording.

### Start the import

1. Open **http://127.0.0.1:8765**. If live capture is active, select **Capture & review → Stop capture** and wait for shutdown before importing.
2. Under **Capture & review → Sessions**, enter a **New session name** and click **Create new session** for this video/event.
3. Open **Videos → Import video**. Set **Destination folder** to the session you just created.
4. Enter the **Local video path**. This reads the original file without copying it. Alternatively, use **Or choose a video** to upload a local copy into the app's data directory; allow enough disk space for that copy. Browser uploads are limited to 64 GiB.
5. Choose **Automatic** under **Name reader** and leave **Sample interval in seconds** at **0.5** to start. Click **Import video for review**.
6. Watch the job card for progress, completion, or an error. Only one import can run at a time. You can cancel it, but already captured races and timestamps remain available. The job stays associated with its selected destination session even if you change views or load another session.

### Review the event

1. Open **Capture & review** as results arrive, or after the import finishes. For each results card, compare the evidence, correct names, select the intended player identities, and click **Confirm identities and count points**. Imports never count points automatically.
2. After the first confirmation, check **Standings**. An untouched session gets the same editable first-character teams as live capture. Rename teams and adjust assignments to match the event. For later races, select the same player identities rather than adding duplicates.
3. Open **Race history** and expand the destination folder. Check the detected courses, confirmed results, recording offsets, and **View evidence** screenshots against the video. Use **Edit course / chapter** to fix a course or intro offset. Add missing races/results manually when recognition did not capture them; do not use cumulative standings as finishing order.
4. Wait for the import to finish and review every captured result. For a 12-race video, check that all 12 races are present and confirmed before treating the standings as complete. A race can have a timestamp without a captured result, or a result with timing that still needs correction.

### Export timestamps and results

1. Open **Timestamps**, select the imported recording, choose plain text or Resolve EDL, and click **Download Timestamps**. The text file contains recording-relative timestamps and course names. TXT omits unknown offsets; EDL requires you to correct them. These are sampled intro estimates, not frame-accurate markers.
2. Open **Race history** and choose **View folder PNG** or **Download folder PNG** for the event. Optionally choose your team under **Settings → Highlight team** and save, or use **Race placements for team** in the PNG preview to select its placement grid.
3. Keep the source video if you need to verify results or timing later. If you uploaded a copy, [video cleanup](user-guide.md#clean-up-uploaded-videos) can remove that copy after confirmation without deleting saved scores or evidence. Original files imported by local path are never deleted by cleanup.

See [Automatic timestamp creation](user-guide.md#automatic-timestamp-creation) for timestamp editing and export details, and [limitations](limitations.md) for recognition and sampling boundaries.
