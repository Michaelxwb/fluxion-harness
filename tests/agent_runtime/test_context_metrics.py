"""[E-05] 压缩指标：真实 `/metrics` 目录 + 触发后计数递增（FEAT-06）。

不得 Mock 的真实边界：真实 uvicorn 单进程（127.0.0.1 真实 socket）+ 真实 Runtime app/路由 →
api-kit 进程内注册表 → `GET /metrics` Prometheus 文本。计数由**真实链路**产生：
· 请求缝 snip —— 真实两连 Run（`RunService` → `AgentRunner` → 压缩层）；
· 摘要层 —— 真实 `RuntimeContextCompactor` + 真实 `make_summary_runner` + 真实 PG + 真实产物根。

label 卫生沿用 `test_runtime_metrics.py` 的口径：label 名不得命中敏感标记，label 值不得落入资源 ID。
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from muad_agent_core.agent import AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
)
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.api.deps import (
    get_credentials_client,
    get_executor_factory,
    get_resolve_client,
)
from muad_agent_runtime.application.context_compaction import (
    RuntimeContextCompactor,
    make_summary_runner,
)
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation
from muad_agent_runtime.main import app
from muad_contracts.platform_settings import (
    CompactionSettings,
    SnipSettings,
    SummarySettings,
)

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse

METRICS_PATH = "/metrics"
LISTEN_TIMEOUT_SEC = 15.0
#: 回复要有**体量**：省略标记本身也占几十字节，被省的组太短时 `bytes_saved` 会是 0。
FIRST_TEXT = "第一轮：收到，" + "细节" * 200
SECOND_TEXT = "第二轮：收到，" + "细节" * 200
SUMMARY_TOKENS = 128

#: 本任务落地的四级计数器（design §3.5 可观测性）。**无流量也必须可见**。
CONTEXT_METRICS: tuple[tuple[str, str], ...] = (
    ("context_compaction_total", "counter"),
    ("context_compaction_bytes_saved_total", "counter"),
    ("context_summary_total", "counter"),
    ("context_summary_tokens_total", "counter"),
)

SENSITIVE_LABEL_MARKERS = ("authorization", "api_key", "token", "secret", "password", "credential")
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)


class _TwoTurnProvider:
    """两个来回都直接收尾（本用例只关心压缩指标，不关心工具）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        # 第一、二、三轮都给体量足够的回复（第三轮之后的调用不会发生）
        return ModelResponse(
            content=FIRST_TEXT if self.calls % 2 == 1 else SECOND_TEXT, finish_reason="stop"
        )


class _SummaryProvider:
    """摘要模型的替身：返回**恰好五字段**的 JSON，并带上 token 用量。

    替身只替"外部模型响应"这一段；`make_summary_runner`、校验、落库、计数都是真实实现。
    """

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            content=json.dumps(
                {
                    "user_goal": "核对清单",
                    "constraints": [],
                    "progress": [],
                    "open_items": [],
                    "artifacts": [],
                },
                ensure_ascii=False,
            ),
            finish_reason="stop",
            input_tokens=100,
            output_tokens=SUMMARY_TOKENS - 100,
        )


def _parse_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = SAMPLE_PATTERN.match(line.strip())
        if matched is None:
            continue
        labels: dict[str, str] = {}
        raw = matched.group("labels")
        if raw:
            for pair in raw[1:-1].split(","):
                name, _, value = pair.partition("=")
                labels[name.strip()] = value.strip().strip('"')
        samples.append((matched.group("name"), labels, float(matched.group("value"))))
    return samples


def _value(
    samples: Sequence[tuple[str, dict[str, str], float]], name: str, labels: dict[str, str]
) -> float:
    for sample_name, sample_labels, value in samples:
        if sample_name == name and all(
            sample_labels.get(key) == item for key, item in labels.items()
        ):
            return value
    return 0.0


def _assert_label_hygiene(samples: Sequence[tuple[str, dict[str, str], float]]) -> None:
    for name, labels, _amount in samples:
        for key, value in labels.items():
            for marker in SENSITIVE_LABEL_MARKERS:
                assert marker not in key.lower(), f"指标 label 名命中敏感标记 {marker}：{name}{labels}"
            assert not UUID_PATTERN.search(value), f"指标 label 值落入资源 ID：{name}{labels}"


@asynccontextmanager
async def _serve_http(target: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口（真实 socket）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "Runtime 未在超时内监听"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=LISTEN_TIMEOUT_SEC)


@asynccontextmanager
async def _runtime_http(
    fake_resolve: FakeResolveClient, executor_factory: ExecutorFactory
) -> AsyncIterator[httpx.AsyncClient]:
    """真实 HTTP 服务真实 Runtime app；依赖覆盖与 `conftest.client` 同口径。

    **三条覆盖缺一不可**：少任何一个都会落到真实依赖上（解析走到 console-platform、
    凭据走到 secret）—— 表现是 `POST /v1/runs` 直接 500，且服务端不打堆栈。
    """
    app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
    app.dependency_overrides[get_executor_factory] = lambda: executor_factory
    app.dependency_overrides[get_credentials_client] = lambda: None
    try:
        async with _serve_http(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_resolve_client, None)
        app.dependency_overrides.pop(get_executor_factory, None)
        app.dependency_overrides.pop(get_credentials_client, None)


async def _metrics(client: httpx.AsyncClient) -> str:
    response = await client.get(METRICS_PATH)
    assert response.status_code == 200
    return response.text


def _snip_settings() -> CompactionSettings:
    """把 snip 压到"三个来回就触发"：阈值 3 组、保头保尾各 1 组。

    取值必须满足 schema 的 `max_groups ≥ keep_head + keep_tail + 1`（`_validate` 会拒）。
    """
    return CompactionSettings(
        snip=SnipSettings(max_groups=3, keep_head_groups=1, keep_tail_groups=1)
    )


def _factory(provider: ModelProvider, tmp_path: Path) -> ExecutorFactory:
    """生产同款装配：请求缝装上**真实** `RuntimeContextCompactor`（与 `default_executor_factory` 同口径）。"""

    async def factory(request: ExecutorRequest) -> RunExecutor:
        context = request.run_context
        assert context is not None
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(),
            hooks=HookPipeline(),
            context_compactor=RuntimeContextCompactor(
                settings=_snip_settings(),
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                conversation_id=context.conversation_id,
                artifact_root=tmp_path,
            ),
        )
        return AgentRunnerExecutor(runner=runner, request=request)

    return factory


async def _send(client: httpx.AsyncClient, tenant: TenantContext, text: str) -> None:
    response = await client.post(
        "/v1/runs",
        json={
            "agent_id": str(tenant.agent_id),
            "platform_user_id": str(tenant.platform_user_id),
            "channel": {"type": "WECOM", "bot_id": "bot-metrics"},
            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
        },
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.completed", events[-1]


async def test_e05_catalog_is_visible_without_any_traffic() -> None:
    """没有任何流量时，四个计数器也必须出现在 `/metrics` 目录里（能写告警、能看基线）。"""
    async with _serve_http(app) as client:
        text = await _metrics(client)

    missing = [name for name, kind in CONTEXT_METRICS if f"# TYPE {name} {kind}" not in text]
    assert not missing, f"压缩指标目录缺失：{missing}\n{text}"


async def test_e05_compaction_counters_increase_on_a_real_run(
    tenant: TenantContext, fake_resolve: FakeResolveClient, tmp_path: Path
) -> None:
    """真实三连 Run 触发请求缝 snip ⇒ 计数与省下字节递增（Prometheus 文本可抓取）。"""
    async with _runtime_http(fake_resolve, _factory(_TwoTurnProvider(), tmp_path)) as client:
        before = _parse_samples(await _metrics(client))
        await _send(client, tenant, "第一轮：先打个招呼")
        await _send(client, tenant, "第二轮：接着说")
        await _send(client, tenant, "第三轮：说完了")
        after = _parse_samples(await _metrics(client))

    fired = {"layer": "snip", "status": "FIRED"}
    assert _value(after, "context_compaction_total", fired) == (
        _value(before, "context_compaction_total", fired) + 1
    ), "snip 触发一次就该记一次"
    assert _value(after, "context_compaction_bytes_saved_total", {"layer": "snip"}) > 0, (
        "省下的字节必须为正（否则这条指标没有意义）"
    )
    _assert_label_hygiene(after)


async def test_e05_summary_counters_increase_on_a_real_compactor(
    tenant: TenantContext, tmp_path: Path
) -> None:
    """摘要层真实跑一次 ⇒ `context_summary_total{status=OK}` 与 tokens 递增。"""
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                last_seq=0,
            )
        )
        await session.commit()

    provider = _SummaryProvider()
    compactor = RuntimeContextCompactor(
        settings=CompactionSettings(
            summary=SummarySettings(enabled=True, threshold_bytes=1, model_ref="probe-model"),
            # 快照里就是这两个字段（`model_ref` 引用的既有 model_definition，不新增回退）
            snip=SnipSettings(enabled=False),
        ),
        tenant_id=tenant.tenant_id,
        run_id=uuid.uuid4(),
        conversation_id=conversation_id,
        artifact_root=tmp_path,
        summary_runner=make_summary_runner(provider=provider, model_id="probe-model"),
    )
    history = (
        ModelMessage(role=ModelRole.USER, content="很长的诉求" * 200),
        ModelMessage(role=ModelRole.ASSISTANT, content="很长的回答" * 200),
    )

    async with _serve_http(app) as client:
        before = _parse_samples(await _metrics(client))
        compacted = await compactor.compact(history)
        after = _parse_samples(await _metrics(client))

    assert len(compacted) == 1, "摘要层真的生效了（历史被换成一条摘要前缀）"
    assert provider.requests, "摘要模型确实被调了一次"
    ok = {"status": "OK"}
    assert (
        _value(after, "context_summary_total", ok) == _value(before, "context_summary_total", ok) + 1
    )
    assert _value(after, "context_summary_tokens_total", {}) == (
        _value(before, "context_summary_tokens_total", {}) + SUMMARY_TOKENS
    ), "token 用量要按模型回报的 input+output 计"
    _assert_label_hygiene(after)
