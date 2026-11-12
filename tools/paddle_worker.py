"""Compatibility entry point for the packaged Paddle name worker."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mkw_tracker.workers.paddle_worker import (  # noqa: E402, F401
    MAX_IMAGE_BYTES, MAX_REQUEST_BYTES, RecognitionWorker, main, serve,
)

if __name__ == '__main__':
    main()
