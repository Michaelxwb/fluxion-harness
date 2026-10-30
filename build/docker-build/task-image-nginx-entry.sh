#!/usr/bin/env bash
# Qianliu task: build/push the nginx entry image.
#
# Qianliu runs this from the checkout root; do not derive paths from BASH_SOURCE.
set -euo pipefail
exec "$(pwd)/build/nginx-build/build.sh"
