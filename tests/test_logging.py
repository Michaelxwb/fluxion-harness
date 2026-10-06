import json
import logging
from datetime import datetime

from muad_logging import configure_logging, set_log_context


def test_log_is_saved_by_service_and_day(tmp_path):
    service = 'test-service'
    configure_logging(service, log_dir=tmp_path, console=False)
    set_log_context(trace_id='trace-1', request_id='req-1')
    logging.getLogger('test').info('hello')
    logging.shutdown()

    day = datetime.now().astimezone().strftime('%Y-%m-%d')
    log_file = tmp_path / service / f'{day}.log'
    assert log_file.exists()
    payload = json.loads(log_file.read_text(encoding='utf-8').strip())
    assert payload['service'] == service
    assert payload['trace_id'] == 'trace-1'
    assert payload['request_id'] == 'req-1'
    assert payload['message'] == 'hello'


def test_unserializable_extra_degrades_to_a_type_placeholder(tmp_path):
    """第三方 logger 把对象塞进 `extra` 时，日志既不报错也不落内容。

    2026-10-06 实测：websockets 的 `self.logger.info("connection open")` 会随 extra 带上连接对象
    （LoggerAdapter），JSON 序列化抛 `TypeError: Object of type ServerConnection is not JSON
    serializable` —— 丢日志 + 往 stderr 刷一坨 "--- Logging error ---"。

    兜底**刻意不取 repr/str**：对象 repr 里可能带请求头/凭据，而脱敏滤镜只看得见键名命中的字段
    与字符串值，repr 是它看不到的通道（密钥不得进入日志）。故只记类型名。
    """

    class Opaque:
        def __repr__(self) -> str:  # pragma: no cover - 断言它**没**被调用
            raise AssertionError('formatter 不得对不可序列化对象取 str/repr')

    service = 'test-service-opaque-extra'
    configure_logging(service, log_dir=tmp_path, console=False)
    logging.getLogger('websockets.server').info('connection open', extra={'connection': Opaque()})
    logging.shutdown()

    day = datetime.now().astimezone().strftime('%Y-%m-%d')
    payload = json.loads(
        (tmp_path / service / f'{day}.log').read_text(encoding='utf-8').strip()
    )
    assert payload['message'] == 'connection open'
    assert payload['connection'] == '<unserializable:Opaque>'
