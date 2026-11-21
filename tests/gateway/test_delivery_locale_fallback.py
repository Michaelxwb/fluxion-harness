"""Terminal empty-text fallback uses the tenant locale, not the process default."""

from __future__ import annotations

from pathlib import Path

from muad_api import AppError
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import DeliveryMessage
from muad_im_gateway.api.delivery import _render_message
from muad_im_gateway.application.ports import PlatformSettingsSnapshot

MESSAGES_FILE = Path(__file__).resolve().parents[2] / "config/api-messages.yaml"
TENANT = "tenant-locale"


class _LocaleClient:
    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.calls = 0

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        self.calls += 1
        return PlatformSettingsSnapshot(
            revision=1,
            settings={"locale": {"default_locale": self.locale}},
        )


class _FailingLocaleClient:
    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))


async def test_cancelled_terminal_uses_tenant_locale() -> None:
    client = _LocaleClient("en-US")
    catalog = MessageCatalog(MESSAGES_FILE)

    message = await _render_message(
        DeliveryMessage(type="terminal", text="", status="CANCELLED"),
        catalog,
        settings_client=client,
        tenant_id=TENANT,
    )

    assert message.text == "This run has been stopped."
    assert client.calls == 1


async def test_abandoned_terminal_uses_tenant_locale() -> None:
    client = _LocaleClient("en-US")
    catalog = MessageCatalog(MESSAGES_FILE)

    message = await _render_message(
        DeliveryMessage(type="terminal", text="", status="FAILED", error_code="RUN_ABANDONED"),
        catalog,
        settings_client=client,
        tenant_id=TENANT,
    )

    assert message.text == "The service was interrupted. Please resend your message."


async def test_locale_read_failure_falls_back_to_catalog_default() -> None:
    catalog = MessageCatalog(MESSAGES_FILE)

    message = await _render_message(
        DeliveryMessage(type="terminal", text="", status="CANCELLED"),
        catalog,
        settings_client=_FailingLocaleClient(),
        tenant_id=TENANT,
    )

    assert message.text == "当前任务已停止"


async def test_non_empty_or_non_terminal_message_does_not_read_settings() -> None:
    client = _LocaleClient("en-US")
    catalog = MessageCatalog(MESSAGES_FILE)

    text_message = await _render_message(
        DeliveryMessage(type="text", text="already rendered"),
        catalog,
        settings_client=client,
        tenant_id=TENANT,
    )
    terminal_with_text = await _render_message(
        DeliveryMessage(type="terminal", text="already rendered", status="CANCELLED"),
        catalog,
        settings_client=client,
        tenant_id=TENANT,
    )

    assert text_message.text == "already rendered"
    assert terminal_with_text.text == "already rendered"
    assert client.calls == 0
