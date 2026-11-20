import ast
import logging
from datetime import datetime
from pathlib import Path

from muad_logging import clear_log_context, configure_logging, get_logger, set_log_context


def test_log_is_saved_by_service_and_day(tmp_path):
    service = 'test-service'
    clear_log_context()
    configure_logging(service, log_dir=tmp_path, console=False)
    set_log_context(trace_id='trace-1', request_id='req-1')
    get_logger('test').info('hello')
    logging.shutdown()

    day = datetime.now().astimezone().strftime('%Y-%m-%d')
    log_file = tmp_path / service / f'{day}.log'
    assert log_file.exists()
    text = log_file.read_text(encoding='utf-8').strip()
    assert text.startswith(f"{datetime.now().astimezone().strftime('%Y-%m-%d')} ")
    assert f"[INFO] {service} test hello" in text
    assert "trace_id=trace-1" in text
    assert "request_id=req-1" in text
    assert not text.startswith("{")


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
    text = (tmp_path / service / f'{day}.log').read_text(encoding='utf-8').strip()
    assert 'connection open' in text
    assert 'connection=<unserializable:Opaque>' in text


def test_uvicorn_records_use_the_same_redacted_output(tmp_path, monkeypatch):
    import io

    old_output = io.StringIO()
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [])
        monkeypatch.setattr(logger, "propagate", logger.propagate)
        monkeypatch.setattr(logger, "level", logger.level)
    server = logging.getLogger("uvicorn")
    server.addHandler(logging.StreamHandler(old_output))
    server.propagate = False

    service = "test-uvicorn-routing"
    configure_logging(service, log_dir=tmp_path, console=False)
    logging.getLogger("uvicorn.error").warning("server warning", extra={"password": "must-not-appear"})
    logging.getLogger("uvicorn.access").info("ordinary access")
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    output = (tmp_path / service / f"{day}.log").read_text(encoding="utf-8")
    assert old_output.getvalue() == ""
    assert output.count("[WARNING] test-uvicorn-routing uvicorn.error server warning") == 1
    assert "password=***" in output and "must-not-appear" not in output
    assert "ordinary access" not in output


def test_business_modules_get_loggers_from_logging_kit():
    root = Path(__file__).resolve().parents[1]
    sources = sorted(path for root_dir in ("apps", "packages")
                     for path in (root / root_dir).glob("**/src/**/*.py"))
    violations = []
    for path in sources:
        if path.is_relative_to(root / "packages/logging-kit"):
            continue
        nodes = list(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
        logging_names = {
            alias.asname or alias.name
            for node in nodes if isinstance(node, ast.Import)
            for alias in node.names if alias.name == "logging"
        }
        for node in nodes:
            if (isinstance(node, ast.ImportFrom) and node.module == "logging"
                and any(alias.name == "getLogger" for alias in node.names)):
                violations.append(f"{path.relative_to(root)}:{node.lineno}")
            if (isinstance(node, ast.Attribute) and node.attr == "getLogger"
                and isinstance(node.value, ast.Name) and node.value.id in logging_names):
                violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not violations, "Use muad_logging.get_logger:\n" + "\n".join(violations)
