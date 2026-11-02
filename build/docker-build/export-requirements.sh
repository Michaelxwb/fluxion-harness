#!/usr/bin/env bash
# Regenerate the single root requirements.txt from uv.lock.
#
# One file is intentionally shared by all five service images (intranet CI uses
# `pip install -r requirements.txt`). Workspace packages (muad-*) are excluded:
# they are installed from local source with `pip install --no-deps`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT="${REPO_ROOT}/requirements.txt"
TMP="${OUTPUT}.tmp"

cd "${REPO_ROOT}"
{
  printf '# Generated from uv.lock by build/docker-build/export-requirements.sh.\n'
  printf '# Shared by all service images; do not edit by hand.\n'
  uv export \
    --frozen \
    --no-dev \
    --no-hashes \
    --no-emit-workspace \
    --no-emit-project \
    --all-packages \
    --format requirements-txt
} > "${TMP}"
mv "${TMP}" "${OUTPUT}"

if grep -qE '^-e |muad-.* @ file:|@ file:' "${OUTPUT}"; then
  echo "[requirements][ERROR] workspace package leaked into ${OUTPUT}" >&2
  exit 1
fi
printf '[requirements] %s: %s lines\n' "${OUTPUT}" "$(grep -cE '^[a-zA-Z0-9]' "${OUTPUT}")"
