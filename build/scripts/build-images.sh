#!/usr/bin/env bash
# Build all intranet service images from the current checkout.
#
# Prerequisites:
#   - build/frontend/dist exists (task0-frontend.sh output) for console-platform.
#   - base images exist (build/base/build.sh) or are pullable from the registry.
#   - docker is logged in to IMAGE_NAMESPACE if PUSH=1.
#
# Example:
#   IMAGE_NAMESPACE=docker.sangfor.com/mss-dev TAG=1.0.0 PUSH=1 \
#     ./build/scripts/build-images.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-docker.sangfor.com/mss-dev}"
IMAGE_PREFIX="${IMAGE_PREFIX:-muad-}"
TAG="${TAG:-$(date +%y%m%d%H%M)}"
PLATFORM="${PLATFORM:-linux/amd64}"
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-docker.sangfor.com/mss-base/muad-python-base:3.12}"
NODE_BASE_IMAGE="${NODE_BASE_IMAGE:-docker.sangfor.com/mss-base/muad-python-node-base:3.12-node24}"
PIP_INDEX_URL="${PIP_INDEX_URL:-http://mirrors.sangfor.org/pypi/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.sangfor.org}"
PUSH="${PUSH:-0}"
SERVICES="${SERVICES:-console-platform agent-runtime agent-worker im-gateway im-connector}"

log() { printf '[images] %s\n' "$*"; }
die() { printf '[images][ERROR] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f build/frontend/dist/index.html ]] || \
  die "build/frontend/dist missing; run build/qianliu/task0-frontend.sh first"

mkdir -p build/dist
: > build/dist/images.env

for service in ${SERVICES}; do
  dockerfile="build/docker/Dockerfile.${service}"
  [[ -f "${dockerfile}" ]] || die "unknown service '${service}' (${dockerfile})"
  case "${service}" in
    agent-runtime|agent-worker) base_image="${NODE_BASE_IMAGE}" ;;
    *) base_image="${PYTHON_BASE_IMAGE}" ;;
  esac
  image="${IMAGE_NAMESPACE}/${IMAGE_PREFIX}${service}:${TAG}"
  log "building ${image} (base=${base_image}, platform=${PLATFORM})"
  docker build \
    --platform "${PLATFORM}" \
    --build-arg "BASE_IMAGE=${base_image}" \
    --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
    --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
    -t "${image}" \
    -f "${dockerfile}" \
    .
  printf '%s_IMAGE=%s\n' "$(echo "${service}" | tr '[:lower:]-' '[:upper:]_')" "${image}" >> build/dist/images.env
  if [[ "${PUSH}" == "1" ]]; then
    log "pushing ${image}"
    docker push "${image}"
  fi
done

log "wrote build/dist/images.env"
cat build/dist/images.env
