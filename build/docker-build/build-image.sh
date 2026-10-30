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

[[ -f "build/docker-build/Dockerfile.${SERVICE}" ]] || die "unknown service"

# Branch routing is shared by all five independent pipelines.
if [[ -z "${IMAGE_NAMESPACE}" ]]; then
  if [[ "${CI_COMMIT_TAG:-}" =~ ^v ]]; then
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

if [[ -z "${TAG}" ]]; then
  if [[ -n "${CI_COMMIT_TAG:-}" ]]; then
    TAG="${CI_COMMIT_TAG}"
  else
    TAG="$(date +%y%m%d%H%M)"
    if [[ -n "${CI_COMMIT_BRANCH:-}" ]]; then
      TAG="${TAG}$(printf '%s' "${CI_COMMIT_BRANCH}" | tr '/:' '__')"
    fi
  fi
fi
TAG="${TAG//\//_}"

case "${SERVICE}" in
  agent-runtime|agent-worker) base_image="${NODE_BASE_IMAGE}" ;;
  *) base_image="${PYTHON_BASE_IMAGE}" ;;
esac

# Console image is assembled from the separately built frontend cache.
if [[ "${SERVICE}" == "console-platform" ]]; then
  [[ -f "${FRONTEND_CACHE_DIR}/dist/index.html" ]] || \
    die "frontend cache missing: ${FRONTEND_CACHE_DIR}/dist/index.html"
  rm -rf build/frontend/dist
  mkdir -p build/frontend/dist
  cp -a "${FRONTEND_CACHE_DIR}/dist/." build/frontend/dist/
fi

registry_host="${IMAGE_NAMESPACE%%/*}"
if [[ -n "${DOCKER_USER:-}" && -n "${DOCKER_PASS:-}" ]]; then
  log "docker login ${registry_host}"
  printf '%s' "${DOCKER_PASS}" | docker login "${registry_host}" \
    --username "${DOCKER_USER}" --password-stdin
elif [[ "${PUSH}" == "1" ]]; then
  die "DOCKER_USER and DOCKER_PASS are required when PUSH=1"
fi

# The base image lives in the private registry. Pull it explicitly while the
# login is fresh; otherwise `docker build` fails later with a less clear error.
if ! docker image inspect "${base_image}" >/dev/null 2>&1; then
  log "pulling base image ${base_image}"
  docker pull "${base_image}" || \
    die "cannot pull base image ${base_image}; check registry login/permissions"
fi

image="${IMAGE_NAMESPACE}/${IMAGE_PREFIX}${SERVICE}:${TAG}"
log "building ${image} (base=${base_image}, platform=${PLATFORM})"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${base_image}" \
  --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
  --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
  -t "${image}" \
  -f "build/docker-build/Dockerfile.${SERVICE}" \
  .

if [[ "${PUSH}" == "1" ]]; then
  log "pushing ${image}"
  docker push "${image}"
fi

mkdir -p "${IMAGE_CACHE_DIR}"
printf '%s\n' "${image}" > "${IMAGE_CACHE_DIR}/image.txt"
printf '%s\n' "${TAG}" > "${IMAGE_CACHE_DIR}/IMAGE_TAG"
log "done; metadata: ${IMAGE_CACHE_DIR}"
