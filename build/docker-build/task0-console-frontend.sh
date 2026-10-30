#!/usr/bin/env bash
# Qianliu Task0: build the Console frontend and stage it for the image assembly task.
#
# Runs inside the intranet Node image (e.g. mss-fe-node:20.9.0). The Qianliu
# checkout root is the current working directory; do not derive it from
# BASH_SOURCE (Qianliu copies task scripts into /tmp/<build>/.qianliu.*.script).
#
# Cache contract:
#   upload:   /tmp/muad-console-frontend/{dist,frontend-dist-<version>.tar.gz,DIST_VERSION}
#   download: the image task restores the same path.
set -euo pipefail

DIST_VERSION="${DIST_VERSION:-$(date +%s)}"
CACHE_DIR="${FRONTEND_CACHE_DIR:-/tmp/muad-console-frontend}"
NPM_REGISTRY="${NPM_REGISTRY:-http://npm.uedc.sangfor.com.cn/}"
REPO_ROOT="$(pwd)"

log() { printf '[frontend] %s\n' "$*"; }
die() { printf '[frontend][ERROR] %s\n' "$*" >&2; exit 1; }

STARTED_AT="$(date +%s)"
TOTAL_STEPS=4
STEP=0
step() { STEP=$((STEP + 1)); log "[${STEP}/${TOTAL_STEPS}] $*"; }

step "resolve frontend workspace"
[[ -f "${REPO_ROOT}/apps/console-platform/frontend/package.json" ]] || \
  die "cwd is not the repository root: ${REPO_ROOT}"
[[ -f "${REPO_ROOT}/apps/console-platform/frontend/package-lock.json" ]] || \
  die "package-lock.json is required for reproducible npm ci"
log "node=$(node --version) npm=$(npm --version)"
log "registry=${NPM_REGISTRY} cache=${CACHE_DIR} version=${DIST_VERSION}"

cd "${REPO_ROOT}/apps/console-platform/frontend"

step "npm ci"
npm ci --registry="${NPM_REGISTRY}" --replace-registry-host=always
log "dependencies installed"

step "npm run build"
npm run build
[[ -f dist/index.html ]] || die "frontend build did not produce dist/index.html"
log "build output: files=$(find dist -type f | wc -l | tr -d ' ') size=$(du -sh dist | cut -f1)"

step "stage frontend cache"
mkdir -p "${CACHE_DIR}"
rm -rf "${CACHE_DIR:?}/"*
cp -a dist "${CACHE_DIR}/dist"
tar -czf "${CACHE_DIR}/frontend-dist-${DIST_VERSION}.tar.gz" -C dist .
echo "${DIST_VERSION}" > "${CACHE_DIR}/DIST_VERSION"
ls -la "${CACHE_DIR}"
log "cache files: files=$(find "${CACHE_DIR}" -type f | wc -l | tr -d ' ') size=$(du -sh "${CACHE_DIR}" | cut -f1)"
log "done in $(( $(date +%s) - STARTED_AT ))s: DIST_VERSION=${DIST_VERSION}"
