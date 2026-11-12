#!/usr/bin/env python3
"""Install optional capture dependencies/data inside this checkout."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mkw_tracker.neural_ocr import venv_executable  # noqa: E402
from mkw_tracker.tesseract import tesseract_executable  # noqa: E402

MODELS = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "jpn": "1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d",
}


def main():
    os.chdir(ROOT)
    env = ROOT / ".venv"
    uv = shutil.which("uv")
    if not env.exists():
        command = [uv, "venv", "--python", sys.executable, "--system-site-packages", str(env)] if uv else [sys.executable, "-m", "venv", "--system-site-packages", str(env)]
        subprocess.run(command, check=True)
    python = str(venv_executable(env, 'python'))
    packages = ["websocket-client==1.9.2"]
    if subprocess.run([python, "-c", "import cv2, numpy"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        packages.append("opencv-python-headless>=4.10,<6")
    if subprocess.run([python, "-c", "import PIL"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        packages.append("Pillow>=10.4,<14")
    install = [uv, "pip", "install", "--python", python] if uv else [python, "-m", "pip", "install"]
    subprocess.run(install + packages, check=True)
    if not tesseract_executable():
        raise SystemExit("Tesseract executable is missing. On Windows install Tesseract OCR in its "
                         "default installation folder, or add a custom installation folder to PATH, then rerun. "
                         "On Arch install tesseract. See docs/windows-setup.md for Windows setup.")
    destination = ROOT / ".local" / "tessdata"
    destination.mkdir(parents=True, exist_ok=True)
    for language, checksum in MODELS.items():
        target = destination / f"{language}.traineddata"
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == checksum:
            continue
        request = urllib.request.Request(f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/{language}.traineddata", headers={"User-Agent": "mkw-tracker-setup"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(20 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise SystemExit(f"Unexpected {language} model checksum; nothing written. Review upstream data before updating the checksum.")
        target.write_bytes(data)
    print("Capture dependencies and English/Japanese OCR data ready in .venv and .local/tessdata.")
    print(f'Run: {venv_executable(".venv", "python")} -m mkw_tracker')
    print('Choose your capture input and OBS connection in the dashboard under Capture & review.')


if __name__ == "__main__":
    main()
