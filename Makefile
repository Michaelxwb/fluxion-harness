.PHONY: check compile test test-migration i18n-check error-message-check lint typecheck

compile:
	uv run python -m compileall -q apps packages migrations

# 共享库上的主套件：**排除**会改 schema 的 test_secret_migration（它 downgrade/upgrade 整库，
# 与其同一次运行会让后续套件连锁失败——历史现象：gateway bind 用例『console server did not become ready』）
test:
	uv run python -m pytest -q --ignore=tests/acceptance/test_secret_migration.py

# 改 schema 的用例单独跑、且放在主套件之后
test-migration:
	uv run python -m pytest -q tests/acceptance/test_secret_migration.py

i18n-check:
	uv run python scripts/check_frontend_i18n.py

error-message-check:
	uv run python scripts/check_error_message_hardcode.py

lint:
	uv run ruff check .

typecheck:
	uv run mypy apps packages

check: compile test test-migration i18n-check error-message-check lint typecheck
