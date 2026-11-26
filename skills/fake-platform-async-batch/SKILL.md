---
name: fake-platform-async-batch
description: ASYNC 批量报表样例：从 fake business platform 读取目标，生成多个子任务，每个子任务产生 JSON/CSV 文件。
execution: ASYNC
invocation_contract:
  version: 1
  invocation: stdin
  schema:
    type: object
    properties:
      targets:
        type: array
        items:
          type: string
        minItems: 1
        description: "目标列表；缺省时从 fake platform 的 /api/v1/targets 读取"
      max_concurrency:
        type: integer
        minimum: 1
        description: "子任务并发数"
      aggregate_mode:
        type: string
        enum: ["ALL", "BEST_EFFORT"]
        description: "聚合模式"
      offline:
        type: boolean
        description: "跳过平台调用；为 true 时必须显式提供 targets"
      fail_targets:
        type: array
        items:
          type: string
        description: "需要模拟失败的目标"
    required: []
    additionalProperties: false
---

# fake-platform-async-batch

用于端到端验证 **ASYNC Skill + BATCH 子任务 fan-out + 文件产物**。

- 平台：`smoke_platform`，HTTP 端点为仓库内 `tools/fake-business-platform/server.py`（`--platform mssw`）。
- 父执行：读取 fake platform 的 `/api/v1/targets`（或输入里的 `targets`），输出
  `{"batch": {"items": [...]}}`，由 Worker 创建子任务。
- 子执行：每个子任务调用平台健康检查、CSRF 业务接口和目标列表，校验 `Traceid` 回显，
  在 `SKILL_OUTPUT_DIR` 写一个 JSON 与一个 CSV 文件。

## 输入

父执行（没有 `target` 字段）：

```json
{
  "targets": ["华东", "华南", "华北"],
  "max_concurrency": 2,
  "aggregate_mode": "BEST_EFFORT",
  "offline": false,
  "fail_targets": ["华南"]
}
```

- `targets` 缺省时从 fake platform 的 `/api/v1/targets` 读取；显式传入时不访问平台。
- `offline: true` 时父/子任务都不访问平台，只用于不依赖登录态的 Worker 验收。
- `fail_targets` 命中的子任务会显式失败，用于验证部分失败计数。

## 定时任务

调用契约使用 `stdin`：`input` 传上面的对象，`argv` 必须为空。最小输入是 `{}`，
表示从 fake platform 读取 targets；如果要离线或固定目标，再显式传 `targets` 和
`offline`。平台按 Skill 声明的 `entrypoint` 执行，不需要额外传脚本路径。

子执行由 Worker 自动传入一个 item，例如 `{"target": "华东", "offline": false}`；不要手工调用。

## 输出

每个子任务写：

- `report-<target>.json`：平台响应、Traceid、目标与校验值。
- `report-<target>.csv`：一行摘要，便于验证文件产物。

## 本地联调

```bash
./tools/fake-business-platform/services.sh start platform
uv run python tools/fake-business-platform/smoke_check.py
uv run pytest -q tests/skills/test_fake_platform_async_batch.py
```

真实 Worker 链路：

```bash
uv run pytest -q tests/acceptance/task_schedule/test_fake_platform_async_batch.py
```
