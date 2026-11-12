---
title: "Choose a name reader"
weight: 20
---

# Choose a name reader

A name reader helps turn a results screenshot into editable player names. Installed readers run on your computer. The optional external reader uses a server you configure.

**It is optional, but highly recommended for mogi capture and recording imports.** You still check the result before points count. Manual scoring needs no reader. Worldwides uses basic Tesseract reading instead of these player-name models.

**Recommended bundle:** installs **PaddleOCR** and **Qwen3-VL-4B GGUF Q4_K_M** together. Choose Vulkan or CPU when asked. After both install successfully, setup offers to make Q4_K_M your default; declining keeps your existing default. PaddleOCR remains a separately selectable alternative, not a silent fallback.

Qwen is recommended for more accurate name reading, especially names containing symbols or Japanese characters. PaddleOCR is the lighter CPU option when your hardware cannot comfortably run Qwen. Neither replaces checking names against the screenshot.

## Where to start

**For a Qwen reader, we recommend Qwen3-VL-4B GGUF Q4_K_M first.** It offers Vulkan on compatible AMD, Intel and NVIDIA GPUs, plus CPU mode, and downloads about **3.33 GB** of model data rather than the NF4 route's **8.88 GB** source checkpoint. Choose **PaddleOCR** if the smallest CPU-only download matters more.

| Reader | Best fit | Before you choose it |
| --- | --- | --- |
| **Vulkan GPU / CPU: Qwen3-VL-4B GGUF Q4_K_M** | Recommended Qwen starting point across GPU vendors | About **3.33 GB** of model data. Needs a compatible Vulkan driver for GPU use; CPU is also available. Broad NF4 quality parity is not established. |
| **CPU: PaddleOCR** | Smallest download; CPU inference | About **81 MB** of model data, plus software. Uses your CPU. |
| **NVIDIA CUDA alternative: Qwen3-VL-4B NF4** | You prefer the CUDA reader, or already use it successfully | Needs **6.5 GiB free VRAM**. About **8.88 GB** of source weights, plus software. |
| **RTX 50-series: Qwen3-VL-8B NVFP4** | A compatible RTX 50-series GPU on Linux | Needs **11 GiB free VRAM** and supported Blackwell hardware. About **17.5 GB** of source weights, plus software. Not native Windows. |

VRAM is your graphics card's memory. **Free memory matters**, not just the number printed on the box. OBS, games, and other apps use some too.

If a reader cannot load, choose another in setup. Keep enough free memory for OBS and other running apps.

Model sizes above are downloads, not total disk requirements. Leave extra space for Python packages, cached downloads, and recordings.

## All available models

**Observed peak VRAM** is measured GPU memory use, not an average or minimum requirement. Measurements describe the recognizer process, not total GPU use: GGUF was measured on a Radeon RX 7900 XTX; CUDA readers on an RTX 5090. GPU entries are sorted by their observed peaks, after the CPU-only choice. Different hardware and test inputs mean this is not a controlled efficiency comparison.

| Reader | Setup engine | Observed peak VRAM | Platform constraint | Why choose it? |
| --- | --- | --- | --- | --- |
| PaddleOCR | `paddle` (alias `cpu`) | None; CPU | No GPU required | Light CPU reader when your hardware cannot comfortably run Qwen. |
| Qwen3-VL-4B GGUF Q4_K_M | `qwen-4b-gguf` | **4.17 GiB** with Vulkan full offload; CPU: none | x64 Windows or modern glibc Linux; compatible driver for Vulkan | Recommended cross-vendor Qwen starting point; generally good results in the small paired check, with CPU available too. |
| Qwen3-VL-4B NF4 | `qwen-4b-nf4` (alias `8gb`) | 4.83 GiB | Compatible NVIDIA CUDA GPU | NVIDIA CUDA alternative. Similar results to Q4_K_M in the small comparison; superiority is not established. |
| Qwen3-VL-8B NVFP4 | `qwen-nvfp4` (alias `nvfp4`) | 8.74 GiB | Linux, supported NVIDIA Blackwell SM120/121 GPU and CUDA 13 | BF16-like quality in the checked sample at about half its measured VRAM; not a guarantee on unseen names. |
| Qwen3-VL-8B INT8 | `qwen-int8` | 11.73 GiB | Compatible NVIDIA CUDA GPU | Larger 8B model for better name-reading quality than the 4B starting options, with substantially more GPU memory. |
| Qwen3-VL-8B BF16 | `qwen` | 17.48 GiB | Compatible NVIDIA CUDA GPU | Full-precision 8B quality choice when you have plenty of GPU memory. |
| Qwen3.6-27B NVFP4 | `qwen36-nvfp4` | 22.98 GiB | Linux, supported NVIDIA Blackwell SM120/121 GPU and CUDA 13 | Newer Qwen family, but results so far have not improved on Qwen3-VL; not recommended for this task. |

The GGUF measurement used twelve copies of one generated name crop, with all language layers and the vision encoder on the GPU. It is a short memory check, not a minimum requirement or a recognition-quality benchmark.

Quality guidance reflects the available checks and maintainer experience, not universal accuracy rankings. See [benchmark scope](technical/recognition.md#qwen-gpu-readers).

CUDA startup checks require more free memory than these observed peaks: **6.5 GiB** for NF4, **11 GiB** for 8B NVFP4, **14 GiB** for INT8, **22 GiB** for BF16 and **25 GiB** for 27B NVFP4. GGUF uses an advisory 4 GiB fit margin, not a fixed minimum-free-memory check or a guaranteed reservation. Leave additional room for OBS and your desktop.

All three 8B profiles download about **17.5 GB** of source weights; the 27B profile downloads about **55.6 GB**, before software and caches. For these CUDA profiles, quantization reduces runtime memory, not the source download. In particular, the NF4 installer downloads about **8.88 GB of BF16 (16-bit) source weights** and quantizes eligible tensors while loading. GGUF arrives already quantized, so its **3.33 GB** download is not evidence of an equivalent reduction in runtime memory or recognition quality. GGUF instead downloads prequantized 4B weights plus an image projector. See [the technical model reference](technical/recognition.md#qwen-gpu-readers) for hardware checks and validation limits. Native Windows setup rejects both NVFP4 choices before installation; WSL mirrored networking with NVIDIA NF4 has been validated; WSL NVFP4 remains unvalidated.

**Tesseract** is the basic reader installed by capture setup, not a separate neural download choice. The setup value `tesseract` is an alias for **Skip for now**. **Automatic** is an app selection policy, not another model to install.

## Install or change a reader

Stop the tracker first. On Windows, double-click **setupWindows.bat**. On Linux/WSL, open the project folder in a terminal and run:

```sh
sh setupLinux.sh
```

Choose an action:

- **Install reader / configure capture:** add a reader, or choose **Recommended** to install PaddleOCR and Q4_K_M together. Existing readers stay installed.
- **Remove reader:** select an installed reader and confirm removal. Removing your default first requires choosing a replacement. Only tracker-managed files are removed; shared download caches and custom model paths are left alone.
- **Set default reader:** choose which installed reader the app should start with.

After a successful installation, setup offers to set that reader as the default. The Recommended bundle offers Q4_K_M only after both installs succeed. Declining keeps your current default; PaddleOCR is not a silent fallback for Q4_K_M.

See [scripted setup options](technical/installation.md#guided-setup-without-prompts) for noninteractive use.

For **Q4_K_M**, choose **Vulkan GPU** with a compatible graphics driver or **CPU**. Setup downloads about **3.33 GB** of prequantized weights and an image projector plus the llama.cpp runtime; download size is not a RAM/VRAM requirement. It does not install CUDA or PyTorch. To switch backend, stop the tracker and rerun setup for this reader. Try a recording first, especially on CPU.

## Select it in the app

Setup can set your default reader when you accept its offer, or through **Set default reader**. Start or restart the app after changing it. You can still select a different reader for a capture or import.

1. For live capture, open **Capture & review → Name recognition**.
2. For recordings, open **Videos → Import video → Name reader**.
3. Choose the reader name that matches what you installed.
4. Start capture or import and read the status message.

**Do not choose Automatic expecting it to use GGUF, NF4, INT8, or NVFP4.** Automatic prefers installed Qwen3-VL-8B BF16 when at least 22 GiB of GPU memory is free, then PaddleOCR, then Tesseract.

Expand **Name recognition guidance** below the reader selector for model and hardware details. You can dismiss an NVFP4 hardware reminder for that model in this browser; the full warning remains in the guidance, and setup or runtime errors are never hidden by that choice.

## If recognition struggles

- Keep the full game image visible, without cropping or overlays covering the table.
- Compare the captured screenshot with the proposed names.
- Select existing players instead of creating duplicates after a spelling mistake.
- Stop capture before switching readers.
- If a GPU reader cannot load, try CPU or Tesseract.

[Fix recognition problems](troubleshooting.md#names-or-results-are-wrong) · [Advanced model reference](technical/recognition.md)


## External vision endpoint

Use this optional reader if you already run a vision-capable model server locally or on a **trusted second computer**. Prefer self-hosting. You manage its installation, resources, security and availability; the tracker never starts or stops it.

1. Open **Settings**, expand **Experimental**, then find **External vision endpoint**.
2. Enter a base URL ending `/v1` (for example `http://192.168.1.20:8080/v1`) or the full `/chat/completions` URL, the server's model ID and an optional API key.
3. For any hostname other than pinned localhost, or a non-loopback numeric destination, explicitly acknowledge image disclosure and possible repeated-request costs. Save before testing or selecting the reader. Editing the URL/model clears consent and the saved key.
4. Click **Test connection**. It sends one newly generated word image, not a capture. Success means that word was transcribed exactly, not that all real names will work.
5. Select **External vision endpoint** in Capture & review or Videos. Automatic never selects it. Normal Mogi results still require review and confirmation.

Hostnames other than pinned localhost require HTTPS, a trusted certificate valid for that hostname, and explicit consent even for trusted LAN names. Each connection pins a checked numeric DNS answer; unsafe peers such as loopback and link-local addresses are blocked. Numeric IPv4/IPv6 and `localhost` (fixed to `127.0.0.1`) also work. HTTP is allowed only for numeric loopback/private LAN addresses or localhost. HTTPS IP URLs need a trusted certificate valid for that IP. Certificate checks cannot be disabled. HTTP exposes names and any API key to network observers. No proxy or redirect is used.

**Only player name crops and a short transcription prompt are sent**, alongside the model ID and request parameters. No full frame, video, session, scores or OBS credentials are included. Each attempt sends up to twelve sequential requests. Recognition may repeat on later observations, so paid image requests can become expensive quickly. CPU and network delays can also miss results screens.

Each crop is resized down, if needed, to fit 512×128 pixels; each PNG is limited to 256 KiB, each JSON request to less than 351 KiB, and each response body to 16 KiB. Requests have a 30-second deadline including DNS, connection and response and an attempt has a 300-second deadline. There are no automatic request retries. A failed reader stops sending until you explicitly restart capture or start another import; baseline candidates remain available for review.

Stop capture, cancel the import, or use **Cancel test** to interrupt local requests. Saving configuration or forgetting the key also invalidates active readers. Remote computation or billing may continue after cancellation. An operating-system DNS lookup can also finish in the background, but cannot send a request after cancellation; further hostname attempts are blocked while that lookup remains outstanding. Reader lifecycle and safe failure categories go to `logs/recognition.log`; `--verbose` adds batch counts. URLs, keys, names and image payloads are omitted. API keys stay in the owner-only `external-vision.json` file, not public settings or API responses; session ZIP backups exclude this file. Exclude it from full-folder copies you share.

OpenAI-compatible does not necessarily mean image-capable. Use a vision model and, for llama.cpp, its matching multimodal projector. Unicode, small kana and symbols can still be wrong. [Protocol and compatibility details](technical/recognition.md#external-vision-protocol)
