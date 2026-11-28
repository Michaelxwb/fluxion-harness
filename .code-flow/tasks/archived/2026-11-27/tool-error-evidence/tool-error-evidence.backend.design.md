# 工具失败事实与 Run 归因 · 后端设计

2026-11-27，供评审。对应 PRD FEAT-01 至 FEAT-05。

## 所有权与数据流

Worker 继续裁定业务错误并返回现有封套。Runtime 的 WorkerTaskClient 在 HTTP 错误分支将封套转换成有界的公开事实；复用 AppError.data 承载已校验的字段，在客户端边界以专用的 Worker 响应异常保存安全消息及其来源。保留 AppError 的既有捕获语义（包括 task_tools 中的清理路径），共享 AppError 仍遵循 API 词条约定。BackgroundTaskToolSet._wrap 只转换一次，传给 TaskToolError。成功响应走原解析路径。

复用 ToolError 的 content()/outcome() 双出口。两者读取同一份公开事实，前者写 role=tool，后者把证据交给 ToolCompletedEvent、FailureEvidence、checkpoint 与审计。禁止通过重新解析 tool JSON 或 evidence_preview 恢复字段。所有新增嵌套字段定义类型；仅在有失败时构造数据。

| 工具面字段 | 产生与消费 |
|---|---|
| code、message | Worker 稳定码与安全文本。未知码仅保留符合诊断标识规则且可安全展示的值；否则记录本地边界分类。 |
| detail | 由 Worker 现有字段错误形态驱动，包含受限的 field/fields、reason、repair、allowed_values、entry。按码声明允许组合、字段路径和枚举值；只读指定键，限制条目数、字符串与总字节。不能验证来源的自由文本舍弃。 |
| action_owner | 共享 failure_hint 的同一业务规则产生 USER、ADMIN 或 NONE；无法确定时省略。owner 表示能修复的人，不作授权依据。 |
| retryable、outcome_known | 明确禁止原样重试序列化 false，无依据省略。未知结果永不输出允许重试。Runner 的权限、等待及目标冲突判定仍由既有 control 决定。字段补正后发起新调用与原样重放分开。 |

安全文本只在收到的平台 msg 与对应 locale 已加载的共享 MessageCatalog 公共词条（含已校验的安全参数）一致时标记为平台返回。不同语言、无法验证或越界的文本使用当前 locale 的安全描述并标明“诊断摘要”；不得把截断视为脱敏。禁写原始非 JSON 正文、请求参数和鉴权信息。远端返回合法的预检拒绝才确认该调用在写入前失败。超时、断连、畸形成功响应依现有发送与结果事实进入边界处理；请求可能到达时保持结果未知。

## 提示、模型和用户文案

扩展 failures.py 的 FailureHint，规则按 code + 经验证的 reason/repair、精确码、已登记码族、未知依序匹配。API 词条提供错误描述，hint 提供面向用户的行动；不为每个工具再建立一张文案表。CI 以可达工具的 ErrorCode 集合及工具自声明稳定码作参数化用例，两种 locale 均应命中语义适用的规则。动态外部未知码进入现有缺失指标；指标标签不带自由文本。

| 输入事实 | 用户主消息和行动 |
|---|---|
| 已确认 draft_schedule 预检，CONTRACT_MISSING 或 CONTRACT_INVALID，REIMPORT_SKILL | 分别解释缺少配置或配置无效，由管理员重新导入，用户随后可再试。影响限于本次操作。只有相关写入均已排除才声明已有任务未变。 |
| 只有同一错误码 | 可以说明预检失败、具体配置原因未确定；不选择 missing/invalid，也不指定修复动作。 |
| 已确认业务值缺失 | 用对应语言追问缺失的业务值；内部字段路径只供诊断。 |
| TOOL_RETRY_UNSAFE | 同一门禁同时处理已禁止的原样调用与结果未知的写入。前者说明原请求不能重复；后者要求先核对结果。分不清时只说明重试已受保护。 |
| 未识别或证据不足 | 说明已知影响及不确定性。只有可查询的记录已落库才提示管理员可在 Run 中查看。 |

事实完整、允许继续时，模型从 role=tool 获得安全字段和动作范围，可追问或解释。修复建议不授予额外执行权。对当前未解决且只有码的失败，在该轮下一次生成自由文本或派发替代执行前，通过既有回复/工具派发边界交付确定性说明；已授权的结果核对和宿主 WAIT/STOP 仍优先。该分支不能伪造 NO_PROGRESS 或覆盖真实 Run 状态。硬停止统一走 render_finalization；检查 executor.failure_reply、run_service._finalize_failed、maintenance 以及流式输出入口，确保这类诊断文字在发送首个 delta 前已选定。保留先前已交付的真实进度，不能靠事后追加正确文案掩盖错误推断。

## 调用归因和持久化

1. Runner 作出门禁判断时拥有 attempt、宿主 write_operation_key、未核对目标和当前调用 ID。在现有阻止重试的状态项上保存来源 Run/调用身份；相同重复失败保留当前未解决尝试的首个来源。复用原键和生命周期，解除或重置状态时同步清理来源。未知写入的目标集合重叠仅能证明冲突；多个可能来源不选择其一。跨 Run 保护只展示允许访问的本 Run 事实，来源不可证时不追查其他 Run。
2. 在既有 no_progress 判定命中时冻结触发调用 ID，明确区分业务来源、门禁触发与 termination.reason。业务失败直接触发时可为同一调用；门禁触发只沿显式来源引用。既有计数键按工具、码及层聚合，不能作为业务操作身份。并行结果顺序、时间戳和同码都不能建立因果。来源冲突、已恢复或无触发证据时，只列观察到的失败。
3. Runtime 的 tool_call_audit 在现有写入中增加可空布尔 retryable、共享枚举值 retry_disposition/agent_disposition 和一个可空 JSONB 公开诊断（经校验的 detail、owner、消息来源、门禁原因及来源身份按需写）。安全文本复用 evidence_preview。只在失败行保存这些事实；三项控制量从完成事件取得，未知不得套用模型默认值。同步覆盖 executor 实时/恢复审计写入、audit_writer 和 execution_checkpoint；重放沿用现有调用身份与幂等边界。snapshot 保存同源最小证据以支持恢复，不另建事实表。
4. run_service._finalization_facts 使用原租户与 Run 查询，一次取所需列。在既有聚合遍历中匹配冻结的调用 ID 和来源 ID，不排序推因、不按失败逐行查询。审计读取失败仅使用已冻结的证据并标记不完整；历史 NULL 留空，不回填、不双写、不引入版本适配器。last_failure 仅用于时间线。用户停止说明唯一依据为真实终止原因，重试许可只描述调用。
5. Console Run 时间线查询和 execution_export_view 从同一审计行投影允许的字段；execution_export_repository 使用整行 JSON，新增列必须在落库前已安全且在导出视图检查键和值。WebChat 复用 ASSISTANT_MESSAGE 的正文和 technical_details。新增可空列采用 Runtime 所有者迁移，无默认回填、无新索引；同一发布批次先迁移再启用新写入。

## 性能验收与验证

实施前保存当前工作区的精确基线（包含未提交变更的源码指纹、迁移版本、构建和环境）。在同机隔离数据库、同数据量、并发、模型与 Worker 响应、预热条件下交替运行基线和候选版本。先重复运行基线校验测量稳定性。分别覆盖成功热路径、结构化拒绝、重复门禁与恢复、长 Run 收尾、Console 详情/导出。固定响应测宿主开销，另记录真实模型输入 token 增量，不能把模型波动当成宿主性能。

- 逐场景比较吞吐、p50/p95/p99 延迟、CPU/Run、峰值内存；审计另记录行宽、WAL/失败、查询计划和缓冲读取，模型侧记录轮次、上下文大小。公开 detail 本身增加失败字节，应实测对尾延迟和吞吐的影响。
- 成功路径不得增加模型调用、网络或数据库往返、审计写次数及诊断序列化。失败和收尾同样不得增加往返；沿用既有事务、索引和集合读取，归因整体 O(n)，状态大小受当前 Run 限额约束。审计查询用 EXPLAIN (ANALYZE, BUFFERS) 比较代表性数据量。
- 延迟和资源占用按候选减基线，吞吐反向计。对重复配对样本报告差值与单侧 95% 置信上界；吞吐、端到端延迟或宿主资源指标上界大于零时不能签署“性能不降”。结果不确定先增加样本或降低测量噪声，稳定退化先优化后复测。分别列出新增必要的失败载荷/存储字节，不能用成功流量均值掩盖失败路径退化。未经测量不发布性能结论。

## 回归责任

| 需求 | 自动验证入口与反例 |
|---|---|
| FEAT-01/02 | HTTP transport → 实际工具注册与模型消息；缺失/无效同码、已知 false/未知、非 JSON 和超时、未知键及合法键夹带敏感值；中英文精确码与码族覆盖。 |
| FEAT-03 | Runner + audit_writer + PostgreSQL + checkpoint 恢复后重建 facts；混入并行同名调用、其他目标、已恢复错误、跨 Run 来源、历史 NULL 与读失败；对比实时/恢复结果及总请求数。 |
| FEAT-04/05 | 用可控模型返回无依据修复及替代调用，检查实际派发、首个流式诊断、最终消息、折叠详情、管理员导出；检查租户隔离、真实停止原因与已知影响范围。 |

约束关联：api/harness-api RULE-005 对应公开字段；arch/harness-arch RULE-003/005/006 对应执行、重试及证据来源；i18n/harness-i18n RULE-i18n-001 对应双语回退；persistence/harness-data RULE-data-001 对应表归属及原查询边界。
