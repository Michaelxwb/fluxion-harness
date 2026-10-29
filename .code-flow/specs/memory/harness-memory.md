---
id: harness-memory
description: 长期记忆协议
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-memory-001
    type: command
    stage: review
    config:
      argv: [uv, run, pytest, -q, tests/agent_core/test_memory_redesign_evidence.py, tests/agent_runtime/test_memory_redesign_confirmation.py]
      cwd: .
      timeout: 600
  - rule: RULE-memory-002
    type: command
    stage: review
    config:
      argv: [uv, run, pytest, -q, tests/agent_runtime/test_memory_redesign_writes.py, tests/agent_runtime/test_memory_redesign_quotas.py]
      cwd: .
      timeout: 600
  - rule: RULE-memory-003
    type: command
    stage: review
    config:
      argv: [uv, run, pytest, -q, tests/agent_runtime/test_memory_redesign_cache.py, tests/agent_runtime/test_memory_redesign_context.py, tests/agent_core/test_memory_redesign_selection.py]
      cwd: .
      timeout: 600
  - rule: RULE-memory-004
    type: command
    stage: review
    config:
      argv: [uv, run, pytest, -q, tests/agent_runtime/test_memory_redesign_job_effects.py, tests/agent_runtime/test_memory_redesign_confirmation.py]
      cwd: .
      timeout: 600
  - rule: RULE-memory-005
    type: command
    stage: review
    config:
      argv: [uv, run, pytest, -q, tests/agent_runtime/test_memory_redesign_job_effects.py, tests/agent_runtime/test_memory_redesign_input_boundary.py]
      cwd: .
      timeout: 600
  - rule: RULE-memory-006
    type: command
    stage: review
    config:
      argv: [uv, run, pytest, -q, tests/migrations/test_memory_redesign_migration.py, tests/agent_runtime/test_memory_redesign_snapshot.py]
      cwd: .
      timeout: 600
---

# harness-memory

## Rules

- [RULE-memory-001] 原话仅证明文本来源，不能授予保存授权；模型不得提供 author/explicit_request。自然语言 remember 必须展示完整候选，绑定租户/用户/Run/submission、候选 hash、预期版本、forget_epoch 和有效期，由真实用户确认原子保存；只有真实入站的显式命令或已授权管理员可以直接保存。候选每 Run 至多一个，工具整轮完成后才暂停；resume 先检查绝对 deadline。options 消费以租户和 source_event_id 唯一，结果 outcome 不进入唯一键。
- [RULE-memory-002] Runtime 是唯一记忆写入所有者；按 Conversation→Run→memory_scope 锁序串行修改，锁内禁止网络 IO。同 tenant/user/key 使用完整唯一身份，软删后 id/version 不重置；每次变更、完整 after 历史、幂等 receipt 和 scope revision 同事务提交。锁内按写后有效集合验证冻结条数/UTF-8 字节限额及绝对上限；仅可淘汰 AGENT，USER/ADMIN 不免除硬限；既有超额时治理可减少但不可增加。
- [RULE-memory-003] 候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。revision 缓存只存候选行：冷读取行与实际 revision 同快照，暖读取 revision 与当前语句时间，并逐次过滤 TTL、重新选择/渲染；读取失败清除旧缓存和 MemoryComponent。每个模型请求前刷新独立 MemoryComponent，完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。
- [RULE-memory-004] 用户/管理员删除保留 key tombstone，递增 forget_epoch 并撤销旧提议；自动写不能解除此删除、人工 disabled 或把 USER/ADMIN 降级为 AGENT。EXPIRE/QUOTA/TASK_COMPLETE 自动退休仅接受真实新入站记录的 source_scope_revision 证明，不以 job 开始时间冒充新依据。自动批量提交核对原 scope revision，过期输入为 STALE；同任务 lineage 续接原稳定 key，完成后清除未决状态。
- [RULE-memory-005] 只有 COMPLETED Run 可入队持久 consolidation job；唯一 job、claim epoch/lease 和终态共同 fence 提交权。输入事件 through_seq、摘要自身 seq/覆盖边界、hash、scope revision 与来源 Run 策略首次冻结后重试不刷新；独立 provider 使用冻结主模型、实时凭据并显式关闭。模型调用次数不保证一次，持久效果/历史/完成事件/job 终态必须同事务最多一次；失败整批回滚，warning/metric 可见，主 Run 终态不变。
- [RULE-memory-006] 新 Run/job 冻结完整 memory 设置、工具历史预算及提炼能力；设置执行期不重读，在线硬额度只提高，降低必须维护窗口排空。迁移需排空所有旧在途 Run/Task/job；每 key 单一稳定基线保留全部 legacy incarnation，不伪造确认来源，旧 AGENT 按原更新时间补 TTL。迁移和 ORM 约束/索引同构；移除旧 ratio/source_type 协议，历史快照原样保留仅作审计，不加兼容写层。

## Conventions

- 来源与授权分开验证。✅ exact source quote 只生成证据候选，真实确认后保存 USER；❌ 看到“记住”或模型声称 USER 就保存同轮任意候选。
- 固定锁序和事务边界。✅ 锁 scope 后同事务写状态/history/receipt/revision；❌ 分事务写历史，或只约束未删除 key 导致 version 从 1 重启。
- 缓存保留新鲜度。✅ 每次请求获取 revision 与 statement_timestamp，按当前时间筛 TTL；❌ 缓存 now()，或把先查 R、后读 S 的行缓存成 revision R。
- 失败直接移除请求中的旧记忆块。✅ 清缓存/组件并记录失败；❌ 用旧成功结果伪装当前成功，或删除其他 SYSTEM/摘要消息。
- 取消和选择共用一次消费身份。✅ 一个 options source_event_id 对应 MEMORY_RESUME_RESOLVED，outcome 在 payload；❌ unique(tenant, options_event_id, outcome) 允许同一 options 同时 SELECTED/CANCELLED。
- 密钥校验覆盖所有新增持久出口。✅ 检查 value/key/source_quote/TASK_STATE 及 history/proposal/log/model input；❌ 把拒绝内容原样写进日志或失败 receipt。
- 内容与权威运行状态分离。✅ TASK_STATE 是短 TTL 线索，执行状态实时核验；❌ 用未确认线索代替权限、任务结果或当前能力。
- 纯退休命令容忍历史 content。✅ 过期清扫等 RETIRE-only 命令用占位 content 规划，只写 retirement 字段、不回写 `content_json`；旧 content 校验失败按 scope 捕获 `ValidationError` 并记录 tenant/user；❌ 一条脏数据卡死整轮清扫，或把容忍扩散到普通写入（`test_local_ttl_retires_legacy_content_without_blocking`）。

## Avoid

- 以上条款是目标实现约束；待对应真实验收通过才能宣称产品已经符合。不得降低测试真实边界、伪造 Gate/Evidence、全库租户存在性判断或删除 dev 数据作为验收准备。
