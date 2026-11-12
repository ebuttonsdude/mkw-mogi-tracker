---
title: "WSL on Windows"
weight: 3
---

# WSL on Windows

WSL2 runs the Linux tracker alongside Windows OBS. **Mirrored networking and the NVIDIA Qwen3-VL-4B NF4 reader have been validated by the maintainer.** NVFP4 has not been validated in WSL.

Use **Windows 11 version 22H2 or later** for [mirrored networking](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking). Native [Windows setup](windows.md) is also available.

## 1. Install WSL and enable mirrored networking

Open **PowerShell as administrator** and run:

```powershell
wsl --install -d Ubuntu
```

1. Open **WSL Settings → Networking**.
2. Set **Networking Mode → Mirrored**.
3. Apply changes and allow WSL to restart, then reboot Windows.
4. Open **Ubuntu** and complete its username/password setup.

If Ubuntu is missing, open a regular terminal and run `wsl --install Ubuntu`. See [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install) for installation help.

## 2. Install dependencies in Ubuntu

Run these in the **Ubuntu terminal**, not PowerShell:

```sh
sudo apt update
sudo apt install git python3 python3-venv curl
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install tesseract-ocr fonts-noto-cjk
```

Close and reopen Ubuntu so `uv` is available.

## 3. Set up the tracker

Keep the project in your Linux home folder, not under `/mnt/c`.

```sh
git clone https://gitlab.sandal.tech/ebuttonsdude/mkw-mogi-tracker.git
cd mkw-mogi-tracker
uv run --no-project --python 3.12 python tools/setup.py
```

Choose a [name reader](../recognition.md). The maintainer's validated NVIDIA setup used **Qwen3-VL-4B NF4**; this needs compatible NVIDIA hardware and sufficient free GPU memory. WSL does not imply that NVFP4 is supported.

## 4. Connect Windows OBS

1. Open OBS on Windows and add your capture source; for example, name it **CapCard**.
2. Open **Tools → WebSocket Server Settings**, enable the server and set a password.
3. Allow OBS through the firewall if Windows prompts.
4. Start the tracker from its folder in Ubuntu:

```sh
sh startLinux.sh
```

Open **http://127.0.0.1:8765** in your Windows browser.

In **Capture & review**, select **OBS WebSocket screenshots**, enter the exact source name and set the WebSocket URL to **ws://127.0.0.1:4455**. Enter your OBS password and select your installed name reader. Create a session, then click **Start capture**.

**Checkpoint:** the status shows `running: Reading CapCard` (or your source name).

Mirrored networking permits localhost connections between Windows and WSL. Use **127.0.0.1**, not IPv6 `::1`, for these connections.

## 5. Add the overlay

In Windows OBS, add a **Browser Source** using **http://127.0.0.1:8765/overlay**. Start with **600 × 1000**, or your preferred size.

Use Linux paths when importing recordings: `/mnt/c/Users/your-name/Videos/race.mp4`, not a Windows drive path.

**Next:** [Your first session](../user-guide.md).
