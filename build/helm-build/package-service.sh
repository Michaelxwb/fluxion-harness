#!/usr/bin/env bash
# Package and optionally upload exactly one service's Helm chart.
#
# This is the per-pipeline companion of task2-helm.sh: when the five service
# pipelines each build their own image, they can also package/upload their own
# chart at the end of the same pipeline.
#
# Usage:
#   ./build/helm-build/package-service.sh console-platform
#   ./build/helm-build/package-service.sh nginx-entry
#
# Environment:
#   HELM_VERSION          chart version; defaults to 1.0.<image tag> or timestamp
#   HELM_OUT_DIR          output directory (default build/helm-build/dist)
#   HELM_CACHE_DIR        metadata output (default /tmp/muad-helm)
#   IMAGE_CACHE_DIR       image metadata dir (default /tmp/muad-image-<service>)
#   AUTH_TOKEN            sfspm token (sfspm_token also accepted); enables upload
#   UPLOAD                optional; derived from AUTH_TOKEN, set 0 to skip upload
#   CI_COMMIT_BRANCH/TAG  select mss-dev-helm / mss-release-helm
set -euo pipefail

SERVICE="${1:-}"
[[ -n "${SERVICE}" ]] || { echo "usage: $0 <service>" >&2; exit 2; }

REPO_ROOT="$(pwd)"
if [[ ! -f "${REPO_ROOT}/build/helm-build/package-helm.sh" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
cd "${REPO_ROOT}"

case "${SERVICE}" in
  console-platform) CHART="muad-console-platform" ;;
  agent-runtime) CHART="muad-agent-runtime" ;;
  agent-worker) CHART="muad-agent-worker" ;;
  im-gateway) CHART="muad-im-gateway" ;;
  im-connector) CHART="muad-im-connector" ;;
  nginx-entry) CHART="muad-nginx" ;;
  *) echo "[helm-service][ERROR] unknown service: ${SERVICE}" >&2; exit 2 ;;
esac

HELM_OUT_DIR="${HELM_OUT_DIR:-${REPO_ROOT}/build/helm-build/dist}"
HELM_CACHE_DIR="${HELM_CACHE_DIR:-/tmp/muad-helm}"
IMAGE_CACHE_DIR="${IMAGE_CACHE_DIR:-/tmp/muad-image-${SERVICE}}"
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

log() { printf '[helm-service:%s] %s\n' "${SERVICE}" "$*"; }
die() { printf '[helm-service:%s][ERROR] %s\n' "${SERVICE}" "$*" >&2; exit 1; }

STARTED_AT="$(date +%s)"
TOTAL_STEPS=4
STEP=0
step() { STEP=$((STEP + 1)); log "[${STEP}/${TOTAL_STEPS}] $*"; }

step "resolve chart version"
# Prefer the image tag written by build-image.sh so image and chart versions
# stay aligned. The fallback keeps the historical 1.0.<timestamp> scheme.
if [[ -z "${HELM_VERSION:-}" ]]; then
  image_tag_file="${IMAGE_CACHE_DIR}/IMAGE_TAG"
  if [[ -f "${image_tag_file}" ]]; then
    image_tag="$(cat "${image_tag_file}")"
    if [[ "${image_tag}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+].*)?$ ]]; then
      HELM_VERSION="${image_tag}"
    elif [[ "${image_tag}" =~ ^[0-9]+$ ]]; then
      HELM_VERSION="1.0.${image_tag}"
    fi
  fi
fi
HELM_VERSION="${HELM_VERSION:-1.0.$(date +%y%m%d%H%M)}"

if [[ -n "${CI_COMMIT_TAG:-}" || "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
  HELM_REPOSITORY="mss-release-helm"
else
  HELM_REPOSITORY="mss-dev-helm"
fi
HELM_REPOSITORY="${HELM_REPOSITORY_OVERRIDE:-${HELM_REPOSITORY}}"

log "chart=${CHART} version=${HELM_VERSION} repository=${HELM_REPOSITORY}"
log "upload=${UPLOAD} output=${HELM_OUT_DIR}"
log "release-info service=${SERVICE} image_tag=${image_tag:-unknown} chart_version=${HELM_VERSION} helm_repository=${HELM_REPOSITORY}"

step "package chart ${CHART}:${HELM_VERSION}"
HELM_OUT_DIR="${HELM_OUT_DIR}" HELM_VERSION="${HELM_VERSION}" \
  bash ./build/helm-build/package-helm.sh "${CHART}"

chart_tgz="${HELM_OUT_DIR}/${CHART}-${HELM_VERSION}.tgz"
[[ -f "${chart_tgz}" ]] || die "package missing: ${chart_tgz}"
log "packaged ${chart_tgz} ($(du -h "${chart_tgz}" | cut -f1))"

step "upload chart ${CHART}:${HELM_VERSION} to ${HELM_REPOSITORY}"
if [[ "${UPLOAD}" == "1" ]]; then
  command -v sfspm >/dev/null 2>&1 || die "sfspm is required when UPLOAD=1"
  : "${AUTH_TOKEN:?AUTH_TOKEN is required when UPLOAD=1}"
  sfspm auth "${AUTH_TOKEN}" --url=http://artifacts.sangfor.com
  sfspm upload "${chart_tgz}" "${HELM_REPOSITORY}"
  log "uploaded ${HELM_REPOSITORY}/${CHART}-${HELM_VERSION}.tgz"
else
  log "upload skipped (UPLOAD=0 or no AUTH_TOKEN)"
fi

step "write helm metadata"
mkdir -p "${HELM_CACHE_DIR}"
if [[ "$(cd "$(dirname "${chart_tgz}")" && pwd)" != "$(cd "${HELM_CACHE_DIR}" && pwd)" ]]; then
  cp -f "${chart_tgz}" "${HELM_CACHE_DIR}/"
fi
printf '%s\n' "$(basename "${chart_tgz}")" > "${HELM_CACHE_DIR}/HELM_PKG"
printf '%s\n' "${HELM_VERSION}" > "${HELM_CACHE_DIR}/HELM_VERSION"

# 部署流水线可 source 这个文件拿镜像 tag / chart 版本 / helm 仓库
image_ref="$(cat "${IMAGE_CACHE_DIR}/image.txt" 2>/dev/null || true)"
if [[ -n "${image_ref}" ]]; then
  {
    printf 'IMAGE_NAMESPACE=%s\n' "${image_ref%/*}"
    printf 'IMAGE_REPOSITORY=%s\n' "${image_ref%:*}"
    printf 'IMAGE_TAG=%s\n' "${image_ref##*:}"
    printf 'CHART_VERSION=%s\n' "${HELM_VERSION}"
    printf 'HELM_REPOSITORY=%s\n' "${HELM_REPOSITORY}"
  } > "${HELM_CACHE_DIR}/release.env"
  log "release env: ${HELM_CACHE_DIR}/release.env"
fi

log "metadata=${HELM_CACHE_DIR} (HELM_PKG, HELM_VERSION, release.env)"
log "done in $(( $(date +%s) - STARTED_AT ))s: ${chart_tgz}"
