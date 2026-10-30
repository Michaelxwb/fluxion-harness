#!/usr/bin/env bash
# Build (and optionally push) the two internal base images.
#
# Usage:
#   BASE_REGISTRY=muad ./build/docker-build/base/build.sh                 # local build only
#   BASE_REGISTRY=docker.sangfor.com/mss-base PUSH=1 ./build/docker-build/base/build.sh
#
# The CI job that publishes base images should pin PYTHON_IMAGE/NODE_IMAGE digests
# or tags explicitly; never rely on `latest` for reproducible application builds.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BASE_REGISTRY="${BASE_REGISTRY:-docker.sangfor.com/mss-base}"
PYTHON_BASE_TAG="${PYTHON_BASE_TAG:-3.12}"
NODE_BASE_TAG="${NODE_BASE_TAG:-3.12-node24}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim-bookworm}"
NODE_IMAGE="${NODE_IMAGE:-node:24-bookworm-slim}"
PLATFORM="${PLATFORM:-linux/amd64}"
PUSH="${PUSH:-0}"

PYTHON_REF="${BASE_REGISTRY}/muad-python-base:${PYTHON_BASE_TAG}"
NODE_REF="${BASE_REGISTRY}/muad-python-node-base:${NODE_BASE_TAG}"

log() { printf '[base] %s\n' "$*"; }
die() { printf '[base][ERROR] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f "${REPO_ROOT}/build/docker-build/base/Dockerfile.python" ]] || die "run from the repository checkout"
[[ -f "${REPO_ROOT}/build/docker-build/base/Dockerfile.python-node" ]] || die "missing node base Dockerfile"

log "python base: ${PYTHON_REF} (${PYTHON_IMAGE}, ${PLATFORM})"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}" \
  -t "${PYTHON_REF}" \
  -f "${REPO_ROOT}/build/docker-build/base/Dockerfile.python" \
  "${REPO_ROOT}"

log "python+node base: ${NODE_REF} (${NODE_IMAGE})"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${PYTHON_REF}" \
  --build-arg "NODE_IMAGE=${NODE_IMAGE}" \
  -t "${NODE_REF}" \
  -f "${REPO_ROOT}/build/docker-build/base/Dockerfile.python-node" \
  "${REPO_ROOT}"

# Smoke test: the two capabilities that caused this base image to exist.
docker run --rm --platform "${PLATFORM}" --entrypoint python "${PYTHON_REF}" --version
docker run --rm --platform "${PLATFORM}" --entrypoint sh "${NODE_REF}" -c \
  'python --version && node --version && python -c "from zoneinfo import ZoneInfo; ZoneInfo(\"Asia/Shanghai\")"'

if [[ "${PUSH}" == "1" ]]; then
  log "pushing ${PYTHON_REF}"
  docker push "${PYTHON_REF}"
  log "pushing ${NODE_REF}"
  docker push "${NODE_REF}"
else
  log "PUSH=0: skip docker push"
fi

log "done"
