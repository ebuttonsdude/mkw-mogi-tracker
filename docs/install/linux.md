---
title: "Linux"
weight: 1
---

# Install on Linux

{{< distro-picker >}}
{{< distro "arch" >}}
## Arch Linux

Arch is the primary tested platform.

**1. Open a terminal and install the prerequisites.**

```sh
sudo pacman -S --needed git uv tesseract tesseract-data-eng noto-fonts-cjk
```

{{< /distro >}}
{{< distro "fedora" >}}
## Fedora

Use a standard, DNF-based Fedora installation. This guide does not cover Fedora Atomic editions. The tracker has not been fully tested on Fedora.

**1. Open a terminal and install the prerequisites.**

```sh
sudo dnf install git uv tesseract google-noto-sans-cjk-fonts
```

Fedora packages [uv](https://packages.fedoraproject.org/pkgs/uv/uv/) and [Noto CJK fonts](https://packages.fedoraproject.org/pkgs/google-noto-sans-cjk-fonts/google-noto-sans-cjk-fonts/). If a derivative does not provide `uv`, use its [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

{{< /distro >}}
{{< distro "ubuntu" >}}
## Ubuntu

Use Ubuntu 24.04 or newer. The tracker has not been fully tested on Ubuntu.

**1. Open a terminal and install the prerequisites.**

```sh
sudo apt update
sudo apt install git curl tesseract-ocr fonts-noto-cjk
```

**2. Install uv**, the tool that prepares Python for you. This command downloads and runs the [official uv installer](https://docs.astral.sh/uv/getting-started/installation/).

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen the terminal after it finishes.

{{< /distro >}}
{{< /distro-picker >}}

## Download and set up the tracker

These commands are the same for every distribution above.

```sh
git clone --branch main https://github.com/ebuttonsdude/mkw-mogi-tracker.git
cd mkw-mogi-tracker
sh setupLinux.sh
```

{{< distro-note "fedora" >}}
If PNG export cannot find a font, use the [font fix](../troubleshooting.md#png-export-cannot-find-a-font).
{{< /distro-note >}}

**Recommended bundle:** installs **PaddleOCR** and **Qwen3-VL-4B GGUF Q4_K_M** together. Choose Vulkan or CPU when asked. After both install successfully, setup offers to make Q4_K_M your default; declining keeps your existing default. PaddleOCR remains a separately selectable alternative, not a silent fallback.

## Choose your reader

Choose **Install reader / configure capture**, then a reader or the **Recommended** bundle (PaddleOCR plus Q4_K_M). Qwen is recommended for names containing symbols or Japanese characters; PaddleOCR is the lighter CPU option. Review every captured result regardless of the reader.

For Q4_K_M, choose Vulkan GPU or CPU. After installation succeeds, setup offers to make the reader your default. You can also rerun setup to **Remove reader** or **Set default reader**. See [reader choices and management](../recognition.md).

Setup installs capture/report support without starting the app, OBS, or your camera.

## Start the tracker

From the project folder, run:

**Linux and WSL**

```sh
sh startLinux.sh
```

The launcher checks that setup has created the Python environment and starts from its own project folder. Extra options are forwarded unchanged; for example, `sh startLinux.sh --verbose`.

Open **http://127.0.0.1:8765** in your browser.

**Success looks like:** the dashboard opens with Standings, Capture & review, and the other tabs. Keep the terminal open while you use the app. To close it, stop capture/imports first, then press **Ctrl+C** in that terminal.

## Next time

Open a terminal in the same project folder and run the start command again. You do not need to reinstall.

**Next:** [Run your first session](../user-guide.md). If you selected a GPU reader, remember to select it in the dashboard too.
