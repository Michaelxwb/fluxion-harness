# 03 用户身份与 Memory 详细设计

## 1. PlatformUser

ChannelIdentity → PlatformUser。内部员工、Web 用户、企微/微信/Mattermost 用户不是不同领域用户类型；差异体现在 identity metadata/Profile。

当前 UserDomainService 已具备 PlatformUser、Profile、Preference、Grant、User360 基础，可保留。

## 2. User Context

每次 Execution 构建 UserContextProjection：

- platform_user_id；
- profile version；
- preference version；
- capability grant/binding versions；
- policy refs；
- memory policy；
- personalization policy。

Projection 进入 Snapshot 或由 Snapshot pin exact versions。

## 3. Memory Taxonomy

- L0 Working Memory：当前 Execution，本地可丢弃。
- Session Raw/L1：session-scoped，共享 SQL。
- SessionContextSummary：只服务 session compaction。
- Personal Episodic/Semantic：user-scoped，独立 store/semantic backend。

当前 SQLSessionMemoryStore 已正确把 SessionContextSummary 与 L2 user retrieval 分开；继续保留。

## 4. Compaction

Compaction 是上下文容量管理，不是长期学习。保留最新 N 轮，旧消息经 Summarizer SPI 压缩；摘要必须有 source_range_hash/provenance。

## 5. Personal Memory Learning

唯一写链：

`Candidate extraction → Policy → Consent → learning_enabled → commit`

当前 PersonalMemoryStore/MemoryLearner 已有 gate shape，但 candidate extraction、完整 policy/consent 和 AgentLoop context retrieval 仍需闭环。

## 6. 隐私

用户必须能查看、纠正、删除自己的 Personal Memory；tenant+user 双 scope；Personal Memory 不因切换 Agent 而复制。
