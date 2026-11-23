---
title: 'OBS reference'
weight: 50
---

# OBS

[Home](../../README.md) · [User guide](user-guide.md) · [OBS](obs.md) · [FAQ](faq.md) · [Troubleshooting](troubleshooting.md)

Set up live capture, recording timing, text standings, or the transparent browser overlay. OBS screenshot capture and recording timing require a loopback WebSocket connection from the tracker server. The dashboard defaults to loopback too, but can use an explicit network binding. Commands below run from the repository root.

## On this page

- [Screenshot capture](#enable-obs-capture)
- [Camera input](#select-a-camera-in-the-web-ui)
- [Use OBS with Discord](#use-obs-video-in-the-tracker-and-discord-together)
- [Text standings](#obs-text-standings)
- [Browser overlay](#transparent-browser-overlay)

## Enable OBS capture

First install the capture dependencies in the [installation guide](../getting-started.md). From the repository root, the setup command is:

```sh
.venv/bin/python tools/setup_local.py
```

See [recognition](recognition.md) for optional name-reader setup. OBS screenshot capture does not use a V4L2 camera stream.

In OBS, check **Tools → WebSocket Server Settings** for the local server address and authentication settings. Then use the dashboard, with no capture-specific launch option:

1. Open **Capture & review**. **Capture input** defaults to **OBS WebSocket screenshots**.
2. Enter the exact **OBS source or scene name**, such as `CapCard` or `Desktop`. A source captures that input directly; a scene includes its composition, cropping and overlays. Choose whichever supplies the unobstructed, uncropped game image.
3. Keep the saved **OBS WebSocket URL** and leave the password blank to reuse the locally saved password. Enter a password only to replace it; **Forget saved password** removes it.
4. Leave **Name recognition** on **Automatic** to prefer installed Qwen3-VL-8B BF16 when at least 22 GiB of GPU memory is free, or explicitly choose an installed GGUF, 4B NF4, BF16, INT8, NVFP4, PaddleOCR, or Tesseract reader. The smaller **Qwen3-VL-4B NF4** option requires at least 6.5 GiB free VRAM and must be selected explicitly; Automatic does not choose it. CUDA Qwen choices show observed peak process VRAM; GGUF memory and live throughput are unmeasured, so try recordings first. GGUF must also be selected explicitly; NVFP4 also requires supported GPU hardware. Check [reader requirements](recognition.md#qwen-gpu-readers) and leave memory for OBS. Select **Start capture**. Wait for **connected: Reading ...**. Use **Stop capture** before changing source or switching back to a camera.

OBS screenshot mode uses the OBS recording clock automatically, but it does not start/stop recordings, change scenes or open the virtual camera. Discord can keep using OBS Virtual Camera. Capture, imports and uploads retain their existing mutual exclusion. Source and mode changes do not connect until you select Start capture. Connection errors appear in capture status; saved reviews remain available when capture stops.

Wait for **connected** capture status, then start the OBS recording. The GPU recognizer loads before capture begins so its initial model load does not stall a result review. Start the tracker **before starting an OBS recording**. This lets it observe the recording start and associate timestamps with the right recording. If a recording is already active when the tracker connects, or the connection/file clock changes, unverified offsets stay blank for correction. A recording pause does not advance the chapter clock. Automatic file splits require manual verification of file-local offsets.

## Select a camera in the web UI

Start the app normally and select the input in **Capture & review**:

```sh
.venv/bin/python -m mkw_tracker
```

Open **Capture & review**, switch **Capture input** to **Camera device**, select **Refresh cameras**, and choose your video input. Device names and `/dev/videoN` numbers vary by machine; choose the input that OBS is feeding. Use **Start capture** to begin recognition and **Stop capture** to finish. No camera starts automatically. The picker lists available capture inputs and reports access errors. If the virtual camera does not appear, start it in OBS and refresh the list.

Choose the name reader, normally Automatic. Capture status names the selected engine, including loading and any Tesseract fallback. Starting may take time while the local model loads; the dashboard remains responsive. Capture keeps the current review queue and saved scores. While capture is starting, running or stopping, other camera/import jobs stay blocked until its device/model resources have closed. A failed camera release requires restarting the tracker rather than risking overlapping streams.

A virtual camera only supplies images, not recording timestamps. To create recording-relative intro markers, enable **Use OBS recording clock**, enter the local OBS WebSocket URL and password if required, then start capture. After a successful clock-enabled capture start, the OBS URL and password are saved locally in `DATA_DIR/obs-connection.json`. This plaintext file has owner-only 0600 permissions and is ignored by Git. Leave the password field blank to reuse it; **Forget saved password** clears the saved value. The password is never returned in state, camera or overlay API responses, regardless of the dashboard bind address. A network-accessible dashboard still lets reachable clients start capture using those saved credentials; see [network access](user-guide.md#access-from-another-device). File-write failures show a warning without stopping an already-started capture. Without that clock, detection and result reviews still work, but new intro offsets remain unknown and require manual timing. Connecting partway through an OBS recording can require verification or a fresh recording before automatic markers are valid.

## Use OBS video in the tracker and Discord together

Use **OBS source screenshots for the tracker** and reserve **OBS Virtual Camera for Discord**. Both receive video from OBS, but the tracker does not compete for the virtual camera's capture stream.

1. In OBS, enable **Tools → WebSocket Server Settings** and note the local URL and authentication settings. Choose the exact input or scene name containing the uncropped game image.
2. In **Capture & review**, select **OBS WebSocket screenshots**, enter the exact source or scene name, keep the saved connection URL, leave the password blank to reuse it, and select **Start capture**. Do not launch a second tracker against the same database.
3. Start **OBS Virtual Camera**, then select that camera in Discord's voice/video settings. Start the virtual camera before refreshing Discord's camera list. The tracker continues to use WebSocket screenshots, not the virtual camera stream.

See [camera contention diagnostics](troubleshooting.md#virtual-camera-is-busy-or-missing-in-discord) if another app cannot use the virtual camera.

## OBS text standings

In **Settings**, choose **Plain text (.txt)** or **Markdown (.md)** under **OBS standings format**. The setting persists across restarts. The app maintains **`DATA_DIR/team-standings.txt`** or **`DATA_DIR/team-standings.md`**, normally inside `.local/tracker/`. The Team standings section shows its absolute path. Configure an OBS text source to read that file. The app replaces it atomically after successful team, roster, result, penalty, or selected-session changes; OBS receives team points, gaps, and room progress without a browser refresh. Team gaps use short deductions such as `-4`, not “4 behind above.” **Download current standings** is a separate snapshot, not the live file. Changing formats changes the output path; update the OBS source to the displayed path. The previous generated output is removed after the new one is written successfully. Write failures are reported beside the live-file path and do not roll back saved race results. Failed file writes retry after one second, even without an open dashboard. Automatic WW results also update their text file immediately after commit. Dashboard polling and standings downloads only read state; they do not trigger file updates.

## Transparent browser overlay

To use the transparent overlay instead of a text file:

1. Start the tracker and open **Overlays**.
2. Copy the displayed **Browser source URL**, normally `http://127.0.0.1:8765/overlay`.
3. Add a **Browser** source in OBS and paste that URL. Set its source width and height to the selected source’s dimensions, initially 560×600. The default URL works on the tracker host. For another machine, read the [network-access warnings](user-guide.md#access-from-another-device) first; an overlay viewer does not have read-only access.
4. Choose team or player standings, font size, panel opacity, title, member/header/gap visibility, animation, overflow scrolling and scroll speed. Select **Save overlay settings**. Changes apply to the overlay automatically. If you change width/height, also update the OBS Browser source dimensions.

The page background is transparent. Panel opacity affects card backgrounds, not the names or scores. Team cards include each member's score and overall rank. Scrolling starts only when content is taller than the configured area; disable it or increase the height for a fixed display. Animation and scrolling respect the browser's reduced-motion preference. The overlay polls local scores, follows the loaded session, preserves its last standings during a connection failure, and reconnects automatically. Keep the tracker running while OBS uses the source. **Open overlay preview** lets you inspect the layout in a browser. Use **Highlight team** to emphasize your team with a star, tinted background and outline; all other teams remain visible. Alternating brighter/darker card backgrounds make rows easier to distinguish without changing transparency. Both options are in overlay Settings. Each team also has a color picker under **Standings**, plus **Automatic color** to reset it. Custom colors apply to the dashboard, PNG and overlay and persist with the team across session loading. Team gaps match the PNG: `-x / -y` means points behind the team above and points behind first. Green up and red down arrows show how many places each team gained or lost with the latest confirmed race. Unchanged teams and first-race standings have no arrow. Movement uses saved race input order, not browser refreshes or pending races, so it survives reloads. Corrections, penalties and roster changes recalculate the comparison using current net scores and team assignments. TXT/MD output remains available independently.

The **Show overlay**, **Hide overlay**, and **Toggle overlay** buttons control the whole scoreboard. Choose a **Show / hide effect**, **Slide / wipe edge**, and **Transition duration** from 0..5000 milliseconds, then save. Bouncy slide, left and 400 ms are the defaults. Other effects are soft fade, checkered flag wipe, pixel dissolve, pop zoom, 3D flip, circle iris and curtain wipe. Zero duration, disabled animations and reduced motion switch instantly. Visibility transitions keep card DOM and overflow position intact. Visibility is shared by browser instances of the same URL and resets to shown when the tracker restarts. [Named sources](../overlay-profiles.md) have independent visibility, automatic-race behavior and effects. Preferences persist.

Automatic visibility is off by default. Each source can enable either **Automatically show during races / hide after results** or **Automatically hide during races / show after results**, not both. The first shows at a recognized course intro and hides at accepted results; the second reverses those actions. Both use the same live detections in Teams, FFA and WW. Pending Mogi results remain unscored; enabling visibility does not enable YOLO. Manual controls override the current phase until the next detected event. Enabling or switching direction waits for the next event. Imported recordings, manual edits, repeated frames and persisted capture state after restart do not replay visibility changes. Missed detection requires manual control; there is no guessed race timer.

For Stream Deck or another local HTTP client, send POST to `http://127.0.0.1:8765/api/overlay/show`, `/api/overlay/hide`, or `/api/overlay/toggle`. Either omit the body or send JSON `{}` with `Content-Type: application/json`. These controls work in both Mogi and Worldwides without a mode tag, so existing hotkeys keep working after a mode switch. Responses include `{"visible": true}` or `{"visible": false}`. GET requests do not change visibility, and the configured listener's Host and same-origin checks apply. These checks are not authentication.

For example: `curl -X POST http://127.0.0.1:8765/api/overlay/toggle`. A client that explicitly supplies `expected_tracker_mode` still gets a stale-mode error if its mode is no longer active. The bodyless exception applies only to these three visibility controls, not to data-changing API routes.

[Character themes and team colors](user-guide.md#character-themes) also apply to PNG reports.
