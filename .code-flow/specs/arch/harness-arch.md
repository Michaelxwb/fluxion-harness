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

- **后端内部分层固定为 `api/ → application/ → infrastructure/`**（`domain/` 为纯领域）：路由只做协议适配与依赖注入，业务编排在 `application/*_service.py`，数据访问在 `infrastructure/repositories/*_repository.py`。**不存在 `modules/<模块>/` 子包**——那是前端的约定（`apps/**/frontend/src/modules/`）；设计模板若按前端形态写后端目录，实现按本分层落地并在任务文档登记「设计待更正」。

- **`py.typed` 只在 `packages/*`，`apps/*` 没有**：8 个共享库（`muad_api`/`muad_common`/`muad_contracts`/`muad_logging`/`muad_artifact_store`/`muad_agent_core`/`muad_platform_sdk`/`muad_skill_sdk`）都带 `py.typed`，四个 app 包（`muad_console_platform`/`muad_agent_runtime`/`muad_agent_worker`/`muad_im_gateway`）都不带。后果：mypy 分析 **test 文件**时会把 app 包当作「已安装但未类型化」的库，对 `from muad_console_platform... import ...` 报 `import-untyped`（*module is installed, but missing library stubs or py.typed marker*）；跨目录传 test 文件还会触发 duplicate-module。**这是口径问题而非代码缺陷**——未改动目录同样复现。判定口径：mypy 的结论只对生产文件成立（`make typecheck` 本就是 `uv run mypy apps packages`，不含 tests）；`cf_validation --files` 传 test 文件时的 mypy 失败按此折算，不要据此改实现。若要让 test 文件也受严格检查，需先给四个 app 包补 `py.typed`（会扩大全仓 mypy 范围，应另立变更并评估新暴露的错误）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
