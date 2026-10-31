#!/usr/bin/env bash
# Local/CI static checks for the intranet build assets. No registry access and no
# Docker daemon required; image builds are validated separately.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"
VERIFY_DIR="$(mktemp -d)"
trap 'rm -rf "${VERIFY_DIR}"' EXIT

CHARTS=(
  muad-console-platform
  muad-agent-runtime
  muad-agent-worker
  muad-im-gateway
  muad-im-connector
  muad-nginx
)

log() { printf '[verify] %s\n' "$*"; }

log "shell syntax"
bash -n build/docker-build/base/build.sh build/docker-build/*.sh build/helm-build/*.sh

log "requirements drift"
./build/docker-build/export-requirements.sh
git diff --exit-code -- requirements.txt

for chart in "${CHARTS[@]}"; do
  log "helm lint/template: ${chart}"
  helm lint "build/helm-build/${chart}"
  helm template muad-"${chart}" "build/helm-build/${chart}" \
    --namespace muad --set global.imageTag=verify >"${VERIFY_DIR}/${chart}.yaml"
done

# Console chart also owns the optional artifacts PVC.
helm template muad-console-platform build/helm-build/muad-console-platform \
  --namespace muad --set global.artifacts.create=true >"${VERIFY_DIR}/muad-console-platform-pvc.yaml"

log "shared configmap parse"
VERIFY_DIR="${VERIFY_DIR}" python3 - <<'PY2'
import yaml
doc = yaml.safe_load(open('deploy/k8s/base/configmap.yaml', encoding='utf-8'))
assert doc['kind'] == 'ConfigMap', doc
assert doc['metadata']['name'] == 'muad-common', doc
assert doc['metadata']['namespace'] == 'muad', doc
assert doc['data']['DEFAULT_TENANT_ID'], doc
print('deploy/k8s/base/configmap.yaml: muad-common ok')
PY2

log "rendered yaml parse"
VERIFY_DIR="${VERIFY_DIR}" python3 - <<'PY'
import glob
import os
import yaml
paths = sorted(glob.glob(os.path.join(os.environ['VERIFY_DIR'], '*.yaml')))
assert len(paths) == 7, paths
for path in paths:
    docs = [doc for doc in yaml.safe_load_all(open(path, encoding='utf-8')) if doc]
    assert docs, path
    for doc in docs:
        assert doc.get('apiVersion') and doc.get('kind') and doc.get('metadata', {}).get('name'), doc
    print(f'{path}: {len(docs)} resources')
PY

log "ok"
