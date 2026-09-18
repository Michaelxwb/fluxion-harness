from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/user-identity"
MESSAGES = ROOT / "config/api-messages.yaml"
GATEWAY_INBOUND = ROOT / "tests/gateway/test_inbound.py"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_e05_bind_code_failure_keeps_sidesheet_open() -> None:
    source = _source("UserDetailTabs.tsx")
    assert "createBindCode(props.userId)" in source
    generate = source[source.index("const generate = async") : source.index("const unbind =")]
    assert "catch" in generate
    assert "setBindCode(null)" in generate
    assert "props.onCancel" not in generate, "生成失败不得关闭详情 SideSheet"


def test_e07_expired_bind_code_message_is_localized() -> None:
    payload = yaml.safe_load(MESSAGES.read_text(encoding="utf-8"))
    expired = payload["codes"]["BIND_CODE_EXPIRED"]["messages"]
    assert expired["zh-CN"] and expired["en-US"]
    assert payload["codes"]["BIND_CODE_EXPIRED"]["http_status"] == 410
    inbound = GATEWAY_INBOUND.read_text(encoding="utf-8")
    assert "bind" in inbound.lower()


def test_user_form_uses_shared_form_modal_with_hints() -> None:
    source = _source("UserFormModal.tsx")
    assert "components/common/FormModal" in source
    assert "width={520}" in source
    assert "common.save" in source
    assert "user.form.userCodeImmutable" in source
    assert "extraText" in source
    order = source.index('field="display_name"') < source.index('field="user_code"')
    assert order, "字段顺序必须为 姓名 → 账号 → 启用状态"
