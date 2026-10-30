#!/usr/bin/env bash
# Qianliu Task2: package the five per-service Helm charts and optionally upload
# them to the intranet Helm repository through sfspm.
#
# The chart packages reference images through global.imageRegistry/global.imageTag,
# set at deploy time; no image build is required for packaging.
set -euo pipefail

CUR_PATH="$(pwd)"
HELM_CACHE_DIR="${HELM_CACHE_DIR:-/tmp/muad-helm}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/tmp/muad-build}"
DIST_VERSION="${DIST_VERSION:-}"
AUTH_TOKEN="${AUTH_TOKEN:-${sfspm_token:-}}"
# UPLOAD is optional: a token means "package + upload", no token means
# "package only". Set UPLOAD=0 to force local packaging even with a token.
if [[ -z "${UPLOAD:-}" ]]; then
  if [[ -n "${AUTH_TOKEN}" ]]; then
    UPLOAD=1
  else
    UPLOAD=0
  fi
fi

log() { printf '[helm-task] %s\n' "$*"; }
die() { printf '[helm-task][ERROR] %s\n' "$*" >&2; exit 1; }

STARTED_AT="$(date +%s)"
TOTAL_STEPS=5
STEP=0
step() { STEP=$((STEP + 1)); log "[${STEP}/${TOTAL_STEPS}] $*"; }

step "resolve chart version"
[[ -f "${CUR_PATH}/build/helm-build/package-helm.sh" ]] || \
  die "cwd is not the repository root: ${CUR_PATH}"
log "cache=${HELM_CACHE_DIR} upload=${UPLOAD}"

if [[ -z "${DIST_VERSION}" && -f "${BUILD_CACHE_DIR}/DIST_VERSION" ]]; then
  DIST_VERSION="$(cat "${BUILD_CACHE_DIR}/DIST_VERSION")"
fi
if [[ -z "${HELM_VERSION:-}" ]]; then
  if [[ "${DIST_VERSION}" =~ ^[0-9]+$ ]]; then
    HELM_VERSION="1.0.${DIST_VERSION}"
  else
    HELM_VERSION="1.0.$(date +%y%m%d%H%M)"
  fi
fi

if [[ -n "${CI_COMMIT_TAG:-}" || "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
  HELM_REPOSITORY="mss-release-helm"
else
  HELM_REPOSITORY="mss-dev-helm"
fi
HELM_REPOSITORY="${HELM_REPOSITORY_OVERRIDE:-${HELM_REPOSITORY}}"

log "HELM_VERSION=${HELM_VERSION} repository=${HELM_REPOSITORY}"

step "package 5 charts version=${HELM_VERSION}"
HELM_OUT_DIR="${HELM_CACHE_DIR}/dist" HELM_VERSION="${HELM_VERSION}" \
  ./build/helm-build/package-helm.sh

step "verify packages"
charts=()
while IFS= read -r chart; do
  charts+=("${chart}")
done < <(find "${HELM_CACHE_DIR}/dist" -maxdepth 1 -type f -name 'muad-*-'"${HELM_VERSION}"'.tgz' | sort)
[[ "${#charts[@]}" -eq 5 ]] || die "expected 5 chart packages, got ${#charts[@]}"
log "found ${#charts[@]} packages in ${HELM_CACHE_DIR}/dist"

step "upload charts to ${HELM_REPOSITORY}"
if [[ "${UPLOAD}" == "1" ]]; then
  command -v sfspm >/dev/null 2>&1 || die "sfspm is required when UPLOAD=1"
  : "${AUTH_TOKEN:?AUTH_TOKEN is required when UPLOAD=1}"
  sfspm auth "${AUTH_TOKEN}" --url=http://artifacts.sangfor.com
  for chart in "${charts[@]}"; do
    sfspm upload "${chart}" "${HELM_REPOSITORY}"
  done
  log "uploaded ${#charts[@]} charts"
else
  log "upload skipped (UPLOAD=0 or no AUTH_TOKEN)"
fi

step "write helm metadata"
mkdir -p "${HELM_CACHE_DIR}"
printf '%s\n' "$(printf '%s\n' "${charts[@]}" | xargs -n1 basename | paste -sd, -)" \
  > "${HELM_CACHE_DIR}/HELM_PKGS"
for chart in "${charts[@]}"; do cp "${chart}" "${HELM_CACHE_DIR}/"; done

log "packages:"
printf '  %s\n' "${charts[@]}"
log "done in $(( $(date +%s) - STARTED_AT ))s"
