#!/usr/bin/env bash
# Qianliu task: build/push exactly the agent-runtime image.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build-image.sh" "agent-runtime"
