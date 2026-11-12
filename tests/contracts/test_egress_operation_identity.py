"""Opaque persisted call IDs are stable inputs; UUID generation is not required."""
import importlib
import json

import pytest


@pytest.mark.parametrize("value", ["fixed", "固定值", "call/a", "call?a", "x" * 200, "a\0b"])
def test_logical_key_preserves_full_identity_without_path_normalization(value):
    key = importlib.import_module("muad_contracts.egress_identity").egress_operation_key
    assert json.loads(key("TOOL_CALL", value)) == ["TOOL_CALL", value]
    assert key("TOOL_CALL", value) == key("TOOL_CALL", value)
    assert key("TASK_SKILL", value) != key("TOOL_CALL", value)
    assert "\0" not in key("TOOL_CALL", value)


@pytest.mark.parametrize("kind,value", [("TOOL_CALL", ""), ("TOOL_CALL", "  "), ("unknown", "fixed")])
def test_logical_key_rejects_missing_identity_or_unknown_source(kind, value):
    key = importlib.import_module("muad_contracts.egress_identity").egress_operation_key
    with pytest.raises(ValueError):
        key(kind, value)
