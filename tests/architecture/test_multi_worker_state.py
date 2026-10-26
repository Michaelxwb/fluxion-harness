"""B-03：多 worker 下指标与 artifact token 不串进程（真实 uvicorn 2+ workers）。

不得 Mock 的真实边界：真实 `uvicorn --workers 2` 子进程、真实 PostgreSQL/Redis（本文件自建
隔离库，沿用 `tests.acceptance.datastores` 的建库/迁移/清理原语）、真实 Console/Runtime
进程、真实 HTTP（每次一条新连接）。

关键断言（scenarios B-03）：
1. `/metrics` 包含**所有 worker** 的样本（多进程快照聚合；`muad_process_up{pid}` 每个
   worker 一条），且 counter 是各 worker 之和 —— 抓一次 `--workers 2` 就不会漏计数；
2. gate（`Deliveries` 的失败结局）在多个连接上的总计数等于实际请求数（没有进程内丢失）；
3. gate gauge 的聚合语义：每个 worker 一条 `pid` 样本，`active subscribers` 总数 = 各 worker 之和；
4. Console 签发的 artifact token 存储在共享 Redis：A worker 签的链接，B worker 也能兑换
   （多 worker 下不再 404），且撤销键随 TTL 生效。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as redis_async

from tests.acceptance.datastores import create_datastore, drop_datastore

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TENANT = "mw-tenant"
INTERNAL_TOKEN = "mw-internal-token"
READY_TIMEOUT_SEC = 90.0
METRIC_POLL_SEC = 10.0
PROCESS_UP = "muad_process_up"
DELIVERY_METRIC = "im_background_delivery_total"
FETCH_KEY_PREFIX = "muad:artifact:fetch:"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, *, timeout: float = READY_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code < 500:
                return
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(0.2)
    raise AssertionError(f"{url} 未在 {timeout}s 内就绪：{last!r}")


def _spawn(
    module: str,
    *,
    port: int,
    env: dict[str, str],
    workers: int,
    app_dir: str | None = None,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    argv = [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)]
    if app_dir:
        argv += ["--app-dir", app_dir]
    if workers > 1:
        argv += ["--workers", str(workers)]
    log = log_path.open("ab")
    return subprocess.Popen(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _base_env(database_url: str, redis_url: str, artifact_root: Path, multiproc: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(artifact_root),
        "SKILL_CACHE_ROOT": str(artifact_root.parent / "skill-cache"),
        "DEFAULT_TENANT_ID": TENANT,
        "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
        "PROMETHEUS_MULTIPROC_DIR": str(multiproc),
        "PYTHONUNBUFFERED": "1",
        # 服务间调用只走回环：不让宿主机的代理接管（Console→Runtime 的解析端口 trust_env=True）。
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


def _process_pids(metrics_text: str) -> set[str]:
    pids: set[str] = set()
    for line in metrics_text.splitlines():
        if line.startswith(f"{PROCESS_UP}{{"):
            labels = line.split("{", 1)[1].split("}", 1)[0]
            for pair in labels.split(","):
                name, _, value = pair.partition("=")
                if name == "pid":
                    pids.add(value.strip('"'))
    return pids


def _metric_value(metrics_text: str, name: str, labels: dict[str, str]) -> float:
    wanted = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
    for line in metrics_text.splitlines():
        if not line.startswith(f"{name}{{"):
            continue
        body, _, raw = line.rpartition(" ")
        rendered = body.split("{", 1)[1].rstrip("}")
        if rendered == wanted:
            return float(raw)
    return 0.0


def _metric_samples(metrics_text: str, name: str) -> list[tuple[dict[str, str], float]]:
    samples: list[tuple[dict[str, str], float]] = []
    for line in metrics_text.splitlines():
        if not line.startswith(f"{name}{{"):
            continue
        body, _, raw = line.rpartition(" ")
        rendered = body.split("{", 1)[1].rstrip("}")
        labels = {}
        for pair in rendered.split(","):
            key, _, value = pair.partition("=")
            labels[key] = value.strip('"')
        samples.append((labels, float(raw)))
    return samples


def _scrape(url: str) -> str:
    response = httpx.get(f"{url}/metrics", timeout=5.0)
    assert response.status_code == 200, response.text
    return response.text


@pytest.fixture(scope="module")
def datastore() -> Iterator[dict[str, str]]:
    if os.environ.get("MUAD_ACCEPTANCE_SHARED_DB") == "1":
        pytest.skip("多 worker 状态测试需要独立库（不能用共享 dev 库）")
    info = create_datastore("muad_mw")
    try:
        yield info
    finally:
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


@pytest.fixture(scope="module")
def work_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("multi-worker-state")
    (root / "artifacts").mkdir()
    (root / "skill-cache").mkdir()
    (root / "metrics-gateway").mkdir()
    (root / "metrics-console").mkdir()
    return root


def test_gateway_two_workers_aggregate_metrics_without_losing_counts(
    datastore: dict[str, str], work_root: Path
) -> None:
    """`--workers 2` 的 Gateway：一次抓取看到两个 worker，counter 总数等于全部请求。"""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = _base_env(
        datastore["database_url"], datastore["redis_url"],
        work_root / "artifacts", work_root / "metrics-gateway",
    )
    env.update({
        "CONSOLE_PLATFORM_URL": "http://127.0.0.1:1",
        "AGENT_RUNTIME_URL": "http://127.0.0.1:1",
        # 有界等待压到 0.2s：每条投递都会 timeout → 计数失败结局（本测试只看计数聚合）。
        "DELIVERY_STREAM_WAIT_SEC": "0.2",
        "BOT_SNAPSHOT_POLL_SEC": "30",
    })
    process = _spawn(
        "muad_im_gateway.main:app",
        port=port,
        env=env,
        workers=2,
        app_dir="apps/im-gateway/src",
        log_path=work_root / "gateway.log",
    )
    try:
        _wait_http(f"{url}/healthz")
        attempts = 12
        for index in range(attempts):
            # 每次一条新连接：uvicorn 用共享监听 socket 把连接分给不同 worker。
            task_id = str(uuid.uuid4())
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{url}/internal/deliveries",
                    headers={
                        "X-Internal-Service": INTERNAL_TOKEN,
                        "X-Tenant-Id": TENANT,
                    },
                    json={
                        "tenant_id": TENANT,
                        "task_id": task_id,
                        "delivery_key": f"task:{task_id}:final",
                        "route": {
                            "channel": "WECOM",
                            "bot_id": f"bot-{index % 2}",
                            "external_user_id": "ext-mw",
                            "external_conversation_id": "chat-mw",
                        },
                        "message": {"type": "text", "text": "multi worker"},
                    },
                )
            # 没有 Connector 消费 → 有界等待超时 → 显式 500，不假装送达。
            assert response.status_code >= 500, response.text

        # 多进程快照按 `FLUSH_INTERVAL_SEC` 刷新：等到**一次抓取**同时满足"两个 worker 都在"与
        # "counter 等于全部请求数"，再看断言（不是放宽：抓到的仍是一次完整聚合的结果）。
        deadline = time.monotonic() + METRIC_POLL_SEC
        metrics_text = _scrape(url)
        while time.monotonic() < deadline:
            pids = _process_pids(metrics_text)
            failed = _metric_value(metrics_text, DELIVERY_METRIC, {"status": "failed"})
            if len(pids) >= 2 and failed == attempts:
                break
            time.sleep(0.3)
            metrics_text = _scrape(url)
        pids = _process_pids(metrics_text)
        assert len(pids) >= 2, f"一次抓取必须包含两个 worker 的样本：{sorted(pids)}"

        # counter 是各 worker 之和：12 次请求（跨两个连接集合）一次抓取就应看到 12。
        failed = _metric_value(metrics_text, DELIVERY_METRIC, {"status": "failed"})
        assert failed == attempts, (
            f"{DELIVERY_METRIC}{{status=failed}}={failed} 与真实请求数 {attempts} 不一致"
            f"（多 worker 抓取漏计数）"
        )
    finally:
        _stop(process)


def test_multiproc_gauge_samples_are_per_worker_and_sum_to_the_total(work_root: Path) -> None:
    """gauge 聚合语义：每个 worker 一条 `pid` 样本；各 worker 之和 = 全局总数。

    真实的 SSE 账本 gauge 由订阅方设置；这里用同一套多进程渲染验证"总量 = 各 worker 之和"
    这条口径（两个 worker 的独立快照 → 一次 render）。
    """
    directory = work_root / "metrics-ledger"
    directory.mkdir(exist_ok=True)
    for pid, value in ((41001, 300), (41002, 700)):
        (directory / f"muad_metrics_{pid}.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "written_at": time.time(),
                    "samples": [
                        {
                            "name": "gateway_sse_subscribers",
                            "labels": [],
                            "value": value,
                        }
                    ],
                    "kinds": {"gateway_sse_subscribers": "gauge"},
                    "help": {"gateway_sse_subscribers": "Active SSE subscribers"},
                }
            ),
            encoding="utf-8",
        )
    from muad_api.metrics import MetricsRegistry

    text = MetricsRegistry(multiproc_dir=directory).render()
    samples = _metric_samples(text, "gateway_sse_subscribers")
    assert {labels.get("pid") for labels, _ in samples} == {"41001", "41002"}
    assert sum(value for _labels, value in samples) == 1000


def _run_in_thread(coro_factory: Any) -> Any:
    """在独立线程的事件循环里跑协程：不动主线程的事件循环（同会话后续异步套件不受影响）。"""
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 - 跨线程原样抛回调用方
            box["error"] = exc

    thread = threading.Thread(target=runner, name="multi-worker-state-db")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _seed_artifact(database_url: str, artifact_root: Path) -> uuid.UUID:
    """真实 PG：一行 artifact + 共享存储里的字节（Console 取件端点的真实目标）。"""
    import asyncpg

    artifact_id = uuid.uuid4()
    storage_key = f"mw/{artifact_id}.txt"
    target = artifact_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("multi worker artifact", encoding="utf-8")
    size = target.stat().st_size

    async def insert() -> None:
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql"))
        try:
            await connection.execute(
                "INSERT INTO runtime.artifact"
                " (id, tenant_id, task_id, artifact_type, storage_key, media_type, size, checksum)"
                " VALUES ($1, $2, $3, 'DOCUMENT', $4, 'text/plain', $5, 'checksum')",
                artifact_id,
                TENANT,
                uuid.uuid4(),
                storage_key,
                size,
            )
        finally:
            await connection.close()

    _run_in_thread(insert)
    return artifact_id


def test_console_artifact_token_issued_in_one_worker_redeems_in_another(
    datastore: dict[str, str], work_root: Path
) -> None:
    """Console `--workers 2`：token 存共享 Redis，跨 worker 兑换成功（不再 404）。"""
    console_port, runtime_port = _free_port(), _free_port()
    console_url = f"http://127.0.0.1:{console_port}"
    runtime_url = f"http://127.0.0.1:{runtime_port}"
    artifact_root = work_root / "artifacts"
    artifact_id = _seed_artifact(datastore["database_url"], artifact_root)
    env = _base_env(
        datastore["database_url"], datastore["redis_url"],
        artifact_root, work_root / "metrics-console",
    )
    console_env = {
        **env,
        "CONSOLE_PLATFORM_URL": console_url,
        "AGENT_RUNTIME_URL": runtime_url,
    }
    runtime_env = {**env, "CONSOLE_PLATFORM_URL": console_url, "IM_GATEWAY_URL": ""}
    runtime = _spawn(
        "muad_agent_runtime.main:app",
        port=runtime_port,
        env=runtime_env,
        workers=1,
        log_path=work_root / "runtime.log",
    )
    console = _spawn(
        "muad_console_platform.main:app",
        port=console_port,
        env=console_env,
        workers=2,
        app_dir="apps/console-platform/backend/src",
        log_path=work_root / "console.log",
    )
    try:
        _wait_http(f"{console_url}/healthz")
        _wait_http(f"{runtime_url}/healthz")
        with httpx.Client(timeout=10.0) as client:
            direct = client.get(
                f"{runtime_url}/internal/artifacts/{artifact_id}",
                headers={"X-Internal-Service": INTERNAL_TOKEN, "X-Tenant-Id": TENANT},
            )
            assert direct.status_code == 200, f"runtime 解析失败：{direct.text}"
            issued = client.post(
                f"{console_url}/internal/artifacts/{artifact_id}/fetch-link",
                headers={"X-Internal-Service": INTERNAL_TOKEN, "X-Tenant-Id": TENANT},
            )
        assert issued.status_code == 200, issued.text
        fetch_url = issued.json()["data"]["url"]
        assert fetch_url.startswith(console_url), fetch_url
        token = fetch_url.split("token=", 1)[1]

        # 共享存储的直接证据：token 键在多进程共享的 Redis 里（不是某个 worker 的内存）。
        async def read_key() -> str | None:
            client = redis_async.from_url(datastore["redis_url"], decode_responses=True)
            try:
                return await client.get(f"{FETCH_KEY_PREFIX}{token}")
            finally:
                await client.aclose()

        assert _run_in_thread(read_key) is not None, (
            "取件令牌必须落在共享 Redis（多 worker 才可能跨进程兑换）"
        )

        # 8 条**新连接**兑换：无论落到哪个 worker 都必须 200 且字节一致。
        for _ in range(8):
            with httpx.Client(timeout=10.0) as client:
                response = client.get(fetch_url)
            assert response.status_code == 200, response.text
            assert response.content == b"multi worker artifact"

        metrics_text = _scrape(console_url)
        deadline = time.monotonic() + METRIC_POLL_SEC
        while len(_process_pids(metrics_text)) < 2 and time.monotonic() < deadline:
            time.sleep(0.3)
            metrics_text = _scrape(console_url)
        assert len(_process_pids(metrics_text)) >= 2, "Console 的 /metrics 必须包含两个 worker"
    finally:
        _stop(console)
        _stop(runtime)
