#!/usr/bin/env bash
# Qianliu release task: build/push one service image, then package/upload its
# Helm chart in the same pipeline node.
#
# Qianliu runs this from the checkout root; do not derive paths from BASH_SOURCE.
# The console pipeline must run task0-console-frontend.sh first and restore
# /tmp/muad-console-frontend/ before this node starts.
set -euo pipefail

"$(pwd)/build/docker-build/build-image.sh" "im-gateway"
"$(pwd)/build/helm-build/package-service.sh" "im-gateway"
