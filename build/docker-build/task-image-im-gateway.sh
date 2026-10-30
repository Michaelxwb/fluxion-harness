#!/usr/bin/env bash
# Qianliu runs this script from the checkout root; do not derive paths from
# BASH_SOURCE (Qianliu may copy the task script into /tmp).
# Task: build/push exactly the im-gateway image.
set -euo pipefail

# CI must publish: without registry credentials the build would silently skip
# pushing, so fail fast here instead of needing a PUSH flag.
: "${DOCKER_USER:?DOCKER_USER is required (Qianliu Secret)}"
: "${DOCKER_PASS:?DOCKER_PASS is required (Qianliu Secret)}"
exec "$(pwd)/build/docker-build/build-image.sh" "im-gateway"
