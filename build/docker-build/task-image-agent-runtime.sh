#!/usr/bin/env bash
# Qianliu runs this script from the checkout root; do not derive paths from
# BASH_SOURCE (Qianliu may copy the task script into /tmp).
# Task: build/push exactly the agent-runtime image.
set -euo pipefail
exec "$(pwd)/build/docker-build/build-image.sh" "agent-runtime"
