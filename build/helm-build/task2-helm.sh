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
UPLOAD="${UPLOAD:-1}"
DIST_VERSION="${DIST_VERSION:-}"
AUTH_TOKEN="${AUTH_TOKEN:-${sfspm_token:-}}"

log() { printf '[helm-task] %s\n' "$*"; }
die() { printf '[helm-task][ERROR] %s\n' "$*" >&2; exit 1; }

[[ -f "${CUR_PATH}/build/helm-build/package-helm.sh" ]] || \
  die "cwd is not the repository root: ${CUR_PATH}"

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

if [[ "${CI_COMMIT_TAG:-}" =~ ^v || "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
  HELM_REPOSITORY="mss-release-helm"
else
  HELM_REPOSITORY="mss-dev-helm"
fi
HELM_REPOSITORY="${HELM_REPOSITORY_OVERRIDE:-${HELM_REPOSITORY}}"

log "HELM_VERSION=${HELM_VERSION} repository=${HELM_REPOSITORY}"
HELM_OUT_DIR="${HELM_CACHE_DIR}/dist" HELM_VERSION="${HELM_VERSION}" \
  ./build/helm-build/package-helm.sh

charts=()
while IFS= read -r chart; do
  charts+=("${chart}")
done < <(find "${HELM_CACHE_DIR}/dist" -maxdepth 1 -type f -name 'muad-*-'"${HELM_VERSION}"'.tgz' | sort)
[[ "${#charts[@]}" -eq 5 ]] || die "expected 5 chart packages, got ${#charts[@]}"

if [[ "${UPLOAD}" == "1" ]]; then
  command -v sfspm >/dev/null 2>&1 || die "sfspm is required when UPLOAD=1"
  : "${AUTH_TOKEN:?AUTH_TOKEN is required when UPLOAD=1}"
  sfspm auth "${AUTH_TOKEN}" --url=http://artifacts.sangfor.com
  for chart in "${charts[@]}"; do
    sfspm upload "${chart}" "${HELM_REPOSITORY}"
  done
fi

mkdir -p "${HELM_CACHE_DIR}"
printf '%s\n' "$(printf '%s\n' "${charts[@]}" | xargs -n1 basename | paste -sd, -)" \
  > "${HELM_CACHE_DIR}/HELM_PKGS"
for chart in "${charts[@]}"; do cp "${chart}" "${HELM_CACHE_DIR}/"; done

log "packages:"
printf '  %s\n' "${charts[@]}"
