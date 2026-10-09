---
name: demo-async
description: 演示用异步技能：按输入延迟若干秒后返回 JSON 回显；用于观察 WAITING_TOOL 等待、结果回流与原 Run 接续。
execution: ASYNC
---

# demo-async

演示用**异步技能**（`execution: ASYNC`）：模型调用后先拿提交回执，Worker 在后台执行
`scripts/main.py`；Run 在此期间进入 `WAITING_TOOL`，结果回流后同一 Run 自动接续。

## 输入（stdin，任意 JSON 对象）

| 字段 | 类型 | 说明 |
|---|---|---|
| `delay_sec` | 数字，可选 | 模拟耗时，0–30，默认 3；用来撑出可观察的等待窗口 |
| `fail` | 布尔，可选 | `true` 时脚本以非零码退出，演示失败结果回流 |
| 其他字段 | 任意 | 原样回显在 `echo` |

## 输出（stdout，JSON 对象）

- `echo`：原始输入（序列化超过 4 KiB 时截断为 `{"truncated": true, "bytes": N}`）
- `delayed_sec`：实际延迟秒数
- `finished_at`：完成时间（UTC，ISO8601）

## 示例

调用输入 `{"delay_sec": 3, "order_id": "A-1001"}` → 3 秒后返回：

```json
{"delayed_sec": 3.0, "echo": {"delay_sec": 3, "order_id": "A-1001"}, "finished_at": "2026-10-09T08:00:00+00:00"}
```
