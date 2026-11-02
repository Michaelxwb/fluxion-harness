#!/usr/bin/env bash
# Package one or more per-service Helm charts. Image tags are deploy-time values.
#
# Usage:
#   ./build/helm-build/package-helm.sh                 # all five charts
#   ./build/helm-build/package-helm.sh muad-im-gateway # one chart
#   ./build/helm-build/package-helm.sh muad-nginx      # nginx entry chart
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
# muad-nginx is built and packaged only when requested explicitly
# (package-helm.sh muad-nginx), so task2 still packages the five services.
KNOWN_CHARTS=("${ALL_CHARTS[@]}" muad-nginx)

if [[ "$#" -gt 0 ]]; then
  CHARTS=("$@")
else
  CHARTS=("${ALL_CHARTS[@]}")
fi

log() { printf '[helm] %s\n' "$*"; }
die() { printf '[helm][ERROR] %s\n' "$*" >&2; exit 1; }

command -v helm >/dev/null 2>&1 || die "helm is required"
mkdir -p "${OUT_DIR}"

STARTED_AT="$(date +%s)"
INDEX=0
TOTAL="${#CHARTS[@]}"
log "version=${VERSION} output=${OUT_DIR} charts=${TOTAL}"

for chart in "${CHARTS[@]}"; do
  INDEX=$((INDEX + 1))
  known=0
  for candidate in "${KNOWN_CHARTS[@]}"; do
    [[ "${chart}" == "${candidate}" ]] && known=1
  done
  [[ "${known}" == "1" ]] || die "unknown chart: ${chart}"

  chart_dir="${CHART_ROOT}/${chart}"
  [[ -f "${chart_dir}/Chart.yaml" ]] || die "chart missing: ${chart_dir}"
  log "[${INDEX}/${TOTAL}] packaging ${chart}:${VERSION}"
  rm -f "${OUT_DIR}/${chart}-${VERSION}.tgz"
  helm package "${chart_dir}" \
    --version "${VERSION}" \
    --app-version "${VERSION}" \
    --destination "${OUT_DIR}"
  tgz="${OUT_DIR}/${chart}-${VERSION}.tgz"
  test -f "${tgz}" || die "package not produced: ${chart}"
  log "packaged ${tgz} ($(du -h "${tgz}" | cut -f1))"
done

log "done in $(( $(date +%s) - STARTED_AT ))s: ${TOTAL} charts"
