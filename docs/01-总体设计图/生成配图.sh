#!/usr/bin/env bash
# 生成总体设计配图、预览页并校验正文引用。
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/生成配图.py "$@"
python3 tools/生成预览.py
python3 tools/校验配图.py
