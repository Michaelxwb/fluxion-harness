#!/usr/bin/env bash
# Local/CI static checks for the intranet build assets. No registry access and no
# Docker daemon required; image builds are validated separately.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

log() { printf '[verify] %s\n' "$*"; }

log "shell syntax"
bash -n build/base/build.sh build/scripts/*.sh build/qianliu/*.sh

log "requirements drift"
./build/scripts/export-requirements.sh
git diff --exit-code -- build/requirements

log "helm lint"
helm lint build/helm/muad-platform

log "helm template: default four units"
helm template muad build/helm/muad-platform --namespace muad >/tmp/muad-platform-default.yaml

log "helm template: connector + created artifacts PVC"
helm template muad build/helm/muad-platform --namespace muad \
  --set imConnector.enabled=true \
  --set global.artifacts.create=true \
  >/tmp/muad-platform-connector.yaml

log "rendered yaml parse"
python3 - <<'PY'
import yaml
for path in ('/tmp/muad-platform-default.yaml', '/tmp/muad-platform-connector.yaml'):
    docs = [doc for doc in yaml.safe_load_all(open(path, encoding='utf-8')) if doc]
    assert docs, path
    for doc in docs:
        assert doc.get('apiVersion') and doc.get('kind') and doc.get('metadata', {}).get('name'), doc
    print(f'{path}: {len(docs)} resources')
PY

log "ok"
