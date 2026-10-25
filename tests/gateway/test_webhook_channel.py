"""S-07 单元面：通用 Webhook 适配器的验签/时间戳/规范化与配置装配。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from muad_common import SharedSettings
from muad_im_gateway.channels.registry import build_channel_registry
from muad_im_gateway.channels.webhook import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookAuthError,
    WebhookChannelAdapter,
    WebhookChannelConfig,
    parse_webhook_channels,
)
from pydantic import ValidationError

SECRET = "unit-webhook-secret"
CHANNEL = "WECOM"


def _adapter(**kwargs: object) -> WebhookChannelAdapter:
    return WebhookChannelAdapter(WebhookChannelConfig(channel=CHANNEL, secret=SECRET), **kwargs)


def _body(event_id: str = "6f1c7a1e-2f5b-4c49-9a9a-5f6e3f0a1b2c") -> bytes:
    return json.dumps(
        {
            "event_id": event_id,
            "bot_id": "bot-1",
            "external_user_id": "user-1",
            "external_conversation_id": "conv-1",
            "message_id": "msg-1",
            "text": "hi",
        }
    ).encode()


def _headers(timestamp: int, raw: bytes, *, secret: str = SECRET) -> dict[str, str]:
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return {TIMESTAMP_HEADER: str(timestamp), SIGNATURE_HEADER: signature}


def test_valid_signature_is_accepted_and_normalized() -> None:
    adapter = _adapter()
    raw = _body()
    adapter.verify(raw_body=raw, headers=_headers(int(time.time()), raw))

    event = adapter.normalize(raw_body=raw, tenant_id="t1")

    assert event.channel == CHANNEL
    assert event.tenant_id == "t1"
    assert event.external_message_id == "msg-1"
    assert str(event.event_id) == "6f1c7a1e-2f5b-4c49-9a9a-5f6e3f0a1b2c"


def test_wrong_signature_is_rejected() -> None:
    adapter = _adapter()
    raw = _body()
    with pytest.raises(WebhookAuthError):
        adapter.verify(raw_body=raw, headers=_headers(int(time.time()), raw, secret="other"))


def test_stale_or_future_timestamp_is_rejected() -> None:
    adapter = _adapter(timestamp_tolerance_sec=300)
    raw = _body()
    for timestamp in (int(time.time()) - 301, int(time.time()) + 301):
        with pytest.raises(WebhookAuthError):
            adapter.verify(raw_body=raw, headers=_headers(timestamp, raw))


def test_missing_or_malformed_headers_are_rejected() -> None:
    adapter = _adapter()
    raw = _body()
    for headers in (
        {},
        {TIMESTAMP_HEADER: "not-a-number", SIGNATURE_HEADER: "x"},
        {TIMESTAMP_HEADER: str(int(time.time()))},
    ):
        with pytest.raises(WebhookAuthError):
            adapter.verify(raw_body=raw, headers=headers)


def test_body_without_event_id_is_rejected() -> None:
    adapter = _adapter()
    raw = json.dumps({"bot_id": "b", "external_user_id": "u", "message_id": "m"}).encode()
    with pytest.raises(ValidationError):
        adapter.normalize(raw_body=raw, tenant_id="t1")


def test_extra_body_fields_are_rejected() -> None:
    adapter = _adapter(timestamp_tolerance_sec=300)
    raw = json.dumps(
        {
            "event_id": "6f1c7a1e-2f5b-4c49-9a9a-5f6e3f0a1b2c",
            "bot_id": "b",
            "external_user_id": "u",
            "message_id": "m",
            "url": "https://example.invalid/secret",
        }
    ).encode()
    with pytest.raises(ValidationError):
        adapter.normalize(raw_body=raw, tenant_id="t1")


def test_registry_adds_webhook_channel_from_configuration_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GATEWAY_WEBHOOK_CHANNELS",
        json.dumps([{"channel": CHANNEL, "secret": SECRET, "reply_url": None}]),
    )
    registry = build_channel_registry(SharedSettings())

    assert registry.names() == ("WEBCHAT", CHANNEL)
    assert isinstance(registry.get(CHANNEL), WebhookChannelAdapter)


def test_registry_without_webhook_configuration_is_webchat_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_WEBHOOK_CHANNELS", "")
    assert build_channel_registry(SharedSettings()).names() == ("WEBCHAT",)


def test_webhook_channel_config_parsing_rejects_bad_input() -> None:
    for raw in (
        "not-json",
        "{}",
        '[{"channel": "", "secret": "s"}]',
        '[{"channel": "WECOM", "secret": ""}]',
        '[{"channel": "WECOM", "secret": "a"}, {"channel": "WECOM", "secret": "b"}]',
    ):
        with pytest.raises(ValueError):
            parse_webhook_channels(raw)
