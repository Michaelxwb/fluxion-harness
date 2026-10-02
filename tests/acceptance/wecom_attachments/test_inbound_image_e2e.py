"""S-01 / S-02 / S-05：入站附件的**需求级**端到端基线。

不得 Mock 的真实边界：真实企微 WS 探针（官方 SDK 认证与收发）→ 真实 Gateway/Runtime/
Console/Worker 进程 → 真实 HTTP 媒体源（真实 AES-256-CBC 密文）→ 真实共享 artifact store →
真实 PostgreSQL（`runtime.artifact` / `runtime.tool_call_audit` 逐行回读）→ **真实模型请求体**
（OpenAI 兼容端点是 HTTP 探针，逐次记录完整 body）。

**这里的"模型"是脚本化的**（`POST /script`）：探针按用例脚本决定调不调工具、调哪个，
并且——这一点很关键——**附件 ID 由它从模型实际收到的上下文里读出来**（`$last_artifact_id`
占位符），而不是让用例预先算一个塞进去：那样就绕过了"模型到底拿没拿到寻址标识"这件要验的事。

所以本文件能验的是"请求体里真的带了图像块 / 工具结果里真的有文档正文"这类**跨边界事实**；
"真实多模态供应商模型看图答得对"需要外部凭据，本环境没有，属 TASK-010 证据里登记的残余。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
)
from tests.e2e.wecom_media_server import AES_KEY, MediaServer, wecom_ciphertext
from tests.e2e.wecom_probe_app import frame_text

WAIT_SEC = 150.0
PDF_TEXT = "DEVICE XL-900"
#: 两张**不同**的图：S-05 要证明"重看的是历史那一张"，不能用同一份字节
PNG_S01 = b"\x89PNG\r\n\x1a\n" + b"s01-device-panel" * 6
PNG_S05 = b"\x89PNG\r\n\x1a\n" + b"s05-history-shot" * 6
PNG_S05_B64 = base64.b64encode(PNG_S05).decode("ascii")


# --------------------------------------------------------------------------- 夹具


def _pdf_bytes(text_value: str) -> bytes:
    """一份**内容已知**的最小真实 pdf（交给真实解析库读，不 mock）。"""
    content = f"BT /F1 12 Tf 20 100 Td ({text_value}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    start_xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(start_xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


# --------------------------------------------------------------------- 驱动与观测


def _callback(message_id: str, reply_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": reply_id},
        "body": {
            "msgid": message_id,
            "chatid": CHAT_ID,
            "from": {"userid": BOUND_EXTERNAL_USER_ID},
            **body,
        },
    }


def _item(media_type: str, url: str) -> dict[str, Any]:
    return {"msgtype": media_type, media_type: {"url": url, "aeskey": AES_KEY}}


def _mixed(*items: dict[str, Any]) -> dict[str, Any]:
    return {"msgtype": "mixed", "mixed": {"msg_item": list(items)}}


def _text_item(content: str) -> dict[str, Any]:
    return {"msgtype": "text", "text": {"content": content}}


def _new_message() -> tuple[str, str]:
    token = uuid.uuid4().hex[:8]
    return f"e2e-{token}", f"req-e2e-{token}"


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate() if asyncio.iscoroutinefunction(predicate) else predicate():
            return
        await asyncio.sleep(0.3)
    raise AssertionError(what)


async def _fetch(statement: str, params: dict[str, object]) -> list[dict[str, Any]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text(statement), params)).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


def _stored(root: Path, message_id: str) -> dict[str, bytes]:
    folder = root / "inbound" / message_id
    if not folder.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir()) if path.is_file()}


async def _wait_reply(probe: Any, reply_id: str, *, timeout: float = WAIT_SEC) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        texts = [
            frame_text(frame)
            for frame in probe.replies
            if (frame.get("headers") or {}).get("req_id") == reply_id
        ]
        if texts and texts[-1]:
            return texts[-1]
        await asyncio.sleep(0.2)
    raise AssertionError(f"回调 {reply_id} 未在 {timeout}s 内得到回复")


async def _push(stack: GatewayStack, frame: dict[str, Any]) -> None:
    probe = stack.ws_probe
    assert probe is not None
    await probe.push_raw_frame(bot_id=BOT_ID, frame=frame)  # type: ignore[attr-defined]


async def _wait_connected(stack: GatewayStack) -> Any:
    probe = stack.ws_probe
    assert probe is not None
    await _wait_for(
        lambda: any(
            (frame.get("body") or {}).get("bot_id") == BOT_ID
            for frame in probe.frames_of("aibot_subscribe")  # type: ignore[attr-defined]
        ),
        what="Gateway 未完成与探针的真实 WS 认证",
    )
    return probe


async def _script(stack: GatewayStack, payload: dict[str, Any]) -> None:
    """设定模型行为（探针进程按请求读它）；传 `{}` 清除。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(f"{stack.llm_url}/script", json=payload)
    response.raise_for_status()


def _requests(stack: GatewayStack) -> list[dict[str, Any]]:
    """探针记录的**全部**模型请求体（最近 50 条），按发生顺序。"""
    response = httpx.get(f"{stack.llm_url}/requests", timeout=10.0)
    response.raise_for_status()
    return list(response.json().get("requests") or [])


def _content_parts(message: dict[str, Any]) -> list[Any]:
    content = message.get("content")
    return content if isinstance(content, list) else []


def _image_payloads(messages: list[Any]) -> list[str]:
    """消息里所有图像内容块的 base64（去掉 data URL 前缀）。"""
    found: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        for part in _content_parts(message):
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url") or "")
                found.append(url.split("base64,", 1)[-1])
    return found


def _tool_texts(messages: list[Any]) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in messages
        if isinstance(message, dict) and message.get("role") == "tool"
    ]


async def _wait_resent_image(
    stack: GatewayStack, since: int, expected_b64: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """等"重看之后那张历史图片作为内容块被重发"；返回 (全部请求体, 本轮之后出现过的图像块)。

    失败时把观察到的图像块 base64 前缀带出来——只报"没等到"没法判断是"没重发"还是
    "重发的是别的图"。
    """
    deadline = time.monotonic() + WAIT_SEC
    observed: list[str] = []
    bodies: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        bodies = _requests(stack)
        observed = [
            payload
            for body in bodies[since:]
            for payload in _image_payloads(body.get("messages") or [])
        ]
        if expected_b64 in observed:
            return bodies, observed
        await asyncio.sleep(0.3)
    return bodies, observed


async def _wait_requests(stack: GatewayStack, predicate: Any, *, what: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + WAIT_SEC
    while time.monotonic() < deadline:
        bodies = _requests(stack)
        if predicate(bodies):
            return bodies
        await asyncio.sleep(0.3)
    raise AssertionError(f"{what}（当前收到 {len(_requests(stack))} 次模型请求）")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------- S-01 主链


@pytest.mark.e2e
async def test_s01_image_is_persisted_and_sent_to_the_model_as_a_content_block(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """S-01：图片 → 落盘 → 契约/落库 → **请求体含图像内容块** → 回答回到渠道。"""
    probe = await _wait_connected(gateway_stack)
    await _script(gateway_stack, {"final_text": "图里是设备面板"})
    message_id, reply_id = _new_message()
    question = "这张图里写了什么？"
    url = media_server.serve(f"/s01/{message_id}", wecom_ciphertext(PNG_S01), filename="panel.png")
    await _push(
        gateway_stack,
        _callback(message_id, reply_id, _mixed(_text_item(question), _item("image", url))),
    )

    reply = await _wait_reply(probe, reply_id)
    assert reply, "回答必须经真实链路回到渠道"

    # ① 落盘且可读回（共享 artifact store 上的真实字节）
    stored = _stored(gateway_stack.artifact_root, message_id)
    assert stored == {"0": PNG_S01}

    # ② 契约承载 → Runtime 落库
    rows = await _fetch(
        "SELECT storage_key, checksum, size, artifact_type FROM runtime.artifact "
        "WHERE tenant_id = :t AND storage_key LIKE :k AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": f"inbound/{message_id}/%"},
    )
    assert len(rows) == 1
    assert rows[0]["checksum"] == hashlib.sha256(PNG_S01).hexdigest()
    assert rows[0]["artifact_type"] == "INBOUND_IMAGE"

    # ③ 发给模型的请求体：提问在文本里，图片作为**图像内容块**内联，且 base64 与源字节一致
    bodies = await _wait_requests(
        gateway_stack,
        lambda items: any(_image_payloads(body.get("messages") or []) for body in items),
        what="模型请求体里始终没有出现图像内容块",
    )
    target = next(
        body for body in bodies if _image_payloads(body.get("messages") or [])
    )
    messages = target["messages"]
    assert any(question in json.dumps(message, ensure_ascii=False) for message in messages)
    assert _image_payloads(messages)[0] == _b64(PNG_S01)


# ---------------------------------------------------------------------- S-02 文档


@pytest.mark.e2e
async def test_s02_document_is_extracted_by_the_real_tool_and_answered(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """S-02：文档 → 落盘 → **模型按上下文里的附件 ID 调真工具** → 抽取正文与文档一致。"""
    probe = await _wait_connected(gateway_stack)
    await _script(
        gateway_stack,
        {
            "tool_name": "read_attachment",
            "tool_arguments": json.dumps({"artifact_id": "$last_artifact_id"}),
            "final_text": f"文档里写的是 {PDF_TEXT}",
        },
    )
    message_id, reply_id = _new_message()
    url = media_server.serve(
        f"/s02/{message_id}", wecom_ciphertext(_pdf_bytes(PDF_TEXT)), filename="manual.pdf"
    )
    await _push(
        gateway_stack,
        _callback(
            message_id,
            reply_id,
            _mixed(_text_item("这份文档里的设备型号是什么？"), _item("file", url)),
        ),
    )

    reply = await _wait_reply(probe, reply_id)
    assert reply

    # ① 文档落盘
    stored = _stored(gateway_stack.artifact_root, message_id)
    assert list(stored) == ["0"] and stored["0"].startswith(b"%PDF-")

    # ② 工具真的被调用，且**用的是上下文里那个 ID**（探针从提示里读出来的）
    rows = await _fetch(
        "SELECT id FROM runtime.artifact WHERE tenant_id = :t AND storage_key LIKE :k "
        "AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": f"inbound/{message_id}/%"},
    )
    assert len(rows) == 1
    artifact_id = str(rows[0]["id"])
    bodies = await _wait_requests(
        gateway_stack,
        lambda items: any(_tool_texts(body.get("messages") or []) for body in items),
        what="模型始终没有收到工具结果",
    )
    call_arguments = [
        json.loads(call["function"]["arguments"])
        for body in bodies
        for message in body.get("messages") or []
        if isinstance(message, dict)
        for call in (message.get("tool_calls") or [])
    ]
    assert call_arguments and call_arguments[0]["artifact_id"] == artifact_id

    # ③ 工具结果里的正文与文档实际内容一致（真实解析库抽出来的）
    tool_text = next(
        text_value
        for body in bodies
        for text_value in _tool_texts(body.get("messages") or [])
        if text_value
    )
    assert PDF_TEXT in tool_text

    # ④ 真实工具调用留痕
    audits = await _fetch(
        "SELECT tool_name, status FROM runtime.tool_call_audit "
        "WHERE tenant_id = :t AND tool_name = 'read_attachment' ORDER BY create_time",
        {"t": gateway_stack.tenant_id},
    )
    assert audits and audits[-1]["tool_name"] == "read_attachment"


# ------------------------------------------------------------------- S-05 重看历史图


@pytest.mark.e2e
async def test_s05_historical_image_is_resent_as_a_content_block_after_view_image(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """S-05 / RULE-03：历史轮次的图片**不进**上下文；模型主动重看后，它作为新内容块被重发。"""
    probe = await _wait_connected(gateway_stack)
    await _script(gateway_stack, {"final_text": "收到图片"})
    # 基线：探针记录是**跨用例**累积的（同一模块复用同一进程），只看本轮之后新增的请求
    baseline = len(_requests(gateway_stack))
    first_id, first_reply = _new_message()
    first_url = media_server.serve(
        f"/s05/{first_id}", wecom_ciphertext(PNG_S05), filename="history.png"
    )
    await _push(
        gateway_stack,
        _callback(first_id, first_reply, _mixed(_text_item("先看这张图"), _item("image", first_url))),
    )
    await _wait_reply(probe, first_reply)
    await _wait_requests(
        gateway_stack,
        lambda items: any(
            _image_payloads(body.get("messages") or []) for body in items[baseline:]
        ),
        what="第一轮的图片没有作为内容块发出",
    )
    seen = len(_requests(gateway_stack))

    # 第二轮：提问并让模型调重看工具
    await _script(
        gateway_stack,
        {
            "tool_name": "view_image",
            "tool_arguments": json.dumps({"artifact_id": "$last_artifact_id"}),
            "final_text": "重看过了",
        },
    )
    second_id, second_reply = _new_message()
    await _push(
        gateway_stack,
        _callback(second_id, second_reply, {"msgtype": "text", "text": {"content": "再看一眼刚才那张图"}}),
    )
    await _wait_reply(probe, second_reply)

    bodies, observed = await _wait_resent_image(gateway_stack, seen, PNG_S05_B64)
    assert any(
        PNG_S05_B64 in payload for payload in observed
    ), f"重看后重发的不是那张历史图片；观察到的图像块（base64 前缀）：{[p[:24] for p in observed]}"
    second_turn = bodies[seen:]
    # ① 第二轮**首个**请求里没有图像块：历史附件只留文本引用（RULE-03）
    assert _image_payloads(second_turn[0].get("messages") or []) == []
    assert any(
        "附件 ID" in json.dumps(message, ensure_ascii=False)
        for message in second_turn[0].get("messages") or []
    ), "历史图片应保留带 artifact_id 的文本引用"
    # ② 重看之后，那张**历史图片本身**被作为内容块重发（字节与第一轮一致）
    resent = [
        payload for body in second_turn for payload in _image_payloads(body.get("messages") or [])
    ]
    assert resent and all(payload == PNG_S05_B64 for payload in resent)
