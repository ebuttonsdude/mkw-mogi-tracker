---
title: 'Recognition reference'
weight: 50
---

# Recognition

[Home](../../README.md) · [User guide](user-guide.md) · [OBS](obs.md) · [FAQ](faq.md) · [Troubleshooting](troubleshooting.md)

The tracker detects results and calculates scores for you. Review and confirm the detected finishing order and player identities before points count. See [limitations](limitations.md) for supported inputs and sampling limits.

That confirmation rule applies to Mogi. [Worldwides mode](ww.md) uses a separate CPU highlighted-player reader and saves clear final VR/position automatically. It never loads the name engines below. Live capture assumes consecutive results within a session and calculates changes from the previous final VR; video imports leave unobserved changes unknown.

## Name readers

Choose the installed reader under **Capture & review → Name recognition**, or **Videos → Import video → Name reader**. No capture-specific launch options are needed.

**Automatic** selects installed Qwen3-VL-8B BF16 when at least 22 GiB of GPU memory is free, then PaddleOCR, then Tesseract. It does not automatically choose a quantized model. All processing stays on this machine. Capture status and import job cards show the selected reader and any fallback.

Neural worker teardown uses Windows Job Objects or POSIX unreaped-child checks. POSIX runtimes without `os.waitid` and `os.WNOWAIT`, including Python 3.11 on macOS, reject neural startup before launching a subprocess. Use Tesseract on those runtimes. Linux and Windows retain their respective worker paths.

For optional neural readers, run the setup commands below from the repository root after completing the [installation guide](../getting-started.md). These install the models; select the reader in the dashboard afterward, or use [guided setup](../recognition.md#install-or-change-a-reader) to save a default. CUDA and Paddle setup uses `uv` to create separate Python 3.12 environments; GGUF downloads a standalone llama.cpp runtime instead. Both keep their model data and runtime files under `.local/`. Selecting a reader does not start capture or download its model.

### Qwen GPU readers

The selectors offer **Qwen3-VL-4B GGUF Q4_K_M** for CPU/Vulkan alongside five CUDA profiles. GGUF needs no CUDA/PyTorch; its observed peak is not a minimum memory requirement. The table separates observed process peaks from startup free-memory checks:

| Reader | Setup engine | Observed peak process VRAM | Minimum free VRAM at startup |
| --- | --- | --- | --- |
| Qwen3-VL-4B GGUF Q4_K_M | `qwen-4b-gguf` | **4.17 GiB** with Vulkan full offload; CPU: none | No fixed check; advisory 4 GiB fit margin |
| Qwen3-VL-4B NF4 | `qwen-4b-nf4` | 4.83 GiB | 6.5 GiB |
| Qwen3-VL-8B BF16 | `qwen` | 17.48 GiB | 22 GiB |
| Qwen3-VL-8B INT8 | `qwen-int8` | 11.73 GiB | 14 GiB |
| Qwen3-VL-8B NVFP4 | `qwen-nvfp4` | 8.74 GiB | 11 GiB |
| Qwen3.6-27B NVFP4 | `qwen36-nvfp4` | 22.98 GiB | 25 GiB |

The 4B NF4 peak came from a production-worker check using an RTX 5090 and a 4.5 GiB PyTorch allocator cap; the other CUDA peaks came from separate benchmark runs. These CUDA runs used short, twelve-name image batches. The GGUF peak came from a separate Radeon RX 7900 XTX check. These are recognizer-process measurements, not total GPU use or a controlled cross-hardware efficiency comparison. Different hardware, inputs, software, or concurrent applications can need more memory. The startup checks include safety headroom and are not promises that inference will fit. Leave memory for OBS, the desktop, and other applications.

Install the profile you intend to select:

```sh
.venv/bin/python tools/setup_neural.py --engine qwen-4b-nf4
.venv/bin/python tools/setup_neural.py --engine qwen
.venv/bin/python tools/setup_neural.py --engine qwen-int8
.venv/bin/python tools/setup_neural.py --engine qwen-nvfp4
.venv/bin/python tools/setup_neural.py --engine qwen36-nvfp4
```

Run only the commands for the profiles you need. The 4B NF4 runtime uses `.local/vlm-nf4-venv`. The existing BF16 runtime stays in `.local/vlm-venv`. INT8 uses `.local/vlm-int8-venv`; both NVFP4 choices use `.local/vlm-nvfp4-venv`. These optional runtimes do not add dependencies to the tracker's `.venv` or replace another reader's environment.

BF16 uses PyTorch 2.10.0 with CUDA 12.8 and Transformers 4.57.6. INT8 and 4B NF4 use that stack with bitsandbytes 0.50.2. NF4 does not install FlashInfer or NVFP4 kernels. The tested NVFP4 stack uses PyTorch 2.14.1 with CUDA 13.0, Transformers 5.20.2, and FlashInfer 0.7.2.post1, with pinned packing and inference kernels cached during setup. A driver's advertised CUDA capability is not the same as PyTorch's bundled CUDA runtime.

The 4B NF4 profile uses [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct), pinned to revision `ebb281ec70b05090aa6165b016eac8ec08e71b17`. Setup downloads approximately 8.88 GB of floating-point source weights to `.local/models/qwen3-vl-4b`, about 8.3 GiB on disk. The worker quantizes language linear layers to bitsandbytes NF4 on load, retaining the vision encoder and output head in BF16. This is not a prequantized four-bit download. Choose **Qwen3-VL-4B NF4** explicitly in either selector; Automatic leaves it unselected.

All three 8B profiles share `.local/models/qwen3-vl-8b`, approximately 17.5 GB of source weights. The 27B profile uses `.local/models/qwen3.6-27b`, approximately 55.6 GB of source weights. INT8 and NVFP4 also quantize floating-point weights on load; their smaller VRAM use does not mean a smaller download. The readers keep models on one GPU and do not offload weights to CPU or disk. The NVFP4 recipe is on-load quantization, not an official prequantized NVIDIA ModelOpt checkpoint. See the [Transformers NVFP4 requirements](https://huggingface.co/docs/transformers/v5.20.2/quantization/nvfp4).

#### GPU compatibility

These five Qwen profiles use NVIDIA CUDA and require BF16-capable hardware. The 4B NF4 reader works with Ada GPUs through bitsandbytes; it is distinct from NVFP4 and does not require Blackwell. See [bitsandbytes hardware support](https://huggingface.co/docs/transformers/quantization/bitsandbytes). This application's NVFP4 backend supports NVIDIA Blackwell **SM120/SM121**, such as RTX 50-series GPUs, with the dedicated CUDA 13 runtime and a compatible driver. It does not support every GPU capable of some other NVFP4 implementation. The worker checks architecture and free memory before loading weights. [FlashInfer backend requirements](https://docs.flashinfer.ai/generated/flashinfer.gemm.mm_fp4.html).

- **RTX 4070:** Ada supports 4B NF4 when at least 6.5 GiB is free. Compute capability 8.9 cannot run this application's Blackwell NVFP4 backend. Its architecture can run INT8, but a 12 GB card cannot meet the 8B INT8 reader's 14 GiB free-memory check.
- **RTX 5070:** compute capability 12.0 passes the NVFP4 architecture check. The 8B NVFP4 reader still needs at least 11 GiB free, so a 12 GB card has little room for OBS and the desktop and is not guaranteed to fit. The 27B reader requires substantially more memory. The 4B NF4 profile is a smaller explicit alternative when at least 6.5 GiB is free.
- **Radeon 9070 XT:** unsupported by these CUDA profiles. The separate [GGUF Vulkan/CPU reader](#qwen-gguf-vulkan-or-cpu) offers a non-CUDA path. PaddleOCR and Tesseract remain CPU alternatives.

The NVIDIA capabilities above are listed in [NVIDIA's GPU table](https://developer.nvidia.com/cuda/gpus). PyTorch ROCm also uses the `torch.cuda` API, so CUDA availability alone does not establish support for this runtime. See [PyTorch HIP semantics](https://docs.pytorch.org/docs/2.14/notes/hip.html#checking-for-hip).

In a 21-frame benchmark, 4B NF4 transcribed 211/252 names exactly, 83.7%, across 21 saved frames and 24 recurring identities. The installed production worker reproduced that result. Its 4.83 GiB process peak was slightly above the earlier research runtime's 4.59 GiB, which is why the selector uses the production measurement. No frame had all twelve names exactly correct. The corpus includes Greek-letter and punctuation-heavy names but no exact Japanese-name ground truth. Results still require review and explicit identity confirmation.

### Qwen GGUF: Vulkan or CPU

**Qwen3-VL-4B GGUF Q4_K_M** (`qwen-4b-gguf`) is the recommended Qwen starting point for broader compatibility and a smaller download. It uses llama.cpp rather than Transformers/PyTorch. The setup helper downloads the model and checksum-pinned runtime; users do not need to build llama.cpp or install CUDA for this reader.

After capture setup, install **one** backend from the repository root:

```sh
.venv/bin/python tools/setup_neural.py --engine qwen-4b-gguf --gguf-backend vulkan
```

For CPU instead, run the same command with `--gguf-backend cpu`. On Windows use `.\.venv\Scripts\python.exe` instead of `.venv/bin/python`. The guided `tools/setup.py` wrapper accepts the same backend flag alongside `--reader qwen-4b-gguf`. Explicit setup defaults to CPU; the interactive chooser asks for Vulkan or CPU.

The model comes from [Qwen's official GGUF repository](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/tree/1cd86afb9a95c410a6038ab3b40d8b578c892266), pinned to revision `1cd86afb9a95c410a6038ab3b40d8b578c892266`. Setup uses `Qwen3VL-4B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen3VL-4B-Instruct-F16.gguf`, approximately **3.33 GB / 3.10 GiB** together. These are prequantized weights plus the vision projector, unlike the NF4 route's floating-point source download.

Runtime archives are pinned to [llama.cpp b12110](https://github.com/ggml-org/llama.cpp/releases/tag/b12110), verified by hashes, and stored under `.local/`. Automated packages cover x64 native Windows and modern glibc Linux only. Vulkan uses the installed vendor GPU driver; see [upstream Vulkan build/runtime notes](https://github.com/ggml-org/llama.cpp/blob/v0.9.0/docs/build.md#vulkan). macOS, ARM, and musl Linux are not covered by this binary installer.

Select **Qwen3-VL-4B GGUF Q4_K_M** explicitly for live capture or imports; Automatic is unchanged. Stop capture/imports before rerunning setup to change backend. The runtime does not download missing components or switch Vulkan/CPU automatically during capture.

NF4 remains a supported NVIDIA CUDA alternative; existing users do not need to switch. This is the same base 4B model as NF4 but a different quantization and image/inference pipeline. **No measured NF4 accuracy parity, minimum RAM/VRAM, or live throughput is claimed.** Try recording imports first, especially on CPU; model file size alone does not establish working-memory requirements.

#### Quick NF4 versus Q4_K_M check

Both production readers received identical, unmodified 398×70 PNG row crops: 24 gameplay crops from two existing upstream finishing-table fixtures (the same 12-player roster in different orders), plus 12 generated Noto Sans CJK names with known text, covering Japanese, Latin, Greek, digits and symbols. Original fixtures were read-only; screenshots, crops, player names and raw predictions remain ignored local evidence, not bundled source.

| Check | NF4 | GGUF Q4_K_M |
| --- | --- | --- |
| Exact matches on 12 synthetic names | **10/12** | **11/12** |
| Visual spelling on 16 clearly readable real crops | **16/16** | **16/16** |
| Agreement between readers across all real crops | **24/24 identical outputs** | **24/24 identical outputs** |

Eight real crops were excluded from strict visual scoring because a stylized epsilon/e, l/I, or star codepoint could not be resolved from pixels alone. Model agreement is not independent accuracy or verified Unicode identity. Across all 36 inputs the readers agreed on 35 outputs. Both replaced a synthetic Greek `ε` with Latin `e`; NF4 additionally read a synthetic `ぱ` as `ば`, which GGUF read correctly. **This quick check found no evident GGUF quality drop, but cannot establish statistical parity or superiority.** It covers few distinct players and clean synthetic text, not broad gameplay conditions.

NF4 ran on an RTX 5090 with a 6 GiB PyTorch allocator cap and independent VRAM monitoring; GGUF ran on CPU with two threads. Minimum observed free GPU memory was **24.53 GiB** and peak increase over baseline was **4.83 GiB**, with no safety abort or unrelated-process signalling. Inference/cleanup ran in a verified separate PID namespace. These different backends are not a speed or VRAM-efficiency comparison. The test compares the complete installed readers, including their image-processing pipelines—not quantization algorithms in isolation.

**Why is the GGUF download smaller?** Header inspection found that the NF4 route's two source checkpoint files total **8,875,719,344 bytes (8.876 GB)**, with every tensor payload stored as BF16. Eligible weights become NF4 during loading; the disk checkpoint remains BF16. This is the [documented bitsandbytes loading workflow](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/reference/nn/linear4bit). GGUF instead ships **2,497,281,664 bytes (2.497 GB)** of prequantized language weights plus **836,180,256 bytes (0.836 GB)** for the F16 vision projector: **3.333 GB total**. Q4_K_M also mixes tensor precisions rather than storing every tensor at exactly four bits. Download size is therefore not evidence that it uses proportionally less VRAM or loses proportionally more quality than NF4.

### PaddleOCR CPU reader

```sh
.venv/bin/python tools/setup_neural.py --engine paddle
```

PaddleOCR uses the PP-OCRv5 server recognizer with an approximately 81 MB model. Choose **PaddleOCR** in Capture & review or **Paddle** in the video import form. Small kana and symbols can still be misread.

### Tesseract

The [local capture setup](../getting-started.md) installs English/Japanese OCR data for Tesseract. Choose **Tesseract** in either form to use it explicitly. It does not require a GPU or either neural model.

## Courses and player names

Course recognition uses the supplied 40-course vocabulary. Unique partial reads can resolve to canonical names; ambiguous numbered variants stay unresolved for correction. Player names do not use that vocabulary and are never silently merged.

Every reader can misread names or visually similar symbols. Generative transcription has no calibrated confidence score, so its confidence is labeled unavailable. Correct the name and select the intended existing player in the review. See [reviewing results](user-guide.md#review-captured-results), including [player disconnects](user-guide.md#player-disconnects).

## Supported frames and sampling

Recognition expects an unobstructed, uncropped **16:9 game image**. The supported 12-player layouts include neutral results tables and in-game team-colored panels with dark result text.

The strict full-table reader requires all 12 finishing positions and initial finishing-phase `+points` evidence. A separate manual-review fallback can read shifted partial tables with at least three coherent rank/`+points` anchors. Each returned position must independently match a rank glyph; names are cropped at the observed row, not an assumed full-table slot. Missing rows stay blank. A fuller single frame may upgrade the same pending review without replacing manual edits. Neither path accepts reordered standings, consumed point animations, team summaries or gameplay as finishing order. The conservative partial guard may reject team panels whose ranks are exclusively dark.

Live capture targets a 250 ms sampling interval. Screenshot acquisition and recognition can make the actual cycle longer, so a short finishing table may be missed. Video imports default to 0.5-second sampling. A missed table can require manual result entry.

Live OBS screenshots use native-resolution JPEG at quality 95. Evidence is saved as PNG, but this does not undo JPEG compression.

The Capture status line shows acquisition time, recognition time, estimated cycle time, and the requested interval. Acquisition includes OBS recording-status queries, screenshot transfer, and decoding. Recognition includes OCR and evidence writes. The 250 ms target is a minimum interval, not a guarantee of four samples per second. Each sample finishes before the next begins.

For identity review and suspected screenshots, see the [user guide](user-guide.md#review-captured-results).


## External vision protocol

The optional `external-vision` reader uses direct standard-library HTTP(S), independently of the tracker-managed GGUF worker. Configure its base URL ending `/v1` (automatically extended to `/chat/completions`) or full endpoint URL and model in Settings. It sends one PNG data URL plus a short transcription instruction in a user message to the chat-completions endpoint, with `stream: false`, `temperature: 0` and `max_tokens: 96`. The optional API key is a Bearer header. It does not query model lists or health routes, follow redirects, use environment proxies or own a server process.

The response must contain exactly one choice, `finish_reason: "stop"`, and nonempty string `message.content` of at most 200 characters without control characters. Confidence is unavailable. Unsupported parameters, text-only models, incomplete output and malformed responses fail closed rather than silently retrying. OpenAI-compatible servers vary; this is a bounded supported protocol, not support for every provider or model.

[llama.cpp's multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md) requires a supported model and projector for image input. [OpenAI's image-input documentation](https://developers.openai.com/api/docs/guides/images-vision) describes base64 image URLs and image token costs. Model/API naming alone does not establish vision support. Test connection generates a fresh random word only in the image, then checks the transcription; even a successful test does not establish accuracy on gameplay names.

Numeric-IP destinations do not use DNS. HTTPS hostnames other than pinned localhost always require disclosure/cost consent, including LAN names. Every DNS answer must be a permitted global/RFC1918/ULA peer; a mixed safe/unsafe answer fails closed. The connection uses one checked numeric answer with the original hostname for TLS SNI/certificate verification and the HTTP Host header, without resolving again. DNS changes cannot bypass these peer restrictions or TLS hostname checks. No address fallback or request retry is performed.

IPv4-mapped, scoped, unspecified, multicast, link-local, reserved and other non-global/non-LAN addresses are rejected. Literal loopback and `localhost` (TCP pinned to `127.0.0.1`, retaining `localhost` for TLS/Host) are permitted, but DNS names resolving to loopback are not. HTTP is limited to numeric loopback/RFC1918/ULA; hostnames require verified HTTPS. IP-based HTTPS certificates must include the IP. Do not disable certificate verification. DNS, connect, TLS and response share the 30-second deadline; TCP connect and TLS handshake each have an additional two-second timeout. OS DNS calls cannot be forcibly cancelled; only one resolver thread may remain outstanding, and later hostname attempts fail while it is busy. No image or key is sent by that thread.

See [configuration, privacy and actual request limits](../recognition.md#external-vision-endpoint). There are at most twelve sequential requests per recognition attempt, not once per race. Startup performs no synthetic warmup transmission. Configuration revisions revoke in-flight readers on edits or key forgetting; a stale browser save cannot restore a forgotten key. Cancellation closes local request I/O, not remote computation. Unique exact-name matching and normal Mogi confirmation are unchanged.
