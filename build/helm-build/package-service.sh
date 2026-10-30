#!/usr/bin/env bash
# Package and optionally upload exactly one service's Helm chart.
#
# This is the per-pipeline companion of task2-helm.sh: when the five service
# pipelines each build their own image, they can also package/upload their own
# chart at the end of the same pipeline.
#
# Usage:
#   ./build/helm-build/package-service.sh console-platform
#
# Environment:
#   HELM_VERSION          chart version; defaults to 1.0.<image tag> or timestamp
#   HELM_OUT_DIR          output directory (default build/helm-build/dist)
#   HELM_CACHE_DIR        metadata output (default /tmp/muad-helm)
#   IMAGE_CACHE_DIR       image metadata dir (default /tmp/muad-image-<service>)
#   UPLOAD                default 1; set 0 for local packaging
#   AUTH_TOKEN            required when UPLOAD=1 (sfspm_token also accepted)
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
  *) echo "[helm-service][ERROR] unknown service: ${SERVICE}" >&2; exit 2 ;;
esac

HELM_OUT_DIR="${HELM_OUT_DIR:-${REPO_ROOT}/build/helm-build/dist}"
HELM_CACHE_DIR="${HELM_CACHE_DIR:-/tmp/muad-helm}"
IMAGE_CACHE_DIR="${IMAGE_CACHE_DIR:-/tmp/muad-image-${SERVICE}}"
UPLOAD="${UPLOAD:-1}"
AUTH_TOKEN="${AUTH_TOKEN:-${sfspm_token:-}}"

log() { printf '[helm-service:%s] %s\n' "${SERVICE}" "$*"; }
die() { printf '[helm-service:%s][ERROR] %s\n' "${SERVICE}" "$*" >&2; exit 1; }

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

if [[ "${CI_COMMIT_TAG:-}" =~ ^v || "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
  HELM_REPOSITORY="mss-release-helm"
else
  HELM_REPOSITORY="mss-dev-helm"
fi
HELM_REPOSITORY="${HELM_REPOSITORY_OVERRIDE:-${HELM_REPOSITORY}}"

log "chart=${CHART} version=${HELM_VERSION} repository=${HELM_REPOSITORY}"
HELM_OUT_DIR="${HELM_OUT_DIR}" HELM_VERSION="${HELM_VERSION}" \
  ./build/helm-build/package-helm.sh "${CHART}"

chart_tgz="${HELM_OUT_DIR}/${CHART}-${HELM_VERSION}.tgz"
[[ -f "${chart_tgz}" ]] || die "package missing: ${chart_tgz}"

if [[ "${UPLOAD}" == "1" ]]; then
  command -v sfspm >/dev/null 2>&1 || die "sfspm is required when UPLOAD=1"
  : "${AUTH_TOKEN:?AUTH_TOKEN is required when UPLOAD=1}"
  sfspm auth "${AUTH_TOKEN}" --url=http://artifacts.sangfor.com
  sfspm upload "${chart_tgz}" "${HELM_REPOSITORY}"
fi

mkdir -p "${HELM_CACHE_DIR}"
printf '%s\n' "${chart_tgz}" > "${HELM_CACHE_DIR}/HELM_PKG"
printf '%s\n' "${HELM_VERSION}" > "${HELM_CACHE_DIR}/HELM_VERSION"
log "done: ${chart_tgz}"
