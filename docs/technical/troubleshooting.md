---
title: 'Troubleshooting reference'
weight: 50
---

# Troubleshooting

[Home](../../README.md) · [User guide](user-guide.md) · [OBS](obs.md) · [FAQ](faq.md) · [Troubleshooting](troubleshooting.md)

Start with the capture status or import job's error message. Stop capture and wait for shutdown before changing input or importing. Do not start a second tracker against the same database.

## The dashboard will not open

Run the tracker from the repository root and open the loopback URL printed in the terminal, normally `http://127.0.0.1:8765`. The tracker must stay running. If that port is occupied, use `.venv/bin/python -m mkw_tracker --port 8766` and open the printed URL. A second instance must use a separate `--data-dir`; do not use it to bypass an active job.

## Capture dependencies or OCR data are missing

Follow the [installation guide](../getting-started.md), then launch with `.venv/bin/python`. Tesseract must be on `PATH`; the setup script keeps English/Japanese data in `.local/tessdata`. A checksum error means the downloaded file did not match the pinned data. Do not bypass the check. Neural name readers need their [separate setup](recognition.md#name-readers).

## A Qwen reader will not load

For **GGUF**, read the exact error message and make sure your graphics drivers are up to date. Check that the selected device supports Vulkan. Use the [reader setup instructions](../recognition.md#install-or-change-a-reader) to change configuration; the CUDA commands below do not apply to GGUF.

For the **CUDA profiles**:

Install the exact selected profile using its [setup command](recognition.md#qwen-gpu-readers); installing BF16 alone does not install the 4B NF4, INT8 or NVFP4 runtimes. For the smaller reader, use `.venv/bin/python tools/setup_neural.py --engine qwen-4b-nf4`, then select **Qwen3-VL-4B NF4** explicitly. It needs at least 6.5 GiB free VRAM at startup. Check the error for the required GPU architecture, CUDA runtime, or free-memory threshold. Selector VRAM labels are measured process peaks, not guarantees that a card of that capacity will fit the model alongside OBS and the desktop.

This NVFP4 backend does not run on RTX 4070 or Radeon GPUs. An RTX 5070 passes the architecture check but still needs enough free VRAM. The 4B NF4 reader is an explicit smaller option for compatible NVIDIA GPUs, including RTX 4060 and RTX 4070, when at least 6.5 GiB is free. The separate GGUF reader offers Vulkan/CPU inference without CUDA; PaddleOCR and Tesseract are smaller CPU alternatives when these requirements are not met. Automatic keeps its existing BF16-first selection and does not switch to a quantized model. Stop capture and wait for cleanup before choosing another reader; do not start a second recognizer to bypass the failure.

## OBS will not connect

Check **Tools → WebSocket Server Settings** in OBS, the local URL, authentication, and exact source or scene name. The default URL is `ws://127.0.0.1:4455`. Use **Start capture** after editing settings; changing a source does not connect automatically. A blank UI password reuses the saved password. See [OBS capture](obs.md#enable-obs-capture). Never post the password in an issue.

## A result was missed or remains pending

Use an uncropped, unobstructed 16:9 image. The tracker needs all 12 finishing rows while the initial `+points` column is visible; later cumulative standings are not finishing order. The 250 ms live target includes acquisition and processing, so slow capture/OCR may miss the window. Check the reported acquisition and recognition times. For imports, try the default 0.5-second interval with a fresh destination, but shorter intervals do not guarantee detection.

Open saved evidence or the original recording and enter a result manually when needed. A suspected screenshot is unverified and must not be treated as a confirmed finishing order. Nothing scores until you confirm 12 distinct identities. See [recognition](recognition.md) and [reviewing results](user-guide.md#review-captured-results).

## Names or teams are wrong

Correct the reviewed name and select the intended existing player. Only a unique exact name match is selected automatically; duplicate names and similar-looking symbols need a manual choice. Automatic first-character teams are created only on the first confirmed captured roster in an untouched session. Change assignments under Standings. See [identity review](user-guide.md#review-captured-results).

## Recording offsets are blank or wrong

Start clock-enabled capture before starting the OBS recording. Camera images alone have no recording clock. Connecting midway, connection changes, or file splits can leave timing unverified. Correct the recording and offset in **Edit course / chapter**. Unknown offsets are omitted from chapter downloads. See [chapter exports](user-guide.md#automatic-timestamp-creation).

## Imports or capture are blocked

Only one import can run. Capture, imports, and uploads protect their resources from overlapping operations. Stop or cancel the active job and wait for its shutdown before starting another. Cancellation retains already captured review cards and chapters. A failed camera release requires restarting the tracker rather than opening another stream.

## Overlay or text standings do not update

Keep the tracker running and check the loaded session. OBS must run locally. For a browser source, use the URL and dimensions shown in Settings; saved width/height changes also need updating in OBS. Check **Show overlay** if it was hidden. The overlay intentionally retains its last good standings on connection failures. For text sources, use the displayed live-file path rather than a downloaded snapshot; changing TXT/MD format changes the path. File-write errors appear beside it. See [OBS outputs](obs.md#obs-text-standings).

## PNG export or Copy image fails

Install Pillow and a Japanese-capable font such as Noto Sans CJK for report rendering. For clipboard restrictions, right-click the preview and choose **Copy image**, open the full-size PNG, or download it. Oversized reports return an explicit error; split the report rather than assuming it contains every race. See [PNG reports](user-guide.md#folder-png-reports-and-lounge-id).

## Virtual camera is busy or missing in Discord

Use [OBS screenshots for the tracker and Virtual Camera for Discord](obs.md#use-obs-video-in-the-tracker-and-discord-together). Start Virtual Camera before refreshing Discord's device list. Do not unload or reconfigure the camera driver during a call or capture.

## Reporting a problem

Include the selected input/name reader, error text, relevant timing, and reproduction steps. Omit credentials and private session data. Do not attach full recordings to Git; prefer a small still-image fixture with provenance. See [Contributing](contributing.md).
