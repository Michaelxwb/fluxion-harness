#!/usr/bin/env bash
# Build (and optionally push) the muad nginx entry image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_DIR="${REPO_ROOT}/build/nginx-build"

BASE_IMAGE="${BASE_IMAGE:-docker.sangfor.com/mss-base/platos-kylin:1.3.2}"
NGINX_BUILD_MODE="${NGINX_BUILD_MODE:-rpm}"
NGINX_SOURCE_IMAGE="${NGINX_SOURCE_IMAGE:-nginx:1.24-bullseye}"
if [[ -z "${IMAGE_REPOSITORY:-}" ]]; then
  if [[ -n "${CI_COMMIT_TAG:-}" || "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
    IMAGE_REPOSITORY="docker.sangfor.com/mss-release/muad-nginx"
  else
    IMAGE_REPOSITORY="docker.sangfor.com/mss-dev/muad-nginx"
  fi
fi

# shellcheck source=build/lib/image-tag.sh
source "${REPO_ROOT}/build/lib/image-tag.sh"
TAG="$(default_image_tag "${REPO_ROOT}")"
PLATFORM="${PLATFORM:-linux/amd64}"
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

log() { printf '[nginx] %s\n' "$*"; }
die() { printf '[nginx][ERROR] %s\n' "$*" >&2; exit 1; }

STARTED_AT="$(date +%s)"
TOTAL_STEPS=5
STEP=0
step() { STEP=$((STEP + 1)); log "[${STEP}/${TOTAL_STEPS}] $*"; }

step "resolve configuration"
command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f "${BASE_DIR}/nginx.conf" ]] || die "missing nginx.conf"

case "${NGINX_BUILD_MODE}" in
  rpm) DOCKERFILE="${BASE_DIR}/Dockerfile" ;;
  copy) DOCKERFILE="${BASE_DIR}/Dockerfile.copy" ;;
  *) die "NGINX_BUILD_MODE must be rpm or copy" ;;
esac
log "image=${IMAGE_REPOSITORY}:${TAG}"
log "mode=${NGINX_BUILD_MODE} base=${BASE_IMAGE} platform=${PLATFORM} push=${PUSH}"

REGISTRY_HOST="${IMAGE_REPOSITORY%%/*}"

step "docker login ${REGISTRY_HOST}"
if [[ -n "${DOCKER_USER:-}" && -n "${DOCKER_PASS:-}" ]]; then
  log "username=${DOCKER_USER}"
  printf '%s' "${DOCKER_PASS}" | docker login "${REGISTRY_HOST}" \
    --username "${DOCKER_USER}" --password-stdin
  log "login ok"
elif [[ "${PUSH}" == "1" ]]; then
  die "DOCKER_USER and DOCKER_PASS are required when PUSH=1"
else
  log "no credentials; skip login (build only)"
fi

step "ensure base image ${BASE_IMAGE}"
if docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
  log "base image present locally"
else
  log "pulling ${BASE_IMAGE}"
  docker pull "${BASE_IMAGE}" || \
    die "cannot pull base image ${BASE_IMAGE}; check registry login/permissions"
  log "base image pulled"
fi

step "docker build ${IMAGE_REPOSITORY}:${TAG}"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "NGINX_SOURCE_IMAGE=${NGINX_SOURCE_IMAGE}" \
  -t "${IMAGE_REPOSITORY}:${TAG}" \
  -f "${DOCKERFILE}" \
  "${BASE_DIR}"
size="$(docker image inspect --format '{{.Size}}' "${IMAGE_REPOSITORY}:${TAG}" 2>/dev/null || true)"
if [[ -n "${size}" ]]; then
  log "built ${IMAGE_REPOSITORY}:${TAG} ($((size / 1024 / 1024)) MiB)"
else
  log "built ${IMAGE_REPOSITORY}:${TAG}"
fi

step "docker push ${IMAGE_REPOSITORY}:${TAG}"
if [[ "${PUSH}" == "1" ]]; then
  docker push "${IMAGE_REPOSITORY}:${TAG}"
  log "pushed ${IMAGE_REPOSITORY}:${TAG}"
else
  log "PUSH=0; push skipped"
fi

log "done in $(( $(date +%s) - STARTED_AT ))s: ${IMAGE_REPOSITORY}:${TAG}"
