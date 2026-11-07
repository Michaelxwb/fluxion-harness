#!/bin/sh
# Install the admin-approved Skill system dependency baseline.
#
# Invoked only during runtime/worker image builds (build/docker-build/Dockerfile.*).
# Never run this from a live Pod: runtime changes are lost on restart, differ
# between replicas, and bypass the image digest/change record.
set -eu

DIR="${1:-/tmp/skill-deps}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-}"
NPM_REGISTRY="${NPM_REGISTRY:-http://npm.uedc.sangfor.com.cn/}"

log() { printf '[skill-deps] %s\n' "$*"; }

log "python baseline from ${DIR}/python-requirements.txt"
pip_args="--no-cache-dir --disable-pip-version-check --index-url ${PIP_INDEX_URL}"
if [ -n "${PIP_TRUSTED_HOST}" ]; then
  pip_args="${pip_args} --trusted-host ${PIP_TRUSTED_HOST}"
fi
# shellcheck disable=SC2086 # pip_args intentionally word-splits into flags
python -m pip install ${pip_args} -r "${DIR}/python-requirements.txt"

if [ -f "${DIR}/node/package.json" ]; then
  log "node baseline from ${DIR}/node/package.json"
  mkdir -p /usr/local/lib/node
  (
    cd "${DIR}/node"
    npm ci --registry="${NPM_REGISTRY}" --replace-registry-host=always --no-audit --no-fund
  )
  rm -rf /usr/local/lib/node/node_modules
  cp -a "${DIR}/node/node_modules" /usr/local/lib/node/node_modules
  # Node's legacy $PREFIX/lib/node lookup lets Skill scripts `require(name)`
  # without NODE_PATH (the sandbox clears the environment).
  for package in /usr/local/lib/node/node_modules/* /usr/local/lib/node/node_modules/@*/*; do
    [ -e "${package}" ] || continue
    name="${package#/usr/local/lib/node/node_modules/}"
    case "${name}" in
      @*/*) mkdir -p "/usr/local/lib/node/$(dirname "${name}")" ;;
    esac
    ln -sfn "node_modules/${name}" "/usr/local/lib/node/${name}"
  done
fi

log "baseline installed"
