FROM python:3.12-bookworm
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ci/browsers
WORKDIR /opt/ci/source
# Context is a tiny allowlisted git archive, not the runner checkout.
COPY . .
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr fonts-noto-cjk nodejs \
    && python -m venv /opt/ci/venv \
    && ln -s /opt/ci/venv .venv \
    && .venv/bin/python tools/setup_local.py \
    && .venv/bin/python -m pip install ruff==0.14.3 build==1.3.0 playwright==1.56.0 \
    && mv .local/tessdata /opt/ci/tessdata \
    && rm -rf /var/lib/apt/lists/*
# Keep completed dependency/browser layers when a docs upstream is unavailable.
RUN .venv/bin/python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
RUN .venv/bin/python -c 'from pathlib import Path; from tools.build_docs import DOWNLOADS, fetch_archive; [fetch_archive(name, Path(".local/docs-cache")) for name in DOWNLOADS]' \
    && mv .local/docs-cache /opt/ci/docs-cache \
    && rm -rf /opt/ci/source
WORKDIR /tmp
