"""S-04 / E-01 / E-02 / E-03 / E-07：企微入站附件的端到端落地与反馈。

不得 Mock 的真实边界：真实 WS 探针（官方 SDK 认证与收发）→ 真实 Gateway 进程 →
**真实 HTTP 媒体源**（真实 AES-256-CBC 密文，探针进程内的 `MediaServer`）→ 真实共享
artifact store → 真实 PostgreSQL（Runtime 落的 `artifact` 行逐行回读）→ 真实 Console
内部端点（`control.im_inbound_audit` 逐行回读）。

这里验的是"用户发了东西之后**到底发生了什么**"：能收的收下并可分别读取，收不了的给出
明确说明并留下审计——**没有静默路径**（RULE-01）。验收类不制造 RED。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from muad_api.catalog import MessageCatalog
from muad_common import SharedSettings
from muad_im_gateway.application.attachment_gate import MAX_ATTACHMENT_BYTES
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

CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "api-messages.yaml"
WAIT_ARTIFACT_SEC = 90.0
WAIT_REPLY_SEC = 90.0
MIB = 1024 * 1024

PNG_A = b"\x89PNG\r\n\x1a\n" + b"shot-A" * 8
PNG_B = b"\x89PNG\r\n\x1a\n" + b"shot-B" * 8
NOTES = "# 巡检记录\n- 设备 A 正常\n".encode()
CASE_ITEMS: tuple[tuple[bytes, str, str], ...] = (
    (PNG_A, "image", "shot.png"),
    (PNG_B, "image", "diagram.png"),
    (NOTES, "file", "notes.txt"),
)


@pytest.fixture(scope="module")
def media_server() -> Iterator[MediaServer]:
    """真实 HTTP 媒体源：网关子进程按回调里的 URL 直接访问它（同一主机）。"""
    server = MediaServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture(scope="module")
def catalog() -> MessageCatalog:
    return MessageCatalog(CATALOG_PATH)


# --------------------------------------------------------------------------- 帧


def _callback(message_id: str, reply_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """与真实企微回调同形的帧（探针只替代外部端点，不替代协议）。"""
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


def _mixed_body(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"msgtype": "mixed", "mixed": {"msg_item": items}}


def _media_item(media_type: str, url: str) -> dict[str, Any]:
    return {"msgtype": media_type, media_type: {"url": url, "aeskey": AES_KEY}}


# --------------------------------------------------------------------- 驱动与观测


async def _fetch(statement: str, params: dict[str, object]) -> list[tuple[Any, ...]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(statement), params)
            return [tuple(row) for row in result]
    finally:
        await engine.dispose()


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_ARTIFACT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate() if asyncio.iscoroutinefunction(predicate) else predicate():
            return
        await asyncio.sleep(0.3)
    raise AssertionError(what)


def _stored(root: Path, message_id: str) -> dict[str, bytes]:
    """该消息在共享 store 上的产物：`{相对键后缀: 字节}`（目录不存在即空）。"""
    folder = root / "inbound" / message_id
    if not folder.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir()) if path.is_file()}


async def _wait_reply(probe: Any, reply_id: str) -> str:
    """等这条消息的回复（回复帧带的是入站回调的 req_id，多帧时取最后一个非空文本）。"""
    deadline = time.monotonic() + WAIT_REPLY_SEC
    while time.monotonic() < deadline:
        texts = [
            frame_text(frame)
            for frame in probe.replies
            if (frame.get("headers") or {}).get("req_id") == reply_id
        ]
        if texts and texts[-1]:
            return texts[-1]
        await asyncio.sleep(0.2)
    raise AssertionError(f"回调 {reply_id} 未在 {WAIT_REPLY_SEC}s 内得到回复")


async def _wait_connected(stack: GatewayStack) -> Any:
    probe = stack.ws_probe
    assert probe is not None
    await _wait_for(
        lambda: any(
            (frame.get("body") or {}).get("bot_id") == BOT_ID
            for frame in probe.frames_of("aibot_subscribe")
        ),
        what="Gateway 未完成与探针的真实 WS 认证",
    )
    return probe


async def _audit_rows(tenant_id: str, message_id: str) -> list[tuple[Any, ...]]:
    return await _fetch(
        "SELECT outcome, reason_code, attachment_count, accepted_count, total_bytes "
        "FROM control.im_inbound_audit "
        "WHERE tenant_id = :t AND external_message_id = :m AND is_deleted = false",
        {"t": tenant_id, "m": message_id},
    )


def _new_message() -> tuple[str, str]:
    token = uuid.uuid4().hex[:8]
    return f"att-{token}", f"req-att-{token}"


# ---------------------------------------------------------------------- S-04 落盘


@pytest.mark.e2e
async def test_s04_three_attachments_land_and_are_readable_separately(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """S-04：一条消息带 3 个附件（2 图 + 1 文档）→ 各自落盘、可分别读取、互不覆盖。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    items = [
        _media_item(
            media_type,
            media_server.serve(
                f"/s04/{message_id}/{position}", wecom_ciphertext(plaintext), filename=filename
            ),
        )
        for position, (plaintext, media_type, filename) in enumerate(CASE_ITEMS)
    ]
    await probe.push_raw_frame(
        bot_id=BOT_ID, frame=_callback(message_id, reply_id, _mixed_body(items))
    )

    expected = {str(index): plaintext for index, (plaintext, _t, _n) in enumerate(CASE_ITEMS)}
    root = gateway_stack.artifact_root
    await _wait_for(
        lambda: _stored(root, message_id).keys() == expected.keys(),
        what=f"三个附件未各自落盘：{_stored(root, message_id)}",
    )

    # ① 可分别读取、互不覆盖：每个键的字节与它自己那份明文逐字节相同
    stored = _stored(root, message_id)
    assert {key: value for key, value in stored.items()} == expected

    # ② 契约：Runtime 把三条引用落成了 artifact 行（AD-1-B：网关只写字节、不写库）
    rows = await _fetch(
        "SELECT storage_key, media_type, size, checksum, artifact_type, run_id "
        "FROM runtime.artifact WHERE tenant_id = :t AND storage_key LIKE :k AND is_deleted = false "
        "ORDER BY storage_key",
        {"t": gateway_stack.tenant_id, "k": f"inbound/{message_id}/%"},
    )
    assert len(rows) == 3, f"artifact 行数应为 3，实得 {rows}"
    for index, (storage_key, media_type, size, checksum, artifact_type, run_id) in enumerate(rows):
        assert storage_key == f"inbound/{message_id}/{index}"
        assert artifact_type == ("INBOUND_IMAGE" if index < 2 else "INBOUND_DOCUMENT")
        assert run_id is not None, "入站产物挂在 Run 上（ck_artifact_run_task_xor）"
        plaintext = expected[str(index)]
        assert size == len(plaintext)
        assert checksum == hashlib.sha256(plaintext).hexdigest()
        assert media_type == ("image/png" if index < 2 else "text/plain")

    # ③ 审计：带载荷的消息一行，数字与落盘事实一致
    audits = await _audit_rows(gateway_stack.tenant_id, message_id)
    assert len(audits) == 1
    outcome, reason_code, count, accepted, total = audits[0]
    assert (outcome, reason_code) == ("RECEIVED", "")
    assert (count, accepted, total) == (3, 3, sum(len(item[0]) for item in CASE_ITEMS))


# ------------------------------------------------------- E-01 / E-02 / E-03 反馈


@pytest.mark.e2e
async def test_e01_unsupported_type_is_explained_and_audited(
    gateway_stack: GatewayStack, catalog: MessageCatalog
) -> None:
    """E-01：不受支持的载荷形态（语音）→ 不落盘 + 明确回复 + 审计。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    await probe.push_raw_frame(
        bot_id=BOT_ID,
        frame=_callback(message_id, reply_id, {"msgtype": "voice", "voice": {"media_id": "v-1"}}),
    )

    reply = await _wait_reply(probe, reply_id)
    assert reply == catalog.message("UNSUPPORTED_MEDIA", "zh-CN")
    assert _stored(gateway_stack.artifact_root, message_id) == {}

    audits = await _audit_rows(gateway_stack.tenant_id, message_id)
    assert len(audits) == 1
    assert audits[0][0] == "REJECTED" and audits[0][1] == "UNSUPPORTED_MEDIA"
    assert audits[0][2] == 0 and audits[0][3] == 0


@pytest.mark.e2e
async def test_e02_oversized_file_reply_carries_the_limit_and_an_audit(
    gateway_stack: GatewayStack, media_server: MediaServer, catalog: MessageCatalog
) -> None:
    """E-02：超过单文件上限 → 回复里**带上限数值** + 审计；超限在下载途中即中止。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    # 远大于上限（上限 + 12 MiB）：这样"网关在途中止"在服务端**明确可观测** ——
    # 只比上限大一点点时，超大缓冲会把剩余字节收下，断言就退化成恒真
    oversized = b"%PDF-1.7\n" + b"x" * (MAX_ATTACHMENT_BYTES + 12 * MIB)
    media_server.chunk_delay = 0.004  # 服务端放慢发送，客户端的中止才来得及发生
    try:
        url = media_server.serve(
            f"/e02/{message_id}", wecom_ciphertext(oversized), filename="big-report.pdf"
        )
        await probe.push_raw_frame(
            bot_id=BOT_ID,
            frame=_callback(
                message_id, reply_id, {"msgtype": "file", "file": {"url": url, "aeskey": AES_KEY}}
            ),
        )

        reply = await _wait_reply(probe, reply_id)
        assert reply == catalog.message(
            "ATTACHMENT_TOO_LARGE", "zh-CN", {"limit": MAX_ATTACHMENT_BYTES}
        )
        assert str(MAX_ATTACHMENT_BYTES) in reply, "上限数值必须出现在用户可见回复里"
        assert _stored(gateway_stack.artifact_root, message_id) == {}

        audits = await _audit_rows(gateway_stack.tenant_id, message_id)
        assert len(audits) == 1
        assert audits[0][0] == "FAILED" and audits[0][1] == "ATTACHMENT_TOO_LARGE"
        assert audits[0][3] == 0  # 一个也没收下
        # 超出上限的字节不该被读完：服务端记录到的写出字节数必须**小于**它持有的密文
        path = url.split(media_server.base_url, 1)[1]
        assert path in media_server.sent, "服务端未记录到写出的字节数，断言会退化为恒真"
        assert media_server.sent[path] < len(media_server.routes[path]), (
            f"超限必须中止读取，而不是读完再判：写出 {media_server.sent[path]} 字节"
        )
    finally:
        media_server.chunk_delay = 0.0


@pytest.mark.e2e
async def test_e03_take_failure_is_explained_and_audited(
    gateway_stack: GatewayStack, media_server: MediaServer, catalog: MessageCatalog
) -> None:
    """E-03：下载失败（媒体 URL 已失效/404）→ 明确失败说明 + 审计；不落盘。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    await probe.push_raw_frame(
        bot_id=BOT_ID,
        frame=_callback(
            message_id,
            reply_id,
            {
                "msgtype": "image",
                "image": {"url": f"{media_server.base_url}/e03/gone", "aeskey": AES_KEY},
            },
        ),
    )

    reply = await _wait_reply(probe, reply_id)
    assert reply == catalog.message("ATTACHMENT_FETCH_FAILED", "zh-CN")
    assert _stored(gateway_stack.artifact_root, message_id) == {}

    audits = await _audit_rows(gateway_stack.tenant_id, message_id)
    assert len(audits) == 1
    assert audits[0][0] == "FAILED" and audits[0][1] == "ATTACHMENT_FETCH_FAILED"
    assert audits[0][2] == 1 and audits[0][3] == 0


# ---------------------------------------------------------------- E-07 重投幂等


@pytest.mark.e2e
async def test_e07_redelivered_message_produces_no_duplicate_artifact(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """E-07：企微重投同一条消息 → 去重，不产生重复产物、不写第二条审计。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    url = media_server.serve(
        f"/e07/{message_id}", wecom_ciphertext(PNG_A), filename="shot.png"
    )
    frame = _callback(
        message_id, reply_id, {"msgtype": "image", "image": {"url": url, "aeskey": AES_KEY}}
    )
    await probe.push_raw_frame(bot_id=BOT_ID, frame=frame)

    root = gateway_stack.artifact_root
    await _wait_for(
        lambda: _stored(root, message_id).keys() == {"0"},
        what="首个附件未落盘",
    )
    first = _stored(root, message_id)

    await probe.push_raw_frame(bot_id=BOT_ID, frame=frame)  # 重投
    await asyncio.sleep(3.0)  # 给重投足够的处理时间：它必须什么都不做

    assert _stored(root, message_id) == first, "重投不得新增或覆盖产物"
    rows = await _fetch(
        "SELECT count(*) FROM runtime.artifact "
        "WHERE tenant_id = :t AND storage_key LIKE :k AND is_deleted = false",
        {"t": gateway_stack.tenant_id, "k": f"inbound/{message_id}/%"},
    )
    assert rows[0][0] == 1, f"重投产生了重复的 artifact 行：{rows}"
    assert len(await _audit_rows(gateway_stack.tenant_id, message_id)) == 1
