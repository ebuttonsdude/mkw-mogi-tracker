#!/usr/bin/env bash
# Watson is a shell executor. Install and test only inside a disposable container.
set -euo pipefail

case "${1:-}" in
  checks|browser) job=$1 ;;
  *) echo 'Usage: tools/run_ci.sh checks|browser' >&2; exit 2 ;;
esac
runner=${CI_RUNNER_DESCRIPTION:-}
if [[ ${runner,,} != watson ]]; then
  echo 'This pipeline requires the watson runner' >&2
  exit 1
fi
cd "$(git rev-parse --show-toplevel)"
image=$(bash tools/ci_image.sh resolve)

run_container() {
  # Stream tracked source only. No host directories, credentials, devices or sockets are mounted.
  git archive HEAD | docker run --rm -i \
    --shm-size=256m \
    --env PIP_DISABLE_PIP_VERSION_CHECK=1 \
    --env PIP_NO_CACHE_DIR=1 \
    "$image" bash -c '
      set -euo pipefail
      mkdir /tmp/project
      cd /tmp/project
      tar -xf -
      [[ $$ == 1 && -f /.dockerenv ]] || exit 1
      ln -s /opt/ci/venv .venv
      mkdir -p .local
      cp -a /opt/ci/tessdata .local/tessdata
      cp -a /opt/ci/docs-cache .local/docs-cache
      exec bash tools/ci_container.sh "$1"
    ' ci-container "$job"
}

if [[ $job == checks ]]; then
  mkdir -p dist
  # Extract as the runner user, not container root. pipefail keeps test/build errors fatal.
  run_container | tar -xf - -C dist --no-same-owner --no-same-permissions
else
  run_container
fi
