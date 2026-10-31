#!/usr/bin/env bash
# Build (and optionally push) the two intranet base images:
#   docker.sangfor.com/mss-base/python:3.12.13
#   docker.sangfor.com/mss-base/python-node:3.12.13-node24
#
# The intranet python:3.11.2 image is PlatOS 1.3 (glibc 2.28) without gcc/make
# or a package manager, so Python 3.12.13 is installed from the relocatable
# python-build-standalone archive instead of being compiled in place.
#
# Usage:
#   # local build, no push
#   BASE_REGISTRY=local SOURCE_BASE_IMAGE=docker.sangfor.com/mss-base/python:3.11.2 \
#     PLATFORM=linux/amd64 PUSH=0 ./build/docker-build/base/build.sh
#
#   # intranet publish (DOCKER_USER / DOCKER_PASS come from CI secrets;
#   # credentials alone enable the push, PUSH is not needed)
#   BASE_REGISTRY=docker.sangfor.com/mss-base PLATFORM=linux/amd64 \
#     DOCKER_USER=... DOCKER_PASS=... ./build/docker-build/base/build.sh
#
# Offline: put the Python standalone archive and node-v24.22.2-linux-x64.tar.gz
# into build/docker-build/base/vendor/ before running.
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
PYTHON_STANDALONE_TAG="${PYTHON_STANDALONE_TAG:-20260325}"
PYTHON_STANDALONE_URL="${PYTHON_STANDALONE_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_TAG}/cpython-${PYTHON_VERSION}%2B${PYTHON_STANDALONE_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz}"
NODE_VERSION="${NODE_VERSION:-24.22.2}"
NODE_DIST_URL="${NODE_DIST_URL:-https://nodejs.org/dist/v${NODE_VERSION}}"
PIP_INDEX_URL="${PIP_INDEX_URL:-http://mirrors.sangfor.org/pypi/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.sangfor.org}"
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

PYTHON_REF="${BASE_REGISTRY}/python:${PYTHON_BASE_TAG}"
NODE_REF="${BASE_REGISTRY}/python-node:${NODE_BASE_TAG}"
REGISTRY_HOST="${BASE_REGISTRY%%/*}"

case "${PLATFORM}" in
  linux/arm64) TARGETARCH=arm64 ;;
  *) TARGETARCH=amd64 ;;
esac

log() { printf '[base] %s\n' "$*"; }
die() { printf '[base][ERROR] %s\n' "$*" >&2; exit 1; }

STARTED_AT="$(date +%s)"
TOTAL_STEPS=6
STEP=0
step() { STEP=$((STEP + 1)); log "[${STEP}/${TOTAL_STEPS}] $*"; }

step "check inputs"
log "source=${SOURCE_BASE_IMAGE}"
log "python=${PYTHON_REF} (Python ${PYTHON_VERSION})"
log "node=${NODE_REF} (Node ${NODE_VERSION})"
log "platform=${PLATFORM} push=${PUSH}"

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f "${BASE_DIR}/Dockerfile.python" ]] || die "missing Dockerfile.python"
[[ -f "${BASE_DIR}/Dockerfile.python-node" ]] || die "missing Dockerfile.python-node"
[[ -f "${BASE_DIR}/extract.py" ]] || die "missing extract.py"
[[ -d "${BASE_DIR}/vendor" ]] || die "missing vendor directory: ${BASE_DIR}/vendor"

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

step "ensure source base image ${SOURCE_BASE_IMAGE}"
if docker image inspect "${SOURCE_BASE_IMAGE}" >/dev/null 2>&1; then
  log "source base image present locally"
else
  log "pulling ${SOURCE_BASE_IMAGE}"
  docker pull "${SOURCE_BASE_IMAGE}" || \
    die "cannot pull source base image ${SOURCE_BASE_IMAGE}; check registry login/permissions"
  log "source base image pulled"
fi

step "build python base ${PYTHON_REF}"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${SOURCE_BASE_IMAGE}" \
  --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
  --build-arg "PYTHON_STANDALONE_TAG=${PYTHON_STANDALONE_TAG}" \
  --build-arg "PYTHON_STANDALONE_URL=${PYTHON_STANDALONE_URL}" \
  --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
  --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
  -t "${PYTHON_REF}" \
  -f "${BASE_DIR}/Dockerfile.python" \
  "${BASE_DIR}"
python_size="$(docker image inspect --format '{{.Size}}' "${PYTHON_REF}" 2>/dev/null || true)"
log "built ${PYTHON_REF}${python_size:+ ($((python_size / 1024 / 1024)) MiB)}"

step "build python+node base ${NODE_REF}"
docker build \
  --platform "${PLATFORM}" \
  --build-arg "BASE_IMAGE=${PYTHON_REF}" \
  --build-arg "NODE_VERSION=${NODE_VERSION}" \
  --build-arg "NODE_DIST_URL=${NODE_DIST_URL}" \
  --build-arg "TARGETARCH=${TARGETARCH}" \
  -t "${NODE_REF}" \
  -f "${BASE_DIR}/Dockerfile.python-node" \
  "${BASE_DIR}"
node_size="$(docker image inspect --format '{{.Size}}' "${NODE_REF}" 2>/dev/null || true)"
log "built ${NODE_REF}${node_size:+ ($((node_size / 1024 / 1024)) MiB)}"

step "push base images"
if [[ "${PUSH}" == "1" ]]; then
  log "pushing ${PYTHON_REF}"
  docker push "${PYTHON_REF}"
  log "pushing ${NODE_REF}"
  docker push "${NODE_REF}"
  log "pushed both base images"
else
  log "PUSH=0; push skipped"
fi

log "done in $(( $(date +%s) - STARTED_AT ))s"
