#!/usr/bin/env bash
# Build (and optionally push) the muad nginx entry image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_DIR="${REPO_ROOT}/build/nginx-build"

BASE_IMAGE="${BASE_IMAGE:-docker.sangfor.com/mss-base/platos-kylin:1.3.2}"
NGINX_BUILD_MODE="${NGINX_BUILD_MODE:-rpm}"
NGINX_SOURCE_IMAGE="${NGINX_SOURCE_IMAGE:-nginx:1.24-bullseye}"
if [[ -z "${IMAGE_REPOSITORY:-}" ]]; then
  if [[ "${CI_COMMIT_TAG:-}" =~ ^v || "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
    IMAGE_REPOSITORY="docker.sangfor.com/mss-release/muad-nginx"
  else
    IMAGE_REPOSITORY="docker.sangfor.com/mss-dev/muad-nginx"
  fi
fi
TAG="${TAG:-1.0.0}"
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

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f "${BASE_DIR}/nginx.conf" ]] || die "missing nginx.conf"

case "${NGINX_BUILD_MODE}" in
  rpm) DOCKERFILE="${BASE_DIR}/Dockerfile" ;;
  copy) DOCKERFILE="${BASE_DIR}/Dockerfile.copy" ;;
  *) die "NGINX_BUILD_MODE must be rpm or copy" ;;
esac

REGISTRY_HOST="${IMAGE_REPOSITORY%%/*}"

if [[ -n "${DOCKER_USER:-}" && -n "${DOCKER_PASS:-}" ]]; then
  log "docker login ${REGISTRY_HOST}"
  printf '%s' "${DOCKER_PASS}" | docker login "${REGISTRY_HOST}" \
    --username "${DOCKER_USER}" --password-stdin
elif [[ "${PUSH}" == "1" ]]; then
  die "DOCKER_USER and DOCKER_PASS are required when PUSH=1"
fi

if ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
  log "pulling base image ${BASE_IMAGE}"
  docker pull "${BASE_IMAGE}" || \
    die "cannot pull base image ${BASE_IMAGE}; check registry login/permissions"
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
