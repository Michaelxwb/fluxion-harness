.PHONY: check compile test acceptance acceptance-e2e smoke i18n-check error-message-check lint typecheck

compile:
	uv run python -m compileall -q apps packages migrations

test:
	uv run python -m pytest -q

# 验收：每轮自动建独立空库 + 独立 Redis DB（tests/acceptance/conftest.py），不需要任何环境变量
acceptance:
	uv run python -m pytest -q tests/acceptance

# 浏览器验收（按域）：make acceptance-e2e DOMAIN=console-auth
# DOMAIN=console-auth-ui 是 component 套件（mock API、无后端）；其余域仍是 E2E。
acceptance-e2e:
	@test -n "$(DOMAIN)" || { echo "用法: make acceptance-e2e DOMAIN=<域>（如 console-auth / console-auth-ui / overview-dashboard / task-schedule）"; exit 2; }
	npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.$(DOMAIN).config.ts

# 15 条跨域 smoke 白名单（e2e/scenarios/smoke-15.scenarios.md，测试以 @smoke 标记）。
# 一次 build，逐域只跑 @smoke；本地 fail-fast，CI 的 smoke job 另行汇总失败。
SMOKE_DOMAINS = console-auth overview-dashboard model-management agent skill mcp platform user-identity settings task-schedule-e2e audit-observability run-observability-e2e webchat-e2e

smoke:
	npm --prefix apps/console-platform/frontend run build
	@set -e; for domain in $(SMOKE_DOMAINS); do \
		echo "== smoke $$domain =="; \
		npm --prefix e2e test -- --config playwright.$$domain.config.ts --grep "@smoke"; \
	done

i18n-check:
	uv run python scripts/check_frontend_i18n.py

error-message-check:
	uv run python scripts/check_error_message_hardcode.py

lint:
	uv run ruff check .

typecheck:
	uv run mypy apps packages

check: compile test i18n-check error-message-check lint typecheck
