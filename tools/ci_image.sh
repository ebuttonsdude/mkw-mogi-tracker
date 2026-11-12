#!/usr/bin/env bash
# A local-daemon toolchain cache, never a registry image or host dependency install.
set -euo pipefail
runner=${CI_RUNNER_DESCRIPTION:-}
[[ ${runner,,} == watson ]] || {
  echo 'This pipeline requires the watson runner' >&2; exit 1;
}
cd "$(git rev-parse --show-toplevel)"
# These tracked HEAD inputs are also the entire build context. No working-tree
# data, host paths, CI credentials or unrelated app source enter the image.
inputs=(
  tools/ci_image.sh tools/ci.Dockerfile tools/setup_local.py tools/build_docs.py
  pyproject.toml mkw_tracker/__init__.py mkw_tracker/neural_ocr.py
  mkw_tracker/diagnostics.py mkw_tracker/name_engines.py mkw_tracker/tesseract.py
  mkw_tracker/themes.py
)
for input in "${inputs[@]}"; do
  git cat-file -e "HEAD:$input" || {
    echo "Required toolchain input missing from HEAD: $input" >&2; exit 1;
  }
done
key=$(git ls-tree HEAD -- "${inputs[@]}" | sha256sum | cut -d ' ' -f 1)
tag="mkw-tracker-ci:$key"
label=org.mkw-tracker.ci-toolchain

validate_image() {
  local identity=$1 actual
  [[ $identity =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  actual=$(docker image inspect --format "{{.Id}} {{index .Config.Labels \"$label\"}}" "$identity") || return 1
  [[ $actual == "$identity $key" ]]
}

case "${1:-}" in
  prepare)
    identity=$(docker image inspect --format '{{.Id}}' "$tag" 2>/dev/null) || identity=
    if [[ ${CI_TOOLCHAIN_REBUILD:-0} == 1 ]] || ! validate_image "$identity"; then
      flags=()
      if [[ ${CI_TOOLCHAIN_REBUILD:-0} == 1 ]]; then
        # Refresh this image only; never prune the shared runner daemon.
        flags=(--pull --no-cache)
      fi
      echo "Building local CI toolchain $tag" >&2
      iidfile=$(mktemp)
      trap 'rm -f -- "$iidfile"' EXIT
      git archive HEAD -- "${inputs[@]}" | docker build "${flags[@]}" \
        --label "$label=$key" --tag "$tag" --iidfile "$iidfile" --file tools/ci.Dockerfile - >&2
      # A concurrent forced rebuild may move the tag; use this build's own ID.
      identity=$(cat "$iidfile")
    else
      echo "Reusing local CI toolchain $identity" >&2
    fi
    validate_image "$identity" || { echo 'Toolchain identity/label mismatch' >&2; exit 1; }
    printf 'CI_TOOLCHAIN_IMAGE=%s\n' "$identity"
    ;;
  resolve)
    identity=${CI_TOOLCHAIN_IMAGE:-}
    validate_image "$identity" || {
      echo 'Missing or mismatched prepared toolchain; run the prepare job first' >&2; exit 1;
    }
    printf '%s\n' "$identity"
    ;;
  *) echo 'Usage: tools/ci_image.sh prepare|resolve' >&2; exit 2 ;;
esac
