#!/usr/bin/env bash
# Build exactly one service image. Each service is a separate Qianliu task/pipeline.
#
# Usage:
#   ./build/docker-build/build-image.sh console-platform
#   ./build/docker-build/build-image.sh agent-runtime
#
# No tests, lint or unit checks are run here: the intranet pipeline builds/pushes
# the image directly. Input existence checks only fail fast on missing artifacts.
set -euo pipefail

SERVICE="${1:-}"
[[ -n "${SERVICE}" ]] || { echo "usage: $0 <service>" >&2; exit 2; }

REPO_ROOT="$(pwd)"
if [[ ! -f "${REPO_ROOT}/build/docker-build/Dockerfile.${SERVICE}" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
cd "${REPO_ROOT}"

IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-}"
IMAGE_PREFIX="${IMAGE_PREFIX:-muad-}"
TAG="${TAG:-}"
PLATFORM="${PLATFORM:-linux/amd64}"
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-docker.sangfor.com/mss-base/python:3.12.13}"
NODE_BASE_IMAGE="${NODE_BASE_IMAGE:-docker.sangfor.com/mss-base/python-node:3.12.13-node24}"
PIP_INDEX_URL="${PIP_INDEX_URL:-http://mirrors.sangfor.org/pypi/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.sangfor.org}"
# PUSH is optional: with registry credentials present the scripts push by
# default, without credentials they only build. Set PUSH=0 to force a local
# build even when credentials are exported.
if [[ -z "${PUSH:-}" ]]; then
  if [[ -n "${DOCKER_USER:-}" && -n "${DOCKER_PASS:-}" ]]; then
    PUSH=1
  else
    PUSH=0
  fi
fi
FRONTEND_CACHE_DIR="${FRONTEND_CACHE_DIR:-/tmp/muad-console-frontend}"
IMAGE_CACHE_DIR="${IMAGE_CACHE_DIR:-/tmp/muad-image-${SERVICE}}"

log() { printf '[image:%s] %s\n' "${SERVICE}" "$*"; }
die() { printf '[image:%s][ERROR] %s\n' "${SERVICE}" "$*" >&2; exit 1; }

STARTED_AT="$(date +%s)"
TOTAL_STEPS=6
if [[ "${SERVICE}" == "console-platform" ]]; then
  TOTAL_STEPS=7
fi
STEP=0
step() { STEP=$((STEP + 1)); log "[${STEP}/${TOTAL_STEPS}] $*"; }

step "resolve configuration"
[[ -f "build/docker-build/Dockerfile.${SERVICE}" ]] || die "unknown service"

# Branch routing is shared by all five independent pipelines.
if [[ -z "${IMAGE_NAMESPACE}" ]]; then
  if [[ -n "${CI_COMMIT_TAG:-}" ]]; then
    IMAGE_NAMESPACE="docker.sangfor.com/mss-release"
  elif [[ "${CI_COMMIT_BRANCH:-}" =~ ^(feature|bug) ]]; then
    IMAGE_NAMESPACE="docker.sangfor.com/mss-dev"
  elif [[ "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
    IMAGE_NAMESPACE="docker.sangfor.com/mss-release"
  else
    IMAGE_NAMESPACE="docker.sangfor.com/mss-dev"
  fi
fi
IMAGE_NAMESPACE="${IMAGE_NAMESPACE_OVERRIDE:-${IMAGE_NAMESPACE}}"

# shellcheck source=build/lib/image-tag.sh
source "${REPO_ROOT}/build/lib/image-tag.sh"
TAG="$(default_image_tag "${REPO_ROOT}")"

case "${SERVICE}" in
  agent-runtime|agent-worker) base_image="${NODE_BASE_IMAGE}" ;;
  *) base_image="${PYTHON_BASE_IMAGE}" ;;
esac

image="${IMAGE_NAMESPACE}/${IMAGE_PREFIX}${SERVICE}:${TAG}"
log "image=${image}"
log "base=${base_image} platform=${PLATFORM} push=${PUSH}"
log "dockerfile=build/docker-build/Dockerfile.${SERVICE} context=."

# Console image is assembled from the separately built frontend cache.
if [[ "${SERVICE}" == "console-platform" ]]; then
  step "restore console frontend cache from ${FRONTEND_CACHE_DIR}"
  [[ -f "${FRONTEND_CACHE_DIR}/dist/index.html" ]] || \
    die "frontend cache missing: ${FRONTEND_CACHE_DIR}/dist/index.html"
  rm -rf build/frontend/dist
  mkdir -p build/frontend/dist
  cp -a "${FRONTEND_CACHE_DIR}/dist/." build/frontend/dist/
  log "frontend files=$(find build/frontend/dist -type f | wc -l | tr -d ' ') size=$(du -sh build/frontend/dist | cut -f1)"
fi

registry_host="${IMAGE_NAMESPACE%%/*}"
step "docker login ${registry_host}"
if [[ -n "${DOCKER_USER:-}" && -n "${DOCKER_PASS:-}" ]]; then
  log "username=${DOCKER_USER}"
  printf '%s' "${DOCKER_PASS}" | docker login "${registry_host}" \
    --username "${DOCKER_USER}" --password-stdin
  log "login ok"
elif [[ "${PUSH}" == "1" ]]; then
  die "DOCKER_USER and DOCKER_PASS are required when PUSH=1"
else
  log "no credentials; skip login (build only)"
fi

# The base image lives in the private registry. Pull it explicitly while the
# login is fresh; otherwise `docker build` fails later with a less clear error.
step "ensure base image ${base_image}"
if docker image inspect "${base_image}" >/dev/null 2>&1; then
  log "base image present locally"
else
  log "pulling ${base_image}"
  docker pull "${base_image}" || \
    die "cannot pull base image ${base_image}; check registry login/permissions"
  log "base image pulled"
fi

step "docker build ${image}"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${base_image}" \
  --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
  --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
  -t "${image}" \
  -f "build/docker-build/Dockerfile.${SERVICE}" \
  .
size="$(docker image inspect --format '{{.Size}}' "${image}" 2>/dev/null || true)"
if [[ -n "${size}" ]]; then
  log "built ${image} ($((size / 1024 / 1024)) MiB)"
else
  log "built ${image}"
fi

step "docker push ${image}"
if [[ "${PUSH}" == "1" ]]; then
  docker push "${image}"
  log "pushed ${image}"
else
  log "PUSH=0; push skipped"
fi

step "write build metadata"
mkdir -p "${IMAGE_CACHE_DIR}"
printf '%s\n' "${image}" > "${IMAGE_CACHE_DIR}/image.txt"
printf '%s\n' "${TAG}" > "${IMAGE_CACHE_DIR}/IMAGE_TAG"
log "metadata=${IMAGE_CACHE_DIR} (image.txt, IMAGE_TAG)"

log "done in $(( $(date +%s) - STARTED_AT ))s: ${image}"
