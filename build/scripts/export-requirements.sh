#!/usr/bin/env bash
# Regenerate per-service third-party requirements from uv.lock.
#
# Workspace packages (muad-*) are intentionally excluded: the intranet images
# install them from local source/wheels with `pip install --no-deps`; including
# editable `-e ./packages/...` lines would make the requirements file depend on
# the build machine's working directory.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${REPO_ROOT}/build/requirements"

SERVICES=(
  "console-platform:muad-console-platform"
  "agent-runtime:muad-agent-runtime"
  "agent-worker:muad-agent-worker"
  "im-gateway:muad-im-gateway"
  "im-connector:muad-im-connector"
)

cd "${REPO_ROOT}"
mkdir -p "${OUT_DIR}"

for entry in "${SERVICES[@]}"; do
  file="${entry%%:*}"
  package="${entry##*:}"
  output="${OUT_DIR}/${file}.txt"
  tmp="${output}.tmp"
  {
    printf '# Generated from uv.lock by build/scripts/export-requirements.sh.\n'
    printf '# Service: %s\n' "${package}"
    printf '# Do not edit by hand; run the script after changing pyproject.toml/uv.lock.\n'
    uv export \
      --frozen \
      --no-dev \
      --no-hashes \
      --no-emit-workspace \
      --no-emit-project \
      --format requirements-txt \
      --package "${package}"
  } > "${tmp}"
  mv "${tmp}" "${output}"
  if grep -qE '^-e |muad-.* @ file:|@ file:' "${output}"; then
    echo "[requirements][ERROR] ${file}: workspace package leaked into ${output}" >&2
    exit 1
  fi
  printf '[requirements] %s: %s lines\n' "${file}" "$(grep -cE '^[a-zA-Z0-9]' "${output}")"
done
