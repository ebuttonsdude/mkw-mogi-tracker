# Third-party notices and distribution boundaries

The tracker's original code is licensed as stated in [COPYING.md](COPYING.md).
Dependencies, models and third-party assets retain their own licenses. Their
licenses do not license the tracker, and the tracker license does not relicense
them. This inventory is not a blanket clearance of every optional environment.

## What we distribute

- The Python wheel and source distribution contain tracker source, UI assets,
  recognition profiles and legal notices. They do **not** bundle dependency
  wheels, virtual environments, OCR models, CUDA libraries or downloaded tools.
- The generated documentation website does incorporate Hextra, FlexSearch,
  Tailwind CSS and Heroicons. Its full notices are published under `licenses/`
  and linked from the site's License & notices page.
- Optional setup downloads install dependencies on the user's machine. Keep
  their accompanying licenses, copyright notices and metadata intact.
- Game-derived recognition masks have a separate unresolved rights boundary;
  see [ATTRIBUTION.md](ATTRIBUTION.md). Uploaded images, fonts and recordings
  are user data and are never included in these distributions.

## Optional application libraries

These are direct configured components and a few important native dependencies,
not an exhaustive transitive bill of materials. Versions are controlled by
`pyproject.toml`, `tools/setup_neural.py`, `tools/setup_gguf.py` and
`mkw_tracker/name_engines.py`. Where a range is allowed, the actual installed
artifact's license and notices must also be checked before redistribution.

| Component | License / primary evidence |
| --- | --- |
| websocket-client 1.9.2 | [Apache-2.0](https://raw.githubusercontent.com/websocket-client/websocket-client/v1.9.2/LICENSE) |
| OpenCV >=4.10,<6 | [Apache-2.0](https://opencv.org/LICENSE/); Python packaging scripts MIT; wheels include LGPL-2.1 FFmpeg and other third-party components ([wheel notices](https://github.com/opencv/opencv-python#licensing)) |
| Pillow >=10.4,<14 | [PIL/MIT-CMU permissive license](https://github.com/python-pillow/Pillow/blob/main/LICENSE); bundled native components have additional notices |
| NumPy | [BSD-3-Clause](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| Tesseract and tessdata_fast English/Japanese models | [Engine Apache-2.0](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE), [model-data Apache-2.0](https://github.com/tesseract-ocr/tessdata_fast/blob/main/LICENSE) |
| PaddleOCR / PaddlePaddle | [Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE), [Apache-2.0](https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE) |
| PyTorch / torchvision | [BSD-3-Clause](https://github.com/pytorch/pytorch/blob/main/LICENSE), [BSD-3-Clause](https://github.com/pytorch/vision/blob/main/LICENSE); CUDA/native distributions include separately licensed components |
| Transformers / Accelerate / huggingface-hub / kernels | [Apache-2.0](https://github.com/huggingface/transformers/blob/main/LICENSE), [Apache-2.0](https://github.com/huggingface/accelerate/blob/main/LICENSE), [Apache-2.0](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE), [Apache-2.0](https://github.com/huggingface/kernels/blob/main/LICENSE) |
| bitsandbytes / einops | [MIT](https://github.com/bitsandbytes-foundation/bitsandbytes/blob/main/LICENSE), [MIT](https://github.com/arogozhnikov/einops/blob/main/LICENSE) |
| llama.cpp b12110 | [MIT](https://github.com/ggml-org/llama.cpp/blob/b12110/LICENSE); downloaded archives may include other libraries/notices |
| FlashInfer | [Apache-2.0 and listed third-party components](https://github.com/flashinfer-ai/flashinfer/blob/main/LICENSE) |
| NVIDIA CUDA runtime/driver libraries and cuDNN | Separate NVIDIA terms; consult the actual downloaded distributions and [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) |
| nvidia-cutlass-dsl | [NVIDIA CUTLASS DSL EULA](https://docs.nvidia.com/cutlass/latest/media/docs/pythonDSL/license.html), **not** the BSD license of ordinary CUTLASS |

The optional NVFP4 reader calls CUTLASS-backed FlashInfer code at runtime.
[The limited NVIDIA permission](LICENSES/NVIDIA-Reader-Exception.txt) addresses
the tracker's AGPL obligations for that combination, not NVIDIA's permissions.
The NVIDIA components are not supplied by this project. Users and redistributors
must comply independently with their terms; no unrestricted redistribution of
NVIDIA components is promised.

## Optional models and kernels

The current official model cards identify these configured models as Apache-2.0:
[Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct),
[Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct),
[Qwen3-VL-4B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF),
[Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) and
[PP-OCRv5_server_rec](https://huggingface.co/PaddlePaddle/PP-OCRv5_server_rec).
Review the exact pinned model revision before redistributing weights. The GGUF
setup retains the official Qwen license and llama.cpp license beside downloads.

The `kernels` loader's Apache license does not establish the license of each
downloaded kernel. The exact pinned `kernels-community/nvfp4-gemm` and `fla`
artifacts were not available for license verification in this review.
The current [mamba-ssm card](https://huggingface.co/kernels-community/mamba-ssm)
states Apache-2.0, but the configured artifact was not separately audited.
Do not bundle these artifacts without verifying their own notices and terms.
No model or kernel binary is included in the project's packages.

## Documentation website assets

| Component | Notice and provenance |
| --- | --- |
| Hextra 0.13.0 | MIT; full upstream license copied from the checksum-verified theme archive to `licenses/hextra.txt` ([source](https://github.com/imfing/hextra/blob/v0.13.0/LICENSE)) |
| FlexSearch 0.8.143 | Apache-2.0; full `package/LICENSE` copied from the checksum-verified npm archive to `licenses/flexsearch.txt`; that archive contains no separate NOTICE file ([upstream](https://github.com/nextapps-de/flexsearch)) |
| Tailwind CSS 4.3.0, compiled into Hextra CSS | MIT; full notice retained in [LICENSES/TailwindCSS-MIT.txt](LICENSES/TailwindCSS-MIT.txt), published as `licenses/tailwindcss.txt` ([upstream](https://github.com/tailwindlabs/tailwindcss/blob/v4.3.0/LICENSE)) |
| Heroicons v1 outlines used by Hextra | MIT, Copyright 2020 Refactoring UI Inc.; full notice retained in [LICENSES/Heroicons-MIT.txt](LICENSES/Heroicons-MIT.txt), published as `licenses/heroicons.txt` ([Hextra provenance](https://imfing.github.io/hextra/docs/guide/shortcodes/icon/)) |

Tracker-specific website layouts, styles and scripts modify the presentation
around these upstream assets; the upstream FlexSearch bundle is not modified.
Preserve upstream copyright/license banners and the full notice files when
publishing generated assets. Additional themes, icons, fonts or optional
renderers require their own review; they are not licensed by this inventory.

Build-only tools include [Hugo](https://github.com/gohugoio/hugo/blob/master/LICENSE)
(Apache-2.0), [Playwright](https://github.com/microsoft/playwright-python/blob/main/LICENSE)
(Apache-2.0), [Ruff](https://github.com/astral-sh/ruff/blob/main/LICENSE),
[build](https://github.com/pypa/build/blob/main/LICENSE) and
[setuptools](https://github.com/pypa/setuptools/blob/main/LICENSE) (MIT).
Browsers, OS packages and fonts used for tests have their own notices; they are
not shipped in the tracker packages.

## Before publishing a fork or self-contained installer

1. Retain the tracker license, grant, applicable exception and attribution.
   Clearly identify your modifications and their dates as required by AGPLv3.
2. Offer the corresponding source for the exact version you distribute or
   provide as a modified network service. Update the dashboard's License &
   source page and website Source link to your matching public source; do not
   point only to this upstream repository if your changes are absent there.
3. Preserve the website's full third-party notices and upstream banners.
4. If bundling dependencies, inventory the **actual** binaries, transitive
   libraries and weights. Preserve all required license/copyright/NOTICE files.
   For LGPL components such as wheel-bundled FFmpeg, meet applicable source
   and replacement/relinking requirements. A link to this inventory alone is
   not a substitute. Review proprietary redistribution rights separately.
5. Do not bundle private configuration, user assets or runtime downloads as a
   shortcut. `tools/check_package.py` rejects known runtime/media paths and
   verifies that project legal documents survive wheel/source packaging.

This review establishes the listed notice and packaging measures, not legal
clearance of every transitive dependency, GPU artifact or game-derived asset.
