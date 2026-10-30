#!/usr/bin/env bash
# Build the five intranet service images concurrently.
#
# Prerequisites:
#   - build/frontend/dist exists (task0-frontend.sh output) for console-platform.
#   - base images exist (build/docker-build/base/build.sh) or are pullable.
#   - docker is logged in to IMAGE_NAMESPACE if PUSH=1.
#
# Example:
#   IMAGE_NAMESPACE=docker.sangfor.com/mss-dev TAG=1.0.0 PUSH=1 \
#     BUILD_JOBS=5 ./build/docker-build/build-images.sh
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
BUILD_JOBS="${BUILD_JOBS:-5}"
SERVICES="${SERVICES:-console-platform agent-runtime agent-worker im-gateway im-connector}"

log() { printf '[images] %s\n' "$*"; }
die() { printf '[images][ERROR] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is required"
[[ -f build/frontend/dist/index.html ]] || \
  die "build/frontend/dist missing; run build/docker-build/task0-frontend.sh first"

mkdir -p build/dist/logs build/dist/services
rm -f build/dist/services/*.env build/dist/images.env

for service in ${SERVICES}; do
  [[ -f "build/docker-build/Dockerfile.${service}" ]] || \
    die "unknown service '${service}' (build/docker-build/Dockerfile.${service})"
done

export IMAGE_NAMESPACE IMAGE_PREFIX TAG PLATFORM PYTHON_BASE_IMAGE NODE_BASE_IMAGE
export PIP_INDEX_URL PIP_TRUSTED_HOST PUSH

build_one() {
  local service="$1"
  local base_image image key log_file
  case "${service}" in
    agent-runtime|agent-worker) base_image="${NODE_BASE_IMAGE}" ;;
    *) base_image="${PYTHON_BASE_IMAGE}" ;;
  esac
  image="${IMAGE_NAMESPACE}/${IMAGE_PREFIX}${service}:${TAG}"
  log_file="build/dist/logs/${service}.log"
  printf '[images] building %s (base=%s)\n' "${image}" "${base_image}"
  if ! docker build \
      --platform "${PLATFORM}" \
      --build-arg "BASE_IMAGE=${base_image}" \
      --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
      --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
      -t "${image}" \
      -f "build/docker-build/Dockerfile.${service}" \
      . >"${log_file}" 2>&1; then
    printf '[images][ERROR] build failed: %s (log: %s)\n' "${service}" "${log_file}" >&2
    tail -40 "${log_file}" >&2
    return 1
  fi
  key="$(printf '%s' "${service}" | tr '[:lower:]-' '[:upper:]_')_IMAGE"
  printf '%s=%s\n' "${key}" "${image}" > "build/dist/services/${service}.env"
  if [[ "${PUSH}" == "1" ]]; then
    if ! docker push "${image}" >>"${log_file}" 2>&1; then
      printf '[images][ERROR] push failed: %s (log: %s)\n' "${service}" "${log_file}" >&2
      tail -40 "${log_file}" >&2
      return 1
    fi
  fi
  printf '[images] done %s\n' "${image}"
}
export -f build_one

log "building ${SERVICES} with BUILD_JOBS=${BUILD_JOBS}"
if ! printf '%s\n' ${SERVICES} | xargs -n1 -P "${BUILD_JOBS}" bash -c 'build_one "$1"' _; then
  die "one or more image builds failed; see build/dist/logs/"
fi

for service in ${SERVICES}; do
  cat "build/dist/services/${service}.env" >> build/dist/images.env
done
log "wrote build/dist/images.env"
cat build/dist/images.env
