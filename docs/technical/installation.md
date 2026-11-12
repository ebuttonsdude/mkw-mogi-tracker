---
title: "Manual installation and building"
weight: 10
---

# Manual installation and building

Use [guided setup](../getting-started.md) unless you need control over the environment. Run commands from the repository root.

## Requirements

The core application uses Python **3.11 or newer** and the standard library. The documented setup selects **Python 3.12** for consistency. It needs no compiler to run.

Optional components:

| Feature | Dependencies |
| --- | --- |
| OBS screenshots | `websocket-client`, OpenCV, Tesseract, English/Japanese OCR data |
| Video import | OpenCV, Tesseract, OCR data |
| PNG reports and uploaded icons | Pillow and a supported font |
| Neural readers | Model weights plus a separate Python environment (CUDA/Paddle) or llama.cpp runtime (GGUF); guided setup uses `uv` |
| Documentation website | Hugo and Hextra; not needed by tracker users |

Package ranges and extras are defined in [pyproject.toml](../../pyproject.toml). The setup scripts use their existing pinned reader stacks; do not mix reader environments.

## Guided setup without prompts

```sh
uv run --no-project --python 3.12 python tools/setup.py --reader cpu
```

All reader values: `cpu` / `paddle`, `8gb` / `qwen-4b-nf4`, `nvfp4` / `qwen-nvfp4`, `qwen`, `qwen-int8`, `qwen36-nvfp4`, `qwen-4b-gguf`, and `skip` / `tesseract`. Aliases install the same engine. `skip` still installs capture/report dependencies and basic OCR data. Both NVFP4 profiles require Linux; setup rejects unsupported operating systems before installation.

On Windows, **setupWindows.bat** opens the same chooser and accepts the same flags, for example `.\setupWindows.bat --reader qwen-int8`. Use **startWindows.bat** afterward; these are source-checkout helpers, not standalone installers.

For the **recommended Qwen starting point**, choose the GGUF backend explicitly. It offers broader hardware compatibility and a smaller model download than the NF4 route:

```sh
uv run --no-project --python 3.12 python tools/setup.py --reader qwen-4b-gguf --gguf-backend vulkan
```

Use `--gguf-backend cpu` for CPU inference, or keep Vulkan for a compatible AMD/Intel/NVIDIA GPU. Interactive selection of the GGUF reader asks for a backend; an explicit `--reader qwen-4b-gguf` without the backend flag defaults to CPU. The backend flag applies only to GGUF. Windows accepts the same flags through `.\setupWindows.bat`. See [GGUF runtime requirements](recognition.md#qwen-gguf-vulkan-or-cpu).

Preview without installing or downloading:

```sh
uv run --no-project --python 3.12 python tools/setup.py --reader skip --dry-run
```

For manual scoring only:

```sh
uv run --no-project --python 3.12 python tools/setup.py --manual-only
```

`uv` may need to download Python before it can run the helper, including for a dry run. If you already have Python 3.12, run `python3 tools/setup.py --reader skip --dry-run` directly to preview the helper without asking uv to prepare an interpreter.

## Create an environment manually

```sh
uv venv --python 3.12 --seed .venv
```

Linux/WSL:

```sh
.venv/bin/python -m mkw_tracker
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m mkw_tracker
```

Without uv, use Python's `venv` module. For example, on Linux with Python 3.12 and venv support installed:

```sh
python3.12 -m venv .venv
```

Neural setup still requires uv. Do not copy virtual environments between operating systems.

## Add capture and reports

Install the system Tesseract executable and fonts first; see the platform quick starts.

Linux/WSL:

```sh
.venv/bin/python tools/setup_local.py
```

Windows:

```powershell
.\.venv\Scripts\python.exe tools\setup_local.py
```

This installs missing Python packages and checksum-verified language data. It does not install OS packages or configure OBS.

For reports only, without capture:

```sh
uv pip install --python .venv/bin/python '.[reports]'
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

## Report fonts

Select a font in **Settings → Overlay & PNG font**. System TTF/OTF fonts and files copied into the data folder’s `fonts/` directory are available after **Reload fonts**. The app does not download fonts. See [report_fonts.py](../../mkw_tracker/report_fonts.py) for discovery and Unicode fallback.

If automatic discovery fails, download the static `NotoSansCJKjp-Regular.otf` from the [official Noto CJK repository](https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/Japanese). Choose a local folder and set `MKW_REPORT_FONT` to that actual file path before launching the tracker.

Linux example, after placing the font at this path:

```sh
export MKW_REPORT_FONT="$HOME/.local/share/fonts/NotoSansCJKjp-Regular.otf"
.venv/bin/python -m mkw_tracker
```

PowerShell example, after placing the font at this path:

```powershell
$env:MKW_REPORT_FONT = "$HOME\Fonts\NotoSansCJKjp-Regular.otf"
.\.venv\Scripts\python.exe -m mkw_tracker
```

The UI selection takes precedence over `MKW_REPORT_FONT`. Missing or invalid fonts fall back to available local fonts without discarding names. Install a font covering the required characters to avoid missing-glyph boxes.

## Build a wheel and source archive

This is packaging, not a step that ordinary users need. Python source does not need a separate compilation step.

```sh
uv build
```

Outputs appear under `dist/` as `mkw_mogi_tracker-<version>.whl` and `mkw_mogi_tracker-<version>.tar.gz`. Use a clean output directory when running the package checker, which expects exactly one of each:

```sh
.venv/bin/python tools/check_package.py dist
```

The checker inspects bundled paths and performs a dependency-free installed-wheel smoke check in a temporary environment. It does not prove OBS or GPU inference works.

For test and process-isolation requirements, read [Contributing](contributing.md) and [AGENTS.md](../../AGENTS.md) before running the full suite.

## Other platforms

macOS and container-based application installs are not part of the beginner support targets. Core Python scoring may work, but capture, fonts, GPU readers, and local OBS networking need separate validation. There is no maintained application Docker image.

## Verification boundary

Platform package names were checked against their upstream documentation for this rewrite. These instructions do not represent fresh clean-machine installation tests on every listed operating system. See [platform guides](../install/_index.md) for known gaps.


## Manage installed readers

Run **setupWindows.bat** on Windows or **sh setupLinux.sh** on Linux/WSL.
Both open the same install, remove, and default-reader menu. Install uv first
using your platform guide. Recognized Arch, Fedora, and Ubuntu setups can offer
missing system prerequisites; commands are shown and run only with your approval.

Close the tracker before removing readers. Removal asks for confirmation and,
when removing your default, requires a replacement choice first. Shared model
files, shared environments, standalone llama.cpp runtimes, and external download
caches may remain because other readers can need them.

For scripted management:

```sh
uv run --no-project --python 3.12 python tools/setup.py --action default --reader qwen-4b-gguf
uv run --no-project --python 3.12 python tools/setup.py --action remove --reader qwen-4b-gguf --replacement tesseract
```

Use `--dry-run` to preview. Installing with `--set-default` explicitly chooses the
installed reader for future app launches. Interactive installs offer the same
choice. The preference is checkout-wide in `.local/reader-setup.json`, including
when the tracker uses a custom data directory. Restart the tracker after changing
the default; browser selection remains editable. Automatic is a selection policy,
not an installable model, and its preference order is unchanged.
