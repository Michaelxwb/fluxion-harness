#!/usr/bin/env bash
# Package one or more per-service Helm charts. Image tags are deploy-time values.
#
# Usage:
#   ./build/helm-build/package-helm.sh                 # all five charts
#   ./build/helm-build/package-helm.sh muad-im-gateway # one chart
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_ROOT="${REPO_ROOT}/build/helm-build"
OUT_DIR="${HELM_OUT_DIR:-${CHART_ROOT}/dist}"
VERSION="${HELM_VERSION:-1.0.$(date +%y%m%d%H%M)}"

ALL_CHARTS=(
  muad-console-platform
  muad-agent-runtime
  muad-agent-worker
  muad-im-gateway
  muad-im-connector
)

if [[ "$#" -gt 0 ]]; then
  CHARTS=("$@")
else
  CHARTS=("${ALL_CHARTS[@]}")
fi

command -v helm >/dev/null 2>&1 || { echo "[helm][ERROR] helm is required" >&2; exit 1; }
mkdir -p "${OUT_DIR}"

for chart in "${CHARTS[@]}"; do
  known=0
  for candidate in "${ALL_CHARTS[@]}"; do
    [[ "${chart}" == "${candidate}" ]] && known=1
  done
  [[ "${known}" == "1" ]] || {
    echo "[helm][ERROR] unknown chart: ${chart}" >&2
    exit 2
  }

  chart_dir="${CHART_ROOT}/${chart}"
  [[ -f "${chart_dir}/Chart.yaml" ]] || {
    echo "[helm][ERROR] chart missing: ${chart_dir}" >&2
    exit 1
  }
  rm -f "${OUT_DIR}/${chart}-${VERSION}.tgz"
  helm package "${chart_dir}" \
    --version "${VERSION}" \
    --app-version "${VERSION}" \
    --destination "${OUT_DIR}"
  test -f "${OUT_DIR}/${chart}-${VERSION}.tgz" || {
    echo "[helm][ERROR] package not produced: ${chart}" >&2
    exit 1
  }
  printf '[helm] %s\n' "${OUT_DIR}/${chart}-${VERSION}.tgz"
done

find "${OUT_DIR}" -maxdepth 1 -type f -name '*.tgz' -print | sort
