# 记忆体系重构（Memory Redesign）设计与需求一体化文档

- **需求目录**: `.code-flow/tasks/2026-10-12/memory-redesign/`
- **模板**: Full（跨 agent-core / agent-runtime / console-platform / migrations / 规范面）
- **PRD**: 无（本需求由既有实现的缺陷分析直接引出，用户故事内联给出）

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 产品经理 | 用户 | 需求定义、业务验收 |
| 开发负责人 | Claude | 技术方案、代码实现 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v0.1 | 2026-10-12 | Claude | 初始设计：三层记忆职责重划、长期记忆读写语义重构、短→长通道、遗忘与配额、规范面同步 |
| v0.2 | 2026-10-12 | Claude | 按外部复审 13 项的首轮修订（**已被 v0.3 取代**：词法取证、两阶段发布、UNION 候选、内联 consolidation、按比例预算均已改） |
| v0.3 | 2026-10-12 | Codex | 根据设计与现有代码对照评审**重写**（本行原误标为 v0.2，与 Claude 的 v0.2 重号，v0.4 期改正）：来源与确认分离、稳定身份及事务历史、硬配额、完整候选检索、遗忘屏障、持久化 consolidation、维护窗口切换与真实边界验收 |
| v0.4 | 2026-10-12 | Claude | 在 v0.3 之上补 4 处，**未改动 v0.3 的任何决策**：§3.3.6 三条硬约束索引 DDL（待确认提议唯一 / options 单次消费 / 过期清扫）；§3.3.7 既有表可复用字段（`run_interrupt` 已有 `options_json`/`resolution_json`/`expires_at`，只需加 `proposal_json`）；§3.4.2 Gateway `/memory` 命令前缀触点；§3.5.1 按 scope revision 缓存候选行的边界与禁用优化 |
| v0.5 | 2026-10-12 | Codex | 保留v0.4缓存方案并补SQL快照一致性/TTL/失败边界；修正options跨outcome唯一约束并复用既有source_event_id；澄清Gateway忙态；补B-23与任务/Spec责任映射 |

### 1.3 修订依据与文档状态

本文件是设计稿，不代表实现或代码验收已通过。Spec Context、bindings与阶段裁决以需求目录 spec-context.yml 及 CLI 实际返回为准；当前未激活编码 TASK。Plan 承接全部现存 required Rule；首个任务创建新增 memory 规范后增量绑定并复核 Design/Plan Gate，之后才进入业务实现。

v0.1/v0.2 的主要问题是先选局部方案，再把性质写成承诺：词法重合不能证明用户确认，同事务不能替代并发控制，增加历史表不能自动保证版本顺序，存在性查询不能保证幂等。v0.3 先定义不变量，再给事务、状态机、消费路径和可使其失败的验收，不再使用“无锁 / 两条 SQL / 永不拒绝”作为先验约束。v0.4 只做缺口补齐，不放松上述任一条。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| **模块名称** | 记忆体系（Memory Architecture） |
| **模块ID** | MOD-MEMORY |
| **所属系统/产品线** | Agent Harness（agent-core / agent-runtime / console-platform） |
| **需求类型** | 架构重构（不是功能叠加：替换长期记忆读写语义、打通短→长通道、补遗忘与配额） |
| **业务背景** | 现行实现已隐含"短期=上下文管理、长期=user_memory KV"两层，但两层都没有按"记忆系统"设计：读侧是把命名空间查找强加给模型，两层之间没有通道，没有任何东西会被遗忘 |
| **核心目标** | 三层职责明确；长期记忆由唯一写入服务和统一候选查询管理，按消费场景选择；来源可证明、变更可回放、遗忘不会被自动写入撤销、容量有硬上限 |

### 2.2 痛点与价值

现状问题清单（编号 P-nn，全部可复现；"现状证据"列给到代码位置）：

| 编号 | 层 | 问题 | 现状证据 | 后果 |
|---|---|---|---|---|
| P-06 | T2 | `recall` 只有 `memory_key` 前缀匹配 | `memory_service.py:175-176` | 检索天花板极低，模型必须猜对 key 才召回得了 |
| P-07 | T2 | 自动注入按 `update_time DESC` 取最新 N 条 | `memory_service.py:151-153` | 老而重要的记忆被新写的静默挤掉，且**被挤掉多少条无任何指标** |
| P-08 | T2 | 无过期/衰减；`enabled` 列在生产零写入点 | `memory_service.py:188`（仅测试调用 `test_memory_service.py:94`） | 记忆只增不减，注入位永久占用 |
| P-09 | T2 | upsert 就地覆盖 | `memory_service.py:75-89` | 旧值/旧来源不可审计、不可回滚，`version` 只是自增计数 |
| P-10 | T2 | `source_type` 由模型自报 | `memory_tools.py:319-326` | "只有 USER_EXPLICIT 自动注入"这条策略没有确定性闸门（RISK-05 长期挂着） |
| P-11 | T2 | `write_policy='CONTROLLED'` 只是标签 | `memory_service.py:71` | 禁写类目（密钥/实时权限/任务结果）只存在于 prompt 描述里，服务端零校验 |
| P-12 | T2 | `category` 不参与检索与注入过滤 | `memory_service.py:169-186` | 白建的维度，模型无法按类别召回 |
| P-13 | T2 | 运行时无遗忘路径 | `MemoryService.disable` 无生产调用方 | 用户说"忘掉它"只能找管理员；Console 之外删不掉 |
| P-14 | T1→T2 | 短期压掉的细节唯一留痕是模型当轮自愿 `remember` | `context_compaction.py:175-206` | 长会话里确立的事实，摘要一压就没，跨会话不可续接 |
| P-15 | T2 | 三条读路径语义不一致 | `list_entries:97`（按 key 排序）/`list_for_injection:113`（USER_EXPLICIT+最新）/`search:157`（前缀、两来源） | 模型看到的、用户看到的、审计看到的不是同一份事实 |
| P-16 | T2 | 索引缺租户维度 + ORM 未声明索引 | `0002_initial_schema.py:760-765` vs `runtime.py:206-209` | 注入查询命不中索引；`metadata.create_all` 出来的库 upsert 直接失败 |
| P-01 | T0 | 注入预算 = `min(2048, 0.2 × 装配历史字节)`，与有效性反相关 | `context_builder.py:393-404` | 第一轮/短消息只能进 1 条，历史越长反而越放宽 |
| P-02 | T0 | `MAX_HISTORY_TOOL_ROUNDS=6` 硬编码 | `context_builder.py:42` | 违反 RULE-snapshot-001（预算须冻结进 `policy_json`） |
| P-03 | T0 | 注释与实现不符 | `context_builder.py:376,397-401` | 注释称"历史为空⇒不注入"，实际首条保底会注入 1 条 |
| P-04 | T0 | 长会话压缩能力依赖显式配置 | `platform_settings.py:75,83` | micro / summary 默认关是现状约束；本需求不将开启压缩与长期记忆正确性绑定 |
| P-05 | T0 | 每条记忆重复一遍 40+ 字节措辞 | `context_builder.py:407-408` | 10 条记忆多花约 400 字节 boilerplate，且重复即噪音 |
| P-17 | T2 | v0.1 取证把"整轮命中记忆请求模式"当作来源证据 | 复审 R-01 | 用户说"记住我喜欢蓝色"，同轮**任意**候选都能升级为 USER 并自动注入 ⇒ 模型可借一句话把编造内容洗成用户偏好 |
| P-18 | T0 | 记忆注入在 `load_history` 播种进 `state["messages"]` | `runner.py:315-324` + `context_builder.py:139` | ① `forget` 之后本轮后续模型请求仍带着旧记忆块；② 本轮新写的记忆要到下个 Run 才注入；③ 记忆字节计入摘要阈值却永不被摘掉 |
| P-19 | T2 | 候选查询按"最近优先"截断 | v0.1 §3.5.1 | 40 条较新的无关记忆就能让唯一匹配的旧偏好进不了排序 ⇒ P-07 未真正解决 |
| P-20 | T1→T2 | consolidation 幂等只靠"先查事件再写" | 复审 R-07 | 两个调用可同时看到"事件不存在"而双写；记忆与事件分事务提交时崩溃窗口导致重写 |
| P-21 | T1→T2 | 未定义同 key 来源冲突策略 | 复审 R-09 | consolidation 沿现有 upsert 语义会把已有 USER 记忆的**内容与来源一起覆盖成 AGENT**，默认配置下该偏好随即停止注入 |
| P-22 | 发布 | `compaction` 解析遇未知键直接 raise | `platform_settings.py:314-316` | 删除 `memory.budget_ratio` 后，存量设置文档与旧快照的 `policy_json` 解析失败 ⇒ 新 Run 创建与 resume 全挂 |
| P-23 | 发布 | `source_type` 列 NOT NULL 且无 server_default | `0002_initial_schema.py:740` | 两阶段之间上线的新代码不写该列 ⇒ 首次 INSERT 直接失败 |
| P-24 | T2 | 配额检查与写入不同步 | 复审 R-10 | 两个 Pod 可同时占用最后一个额度；且"硬配额 + 只淘汰 AGENT + 用户要求永不拒绝"在满额USER场景无法同时成立 |

| 维度 | 内容 |
|---|---|
| **目标用户** | IM 终端用户（跨会话连续性）；平台管理员（治理）；审计人员（取证）；运维（容量） |
| **当前问题** | 上表 16 项：长期记忆读侧是命名空间查找、写侧无闸门、变更不留痕、永不遗忘；短期与长期之间没有通道；可调项与规范面各说各话 |
| **业务影响** | "它居然忘了"无法解释也无法恢复；用户偏好要么首句不生效、要么被后来写入悄悄挤掉；审计查不到一条记忆为什么变成现在这样 |
| **预期价值** | 记忆可按内容召回、按相关度注入、会过期、可遗忘、可回放；三层职责在规范里有唯一说法 |

| 编号 | 用户故事 | 优先级 |
|---|---|---|
| US-01 | 作为用户，我希望我的偏好在**下次会话第一句**就生效，不用先提醒它"你知道的" | P0 |
| US-02 | 作为用户，我能让 agent 忘掉某条记忆，也能看到它为什么被记、什么时候过期 | P0 |
| US-03 | 作为用户，昨天没做完的任务，今天新开会话能接着做 | P1 |
| US-04 | 作为管理员，记忆写入有硬配额与闸门；优先淘汰推断条目，仍不足时明确告知用户清理或申请提高配额，不静默超额或删除已确认事实 | P1 |
| US-05 | 作为审计人员，每次记忆取值变更都能回放：哪一轮、依据用户哪句话、旧值是什么 | P0 |
| US-06 | 作为运维，所有可调项都在平台设置里并随 Run 冻结；每一层退化都有指标 | P1 |
| US-07 | 作为平台负责人，三层记忆的职责与唯一实现点在规范里只有一处说法 | P0 |

### 2.3 功能方案

#### 2.3.1 必须成立的不变量

以下不变量优先于 SQL 条数和局部性能优化。不能以退化策略破坏它们。

| 编号 | 不变量 | 证明边界 |
|---|---|---|
| INV-01 | 模型不能授予 USER / ADMIN 来源或用户保存确认 | 确认绑定候选内容与版本；只有真实入站用户响应或受授权管理员操作能授予 |
| INV-02 | 同一用户的记忆修改串行提交，内容与历史原子一致 | memory_scope 行锁；完整 after 快照与活跃行同事务 |
| INV-03 | 同 key 的 identity 与 version 跨删除保持连续 | 完整唯一键；删除保留 tombstone；重新记住使用原 id |
| INV-04 | 容量增长不突破冻结额度，治理操作不增加既有超限 | 锁内计算写后集合；只升额度产生的既有集合不被旧 Run 回退；绝对条数/字节界始终成立 |
| INV-05 | 自动推断不能覆盖已确认事实、解除人工禁用或复活用户删除 | 区分用户删除与 TTL/配额退休；后两者也只接受可证明的新入站依据 |
| INV-06 | 内容检索先检查匹配，再排序；旧条目不因更新时间被预先排除 | 单次查询读取配额内完整候选；非空 query 零重合返回空 |
| INV-07 | 发送的完整记忆块严格满足条数与 UTF-8 字节上限 | 使用最终渲染结果计量；无首条越限保底 |
| INV-08 | consolidation 的持久写入效果最多一次，旧执行者不能提交 | job 唯一键、epoch/lease fence；效果、历史、完成事件、job 终态同事务 |
| INV-09 | 记忆功能失败不改变主 Run 终态；失败不能假装成功 | 主 Run 与后台 job 分离；warning + metric；事件可写时才写 |
| INV-10 | 删除配置或收紧容量不能使在途对象静默改用新政策 | 维护窗口排空旧策略对象；在线容量只提高；历史快照保留作审计，不重新执行 |

“最优”在本需求中指：满足上述不变量、符合当前部署与数据边界、复杂度与当前配额相称；不承诺零误判的自然语言语义推理。

#### 2.3.2 功能清单

| 功能ID | 功能名称 | 具体行为 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-01 | 三层职责收敛 | T0 为当前请求消息，T1 为会话事件与权威摘要，T2 为跨会话事实与任务线索；禁止把任务线索当执行状态或权限 | P0 | US-07 |
| FEAT-02 | 证据与确认 | remember 是候选提议；原话证据按当前 submission 绑定；复用 WAITING_INPUT / resume 确认完整候选，禁止模型自报 author / explicit_request | P0 | US-05 |
| FEAT-03 | 事务历史 | 稳定 identity、单调 version、完整 after 快照；存量迁移基线；所有修改由 Runtime 唯一写入服务执行 | P0 | US-05 |
| FEAT-04 | 完整候选与场景选择 | 读取完整有界集合；分别定义偏好、内容检索、任务续接；共用匹配和排序原语，不把不同产品行为强行等同 | P0 | US-01, US-03 |
| FEAT-05 | 单块注入 | 结构化 memory component 渲染成一条 SYSTEM 消息；完整字节硬限；长条跳过；丢条按原因计数 | P0 | US-01 |
| FEAT-06 | recall | query + category；非空 query 零命中返回空；空 query 为列表；返回来源、确认、版本及有效期 | P0 | US-01 |
| FEAT-07 | forget | 版本检查后软删、留历史和删除墓碑；递增 forget_epoch 逻辑撤销旧提议；当前 Run 的 memory component 失效并重建 | P0 | US-02 |
| FEAT-08 | 硬配额与内容策略 | 用户级锁内按写后集合计算；优先淘汰 AGENT；仍不足拒绝；USER/ADMIN 也不绕过硬限；候选与证据均禁存密钥 | P0 | US-04 |
| FEAT-09 | 持久化 consolidation | COMPLETED 时入队，Runtime 后台 claim；固定输入边界；独立 provider 使用冻结主模型；结果原子提交 | P1 | US-03 |
| FEAT-10 | 设置与快照 | memory 完整配置随 Run 冻结；删除 ratio；历史轮数配置化；后台 job 只读其来源 Run 的冻结策略 | P0 | US-06 |
| FEAT-11 | Schema 一致 | scope / history / command receipt / job；稳定 key 唯一约束；租户前导索引；迁移与 ORM parity | P0 | US-06 |
| FEAT-12 | Console 与观测 | 状态、来源、到期、历史；管理写操作调用 Runtime；来源与操作者分离；claim / stale / quota / dropped 可见 | P1 | US-02, US-06 |
| FEAT-13 | 规范同步 | 新增 memory Tier-1 spec；修订 data 的稳定身份例外、arch 注入刷新、snapshot / test / ui-detail；同步相关 docs | P0 | US-07 |

#### 2.3.3 配置项

所有字段在 contracts 单一 schema 定义，Run 创建时冻结；Agent 的 memory_write 只覆盖 write_enabled，不授予来源或越限能力。有效候选上限由全局绝对条数界限制，避免“全量读取”退化为无界扫描。

| memory 字段 | 默认 | 约束与语义 |
|---|---|---|
| write_enabled | true | 控制新增/覆盖/自动提炼；不阻止用户 forget 或管理员删除 |
| max_injected_memories | 10 | 0..20；0 关闭整个自动记忆块 |
| max_injected_bytes | 2048 | 0..16384；包含措辞、分区标题、key、标点、换行；0 关闭 |
| max_recall_bytes | 4096 | 1..16384；完整 JSON 回执；装不下固定封套时返回可读预算错误 |
| recall_default_limit | 10 | 0..20；显式 limit 同范围；0 返回空 items |
| inject_inferred | false | 仅控制普通 AGENT 事实；不控制任务续接通道 |
| task_state_injection_enabled | true | 对明确续接请求读取 TASK_STATE；分区标注未经确认 |
| max_entries_per_user | 200 | 1..1000；绝对上界 1000；计所有未删除且未过期条目，包括 disabled |
| max_memory_bytes_per_user | 65536 | 1024..1048576；计有效条目 content_json 与 source_quote 的 canonical UTF-8 字节 |
| ttl_days_user | 0 | 0..3650；0 不过期；适用于确认后的 USER 与 ADMIN |
| ttl_days_agent | 30 | 1..365；普通推断 |
| ttl_days_task_state | 7 | 1..90；TASK_STATE 独立 TTL |
| consolidation_enabled | true | 使用 Agent 显式选择且已冻结的主模型；无新增平台默认模型 |
| consolidation_min_user_messages | 1 | 1..100；只数通过输入边界的本 Run 用户消息，不数工具事件 |
| consolidation_max_input_bytes | 32768 | 1024..131072；输入构建后超限则 SKIPPED，不静默截断事实 |
| consolidation_max_output_tokens | 1024 | 128..4096；独立输出上限 |
| consolidation_timeout_sec | 30 | 1..120；包含凭据解析、模型调用；不复用已结束 Run 的 deadline |
| consolidation_max_candidates | 8 | 1..20；含至多 1 条 TASK_STATE |
| consolidation_max_attempts | 2 | 1..3；基础设施瞬时失败可重试；坏形状、STALE、策略拒绝不重试 |
| consolidation_retry_delay_sec | 5 | 1..60；attempt 间隔，由后台任务读取冻结值 |
| consolidation_lease_sec | 60 | 16..300；必须 ≥ timeout_sec + 15，提交前核对 lease |

compaction 删除整个 memory.budget_ratio 分支；新增 max_history_tool_rounds，默认 6，范围 0..50，0 表示不回放历史工具轮。micro / summary 的既有开关及默认值不变；consolidation 不以启用摘要为前提。

候选字符数、key 格式、确认协议格式等契约常量在 contracts 统一声明；它们不是每轮动态设置。新增配置键不再手写“7 个”的数量断言，catalog 与 schema 叶子集合由测试逐键比对。

### 2.4 范围与边界

| 类别 | 内容 |
|---|---|
| In Scope | FEAT-01..13；复用既有中断确认；新增 TASK_STATE 类别；Runtime 持久后台任务；共享排序与契约；维护窗口数据迁移 |
| Out of Scope | embedding / pgvector；新 session/thread 实体；Worker 构建模型请求；跨用户记忆；独立用户门户；自动语义去重/冲突消解；物理清除会话或审计原文 |
| 作用域 | T2 恒为 (tenant_id,user_id)，不加 agent 维度；这是产品可见范围，相关度不是访问控制，未来私有 agent 记忆需另立作用域需求 |
| 容量边界 | 配额限制有效事实；历史、tombstone、命令回执、job 属审计与控制数据，单独监控容量，禁止通过软删历史释放事实额度 |
| 恢复边界 | 新 Run 的跨会话续接依赖成功提交的任务线索；模型/存储故障时明确可观察地退化；任务真实状态必须调用当前 get_task 等权威接口核验 |
| forget 的含义 | 撤销指定 key 的长期读取资格并阻止自动复活；历史和原会话保持审计可查，不能承诺模型从已发送的原会话中物理遗忘 |

### 2.5 验收条件

#### 2.5.1 业务规则

| 编号 | 规则 |
|---|---|
| RULE-01 | 删除 corroborate 的来源猜测。模型提交候选不等于用户确认；真实确认绑定当前用户、完整候选 canonical hash、key、expected_version、有效期，并一次性消费 |
| RULE-02 | 自动偏好只取 USER/ADMIN 且已确认的 PREFERENCE / WORK_STYLE；普通 AGENT 默认不注入；TASK_STATE 只走明确续接通道并标注不确定性 |
| RULE-03 | 所有修改锁同一 memory_scope；取得最终 id/version 后写完整 after 历史，同事务提交；任何历史失败整体回滚；回放按 version，不按时间推测 |
| RULE-04 | CREATE / OVERWRITE / CONFIRM / ENABLE / DISABLE / DELETE / EXPIRE / QUOTA_EVICT / RESTORE / TASK_COMPLETE / MIGRATION_BASELINE 均有明确历史原因；所有可见状态变化递增 version |
| RULE-05 | 候选查询带 tenant/user、未删除、enabled、expires_at 条件，读取全部有界候选；选择函数按消费模式执行，不允许先按最新 N 排除老事实 |
| RULE-06 | 注入条数和完整 UTF-8 字节均为硬限；长条跳过继续尝试；0 关闭；每个未选候选只计一个丢弃原因；查询候选之前不存在隐蔽截断 |
| RULE-07 | 空集合整段省略；查询失败 warning + metric，本轮不注入；错误不能恢复旧 memory component 或吞掉其他 SYSTEM 前缀 |
| RULE-08 | 锁内对写后集合清扫/淘汰/算配额；优先淘汰 AGENT，永不自动淘汰 USER/ADMIN；冻结额度内仍不足拒绝；旧低额度不回退已经扩容的既有集合，删除等减容永远允许；清扫失败回滚 |
| RULE-09 | forget 保留 identity/version 链并递增 forget_epoch；USER_DELETE/ADMIN_DELETE 禁止自动复活，旧提议不能消费；当前 Run 下一请求不再使用旧 memory component |
| RULE-10 | consolidation 效果最多一次；批量写入、历史、scope revision、完成事件与 job 终态原子提交；可重复模型调用，不承诺网络请求恰好一次 |
| RULE-11 | memory component 渲染在连续 SYSTEM 受保护前缀；组件可按明确刷新协议替换；压缩层仍不能裁剪该前缀 |
| RULE-12 | scope 由受信调用上下文绑定，不从模型参数取；ADMIN 写入只来自 Console 授权后的内部调用；来源 author 与 actor 分开 |
| RULE-13 | 自动提炼不可覆盖 USER/ADMIN、人工 disabled 或删除墓碑；owner revision 变化整批 STALE；EXPIRE/QUOTA_EVICT 仅新入站依据可恢复，逐 key 检查不能省略 |
| RULE-14 | 存量先写基线再改变来源/TTL；无法补证的旧模型自报来源按 AGENT 处理；迁移不能伪造之前不存在的历史 |
| RULE-15 | 收尾失败至少有 warning + metric；DB 不可写时不能承诺失败事件必然落库，也不能为此改变主 Run 终态 |

#### 2.5.2 验收场景与不得 mock 的边界

以下是待实现验收契约，均未执行或宣称通过；Plan 阶段必须给每项绑定真实测试路径/用例名、owner、argv/cwd/timeout，不可用尚不存在的 -k 名称充当 Evidence。

| 场景ID | 功能 | 层级与真实边界 | 可使设计失败的断言 |
|---|---|---|---|
| B-01 | FEAT-02 | unit，纯证据校验 | 仅含“记住”、任意词重合、部分截取否定句、模型自报 USER 均不能获得确认 |
| B-02 | FEAT-04 | unit，分词与选择 | CJK/latin、无 query、有 query、同分确定性；非空 query 零覆盖为零条 |
| B-03 | FEAT-03 | integration，真实 PG、5 个独立事务同 key 写 | 同 id、version 连续且历史一一对应；事务时间逆序不影响回放 |
| B-04 | FEAT-08 | integration，真实 PG、独立连接不同 key 并发写 | 199/200 最后额度只能一个写者取得；字节额度也不可穿透 |
| B-05 | FEAT-08 | unit + PG，禁存内容 | value、key、quote、TASK_STATE 任一含密钥形态则拒绝；memory/history/proposal/log 中无 canary |
| B-06 | FEAT-06 | integration，真实 PG + 工具回执 | 非空库无关 query 返回空并计 SEARCH miss；空 query 为 LIST；完整 JSON 字节不越限 |
| B-07 | FEAT-07 | integration，真实 PG + 模型 HTTP 探针 | 同 Run forget 后下一请求旧 memory 条目消失；删库/刷新失败不宣称成功并移除组件；历史仍可查 |
| B-08 | FEAT-05 | integration，真实 PG + 请求探针 | 200 条中最老的匹配仍参与选择；最终选取与丢条计数一致 |
| B-09 | FEAT-05 | unit + 请求探针 | 完整块恰等预算可进、超 1 字节不可进；0 关闭；长条失败后短条可进；无首条越限 |
| B-10 | FEAT-09 | integration，真实 PG + HTTP 模型探针 | 两个 Runtime 并发 claim、过期 lease 接管；最多一套记忆/历史/完成事件；旧 epoch 无法提交 |
| B-11 | FEAT-09 | integration，真实 PG + 故障注入 | 坏形状、批内后项失败、历史失败整批零效果；效果提交前/后崩溃均不重复；Run 保持 COMPLETED |
| B-12 | FEAT-10 | integration，真实 PG + 两段 Run | 修改一个设置，新 Run 读新值，旧 Run/resume/job 均读冻结值；无其他叶子意外变化 |
| B-13 | FEAT-11 | integration，迁移库 + ORM create_all | 表/索引/约束逐项 parity；完整 key 唯一与历史 (origin_id,version) 唯一可真实阻止违规 |
| B-14 | FEAT-03,11 | integration，旧 revision 存量库升级 | 基线 A/version20 → 来源/TTL政策version21 → 新写B/version22仍可回放A；旧来源不伪造 USER；旧 AGENT 不覆写也会到期 |
| B-15 | FEAT-02 | integration，真实 Gateway/Runtime/PG/resume | 确认内容与展示一致；其他用户、编号、过期、旧版本/forget_epoch、重放均不能保存另一份内容；同轮两提议明确拒绝第二个；无效回答仍 WAITING，拒绝合法续跑 |
| B-16 | FEAT-07,09 | integration，真实 PG + HTTP 模型探针 | forget 后才开始提炼旧会话仍不可复活；模型期间删除使 job STALE；人工 disabled 不被启用；自动退休仅新入站 source_scope_revision 可恢复 |
| B-17 | FEAT-03,09 | integration，真实 PG | AGENT 同 key 不覆盖 USER/ADMIN；晚结束的旧 Run 不覆盖较新状态；同值重放不虚增确认数 |
| B-18 | FEAT-08 | integration，真实 PG | 全 USER/ADMIN 满额明确拒绝；淘汰失败不写新事实；批量失败恢复已淘汰事实 |
| B-19 | FEAT-09 | integration，摘要关闭 + 真实模型 HTTP | consolidation 仍用冻结主模型；凭据实时取；provider 正常关闭；timeout 不改变 Run 终态 |
| B-20 | FEAT-11 | integration，迁移前置检查与恢复演练 | 存在 WAITING_INPUT/WAITING_TOOL/job 或超额数据时阻断切换；旧键只从新当前设置移除；同 key 两个version1 incarnation完整归档且新history唯一 |
| B-21 | FEAT-09 | integration，真实 PG + 延迟 job / 后续摘要 | R1 job在R2摘要后执行仍仅含R1边界；重试输入hash/revision不可刷新；记忆变更后重试STALE |
| B-22 | FEAT-08,10 | integration，真实PG+旧新Run | 额度200→300后250条均被旧Run读到；旧Run forget成功；旧冻结额度不额外淘汰；在线降低拒绝，维护排空才可降 |
| B-23 | FEAT-04,05 | integration，真实 PG + 并发 writer / 当前语句时间 | 暖缓存不重查全量；revision与行集同快照；跨来源写入使缓存失效；revision不变也即时过滤TTL；轻量/全量读失败均不复用旧缓存；0预算无注入查询 |
| E-01 | FEAT-05 | acceptance，真实 Runtime + PG/Redis + 模型 HTTP | memory/summary 在保护前缀，micro/snip/summary 不裁 memory；刷新只替换组件 |
| E-02 | FEAT-02 | acceptance，真实 Gateway → Runtime → resume | 自然语言候选展示后用户真实确认，重连/重复提交不重复保存；拒绝后不产生 USER 事实 |
| E-03 | FEAT-04 | acceptance，真实 Gateway + 两个 conversation | 已确认中文偏好在 /new 后“你好”首轮可见；不要求 query 包含“中文” |
| E-04 | FEAT-12 | browser，真实 Console/Runtime/PG/Chrome | 创建、启禁用、删除、历史分页和来源状态本地化；越权不可操作 |
| E-05 | FEAT-04,09 | acceptance，真实 Gateway/Runtime/PG + 模型探针 | 默认设置提炼未决项；/new后续接进入请求；多条→用户“第2个”→目标实际进入请求；A→B→C仍唯一，B完成后无旧未决；大checkpoint投递引用，引用超预算仅计丢弃 |
| E-06 | FEAT-09 | acceptance，真实两个 Runtime 子进程 | SIGKILL claim 执行者，另一实例接管；故障与成功可见；持久写入效果最多一次 |
| S-01 | FEAT-02,12 | security，真实 API 身份与租户边界 | 伪造 user/tenant/author、跨用户确认、无服务身份内部调用均拒绝；未授权 ADMIN 不写 |
| S-02 | FEAT-05,08 | security，真实装配与 canary 检查 | 标注引用与未经确认；密钥不进入新增表、历史、回执、日志、模型 consolidation Prompt |

#### 2.5.3 非功能约束

- 正确性先于语句数。写入是短事务、有数据库锁；禁止锁内网络调用、无界重试或吞掉 history 错误。
- 一次候选查询返回最多配置硬上界 1000 条，单条 value ≤512 字符，quote ≤512 字符；超过数据库配置不变量时明确报错并告警，不静默 LIMIT。
- consolidation 每个成功 Run 至多一个 job，正常至多一次模型调用；接管时可重调，attempt/time/input/output 全有界。
- job、续接选项消费与 TTL sweep 在 Runtime 统一生命周期内执行，不新增部署单元；无 sticky instance，PG 为权威。
- 只读错误和模型失败退化为缺少记忆；写入拒绝要给工具可读错误，主 Run 不因可选记忆操作失败而终止。

## 3. 技术设计

### 3.1 方案选型与 ADR

#### 3.1.1 三层职责

| 层 | 内容与载体 | 生命周期 | 唯一职责 |
|---|---|---|---|
| T0 工作记忆 | 当前 ModelRequest 的结构化 SYSTEM 组件与回放消息 | 请求/执行内，按已持久化事实重建 | 为当前调用组装上下文；不充当事实存储 |
| T1 会话记忆 | canonical_event + 最新 CONTEXT_SUMMARY；TRANSCRIPT 只作逐字存档 | conversation，/new 后不自动回放旧会话 | 保留可重建的会话历史 |
| T2 长期记忆 | user_memory + history + scope | (tenant,user)，确认、TTL、删除、配额 | 跨会话事实与未经确认的任务线索；不授予能力或权限 |

#### 3.1.2 关键取舍

| 决策 | 选中方案 | 取舍依据 |
|---|---|---|
| 来源 | 确认候选 + 精确原话证据 | 自然语言词法无法证明语义或长期保存意愿；确认协议使授权可验证 |
| 写入 | Runtime 唯一事务服务，用户级 scope 锁 | 配额与多 key 批量修改需要统一协调；Console 不复制写入算法 |
| 版本 | 稳定 key identity + 完整 after 历史 | 不维护“当前多行”或新旧双列；先改后记录仍可原子回滚 |
| 检索 | 有界全量候选 + 纯选择原语 | 当前 200 默认、1000 绝对上界足够；不拿最新 N 牺牲正确性 |
| 续接 | 独立 TASK_STATE 通道 | 偏好与任务线索的可信度、检索触发、TTL 不同 |
| consolidation | 持久 job + 独立主模型 provider | 终态后内存协程会丢任务；摘要开关/已关闭 provider 不应影响此功能 |
| 发布 | 维护窗口一次 schema/code 切换 | 不做临时 source_type 写入、双写、旧快照适配或两阶段兼容部署 |

- **ADR-01**：不引入 session/thread；T2 用户共享是明确产品权限边界，不能靠排序实现隔离。
- **ADR-02**：删除 corroborate 的 author 推断；确认是 Runtime 协议事实，author 不在工具 schema。
- **ADR-03**：暂不引入 embedding。词法检索只保证实际词匹配；SEARCH miss 指标与人工相关性样本用于后续评估，不把返回非空等同语义正确。
- **ADR-04**：删除 ratio 并一次迁移当前配置；新协议不执行旧格式在途快照，不留兼容分支。
- **ADR-05**：user_memory 是稳定 key 注册表，完整 UNIQUE 包含 tombstone；data spec 必须明确该例外，不能假称仍满足旧 partial unique 口径。
- **ADR-06**：consolidation 的模型定义取来源 Run 已冻结的主模型；单独取当前凭据、建 provider、设预算并 finally 关闭，禁止借用 executor 生命周期。
- **ADR-07**：自动写冲突先看权限/确认/删除状态；相关度不能决定是否允许覆盖。
- **ADR-08**：memory 作为可刷新组件存在于受保护 SYSTEM 前缀；压缩与记忆刷新是不同操作。
- **ADR-09**：硬配额适用于全部来源；清理失败回滚；用户请求不能通过故障路径变成无限容量。
- **ADR-10**：consolidation 采用可重复计算、最多一次提交；不声称模型端支持 exactly-once。
- **ADR-11**：原话只能证明文字来源；ADMIN/USER 确认也不证明记忆事实正确，不覆盖当前指示与实时权威系统。

### 3.2 组件与依赖

#### 3.2.1 组件分工

| 位置 | 职责 |
|---|---|
| packages/contracts/.../memory.py | 严格枚举/DTO、候选确认协议、TASK_STATE 输入、续接 options/hash/原任务引用、字节常量、canonical hash |
| packages/agent-core/.../memory/ | tokenize / lexical_coverage / select_memories / render_memory_block；resume.py 固定完整请求/选择语法及候选 hash；纯函数，无 app/DB/network import |
| Runtime application/memory_service.py | 唯一 MemoryMutationService；调用者身份已绑定；scope 锁、冲突、配额、历史、命令回执 |
| Runtime application/memory_commands.py | 受信 MemoryWriteContext、严格批量命令与只含元数据的回执 DTO |
| Runtime application/memory_mutations.py | 锁内整批规划、隐式退休与最终状态配额、完整 after history；不提交事务 |
| Runtime application/memory_confirmation.py / memory_confirmation_resume.py | Run内互斥登记typed候选与saved=false回执，整轮完成后经checkpoint原子暂停；真实resume按完整回答/持久proposal hash/版本/epoch/期限验证，唯一writer与结果/interrupt/Run CAS/submission同事务，原候选回执不改写 |
| Runtime application/memory_proposals.py | 从受信submission/USER_MESSAGE构造候选hash/版本/epoch/期限，读取全状态key并直接格式化完整确认提示；取得行锁后复核数据库时钟与执行fence |
| Runtime application/checkpoint.py | 同事务写完整回合continuation、MEMORY_CONFIRMATION interrupt/提示事件、WAITING_INPUT与lease释放 |
| packages/agent-core/.../agent/suspension.py | typed ToolSuspensionPort/MemorySuspendRequest；Runner整轮收口后暂停，无app/DB依赖 |
| Runtime application/memory_queries.py | 有界候选与版本历史查询；共享 MemoryReadFilters |
| Runtime application/memory_cache.py | 当前Run的候选行/revision缓存；轻量head校验、当次TTL过滤，失败清除并warning/metric |
| Runtime application/memory_context.py | 冻结policy/query，按请求刷新typed MemoryComponent；续接引用按原 key/version/hash 校验当次有效快照；完整渲染预算/丢条计数，forget IO前invalidate，失败后本Run持续禁用；generation拒绝在途旧快照 |
| Runtime application/memory_runtime.py / memory_tools.py | 每Run组装共享Loader、严格工具与确认port；受信claim identity/submission由RunService传入；recall独立查全集，remember仅提议，forget按version经唯一writer删除 |
| Runtime application/memory_resume.py / memory_resume_options.py | 真实入站 USER_MESSAGE 固定 scope revision；Conversation 锁内追加有界 options/统一消费结果，标题/目标词法匹配、300秒有效期、单项选择也先建 options；Runtime 直接生成清单 |
| Runtime application/memory_resume_queries.py / memory_resume_metrics.py | 按 tenant/user/Conversation/Run 读持久引用；成功 outcome 指标只在外层事务提交后发出，外层或 savepoint 回滚不计成功 |
| Runtime application/run_service.py / run_events.py | CREATE/RESUME 交真实入站服务；EventWriter 接受服务端预分配 UUID，保持 append-only 与 source 唯一；清单/无匹配提示由 Runtime 持久事件和真实 Run 终态直接交付，模型执行器接收受信 resume_ref |
| Runtime application/run_resume.py / run_submission.py | resume 短事务先按tenant/key/endpoint取advisory锁并重查重放；Conversation→Run→Interrupt锁后复查状态/deadline，Scope锁等待后与最终CAS前也重验数据库时刻；显式resume必须提供匹配的platform_user_id，原input.id不冒充transport幂等键 |
| Runtime application/memory_consolidation.py | 输入构建/模型解析/候选验证；不自行开 per-item 提交 |
| Runtime application/memory_jobs.py | 持久 claim、lease/epoch、重试、原子效果提交；接入现有 lifespan |
| Runtime application/context_builder.py | 仅按租户/用户重建canonical与摘要，不做memory预查或比例预算 |
| Runtime application/executor.py / agent-core AgentRunner | 默认Executor注入typed ModelContextLoader；每次模型发送（含重试）刷新派生请求中的单一记忆组件，再压缩；不写回权威history |
| Runtime infrastructure/models/runtime.py | scope / memory / history / receipt / job ORM 与索引 |
| Console application/memory_service.py | 管理员鉴权与协议适配；写操作调用受保护 Runtime API，读列表/历史可用只读仓储 |
| Console infrastructure/repositories/memory_repository.py | 只读查询与分页，包含 tenant/user；不直接 UPDATE/INSERT runtime 表 |
| Gateway inbound/resume 适配 | 传真实入站 message/用户身份及确认输入；禁止由 LLM 伪造确认回调 |

新增共享包仅为 contracts 的模块与 agent-core 的纯函数；不新建 DAO 副本、跨 app import 或新的部署单元。

#### 3.2.2 模型请求组件

~~~text
[SYSTEM] PromptBuilder 的固定 Agent 提示
[SYSTEM] MemoryComponent 渲染结果（0 或 1 条；偏好/相关事实/任务线索分区）
[SYSTEM] T1 历史摘要（若有）
[USER|ASSISTANT|TOOL] 当前会话回放与本轮结果
~~~

MemoryComponent 保存来源 key/version、snapshot_revision 和渲染文本，而不是用文本前缀猜是哪段。load_history、resume、continuation 只登记同一组件刷新入口；实际候选查询集中在每次模型发送前，禁止 load_history 与首次 pre-call 各查询一遍。executor 注入 typed async context-loader 协议，agent-core 不导入 Runtime；刷新只替换具名 memory 组件，其他消息保持原角色与顺序。

forget / remember 确认 / 管理修改在本 Run 发生时触发 invalidate。forget 在 IO 开始前先移除对应组件；成功后从新 snapshot 重建，失败则本 Run 后续请求保持无记忆组件。其他 SYSTEM 和 T1 历史不变，工具回执明确区分保存/删除成功与失败。历史会话中曾出现的原话/recall 回执不会被冒充为物理擦除。

每次新模型调用前读取当前有界 memory snapshot，刷新组件；不重新读取平台设置。这样跨 Run/Console 已提交的删除也会在后续查询可见，不以 scope revision 的旧内存缓存继续发送已撤销的条目。线性化边界是本次 snapshot 查询，已在发送中的请求不承诺撤回。

### 3.3 数据设计

#### 3.3.1 memory_scope（新增）

标准四列 + tenant_id/user_id/revision/forget_epoch（初值0）。UNIQUE(tenant_id,user_id) 为完整唯一，is_deleted 恒 false；逻辑用户引用，不与 control 建 FK。初次写入 INSERT ON CONFLICT DO NOTHING 后 SELECT FOR UPDATE；成功修改集合时 revision 加 1，空操作/幂等重放不加。

用户/ADMIN DELETE 或 CLEAR 同事务递增 forget_epoch；旧提议携带的 epoch 不等即逻辑失效。forget 不遍历/更新其他 Run 的 interrupt，避免持 scope 再等待 interrupt 的反向锁序。其他修改只递增 revision。

这是同步原语与聚合版本，读取已有记忆不会创建 scope 行。候选查询用一个 SQL snapshot 同时得到 scope revision、数据库 statement_timestamp() 与所有符合条件的行。

#### 3.3.2 user_memory（稳定 identity）

| 字段 | 约束/意义 |
|---|---|
| 标准四列 / tenant_id / user_id / memory_key | 保留；完整 UNIQUE(tenant_id,user_id,memory_key)，含已删除行 |
| category | PREFERENCE / WORK_STYLE / EXPLICIT / TASK_STATE 严格枚举 |
| content_json | 严格 JSON；普通事实仅 value ≤512 字符；TASK_STATE 使用下述独立 schema |
| author | USER=真实确认的候选；ADMIN=授权人工录入；AGENT=未经确认的推断/任务线索 |
| explicit_request | USER 确认时 true；AGENT false；ADMIN 不伪造用户请求 |
| confidence | 1..5；AGENT 固定默认 2；不会授予覆盖权限 |
| confirmed_count / last_confirmed_at | 仅真实、首次消费的用户确认增加；重复消息/命令重放不加 |
| expires_at | author/category 决定；NULL 仅允许政策明确不过期的 USER/ADMIN |
| source_event_id / source_seq / source_ref | 真正依据事件与提交边界；运行时取值，模型不能选任意历史事件 |
| source_quote | 当前 submission 可验证的完整原话，≤512 字符；无原话时 NULL，确认事件仍可取证 |
| confirmation_event_id | 用户真实确认对应事件；USER 必填，ADMIN/AGENT 可空 |
| version | biginteger；CREATE 从 1；所有真实修改 +1；DELETE/RESTORE 不重置 |
| enabled / is_deleted | disabled 为人工禁用；is_deleted 行需依据 retirement_reason 区分永久删除屏障与自动退休 |
| retirement_reason / retirement_revision | USER_DELETE / ADMIN_DELETE / EXPIRE / QUOTA_EVICT / TASK_COMPLETE 或 NULL；只有前两者是用户删除墓碑；退休记录当次 scope revision |
| last_source_run_id / last_source_seq | 最近自动更新依据；同 conversation 比 seq，跨 conversation 依受信 resume 引用的 expected_version 检查因果关系 |
| write_policy | 固定 SERVER_CONTROLLED，不接受模型传值 |

普通 key 长度与字符格式沿用当前契约；task.* 命名空间保留给 TASK_STATE，用户/模型普通事实不能占用。TASK_STATE key = task.<root_conversation_uuid_hex>。新任务以当前 conversation 为 root；续接时使用 Runtime 已选择的原 key/expected_version，更新同一 identity，不为续接 conversation 再造一条未决状态。一个会话支持一个任务 checkpoint；同会话多独立任务的结构化拆分不在本版范围。结构：

~~~json
{
  "title": "季度报告",
  "goal": "完成季度经营报告",
  "constraints": ["只使用用户授权的数据源"],
  "open_items": ["补齐未收到的数据"],
  "next_action": "先确认数据是否已到",
  "root_conversation_id": "uuid",
  "source_conversation_id": "uuid"
}
~~~

title ≤80 字符；goal/next_action ≤256；constraints/open_items 每项 ≤128、各至多 5 项；content canonical UTF-8 ≤2048 字节。只存目标、约束与未决线索，不存工具结果全文、授权结论或“任务已成功”的权威状态；所有这些字段仍过内容闸门。空 open_items 表示任务无待续项；持有匹配的 resume 引用时以 TASK_COMPLETE 退休原 checkpoint，保留历史。新目标需要新的真实用户依据；当前 conversation root 的 TASK_COMPLETE 条目可在新依据下进入新任务状态，用户删除墓碑仍不可越过。

索引全部以 tenant_id 为首列：

~~~sql
CREATE UNIQUE INDEX uq_user_memory_key
  ON runtime.user_memory (tenant_id, user_id, memory_key);
CREATE INDEX ix_user_memory_candidates
  ON runtime.user_memory (tenant_id, user_id, category, update_time DESC)
  WHERE is_deleted = false AND enabled = true;
CREATE INDEX ix_user_memory_expiry
  ON runtime.user_memory (tenant_id, expires_at, user_id)
  WHERE is_deleted = false AND expires_at IS NOT NULL;
~~~

这是对 harness-data#RULE-data-001 的有意修订：稳定 key 注册表的唯一性覆盖 tombstone，其他软删业务实体继续用 partial unique。迁移旧行同 key 多 incarnation 时保留历史关联，选择当前活跃 incarnation 或最新已删 incarnation 作为稳定 identity；其他旧行先归档到基线历史再移出活跃表，不能直接丢数据。

#### 3.3.3 user_memory_history（新增，append-only）

标准四列（is_deleted 恒 false）+ tenant/user/key/origin_id/version/change_reason/actor_type/actor_id/command_id/source_run_id/after_json。after_json 是与该版本活跃行完全同构的快照，包含 enabled、is_deleted、retirement_reason/revision、TTL、证据和确认字段，不仅是 content。新增可空 legacy_snapshots_json 数组，仅迁移基线使用，保存该 key 所有旧 incarnation 的原 id/version/完整行；普通变更不写此列。

UNIQUE(origin_id,version)；索引 (tenant,user,key,version DESC)。时间用于展示，顺序由 version 决定；上一版本给旧状态。首条 CREATE 没有旧状态，MIGRATION_BASELINE 明确标注“迁移前未记录的历史不可恢复”，不得伪造版本 1..N。

source/actor/command 的审计引用使用逻辑 UUID/标识，保留审计后不妨碍账号或执行对象清理。history insert 失败使事实、淘汰和 scope revision 全部回滚。

#### 3.3.4 memory_command_receipt（新增）

标准四列，is_deleted 恒 false；UNIQUE(tenant_id,command_id)。保存 endpoint、user_id、canonical fingerprint、status、response_json、source_run_id。response 只含必要元数据，不含密钥/候选原文。

tool command_id 由可信 run_id/tool_call_id 派生；确认由 interrupt_id + 真实入站 message_id 派生；Console Idempotency-Key 经受保护内部接口传入。指纹包含 tenant/user/endpoint/action/规范化输入，不依赖响应文案。

同 ID 同指纹重放已提交回执；不同指纹拒绝。Scope 行锁后按 `(tenant_id,command_id)` 派生事务 advisory lock，再重查 receipt；跨 user/endpoint 共用这个命令身份并明确拒绝异指纹，完整唯一约束兜底。命令回执与事实/history 同事务，不使用“先标成功后写入”的两步操作。批量清空只生成一个 receipt，各 history 行关联同 command_id。

#### 3.3.5 memory_consolidation_job（新增）

标准四列，is_deleted 恒 false；UNIQUE(tenant_id,run_id)。列：conversation_id/user_id/through_seq/status/lease_owner/claim_epoch/lease_until/attempt/next_attempt_at/result_json/enqueued_scope_revision/input_scope_revision/input_json/input_hash/resume_ref_json/error_code。

- status ∈ PENDING / RUNNING / RETRY / DONE / SKIPPED / FAILED。
- job 引用来源 Run 的不可变 snapshot_id；不保存 api_key，不重新取平台设置。
- input_json 是一次构建的有界脱敏输入 DTO，input_hash 用 canonical_json；input_scope_revision 与输入一旦记录不可由重试改写。result_json 只允许已过密钥与形状校验的候选；写入效果与 DONE 在同一个事务完成。
- 部分 claim 索引 (tenant_id,next_attempt_at,create_time) WHERE status IN ('PENDING','RETRY')；另建 (tenant_id,lease_until) WHERE status='RUNNING'。
- 一旦 DONE/SKIPPED/FAILED 不自动重开；人工重试是新命令并保留原失败依据，不能清掉终态事件伪装首次执行。

完成 canonical_event = MEMORY_CONSOLIDATED。唯一索引 (tenant_id,run_id) WHERE event_type='MEMORY_CONSOLIDATED' AND is_deleted=false；事件用于审计，job 是执行权威，不能只用“查事件是否存在”做并发闸门。

#### 3.3.6 其余硬约束索引（文本提到但必须落到 DDL）

| 约束 | 定义 | 挡住的行为 |
|---|---|---|
| `uq_run_interrupt_pending_memory_confirm` | `UNIQUE (tenant_id, run_id) WHERE interrupt_type='MEMORY_CONFIRMATION' AND status='WAITING' AND is_deleted=false` | 同一 Run 挂两个待确认提议（§3.4.2 的"同轮第二个 remember 返回 PENDING"不能只靠应用层判断） |
| `uq_canonical_event_source`（既有，复用） | `UNIQUE (tenant_id, source_event_id) WHERE source_event_id IS NOT NULL AND is_deleted=false`；统一最终事件 `MEMORY_RESUME_RESOLVED` 的 source_event_id=options_event_id，outcome 只放 payload | 同一条 options 的 SELECTED/CANCELLED/EXPIRED 互斥；唯一键不得包含 outcome，也不新增不存在的 options_event_id/outcome 物理列 |
| `ix_memory_confirm_expiry` | `(tenant_id, run_id, expires_at) WHERE interrupt_type='MEMORY_CONFIRMATION' AND status='WAITING'` | 过期提议的后台清扫（不占 scope 锁扫全表） |

上述新增索引与复用的既有约束一起进 schema_parity 的逐项比对（B-13）。两个唯一约束各验证真实重复插入被拒，其中 options 必须覆盖 SELECTED 与 CANCELLED 跨 outcome 冲突；过期清扫索引验证声明/迁移一致及查询谓词，不拿非唯一索引断言“第二行被拒”。唯一约束是协议的一部分，不是优化。

#### 3.3.7 既有表的可复用字段（避免误判为新增）

| 既有载体 | 已有什么 | 本需求怎么用 |
|---|---|---|
| `runtime.run_interrupt` | `interrupt_type` / `prompt_text` / `options_json`(JSONB) / `status` / `resolution_json` / `expires_at` / `resolved_at`（`runtime.py:143-164`） | `MEMORY_CONFIRMATION` 只**新增一列 `proposal_json`**；待确认列表可直接用 `options_json` 投递，用户回答落 `resolution_json`，过期用 `expires_at` ⇒ 无需新建中断表 |
| `runtime.run_submission` | `uq_run_submission_tenant_key_endpoint`(partial unique) + `request_fingerprint` + 终态回执 | `memory_command_receipt` 的形态沿用同一套幂等范式（不同 endpoint、不同表），不复用其唯一键 |
| `muad_im_gateway` inbound | `COMMAND_PREFIXES = ("/bind","/new","/stop","/skills")`（`apps/im-gateway/src/muad_im_gateway/application/inbound.py:155`），命令不排队长流队列 | `/memory` 加入该元组以获得同样的"不被长流拖住"性质（见 §3.4.2 注） |

### 3.4 写入与确认协议

#### 3.4.1 原话证据

verify_quote 只验证 quote 是当前 submission 的完整用户消息，或该消息中明确界定的引用单元；不得从含否定/问题/条件的完整句子中任意剪一个肯定片段。本文不实现自然语言语义判真：不能确定引用单元边界时只允许完整消息。只统一换行，不做语义改写。

value 与 quote 相等只能证明文本来源，仍不能自动设置 explicit_request 或跳过确认。没有 quote、quote 不匹配可作为 AGENT 候选提出，但最终 USER 来源只在用户真实确认后赋予；quote 无法验证时不落假证据。

#### 3.4.2 remember 与真实确认

工具 schema 为 {memory_key,value,category,source_quote?}，前三项必填，additionalProperties=false；category 不包含 TASK_STATE。author、tenant/user、source_event_id、explicit_request、version 不可由模型设置。

普通自然语言调用 remember：
1. 先校验内容与可写开关，构造完整候选和 expected_version。
2. 建立提议时读取该 key 的全部状态（含 disabled/deleted/expired）及 scope epoch，不能用模型可见候选过滤器把 tombstone 当不存在。remember handler 只登记结构化 suspend_request，不在工具内改变 Run 状态。完整工具回合先收口结果与审计，再原子保存 checkpoint、MEMORY_CONFIRMATION interrupt、Run WAITING_INPUT 与 lease 释放；Runner 返回 WAITING_INPUT，不再调模型。run_interrupt 新增 proposal_json，绑定 tenant/user/key/category/value/hash/expected_version/forget_epoch/expiry。同 Run 最多一个 WAITING MEMORY_CONFIRMATION，数据库 partial unique 兜底；同轮第二个 remember 返回 MEMORY_CONFIRMATION_PENDING，不覆盖首个候选。
3. Runtime 持久化并直接格式化投递待保存完整内容与候选编号；用户明确“确认 <编号>”或“拒绝 <编号>”。仅匹配真实入站消息，模型 ask_user 回执/工具文本不能充当批准；其他回答不消费提议。
4. resume 先按 interrupt 类型分派，MEMORY_CONFIRMATION 不进通用无条件消费分支。确认事务按锁序核验 hash/版本/forget_epoch/用户/期限，写真实 USER_MESSAGE 与确认结果事件、USER 事实/history/receipt、interrupt RESOLVED、submission 回执以及 Run WAITING_INPUT→CREATED CAS；失败无部分保存。拒绝也一次性消费并合法续跑，但不写事实。无效编号/其他回答保持等待并返回专用错误；有效编号对应提议已过期/旧版本/旧 epoch 且 Run 仍可恢复时，消费为 STALE 并续跑告知重新提议。Run 已超过绝对 deadline 或进入终态时，不保存、不恢复，返回相应终态/过期错误；此规则优先于提议重试。
5. 确认结果通过新事件/恢复后的结果消息报告 saved=true/version/TTL；原候选工具结果 saved=false, confirmation_required=true 保持不可变，不回写已落库事件。

实现落点（TASK-015）：暂停事件 `MEMORY_CONFIRMATION_REQUIRED` 持久化整份 proposal 的 canonical hash；确认时复核该 hash 与原 source/submission、展示文本及期限，不能仅在可变提议中自检 candidate hash。结果追加 `MEMORY_CONFIRMATION_RESOLVED`，source_event_id 固定 interrupt UUID，同事务写 resolution_json，并以 message.delta 投递；ContextBuilder 将这条新 Runtime 结果作为后续模型可见的消息，不重写原工具结果。command_id 使用 interrupt UUID 与本次真实 USER_MESSAGE UUID 绑定永久消费身份；输入原 message_id 另外保留，幂等重放复用同一 canonical 事件。

真实确认需要冻结 TTL 与配额，TASK-015 已将 Run 的 memory 分组完整序列化并用同一设置 schema 严格读回，避免原五字段载体丢掉 TTL/配额而悄悄用默认值。TASK-016 仍负责工具历史预算、job快照及冻结能力的最终验收；不以当前完成的 Run memory 接线替代该任务。

显式命令 /memory remember <key> <category> <value> 可直接绑定用户对完整参数的保存意图：Runtime 在真实 USER_MESSAGE 上做严格语法解析，执行同一事务服务，模型不能设置此证据。USER 的 confirmation_event_id 直接引用该真实命令 USER_MESSAGE；参数不匹配的 remember 仍需要候选确认。命令解析与自然语言确认是两种用户入口，共用写入契约，不是旧协议兼容层。

**Gateway 调度触点**：把 `/memory` 加进入站 `COMMAND_PREFIXES`，使它和 `/new`/`/stop` 一样**不排进长流队列**（`inbound.py:155` 的既有性质）；但 Gateway **不解析参数、不判定语义**，只把它当普通入站消息转给 Runtime，权威解析仍在 Runtime 的真实 USER_MESSAGE 上做。前缀分类只绕过 Gateway 长流排队，不绕过 Runtime 会话忙态：RUNNING/WAITING_TOOL 时不能据此另建同会话 Run，返回 RUN_BUSY 并清楚告知重试；WAITING_INPUT 则仍只走合法 resume。不得宣称加前缀即可在任意忙态保存成功。不加进该元组也能跑（未匹配前缀的消息按普通消息下发），代价是"长流进行中输入 /memory 要等锁"——这与 `/stop` 曾经饿死是同一类故障，因此列为必改而非可选。

确认有效期是 min(Run 现有 deadline, 提议建立时间+300 秒)。重复同 message_id 重放原结果；发生 key 版本变化必须重新展示新候选，不能接受旧批准。forget 同事务递增 scope.forget_epoch，逻辑使此前所有未消费提议失效；不持 scope 跨 Run 修改 interrupt。确认持锁复查 epoch/tombstone/version，旧提议绝不复活刚删除的记忆；允许无关删除也要求重新提议，以换取简单一致的锁协议。

#### 3.4.3 唯一事务服务

固定锁序：Conversation（若涉及 canonical/Run）→ Run → Interrupt/Job（若涉及）→ MemoryScope → UserMemory。纯管理修改只取 Scope/Memory，不在拿到 scope 后新增等待 conversation 锁；会写 canonical 的 job 提交先取 conversation 锁。所有网络/model IO 在事务外。

一次命令：
1. 建立受信 MemoryWriteContext；幂等指纹检查。
2. 按锁序取得 scope，锁后重查 receipt、key/version、证据与冲突状态。
3. 计算清扫、淘汰、整批候选的写后集合；禁止低可信覆盖；只在容量增长路径施加冻结额度，纯删除/减容不因旧低额度拒绝。
4. 配额不足或任何策略拒绝则整体拒绝，既有事实与历史不变。
5. 执行状态修改并取得真实 id/version；插完整 history；scope revision +1；写 receipt；提交。
6. 提交后再发回执和成功指标；数据库异常映射明确错误并回滚，不吞掉。

DB 锁不是分布式事务替代品：Console 不直接写 runtime 表。Console 的配置审计不能被宣称与 Runtime HTTP 同事务；最终记忆 actor/版本审计以 Runtime history 为权威，Console 本地操作日志关联 command_id。

#### 3.4.4 同 key 冲突矩阵

| 写者/现状 | ACTIVE AGENT | ACTIVE USER/ADMIN | DISABLED | DELETED tombstone |
|---|---|---|---|---|
| 自动 AGENT / consolidation | 仅期望版本与依据边界有效时更新 | 跳过，不降权 | 跳过，不启用 | USER/ADMIN 删除必跳过；自动退休仅新依据可恢复 |
| 新用户候选确认 | 依展示的版本覆盖，升级 USER | 依展示的版本覆盖 | 明确告知重新启用后确认 | 明确告知重新记住后确认；必须是新提议 |
| ADMIN 显式修改 | 按 expected_version 修改 | 同左 | 显式 ENABLE/RESTORE | 显式 RESTORE；保留 identity/version |

ENABLE/RESTORE 不是自动延长 TTL；过期条目需显式给出新有效期或新确认。普通 ADMIN 事实才 author=ADMIN，管理员启禁用 AGENT 并不把原内容伪装为管理员确认。TASK_STATE 的自动写仍保留 AGENT。

每条真实 USER_MESSAGE 入站落库时记录 source_scope_revision（scope 不存在为0）；这是事件的不可变元数据，不使用模型输入的数字。读取故障用 savepoint 回滚该次查询，保留真实入站并记录 revision=null、warning/ERROR，不把失败伪装为0；缺失依据不得自动恢复。若并发读取导致较旧 revision，只能保守拒绝自动恢复，不能放宽。

EXPIRE / QUOTA_EVICT / TASK_COMPLETE 是自动退休，不等同用户删除。恢复必须绑定真实新 USER_MESSAGE 的 source_scope_revision ≥ retirement_revision；该 revision 在消息入站落库时记录，不允许 claim/model 开始时补成当前值。无新消息、旧 Run 延迟 job 或依据字段缺失均不得恢复。USER_DELETE/ADMIN_DELETE 不接受任何自动恢复；用户新确认或 ADMIN 显式 RESTORE 才可解除。

#### 3.4.5 内容闸门与配额

校验 key、value、quote、TASK_STATE 全部字符串及待持久候选；拒绝已知密钥格式、敏感键值（password/api_key/token 等）及本次执行已知凭据 canary。不要声称正则能识别所有自然语言里的秘密。来源证据无豁免，异常和拒绝回执不回显敏感原文；记忆内容禁止用于表达实时权限或替代执行授权。

配额计算对象为未删除且未过期集合，disabled 同样占额；content/quote 用唯一 canonical_json/UTF-8 口径。先清扫过期，再以低可信、较少命中、较旧依据的确定性顺序淘汰 AGENT；没有可淘汰项仍超限就拒绝。一个事务清扫/淘汰条数受事实绝对上限 1000 限定，不引入“最多50条清理后继续越限”的逃生口。

USER/ADMIN 超限回 MEMORY_QUOTA_EXCEEDED，告知删除旧条目或由管理员调整配额。在线只允许提高事实配额。降低条数/字节额度必须进入维护窗口，排空持有旧额度的 Run/Task/job 后锁内清理可淘汰 AGENT、预检已确认集合，再保存新设置；无法满足新额度则拒绝，不改历史快照，不建立热更新容量例外。

提高额度后，旧 Run 可能读到新 Run 合法写出的更大集合：读取只检查绝对上界（1000条/1MiB），不用旧冻结额度报错；旧 Run 的冻结额度仅限制新增容量。若现有集合已超过该旧额度，旧 Run 允许 DELETE/EXPIRE/减容，但拒绝任何容量增长，且不为追平旧额度额外淘汰既有事实。无论新旧 Run，纯治理减容不会因为余额已超旧限被阻止。批量配额拒绝时仍整体回滚。

### 3.5 读侧与预算

#### 3.5.1 完整候选与排序

MemoryReadFilters 统一 tenant/user、删除、enabled、TTL。Console 状态筛选有意允许查看 disabled/expired；history 单独看审计，不把管理可见性与模型可见性混为同一个过滤器。

一次 snapshot SQL 读取全部有效候选与数据库 statement_timestamp()/scope revision。返回数量超过绝对1000条或有效事实字节超过绝对1MiB才是存储不变量故障，本轮不注入并告警；旧 Run 冻结的较小额度不用于候选截断或读故障判断，不以 LIMIT 隐藏。普通事实与 TASK_STATE 先分消费通道：

| 通道 | 选择规则 |
|---|---|
| 偏好注入 | USER/ADMIN 的 PREFERENCE/WORK_STYLE，与 query 无关；最近确认、稳定 key 排序；按预算可丢，不能承诺200条偏好同时生效 |
| 相关事实注入 | EXPLICIT 已确认事实需非空 query 且 coverage>0；普通 AGENT 还须 inject_inferred=true |
| recall SEARCH | 非空 query 先 coverage>0，再以 coverage、来源可信度、确认时间/更新时间、key 为字典序排序；零命中空 items |
| recall LIST | query 缺省/空白，按更新时间与 key 列表；不计搜索命中 |
| 任务续接 | 仅明确继续请求且 task_state_injection_enabled；见下一节 |

rank/selection 为纯函数，now 由调用方传数据库时刻，author/category 为严格枚举。不再用一个 soft weight 承诺 USER 永远压过 AGENT，写入权限与检索分数分离。词法函数采用 CJK bigram + latin word；不承诺同义词或纯单字 CJK 完整匹配，测试固定实际边界。

**热路径成本与缓存边界（B-23）**：保留按 scope revision 缓存候选行。缓存仅存当前 Run 内的有界候选快照，键为 (tenant_id,user_id,revision)，不缓存排序/最终 SYSTEM 块/当前时间，不是跨进程权威或平台设置缓存。

- 冷缓存直接用一个 SQL snapshot 读取候选、实际 revision 与数据库当前语句时刻；暖缓存每模型调用读取同一语句内的 revision + statement_timestamp()。revision 相等才复用行集合，当次重新过滤 expires_at、选择、渲染与算预算；缺 scope 明确为 revision=0，不拿其他用户条目补齐。
- revision 变化时，再查完整 snapshot，并以完整查询实际返回的 revision 标记缓存；禁止“先读 R、后取候选 S，再把 S 标为 R”。完整 snapshot 前后跨并发提交时，单语句一致性保证行集合与其 revision 同源。
- 所有写者（确认、forget、Console、TTL/配额退休、job）在事实事务内递增 revision。轻量读取失败或完整读取失败即清除缓存/组件并计 ERROR，本轮不注入，不回退旧值。
- TTL 使用当次数据库语句时刻，不能连 now 一起缓存，也不能复用跨模型调用的长事务起始 now()。线性化边界是当次 revision/snapshot 语句；该边界后并发提交的删除属于重叠操作，下一调用再观察，不声称撤回已在发送的请求。
- 正常暖路径每调用一次轻量查询；首次冷读一次完整查询，revision 改变时增加一次完整查询。进程恢复/缓存失效可再次完整查询，不承诺整个 Run 无论故障都只查一次。注入被0预算关闭时不做这些注入查询，recall 的独立读取不受影响。

缓存实现与 B-23 的并发、TTL、失败断言一起交付；它细化 §3.2.2 的刷新实现，保持该节线性化边界与 INV-06/07/09，不以性能收益改变可见事实。

#### 3.5.2 任务续接闭环

TASK_STATE 由 consolidation 写入，未过期、未禁用、非 tombstone 才可读，明确标注“会话提炼的未确认线索，请核对”。

- 当前真实用户输入含明确继续意图时进入任务通道；规则在纯函数与提示契约中固定，含继续/接着/上次/昨天任务及英文 continue/resume 的完整请求形式，不能仅见一个词就假定唯一任务。
- 带具体主题：从目标/标题按 coverage>0 匹配；无匹配返回找不到线索，要求用户给主题，不随意取最新。
- 泛指“继续上次任务”：只有一条未决 TASK_STATE 可带入；多条（含主题匹配多条）给有界任务标题/key 清单，并落 Runtime 生成的 MEMORY_RESUME_OPTIONS 事件，包含候选 key/version/编号/hash/300秒 expiry；清单由 Runtime 确定格式及编号后作为持久事件直接投递 Gateway，不依赖模型改写顺序；零条明确说明没有可用线索。
- 下一条真实用户消息先检查未消费的 options；“第2个”/完整 key/唯一标题可确定选择，无须再次出现继续词。重新查询有效性与 version，单次消费 options；过期、已删除或版本变化要求重列，不依赖模型自主 recall。消费/取消用追加事件引用 options_event_id，数据库唯一约束保证同 options 最多一个最终结果，原 options 事件不修改。输入只匹配完整选择语法，不从含否定词的句子中抽编号；无关消息取消待选，避免把普通对话误当确认。
- 用户在待选期间给出完整的新续接请求时，先取消旧 options，再按本次真实 USER_MESSAGE 的主题和有效快照重新匹配；不把新主题当旧编号选择。options/hash 或受信引用损坏记录 warning/ERROR，拒绝带入旧任务；损坏清单消费为 EXPIRED 并重新列出有效候选。
- 唯一选择在 conversation 锁内落统一 MEMORY_RESUME_RESOLVED（outcome=SELECTED，Runtime 写，模型不可伪造），包含原 key/version/root_conversation_id/真实 USER_MESSAGE 引用；若尚无 options，先在同事务建立仅一项 options，再写结果。所有 SELECTED/CANCELLED/EXPIRED 结果以 source_event_id 引用同一 options 事件，由既有唯一约束互斥。来源 job 固定该引用并原子更新原 checkpoint；继续会话不新增 task.B。完成时退休同一原 key，故 A→B→C 仍只有一个未决任务。
- TASK-014 已提供 canonical 引用与真实 Runtime 请求注入，跨会话保留原 key/root；job 固定输入、原 checkpoint 更新与完成退休由后续 consolidation 任务实现，最终 Gateway/Runtime E-05 由 TASK-030 登记并留到需求终验。
- 即使是一个匹配也不把模型提炼当已授权执行：先核验真实任务、现有授权与待办是否仍有效。
- /new 只清会话，不删除 T2；自然语言续接由装配器读取，不依赖模型自愿 recall。
- 明确续接时先装目标任务/澄清清单，再填偏好；其他请求先偏好。没有比例预算，完整块仍受同一硬限。
- 任务状态本体装不下时先尝试有界引用行 {title,key,version,omitted:true}，也计完整块条数与字节；能投递引用时提示用 recall 精确 key 获取详情。引用也装不下则省略并计 bytes dropped，不承诺模型能收到提示或续接；0预算始终无块。

#### 3.5.3 完整渲染预算

试装条目时渲染完整 SYSTEM 块，计 UTF-8 字节，包含 notice、分区、key/version、换行。相等可进，大于拒绝；装不下的长条跳过继续尝试短条。max_injected_memories/bytes 任一为0则无块，无占位。

notice 是一次性的非指令说明；confirmed / inferred / task_state 分区明确。没有首条越限保底；每条只计一个 dropped reason（count / bytes），策略不适用条目不计预算丢弃。

recall 同样对最终 JSON 计预算并带 truncated 元数据；limit=0 返回空不算 SEARCH miss。有效 SEARCH query 有库候选但全不匹配才记 miss；数据库错误记 ERROR，不伪装 miss。LIST、SEARCH、TASK_RESUME 指标分开。

### 3.6 consolidation 生命周期

#### 3.6.1 入队与执行

1. 仅 COMPLETED Run 满足冻结开关/用户消息阈值时入队；WAITING_INPUT/WAITING_TOOL/FAILED/CANCELLED 不入队。
2. 主终态 CAS 事务内以 savepoint INSERT 唯一 job，记录 through_seq、snapshot_id、当时读取的 enqueued_scope_revision 与最后受信 resume 引用。job 入队失败回滚该子事务，主终态仍提交，warning + metric；异常 DB 情况下不保证额外失败事件能写。
3. 后台 claim 使用 FOR UPDATE SKIP LOCKED；分配 lease_owner/claim_epoch/attempt，提交后才开始外部 IO。允许任意 Runtime 接管，HTTP/SSE 断流不影响 job。
4. 首次 attempt 只选 summary.seq ≤ through_seq 且 covers_up_to_seq ≤ through_seq 的最新权威摘要，随后读取覆盖边界之后、through_seq 之前的用户/assistant 文本；禁止使用当前无边界 latest_summary。无合格摘要则读该边界内原文；工具结果全文不作为事实输入。脱敏后超预算则 SKIPPED(INPUT_TOO_LARGE)。
5. 读取 memory snapshot，revision 若不同于 enqueued_scope_revision 则模型前 STALE。第一次构建的 input_json/hash/input_scope_revision/摘要边界原子存 job，epoch/lease 校验后才调模型；后续 attempt/接管原样复用，不更新依据或 revision。收集稳定 key 的禁用/退休约束。模型提炼普通 AGENT 候选与至多一个当前任务 lineage 的 TASK_STATE，不能指定 author/tenant/user/TTL。
6. 创建独立 audited provider，模型定义来自该 Run 主模型 snapshot，api_key 按冻结 model_id 经 API-09 实时读取；独立总 timeout/output 上限；finally 关闭。
7. 模型输出严格为 `{candidates, task_completed}`；普通 key 由服务规范化为 `infer.*`，TASK_STATE 必须保持受信 root/key 和当前 source conversation。要求正常 stop、候选数在冻结上限内且 key 唯一；完成标志与 TASK_STATE 候选不能并存。任何形状或禁存内容失败整批 REJECTED；本版不采用“坏项跳过好项提交”。
8. 进入短提交事务按锁序拿 conversation/run/job/scope，核对 job epoch/lease/终态、input_scope_revision、逐 key 冲突、through_seq 依据。
9. scope revision 已变则整批 STALE，不用旧结果覆盖；重新 claim 不得刷新旧输入冒充同一次结果。该保守策略也会跳过因其他 key 或前驱 job 修改导致的冲突，允许提炼缺失并计数，不能假称没有误跳过；未来新 Run 可产生新 job。
10. 应用整批允许候选，写 history、scope revision、命令回执、MEMORY_CONSOLIDATED 与 job DONE/SKIPPED；统一提交。

模型输出重复 key、非法 TASK_STATE source_conversation_id、普通事实占 task.* 均拒绝。任务 root/key/resume_ref 与来源 Run 的受信选择不一致同样拒绝。自动普通 key 由候选规范化 key 加 infer. 命名空间限定，不能通过模型改名覆盖已确认事实；USER/ADMIN 删除墓碑对同 key 永久有效，自动退休按新入站依据校验；语义改写的全局遗忘不在本版保证范围内。

#### 3.6.2 顺序与恢复

同一 conversation 的 job 按 through_seq 串行完成，后继不能越过仍在 RUNNING/RETRY 的前驱；终态 FAILED/SKIPPED 可放行后继。TASK_STATE 在同 conversation 比 source_seq；跨 conversation 只凭 MEMORY_RESUME_RESOLVED(outcome=SELECTED) 的 expected_version/root 引用更新同一原 key。新结果不能被旧 through_seq 或旧续接引用覆盖。

job 模型调用可因 SIGKILL/lease 接管重复；写入效果原子最多一次。提交后、回执前崩溃，下一执行者读到终态重放结果；提交前崩溃不留下部分事实。lease 到期的旧执行者无权写 FAILED 或 DONE，失败记录同样校验 epoch。

可重试基础设施错误到 max_attempts，坏形状/策略拒绝/STALE/输入超限立即终态。达到上限时写 job FAILED 和失败事件（若可写），warning + metric；DB 持续不可写靠日志和指标告警，不能写出假的成功事件。

完成 payload 只含 written/skipped/candidates/reason/attempt/through_seq 等计数与依据 ID，不含 value/quote。reason ∈ NONE / NO_CANDIDATES / POLICY / STALE / INPUT_TOO_LARGE / REJECTED / FAILED / DISABLED。

### 3.7 API 与 Console

| 端点 | 契约 |
|---|---|
| GET /api/v1/users/{user_id}/memory | author/category/status；分页 items/page/page_size/total；ACTIVE/DISABLED/EXPIRED；不返回 deleted，用户存在检查按 tenant |
| GET /api/v1/users/{user_id}/memory/{memory_key}/history | 只读分页，按 version DESC；deleted 的历史仍可查 |
| POST /api/v1/users/{user_id}/memory | ADMIN 人工内容；Idempotency-Key 必填；授权后转 Runtime，不能自选 author |
| PATCH /api/v1/users/{user_id}/memory/{memory_key} | enabled/expected_version；明确过期与恢复语义，版本冲突不覆盖 |
| DELETE /api/v1/users/{user_id}/memory/{memory_id} | 指定 id/expected_version 删除；保留 identity/history；mutation command 防重 |
| DELETE /api/v1/users/{user_id}/memory | 一命令原子清空并保留逐项历史；撤销所有 pending 提议 |
| POST /internal/admin/users/{user_id}/memory/commands | Console → Runtime 唯一管理写入口；服务身份 + 已鉴权 actor/tenant，规范化指纹与幂等回执 |
| GET/DELETE /internal/admin/users/{user_id}/memory | 既有管理入口必须统一转同一查询/事务服务，DELETE 不再独立 SQL 更新 |
| recall | query?/category?/memory_key?/limit?；memory_key精确过滤；返回notice/items/truncated及每项author/explicit_request/version/updated_at/expires_at |
| forget | memory_key/expected_version；WRITE、默认 SERIAL；服务从当前 scope 绑定身份，未知 key 明确 NOT_FOUND |

内部 API 使用 api-kit 服务身份、Envelope 与错误目录；不靠仅由调用方填写的 author/tenant 头授予权限。Console memory 页面复用现有详情 Tab，显示“已确认用户 / 管理员 / 推断”、状态、TTL、版本历史；author 与 actor 分开显示。

错误码包括现有 key/value/category 错误、MEMORY_QUERY_INVALID、MEMORY_QUOTA_EXCEEDED、MEMORY_FORBIDDEN_CONTENT、MEMORY_NOT_FOUND、MEMORY_VERSION_CONFLICT、MEMORY_CONFIRMATION_EXPIRED、MEMORY_CONFIRMATION_INVALID、MEMORY_CONFIRMATION_PENDING、MEMORY_QUOTA_REDUCTION_REQUIRES_MAINTENANCE、MEMORY_RESULT_BUDGET_EXCEEDED。删除 source_type / prefix 旧错误；所有新码在 ErrorCode 与 api-messages.yaml 双向注册、本地化，不回显禁存内容。

### 3.8 可靠性与观测

| 失败点 | 明确行为 | 可见性 |
|---|---|---|
| 注入查询/刷新 | 本轮无 memory component，不使用旧缓存 | warning + memory_inject_total{outcome=ERROR} |
| 证据缺失/确认无效 | 不授予 USER；提议拒绝或等待真实输入 | memory_confirmation_total{outcome} + 可读回执 |
| 配额/清扫/历史失败 | 整批回滚，不成功保存，不继续超额 | memory_write_total{outcome} + warning/错误 |
| 入队失败 | savepoint 回滚，Run 终态不变 | warning + memory_consolidation_enqueue_total{outcome=ERROR} |
| 模型/批量提交失败 | job 重试或终态失败，Run 不变 | warning + memory_consolidation_total{outcome} |
| owner revision/epoch/lease 冲突 | STALE 或失去执行权，不写事实 | memory_consolidation_total{outcome=STALE/FENCED} |
| TTL 到期 | 读时立即过滤；后台 sweep 用同一事务协议写 EXPIRE history | memory_expiry_total{outcome} |

新增/修订指标：memory_inject_dropped_total{reason=count|bytes}、memory_recall_hit_total{hit}（仅 SEARCH）、memory_recall_total{mode,outcome}、memory_confirmation_total{outcome}、memory_consolidation_enqueue_total{outcome}、memory_consolidation_total{outcome}、memory_quota_evictions_total、memory_expiry_total{outcome}、memory_write_conflicts_total{reason}。指标定义于 Runtime catalog，空流量也暴露；无用户/key/UUID 等高基数 label。

日志统一 extra 字段，仅写关联 ID、数量与错误码；value、quote、完整候选、prompt 不进日志。remember 候选在通用 tool_call_audit 落库之前即执行特殊脱敏，只保存 key/category/hash/状态，避免 handler 拒绝之前原参数已进入审计。history/job/proposal/receipt/tool audit 与模型 consolidation 输入均纳入 canary 反查。TTL sweep 每次锁一个 scope、处理数量有界，禁止占锁调用 Console 或模型。

## 4. 部署、迁移与运维

### 4.1 发布前置条件

本次是明确的维护窗口切换，不支持新旧 memory 写协议滚动共存。
1. 停止接收新 Run 和管理写入；继续提供必要的读取与等待状态。
2. 排空 CREATED/RUNNING；WAITING_INPUT/WAITING_TOOL 及未完成 Task 若携带旧预算必须完成或由运维明确取消，不能静默改快照或冒充已完成。
3. 停止 dev/后台写服务；确认无旧执行者、未提交确认或 consolidation job；备份并记录数据库/设置版本。
4. 预检超额用户、同 key 多 incarnation、旧设置和 Agent runtime_config。未经显式处置的已确认事实不被自动删。

### 4.2 一次性迁移与切换

迁移 revision 0020 为单次维护迁移；具体可在同窗口拆脚本步骤，但中间 schema 不对业务开放。
- 每个 key 只写一条当前状态 MIGRATION_BASELINE，canonical version 为该 key 存量最大 version；legacy_snapshots_json 保存全部旧 incarnation 的原 id/version/完整行（含 source_type、enabled/deleted）。选择当前活跃 incarnation，或没有活跃行时最新已删 incarnation 作为稳定 id，其他旧行归档后移出活跃表。
- 基线 after_json 是当前选择行在新 schema 的对应状态，旧 incarnation 的相同 version 只放 legacy_snapshots_json，不产生冲突的 canonical history；新变更从 canonical version+1 起。来源/TTL 等变更随后按正常递增版本写历史；不捏造迁移前的变更序列。所有旧禁存内容先预检，发现密钥则停止迁移并按显式清理方案处理，不把秘密扩散到基线。
- 无真实确认证据的存量来源一律 AGENT；explicit_request=false，保留旧来源仅作历史说明，不能用自报 USER_EXPLICIT/EXPLICIT 迁成已确认 USER。
- 以旧 update_time + 对应 TTL 回填 AGENT expires_at；已经到期的行在迁移中写 EXPIRE。不等待下一次覆写；用户原话/管理员的真实确认需要可证明记录才能保留其级别。
- 创建 scope/history/receipt/job、run_interrupt proposal_json、完整 key 唯一/候选/到期/claim 索引与 ORM 同构；删旧查询索引、旧 partial key 唯一与 source_type。
- 当前平台设置通过单次迁移插入新 revision：只移除旧 compaction.memory 分支并添加新字段，保留其他值、旧设置版本与 RESTORE 审计；当前配置只读取未删除版本。Agent runtime_config 同步移除旧 ratio 配置，递增 revision 并记录 UPDATE 审计；已删除 Agent 与终态快照原样保留。
- 终态旧 Run/Task snapshot 只保留审计原样，不重新执行；在途对象已于前置条件排空。快照 schema 不用“失败返回 None”绕过协议不兼容。
- 若旧 AGENT 超配额，按明确排序清理并写历史；不可静默升级或删除有可证明确认的事实。预检不通过则回滚迁移、保持维护窗口。

部署新代码后完成 migration/schema parity、设置读取、新 Run 创建、确认、forget、后台 job smoke，再开放流量。普通测试/验收仍独占临时库，不在 dev 库清行。

### 4.3 回滚

开放流量前失败：停止新代码，在同一维护窗口从备份恢复 schema/数据/设置及旧代码，并复查版本，不声称 DROP 新列即可无损恢复旧语义。

开放流量后有新确认/历史/job：优先修复新协议；若必须回退，停写并导出新版本变更，制定可审查的数据恢复方案，不能直接回旧代码丢弃新事实。本文不承诺无损在线代码回滚。

### 4.4 运维信号

- SEARCH miss 与实际相关性抽样结合评估词法能力；LIST 非空不计检索成功。
- bytes/count dropped 持续增长说明装配预算不足；不能用提高事实配额替代注入预算。
- STALE/FENCED 增多说明并发治理或租约预算需检查；不得取消 fence 以提高“成功率”。
- history/tombstone/job/receipt 单独监控行数与存储量；审计清理另有策略，不复用活跃事实配额。
- 默认 consolidation 每成功 Run 增加一次有界模型调用，成本与后台延迟可观察；关闭后偏好和手工 recall 仍正常，跨会话任务线索停止新增。

## 5. 风险与明确取舍

| 风险 | 当前处理 | 不能声称的性质 | 验收 |
|---|---|---|---|
| 自然语言无法确定保存意愿 | 真实候选确认；显式命令可直存 | “词法匹配就是用户明确要求” | B-01,B-15,E-02 |
| 确认增加一次交互 | 候选完整展示，复用现有中断 | “完全无交互且来源可证明” | E-02 |
| 同义词词法不匹配 | 明确 SEARCH miss 与澄清 | “纯函数换函数体即可无成本升级向量检索” | B-02,B-06 |
| 任务提炼缺失/幻觉 | 未确认分区、TTL、实时状态核验 | “AGENT 线索就是权威任务结果” | E-05,S-02 |
| scope 锁热点 | 短事务、无锁内 IO、有界集合 | “无锁也能保证用户总配额” | B-03,B-04 |
| 审计容量持续增长 | 独立容量监控与后续治理 | “200条事实配额同时限制全部审计存储” | 运维容量检查 |
| 删除的语义边界 | key tombstone、当前组件刷新 | “软删能擦除已发送会话/所有语义改写” | B-07,B-16 |
| 容量规则降低影响旧写者 | 降低额度仅维护窗口，在线只提高 | “旧快照硬配额与新租户硬限额可互不协调” | B-04,B-12,B-18 |
| 维护窗口 | 一次迁移，备份与预检 | “不保留兼容层仍可新旧写协议在线共存” | B-14,B-20 |
| 模型/DB 不可用 | job 状态、warning、metric | “失败事件在不可写DB里必然落库” | B-11,E-06 |

## 6. 需求追溯与规范承接

| 功能 | 用户故事 | 关键场景 |
|---|---|---|
| FEAT-01 | US-07 | E-01，规范矩阵 |
| FEAT-02 | US-02,US-05 | B-01,B-15,E-02,S-01 |
| FEAT-03 | US-05 | B-03,B-14,B-17 |
| FEAT-04 | US-01,US-03 | B-02,B-08,B-23,E-03,E-05 |
| FEAT-05 | US-01,US-06 | B-07,B-09,B-23,E-01 |
| FEAT-06 | US-01 | B-06,S-01 |
| FEAT-07 | US-02 | B-07,B-16,E-04 |
| FEAT-08 | US-04 | B-04,B-05,B-18 |
| FEAT-09 | US-03,US-06 | B-10,B-11,B-16,B-19,B-21,E-05,E-06 |
| FEAT-10 | US-06 | B-12,B-20,B-22 |
| FEAT-11 | US-06 | B-13,B-14,B-20 |
| FEAT-12 | US-02,US-06 | E-04,S-01 |
| FEAT-13 | US-07 | Context required rules + 文档/目录一致性验收 |

### 6.1 Spec Compliance Matrix

这是待承接矩阵，不是 Gate 裁决。规范与现有设计冲突必须在本需求 Context 中显式修订并验证。

| Spec / Rule | 设计落点及验证 | 必需承接 |
|---|---|---|
| harness-api#RULE-api-001 | §3.7 API 与 Console；元数据 verifier ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | TASK-023 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-api#RULE-api-002 | §3.4.3 唯一事务服务、§3.3.1 memory_scope（新增）、§3.3.3 user_memory_history（新增，append-only）、§3.3.4 memory_command_receipt（新增）；元数据 verifier ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | TASK-009 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-arch#RULE-arch-001 | §3.8 可靠性与观测、§3.6 consolidation 生命周期；元数据 verifier ["uv","run","pytest","-q","tests/architecture"] | TASK-022 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-skill#RULE-skill-001 | §3.5.3 完整渲染预算、§3.2.2 模型请求组件；元数据 verifier ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] | TASK-006 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-data#RULE-data-001 | §3.3 数据设计、§4.2 一次性迁移与切换、§4.1 发布前置条件；元数据 verifier ["uv","run","pytest","-q","tests","-k","schema_parity"] | TASK-007 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-snapshot#RULE-snapshot-001 | §2.3.3 配置项、§3.2.2 模型请求组件、§4.2 一次性迁移与切换；元数据 verifier ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | TASK-016 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-secret#RULE-secret-001 | §2.5.2 验收场景与不得 mock 的边界、§3.8 可靠性与观测；元数据 verifier ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | TASK-029 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-test#RULE-test-001 | §2.5.2 验收场景与不得 mock 的边界；元数据 verifier ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | TASK-030 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-ui-detail#RULE-ui-detail-001 | §3.7 API 与 Console；元数据 verifier ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | TASK-028 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-ui#RULE-ui-001 | §3.7 API 与 Console；元数据 verifier ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | TASK-028 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-frontend#RULE-front-001 | §3.7 API 与 Console；元数据 verifier ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | TASK-026 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-i18n#RULE-i18n-001 | §3.7 API 与 Console、§6.1 Spec Compliance Matrix；元数据 verifier ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | TASK-027 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-time#RULE-time-001 | §3.3 数据设计、§4.2 一次性迁移与切换、§4.1 发布前置条件；元数据 verifier ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | TASK-007 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-model#RULE-model-001 | §3.6.1 入队与执行、§3.6.2 顺序与恢复；元数据 verifier ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | TASK-019 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-log#RULE-log-001 | §3.8 可靠性与观测、§3.4.5 内容闸门与配额；元数据 verifier ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | TASK-018 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-mcp#RULE-mcp-001 | §3.8 共享ToolRegistry pipeline的schema失败回执只含安全错误，避免remember原实例泄漏；MCP仍按冻结catalog走统一pipeline，不改变transport/Server授权边界；元数据verifier ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] | TASK-018 范围增量承接；原MCP规则保持，另执行共享pipeline回归 |
| harness-im#RULE-im-001 | §3.4.2 remember 与真实确认、§3.5.2 任务续接闭环；元数据 verifier ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | TASK-017 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-im#RULE-im-002 | §3.4.2 remember 与真实确认、§3.5.2 任务续接闭环；元数据 verifier ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | TASK-017 唯一责任；保留 code verifier stage，不假称已执行 |
| harness-memory#RULE-memory-001 | INV-01 / RULE-01/12；真实来源与候选确认；verifier命令见任务书 Spec Responsibility | TASK-015 唯一责任；stage=review，TASK-001已创建并增量绑定，尚未执行 |
| harness-memory#RULE-memory-002 | INV-02/03/04 / RULE-03/04/08；事务身份与硬配额；verifier命令见任务书 Spec Responsibility | TASK-009 唯一责任；stage=review，TASK-001已创建并增量绑定，尚未执行 |
| harness-memory#RULE-memory-003 | INV-06/07 / RULE-05/06/07/11；候选、缓存和请求预算；verifier命令见任务书 Spec Responsibility | TASK-012 唯一责任；stage=review，TASK-001已创建并增量绑定，尚未执行 |
| harness-memory#RULE-memory-004 | INV-05 / RULE-09/13；删除屏障与来源冲突；verifier命令见任务书 Spec Responsibility | TASK-021 唯一责任；stage=review，TASK-001已创建并增量绑定，尚未执行 |
| harness-memory#RULE-memory-005 | INV-08/09 / RULE-10/15；固定输入、fence与失败可见；verifier命令见任务书 Spec Responsibility | TASK-021 唯一责任；stage=review，TASK-001已创建并增量绑定，尚未执行 |
| harness-memory#RULE-memory-006 | INV-10 / RULE-14；冻结配置与维护迁移；verifier命令见任务书 Spec Responsibility | TASK-007 唯一责任；stage=review，TASK-001已创建并增量绑定，尚未执行 |

同步 docs/02 §4.23、docs/04 §10/§11、docs/07 管理 API、docs/15 Console 字段、configuration-inventory；path_mapping 为新 memory 载体精确匹配。新增规范只描述目标要求；既有规则的事实性口径随对应代码/Schema变更同步改写并refresh，不提前宣称已实现。

### 6.2 设计进入 Plan 的条件

- 建立并验证本需求 Spec Context；确认 required rule 修订已承接，不伪造 binding 或 Gate pass。
- 对 source/confirmation、锁序、stable identity、配额提高/降低边界、job fence 做独立设计复核。
- 把每个场景转成真实 Acceptance Contract，补 browser domain 与 inventory；运行命令为 make acceptance、make acceptance-e2e DOMAIN=agent（新增域配置时同步修改契约）。
- 实现完成前所有本文验收均为 planned；只在真实执行后记录 Evidence，Done 结论以 finish 实际返回为准。

## 附录：术语

| 术语 | 含义 |
|---|---|
| author | 内容来源/确认级别；USER 只由真实候选确认产生 |
| actor | 执行本次修改的人或自动任务；与 author 不等同 |
| memory_scope revision | 用户记忆聚合的提交版本，用于批量冲突检测 |
| version | 单 key 的连续修改版本，跨软删/恢复不重置 |
| tombstone | 用户/ADMIN 删除 key 的保留身份，自动写不可解除；自动退休另按新入站依据处理 |
| TASK_STATE | 非权威、短 TTL 的会话任务续接线索 |
| confirmation | 绑定完整候选与版本的真实用户输入，不是词法猜测 |
| job fence | claim epoch、lease 和终态共同限定提交权 |



