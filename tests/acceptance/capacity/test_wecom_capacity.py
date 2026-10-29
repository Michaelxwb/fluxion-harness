"""企微单 bot + 单 Connector 的 1000 用户私聊容量诊断。

默认 1000 用户、60s ramp、纯文本、每人 1 轮；`CAPACITY_LOAD_USERS` 可降到 100/300/600
做阶梯。该用例标记 `capacity_load`，默认 acceptance 不收集，必须显式运行：

    CAPACITY_LOAD_USERS=1000 MUAD_WECOM_METRICS_OUT=/tmp/wecom-1000.json \
      uv run pytest -q -s tests/acceptance/capacity/test_wecom_capacity.py

并发档（1000 个 Run 同时在飞、模型 15s 首块 + 500ms/段）：

    WECOM_CAPACITY_RAMP_SEC=0 WECOM_MODEL_LATENCY_MS=15000 \
    WECOM_MODEL_CHUNK_INTERVAL_MS=500 WECOM_MODEL_DELTAS=20 \
    make capacity-wecom

真实边界：真实四单元栈 + 真实 WSS 探针 + 真实 Connector；每个用户一份独立
ChannelIdentity/PlatformUser，各自一条私聊消息。**本地结果不等于真实企微平台容量结论。**

诊断开关 `WECOM_MODEL_SLOTS`（默认 2000）与 WebChat 1000×1 同口径；正式 staging E-09
保持 seed 16，不加载本诊断覆盖。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.acceptance.capacity.delivery_harness import (
    PROBE_API_KEY,
    set_probe_behavior,
)
from tests.acceptance.capacity.wecom_capacity import (
    open_wecom_capacity_harness,
    run_wecom_private_chat_load,
)

pytestmark = [pytest.mark.integration, pytest.mark.capacity_load]

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE = ROOT / "tests/capacity/profiles/local-macbook-1000x1-wecom.yml"
USERS = int(os.environ.get("CAPACITY_LOAD_USERS", "1000"))
RAMP_SEC = int(os.environ.get("WECOM_CAPACITY_RAMP_SEC", "60"))
WAIT_TIMEOUT_SEC = float(os.environ.get("WECOM_CAPACITY_WAIT_SEC", "900"))
#: 真实模型首块延迟；并发档用它把 Run 留在执行中，而不是 800ms 就结束。
MODEL_LATENCY_MS = int(os.environ.get("WECOM_MODEL_LATENCY_MS", "800"))
MODEL_CHUNK_INTERVAL_MS = int(os.environ.get("WECOM_MODEL_CHUNK_INTERVAL_MS", "0"))
MODEL_DELTAS = int(os.environ.get("WECOM_MODEL_DELTAS", "3"))


@pytest.fixture(scope="session")
def capacity_profile_path() -> Path:
    raw = os.environ.get("MUAD_CAPACITY_PROFILE", str(DEFAULT_PROFILE))
    path = Path(raw)
    if not path.is_file():
        pytest.fail(f"企微容量 profile 不存在：{path}")
    return path


@pytest.fixture(scope="module")
async def wecom_capacity_harness(
    tmp_path_factory: pytest.TempPathFactory,
    capacity_profile_path: Path,
):
    root = tmp_path_factory.mktemp("wecom-capacity")
    async with open_wecom_capacity_harness(
        root=root,
        profile_path=capacity_profile_path,
        users=USERS,
    ) as harness:
        yield harness


async def test_wecom_1000_private_chat_capacity(wecom_capacity_harness) -> None:
    set_probe_behavior(
        wecom_capacity_harness.stack.model_probe.url,
        {
            "latency_ms": MODEL_LATENCY_MS,
            "chunk_interval_ms": MODEL_CHUNK_INTERVAL_MS,
            "deltas": [f"段{index}" for index in range(MODEL_DELTAS)],
        },
        api_key=PROBE_API_KEY,
    )
    result = await run_wecom_private_chat_load(
        wecom_capacity_harness,
        users=USERS,
        ramp_sec=RAMP_SEC,
        wait_timeout_sec=WAIT_TIMEOUT_SEC,
    )
    payload = result.summary()
    print("WECOM-CAPACITY " + json.dumps(payload, ensure_ascii=False, sort_keys=True))
    target = os.environ.get("MUAD_WECOM_METRICS_OUT", "").strip()
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    assert result.sent == USERS, "每条私聊消息都必须真实发到探针 WS"
    assert result.runs_created >= USERS, (
        f"每个用户都必须创建真实 Run：{result.runs_created}/{USERS}；"
        f"errors={list(result.errors)}"
    )
    assert result.completed >= USERS, (
        f"每个 Run 都必须真实完成：completed={result.completed}/{USERS}；"
        f"failed={result.failed} cancelled={result.cancelled} "
        f"failure_codes={dict(result.failure_codes)} errors={list(result.errors)}"
    )
    assert result.finish_frames >= USERS, (
        f"每个用户都必须收到终帧：{result.finish_frames}/{USERS}"
    )
