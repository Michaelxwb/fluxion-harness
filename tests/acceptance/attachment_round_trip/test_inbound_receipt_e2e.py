"""[TASK-004] S-04 / E-02：入站附件回执，与拒绝反馈**合并为至多一条**（RULE-04）。

不得 Mock 的真实边界：真实企微 WS 探针（官方 SDK 认证与收发）→ 真实网关进程 → 真实渠道帧
→ 真实 HTTP 媒体源（真实 AES-256-CBC 密文）→ 真实共享 artifact store。
断言落在**用户实际收到的帧文本**上（`frame_text` 统一取三种合法体的文本）。

**与设计稿的一处偏离（如实登记）**：设计 S-04 的前置条件写「3 个附件，其中 1 个**超限**」，
本用例改用**类型不在白名单**（`application/zip`）来制造"部分接收"。理由：超限要造 50 MiB
夹具并真的下载到中止，而"超限"这条路径已由兄弟用例
`tests/acceptance/im_gateway/test_wecom_attachments.py::test_e02_oversized_file_reply_carries_the_limit_and_an_audit`
覆盖并断言了上限数值；本用例要验的是**回执的合并语义**，与"因为什么被拒"无关。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from muad_api.catalog import MessageCatalog

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
)
from tests.e2e.wecom_media_server import AES_KEY, MediaServer, wecom_ciphertext
from tests.e2e.wecom_probe_app import frame_text

pytestmark = pytest.mark.e2e

CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "api-messages.yaml"
CATALOG = MessageCatalog(CATALOG_PATH)
LOCALE = "zh-CN"

WAIT_SEC = 150.0
#: 收到回执后再静默这么久，用来确认**没有第二条**附件相关反馈跟上来
SETTLE_SEC = 3.0

PNG = b"\x89PNG\r\n\x1a\n" + b"s04-receipt-panel" * 4
TXT = b"notes for the receipt case"
#: 不在白名单 → 实检拒绝（原因码 ATTACHMENT_TYPE_NOT_ALLOWED）
ZIP = b"PK\x03\x04" + b"not-whitelisted" * 4

RECEIPT_ALL = "ATTACHMENT_RECEIPT_ALL"
RECEIPT_PARTIAL = "ATTACHMENT_RECEIPT_PARTIAL"
TYPE_NOT_ALLOWED = "ATTACHMENT_TYPE_NOT_ALLOWED"


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


def _new_message() -> tuple[str, str]:
    token = uuid.uuid4().hex[:8]
    return f"s04-{token}", f"req-s04-{token}"


async def _wait_connected(stack: GatewayStack) -> Any:
    probe = stack.ws_probe
    deadline = time.monotonic() + WAIT_SEC
    while time.monotonic() < deadline:
        if probe.frames_of("aibot_subscribe"):
            return probe
        await asyncio.sleep(0.2)
    raise AssertionError("WS 探针未在时限内完成订阅")


def _texts_for(probe: Any, reply_id: str) -> list[str]:
    return [
        frame_text(frame)
        for frame in probe.replies
        if (frame.get("headers") or {}).get("req_id") == reply_id
    ]


async def _collect_after(probe: Any, reply_id: str, needle: str) -> list[str]:
    """等到出现含 `needle` 的帧，再留一个静默期，返回该 req_id 下**全部**文本帧。

    静默期是必需的：RULE-04 断言的是"**没有第二条**"，只等到第一条就下结论等于没验。
    """
    deadline = time.monotonic() + WAIT_SEC
    while time.monotonic() < deadline:
        if any(needle in text for text in _texts_for(probe, reply_id)):
            break
        await asyncio.sleep(0.2)
    else:
        raise AssertionError(f"回调 {reply_id} 未在 {WAIT_SEC}s 内收到含「{needle}」的帧")
    await asyncio.sleep(SETTLE_SEC)
    return _texts_for(probe, reply_id)


def _stored(root: Path, message_id: str) -> dict[str, bytes]:
    folder = root / "inbound" / message_id
    if not folder.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir()) if path.is_file()}


# --------------------------------------------------------------------------- 场景


async def test_s04_partial_acceptance_sends_exactly_one_merged_receipt(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """S-04：3 个附件（2 个收下、1 个类型被拒）→ 用户收到**一条**合并回执，且仅此一条。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    routes = [
        ("image/png", PNG, "shot.png"),
        ("text/plain", TXT, "notes.txt"),
        ("application/zip", ZIP, "bundle.zip"),
    ]
    items = [
        _item(mime, media_server.serve(f"/s04/{message_id}/{index}", wecom_ciphertext(data),
                                       filename=name))
        for index, (mime, data, name) in enumerate(routes)
    ]

    await probe.push_raw_frame(
        bot_id=BOT_ID, frame=_callback(message_id, reply_id, _mixed(*items))
    )

    texts = await _collect_after(probe, reply_id, "已收到")

    expected = CATALOG.message(
        RECEIPT_PARTIAL,
        LOCALE,
        {
            "accepted": 2,
            "rejected": 1,
            "reason": CATALOG.message(TYPE_NOT_ALLOWED, LOCALE),
        },
    )
    assert texts.count(expected) == 1, f"合并回执恰好一条，实收：{texts!r}"

    # RULE-04：拒绝说明**不得单独成条**——它只能作为回执里的原因出现
    standalone = CATALOG.message(TYPE_NOT_ALLOWED, LOCALE)
    assert standalone not in texts, f"拒绝说明被单独发了一条，违反「至多一条」：{texts!r}"

    # 收下的两个真的落了盘（回执说"已收到 2 个"必须有实体对应）
    stored = _stored(gateway_stack.artifact_root, message_id)
    assert len(stored) == 2, f"应有 2 个附件落盘，实际 {sorted(stored)}"
    assert sorted(stored.values()) == sorted([PNG, TXT])


async def test_e02_all_rejected_sends_only_the_rejection_not_a_receipt(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """E-02：全部被拒 → **只发一条拒绝说明**，不叠加回执（叠加就成了第二条）。"""
    probe = await _wait_connected(gateway_stack)
    message_id, reply_id = _new_message()
    items = [
        _item(
            "application/zip",
            media_server.serve(f"/e02/{message_id}/{index}", wecom_ciphertext(ZIP),
                               filename=f"bundle-{index}.zip"),
        )
        for index in range(2)
    ]

    await probe.push_raw_frame(
        bot_id=BOT_ID, frame=_callback(message_id, reply_id, _mixed(*items))
    )

    texts = await _collect_after(probe, reply_id, "未接收")

    rejection = CATALOG.message(TYPE_NOT_ALLOWED, LOCALE)
    assert texts.count(rejection) == 1, f"拒绝说明恰好一条，实收：{texts!r}"
    for text in texts:
        assert not text.startswith("已收到"), f"全拒时不得叠加回执，实收：{texts!r}"

    assert _stored(gateway_stack.artifact_root, message_id) == {}, "全拒时不应留下任何字节"
