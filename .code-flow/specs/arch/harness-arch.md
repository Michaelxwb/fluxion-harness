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

- **系统提示的 section 顺序是契约**（2026-10-07 借鉴 P10 的动态提示设计）：装配出来的提示由**具名段落按固定顺序**组成，唯一实现在 `DefaultPromptBuilder.build`（`packages/agent-core/src/muad_agent_core/prompt/builder.py`）：`instructions` → `## Available skills`（目录）→ `<!-- prompt_template_version -->`（尾标）。加段落**只在尾部追加**，不得插队——顺序一旦只是 `append` 的副产物，任何重构都能悄悄挪动它。用例断言**相对位置**（`prompt.index(A) < prompt.index(B)`），不要断言「包含某段文本」：后者在顺序错乱、甚至同一段出现两次时同样通过。
  - ✅ `prompt.index("be helpful") < prompt.index("## Available skills") < prompt.index("<!-- prompt_template_version")`（`tests/agent_core/test_prompt_builder.py`）
  - ❌ `assert "## Available skills" in prompt` —— 段落被挪到 instructions 之前也照样绿

- **段落为空怎么办：看模型的行为该不该变**（同上，2026-10-07）。核心能力缺失 ⇒ **显式说明**（`tools` 为空要写 `(none)`，否则模型会去调一个不存在的工具）；补充信息缺失 ⇒ **整段省略**，不留占位（Skill 目录、记忆为空时什么都没发生，写「当前没有可用技能」只是噪音，还稀释注意力）。**同一判据也管「段落被截断」**：目录超出预算时模型会以为看到的就是全部，属于行为会变的缺失 ⇒ 必须在段尾说明还有技能未列出、用 `search_skills` 找（`CATALOG_TRUNCATED_NOTE`，**只在真的丢了条目时出现**）。
  - ✅ `if skills:` 才追加 Skill 段（`builder.py`）；`test_empty_skill_catalog_is_omitted` 钉住省略；`test_truncation_is_announced_only_when_something_was_dropped` 钉住「被截断才说明」
  - ❌ 空段落也输出「（无）」「暂无」之类占位；❌ 截断了却不说（模型按不完整的清单行事）

- **自由形态的 JSON 载荷必须是严格 JSON，且**只在一处**判它**（2026-10-07）：`input` / `execution_snapshot` / `input_template` 这类 `dict[str, Any]` 走不到类型检查，必须在**入参**（契约 DTO 的 `field_validator`，见 `muad_contracts.canonical.ensure_strict_json`）就拒掉非 JSON 值。两个下游看过**同一份表示**是硬要求：实测 `CreateTaskRequest.model_dump(mode="json")` 会把 `NaN` 静默转成 `None`，于是**幂等指纹算的是 `{"threshold": null}`，真正写进 jsonb 的却是 `{"threshold": NaN}`**——同一份载荷在两个消费者眼里不是同一份。
  - ✅ 非有限数/未知类型在 DTO 层 422（`tests/agent_worker/test_api.py::test_non_standard_json_payload_is_rejected_at_the_boundary`）；`instructions` 先 `strip()` 再判空（`min_length=1` 挡不住 `"   "`，而装配侧会 strip ⇒ 保存合法、发出空提示）
  - ❌ 让 `NaN` 一路穿过 DTO 与指纹、直到 `INSERT ... ::jsonb` 才被 PostgreSQL 拒（`invalid input syntax for type json`）⇒ 调用方的错报成 500

- **降级必须**语义等价**，否则不许降级**（2026-10-07）：判断"能不能退"的判据不是"退之后还跑得动吗"，而是"退之后还是不是同一个 Agent"。压缩失败 ⇒ 原文照发（只是没省 token，同一个 Agent）**该退**；系统提示拿不到 ⇒ 回退到构建期那段不含工具/工作目录/记忆的字符串，是**换了个 Agent**，**必须炸**。
  - ✅ `RuntimeContextCompactor.compact` 失败保留原历史 + `context_compaction_total{status="FAILED"}`（`apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py:111-121`）
  - ❌ 拿"能跑"当借口把缺能力的那份兜底喂给模型 —— 静默换 Agent 比直接失败危险得多

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
