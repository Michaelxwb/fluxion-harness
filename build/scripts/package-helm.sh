#!/usr/bin/env bash
# Package the intranet Helm chart. The image tag is a deploy-time value, so the
# chart package is build-independent; appVersion is the build version for traceability.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_DIR="${REPO_ROOT}/build/helm/muad-platform"
OUT_DIR="${HELM_OUT_DIR:-${REPO_ROOT}/build/helm/dist}"
VERSION="${HELM_VERSION:-1.0.$(date +%y%m%d%H%M)}"

command -v helm >/dev/null 2>&1 || { echo "[helm][ERROR] helm is required" >&2; exit 1; }
mkdir -p "${OUT_DIR}"
rm -f "${OUT_DIR}/muad-platform-${VERSION}.tgz"

helm lint "${CHART_DIR}"
helm package "${CHART_DIR}" \
  --version "${VERSION}" \
  --app-version "${VERSION}" \
  --destination "${OUT_DIR}"

test -f "${OUT_DIR}/muad-platform-${VERSION}.tgz" || {
  echo "[helm][ERROR] package not produced" >&2
  exit 1
}
printf '[helm] %s\n' "${OUT_DIR}/muad-platform-${VERSION}.tgz"
