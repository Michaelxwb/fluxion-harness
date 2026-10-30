#!/usr/bin/env bash
# Build (and optionally push) the muad nginx entry image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_DIR="${REPO_ROOT}/build/nginx-build"

BASE_IMAGE="${BASE_IMAGE:-docker.sangfor.com/mss-base/platos-kylin:1.3.2}"
NGINX_BUILD_MODE="${NGINX_BUILD_MODE:-rpm}"
NGINX_SOURCE_IMAGE="${NGINX_SOURCE_IMAGE:-nginx:1.24-bullseye}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-docker.sangfor.com/mss-base/muad-nginx}"
TAG="${TAG:-1.0.0}"
PLATFORM="${PLATFORM:-linux/amd64}"
PUSH="${PUSH:-0}"

log() { printf '[nginx] %s\n' "$*"; }
die() { printf '[nginx][ERROR] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f "${BASE_DIR}/nginx.conf" ]] || die "missing nginx.conf"

case "${NGINX_BUILD_MODE}" in
  rpm) DOCKERFILE="${BASE_DIR}/Dockerfile" ;;
  copy) DOCKERFILE="${BASE_DIR}/Dockerfile.copy" ;;
  *) die "NGINX_BUILD_MODE must be rpm or copy" ;;
esac

REGISTRY_HOST="${IMAGE_REPOSITORY%%/*}"

if [[ "${PUSH}" == "1" ]]; then
  : "${DOCKER_USER:?DOCKER_USER is required when PUSH=1}"
  : "${DOCKER_PASS:?DOCKER_PASS is required when PUSH=1}"
  log "docker login ${REGISTRY_HOST}"
  printf '%s' "${DOCKER_PASS}" | docker login "${REGISTRY_HOST}" \
    --username "${DOCKER_USER}" --password-stdin
fi

log "build ${IMAGE_REPOSITORY}:${TAG} (mode=${NGINX_BUILD_MODE}, base=${BASE_IMAGE}, platform=${PLATFORM})"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "NGINX_SOURCE_IMAGE=${NGINX_SOURCE_IMAGE}" \
  -t "${IMAGE_REPOSITORY}:${TAG}" \
  -f "${DOCKERFILE}" \
  "${BASE_DIR}"

if [[ "${PUSH}" == "1" ]]; then
  log "push ${IMAGE_REPOSITORY}:${TAG}"
  docker push "${IMAGE_REPOSITORY}:${TAG}"
else
  log "PUSH=0: skip docker push"
fi

log "done"
