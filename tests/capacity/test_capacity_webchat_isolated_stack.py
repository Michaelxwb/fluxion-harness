"""B-307：四单元独立验收栈与故障进程控制（TASK-107；设计 §2.5 验收条件 / §4.3 分层验收与报告）。

真实边界（不 mock 任何服务、不用伪造成功回执）：
- **数据存储复用验收机制**：`tests/acceptance/datastores.create_datastore` 建空库 + `alembic upgrade
  head`，并按验收约定占 10–15 号隔离 Redis 号位（`SET NX EX` 占号，收尾释放）；
- **四单元是真实 uvicorn 子进程**（Console/Runtime/Worker/Gateway）：独立端口、独立进程组，
  OS 可见的真实 env（macOS `ps eww` / Linux `/proc/<pid>/environ`）证明库/Redis/共享卷确实注入进程；
- **共享卷**：四单元同挂一个 `ARTIFACT_ROOT`（RWX 口径），每进程 `SKILL_CACHE_ROOT` 独立；
  Worker `/readyz` 真实探测该挂载点 —— 删掉目录立刻 503，恢复后 200；
- **入口代理**按 `deploy/k8s/base/ingress.yaml` 的真实分流规则转发（认证面 → Console、其余聊天
  API → Gateway），可起停；模型侧是 TASK-106 的真实 HTTP 协议探针子进程（真实 SSE 分帧）；
- **故障进程控制**：SIGKILL 与优雅停机都作用于整进程组，替换实例共享同一库/卷/Redis；
- **收尾只清本轮**：drop 本轮库、停全部子进程（含进程组）、释放 Redis 号位与端口，不动共享 dev 库。

断言位置见各用例内的中文说明（B-307 关键断言逐项对应）。
"""

from __future__ import annotations

import os
import shutil
import socket
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from muad_api.startup import migration_head
from muad_common import SharedSettings

from tests.acceptance.capacity.stack import (
    DEV_DEFAULT_PORTS,
    UNITS,
    CapacityStack,
    acceptance_databases,
    acquire_datastore,
    database_exists,
    load_ingress_routes,
    query_value,
    read_process_environ,
    redis_value,
)
from tests.acceptance.datastores import (
    REDIS_CLAIM_KEY,
    REDIS_DB_COUNT,
    REDIS_DB_START,
    create_datastore,
    drop_datastore,
)
from tests.capacity.profile import load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = Path(__file__).resolve().parent / "profiles" / "agent-capacity-webchat.example.yml"
PROBE_KEY = "sk-capacity-stack-probe"
PROBE_PAYLOAD = {
    "model": "probe-model",
    "stream": True,
    "messages": [{"role": "user", "content": "ping"}],
}


@dataclass(frozen=True, slots=True)
class DevBaseline:
    """开跑前记录共享 dev 侧的真实基线：收尾只允许本轮的临时库/号位消失。"""

    database_name: str
    admin_database_url: str
    redis_url: str
    acceptance_databases: tuple[str, ...]


def _db_index(url: str) -> int:
    return int(urlsplit(url).path.lstrip("/"))


def _http() -> httpx.Client:
    # trust_env=False：本机系统代理会把探针/代理的真实 socket 行为改写掉（TASK-106 同口径）。
    return httpx.Client(timeout=15.0, trust_env=False)


def _probe_payload(response: httpx.Response) -> dict[str, Any]:
    """健康探针经 api-kit 封套（`data`）返回；探针控制面是裸 JSON —— 两种都兼容。"""
    body = response.json()
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body


def _port_is_reusable(port: int) -> bool:
    """端口真的被归还：能重新 bind 才算没有残留监听（不是看栈内记账）。"""
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _assert_group_gone(pgid: int) -> None:
    """进程组整体消失（不只是组长进程退出）。"""
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)


@pytest.fixture(scope="module")
def dev_baseline() -> DevBaseline:
    """共享 dev 库/Redis 的只读基线（本栈绝不写 dev 库，只读 pg_database 与占号键）。"""
    settings = SharedSettings()
    admin_url = settings.require_database_url()
    redis_url = settings.require_redis_url()
    return DevBaseline(
        database_name=urlsplit(admin_url).path.lstrip("/"),
        admin_database_url=admin_url,
        redis_url=redis_url,
        acceptance_databases=acceptance_databases(admin_url),
    )


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory, dev_baseline: DevBaseline) -> Iterator[CapacityStack]:
    """真实独立栈（profile 的 replica_counts 决定 Runtime/Worker 副本数）；模块结束统一收尾。"""
    instance = CapacityStack(
        root=tmp_path_factory.mktemp("capacity-stack"),
        profile=load_profile(PROFILE_PATH),
    )
    instance.start()
    try:
        yield instance
    finally:
        instance.teardown()


def test_b307_datastore_reuses_acceptance_empty_db_and_isolated_redis_slots(
    stack: CapacityStack, dev_baseline: DevBaseline
) -> None:
    """断言①：空库 + alembic head + 10–15 号 Redis 号位，且与共享 dev 库/号位分离。"""
    datastore = stack.datastore
    assert datastore.name.startswith("muad_acc_"), "复用验收建库前缀（muad_acc_<uuid>）"
    assert datastore.name != dev_baseline.database_name, "绝不落在共享 dev 库上"
    assert stack.database_url != dev_baseline.admin_database_url

    head = migration_head(ROOT / "migrations" / "versions")
    assert head is not None, "迁移脚本必须能判定唯一 head"
    assert query_value(stack.database_url, "SELECT version_num FROM alembic_version") == head, (
        "空库必须已由 alembic upgrade head 迁移（真实查 alembic_version）"
    )
    assert (
        query_value(stack.database_url, "SELECT to_regclass('runtime.run_record')::text")
        == "runtime.run_record"
    ), "迁移真的建了业务表，不是空库冒充"

    index = _db_index(stack.redis_url)
    assert REDIS_DB_START <= index < REDIS_DB_START + REDIS_DB_COUNT, "隔离 Redis 号位取验收约定区间"
    assert redis_value(stack.redis_url, REDIS_CLAIM_KEY) == datastore.name, (
        "本轮的号位确实被本栈占住（占号值 = 库名）"
    )
    assert redis_value(dev_baseline.redis_url, REDIS_CLAIM_KEY) is None, "dev 号位（0 号）不被本栈动过"


def test_b307_acceptance_session_datastore_is_reused_instead_of_a_second_one(
    tmp_path: Path,
) -> None:
    """复用分支：验收会话已建好独立库/号位时，栈复用它（owns=False）且收尾不 drop 会话的库。

    这不是细节：进程内 seed 走 `SharedSettings()`（读 env），服务子进程走注入 env —— 两份库
    会裂脑（seed 写旧库、服务读新库）。验收会话里必须复用同一份。
    """
    info = create_datastore()
    saved = {key: os.environ.get(key) for key in ("DATABASE_URL", "REDIS_URL")}
    os.environ["DATABASE_URL"] = info["database_url"]
    os.environ["REDIS_URL"] = info["redis_url"]
    try:
        handle = acquire_datastore()
        assert handle.owns is False, "会话里已有独立库：复用，不另建"
        assert handle.name == info["name"]
        assert handle.database_url == info["database_url"]
        assert handle.redis_url == info["redis_url"]

        CapacityStack(root=tmp_path / "reuse", datastore=handle).teardown()
        assert database_exists(info["admin_database_url"], info["name"]) is True, (
            "复用句柄的收尾不得 drop 验收会话的库"
        )
        assert redis_value(info["redis_url"], REDIS_CLAIM_KEY) == info["name"], "号位仍由会话持有"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


def test_b307_four_units_run_as_real_processes_on_isolated_ports(stack: CapacityStack) -> None:
    """断言②：四单元真实子进程 + 独立端口 + 独立进程组 + OS 可见的真实注入 env。"""
    instances = stack.all_instances()
    assert {instance.unit for instance in instances} == set(UNITS), "四单元齐备"
    assert stack.profile is not None and stack.profile.resources is not None
    assert len(instances) == sum(
        stack.profile.resources.replica_counts[unit] for unit in UNITS
    ), "实例数由锁定 profile 的 replica_counts 决定"

    ports = {instance.port for instance in instances}
    assert len(ports) == len(instances), "端口互不冲突（真实 bind 结果）"
    assert not (ports & set(DEV_DEFAULT_PORTS.values())), "不占用 dev 默认端口 8000–8003"

    with _http() as client:
        for instance in instances:
            assert instance.pid != os.getpid(), "必须是独立进程，不是进程内 uvicorn 线程"
            assert instance.pgid == instance.pid, "start_new_session ⇒ 进程组独立（killpg 一次带走整组）"
            assert f"uvicorn {instance.module}:app" in instance.command_line(), (
                "OS 层看到的真实命令行就是该单元的 ASGI 入口"
            )
            env = read_process_environ(instance.pid)
            assert env.get("MUAD_CAPACITY_STACK") == stack.stack_id, "读到的是本栈进程的真实 env"
            assert env.get("DATABASE_URL") == stack.database_url
            assert env.get("REDIS_URL") == stack.redis_url
            assert env.get("ARTIFACT_ROOT") == str(stack.artifacts_root)
            assert env.get("SKILL_CACHE_ROOT") == str(instance.skill_cache_root), (
                "每进程独立 Skill cache root"
            )
            response = client.get(f"{instance.url}/healthz")
            assert response.status_code == 200, f"{instance.unit}#{instance.index} 真实 HTTP 存活"


def test_b307_shared_volume_is_probed_by_the_services_themselves(stack: CapacityStack) -> None:
    """断言③：共享卷是四单元同挂的真实目录，Worker 的就绪检查真实依赖它。"""
    root = stack.artifacts_root
    assert root.is_dir()
    worker = stack.instance("agent-worker")
    runtime = stack.instance("agent-runtime")

    with _http() as client:
        ready = client.get(f"{worker.url}/readyz")
        assert ready.status_code == 200
        assert _probe_payload(ready)["artifact_root"] == str(root), (
            "服务自己报告的真实挂载点 = 本栈共享卷"
        )
        runtime_ready = client.get(f"{runtime.url}/readyz")
        assert runtime_ready.status_code == 200
        assert "artifact_storage" not in _probe_payload(runtime_ready).get("failed", [])

        for instance in stack.all_instances():
            assert read_process_environ(instance.pid)["ARTIFACT_ROOT"] == str(root), (
                "四单元同挂一个 ARTIFACT_ROOT（RWX 口径）"
            )

        # 真实探测：目录消失 ⇒ 就绪立即失败；恢复 ⇒ 通过（不是缓存里的假回执）
        shutil.rmtree(root)
        missing = client.get(f"{worker.url}/readyz")
        assert missing.status_code == 503
        assert "artifact_storage" in _probe_payload(missing)["failed"]
        root.mkdir(parents=True, exist_ok=True)
        assert client.get(f"{worker.url}/readyz").status_code == 200

    written = stack.write_shared_file("capacity/probe.txt", b"shared-volume")
    assert written.read_bytes() == b"shared-volume"
    assert stack.read_shared_file("capacity/probe.txt") == b"shared-volume"


def test_b307_entry_proxy_follows_ingress_manifest_and_stops_cleanly(
    stack: CapacityStack,
) -> None:
    """断言④：入口代理按真实 ingress 清单分流（认证面 → Console、其余聊天 API → Gateway），可起停。"""
    routes = load_ingress_routes()
    assert stack.proxy.stats()["routes"] == [
        {"path": route.path, "unit": route.unit} for route in routes
    ], "代理分流表就是 deploy/k8s/base/ingress.yaml 的规则（不另写一份）"
    assert any(
        route.path == "/api/v1/webchat/auth" and route.unit == "console-platform" for route in routes
    )
    assert any(route.path == "/api/v1/webchat/runs" and route.unit == "im-gateway" for route in routes)

    console_url = stack.url_of("console-platform")
    gateway_url = stack.url_of("im-gateway")
    chat_event = "/api/v1/webchat/runs/00000000-0000-0000-0000-000000000000/events"
    with _http() as client:
        direct_console = client.get(f"{console_url}/api/v1/webchat/auth/exchange")
        direct_gateway = client.get(f"{gateway_url}{chat_event}")
        before = stack.proxy.stats()["requests"]
        via_proxy_console = client.get(f"{stack.proxy.url}/api/v1/webchat/auth/exchange")
        via_proxy_gateway = client.get(f"{stack.proxy.url}{chat_event}")
        after = stack.proxy.stats()["requests"]
        assert via_proxy_console.status_code == direct_console.status_code, (
            "认证面经代理与直连 Console 同响应"
        )
        assert via_proxy_gateway.status_code == direct_gateway.status_code, (
            "聊天 API 经代理与直连 Gateway 同响应"
        )
        assert after["console-platform"] == before["console-platform"] + 1, "认证面只命中 Console"
        assert after["im-gateway"] == before["im-gateway"] + 1, "聊天 API 只命中 Gateway"
        assert client.get(f"{stack.proxy.url}/healthz").status_code == 200, "站点兜底 → Console"

        # 代理可停可起：停机后真实连接失败，重启后恢复转发
        stopped_url = stack.proxy.url
        stack.stop_proxy()
        with pytest.raises(httpx.TransportError):
            client.get(f"{stopped_url}/healthz")
        restarted = stack.start_proxy()
        assert restarted.pid != stack.all_instances()[0].pid, "代理是独立进程"
        assert client.get(f"{restarted.url}/healthz").status_code == 200


def test_b307_model_probe_subprocess_serves_real_sse(stack: CapacityStack) -> None:
    """断言⑤：模型侧是 TASK-106 的真实 HTTP 协议探针子进程（真实 SSE 分帧 + 控制面计数）。"""
    probe = stack.model_probe
    assert probe.pid != os.getpid() and probe.alive, "HTTP 协议探针是真实子进程"
    with _http() as client:
        with client.stream(
            "POST",
            f"{probe.url}/v1/chat/completions",
            json=PROBE_PAYLOAD,
            headers={"Authorization": f"Bearer {PROBE_KEY}"},
        ) as streamed:
            assert streamed.status_code == 200
            data_lines = [line for line in streamed.iter_lines() if line.startswith("data:")]
        assert any('"content"' in line for line in data_lines), "探针按真实 SSE 分帧下发增量"
        assert data_lines[-1] == "data: [DONE]"
        probe_state = _probe_payload(client.get(f"{probe.url}/__probe__/state"))
        assert probe_state["call_count"] >= 1, "真实调用被探针计数（不是伪造回执）"


def test_b307_fault_process_control_kills_and_replaces_instances(stack: CapacityStack) -> None:
    """断言⑥：故障进程控制 —— SIGKILL 整组、替换实例、共享库/卷/Redis 不变。"""
    crashed = stack.instance("agent-runtime", index=0)
    crashed_pid, crashed_pgid, crashed_port = crashed.pid, crashed.pgid, crashed.port
    stack.kill_instance(crashed)
    assert crashed.alive is False
    _assert_group_gone(crashed_pgid)
    assert _port_is_reusable(crashed_port), "SIGKILL 后端口释放"

    sibling = stack.instance("agent-runtime", index=1)
    with _http() as client:
        assert sibling.alive is True, "另一副本不受影响（故障半径限于被替换的实例）"
        assert client.get(f"{sibling.url}/healthz").status_code == 200

    replacement = stack.replace_instance("agent-runtime", index=0)
    assert replacement is stack.instance("agent-runtime", index=0)
    assert replacement.pid != crashed_pid and replacement.port != crashed_port, "替换实例是全新进程/端口"
    env = read_process_environ(replacement.pid)
    assert env["DATABASE_URL"] == stack.database_url, "替换后仍指向同一独立库"
    assert env["REDIS_URL"] == stack.redis_url, "替换后仍指向同一隔离号位"
    assert env["ARTIFACT_ROOT"] == str(stack.artifacts_root), "替换后仍挂同一共享卷"
    with _http() as client:
        assert client.get(f"{replacement.url}/healthz").status_code == 200

    stack.stop_instance(replacement)
    assert replacement.alive is False, "优雅停机（SIGTERM 整组）同样带走进程组"


def test_b307_profile_replica_counts_drive_instances_and_excess_is_rejected(
    stack: CapacityStack, tmp_path: Path
) -> None:
    """断言⑦：实例数来自锁定 profile；超过 max_replica_counts 在启动前显式拒绝。"""
    profile = stack.profile
    assert profile is not None and profile.resources is not None
    for unit in UNITS:
        assert len(stack.instances(unit)) == profile.resources.replica_counts[unit]
    assert len(stack.all_instances()) == sum(profile.resources.replica_counts.values())

    over = replace(
        profile,
        resources=replace(
            profile.resources,
            replica_counts={**profile.resources.replica_counts, "agent-runtime": 99},
        ),
    )
    with pytest.raises(ValueError, match="max_replica_counts"):
        CapacityStack(root=tmp_path / "over-max", profile=over)


def test_b307_teardown_releases_only_this_round_and_leaves_dev_untouched(
    stack: CapacityStack, dev_baseline: DevBaseline
) -> None:
    """断言⑧（本文件最后一个用例）：收尾 drop 本轮库/进程/号位/端口，共享 dev 库原样保留。"""
    instances = stack.all_instances()
    proxy, probe = stack.proxy, stack.model_probe
    datastore, shared_root = stack.datastore, stack.artifacts_root
    stack.teardown()

    for instance in instances:
        assert instance.alive is False
        _assert_group_gone(instance.pgid)
        assert _port_is_reusable(instance.port), "端口全部归还（收尾后可再 bind）"
    assert proxy.alive is False and probe.alive is False, "代理与探针子进程一并停掉"
    assert not shared_root.exists(), "本轮共享卷随收尾移除"

    assert database_exists(dev_baseline.admin_database_url, datastore.name) is False, "本轮临时库已 drop"
    assert acceptance_databases(dev_baseline.admin_database_url) == dev_baseline.acceptance_databases, (
        "共享库上不留本轮残留的 muad_acc_* 库"
    )
    assert database_exists(dev_baseline.admin_database_url, dev_baseline.database_name) is True, (
        "共享 dev 库原样保留"
    )
    assert redis_value(stack.redis_url, REDIS_CLAIM_KEY) is None, "隔离号位已释放"
    assert redis_value(dev_baseline.redis_url, REDIS_CLAIM_KEY) is None, "dev 号位始终未被占用"
