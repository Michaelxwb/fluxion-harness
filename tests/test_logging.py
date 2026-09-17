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
