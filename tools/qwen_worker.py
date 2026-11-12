"""Compatibility entry point for the packaged Qwen name worker."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mkw_tracker.workers.qwen_worker import QwenWorker, main  # noqa: E402, F401

if __name__ == '__main__':
    main()
