"""Find Tesseract without requiring Windows users to edit PATH."""

import os
from pathlib import Path
import shutil
import sys


def tesseract_executable():
    """Prefer PATH, then the current-user and system Windows install defaults."""
    executable = shutil.which('tesseract')
    if executable or sys.platform != 'win32':
        return executable
    for variable, directory in (
            ('LOCALAPPDATA', 'Programs/Tesseract-OCR'),
            ('ProgramW6432', 'Tesseract-OCR'),
            ('ProgramFiles', 'Tesseract-OCR')):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / directory / 'tesseract.exe'
            if candidate.is_file():
                return str(candidate)
    return None
