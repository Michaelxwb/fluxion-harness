"""[TASK-010] 端到端验收基线：S-03 / S-06 / S-07 / S-10。

本需求跨回调、工具、PG、共享存储、渠道帧五个边界。前面各任务**各自都绿**，而"东西真的从一端
走到了另一端"只有一条真实链路能照出来——上一需求就栽在这里（四个前置全绿，而"图片从未进过
模型请求体"这个 P0 只有端到端才看得见）。所以本文件**不 mock 业务 API / DB / 落盘 / 渠道帧**。

真实边界：真实 WeCom 协议 WS 探针（官方 SDK 认证收发）→ 真实 Gateway 进程 → 真实 Runtime
HTTP/SSE → 真实 PostgreSQL → 真实共享 artifact store → 真实渠道帧。
模型由 `tests/e2e/openai_probe_app` 承载：它既是**真实 HTTP 的 OpenAI 兼容端点**，也是
"模型实际收到了什么"的**观测点**（`GET /requests` 回读请求体）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

import httpx
from muad_common import SharedSettings

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
)
from tests.e2e.seed_im_gateway import seed_delivery_task
from tests.e2e.wecom_media_server import AES_KEY, MediaServer, wecom_ciphertext
from tests.e2e.wecom_probe_app import frame_text

WAIT_SEC = 150.0
#: 收到目标文本后再静默这么久。用来确认"只有一条"，也用来给 Run 落终态留时间。
SETTLE_SEC = 2.0

#: 分页指引里的下一个 offset（`read_attachment` 的片段标注文案）。**从模型实际收到的
#: 工具结果里取**而不是在测试里算：算出来的偏移量只能证明我算得对，取出来才能证明
#: "工具真的把翻页指引交给了模型、模型真的照着读了下一段"。
NEXT_OFFSET_RE = re.compile(r"offset=(\d+)")

#: S-03 的长文档：前 20 000 字符**不含任何事实**（正好是 `read_attachment` 的单次默认上限），
#: 末尾才是要答的那个事实。于是"只读了开头"和"读到了末尾"在这个夹具上是**互斥**的——
#: 前者根本拿不到答案，不存在"蒙对"。
FILLER = "这一段是填充内容，不含任何结论。"
TAIL_FACT = "文档末尾的关键事实：最终交付日期是 2031-07-19。"
FIRST_SEGMENT_CHARS = 20_000


def _document() -> str:
    prefix = (FILLER * (FIRST_SEGMENT_CHARS // len(FILLER) + 1))[:FIRST_SEGMENT_CHARS]
    return prefix + TAIL_FACT + "\n（文档结束）\n"


def _new_message(tag: str) -> tuple[str, str]:
    token = uuid.uuid4().hex[:8]
    return f"{tag}-{token}", f"req-{tag}-{token}"


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


def _replies(stack: GatewayStack, reply_id: str) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    return [
        frame_text(frame)
        for frame in probe.replies  # type: ignore[attr-defined]
        if (frame.get("headers") or {}).get("req_id") == reply_id
    ]


async def _wait_for_reply(
    stack: GatewayStack, reply_id: str, needle: str, *, timeout: float = WAIT_SEC
) -> str:
    """等到该 `req_id` 下出现含 `needle` 的回复帧，返回它。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for text in _replies(stack, reply_id):
            if needle in text:
                return text
        await asyncio.sleep(0.3)
    raise AssertionError(f"未在 {timeout}s 内收到含「{needle}」的回复，实收：{_replies(stack, reply_id)!r}")


async def _wait_run_settled(stack: GatewayStack, *, timeout: float = WAIT_SEC) -> None:
    """等到本租户**没有活跃 Run**。

    必须等：WS 推送按"最新会话"解析，前一个 Run 还没落终态时再推一条会被判成 `RUN_BUSY`
    ——表现为"第二条消息收不到回复"，且随机。这是既有验收栈记录过的顺序假设。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = await _scalar(
            "SELECT count(*) FROM runtime.run_record "
            "WHERE tenant_id = :t AND status IN ('CREATED','RUNNING','WAITING_INPUT')",
            {"t": stack.tenant_id},
        )
        if int(active or 0) == 0:
            return
        await asyncio.sleep(0.3)
    raise AssertionError("本租户仍有活跃 Run，第二条消息会被判 RUN_BUSY")


async def _scalar(statement: str, params: dict[str, object]) -> object:
    """真实 PG 标量查询（独立 engine：验收栈多事件循环，复用缓存 engine 会跨 loop 出事）。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _set_script(stack: GatewayStack, **script: str) -> None:
    """设定模型的下一步行为（真实 HTTP 打探针进程）。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(f"{stack.llm_url}/script", json=script)
        assert response.status_code == 200, response.text


async def _requests(stack: GatewayStack) -> list[dict[str, Any]]:
    """模型实际收到的请求体（最近 50 条）。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{stack.llm_url}/requests")
        assert response.status_code == 200, response.text
        return list(response.json()["requests"])


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def _conversation_text(body: dict[str, Any]) -> str:
    """一份请求体里模型能看到的**全部**文本（含工具结果）。"""
    return "\n".join(_message_text(item) for item in (body.get("messages") or []))


async def _wait_connected(stack: GatewayStack) -> None:
    """等网关的 WS 客户端**真的订阅上**再推帧。

    不等的表现是 `探针没有 <bot> 的已连接客户端`——栈刚起来，订阅还在路上。
    """
    probe = stack.ws_probe
    assert probe is not None
    deadline = time.monotonic() + WAIT_SEC
    while time.monotonic() < deadline:
        if probe.frames_of("aibot_subscribe"):  # type: ignore[attr-defined]
            return
        await asyncio.sleep(0.2)
    raise AssertionError("WS 探针未在时限内完成订阅")


async def _push(stack: GatewayStack, frame: dict[str, Any]) -> None:
    probe = stack.ws_probe
    assert probe is not None
    await probe.push_raw_frame(bot_id=BOT_ID, frame=frame)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- S-03


async def test_s03_long_document_is_read_past_the_first_segment(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """S-03：真实回调 → 真实落盘 → 真实工具 → **真实模型请求体**，分两段读完再回答。

    夹具把"只读开头"做成**拿不到答案**：前 20 000 字符（正好一次默认读取的上限）里没有任何
    事实，事实只在末尾。于是最终回答里出现末尾事实，只能来自**第二段真的进了模型上下文**。

    第一轮用默认参数读（拿翻页指引），第二轮用**指引里给的 offset** 读末尾——而不是测试自己
    算一个偏移量塞进去。后者只能证明"我算得对"，证明不了"工具把指引交给了模型、模型照做了"。
    """
    await _wait_connected(gateway_stack)
    document = _document()
    assert document[FIRST_SEGMENT_CHARS:].startswith(TAIL_FACT), "夹具坏了：末尾事实不在第二段里"
    message_id, reply_id = _new_message("s03a")
    url = media_server.serve(
        f"/s03/{message_id}", wecom_ciphertext(document.encode("utf-8")), filename="季度报告.txt"
    )

    # 第一轮：默认参数读（不带 offset/limit）
    await _set_script(
        gateway_stack,
        tool_name="read_attachment",
        tool_arguments='{"artifact_id": "$last_artifact_id"}',
        final_text="第一段已读。",
    )
    await _push(
        gateway_stack,
        _callback(message_id, reply_id, {"msgtype": "file", "file": {"url": url, "aeskey": AES_KEY}}),
    )
    await _wait_for_reply(gateway_stack, reply_id, "第一段已读")
    await _wait_run_settled(gateway_stack)

    first_bodies = await _requests(gateway_stack)
    first_context = "\n".join(_conversation_text(body) for body in first_bodies)
    # 第一段（20 000 字符）必须**整段**进模型上下文：这正是 `externalizable_result=False`
    # 要保的东西——按 8KB 外置的话模型只拿到 200 字符预览，而**分页指引在结果末尾，会被一起截掉**，
    # 于是"长文档可读完"在真实链路上等于不存在，且不报错（2026-10-03 实测发现，已修）。
    assert len(first_context) > FIRST_SEGMENT_CHARS, (
        f"第一段没有整段交给模型（上下文只有 {len(first_context)} 字符）——"
        "工具结果被大结果外置截成了预览"
    )
    assert TAIL_FACT not in first_context, "第一轮不该看见末尾事实——否则这条用例证明不了「不是只读了开头」"

    match = NEXT_OFFSET_RE.search(first_context)
    assert match is not None, "工具结果里必须带翻页指引（下一段 offset），否则模型无从续读"
    next_offset = int(match.group(1))
    assert next_offset == FIRST_SEGMENT_CHARS, f"翻页指引给的 offset 不对：{next_offset}"

    # 第二轮：**照着指引**读末尾
    message_id, reply_id = _new_message("s03b")
    await _set_script(
        gateway_stack,
        tool_name="read_attachment",
        tool_arguments=f'{{"artifact_id": "$last_artifact_id", "offset": {next_offset}}}',
        final_text=TAIL_FACT,
    )
    await _push(
        gateway_stack,
        _callback(message_id, reply_id, {"msgtype": "file", "file": {"url": url, "aeskey": AES_KEY}}),
    )
    reply = await _wait_for_reply(gateway_stack, reply_id, TAIL_FACT)

    assert TAIL_FACT in reply, "最终回答必须体现文档**末尾**的事实"

    # 断言落在**模型请求体**上：第二段正文真的进了模型上下文（不是被工具"读过就算"）
    bodies = await _requests(gateway_stack)
    later = [body for body in bodies if TAIL_FACT in _conversation_text(body)]
    assert later, "没有任何一次模型请求里出现过第二段正文——工具读了但没交给模型"


# --------------------------------------------------------------------- S-06 / S-10

ARTIFACT_BODY = "# 季度结论\n\n本季度结论：增长 12%，主要来自续约。\n"
ARTIFACT_NAME = "季度结论.md"


def _args(**kwargs: Any) -> str:
    """工具参数（探针要的是 JSON 字符串）。"""
    return json.dumps(kwargs, ensure_ascii=False)


def _media_frames(stack: GatewayStack) -> list[dict[str, Any]]:
    """用户**真的收到**的媒体帧（`image`/`file` 体带 `media_id`）。"""
    probe = stack.ws_probe
    assert probe is not None
    return [
        frame
        for frame in probe.replies  # type: ignore[attr-defined]
        if (frame.get("body") or {}).get("msgtype") in ("image", "file")
    ]


async def _audit_ids(stack: GatewayStack) -> set[Any]:
    return {row[0] for row in await _audit_rows(stack)}


async def _new_audit_rows(stack: GatewayStack, before: set[Any]) -> list[tuple[Any, ...]]:
    """**本用例新增**的审计行。

    必须按差集取：同一模块里的兄弟用例也会往同一个租户写审计行，"全租户有几行"这种断言
    会随着用例数量变化——它测的是"我前面跑过几个用例"，不是"这次交付记了几行"。
    """
    return [row for row in await _audit_rows(stack) if row[0] not in before]


async def _audit_rows(stack: GatewayStack) -> list[tuple[Any, ...]]:
    """交付审计逐行回读（真实 PG；网关不持库，记录只能经 console 内部端点落在这里）。"""
    return await _fetch_all(
        "SELECT artifact_id, outcome, route_key FROM control.artifact_delivery_audit "
        "WHERE tenant_id = :t ORDER BY create_time",
        {"t": stack.tenant_id},
    )


async def _fetch_all(statement: str, params: dict[str, object]) -> list[tuple[Any, ...]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return list((await connection.execute(text(statement), params)).all())
    finally:
        await engine.dispose()


async def test_s06_agent_writes_an_artifact_and_delivers_it_in_session(
    gateway_stack: GatewayStack,
) -> None:
    """S-06：**真实会话 → 真实产物 → 真实 HTTP 交付调用 → 真实渠道帧**。

    这条是需求的硬需求落点："写完文件之后，Agent 必须能把它发送给用户"。所以断言**落在用户
    实际收到的帧上**（媒体体 + `media_id`），而不是"工具返回了一段成功文案"。

    一次 Run 里走**两步工具**（写 → 交付）：`write_artifact` 与 `deliver_artifact` 必须在同一次
    Run 内，因为交付工具只允许交付**本次 Run 自己写出的**产物（跨 Run 会被判定为不可交付）。
    """
    await _wait_connected(gateway_stack)
    probe = gateway_stack.ws_probe
    assert probe is not None
    uploads_before = len(probe.uploads)  # type: ignore[attr-defined]
    media_before = len(_media_frames(gateway_stack))
    audit_before = await _audit_ids(gateway_stack)

    message_id, reply_id = _new_message("s06")
    await _set_script(
        gateway_stack,
        tools=[
            {
                "name": "write_artifact",
                "arguments": _args(content=ARTIFACT_BODY, filename=ARTIFACT_NAME),
            },
            {"name": "deliver_artifact", "arguments": _args(artifact_id="$last_artifact_id")},
        ],
        final_text="文件已写好并发给你了。",
    )
    await _push(
        gateway_stack,
        _callback(
            message_id,
            reply_id,
            {"msgtype": "text", "text": {"content": "把季度结论写成文件发我"}},
        ),
    )
    await _wait_for_reply(gateway_stack, reply_id, "文件已写好并发给你了。")
    await _wait_run_settled(gateway_stack)

    # ① 用户**真的收到了文件**：媒体体 + media_id（不是一句"已发送"）
    media = _media_frames(gateway_stack)[media_before:]
    assert len(media) == 1, f"用户应恰好收到一条媒体消息，实收 {len(media)}"
    body = media[0]["body"]
    assert body["msgtype"] == "file", "kind=DOCUMENT 要按文件形态发"
    assert body["file"]["media_id"], "必须带 media_id（企微出站媒体的唯一通路）"

    # ② 这个 media_id 是**真上传换来的**：三步分片协议真的走完了
    assert len(probe.uploads) == uploads_before + 1, "应当恰好发生一次上传"  # type: ignore[attr-defined]
    assert probe.upload_chunks, "分片帧不能为空"  # type: ignore[attr-defined]
    init_body = probe.uploads[-1]["body"]  # type: ignore[attr-defined]
    assert init_body["filename"] == ARTIFACT_NAME
    assert init_body["type"] == "file"

    # ③ 交付有审计记录（网关不持库，记录经 console 内部端点落进 control）
    audit = await _new_audit_rows(gateway_stack, audit_before)
    assert [row[1] for row in audit] == ["DELIVERED"], f"审计应恰好一行 DELIVERED：{audit!r}"
    assert audit[0][2], "route_key 由适配器产出，不得为空"

    # ④ **工具结果必须说"已交付"** —— 这条是 2026-10-03 补的，因为原用例漏了它。
    # 真机事故正是：用户**收到了帧**、审计也记了 `DELIVERED`，而工具结果却报"交付失败，可重试"
    # （网关响应里有个 `duplicate` 字段没进契约，runtime 严格解析直接抛，把成功当失败）。
    # 只断言"帧到了"抓不到这类错——必须同时断言**模型被告知的那句话**也是对的，
    # 否则模型会对用户说"发不出去"，而用户手里已经有文件了。
    tool_results = [
        _message_text(message)
        for body in await _requests(gateway_stack)
        for message in (body.get("messages") or [])
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    assert any("已交付产物" in text for text in tool_results), (
        f"交付工具必须向模型报「已交付」，实际工具结果：{tool_results}"
    )
    assert not any("交付失败" in text for text in tool_results), (
        f"文件已经送达，工具结果不得报失败（谎报失败同样违反 RULE-03）：{tool_results}"
    )

    # ④ 审计指的那个产物真的落库了，且字节与模型写的一致
    rows = [
        row
        for row in await _fetch_all(
            "SELECT id, storage_key, size FROM runtime.artifact "
            "WHERE tenant_id = :t AND artifact_type = 'AGENT_OUTPUT'",
            {"t": gateway_stack.tenant_id},
        )
        if row[0] == audit[0][0]
    ]
    assert len(rows) == 1, f"审计指向的产物必须存在且唯一，实际 {len(rows)}"
    assert rows[0][2] == len(ARTIFACT_BODY.encode("utf-8"))
    assert (gateway_stack.artifact_root / str(rows[0][1])).read_bytes() == ARTIFACT_BODY.encode(
        "utf-8"
    )


async def test_s10_delivering_the_same_artifact_twice_lands_one_frame_and_one_audit_row(
    gateway_stack: GatewayStack,
) -> None:
    """S-10：同一产物**对同一路由**交付两次 ⇒ 用户只收到一次，审计**仍只有一行**。

    渠道侧不做幂等（同 `media_id` 发两次 = 用户看到两条），所以去重只能落在网关既有链路上：
    幂等键 `(tenant_id, artifact_id, route_key)`（审计）+ 传输层占位（Redis）。
    这条断言的是**用户视角**的"恰好一次"——审计只有一行但用户收到两条，同样是失败。
    """
    await _wait_connected(gateway_stack)
    probe = gateway_stack.ws_probe
    assert probe is not None
    media_before = len(_media_frames(gateway_stack))
    audit_before = await _audit_ids(gateway_stack)

    message_id, reply_id = _new_message("s10")
    await _set_script(
        gateway_stack,
        tools=[
            {
                "name": "write_artifact",
                "arguments": _args(content=ARTIFACT_BODY, filename=ARTIFACT_NAME),
            },
            {"name": "deliver_artifact", "arguments": _args(artifact_id="$last_artifact_id")},
            {"name": "deliver_artifact", "arguments": _args(artifact_id="$last_artifact_id")},
        ],
        final_text="已经发过了。",
    )
    await _push(
        gateway_stack,
        _callback(
            message_id,
            reply_id,
            {"msgtype": "text", "text": {"content": "发我两次试试"}},
        ),
    )
    await _wait_for_reply(gateway_stack, reply_id, "已经发过了。")
    await _wait_run_settled(gateway_stack)

    media = _media_frames(gateway_stack)
    assert len(media) == media_before + 1, (
        f"同一产物对同一路由只该发一次，实收 {len(media) - media_before} 条"
    )

    audit = await _new_audit_rows(gateway_stack, audit_before)
    assert len(audit) == 1, f"审计只该有一行（同键更新而非新增），实际 {len(audit)} 行：{audit!r}"
    assert audit[0][1] == "DELIVERED"


# --------------------------------------------------------------------------- S-07


async def test_s07_background_task_delivers_its_artifact_through_the_worker(
    gateway_stack: GatewayStack,
) -> None:
    """S-07：**真实 Worker 进程 → 真实网关 `/internal/deliveries` → 真实渠道帧**。

    这条要证明的是后台路径也**真的把文件交到了用户手里**——修复前的状态正是设计 §1 写的
    那条：交付正文里只有一句「完整结果见附件：`<uuid>`」，用户看到一串 UUID，
    既没有文件也没有可点的入口。所以断言落在**媒体帧**上，而"正文里有没有 UUID"无关紧要。

    Worker 只持有**不透明的** `result_artifact_id`，要经 runtime 的解析单点换成渠道中立的
    引用才发得出去——这条调用边只在真实栈上通不通，是端到端才照得出来的。
    """
    await _wait_connected(gateway_stack)
    probe = gateway_stack.ws_probe
    assert probe is not None

    # 真产物：字节落共享 store，行落 runtime.artifact
    artifact_id = uuid.uuid4()
    storage_key = f"background/{uuid.uuid4().hex}.md"
    payload = "# 后台任务结果\n\n跑完了，结论在这里。\n".encode()
    target = gateway_stack.artifact_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    await _execute(
        "INSERT INTO runtime.artifact "
        "(id, tenant_id, task_id, artifact_type, storage_key, media_type, size, checksum, "
        " metadata_json) "
        "VALUES (:id, :t, :task, 'AGENT_OUTPUT', :key, 'text/markdown', :size, :sum, "
        " '{\"kind\": \"DOCUMENT\", \"filename\": \"后台结果.md\"}'::jsonb)",
        {
            "id": artifact_id,
            "t": gateway_stack.tenant_id,
            # 表上有 XOR 约束：`task_id` / `run_id` 恰好填一个
            "task": uuid.uuid4(),
            "key": storage_key,
            "size": len(payload),
            "sum": "sha256:" + "0" * 64,
        },
    )
    seed_delivery_task(
        tenant_id=gateway_stack.tenant_id,
        agent_id=gateway_stack.agent_id,
        platform_user_id=gateway_stack.platform_user_id,
        intent_key=f"s07-{uuid.uuid4().hex[:8]}",
        bot_id=BOT_ID,
        external_user_id=BOUND_EXTERNAL_USER_ID,
        external_conversation_id=CHAT_ID,
        result_artifact_id=artifact_id,
    )

    media_before = len(_media_frames(gateway_stack))
    audit_before = await _audit_ids(gateway_stack)

    # 等 Worker 的投递循环把它取走并投出去（轮询间隔在栈里已压到 1s）
    await _wait_for(
        lambda: len(_media_frames(gateway_stack)) > media_before,
        what="Worker 未在时限内把产物投出去",
    )
    # 再静默一会儿：确认"恰好一次"——只等到第一条就下结论等于没验去重
    await asyncio.sleep(SETTLE_SEC)

    media = _media_frames(gateway_stack)[media_before:]
    assert len(media) == 1, f"后台投递只该发一次，实收 {len(media)} 条"
    body = media[0]["body"]
    assert body["msgtype"] == "file"
    assert body["file"]["media_id"], "必须带 media_id（企微出站媒体的唯一通路）"

    audit = await _new_audit_rows(gateway_stack, audit_before)
    assert len(audit) == 1, f"审计只该一行，实际 {len(audit)}：{audit!r}"
    assert audit[0][0] == artifact_id, "审计记的必须是这条后台产物"
    assert audit[0][1] == "DELIVERED"


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.3)
    raise AssertionError(f"{what}（等待 {timeout}s）")


async def _execute(statement: str, params: dict[str, object]) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


# --------------------------------------------------- 入站图片回发（2026-10-03 补）

#: 一张最小的合法 PNG（1×1 透明）。用真 PNG 头而不是随便几个字节——入库类型是按文件名推的，
#: 但这里要让"它确实是张图"在夹具层面也站得住。
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001-0d0a2db40000000049454e44ae426082".replace("-", "")
)


async def test_inbound_image_can_be_sent_back_to_the_user(
    gateway_stack: GatewayStack, media_server: MediaServer
) -> None:
    """**用户发来的图，agent 能原样回发** —— 2026-10-03 补的端到端。

    在此之前这条路是**结构性堵死**的，而用户真机上试的就是这一句（"把这张图片重新发给我"）：

    - `write_artifact` 只吃文本 ⇒ agent **造不出位图**（写 `.svg` 也只会被标成 `text/markdown`）；
    - `deliver_artifact` 的作者校验只认"本次 Run 自产" ⇒ **转发入站附件被拒**。

    两条合起来 = agent 一张图都发不出去。转发不产生新内容，所以只需要放开作者校验
    （且**只能**放开到"本会话的入站附件"）。这条用例断言的是**用户实际收到的帧**：
    必须是 `msgtype=image` + `media_id`，而不是一个被当成普通文件发出去的 `.png`。
    """
    await _wait_connected(gateway_stack)
    media_before = len(_media_frames(gateway_stack))

    # ① 用户发来一张图（真实回调 → 真实落盘 → Runtime 落 artifact 行）
    message_id, reply_id = _new_message("img")
    url = media_server.serve(
        f"/img/{message_id}", wecom_ciphertext(PNG_BYTES), filename="截图.png"
    )
    await _set_script(
        gateway_stack, tool_name="view_image", tool_arguments=_args(artifact_id="$last_artifact_id"),
        final_text="看到了。",
    )
    await _push(
        gateway_stack,
        _callback(
            message_id, reply_id, {"msgtype": "image", "image": {"url": url, "aeskey": AES_KEY}}
        ),
    )
    await _wait_for_reply(gateway_stack, reply_id, "看到了。")
    await _wait_run_settled(gateway_stack)

    # ② 第二轮：**把这张图回发给用户**
    # 探针的请求列表是**模块级累积**的（前序用例的结果也在里面），所以从这里切一刀，
    # 后面只看**本用例**产生的请求——否则断言会扫到别人的工具结果。
    requests_before = len(await _requests(gateway_stack))
    message_id, reply_id = _new_message("imgback")
    await _set_script(
        gateway_stack,
        tool_name="deliver_artifact",
        tool_arguments=_args(artifact_id="$last_artifact_id"),
        final_text="图已经发回给你了。",
    )
    await _push(
        gateway_stack,
        _callback(
            message_id, reply_id, {"msgtype": "text", "text": {"content": "把这张图再发我一次"}}
        ),
    )
    await _wait_for_reply(gateway_stack, reply_id, "图已经发回给你了。")
    await _wait_run_settled(gateway_stack)

    # ③ 用户**真的收到了图片**：`msgtype=image` + `media_id`
    frames = _media_frames(gateway_stack)[media_before:]
    assert len(frames) == 1, f"应当恰好收到一条媒体消息，实收 {len(frames)}"
    body = frames[0]["body"]
    assert body["msgtype"] == "image", (
        f"入站图片回发必须走 image 体（这个通道只用 media_id 通路），实际 {body['msgtype']}"
    )
    assert body["image"]["media_id"]

    # ④ 工具结果必须报「已交付」，不得谎报失败
    tool_results = [
        _message_text(message)
        for body in (await _requests(gateway_stack))[requests_before:]
        for message in (body.get("messages") or [])
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    assert any("已交付产物" in text for text in tool_results), tool_results
    assert not any("交付失败" in text or "tool failed" in text for text in tool_results), tool_results
