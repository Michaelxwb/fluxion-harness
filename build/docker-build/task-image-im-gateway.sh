#!/usr/bin/env bash
# Qianliu task: build/push exactly the im-gateway image.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build-image.sh" "im-gateway"
