#!/usr/bin/env bash
# Invoked by run_ci.sh inside a disposable Python Bookworm container, never on the host.
set -euo pipefail
[[ -f /.dockerenv && $PWD == /tmp/project ]] || {
  echo 'Run this script through tools/run_ci.sh, not on the host' >&2
  exit 1
}
case "${1:-}" in
  checks|browser) job=$1 ;;
  *) echo 'Expected checks or browser' >&2; exit 2 ;;
esac
# Keep stdout exclusively for the checked package tar stream.
exec 3>&1 1>&2
export PLAYWRIGHT_BROWSERS_PATH=/opt/ci/browsers
if [[ $job == checks ]]; then
  .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
  .venv/bin/python -m ruff check mkw_tracker tests tools
  node --check mkw_tracker/static/app.js
  node --check mkw_tracker/static/overlay.js
  node --check mkw_tracker/static/overlay-profiles.js
  node --check mkw_tracker/static/ww.js
  node --check mkw_tracker/static/fonts.js
  .venv/bin/python -m compileall -q mkw_tracker tests tools
  .venv/bin/python -m build
  .venv/bin/python tools/check_package.py dist
  cd dist
  tar -cf - mkw_mogi_tracker-*.whl mkw_mogi_tracker-*.tar.gz >&3
else
  .venv/bin/python -W error::ResourceWarning -m tests.browser_discord
  .venv/bin/python -W error::ResourceWarning -m tests.browser_smoke
  .venv/bin/python -W error::ResourceWarning -m tests.browser_overlay
  .venv/bin/python -W error::ResourceWarning -m tests.browser_overlay_motion
  .venv/bin/python -W error::ResourceWarning -m tests.browser_overlay_profiles
  .venv/bin/python -W error::ResourceWarning -m tests.browser_lounge_cards
  .venv/bin/python -W error::ResourceWarning -m tests.browser_lounge_size
  .venv/bin/python -W error::ResourceWarning -m tests.browser_overlay_workspace
  .venv/bin/python -W error::ResourceWarning -m tests.browser_overlay_backgrounds
  .venv/bin/python -W error::ResourceWarning -m tests.browser_evidence_order
  .venv/bin/python -W error::ResourceWarning -m tests.browser_course_images
  .venv/bin/python -W error::ResourceWarning -m tests.browser_settings_layout
  .venv/bin/python -W error::ResourceWarning -m tests.browser_fonts
  .venv/bin/python -W error::ResourceWarning -m tests.browser_backups
  .venv/bin/python -W error::ResourceWarning -m tests.browser_yolo
  .venv/bin/python -W error::ResourceWarning -m tests.browser_partial_review
  .venv/bin/python -W error::ResourceWarning -m tests.browser_external_vision
  .venv/bin/python -W error::ResourceWarning -m tests.browser_ffa
  .venv/bin/python -W error::ResourceWarning -m tests.browser_custom_themes
  .venv/bin/python -W error::ResourceWarning -m tests.browser_ww
  .venv/bin/python tools/build_docs.py --base-url http://127.0.0.1:1313/
  .venv/bin/python -W error::ResourceWarning -m tests.browser_docs
fi
