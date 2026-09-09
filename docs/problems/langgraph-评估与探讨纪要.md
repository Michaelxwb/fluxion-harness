# LangGraph 评估与探讨纪要

- 日期：2026-09-08
- 性质：技术探讨记录，非 ADR（未做架构决策）；若后续立项，先立 ADR 再动代码。
- 背景：团队评估“自研执行语义 vs 引入 LangGraph 框架”的取舍，以及相关外围问题
  （控制台、SOP、网关、版本、A2A）。

## 结论先行

1. 当前项目**未使用** LangGraph/LangChain（依赖、代码、文档零引用），执行语义自研。
2. 对**现在的 Fluxion**：不引入，弊大于利；真要吃红利，用“挂接”而不用“替换”。
3. 对**最初阶段（假设重来）**：先用框架跑 MVP，大概率更好——先快后稳。
4. 框架**能做到**多租户/审计/快照/无状态，但姿势别扭；最难迁的授权、审计、租户层，
   框架本来也提供不了，正好都不用换。
5. 控制台、SOP 编排、IM 网关、能力注册表该自己写的，一个都省不掉——框架只省
   “跑起来”的部分，不省“管起来”的部分。

## 1. 现状：未使用 LangGraph

- `pyproject.toml` / `uv.lock` / 全仓代码 / 文档：零引用。
- 工作流侧走自研 DSL（`WorkflowDefinition` + Designer + 校验 + 发布链），
  执行由自家 Runtime（`backend/src/fluxion/runtime/` + `services/` 编排）驱动。

## 2. 为何不把执行语义绑到框架（现在）

1. **Microkernel + Plugin**（架构基线）：Kernel 只依赖自家 Contract。执行语义外包
   等于把最核心的一层交给第三方，违背“一切可插拔”。
2. **无状态 Runtime + ExecutionSnapshot**：有状态图执行模型与快照语义不对同构，
   且 durable state 不得进入 Agent Runtime（基线）。
3. **第一公民要求框架不原生提供**：租户全链路强制、Secret 约束、独立审计——
   用了框架也要在外面再包一层，收益不大。
4. **可控性与 DFX**：超时/重试/熔断、P95/P99 开销基线、PostgreSQL 单库契约测试，
   自研每个环节可量化；引框架等于引黑盒。
5. 分层观：基础设施层用框架（FastAPI/SQLAlchemy/Pydantic/Semi），
   **执行语义层自研**——被锁定的恰恰是最核心的一层，不值得。

## 3. 第一公民要求：框架能否做到

| 要求 | 结论 | 说明 |
|---|---|---|
| 多租户强制 | 能，但靠自律 | `thread_id` + 自定义 checkpointer + 鉴权中间件可做；但框架默认单租户无感，隔离靠“每次都记得”，自研是靠机制保证 |
| 审计独立事实源 | 基本帮不上 | 回调/tracing 是执行附带日志；独立、可查、独立保留策略的 AuditLog 本来就要自建 |
| ExecutionSnapshot | 最接近，甚至更多 | checkpoint + time travel 本质就是快照；但那是有状态图执行的快照，与“无状态 + 版本冻结”语义不完全同构，版本冻结仍需自包解析层 |
| 无状态 | 能，标准姿势 | 无状态进程 + 外部 checkpointer（Postgres/Redis）是官方推荐部署；checkpointer/Store 可自实现直连 Registry，事实源仍归自己 |

坑（工程问题，非能不能的问题）：

- 本地 dev 顺手用内存 checkpointer 的“有状态幻觉”；thread 寻址与租户维度需映射层。
- checkpoint schema 归框架所有，升级可能要迁移数据。
- LangSmith 默认外发 traces，自 hosted 环境必须关/自建，否则撞 Secret 约束。

升级与 checkpoint 的补充约定（成立）：功能可不追新，安全补丁必须跟；
调用收敛在 Adapter 后则升级可控；checkpoint 迁移脚本自己写，但要附带回归
（快照一致性要求最高），本质是“适配层 + 迁移预案”两份长期责任。

## 4. 若替换为 LangGraph：工作量与架构影响

- 粗估 **2~4 人月**（小团队）：执行内核重写 ~40%（版本冻结解析最难）、
  流式与通道链路 ~20%（错误契约重写）、测试迁移 ~20%（最易低估）、
  可观测与审计 ~10%、ADR 与文档 ~10%。
- **绞杀者模式**（推荐）：不动 Contract，LangGraph 藏在新 Executor Adapter 后，
  与现有 Runtime 并存灰度，影响可控，可回滚；代价是过渡期双路径维护。
- **重写模式**：废掉 Microkernel 基线，DFX 基线与契约测试全部重验，约等于一次大版本重构。
- 建议：不“换”而“挂”——主流程保留快照 + Runtime，只把复杂编排（分支/循环/
  human-in-the-loop/中断恢复）挂成子图执行器，2~3 周可出 spike 验证。

## 5. 授权模型：用户 × Agent 维度

- **Agent 维度原生支持**：不同图/分支 `bind_tools` 不同 tool 列表即可；
  MCP 拉进来的 tools 同等对待。
- **用户维度框架不管**：无“用户×工具”权限矩阵。业界做法：调用前按用户授权
  动态过滤 tool 列表（推荐），或中间件在执行点 fail-closed。
- 结论：现有 Policy + Binding 层**原样保留**，继续坐在调用前面；
  LangGraph 只消费过滤后的 tool 列表。最难迁的层恰好不用迁。

## 6. 基于 LangGraph 自写控制台：可行

控制台与执行框架解耦，UI 工作量基本不变；省力的只是后端一部分：

| 现有功能 | LangGraph 对应物 | 说明 |
|---|---|---|
| 智能体 CRUD + 版本 | Assistants API | 原生自带 |
| 执行记录 | Threads + Runs API | 含回放 |
| Web Chat 流式 | `astream_events` | 事件齐全 |
| 运行态小旋钮 | `configurable` | 天然对应 |
| 工作流执行 | 图本身 | 不用再写 DSL 执行器 |
| 能力注册表/模型三层链/授权/审计/用户 360/评测 | 无 | 照样自建；评测接 LangSmith 则与数据不出境冲突 |

注意：Studio 是开发者调试器（无租户/RBAC/审计，会泄露内部 state），
不能作为生产控制台；生产 Console 必须自写。若只用开源 `langgraph` 库
（不用 Platform），状态全落自己 Postgres，控制台架构与现在同构。

## 7. 业务接口独立与 SOP（Console 配置即发布）

定稿分层（与框架无关，换执行核也不动）：

```text
业务接口层（业务方拥有：HTTP/MCP）
  → 注册/适配（Console 能力管理，版本化；凭据走 SecretRef；超时/重试/melt 必填）
能力注册表（Tool/Skill/MCP：中立层，不属于任何 Agent；第三方缺 MCP 时由 plugins/ 补翻译层）
  → 引用（Agent 配 capability_ref；SOP 按步骤编排，参数映射 + 发布校验 + 版本冻结）
Agent / SOP（只消费能力；写接口默认先“需确认”再放开）
```

- 业务接口版本：能力版本 pin 死业务接口版本，大版本升级走新能力版本，老 SOP 不受影响。
- 用户上下文（tenant/user）调用链全程透传，作为适配层强制字段。
- SOP 落地顺序：OpenAPI 一键导入成 Tool → 线性 SOP（顺序+参数映射+发布）→
  分支/人工确认/补偿。
- LangGraph 实现路径同样成立：`@tool` / MCP client 接入；SOP 存 DB，
  执行时按版本动态 `build` 图（`add_node`/`add_conditional_edges`，通用超集 State，
  `interrupt` 做人工确认）。动态拼图会丢掉 Studio 可视化与静态检查红利，
  发布校验仍需自写——和自研 DSL 同一笔账。

## 8. 相关结论（网关 / MCP 位置 / 版本 / A2A）

- **IM 消息网关必须自写**：入站解析+验签、归一化、路由、出站发送、审计，
  框架一件都不给。现有 `channel_auth.py`（wecom/mattermost）是鉴权芯；
  一个渠道一个 Adapter；`channel.py` 的 S2 残留信任口随网关落成一并收掉；
  建议先啃企业微信全链路。
- **MCP Server 位置**：谁拥有业务，谁拥有它的 Server（跟业务仓走，
  streamable HTTP）；harness 只做注册/鉴权/调用（现有 `runtime/mcp.py` 客户端
  不动）；第三方 SaaS 无人写时，才由 `plugins/providers/` 补翻译层。
- **版本概念的去留**：砍“发布仪式”不砍“版本数据”。建议分级——严格版
  （Agent/SOP/授权规则/RuntimeProfile，保留 draft→publish）、轻量版
  （模型/能力/渠道，保存即新版本、历史可回滚）、免版本（用户/绑定/审计流水）；
  ExecutionSnapshot 版本冻结是运行时底线，不动。动基线第 5 条需另立 ADR。
- **A2A**：LangGraph 双向支持（`a2a-sdk` 包 Server；远端 Agent 当 tool/node 调）。
  协议尚在演进，SDK 版本 pin 死；“谁能调谁”仍归自家 Policy；与现有
  `protocols/a2a.py` 之间加薄翻译层即可，无需为此换架构。

## 9. 待决策事项

1. 版本分级简化：另立 ADR（动基线第 5 条），明确各模块档位与验收标准。
2. LangGraph spike（可选）：版本冻结 + 动态拼图 + 用户级 tool 过滤，
   跑通一条 SOP，对比现有路径成本，再定去留。
3. IM 网关与 OpenAPI→Tool 导入可并行排期，互不阻塞。
