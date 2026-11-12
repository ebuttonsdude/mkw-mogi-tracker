---
title: "Windows compatibility reference"
weight: 30
---

# Windows compatibility reference

Use the [step-by-step Windows guide](../install/windows.md) for ordinary installation. Windows uses the same local browser dashboard; it is not a separate desktop UI or a packaged executable.

## Compatibility evidence

The tracker has been tested on **Windows 10 and Windows 11**. Windows is tested occasionally; **Arch Linux is the main tested platform**. Automated compatibility checks cover startup, manual scoring, Mogi/WW sessions, Unicode paths, uploads, private credentials and PNG exports. Chromium checks exercise dashboard and overlay behavior with mocked inputs.

| Feature | Platform details |
| --- | --- |
| Manual scoring | Standard-library Python; no GPU required. |
| OBS screenshots | Connect through the local OBS WebSocket server. |
| PaddleOCR / Qwen NF4 | CPU reader or compatible NVIDIA CUDA GPU respectively. |
| Qwen GGUF | Separate x64 Windows CPU/Vulkan runtime; no CUDA/PyTorch required. |
| NVFP4 | Both profiles require Linux and supported NVIDIA Blackwell hardware; native Windows setup rejects them. |
| Data and credentials | Use local NTFS storage. Unsupported ACL or file-identity operations fail closed. |

## Python and paths

Double-click `setupWindows.bat` to open the complete reader chooser, then use `startWindows.bat` for everyday startup. The setup wrapper uses uv to select Python 3.12; its direct command is:

```powershell
uv run --no-project --python 3.12 python tools/setup.py
```

For manual setup:

```powershell
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\python.exe -m mkw_tracker
```

Run these from your checkout. Windows virtualenv executables live under `Scripts`, not `bin`. No activation or PowerShell execution-policy change is required. Never reuse a Linux virtualenv on Windows.

## Custom data and cache locations

For a custom data directory, use an actual writable folder on your machine. This example creates data under your home folder and uses a different port:

```powershell
.\.venv\Scripts\python.exe -m mkw_tracker --data-dir "$HOME\MKW-Tracker-Data" --port 8766
```

Open `http://127.0.0.1:8766`. Do not run two instances against the same data directory.

For a large model installation, uv and model libraries also use caches. You can set `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `HF_HOME`, and `TORCH_HOME` to suitable folders before setup. Those are optional space-management settings, not application prerequisites. Do not publish personal cache paths in bug reports.

## Tesseract

Install from the [Windows distributor linked by the project](https://github.com/UB-Mannheim/tesseract/wiki). Use a dedicated installation folder. Its uninstaller may remove the installation directory, so do not choose a folder containing unrelated files.

Setup and recognition prefer Tesseract on PATH, then check the default per-user and Program Files installation folders. No PATH change is needed for those defaults. For a custom location, use the [persistent user-PATH steps](../install/windows.md#tesseract-is-missing), or add it to the current PowerShell session and launch the tracker from that same session:

```powershell
$env:PATH = "$env:ProgramFiles\Tesseract-OCR;" + $env:PATH
Get-Command tesseract
tesseract --version
.\startWindows.bat
```

Change the path if your installation is elsewhere. A temporary PowerShell PATH change is not inherited by a later File Explorer double-click. Do not assume an installer's custom-location flag was honored; check the actual files.

The tracker downloads its checksum-verified English/Japanese reading data separately. See [manual capture setup](installation.md#add-capture-and-reports).

## Fonts and reports

The app searches Windows system/user font directories and its supported local Noto locations. Candidates include static Noto CJK JP and suitable installed Japanese fonts. A discovered font still needs to cover the characters in the report.

Use Windows Optional features to install Japanese supplemental fonts, or provide a static Noto font. See [explicit font paths](installation.md#report-fonts). Report generation never downloads fonts.

## OBS and recordings

Use `ws://127.0.0.1:4455` with the actual port from OBS. Saved OBS credentials use a separate private file with a protected owner-only DACL. Leave the password blank to reuse it; **Forget saved password** removes it and takes precedence over an in-flight save.

Recording paths use native Windows syntax. Import-by-path reads the original without copying or deleting it. Browser uploads create app-managed copies. Cleanup removes only those copies after confirmation.

## WSL2 alternative

The maintainer validated **WSL2 with mirrored networking**, Windows OBS, and an NVIDIA GPU using **Qwen3-VL-4B NF4**. This does not validate NVFP4 in WSL. Native Windows remains a separate route that does not need WSL.

1. On **Windows 11 version 22H2 or later**, open PowerShell as administrator and run `wsl --install -d Ubuntu`.
2. In **WSL Settings → Networking**, choose **Mirrored**, apply the change, allow WSL to restart, and reboot Windows.
3. Open Ubuntu and install dependencies and the tracker using the [complete validated WSL guide](../install/wsl.md#2-install-dependencies-in-ubuntu).
4. Open Windows OBS and enable **Tools → WebSocket Server Settings** with a password.
5. Start the tracker in Ubuntu. Open **http://127.0.0.1:8765** in the Windows browser and use **ws://127.0.0.1:4455** for OBS. Match your source name exactly, select your installed reader and start capture.
6. Add the OBS Browser Source at **http://127.0.0.1:8765/overlay**, initially **600 × 1000** or your preferred size.

[Mirrored networking](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking) allows Windows↔WSL localhost connections; use IPv4 **127.0.0.1**, not `::1`. Keep the checkout in the Linux home directory. Recording paths use Linux syntax such as `/mnt/c/Users/your-name/Videos/race.mp4`.

## Running compatibility checks

Use disposable data and mocked capture inputs. Read the process-safety requirements in [AGENTS.md](../../AGENTS.md) before running tests. Tests involving termination, process groups, fake PIDs, or signal failure paths require a verified separate PID namespace/container/VM, not merely a different virtualenv or terminal.

See [Contributing](contributing.md) for commands and validation boundaries. Never run compatibility checks against a user's active tracker database, OBS, camera, or GPU recognizer.
