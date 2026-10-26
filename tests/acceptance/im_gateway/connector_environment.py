"""Connector 验收栈：真实 Console/Runtime + 两个真实 Connector 进程 + 假企微 WS。

不启动 Gateway：TASK-007 验证 Connector 自己的租约/流/降级边界；Gateway 切换归 TASK-009。
所有服务都是独立 uvicorn 子进程，Redis/PG 用验收会话的隔离实例。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from muad_common import SharedSettings

from tests.acceptance.datastores import seed_platform_settings
from tests.acceptance.im_gateway.environment import (
    INTERNAL_TOKEN,
    cleanup,
)
from tests.acceptance.task_schedule.environment import (
    ServiceProcess,
    clear_engine_caches,
    free_port,
)
from tests.e2e.seed_im_gateway import (
    AGENT_KEY,
    BOT_ID,
    BOT_SECRET,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    TENANT,
    seed_control,
)

CONNECTOR_READY_TIMEOUT_SEC = 30.0
CONNECTOR_PLATFORM_SETTINGS = {
    "im": {"progress_interval_sec": 1},
    "task": {"delivery_backoff_base_sec": 1},
}


@dataclass
class ConnectorStack:
    console_url: str
    runtime_url: str
    llm_url: str
    wecom_ws_url: str
    artifact_root: Path
    tenant_id: str
    agent_id: object
    platform_user_id: object
    bot_id: str
    bot_secret: str
    external_user_id: str
    chat_id: str
    processes: dict[str, ServiceProcess] = field(default_factory=dict)
    ws_probe: object | None = None

    def connector(self, index: int = 0) -> ServiceProcess:
        return self.processes[f"connector-{index}"]

    def owner_process(self, owner_id: str) -> ServiceProcess:
        for name, process in self.processes.items():
            if name.startswith("connector-") and process.env.get("CONNECTOR_OWNER_ID") == owner_id:
                return process
        raise AssertionError(f"owner process not found: {owner_id}")


def _spawn(
    processes: list[ServiceProcess],
    *,
    name: str,
    module: str,
    port: int,
    env: dict[str, str],
    logs: Path,
) -> ServiceProcess:
    process = ServiceProcess(
        name=name,
        module=module,
        port=port,
        env=env,
        log_path=logs / f"{name}.log",
    )
    processes.append(process)
    return process


def start_connector_stack(
    root: Path,
    *,
    ws_probe_url: str,
    ws_ca_file: str,
    connectors: int = 2,
    connector_env: dict[int, dict[str, str]] | None = None,
) -> tuple[ConnectorStack, list[ServiceProcess]]:
    settings = SharedSettings()
    database_url = os.environ.get("DATABASE_URL") or settings.database_url
    redis_url = os.environ.get("REDIS_URL") or settings.redis_url
    assert database_url and redis_url, "验收栈需要 DATABASE_URL/REDIS_URL"

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    console_port, runtime_port, llm_port = free_port(), free_port(), free_port()
    console_url = f"http://127.0.0.1:{console_port}"
    runtime_url = f"http://127.0.0.1:{runtime_port}"
    llm_url = f"http://127.0.0.1:{llm_port}"

    base_env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(artifact_root),
        "DEFAULT_TENANT_ID": TENANT,
        "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
        "CONSOLE_PLATFORM_URL": console_url,
        "AGENT_RUNTIME_URL": runtime_url,
        "WECOM_WS_URL": ws_probe_url,
        "WECOM_WS_CA_FILE": ws_ca_file,
        "BOT_SNAPSHOT_POLL_SEC": "1",
        "CONNECTOR_LEASE_TTL_SEC": "5",
        "CONNECTOR_LEASE_RENEW_SEC": "1",
        "CONNECTOR_INBOUND_LOST_AFTER_SEC": "60",
        "CONNECTOR_HANDLED_CHANNELS": "WECOM",
    }

    processes: list[ServiceProcess] = []
    llm = _spawn(
        processes,
        name="llm-probe",
        module="tests.e2e.openai_probe_app",
        port=llm_port,
        env=base_env,
        logs=logs,
    )
    console = _spawn(
        processes,
        name="console",
        module="muad_console_platform.main",
        port=console_port,
        env={**base_env, "AGENT_RUNTIME_URL": runtime_url, "CONSOLE_PLATFORM_URL": console_url},
        logs=logs,
    )
    runtime = _spawn(
        processes,
        name="runtime",
        module="muad_agent_runtime.main",
        port=runtime_port,
        env={**base_env, "POD_NAME": "e2e-connector-runtime", "IM_GATEWAY_URL": ""},
        logs=logs,
    )
    for index in range(connectors):
        env = {
            **base_env,
            "CONNECTOR_OWNER_ID": f"connector-{index}",
            "CONNECTOR_INBOUND_SHARDS": "8",
        }
        env.update((connector_env or {}).get(index, {}))
        _spawn(
            processes,
            name=f"connector-{index}",
            module="muad_im_connector.main",
            port=free_port(),
            env=env,
            logs=logs,
        )

    llm.start()
    cleanup(database_url)
    seeded = seed_control(llm_url)
    seed_platform_settings(database_url, TENANT, CONNECTOR_PLATFORM_SETTINGS)
    console.start()
    runtime.start()
    for index in range(connectors):
        processes[3 + index].start()
    _wait_for_connectors(processes, connectors)

    stack = ConnectorStack(
        console_url=console_url,
        runtime_url=runtime_url,
        llm_url=llm_url,
        wecom_ws_url=ws_probe_url,
        artifact_root=artifact_root,
        tenant_id=seeded["tenant_id"],
        agent_id=seeded["agent_id"],
        platform_user_id=seeded["platform_user_id"],
        bot_id=seeded["bot_id"],
        bot_secret=seeded["bot_secret"],
        external_user_id=seeded["bound_external_user_id"],
        chat_id=seeded["chat_id"],
        processes={process.name: process for process in processes},
    )
    return stack, processes


def _wait_for_connectors(processes: list[ServiceProcess], connectors: int) -> None:
    deadline = time.monotonic() + CONNECTOR_READY_TIMEOUT_SEC
    pending = {f"connector-{index}" for index in range(connectors)}
    while time.monotonic() < deadline:
        for name in tuple(pending):
            url = next(process.url for process in processes if process.name == name)
            try:
                if httpx.get(f"{url}/readyz", timeout=2.0).status_code == 200:
                    pending.discard(name)
            except httpx.HTTPError:
                continue
        if not pending:
            return
        time.sleep(0.1)
    logs = {process.name: process.log_path.read_text(errors="replace")[-2000:] for process in processes}
    raise AssertionError(f"connectors not ready: {pending}; logs={logs}")


def stop_connector_stack(processes: list[ServiceProcess]) -> None:
    for process in reversed(processes):
        process.stop()
    clear_engine_caches()


def kill_process(process: ServiceProcess) -> None:
    """SIGKILL：不触发优雅收尾/租约释放，验证真实过期接管。"""
    proc = process._process  # noqa: SLF001 - 验收故意制造崩溃
    assert proc is not None
    proc.kill()
    proc.wait(timeout=10)


def restart_process(process: ServiceProcess) -> None:
    process.start()


__all__ = [
    "AGENT_KEY",
    "BOT_ID",
    "BOT_SECRET",
    "BOUND_EXTERNAL_USER_ID",
    "CHAT_ID",
    "TENANT",
    "ConnectorStack",
    "INTERNAL_TOKEN",
    "kill_process",
    "restart_process",
    "start_connector_stack",
    "stop_connector_stack",
]
