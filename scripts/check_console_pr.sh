#!/usr/bin/env bash
# Same component domains as PR CI; default also runs static checks and builds once.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  "")
    uv run make compile i18n-check error-message-check lint typecheck
    npm --prefix apps/console-platform/frontend run build
    uv run pytest -q tests/test_configuration_inventory.py tests/frontend
    ;;
  --component-only) ;;
  *) echo "Usage: bash scripts/check_console_pr.sh [--component-only]" >&2; exit 2 ;;
esac

domain_names="$(uv run python - <<'PY'
import re
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path('.github/workflows/check.yml').read_text())
steps = workflow['jobs']['component']['steps']
commands = [step['run'] for step in steps if 'for domain in' in step.get('run', '')]
if len(commands) != 1:
    raise SystemExit('Cannot identify the CI component domain loop')
match = re.search(r'for domain in\s+(.*?)\s+do\b', commands[0], re.S)
if not match:
    raise SystemExit('Cannot parse the CI component domain loop')
domains = match[1].replace('\\', ' ').split()
if not domains or any(not re.fullmatch(r'[a-z0-9-]+', domain) for domain in domains):
    raise SystemExit('Invalid CI component domains')
for domain in domains:
    if not Path(f'e2e/playwright.{domain}.config.ts').is_file():
        raise SystemExit(f'Missing component config: {domain}')
print(' '.join(domains))
PY
)"
read -r -a component_domains <<< "$domain_names"
failed=""
mkdir -p /tmp/component-logs
for domain in "${component_domains[@]}"
do
  echo "::group::component ${domain}"
  log="/tmp/component-logs/${domain}.log"
  if make acceptance-e2e DOMAIN="${domain}" BUILD_FRONTEND=0 >"$log" 2>&1; then
    echo "PASS ${domain}"
  else
    echo "FAIL ${domain}"
    failed="${failed} ${domain}"
    # 每个域跑完都会清空 outputDir，当场搬走 trace/error-context，避免只剩最后一个域的。
    cp -r e2e/test-results "/tmp/component-logs/${domain}-test-results" 2>/dev/null || true
    reason="$(grep -m1 -E '✘|^[[:space:]]*[0-9]+\) ' "$log" || true)"
    if [ -z "${reason}" ]; then
      reason="$(grep -m1 -E 'Error:|Timed out|EADDRINUSE|ECONNREFUSED|refused|StartupValidationError' "$log" || true)"
    fi
    echo "::error::${domain}: ${reason:-（未匹配到错误行，见 artifact component-domain-logs）}"
    tail -c 2000 "$log"
  fi
  echo "::endgroup::"
done
if [ -n "${failed}" ]; then
  echo "::error::component 验收失败的域:${failed}"
  exit 1
fi
echo "全部 ${#component_domains[@]} 个 component 域通过"
