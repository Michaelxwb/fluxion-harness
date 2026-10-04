"""S-401: real WS, Gateway, Runtime/PG and model HTTP → native progress reply."""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import httpx
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import GatewayStack
from tests.e2e.wecom_probe_app import WeComProbe, frame_text


async def test_s401_real_run_switches_model_tool_and_elapsed(gateway_stack: GatewayStack) -> None:
    stack = gateway_stack
    probe = stack.ws_probe
    assert isinstance(probe, WeComProbe)
    marker = f"progress-{uuid4().hex}"
    since = len(probe.received)
    deadline = time.monotonic() + 45
    while stack.bot_id not in probe.connection_bots.values():
        assert time.monotonic() < deadline, "Gateway bot did not connect to TLS probe"
        await asyncio.sleep(0.05)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{stack.llm_url}/script",
            json={
                "tool_name": "current_time",
                "tool_arguments": "{}",
                "final_text": marker,
                "delay_ms": 1500,
            },
        )
        response.raise_for_status()
        try:
            await probe.push_message(
                bot_id=stack.bot_id,
                message_id=marker,
                external_user_id=stack.bound_external_user_id,
                chat_id=stack.chat_id,
                text="查询当前时间",
                reply_id=f"req-{marker}",
            )
            frames = await _wait_final(probe, since, marker)
        finally:
            (await client.post(f"{stack.llm_url}/script", json={})).raise_for_status()
    contents = [frame_text(frame) for frame in frames]
    assert any("<think>🔵 思考中" in content for content in contents)
    assert any("<think>⚙️ 执行中" in content for content in contents)
    assert any("已执行 00:01" in content for content in contents)
    assert contents[-1] == marker
    assert len({frame["body"]["stream"]["id"] for frame in frames}) == 1
    assert sum(frame["body"]["stream"]["finish"] for frame in frames) == 1
    await _assert_persisted_events(stack)


async def _wait_final(probe: WeComProbe, since: int, marker: str) -> list[dict]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        frames = [
            item.frame
            for item in probe.received[since:]
            if item.frame.get("cmd") == "aibot_respond_msg"
            and item.frame.get("body", {}).get("msgtype") == "stream"
        ]
        if frames and frame_text(frames[-1]) == marker and frames[-1]["body"]["stream"]["finish"]:
            return frames
        await asyncio.sleep(0.05)
    raise AssertionError("No completed progress reply on real WebSocket")


async def _assert_persisted_events(stack: GatewayStack) -> None:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT e.stream_type FROM runtime.canonical_event e "
                            "JOIN runtime.run_record r ON r.id = e.run_id "
                            "WHERE r.tenant_id = :tenant AND r.input_text = :input "
                            "ORDER BY e.seq"
                        ),
                        {"tenant": stack.tenant_id, "input": "查询当前时间"},
                    )
                )
                .scalars()
                .all()
            )
        assert rows.count("model.started") == 2
        assert rows.count("model.completed") == 2
        assert rows.index("model.completed") < rows.index("tool.started")
        assert rows.index("tool.completed") < rows.index("run.completed")
    finally:
        await engine.dispose()
