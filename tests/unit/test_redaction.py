from framework.observability.logging import _redact


def test_sensitive_values_are_redacted():
    value = {
        "Authorization": "Bearer secret",
        "nested": {"token": "abc", "safe": "ok"},
    }
    redacted = _redact(value)
    assert redacted["Authorization"] == "***"
    assert redacted["nested"]["token"] == "***"
    assert redacted["nested"]["safe"] == "ok"
