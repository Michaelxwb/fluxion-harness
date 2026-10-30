#!/usr/bin/env bash
# Qianliu Task2: package the platform Helm chart and (optionally) upload it to
# the intranet Helm repository through sfspm.
#
# Independent of the image task: the chart references images through
# global.imageRegistry/global.imageTag, which are set at deploy time.
set -euo pipefail

CUR_PATH="$(pwd)"
HELM_CACHE_DIR="${HELM_CACHE_DIR:-/tmp/muad-helm}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/tmp/muad-build}"
UPLOAD="${UPLOAD:-1}"
DIST_VERSION="${DIST_VERSION:-}"

log() { printf '[helm-task] %s\n' "$*"; }
die() { printf '[helm-task][ERROR] %s\n' "$*" >&2; exit 1; }

[[ -f "${CUR_PATH}/build/scripts/package-helm.sh" ]] || \
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
  ./build/scripts/package-helm.sh

chart_tgz="$(find "${HELM_CACHE_DIR}/dist" -maxdepth 1 -type f -name 'muad-platform-*.tgz' | sort | tail -1)"
[[ -n "${chart_tgz}" ]] || die "chart package not found"

if [[ "${UPLOAD}" == "1" ]]; then
  command -v sfspm >/dev/null 2>&1 || die "sfspm is required when UPLOAD=1"
  : "${AUTH_TOKEN:?AUTH_TOKEN is required when UPLOAD=1}"
  sfspm auth "${AUTH_TOKEN}" --url=http://artifacts.sangfor.com
  sfspm upload "${chart_tgz}" "${HELM_REPOSITORY}"
fi

mkdir -p "${HELM_CACHE_DIR}"
cp "${chart_tgz}" "${HELM_CACHE_DIR}/"
echo "$(basename "${chart_tgz}")" > "${HELM_CACHE_DIR}/HELM_PKGS"
printf '[helm-task] %s\n' "${chart_tgz}"
