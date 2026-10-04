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
- **指标暴露与 label 卫生**：每个服务声明自己的 metric catalog 并通过 api-kit 暴露真实 `GET /metrics`（无流量时 catalog 亦可见）；label 不得含高基数或敏感维度（禁止资源 UUID/Secret/消息体/用户可控路径，路由 label 取模板而非具体路径）；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志；`run_reclaim_total` 属 Runtime（Worker 无 Run 回收路径，对应 `task_reclaim_total`）。四个单元**都**声明 catalog 后再 `install_metrics`：Runtime/Worker 见各自 `metrics.py` 的 `CATALOG`；IM Gateway 见 `apps/im-gateway/src/muad_im_gateway/metrics.py`（2026-10-04 补齐，此前它是「只有 `install_metrics(app)`、目录为空」的现状差异）——机检 `tests/gateway/test_gateway_metrics.py`（无流量也暴露目录 + label 卫生）。

- **探针口径**：`/healthz` 是存活、`/readyz` 是就绪（依赖缺失返回 503，api-kit `install_health_probes`）。部署侧让 `readinessProbe` 接 `/readyz`、`livenessProbe` 接 `/healthz` —— 四个 k8s 清单**已按此接**（2026-10-04 改；此前两个探针都指 `/healthz`，api-kit 的 `/readyz` 从未被部署消费）。**服务间接线同理**：`CONSOLE_PLATFORM_URL`/`AGENT_RUNTIME_URL`/`AGENT_WORKER_URL`/`IM_GATEWAY_URL`/`DEFAULT_TENANT_ID` 必须由 ConfigMap 显式给（缺了应用会回落到 `127.0.0.1` 本机默认值，集群内必然连不上对端），`INTERNAL_SERVICE_TOKEN` 必须四个单元一致；迁移要有集群内入口（Job 用 Console 镜像做载体，`alembic` 因此进了该包依赖）。以上同由 `tests/architecture/test_k8s_deploy_manifests.py` 机检。

- **后端内部分层固定为 `api/ → application/ → infrastructure/`**（`domain/` 为纯领域）：路由只做协议适配与依赖注入，业务编排在 `application/*_service.py`，数据访问在 `infrastructure/repositories/*_repository.py`（**该收口仅 console 后端成立**：全仓只有 `apps/console-platform/backend/.../infrastructure/repositories/` 与 `.../domain/`；runtime/worker/gateway 无这两个包，其数据访问落在 `infrastructure/*.py` 与 `application/*_service.py`）。**不存在 `modules/<模块>/` 子包**——那是前端的约定（`apps/**/frontend/src/modules/`）；设计模板若按前端形态写后端目录，实现按本分层落地并在任务文档登记「设计待更正」。

- **应用层可以按功能划分子包**：Runtime 附件与制品逻辑集中在 `application/attachments/`（`tools.py`、`inbound.py`、`reference.py`、`tool_results.py`）；功能子包仍属于 application 层，数据库模型与存储实现归 infrastructure，执行器负责组装。✅ `application/attachments/tools.py` 引用 `infrastructure.models.runtime.Artifact`；❌ 在功能子包中复制数据库模型，或反向引用执行器和 Run 编排服务导致循环依赖。

- **`py.typed` 只在 `packages/*`，`apps/*` 没有**：8 个共享库（`muad_api`/`muad_common`/`muad_contracts`/`muad_logging`/`muad_artifact_store`/`muad_agent_core`/`muad_platform_sdk`/`muad_skill_sdk`）都带 `py.typed`，四个 app 包（`muad_console_platform`/`muad_agent_runtime`/`muad_agent_worker`/`muad_im_gateway`）都不带。后果：mypy 分析 **test 文件**时会把 app 包当作「已安装但未类型化」的库，对 `from muad_console_platform... import ...` 报 `import-untyped`（*module is installed, but missing library stubs or py.typed marker*）；跨目录传 test 文件还会触发 duplicate-module。**这是口径问题而非代码缺陷**——未改动目录同样复现。判定口径：mypy 的结论只对生产文件成立（`make typecheck` 本就是 `uv run mypy apps packages`，不含 tests）；`cf_validation --files` 传 test 文件时的 mypy 失败按此折算，不要据此改实现。若要让 test 文件也受严格检查，需先给四个 app 包补 `py.typed`（会扩大全仓 mypy 范围，应另立变更并评估新暴露的错误）。

- **Pod 绑定门禁是「文本标记扫描」而非语义分析**：`POD_MARKERS = ("pod_id", "agent_pod", "pod_mapping", "agent_to_pod")`，四个部署单元的任一 `*.py` 源码文本命中任一标记即红（`tests/architecture/test_im_gateway_boundaries.py:19,80-89`）。因此该门禁只约束**命名**——✅ 用 `lease_owner`/`instance_id` 表达执行者归属；❌ 变量名写成 `pod_mapping` 之类，即便语义上并未做 Pod 绑定也会在四单元内触发红。

- **Console 部署单元是 `apps/console-platform/backend`，不是整个 `apps/console-platform`**：`CONSOLE = "apps/console-platform/backend"`（`tests/architecture/test_im_gateway_boundaries.py:16`），它与 Gateway/Runtime/Worker 同列 `DEPLOYMENT_UNITS`（`:17`，入口断言见 `:46-52`）。前端 `apps/console-platform/frontend` 不在部署单元的可检查范围里；新增跨单元边界检查时不要把整个 `console-platform` 目录当作 Console 单元。

- **压缩的「受保护前缀」是开头连续的一段 `role=SYSTEM`**：系统提示、memory 注入、摘要前缀（`summary_message` 渲染出来也是 SYSTEM）都在这一段里，它们是每次请求必须原样带上的**权威上下文**，不是历史。所以任何**会删消息**的压缩层（snip、条数兜底）只能在其后的**对话区**上工作；只换内容不删消息的层（micro）不涉及这条边界。裁掉前缀＝agent 失忆（系统提示定义了身份与指令）、memory 注入措辞失效、被压缩掉的那段历史净消失。
  - ✅ `prefix, region = split_protected_prefix(sanitized)`，snip 与条数兜底都只切 `region`（`packages/agent-core/src/muad_agent_core/context/compactor.py:89-108,203-204,305`）；memory 与摘要以 SYSTEM 前置、落在前缀里（`apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py:109-119`，措辞的唯一效力边界见 `:42-46`）
  - ❌ 对整条消息列表（含开头 SYSTEM）直接 snip / 按条数裁剪 ⇒ 系统提示或摘要前缀被当历史省掉

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
