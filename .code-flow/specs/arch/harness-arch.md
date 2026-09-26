---
id: harness-arch
description: Agent Harness 通用平台规则：arch
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-arch-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/architecture
    cwd: .
    timeout: 300
---

# harness-arch

## Rules

- [RULE-arch-001] 固定四个部署单元（console-platform / agent-runtime / agent-worker / im-gateway）；Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行。

## Conventions

本 Spec 的 verifier 跑 `tests/architecture`，该套件实际强制的规则族比 RULE-arch-001 更宽，以下三条同为硬约束：

- **依赖方向单向**：`packages/*` 不得 import 四个 app 模块；runtime/worker 不得 import `muad_console_platform`（跨部署单元一律走 HTTP 契约，参考 `infrastructure/console_client.py`）；`skill-sdk` 不依赖 runtime/console/worker（`tests/architecture/test_import_direction.py`）。
- **IM Gateway 边界**：Gateway 不持库（不得 import `sqlalchemy`，迁移与持库归其他 Owner）；渠道 SDK（`aibot`/`websockets`）只允许出现在 Gateway 的 `channels/` 适配器层；`iter_events()` 是唯一入站规范化入口，只由 `application/inbound.py` 消费（`tests/architecture/test_im_gateway_boundaries.py`）。
- **指标暴露与 label 卫生**：每个服务声明自己的 metric catalog 并通过 api-kit 暴露真实 `GET /metrics`（无流量时 catalog 亦可见）；label 不得含高基数或敏感维度（禁止资源 UUID/Secret/消息体/用户可控路径，路由 label 取模板而非具体路径）；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志；`run_reclaim_total` 属 Runtime（Worker 无 Run 回收路径，对应 `task_reclaim_total`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
