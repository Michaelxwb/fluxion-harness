"""demo-async：演示用异步技能脚本。

约定（与 SkillExecutor 一致）：
- 输入：stdin 上的一个 JSON 对象（工具调用参数 / 任务输入）。
- 输出：stdout 上的一个 JSON 对象（≤ 64 KiB）；成功退出码 0，失败写 stderr、退出码非 0。

字段：
- ``delay_sec``：模拟耗时，0–30（默认 3），撑出 WAITING_TOOL 观察窗口。
- ``fail``：``true`` 时以非零码退出，演示失败结果回流。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime

ECHO_LIMIT_BYTES = 4096
DEFAULT_DELAY_SEC = 3.0
MAX_DELAY_SEC = 30.0


def _delay_seconds(payload: dict[str, object]) -> float:
    raw = payload.get("delay_sec", DEFAULT_DELAY_SEC)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("delay_sec must be a number")
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("delay_sec must be finite")
    return max(0.0, min(value, MAX_DELAY_SEC))


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    delay = _delay_seconds(payload)
    if payload.get("fail") is True:
        print(f"demo-async injected failure after {delay:.1f}s", file=sys.stderr)
        return 1
    if delay:
        time.sleep(delay)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    size = len(encoded.encode("utf-8"))
    echo: object = payload if size <= ECHO_LIMIT_BYTES else {"truncated": True, "bytes": size}
    result = {
        "echo": echo,
        "delayed_sec": delay,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
