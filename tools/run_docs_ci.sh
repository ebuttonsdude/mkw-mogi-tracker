#!/usr/bin/env bash
# Shell-executor safe: install/build in a disposable container, publish only docs.
set -euo pipefail
: "${CI_PAGES_URL:?GitLab Pages must be configured on this instance}"
# Repository visibility is not Pages access control. Both public projects and
# private projects may publish; GitLab's separate server-side Pages setting
# determines the audience. This script neither changes nor verifies that setting.
case "${CI_PROJECT_VISIBILITY:-}" in
  private|public) ;;
  *) echo 'Expected public or private project visibility; refusing to publish.' >&2; exit 1 ;;
esac
cd "$(git rev-parse --show-toplevel)"
image=$(bash tools/ci_image.sh resolve)

# No host mounts, Docker socket, credentials, hardware, or host PID namespace.
# Host CI credentials stay outside the build container.
mkdir -p public
git archive HEAD | docker run --rm -i \
  --env "DOCS_BASE_URL=${CI_PAGES_URL%/}/" \
  "$image" bash -c '
    set -euo pipefail
    mkdir /tmp/project
    cd /tmp/project
    tar -xf -
    [[ $$ == 1 && -f /.dockerenv ]] || exit 1
    ln -s /opt/ci/venv .venv
    mkdir -p .local
    cp -a /opt/ci/docs-cache .local/docs-cache
    .venv/bin/python tools/build_docs.py --base-url "$DOCS_BASE_URL" --destination /tmp/docs-public >&2
    tar -cf - -C /tmp/docs-public .
  ' | tar -xf - -C public --no-same-owner --no-same-permissions
printf 'Documentation website (%s project; audience controlled by Pages settings): %s\n' \
  "$CI_PROJECT_VISIBILITY" "$CI_PAGES_URL"
