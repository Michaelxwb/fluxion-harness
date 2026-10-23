.PHONY: check compile test acceptance acceptance-clean acceptance-e2e smoke capacity-load e2e-changed e2e-changed-plan i18n-check error-message-check lint typecheck

compile:
	uv run python -m compileall -q apps packages migrations

test:
	uv run python -m pytest -q

# 验收：每轮自动建独立空库 + 独立 Redis DB（tests/acceptance/conftest.py），不需要任何环境变量
acceptance:
	uv run python scripts/clean_acceptance_claims.py
	uv run python -m pytest -q -m "not capacity_load" tests/acceptance

# 浏览器验收（按域）：make acceptance-e2e DOMAIN=console-auth
# DOMAIN=console-auth-ui 是 component 套件（mock API、无后端）；其余域仍是 E2E。
# BUILD_FRONTEND=0：调用方已构建（CI/批量循环一次 build，避免逐域重复）。
BUILD_FRONTEND ?= 1
acceptance-e2e:
	@test -n "$(DOMAIN)" || { echo "用法: make acceptance-e2e DOMAIN=<域>（如 console-auth / console-auth-ui / overview-dashboard / task-schedule）"; exit 2; }
	@if [ "$(BUILD_FRONTEND)" = "1" ]; then npm --prefix apps/console-platform/frontend run build; fi && npm --prefix e2e test -- --config playwright.$(DOMAIN).config.ts

# 15 条跨域 smoke 白名单（e2e/scenarios/smoke-15.scenarios.md，测试以 @smoke 标记）。
# 一次 build，逐域只跑 @smoke；本地 fail-fast，CI 的 smoke job 另行汇总失败。
SMOKE_DOMAINS = console-auth overview-dashboard model-management agent skill mcp platform user-identity settings task-schedule-e2e audit-observability run-observability-e2e webchat-e2e

smoke:
	npm --prefix apps/console-platform/frontend run build
	@set -e; for domain in $(SMOKE_DOMAINS); do \
		echo "== smoke $$domain =="; \
		npm --prefix e2e test -- --config playwright.$$domain.config.ts --grep "@smoke"; \
	done

# changed profile：按 e2e/scenarios/*.scenarios.md 的 trigger_paths 命中当前 diff 才跑非 smoke。
e2e-changed:
	uv run python scripts/e2e_changed.py --run

e2e-changed-plan:
	uv run python scripts/e2e_changed.py --plan

# 显式容量压测（200 用户受限档；1000 用户正式验收仍是 manual/staging 边界）。
# 强制清理 Redis 10–15 的残留 claim（仅在确认没有并发验收时使用）。
acceptance-clean:
	uv run python scripts/clean_acceptance_claims.py --force

capacity-load:
	CAPACITY_LOAD_USERS=200 uv run pytest -q -m capacity_load tests/acceptance/capacity

i18n-check:
	uv run python scripts/check_frontend_i18n.py

error-message-check:
	uv run python scripts/check_error_message_hardcode.py

lint:
	uv run ruff check .

typecheck:
	uv run mypy apps packages

check: compile test i18n-check error-message-check lint typecheck
