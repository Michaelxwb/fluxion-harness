#!/usr/bin/env bash
# Build (and optionally push) the two intranet base images:
#   docker.sangfor.com/mss-base/python:3.12.13
#   docker.sangfor.com/mss-base/python-node:3.12.13-node24
#
# The Python base is derived from the intranet Python 3.11.2 image by compiling
# Python 3.12.13 inside it. This keeps glibc/OpenSSL aligned with the base OS.
#
# Usage:
#   # local build, no push
#   BASE_REGISTRY=local SOURCE_BASE_IMAGE=python:3.11.2 PUSH=0 \
#     ./build/docker-build/base/build.sh
#
#   # intranet publish (DOCKER_USER / DOCKER_PASS come from CI secrets)
#   BASE_REGISTRY=docker.sangfor.com/mss-base PLATFORM=linux/amd64 PUSH=1 \
#     DOCKER_USER=... DOCKER_PASS=... ./build/docker-build/base/build.sh
#
# Offline: put Python-3.12.13.tgz and node-v24.22.2-linux-x64.tar.gz into
# build/docker-build/base/vendor/ before running.
set -euo pipefail

REPO_ROOT="$(pwd)"
if [[ ! -f "${REPO_ROOT}/build/docker-build/base/Dockerfile.python" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
fi
BASE_DIR="${REPO_ROOT}/build/docker-build/base"

BASE_REGISTRY="${BASE_REGISTRY:-docker.sangfor.com/mss-base}"
PYTHON_BASE_TAG="${PYTHON_BASE_TAG:-3.12.13}"
NODE_BASE_TAG="${NODE_BASE_TAG:-3.12.13-node24}"
SOURCE_BASE_IMAGE="${SOURCE_BASE_IMAGE:-docker.sangfor.com/mss-base/python:3.11.2}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12.13}"
PYTHON_SOURCE_URL="${PYTHON_SOURCE_URL:-https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz}"
NODE_VERSION="${NODE_VERSION:-24.22.2}"
NODE_DIST_URL="${NODE_DIST_URL:-https://nodejs.org/dist/v${NODE_VERSION}}"
PIP_INDEX_URL="${PIP_INDEX_URL:-http://mirrors.sangfor.org/pypi/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.sangfor.org}"
PLATFORM="${PLATFORM:-linux/amd64}"
PUSH="${PUSH:-0}"

PYTHON_REF="${BASE_REGISTRY}/python:${PYTHON_BASE_TAG}"
NODE_REF="${BASE_REGISTRY}/python-node:${NODE_BASE_TAG}"
REGISTRY_HOST="${BASE_REGISTRY%%/*}"

case "${PLATFORM}" in
  linux/arm64) TARGETARCH=arm64 ;;
  *) TARGETARCH=amd64 ;;
esac

log() { printf '[base] %s\n' "$*"; }
die() { printf '[base][ERROR] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f "${BASE_DIR}/Dockerfile.python" ]] || die "missing Dockerfile.python"
[[ -f "${BASE_DIR}/Dockerfile.python-node" ]] || die "missing Dockerfile.python-node"
[[ -d "${BASE_DIR}/vendor" ]] || die "missing vendor directory: ${BASE_DIR}/vendor"

if [[ "${PUSH}" == "1" ]]; then
  : "${DOCKER_USER:?DOCKER_USER is required when PUSH=1}"
  : "${DOCKER_PASS:?DOCKER_PASS is required when PUSH=1}"
  log "docker login ${REGISTRY_HOST}"
  printf '%s' "${DOCKER_PASS}" | docker login "${REGISTRY_HOST}" \
    --username "${DOCKER_USER}" --password-stdin
fi

log "python base: ${PYTHON_REF} <- ${SOURCE_BASE_IMAGE} (Python ${PYTHON_VERSION}, ${PLATFORM})"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${SOURCE_BASE_IMAGE}" \
  --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
  --build-arg "PYTHON_SOURCE_URL=${PYTHON_SOURCE_URL}" \
  --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
  --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
  -t "${PYTHON_REF}" \
  -f "${BASE_DIR}/Dockerfile.python" \
  "${BASE_DIR}"

log "python+node base: ${NODE_REF} (Node ${NODE_VERSION})"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${PYTHON_REF}" \
  --build-arg "NODE_VERSION=${NODE_VERSION}" \
  --build-arg "NODE_DIST_URL=${NODE_DIST_URL}" \
  --build-arg "TARGETARCH=${TARGETARCH}" \
  -t "${NODE_REF}" \
  -f "${BASE_DIR}/Dockerfile.python-node" \
  "${BASE_DIR}"

if [[ "${PUSH}" == "1" ]]; then
  log "pushing ${PYTHON_REF}"
  docker push "${PYTHON_REF}"
  log "pushing ${NODE_REF}"
  docker push "${NODE_REF}"
else
  log "PUSH=0: skip docker push"
fi

log "done"
