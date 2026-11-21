"""Real TLS WS → Connector → Redis → Gateway → Runtime/PG → reply; real HTTP media."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import httpx
import pytest
from muad_common import process_settings
from muad_common.redis_client import create_redis_client
from muad_contracts.im import inbound_batch_member_key
from muad_im_core.inbound_batch_journal import RedisBatchJournal

from tests.acceptance.im_gateway.environment import GatewayStack
from tests.acceptance.im_gateway.test_execution_progress import _wait_final
from tests.acceptance.im_gateway.test_wecom_attachments import (
    _callback,
    _fetch,
    _wait_connected,
    _wait_reply,
)
from tests.e2e.wecom_media_server import AES_KEY, MediaServer, wecom_ciphertext
from tests.e2e.wecom_probe_app import frame_text

pytestmark = pytest.mark.integration


@pytest.fixture
def media_source() -> Iterator[MediaServer]:
    media = MediaServer()
    try:
        yield media
    finally:
        media.close()


async def _push_text(stack: GatewayStack, message_id: str, text: str, *, chat_id: str | None = None) -> None:
    probe = await _wait_connected(stack)
    await probe.push_message(bot_id=stack.bot_id, message_id=message_id, text=text,
                             external_user_id=stack.bound_external_user_id,
                             chat_id=chat_id or stack.chat_id, reply_id=f"req-{message_id}")


async def _push_file(stack: GatewayStack, message_id: str, url: str) -> None:
    probe = await _wait_connected(stack)
    await probe.push_raw_frame(bot_id=stack.bot_id, frame=_callback(
        message_id, f"req-{message_id}", {"msgtype": "file", "file": {"url": url, "aeskey": AES_KEY}}))


async def _run_count(stack: GatewayStack, message_id: str) -> int:
    rows = await _fetch(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id=:t AND input_message_id=:m",
        {"t": stack.tenant_id, "m": message_id})
    return rows[0][0]


async def _pending_batch(stack: GatewayStack, message_id: str) -> None:
    client = create_redis_client(process_settings().redis_url, decode_responses=True)
    try:
        async with asyncio.timeout(10):
            while True:
                batch_id = await client.get(
                    inbound_batch_member_key(stack.tenant_id, stack.bot_id, message_id))
                if batch_id:
                    record = await RedisBatchJournal(client).load(
                        tenant_id=stack.tenant_id, bot_id=stack.bot_id, batch_id=batch_id)
                    if record is not None and record.state == "PENDING":
                        return
                await asyncio.sleep(0.05)
    finally:
        await client.aclose()


async def test_single_text_has_progress_and_one_final_reply(gateway_stack: GatewayStack) -> None:
    stack = gateway_stack
    probe = await _wait_connected(stack)
    marker = f"natural-{uuid.uuid4().hex}"
    since = len(probe.received)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"{stack.llm_url}/script", json={
            "tool_name": "current_time", "tool_arguments": "{}", "final_text": marker, "delay_ms": 600})
        response.raise_for_status()
        try:
            await _push_text(stack, marker, "查询当前时间")
            frames = await _wait_final(probe, since, marker)
        finally:
            (await client.post(f"{stack.llm_url}/script", json={})).raise_for_status()
    assert any("思考中" in frame_text(frame) for frame in frames)
    assert any("执行中" in frame_text(frame) for frame in frames)
    assert sum(frame["body"]["stream"]["finish"] for frame in frames) == 1
    assert await _run_count(stack, marker) == 1


async def test_failed_member_rejects_entire_batch(
    gateway_stack: GatewayStack, media_source: MediaServer,
) -> None:
    stack = gateway_stack
    token = uuid.uuid4().hex
    await _push_text(stack, token, "读取这次发送的全部附件")
    await _push_file(stack, f"{token}-file", f"{media_source.base_url}/expired")
    probe = await _wait_connected(stack)
    reply = await _wait_reply(probe, f"req-{token}")
    assert "本次未处理" in reply or "未处理" in reply
    assert await _run_count(stack, token) == 0
    assert await _run_count(stack, f"{token}-file") == 0


async def test_slow_media_does_not_block_other_route_text_or_command(
    gateway_stack: GatewayStack, media_source: MediaServer,
) -> None:
    stack = gateway_stack
    probe = await _wait_connected(stack)
    token = uuid.uuid4().hex
    url = media_source.serve(f"/{token}", wecom_ciphertext(b"slow input"), filename="slow.txt", delay=10)
    await _push_text(stack, token, "读取附件")
    await _push_file(stack, f"{token}-file", url)
    await _pending_batch(stack, f"{token}-file")
    await _push_text(stack, f"{token}-new", "/new", chat_id=f"other-{token}")
    reply = await _wait_reply(probe, f"req-{token}-new")
    assert reply
    assert not (stack.artifact_root / "inbound" / f"{token}-file").exists()
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"{stack.connector_url}/readyz")
        response.raise_for_status()
        assert response.json()["attachment_tasks"] > 0
    # A same-route command must close the unfinished batch honestly, without waiting for HTTP.
    await _push_text(stack, f"{token}-stop", "/stop")
    assert await _wait_reply(probe, f"req-{token}-stop")
    failure = await _wait_reply(probe, f"req-{token}")
    assert "未处理" in failure
    assert await _run_count(stack, token) == 0


async def test_restart_during_download_reports_original_batch_failure(
    gateway_stack: GatewayStack, media_source: MediaServer,
) -> None:
    stack = gateway_stack
    probe = await _wait_connected(stack)
    token = uuid.uuid4().hex
    await _push_text(stack, token, "读取全部附件")
    url = media_source.serve(f"/{token}", wecom_ciphertext(b"unfinished"), filename="slow.txt", delay=10)
    await _push_file(stack, f"{token}-file", url)
    await _pending_batch(stack, f"{token}-file")
    process = stack.processes["connector"]
    assert process._process is not None
    process._process.kill()
    process._process.wait(timeout=10)
    process.start()
    reply = await _wait_reply(probe, f"req-{token}")
    assert "未处理" in reply
    assert await _run_count(stack, token) == 0
    assert await _run_count(stack, f"{token}-file") == 0
