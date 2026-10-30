#!/usr/bin/env bash
# Qianliu release task: build/push one service image, then package/upload its
# Helm chart in the same pipeline node.
#
# Qianliu runs this from the checkout root; do not derive paths from BASH_SOURCE.
# The console pipeline must run task0-console-frontend.sh first and restore
# /tmp/muad-console-frontend/ before this node starts.
set -euo pipefail

# CI must publish: without registry credentials the build would silently skip
# pushing, so fail fast here instead of needing a PUSH flag.
: "${DOCKER_USER:?DOCKER_USER is required (Qianliu Secret)}"
: "${DOCKER_PASS:?DOCKER_PASS is required (Qianliu Secret)}"

AUTH_TOKEN="${AUTH_TOKEN:-${sfspm_token:-}}"
: "${AUTH_TOKEN:?AUTH_TOKEN is required (Qianliu Secret)}"
export AUTH_TOKEN

log() { printf '[release:console-platform] %s\n' "$*"; }

log "[1/2] build and push image"
"$(pwd)/build/docker-build/build-image.sh" "console-platform"
log "[2/2] package and upload Helm chart"
"$(pwd)/build/helm-build/package-service.sh" "console-platform"
log "release console-platform done"
