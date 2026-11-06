#!/usr/bin/env bash
# Build the bwrap-enabled python-node base and the session-manager probe image.
#
# Usage:
#   bash build/docker-build/sandbox/build.sh
#
# Optional:
#   BASE_IMAGE=docker.sangfor.com/mss-base/python:3.12.13
#   BASE_TAG=docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106
#   CANONICAL_TAG=docker.sangfor.com/mss-base/python-node:3.12.13-node24
#   PROBE_TAG=docker.sangfor.com/mss-base/sandbox-session-probe:0.1
#   PLATFORM=linux/amd64
#   TARBALL=/path/to/muad-python-node-bwrap-20261106.tar
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

BASE_IMAGE="${BASE_IMAGE:-docker.sangfor.com/mss-base/python:3.12.13}"
BASE_TAG="${BASE_TAG:-docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106}"
CANONICAL_TAG="${CANONICAL_TAG:-docker.sangfor.com/mss-base/python-node:3.12.13-node24}"
PROBE_TAG="${PROBE_TAG:-docker.sangfor.com/mss-base/sandbox-session-probe:0.1}"
PLATFORM="${PLATFORM:-linux/amd64}"
TARBALL="${TARBALL:-$ROOT/build/dist/muad-python-node-bwrap-20261106.tar}"

case "$PLATFORM" in
  linux/amd64) TARGETARCH=amd64 ;;
  linux/arm64) TARGETARCH=arm64 ;;
  *) echo "unsupported PLATFORM: $PLATFORM" >&2; exit 2 ;;
esac

echo "[1/4] build base $BASE_TAG"
docker build --platform "$PLATFORM" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "TARGETARCH=$TARGETARCH" \
  -t "$BASE_TAG" \
  -f build/docker-build/base/Dockerfile.python-node \
  build/docker-build/base

echo "[2/4] tag canonical $CANONICAL_TAG"
docker tag "$BASE_TAG" "$CANONICAL_TAG"

echo "[3/4] build session probe $PROBE_TAG"
docker build --platform "$PLATFORM" \
  --build-arg "BASE_IMAGE=$BASE_TAG" \
  -t "$PROBE_TAG" \
  -f build/docker-build/sandbox/Dockerfile.session-probe .

echo "[4/4] save images to $TARBALL"
mkdir -p "$(dirname "$TARBALL")"
docker save -o "$TARBALL" "$BASE_TAG" "$CANONICAL_TAG" "$PROBE_TAG"
shasum -a 256 "$TARBALL" | tee "$TARBALL.sha256"

echo "done: $TARBALL"
