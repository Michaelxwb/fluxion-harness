.PHONY: check compile test i18n-check error-message-check lint typecheck

compile:
	uv run python -m compileall -q apps packages migrations

test:
	uv run python -m pytest -q

i18n-check:
	uv run python scripts/check_frontend_i18n.py

error-message-check:
	uv run python scripts/check_error_message_hardcode.py

lint:
	uv run ruff check .

typecheck:
	uv run mypy apps packages

check: compile test i18n-check error-message-check lint typecheck
