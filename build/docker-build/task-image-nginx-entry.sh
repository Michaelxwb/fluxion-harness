#!/usr/bin/env bash
# Qianliu task: build/push the nginx entry image.
#
# Qianliu runs this from the checkout root; do not derive paths from BASH_SOURCE.
set -euo pipefail

# CI must publish: without registry credentials the build would silently skip
# pushing, so fail fast here instead of needing a PUSH flag.
: "${DOCKER_USER:?DOCKER_USER is required (Qianliu Secret)}"
: "${DOCKER_PASS:?DOCKER_PASS is required (Qianliu Secret)}"
exec "$(pwd)/build/nginx-build/build.sh"
