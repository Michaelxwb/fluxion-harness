.PHONY: check compile test i18n-check error-message-check

compile:
	python -m compileall -q apps packages migrations

test:
	python -m pytest -q

i18n-check:
	python scripts/check_frontend_i18n.py

error-message-check:
	python scripts/check_error_message_hardcode.py

check: compile test i18n-check error-message-check
