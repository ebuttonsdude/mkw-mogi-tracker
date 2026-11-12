---
title: "Fix a problem"
weight: 60
---

# Fix a problem

Start with the heading that matches what you see. Keep any error message; it helps identify the next step.

## Setup says a command is missing

**`uv` or `git` is not recognized**

Close and reopen your terminal after installing it. On Windows, use PowerShell, not the Ubuntu terminal, unless you chose WSL.

**Tesseract is missing**

Install it using your [platform's instructions](getting-started.md). On Windows, default Tesseract folders are detected automatically. For a custom folder, follow [the persistent user-PATH steps](install/windows.md#tesseract-is-missing), so double-click startup can find it too.

## The dashboard will not open

1. Look at the terminal where you started the tracker. Is it still running?
2. Open `http://127.0.0.1:8765` on that same computer. For WSL, try `http://localhost:8765` from Windows.
3. Read any terminal error before retrying.

Do not start a second tracker against the same data folder. Do not change the address to `0.0.0.0` to fix a local connection problem.

## OBS will not connect

1. Confirm that OBS is running on the same computer.
2. In OBS, check **Tools → WebSocket Server Settings**.
3. Match the tracker URL's port to OBS. The usual URL is `ws://127.0.0.1:4455`.
4. Enter the correct OBS password.
5. Copy the exact source or scene name, including spaces.
6. Start capture and read its status.

For WSL with Windows OBS, follow the [mirrored networking setup](install/wsl.md).

## Names or results are wrong

1. Check that the full game image is visible and the table is unobstructed.
2. Confirm you selected the [reader you installed](recognition.md#select-it-in-the-app).
3. Correct the proposed names and choose the existing player identities.
4. Confirm only when all twelve players and their finishing order are right.

For a missed race, check the recording and [enter it manually](user-guide.md#enter-a-race-manually). Do not guess from cumulative scores.

## The GPU reader will not load

- Check **free** graphics memory, not only total memory.
- NF4 needs at least **6.5 GiB free**; the recommended NVFP4 needs **11 GiB free**.
- NVFP4 requires compatible hardware and Linux. It is not supported on native Windows.
- Stop capture before changing readers. Try **PaddleOCR** or **Tesseract** if needed.

An installed model is not proof that your current machine can run it with OBS open.

## The GGUF Vulkan reader fails

1. Stop capture and imports, then read the exact error in the tracker status or terminal.
2. Make sure your graphics drivers are up to date and include Vulkan support for your GPU.
3. Check whether the error names a missing Vulkan device, insufficient memory, or a model/runtime file problem; keep that message when asking for help.

Installing a model does not install or update your graphics driver. See [reader setup](recognition.md#install-or-change-a-reader) for changing a reader or backend.

## My scores are still zero

Open **Capture & review**. Pending mogi results do not count until you confirm them. Also check that you loaded the correct session and tracker mode.

## PNG export cannot find a font

Guided setup installs Pillow. Reports also need a local font containing your players' characters.

- **Arch:** install `noto-fonts-cjk`.
- **Ubuntu:** install `fonts-noto-cjk`.
- **Fedora:** install `google-noto-sans-cjk-fonts`.
- **Windows:** install Japanese supplemental fonts in Windows Optional features, or a static Noto CJK font.

Choose **Settings → Overlay & PNG font → Reset to default**, or copy a suitable TTF/OTF into the data folder’s `fonts/` directory and select **Reload fonts**. Missing characters use installed fallback fonts; if none covers a character, a box can appear without changing the saved name. See [report font setup](technical/installation.md#report-fonts). Fonts are not downloaded during export.

## The overlay is empty or outdated

1. In OBS, hide the Browser Source, then show it again. If it is still stale, use the source’s **Refresh** action.
2. Keep the tracker running and open the overlay URL in your browser to check it.
3. Confirm a mogi race and check the selected session.
4. In Settings, select **Show overlay** and save the overlay settings.
5. Match the OBS Browser source dimensions to the tracker settings.

If the tracker disconnected, the overlay may still show its last good scores.

## An import stopped or missed races

Only one import can run at a time, and live capture must be stopped first. Start with the default **0.5-second** sample interval. A recording can still contain screens too short or unclear to read.

Cancelled imports keep already found results. Check the session before importing again so you do not mistake partial results for a completed event.

## Enable diagnostic logs

Stop the tracker, then start it with `--verbose`:

```sh
.venv/bin/python -m mkw_tracker --verbose
```

On Windows, run `.\startWindows.bat --verbose` in PowerShell from the tracker folder.

Check **`.local/tracker/logs/`**, or **`<data-dir>/logs/`** if you chose a different data folder. `app.log` covers startup and output errors; `http.log` covers dashboard/API operations; `capture.log` covers capture and reader readiness; `obs.log` covers OBS connections; `recognition.log` covers local reader workers; `imports.log` covers video imports. Verbose mode adds request statuses, sample timings and import progress to those files. The terminal still shows concise warnings and errors, not every sample.

Files rotate at 2 MiB with three backups per component. Logs use UTC timestamps and omit credentials, request payloads, recording paths and raw exception text. Raw model-worker stderr is not recorded. Keep the dashboard's capture/import error message too, and review files before sharing.

## Still stuck?

Report the operating system, what you tried, the selected reader, and the exact error. Remove passwords, access tokens, personal paths, and private player information. Do not attach your database or a full recording.

[Detailed troubleshooting](technical/troubleshooting.md)
