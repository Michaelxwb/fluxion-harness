---
{"description": "长期记忆协议", "next_rule": 1}
---

# harness-memory

## Rules

- [RULE-memory-001] 原话仅证明文本来源，不能授予保存授权；模型不得提供 author/explicit_request。自然语言 remember 必须展示完整候选，绑定租户/用户/Run/submission、候选 hash、预期版本、forget_epoch 和有效期，由真实用户确认原子保存；只有真实入站的显式命令或已授权管理员可以直接保存。候选每 Run 至多一个，工具整轮完成后才暂停；resume 先检查绝对 deadline。options 消费以租户和 source_event_id 唯一，结果 outcome 不进入唯一键。
- [RULE-memory-002] Runtime 是唯一记忆写入所有者；按 Conversation→Run→memory_scope 锁序串行修改，锁内禁止网络 IO。SINGLE/SET/NOTE 使用稳定唯一身份（SINGLE/SET 由 kind/predicate/scope/member hash 生成，NOTE 用稳定 id），软删后 id/version 不重置；每次变更、完整 after 历史、幂等 receipt 和 scope revision 同事务提交。同 command 重放返回首次回执；PENDING 只挂已有目标槽位，新后台 NOTE 在 writer 受理时分配一次身份。锁内按写后有效集合验证冻结条数/UTF-8 字节限额及绝对上限；仅可淘汰 AGENT，USER/ADMIN 不免除硬限；既有超额时治理可减少但不可增加。

- [RULE-memory-003] 候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。用户记忆 v2 在覆盖率/BM25 正分之上执行 scope 三态与 NOTE 实体门控：带 entity 标签的候选必须有正向匹配，且候选全部实体标签都出现在 query 中；无匹配时带 entity 标签的候选不注入，无标签 NOTE 仅按 topic。交互式 Run 的冷/热读取必须以 PG head revision 为准，逐次按当前语句时间过滤 TTL；经验/TASK_STATE 只冻结准入 key，行状态/版本/TTL 随快照刷新；读取失败清缓存并如实返回 degraded。交互式 Run 在每个模型请求前刷新独立 MemoryComponent；完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。定时 Task 的准入读取沿用上述选择、TTL、预算与失败降级约束，冻结实际 READ_ONLY 注入块及其版本；等待和恢复始终使用原块，不重新读取当前记忆或刷新块内条目的状态、版本与 TTL，也不写长期记忆。

- [RULE-memory-004] 用户/管理员单条删除保留 key tombstone 与 version，递增 revision 并撤销该 key 旧提议，不递增 forget_epoch；清空递增 forget_epoch 并撤销所有旧提议。自动写不能解除此删除、人工 disabled 或把 USER/ADMIN 降级为 AGENT。EXPIRE/QUOTA/TASK_COMPLETE 自动退休仅接受真实新入站记录的 source_scope_revision 证明，不以 job 开始时间冒充新依据。自动批量提交核对原 scope revision，过期输入为 STALE；同任务 lineage 续接原稳定 key，完成后清除未决状态。

- [RULE-memory-005] 只有 COMPLETED Run 可入队持久 consolidation job；唯一 job、claim epoch/lease 和终态共同 fence 提交权。输入事件 through_seq、摘要自身 seq/覆盖边界、hash、scope revision 与来源 Run 策略首次冻结后重试不刷新；独立 provider 使用冻结主模型、实时凭据并显式关闭。模型调用次数不保证一次，持久效果/历史/完成事件/job 终态必须同事务最多一次；失败整批回滚，warning/metric 可见，主 Run 终态不变。
- [RULE-memory-006] 新 Run/job 冻结完整 memory 设置、工具历史预算及提炼能力；设置执行期不重读，在线硬额度只提高，降低必须维护窗口排空。迁移需排空所有旧在途 Run/Task/job。开发阶段无历史数据，v2 允许原地重建 user_memory/history：不保留 legacy incarnation 或 legacy_snapshots_json，不伪造确认来源。迁移和 ORM 约束/索引同构；移除旧 ratio/source_type 协议，不新增兼容写层：v2 行只写 memory_kind，legacy 行（经验/TASK_STATE）仍以 category 判别，二者不互相填充。

## Conventions

- 实时确认与会话详情必须使用同一份确认投影，确认候选、来源和选项在等待状态事务内持久化；等待输入期间订阅可继续接收恢复或终态。✅ 首次等待即展示确认，确认后继续接收原 Run；❌ 过滤确认事件并依赖刷新才能看到候选。
- 确认结果按消费者分流：会话历史把它当人类可见回执，模型历史把它当用户确认后的引用事实。✅ 回放后请求仍以 user 结尾；❌ 合成 assistant 消息（供应商按预填拒绝，或模型误以为已经回答）。
- 确认回执在分页水位内出现同 Run 终态且没有最终回答时保留；最终回答与终态的存在性都只读取水位内 canonical 事实。✅ 成功只看到模型回复，失败仍看到保存结果，固定水位翻页集合不变；❌ 用当前 Run 状态或水位之后的回复改变旧页可见性。
- 后台提炼的 `infer.X` 与已有 `X` 同类别同内容时省略重复新增或无变化副本；已有不同值必须继续通过统一 writer 的版本、来源与人工保护校验。✅ 不重复新增，旧推断有新依据时仍可更新；❌ 去重提前跳过已有推断的更新，使旧值长期残留。
- 文本渠道的确认动作与 WebChat 选项同义：恰有一个待确认候选时，用户只回复“确认/拒绝”即可；带编号时仍逐项核对，编号不属于当前 Run 就显式 INVALID。✅ 企微用户不用抄候选 UUID；❌ 只接受完整编号或把短动词当普通消息。
- 来源与授权分开验证。✅ exact source quote 只生成证据候选，真实确认后保存 USER；❌ 看到“记住”或模型声称 USER 就保存同轮任意候选。
- 固定锁序和事务边界。✅ 锁 scope 后同事务写状态/history/receipt/revision；❌ 分事务写历史，或只约束未删除 key 导致 version 从 1 重启。
- 缓存保留新鲜度。✅ 每次请求获取 revision 与 statement_timestamp，按当前时间筛 TTL；❌ 缓存 now()，或把先查 R、后读 S 的行缓存成 revision R。
- 失败直接移除请求中的旧记忆块。✅ 清缓存/组件并记录失败；❌ 用旧成功结果伪装当前成功，或删除其他 SYSTEM/摘要消息。
- 取消和选择共用一次消费身份。✅ 一个 options source_event_id 对应 MEMORY_RESUME_RESOLVED，outcome 在 payload；❌ unique(tenant, options_event_id, outcome) 允许同一 options 同时 SELECTED/CANCELLED。
- 密钥校验覆盖所有新增持久出口。✅ 检查 value/key/source_quote/TASK_STATE 及 history/proposal/log/model input；❌ 把拒绝内容原样写进日志或失败 receipt。
- 内容与权威运行状态分离。✅ TASK_STATE 是短 TTL 线索，执行状态实时核验；❌ 用未确认线索代替权限、任务结果或当前能力。
- 纯退休命令容忍历史 content。✅ 过期清扫等 RETIRE-only 命令用占位 content 规划，只写 retirement 字段、不回写 `content_json`；旧 content 校验失败按 scope 捕获 `ValidationError` 并记录 tenant/user；❌ 一条脏数据卡死整轮清扫，或把容忍扩散到普通写入（`test_local_ttl_retires_legacy_content_without_blocking`）。
- 带原提议身份的 resume 不得降级成普通消息。✅ 无待消费中断时返回 `MEMORY_CONFIRMATION_EXPIRED`（410）且不调用模型；❌ `prepare_confirmation` 返回 `None` 后按普通续聊执行，用户看到模型回复而记忆未保存（`test_local_b15_bound_resume_without_pending_confirmation_is_expired_not_a_normal_message`）。

## Avoid

- 以上条款是目标实现约束；待对应真实验收通过才能宣称产品已经符合。不得降低测试真实边界、伪造验收结果、全库租户存在性判断或删除 dev 数据作为验收准备。

## Applied Deltas

- {"source":"2026-11-14/user-memory-retrieval-optimization/user-memory-retrieval-optimization.md#TASK-002#DELTA-001","op":"update","rule":"RULE-memory-006","payload_sha256":"8c63b3f57c79d2d79293cec5b5c6006047b93b85454a740df87b5485d3552887","at":"2026-11-14T19:02:02.474Z"}

- {"source":"2026-11-14/user-memory-retrieval-optimization/user-memory-retrieval-optimization.md#TASK-004#DELTA-001","op":"update","rule":"RULE-memory-002","payload_sha256":"b5bba8c939c60d7ea2932cebdd75fc770127e0305b058a2deefca62fb4ab9e2e","at":"2026-11-14T19:02:02.474Z"}

- {"source":"2026-11-14/user-memory-retrieval-optimization/user-memory-retrieval-optimization.md#TASK-004#DELTA-002","op":"update","rule":"RULE-memory-004","payload_sha256":"9ab15327e50fb83b965670199469cac190a20c56d162504341cdbf879fb6265b","at":"2026-11-14T19:02:02.474Z"}

- {"source":"2026-11-14/user-memory-retrieval-optimization/user-memory-retrieval-optimization.md#TASK-006#DELTA-001","op":"update","rule":"RULE-memory-003","payload_sha256":"01c40a092c2bf7779cf60c5eab413e65fca569a683616d82e69cb4b53be88a6f","at":"2026-11-14T19:02:02.474Z"}

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-010#DELTA-002","op":"update","rule":"RULE-memory-003","payload_sha256":"9dc9a162f72f5c2907f0d513c27ef2184bfe9dcafa80166c1f35307c628abe39","at":"2026-11-16T05:09:15.751Z"}
