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


def test_redaction_filter_redacts_message():
    record = _record('password=%s', args=('hunter2',))
    assert RedactionFilter().filter(record) is True
    assert record.getMessage() == 'password=***'


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(RedactionFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_extra_fields_reach_the_formatter_and_are_redacted():
    """`extra={...}` 是标准库唯一的附加字段通道 —— 输出与脱敏**必须同时**覆盖它。

    此前两条链路都只认 `record.fields`（全仓 **0 个调用方**），于是 14 处
    `extra={...}`（`schedule_id`/`run_id`/`memory_key`/`metric`…）的结构化字段**静默丢失**；
    更糟的是脱敏边界挂在一个没人用的通道上 —— 谁单独把 formatter 那半修好，未脱敏的
    `extra` 就直接落盘。这条用例同时钉住两半，正是为了避免这种"只修一半"。
    """
    handler = _CaptureHandler()
    logger = logging.getLogger('test.extra-fields')
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)  # root 默认 WARNING，不设这一级 info 根本走不到 handler
    logger.propagate = False
    try:
        logger.info('scheduler_tick', extra={'schedule_id': 'sch-1', 'api_key': 'SECRETVALUE'})
    finally:
        logger.handlers = []

    record = handler.records[0]
    assert record.api_key == '***', '敏感键名必须按**键名**脱敏，而不是只处理值'
    payload = json.loads(JsonLogFormatter('svc').format(record))
    assert payload['schedule_id'] == 'sch-1'
    assert payload['api_key'] == '***'
    assert 'SECRETVALUE' not in json.dumps(payload)


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


def test_formatter_reserved_keys_win_over_extra_fields():
    """保留键不得被 `extra` 覆写 —— `extra` 挂在 record 属性上，跟保留键同名时优先级要明确。"""
    clear_log_context()
    record = _record('hello')
    record.level = 'X'
    record.message = 'Y'
    record.custom = 1
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


def test_configure_logging_writes_extra_fields_and_redacts_them(tmp_path):
    """端到端：`extra={...}` 必须**既落进日志文件、又不泄露敏感值**。

    这是整条链路（root handler + RedactionFilter + JsonLogFormatter）的合并断言 ——
    上一版这两半各改各的，单看任何一半都是绿的，合起来却一条字段都写不出去。
    """
    service = 'test-extra-fields-file'
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    configure_logging(service, log_dir=tmp_path, console=False)
    try:
        logging.getLogger('test.redaction').info(
            'scheduler_tick', extra={'schedule_id': 'sch-1', 'api_key': 'SECRETVALUE'}
        )
    finally:
        logging.shutdown()
        for handler in list(root.handlers):
            root.removeHandler(handler)

    day = datetime.now().astimezone().strftime('%Y-%m-%d')
    text = (tmp_path / service / f'{day}.log').read_text(encoding='utf-8')
    assert 'SECRETVALUE' not in text
    payload = json.loads(text.strip().splitlines()[-1])
    assert payload['schedule_id'] == 'sch-1'
    assert payload['api_key'] == '***'
