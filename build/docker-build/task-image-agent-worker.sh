#!/usr/bin/env bash
# Qianliu task: build/push exactly the agent-worker image.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build-image.sh" "agent-worker"
