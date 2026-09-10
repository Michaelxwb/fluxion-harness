from framework.web.errors import AppError, ConflictError, NotFoundError
from framework.web.response import failure, ok


def test_ok_response_shape():
    response = ok({"value": 1})
    assert response.code == "OK"
    assert response.message == "success"
    assert response.data == {"value": 1}
    assert response.request_id
    assert response.timestamp is not None


def test_failure_response_shape():
    response = failure(code="SERVICE_CONFIGURATION_INVALID", message="bad scope", data={"t": "tenant"})
    assert response.code == "SERVICE_CONFIGURATION_INVALID"
    assert response.message == "bad scope"
    assert response.data == {"t": "tenant"}
    assert response.request_id
    assert response.timestamp is not None


def test_app_error_carries_code_status_and_details():
    exc = AppError(code="SCOPE_INVALID", message="invalid", status_code=400, details={"f": "tenant_id"})
    assert exc.code == "SCOPE_INVALID"
    assert exc.status_code == 400
    assert exc.details == {"f": "tenant_id"}
    # handler maps AppError 1:1 into the failure envelope
    envelope = failure(code=exc.code, message=exc.message, data=exc.details)
    assert envelope.code == "SCOPE_INVALID"
    assert NotFoundError().status_code == 404
    assert ConflictError().status_code == 409
