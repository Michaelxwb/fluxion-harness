.PHONY: check compile test acceptance acceptance-e2e i18n-check error-message-check lint typecheck

compile:
	uv run python -m compileall -q apps packages migrations

test:
	uv run python -m pytest -q

# 验收：每轮自动建独立空库 + 独立 Redis DB（tests/acceptance/conftest.py），不需要任何环境变量
acceptance:
	uv run python -m pytest -q tests/acceptance

# 浏览器验收（按域）：make acceptance-e2e DOMAIN=console-auth
acceptance-e2e:
	@test -n "$(DOMAIN)" || { echo "用法: make acceptance-e2e DOMAIN=<域>（如 console-auth / overview-dashboard / audit-observability / task-schedule）"; exit 2; }
	npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.$(DOMAIN).config.ts

i18n-check:
	uv run python scripts/check_frontend_i18n.py

error-message-check:
	uv run python scripts/check_error_message_hardcode.py

lint:
	uv run ruff check .

typecheck:
	uv run mypy apps packages

check: compile test i18n-check error-message-check lint typecheck
