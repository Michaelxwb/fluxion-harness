#!/usr/bin/env bash
# Qianliu Task1: build and push the five service images.
#
# Requires Task0 cache (frontend dist) at /tmp/muad-console-frontend.
# Qianliu cache contract:
#   download: /tmp/muad-console-frontend
#   upload (optional): /tmp/muad-build (image list for downstream jobs)
set -euo pipefail

CUR_PATH="$(pwd)"
FRONTEND_CACHE_DIR="${FRONTEND_CACHE_DIR:-/tmp/muad-console-frontend}"
BUILD_CACHE_DIR="${BUILD_CACHE_DIR:-/tmp/muad-build}"
DIST_VERSION="${DIST_VERSION:-}"
TAG="${TAG:-}"

log() { printf '[images-task] %s\n' "$*"; }
die() { printf '[images-task][ERROR] %s\n' "$*" >&2; exit 1; }

[[ -f "${CUR_PATH}/build/docker-build/build-images.sh" ]] || \
  die "cwd is not the repository root: ${CUR_PATH}"

if [[ -z "${DIST_VERSION}" && -f "${FRONTEND_CACHE_DIR}/DIST_VERSION" ]]; then
  DIST_VERSION="$(cat "${FRONTEND_CACHE_DIR}/DIST_VERSION")"
fi
[[ -n "${DIST_VERSION}" ]] || die "DIST_VERSION missing (Task0 cache not downloaded?)"

# Restore Task0 output into the Docker build context.
rm -rf "${CUR_PATH}/build/frontend/dist"
mkdir -p "${CUR_PATH}/build/frontend/dist"
if [[ -d "${FRONTEND_CACHE_DIR}/dist" ]]; then
  cp -a "${FRONTEND_CACHE_DIR}/dist/." "${CUR_PATH}/build/frontend/dist/"
else
  tar -xzf "${FRONTEND_CACHE_DIR}/frontend-dist-${DIST_VERSION}.tar.gz" \
    -C "${CUR_PATH}/build/frontend/dist"
fi
[[ -f "${CUR_PATH}/build/frontend/dist/index.html" ]] || \
  die "frontend dist missing index.html after restoring Task0 cache"

# Branch -> repository routing, same policy as the old console pipeline.
if [[ "${CI_COMMIT_TAG:-}" =~ ^v ]]; then
  IMAGE_NAMESPACE="docker.sangfor.com/mss-release"
elif [[ "${CI_COMMIT_BRANCH:-}" =~ ^(feature|bug) ]]; then
  IMAGE_NAMESPACE="docker.sangfor.com/mss-dev"
elif [[ "${CI_COMMIT_BRANCH:-}" =~ ^(patch|master|release) ]]; then
  IMAGE_NAMESPACE="docker.sangfor.com/mss-release"
else
  IMAGE_NAMESPACE="docker.sangfor.com/mss-dev"
fi
IMAGE_NAMESPACE="${IMAGE_NAMESPACE_OVERRIDE:-${IMAGE_NAMESPACE}}"

if [[ -z "${TAG}" ]]; then
  if [[ -n "${CI_COMMIT_TAG:-}" ]]; then
    TAG="${CI_COMMIT_TAG}"
  else
    TAG="$(date +%y%m%d%H%M)"
    if [[ -n "${CI_COMMIT_BRANCH:-}" ]]; then
      # Docker tags cannot contain '/'; branches such as feature/foo are common.
      TAG="${TAG}$(printf '%s' "${CI_COMMIT_BRANCH}" | tr '/:' '__')"
    fi
  fi
fi
TAG="${TAG//\//_}"

log "DIST_VERSION=${DIST_VERSION} TAG=${TAG} namespace=${IMAGE_NAMESPACE}"

if [[ "${PUSH:-1}" == "1" ]]; then
  : "${DOCKER_USER:?DOCKER_USER is required when PUSH=1}"
  : "${DOCKER_PASS:?DOCKER_PASS is required when PUSH=1}"
  printf '%s' "${DOCKER_PASS}" | docker login docker.sangfor.com \
    --username "${DOCKER_USER}" --password-stdin
fi

cd "${CUR_PATH}"
IMAGE_NAMESPACE="${IMAGE_NAMESPACE}" \
TAG="${TAG}" \
PLATFORM="${PLATFORM:-linux/amd64}" \
PUSH="${PUSH:-1}" \
BUILD_JOBS="${BUILD_JOBS:-5}" \
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-docker.sangfor.com/mss-base/muad-python-base:3.12}" \
NODE_BASE_IMAGE="${NODE_BASE_IMAGE:-docker.sangfor.com/mss-base/muad-python-node-base:3.12-node24}" \
PIP_INDEX_URL="${PIP_INDEX_URL:-http://mirrors.sangfor.org/pypi/simple}" \
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.sangfor.org}" \
  ./build/docker-build/build-images.sh

mkdir -p "${BUILD_CACHE_DIR}"
cp "${CUR_PATH}/build/dist/images.env" "${BUILD_CACHE_DIR}/images.env"
echo "${DIST_VERSION}" > "${BUILD_CACHE_DIR}/DIST_VERSION"
echo "${TAG}" > "${BUILD_CACHE_DIR}/IMAGE_TAG"
log "images staged in ${BUILD_CACHE_DIR}"
