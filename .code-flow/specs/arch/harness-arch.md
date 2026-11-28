---
{
  "description": "部署单元、边界与容量",
  "next_rule": 10
}
---

# harness-arch

## Rules

- [RULE-arch-001] 固定四个**逻辑业务部署单元**（console-platform / agent-runtime / agent-worker / im-gateway）；`im-gateway` 可拆为无状态 web 与有状态 connector 两个运行时角色，connector 不是第五个业务微服务。Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行；Connector 只持有 bot 传输所有权与回复上下文，不建立 agent→Pod 映射。

- [RULE-001] `packages/*` 不依赖任何 app 模块；runtime/worker 不得 import `muad_console_platform`，跨部署单元只走 HTTP 契约；`skill-sdk` 不依赖 runtime/console/worker/connector。机检 `tests/architecture/test_import_direction.py`。

- [RULE-002] IM Gateway web 角色不持库（不得 import `sqlalchemy`）；渠道 SDK 只允许出现在 Connector 的 `channels/` 适配器；`iter_events()` 是 IM 入站规范化唯一入口，Connector 只发 `im.inbound`/`im.outbound`，不直接调 Runtime。机检 `tests/architecture/test_im_gateway_boundaries.py`。

- [RULE-003] 单次工具失败应作为结构化事实交给正常 Agent 回合。不可重试约束原操作重做，能否继续由剩余权限、预算、期限、副作用门禁和进展决定。执行进入硬停止或模型不可用时，由宿主依据已持久化事实确定性收尾，不补发模型调用。正文说明已完成范围、真实障碍和可行下一步，保留可交付结果。技术码、Run ID 与脱敏诊断只进技术详情。本条覆盖 Conventions 中确定性失败第一次即停和追加模型收尾的旧口径。

- [RULE-004] 超出平台出站准入窗口的请求必须在外发前拦截并审计。对被判定为可等待且可安全重试的工具调用，在冻结 Run 预算、截止时间、取消和租约约束内按 Retry-After 或退避等待，沿用同一调用身份重试；不满足门禁时不得重发。聊天 Skill 仅因出站窗口限流结束时，保留已有正文和经核实可交付的部分结果，在回复末尾追加受限说明并停止派发后续工具；独立故障按其真实原因收尾。

- [RULE-005] 工具失败控制必须区分 Agent 是否继续、原调用是否允许重试、是否需要等待、失败作用域以及是否必须由宿主硬停止。retryable=false 禁止原调用重做，不单独推出 Run 终止；身份或能力完整性受损、关键事实不可持久化、模型不可用、执行权丢失、截止时间到期、总预算耗尽或停止未确认时，由宿主安全收尾。

- [RULE-006] 外部工具的准备、派发与结果须由受信执行事实分别判定。明确不可重试的已知失败约束原操作，未知副作用只约束可能重复该操作的调用。独立的获授权操作仍可继续。自动重试与正常派发使用同一门禁。普通工具或技能作用域的成功须清除该作用域的连续失败计数。

- [RULE-007] 工具结果在 Kernel 统一为 typed ToolResult/ToolError：成功正文是数据，失败由 outcome/异常表达；pipeline 不得解析正文 JSON 判断成败；outcome_parser、follow_up_messages 与 tool_failure_outcome 的正文推断删除。

- [RULE-008] 上下文压缩边界统一由 ContextPlan 计算：显式当前 Run 输入/来源/活跃 Skill；pinned 不受压缩；older 可摘要；working 可 tail 压缩；受保护预算按 UTF-8 字节且超限可见。

- [RULE-009] 开发阶段 cutover 是破坏性切换：暂停调度生产/停止 claim/收口 RUNNING/处置 WAITING_* 后删除旧 canonical_event/task_event 或重置开发库；不迁移、不保留 v1 分支；cutover 后只向前修复，回滚需重置开发库。

## Conventions
- **限流与失败收尾**（RULE-004、RULE-005）
  - ✅ 可安全重试的短时限流在剩余预算内沿用原调用身份；仅因出站窗口限流结束时，保留已有正文并补充提示。普通不可重试的工具失败仍可交给 Agent 选择其他安全路径。
  - ❌ 因限流放行超限请求、重发结果未知的写入，或用限流提示覆盖独立故障。硬停止后不得继续派发工具。


- **容量压测的副本基线由 profile 声明**：Console 固定 1 Pod；Gateway/Connector 的副本按目标 profile 的 `max_replica_counts`/`process_counts` 核算；Runtime/Worker 仍可横向扩展。✅ 每轮压测前执行 `check_agent_capacity_deployment.py --live-namespace`，核对期望/实际/就绪副本与 HPA，缩容结束再施压；❌ 清单写 1 却沿用多副本，或把非目标拓扑的完成率当目标容量。
  - **单 Pod 出站并发同样按目标并发定容**：Gateway 的 `EGRESS_HTTP_MAX_CONNECTIONS × max_replicas` ≥ `workload.users × active_runs_per_user`——每个用户建会话/提交消息各占一条 Gateway→Runtime 连接，池不够时请求排队到 `PoolTimeout`（实测池不足会批量 `PoolTimeout`）。发布预检机检 `outbound-concurrency`。
- **受理阈值以 Console 设置为唯一来源，Runtime 只做单调投影**（2026-10-27）：`capacity` 分组在 Console 配置；`runtime.run_admission_policy` 是 Runtime 侧的 PG 阈值投影，按 `source_settings_revision` **单调 upsert**（只升不降），事务内按 USER→TENANT 顺序锁行计数，达到有效阈值即拒且不新增占位。配置快照在短事务外取；读到更低版本要退出事务后重取一次，仍低即显式失败——绝不按旧阈值放行、不回落 schema 默认值。降额只影响新受理，已有 Run 与计数行不动；新 Run 的初始排队态是 `WAITING_RUNTIME`（`application/run_admission.py`、`infrastructure/run_admission_store.py`；受理指标；材料由 Console `/internal/runtime/admission-materials` 提供）。✅ 同一 PG 行跨实例计数；❌ 每 Pod 本地缓存独立判定。

本 Spec 的 verifier 跑 `tests/architecture`，该套件实际强制的规则族比 RULE-arch-001 更宽，以下三条同为硬约束：

- **依赖方向单向**：`packages/*` 不得 import app 模块（`tests/architecture/test_import_direction.py` 的 `APP_MODULES` 覆盖 runtime/worker/gateway/connector/console 五个 app 包）；runtime/worker 不得 import `muad_console_platform`（跨部署单元一律走 HTTP 契约，参考 `infrastructure/console_client.py`）；`skill-sdk` 不依赖 runtime/console/worker/connector。
- **IM Gateway / Connector 边界**：Gateway web 角色不持库（不得 import `sqlalchemy`，迁移与持库归其他 Owner）；渠道 SDK（`aibot`/`websockets`）**只允许出现在 Connector 的 `channels/` 适配器表面**——Gateway 里任何位置出现渠道 SDK 都红。`iter_events()` 是 IM 入站规范化的唯一入口（WebChat 的 SYNC 路径例外，见 `harness-im.md`）；Connector 用 `im.inbound` 规范化事件与 `im.outbound` 命令，不直接调 Runtime（`tests/architecture/test_im_gateway_boundaries.py`）。
- **指标暴露与 label 卫生**：每个运行时角色（四个业务单元 + Connector）声明自己的 metric catalog 并通过 api-kit 暴露真实 `GET /metrics`（无流量时 catalog 亦可见）；label 不得含高基数或敏感维度（禁止资源 UUID/Secret/消息体/用户可控路径，路由 label 取模板而非具体路径）；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志；回收指标归 Runtime（Worker 无 Run 回收路径，对应指标见各自 catalog）。每个运行时角色都声明 catalog 后再 `install_metrics`：Runtime/Worker 见各自 `metrics.py` 的 `CATALOG`；IM Gateway/Connector 各自见 `metrics.py`——机检 `tests/gateway/test_gateway_metrics.py`（无流量也暴露目录 + label 卫生）。

- **探针口径与预算**：`/healthz` 是存活、`/readyz` 是就绪（依赖缺失 503）。每个运行时角色（含 Connector）的 k8s 清单必须 `readinessProbe` 接 `/readyz`、`livenessProbe` 接 `/healthz`，并显式声明有界预算；发布预检拒绝缺省或低于预算的配置。✅ 允许短时负载下健康请求延迟；❌ 省略超时沿用 K8s 默认值——实测活跃 Runtime 被探针重启、同秒网关大量断连。**服务间接线同理**：`CONSOLE_PLATFORM_URL`/`AGENT_RUNTIME_URL`/`AGENT_WORKER_URL`/`IM_GATEWAY_URL`/`DEFAULT_TENANT_ID` 必须由 ConfigMap 显式给（缺了会回落到本机回环地址），`INTERNAL_SERVICE_TOKEN` 所有角色一致；迁移要有集群内入口（Job 用 Console 镜像，`alembic` 因此进该包依赖）。机检：`tests/architecture/test_k8s_deploy_manifests.py`、`test_capacity_webchat_deployment_preflight.py`。

- **后端内部分层固定为 `api/ → application/ → infrastructure/`**（`domain/` 为纯领域）：路由只做协议适配与依赖注入，业务编排在 `application/*_service.py`，数据访问在 `infrastructure/repositories/*_repository.py`（**该收口仅 console 后端成立**：全仓只有 `apps/console-platform/backend/.../infrastructure/repositories/` 与 `.../domain/`；runtime/worker/gateway 无这两个包，其数据访问落在 `infrastructure/*.py` 与 `application/*_service.py`）。**不存在 `modules/<模块>/` 子包**——那是前端的约定（`apps/**/frontend/src/modules/`）；设计模板若按前端形态写后端目录，实现按本分层落地并在任务文档登记「设计待更正」。

- **应用层可以按功能划分子包**：Runtime 附件与制品逻辑集中在 `application/attachments/`（`tools.py`、`inbound.py`、`reference.py`、`tool_results.py`、`archive_tools.py`、`output_service.py`、`transcripts.py`、`immutable_store.py`）；功能子包仍属于 application 层，数据库模型与存储实现归 infrastructure，执行器负责组装。✅ `application/attachments/tools.py` 引用 `infrastructure.models.runtime.Artifact`；❌ 在功能子包中复制数据库模型，或反向引用执行器和 Run 编排服务导致循环依赖。

- **`py.typed` 只在 `packages/*`，五个 app 包里只有 Connector 例外**：9 个共享库（含 `muad_im_core`）都带 `py.typed`；四个既有 app 包（console/runtime/worker/gateway）不带，`muad_im_connector` **带**。后果：mypy 分析 **test 文件**时会把不带标记的 app 包当「已安装但未类型化」的库，报 `import-untyped`；**这是口径问题而非代码缺陷**，未改动目录同样复现。判定口径：`make typecheck` 只跑 `apps packages`，不含 tests；对 test 文件跑 mypy 时的失败按此折算，不要据此改实现。若要 test 文件也受严格检查，需先给四个 app 包补 `py.typed`（另立变更并评估新错误）。

- **Pod 绑定门禁是「文本标记扫描」而非语义分析**：`POD_MARKERS = ("pod_id", "agent_pod", "pod_mapping", "agent_to_pod")`，每个运行时角色（含 Connector）的任一 `*.py` 源码文本命中任一标记即红（`tests/architecture/test_im_gateway_boundaries.py` 的 `POD_MARKERS` 与强制用例）。因此该门禁只约束**命名**——✅ 用 `lease_owner`/`instance_id` 表达执行者归属；❌ 变量名写成 `pod_mapping` 之类，即便语义上并未做 Pod 绑定也会在运行时角色内触发红。

- **Console 部署单元是 `apps/console-platform/backend`，不是整个 `apps/console-platform`**：`CONSOLE = "apps/console-platform/backend"`，它与 Gateway/Runtime/Worker 同列 `DEPLOYMENT_UNITS`（入口断言在 `tests/architecture/test_im_gateway_boundaries.py`）。前端 `apps/console-platform/frontend` 不在部署单元的可检查范围里；新增跨单元边界检查时不要把整个 `console-platform` 目录当作 Console 单元。

- **压缩的「受保护前缀」是开头连续的一段 `role=SYSTEM`**：系统提示、memory 注入、摘要前缀（`summary_message` 渲染出来也是 SYSTEM）都在这一段里，它们是每次请求必须原样带上的**权威上下文**，不是历史。所以任何**会删消息**的压缩层（请求边界 `fit_history`）只能在其后的**对话区**上工作；只替换材料、不删前缀的摘要层不涉及这条边界。裁掉前缀＝agent 失忆（系统提示定义了身份与指令）、memory 注入措辞失效、被压缩掉的那段历史净消失。
  - ✅ `prefix, region = split_protected_prefix(sanitized)`，`fit_history` 只切 `region`，并始终保护头部锚点、pinned 与最新完整工具回合；`with_summary` 只移除材料组。Runtime `memory_context.py` 从当前Run的revision缓存刷新typed `MemoryComponent`，`AgentRunner._prepare_model_request` 在每次发送（含重试）前把该组件插在固定提示后、摘要前，再执行压缩；`context_builder.load_history` 只重建canonical与摘要，不预先查记忆。记忆的刷新/失效与来源口径归 RULE-memory-003（`harness-memory.md`），本 spec 不重复定义。
  - ❌ 对整条消息列表（含开头 SYSTEM）直接裁剪 / 按条数或回合数截断 ⇒ 系统提示或摘要前缀被当历史省掉

- **系统提示的 section 顺序是契约**（2026-10-07 借鉴 P10 的动态提示设计）：装配出来的提示由**具名段落按固定顺序**组成，唯一实现在 `DefaultPromptBuilder.build`（`packages/agent-core/src/muad_agent_core/prompt/builder.py`）：`instructions` → `## Available skills`（目录）→ `<!-- prompt_template_version -->`（尾标）。加段落**只在尾部追加**，不得插队——顺序一旦只是 `append` 的副产物，任何重构都能悄悄挪动它。用例断言**相对位置**（`prompt.index(A) < prompt.index(B)`），不要断言「包含某段文本」：后者在顺序错乱、甚至同一段出现两次时同样通过。
  - ✅ `prompt.index("be helpful") < prompt.index("## Available skills") < prompt.index("<!-- prompt_template_version")`（`tests/agent_core/test_prompt_builder.py`）
  - ❌ `assert "## Available skills" in prompt` —— 段落被挪到 instructions 之前也照样绿

- **段落为空怎么办：看模型的行为该不该变**（同上，2026-10-07）。核心能力缺失 ⇒ **显式说明**（`tools` 为空要写 `(none)`，否则模型会去调一个不存在的工具）；补充信息缺失 ⇒ **整段省略**，不留占位（Skill 目录、记忆为空时什么都没发生，写「当前没有可用技能」只是噪音，还稀释注意力）。**同一判据也管「段落被截断」**：目录超出预算时模型会以为看到的就是全部，属于行为会变的缺失 ⇒ 必须在段尾说明还有技能未列出、用 `search_skills` 找（`CATALOG_TRUNCATED_NOTE`，**只在真的丢了条目时出现**）。
  - ✅ `if skills:` 才追加 Skill 段（`builder.py`）；`test_empty_skill_catalog_is_omitted` 钉住省略；`test_truncation_is_announced_only_when_something_was_dropped` 钉住「被截断才说明」
  - ❌ 空段落也输出「（无）」「暂无」之类占位；❌ 截断了却不说（模型按不完整的清单行事）

- **自由形态的 JSON 载荷必须是严格 JSON，且**只在一处**判它**（2026-10-07）：`input` / `execution_snapshot` / `input_template` 这类 `dict[str, Any]` 走不到类型检查，必须在**入参**（契约 DTO 的 `field_validator`，见 `muad_contracts.canonical.ensure_strict_json`）就拒掉非 JSON 值。「同一份载荷在幂等指纹与 jsonb 落库两个消费者眼里必须是同一份」的完整口径、`NaN`→`None` 反例与逐层防护见 `harness-api.md`（RULE-api-002 段），本 spec 不重复展开。
  - ✅ 非有限数/未知类型在 DTO 层 422（`tests/agent_worker/test_api.py::test_non_standard_json_payload_is_rejected_at_the_boundary`）；`instructions` 先 `strip()` 再判空（`min_length=1` 挡不住 `"   "`，而装配侧会 strip ⇒ 保存合法、发出空提示）
  - ❌ 让 `NaN` 一路穿过 DTO 与指纹、直到 `INSERT ... ::jsonb` 才被 PostgreSQL 拒（`invalid input syntax for type json`）⇒ 调用方的错报成 500

- **降级必须**语义等价**，否则不许降级**（2026-10-07）：判断"能不能退"的判据不是"退之后还跑得动吗"，而是"退之后还是不是同一个 Agent"。压缩失败 ⇒ 原文照发（只是没省 token，同一个 Agent）**该退**；系统提示拿不到 ⇒ 回退到构建期那段不含工具/工作目录/记忆的字符串，是**换了个 Agent**，**必须炸**。
  - ✅ `RuntimeContextCompactor.compact` 失败保留原历史 + 压缩失败指标（`apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py` 的 `compact` 异常分支）
  - ❌ 拿"能跑"当借口把缺能力的那份兜底喂给模型 —— 静默换 Agent 比直接失败危险得多

- **Runtime 执行归属只由 PG claim 产生，HTTP 只订阅持久事件**（async-tool-runtime，2026-10-09）：`POST /v1/runs` 的创建事务只落 `RunRecord` + `RUN_CREATED` canonical；执行由 `ExecutionSupervisor.claim_and_submit`（关闭锁内 claim、排除本实例在跑 run）领取 `CREATED`，`WAITING_TOOL` 由 `ContinuationPump` 经 `claim_continuation`（conversation 行 `FOR UPDATE SKIP LOCKED` + Run 锁内 epoch CAS）领取；心跳、事件与终态写入统一校验 owner/epoch/lease。
  - ✅ 锁内预留本地槽、锁外 await claim；`free_slots` 扣除正在领取的槽；`finally` 释放预留且异常/取消原样传播。❌ 在 `finally` 中 `return False` 吞掉领取异常或 `CancelledError`，使启动失败不可见、关闭取消失效。
  - ✅ 断流不等于取消：SSE 断开后 Run 继续执行，重连按最后确认 seq 回放；claim 失败不改业务状态，只有 Run/continuation/lease 同事务切到新 owner+epoch 才算抢占成功
  - ❌ 把 Runner 生命周期绑在 HTTP 请求/SSE generator 上（连接断开=业务取消），或在请求内同步跑完整 Run（`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`、`async_tools/continuation_service.py`；机检：`tests/agent_runtime/test_tool_wait_state_machine.py`）
- **失败跟进**（RULE-003）：正常回合读取结构化失败并在原权限和预算内决定下一步；同类失败缺乏进展达到策略上限后，由宿主根据已保存的事实收尾。✅ 认证被拒后解释阻塞或改走获授权的只读查询；❌ 因首次失败提前结束、用无关工具结果清除失败计数，或在硬停止后额外调用模型。
- **工具并发是显式声明，默认串行**（async-tool-runtime，2026-10-09）：`ToolDefinition.concurrency`（`SERIAL` 默认 / `PARALLEL_READ`）+ `resource_key` 不可由模型设置；规划器按原调用序切**连续**批，资源冲突/未知依赖拆串行，`WRITE`/`EXTERNAL` 是前后屏障，批大小 ≤ `parallel_limit`（1–16，bool/小数/越界显式拒绝）。第一版只登记已证明只读的工具（`search_skills`/`read_skill_resource`/隔离 `get_task`/`list_tasks`）；`load_skill`、memory 与全部 MCP 默认 SERIAL。
  - ✅ 先按原序校验/授权/预留预算，再只并发 handler IO，结果按原序收口；❌ 按 `ToolEffect.READ` 或 MCP `readOnlyHint` 自动放行并行（对端提示不是授权依据）
  - 机检：`tests/agent_runtime/test_tool_parallel_planner.py`（`packages/agent-core/src/muad_agent_core/tools/planner.py`、`tools/registry.py`）

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。

## Applied Deltas

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-001","op":"add","rule":"RULE-001","payload_sha256":"6ef0723d530ab01930dfc045910e4d3bae8ab77e860ad175cb5513682a8710b0","at":"2026-11-18T14:06:44.260Z"}

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-002","op":"add","rule":"RULE-002","payload_sha256":"a496ba6a6dd49ef5a92bf62b41ff1de102aaba69a75aacee262b83a772d3b8cc","at":"2026-11-18T14:06:44.261Z"}

- {"source":"2026-11-21/agent-output-and-platform-defaults/agent-output-and-platform-defaults.md#TASK-007#DELTA-002","op":"add","rule":"RULE-003","payload_sha256":"07379992368498a66463980b88dbc3014e3215ebfcdc0117cc04cb7dbb84fa45","at":"2026-11-21T07:54:54.594Z"}

- {"source":"2026-11-23/chat-egress-rate-limit-notice/chat-egress-rate-limit-notice.md#TASK-002#DELTA-001","op":"add","rule":"RULE-004","payload_sha256":"517fc9693939f052dffaf5e73df2f16bb8678618d52df1cd6701781815a29b1d","at":"2026-11-23T07:47:20.774Z"}

- {"source":"2026-11-24/skill-results-conversation-finalization/skill-results-conversation-finalization.md#TASK-006#DELTA-001","op":"update","rule":"RULE-003","payload_sha256":"541b70dac6d63b110935e01f80b0085f93bfdf6a21732346eb3f13876b3f6114","at":"2026-11-24T09:27:16.307Z"}

- {"source":"2026-11-26/agent-tool-failure-recovery/agent-tool-failure-recovery.md#TASK-001#DELTA-001","op":"add","rule":"RULE-005","payload_sha256":"5153ae36588bb3cdd7f3e18b6ab21864b6a2f3d5dffbce79ce4cbc1a1e687bf3","at":"2026-11-26T12:11:20.729Z"}

- {"source":"2026-11-26/agent-tool-failure-recovery/agent-tool-failure-recovery.md#TASK-002#DELTA-003","op":"update","rule":"RULE-004","payload_sha256":"1533befa31f721cba4f620472f6f727a0b13b1b4bef0ae131e81fd69521f8644","at":"2026-11-26T12:11:20.731Z"}

- {"source":"2026-11-27/agent-tool-failure-chain/agent-tool-failure-chain.md#TASK-001#DELTA-001","op":"add","rule":"RULE-006","payload_sha256":"fa90050a78e3fc158e1ff12bfd941dda3f51ba21fcf5ae5e3464a77f3f0009f3","at":"2026-11-28T00:53:13.869Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-001#DELTA-001","op":"add","rule":"RULE-007","payload_sha256":"bed7e67913ba83b0eb133f1b265ded98c5f37e264e34fa00d73d5573762718d8","at":"2026-11-28T01:04:28.946Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-005#DELTA-001","op":"add","rule":"RULE-008","payload_sha256":"ba2b1176bfc8cfb66bbede78958b70de3f5042fc5fea6036322ed94fc6472e1a","at":"2026-11-28T01:04:28.947Z"}

- {"source":"2026-11-21/agent-execution-contracts/agent-execution-contracts.md#TASK-013#DELTA-001","op":"add","rule":"RULE-009","payload_sha256":"be9661699a4605ee8ccd30055ead95110e9d048787992a63a1fb6c6edbdd25dd","at":"2026-11-28T01:04:28.949Z"}
