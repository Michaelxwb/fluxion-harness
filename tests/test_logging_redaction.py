import json
import logging
from datetime import datetime
from typing import Any

import pytest
from muad_logging import clear_log_context, configure_logging, set_log_context
from muad_logging.formatter import JsonLogFormatter
from muad_logging.redaction import RedactionFilter, redact_text, redact_value


def _record(message: str, *, args: tuple[Any, ...] = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name='test.redaction',
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_redact_text_masks_sensitive_keys():
    assert redact_text('api_key=SECRETVALUE') == 'api_key=***'
    assert redact_text('password: hunter2') == 'password: ***'
    assert redact_text('refresh_token = abc123') == 'refresh_token = ***'


def test_redact_text_masks_auth_scheme_tokens():
    assert redact_text('Bearer SECRETVALUE') == 'Bearer ***'
    assert redact_text('Basic dXNlcjpwYXNz') == 'Basic ***'


def test_redact_text_does_not_leak_authorization_header_token():
    assert 'SECRETVALUE' not in redact_text('Authorization: Bearer SECRETVALUE')


def test_redact_value_masks_nested_structures():
    payload = {
        'access_token': 'abc',
        'nested': {'password': 'p', 'keep': 'visible'},
        'items': [{'cookie': 'c'}, 'Bearer SECRETVALUE'],
    }
    redacted = redact_value(payload)
    assert redacted['access_token'] == '***'
    assert redacted['nested'] == {'password': '***', 'keep': 'visible'}
    assert redacted['items'][0] == {'cookie': '***'}
    assert redacted['items'][1] == 'Bearer ***'


def test_redaction_filter_redacts_message_and_fields():
    record = _record('password=%s', args=('hunter2',))
    record.fields = {'access_token': 'abc', 'keep': 'ok'}
    assert RedactionFilter().filter(record) is True
    assert record.getMessage() == 'password=***'
    assert record.fields == {'access_token': '***', 'keep': 'ok'}


def test_formatter_reserved_keys_win_over_log_context():
    clear_log_context()
    try:
        set_log_context(level='HACKED', message='OVERRIDDEN', trace_id='t1')
        payload = json.loads(JsonLogFormatter('svc').format(_record('hello')))
    finally:
        clear_log_context()
    assert payload['level'] == 'INFO'
    assert payload['message'] == 'hello'
    assert payload['trace_id'] == 't1'


def test_formatter_reserved_keys_win_over_record_fields():
    clear_log_context()
    record = _record('hello')
    record.fields = {'level': 'X', 'message': 'Y', 'custom': 1}
    payload = json.loads(JsonLogFormatter('svc').format(record))
    assert payload['level'] == 'INFO'
    assert payload['message'] == 'hello'
    assert payload['custom'] == 1


def test_configure_logging_rejects_invalid_level(tmp_path):
    with pytest.raises(ValueError):
        configure_logging('test-invalid-level', log_dir=tmp_path, level='NOT-A-LEVEL', console=False)


def test_configure_logging_redacts_secrets_in_log_file(tmp_path):
    service = 'test-redaction-e2e'
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    configure_logging(service, log_dir=tmp_path, console=False)
    try:
        logging.getLogger('test.redaction').info('Authorization: Bearer SECRETVALUE')
    finally:
        logging.shutdown()
        for handler in list(root.handlers):
            root.removeHandler(handler)

    day = datetime.now().astimezone().strftime('%Y-%m-%d')
    text = (tmp_path / service / f'{day}.log').read_text(encoding='utf-8')
    assert 'SECRETVALUE' not in text
    assert '***' in text
