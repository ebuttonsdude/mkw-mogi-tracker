---
title: "Get started on Windows"
weight: 2
---

# Get started on Windows

**Recommended bundle:** installs **PaddleOCR** and **Qwen3-VL-4B GGUF Q4_K_M** together. Choose Vulkan or CPU when asked. After both install successfully, setup offers to make Q4_K_M your default; declining keeps your existing default. PaddleOCR remains a separately selectable alternative, not a silent fallback.


**Set up once, then double-click `startWindows.bat` whenever you want to use the tracker.** It runs directly on Windows and opens a dashboard in your browser. You do not need WSL or a separate Python installation.

A name reader is optional, but highly recommended for captured Mogi results. Qwen is recommended for more accurate name reading, especially names containing symbols or Japanese characters. PaddleOCR is the lighter CPU option when your hardware cannot comfortably run Qwen. Neither replaces checking names against the screenshot. **For Qwen, we recommend Qwen3-VL-4B GGUF Q4_K_M**: Vulkan works with compatible AMD/Intel/NVIDIA GPUs, CPU mode is available, and the model download is smaller than our NF4 route. Choose **PaddleOCR** for the smallest CPU download. You can also keep basic Tesseract reading or score manually.

Windows 10 and Windows 11 have been tested. Windows is tested occasionally; Arch Linux is the main tested platform. Both NVFP4 readers are Linux-only.

## 1. Download the tracker


**Recommended: use Git so future updates take one command.**

1. Install [Git for Windows](https://gitforwindows.org/) with its default options.
2. Open a new **PowerShell** window from the Windows Start menu.
3. Copy and paste each line below, then press **Enter**. Wait until the prompt—the line starting with `PS`—comes back before entering the next line.

```powershell
cd "$HOME"
git clone "https://gitlab.sandal.tech/ebuttonsdude/mkw-mogi-tracker.git"
```

If the download fails, check the URL and the exact error. Do not put passwords or tokens in commands, screenshots, or bug reports.

**Checkpoint:** your Windows user folder now contains **mkw-mogi-tracker**. You only clone once.

<details>
<summary><strong>Alternative: download a ZIP without Git</strong></summary>

1. [Download the tracker ZIP](https://gitlab.sandal.tech/ebuttonsdude/mkw-mogi-tracker/-/archive/main/mkw-mogi-tracker-main.zip).
2. Right-click the downloaded file and select **Extract All…**, then **Extract**.
3. Open the extracted **mkw-mogi-tracker-main** folder. If it contains another folder with that name, open that one too.

The correct folder contains **README.md**, **setupWindows.bat**, **startWindows.bat**, and **mkw_tracker**. Do not run the scripts from inside the ZIP. ZIP downloads need manual updates; they cannot use `git pull`.

</details>

## 2. Install the setup tool and Tesseract

**uv** downloads the correct Python version and prepares the tracker's packages. In PowerShell, run:

```powershell
winget install --id astral-sh.uv -e
```

Wait for installation to finish, then close PowerShell. If Windows cannot find `winget`, follow [uv's official installation instructions](https://docs.astral.sh/uv/getting-started/installation/) instead. The tracker uses [uv's automatic Python download](https://docs.astral.sh/uv/guides/install-python/) to obtain Python 3.12 if needed.

Next, install **Tesseract**, which reads parts of the game image even when you choose a neural name reader:

1. Open the [Tesseract for Windows download page](https://github.com/UB-Mannheim/tesseract/wiki).
2. Download and run its **64-bit installer**.
3. Keep its suggested installation folder. Use a dedicated folder, not one containing unrelated files; its uninstaller may remove that folder.

The tracker finds Tesseract on PATH or in its usual Windows installation folders. **You do not need to edit PATH for a default installation.** A custom folder needs the [custom-path steps](#tesseract-is-missing).

**Only entering scores by hand?** You can skip Tesseract and use the [manual-only setup](#manual-scoring-only) below.

## 3. Run setup and choose a reader

1. Open your tracker folder in **File Explorer**.
2. Double-click **setupWindows.bat**. A setup window opens.
3. Choose **Install reader / configure capture**, then choose a reader or the **Recommended** bundle.
4. For Q4_K_M, choose **Vulkan GPU** or **CPU** when prompted.
5. Wait for installation to finish, then accept or decline the offer to set the installed reader as your default.

The **Recommended** bundle installs PaddleOCR and Q4_K_M, then offers Q4_K_M as default. Compare [reader choices and memory use](../recognition.md#all-available-models) before selecting larger models.

VRAM is your graphics card's memory. **Free memory matters:** OBS, games, and other apps use some too. The NF4, BF16, INT8, and NVFP4 installers use NVIDIA CUDA; the separate **GGUF** choice can use Vulkan on compatible AMD/Intel/NVIDIA GPUs, or the CPU. Both NVFP4 choices stop before installation on native Windows. See [reader details](../recognition.md) before choosing a larger model.

**Recommended GGUF setup:** choose **Vulkan GPU** if your GPU is Vulkan compatible, or **CPU** if you do not want GPU acceleration (may be very slow).

Setup installs into **.venv** and **.local** in your tracker folder; download tools may also use caches elsewhere. It does not install system packages, change saved sessions, start OBS, or start capture. You do not need administrator mode, environment activation, or a PowerShell execution-policy change for the tracker scripts.

**Checkpoint:** look for **Setup complete**. If setup reports an error instead, keep the message and use [the fixes below](#if-something-gets-stuck). Do not assume a partial download means setup finished.

## 4. Start the tracker

1. Double-click **startWindows.bat** in File Explorer.
2. Wait for this message in the tracker window:

```text
Race tracker: http://127.0.0.1:8765
```

3. Open **[the dashboard](http://127.0.0.1:8765)** in your browser on the same PC. If the link does not work, paste `http://127.0.0.1:8765` into the browser's address bar.

**Keep the tracker window open.** You can minimise it. Closing the browser does not stop the tracker.

## 5. Select your reader and capture input

If you accepted setup’s default-reader offer, the app starts with that choice. Otherwise, select the installed reader below; you can also change it for a particular capture or import.

- **Live from OBS:** follow [Read results from OBS](../obs.md#read-results-from-obs). In **Capture & review → Name recognition**, select the reader you installed.
- **Saved video:** open **Videos → Import video**, choose the matching **Name reader**, and follow [Import a recording](../user-guide.md#import-a-recording).
- **Skip for now:** choose **Tesseract** for reading, or [enter races manually](../user-guide.md#enter-a-race-manually).

**Set your Qwen reader as default in setup, or select it in the dashboard. Automatic does not choose GGUF, NF4, INT8, or NVFP4.** Worldwides uses its own Tesseract-based reader, not these player-name models.

Model loading may take a while; watch capture status or import progress.

**Mogi points count only after you check the names and finishing order and confirm the result.** Continue with [Your first session](../user-guide.md).

## Opening it next time

Double-click **startWindows.bat**, then open your dashboard bookmark. You do not need setup or a model download again.

**To stop:** stop capture and finish or cancel imports, then click the tracker window and press **Ctrl+C**. Your saved scores remain saved. Do not start a second copy against the same data folder.

**To manage readers later:** stop the tracker and run **setupWindows.bat** again. Choose **Install reader / configure capture**, **Remove reader**, or **Set default reader**. Removing the default requires selecting a replacement first. See [reader management](../recognition.md#install-or-change-a-reader).

## Manual scoring only

This route needs uv, but not Tesseract or model downloads. Open PowerShell and enter your tracker folder. You can copy its path from File Explorer's address bar:

```powershell
cd "C:\Users\yourname\mkw-mogi-tracker"
.\setupWindows.bat --manual-only
```

Replace the example path with your actual folder; a ZIP folder is usually named **mkw-mogi-tracker-main**. Wait for **Setup complete**, then double-click **startWindows.bat**.

Manual-only setup does not install image/capture packages. Run normal setup later to add capture, PNG reports, and uploaded-icon support. **Skip for now** in the normal chooser is different: it still installs those packages and requires Tesseract.

## Updating and keeping your scores safe

Stop the tracker and [back up your data](../data-and-backups.md) before updating. The default data folder is **.local → tracker** inside the tracker folder. A backup can include a saved OBS password; keep it private. Imported original recordings need their own backup.

**Git installation:** open PowerShell in your tracker folder as described above, then run:

```powershell
git pull --ff-only
```

Start with **startWindows.bat** afterward. If an update changes dependencies, rerun **setupWindows.bat** with your reader choice. If Git refuses because of local changes, keep the error; do not delete or reset files to force it through.

**ZIP installation:** download and extract the latest ZIP, then copy its source files into your existing tracker folder. Keep **.local** and **.venv** intact. Do not replace saved data or move an existing virtual environment to a different folder. Follow any dependency-update instructions, then start normally.

## If something gets stuck

### Setup cannot find uv

Finish step 2, close and reopen the setup window, then retry. If needed, sign out of Windows and back in so File Explorer gets the updated PATH. See [uv installation](https://docs.astral.sh/uv/getting-started/installation/) for alternative installation methods. Do not disable Windows security settings to repair a missing command.

### Tesseract is missing

Check where the installer put **tesseract.exe**. The normal system and per-user installation folders are discovered automatically.

For a custom folder, search the Windows Start menu for **Edit environment variables for your account**. Under **User variables**, select **Path → Edit → New**, and add the folder containing **tesseract.exe**, not the executable itself. Keep existing entries. Save, then sign out and back in before retrying **setupWindows.bat** and **startWindows.bat**. A temporary `$env:PATH` change in PowerShell does not carry over to a later File Explorer double-click.

### Setup fails or the GPU reader will not load

Keep the exact error. Check internet access and disk space, then rerun setup with the same choice; completed downloads may be reused. For GPU compatibility or free-memory failures, choose **PaddleOCR** or **Tesseract** rather than lowering safety checks. Selecting NVFP4 on native Windows cannot work, regardless of your GPU.

### The GGUF Vulkan reader fails

Check that your GPU vendor's Vulkan driver is installed and working. Stop capture and rerun **setupWindows.bat**, choose **Install reader / configure capture**, then **Q4_K_M → CPU** to switch backend. The tracker does not silently download another runtime or switch backend during capture. You can also use **PaddleOCR** or **Tesseract**. A model download finishing is not proof of GPU compatibility.

### The start window says the Python environment is missing

Run **setupWindows.bat** first. Check that you extracted the ZIP and are in the folder containing **README.md** and **mkw_tracker**, not its outer folder. Never copy a Linux `.venv` into Windows.

### The dashboard cannot connect or the address is already in use

Leave the tracker window open and read its last error. Check whether you already started the tracker in another window; use that instance rather than opening a second one. Do not close unrelated programs or change firewall settings at random.

### PNG reports cannot display Japanese names

Normal setup includes the image library, but fonts come from Windows. Add **Japanese Supplemental Fonts** in Windows Optional features, or follow the [font troubleshooting steps](../troubleshooting.md#png-export-cannot-find-a-font).

Still stuck? See [Troubleshooting](../troubleshooting.md) and the [Windows compatibility reference](../technical/windows-setup.md). Remove credentials and private paths before sharing errors.
