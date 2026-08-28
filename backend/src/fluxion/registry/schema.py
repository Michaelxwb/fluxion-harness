from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

resource_definitions = Table(
    "resource_definitions",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("kind", String(64), primary_key=True),
    Column("resource_id", String(255), primary_key=True),
    Column("version", String(64), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("spec_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
)

Index(
    "idx_resource_latest_published",
    resource_definitions.c.tenant_id,
    resource_definitions.c.kind,
    resource_definitions.c.resource_id,
    resource_definitions.c.status,
    resource_definitions.c.published_at,
)

resource_bindings = Table(
    "resource_bindings",
    metadata,
    Column("binding_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("subject_type", String(32), nullable=False),
    Column("subject_id", String(128), nullable=False),
    Column("resource_type", String(64), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("resource_version_selector", String(64), nullable=False),
    Column("config_json", JSON, nullable=True),
    Column("credential_ref", String(512), nullable=True),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_binding_subject",
    resource_bindings.c.tenant_id,
    resource_bindings.c.subject_type,
    resource_bindings.c.subject_id,
    resource_bindings.c.resource_type,
    resource_bindings.c.enabled,
)

# F8：服务 list_bindings_page 的 tenant-only（+可选 resource_type）按 created_at
# 排序分页——idx_binding_subject 中 created_at 不在列，无法服务排序查询。
Index(
    "idx_binding_tenant_created",
    resource_bindings.c.tenant_id,
    resource_bindings.c.created_at,
)

audit_logs = Table(
    "audit_logs",
    metadata,
    Column("audit_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("publish_id", String(128), nullable=True),
    Column("action", String(64), nullable=False),
    Column("target_type", String(64), nullable=False),
    Column("target_id", String(255), nullable=False),
    Column("before_json", JSON, nullable=True),
    Column("after_json", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_audit_target",
    audit_logs.c.tenant_id,
    audit_logs.c.target_type,
    audit_logs.c.target_id,
    audit_logs.c.created_at,
)

# F8：服务 list_audit 的 tenant-only 按 created_at 排序分页——idx_audit_target 中
# target_type/target_id 夹在 tenant_id 与 created_at 之间，无法服务 tenant-only 排序。
Index(
    "idx_audit_tenant_created",
    audit_logs.c.tenant_id,
    audit_logs.c.created_at,
)

publish_records = Table(
    "publish_records",
    metadata,
    Column("publish_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("resource_type", String(64), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("version", String(64), nullable=False),
    Column("operation", String(32), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("event_id", String(128), nullable=False, unique=True),
    Column("publish_note", String(1000), nullable=True),
    Column("approval_id", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_publish_record_resource",
    publish_records.c.tenant_id,
    publish_records.c.resource_type,
    publish_records.c.resource_id,
    publish_records.c.created_at,
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", String(255), nullable=False),
    Column("version", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("locked_by", String(128), nullable=True),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("last_error", String(1000), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
)

Index(
    "idx_outbox_dispatch",
    outbox_events.c.status,
    outbox_events.c.available_at,
    outbox_events.c.created_at,
)

config_revisions = Table(
    "config_revisions",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

platform_users = Table(
    "platform_users",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("platform_user_id", String(128), primary_key=True),
    Column("display_name", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

chat_access_tokens = Table(
    "chat_access_tokens",
    metadata,
    Column("access_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("platform_user_id", String(128), nullable=False),
    Column("agent_id", String(255), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

Index(
    "idx_chat_access_user",
    chat_access_tokens.c.tenant_id,
    chat_access_tokens.c.platform_user_id,
    chat_access_tokens.c.revoked_at,
)

channel_identities = Table(
    "channel_identities",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("channel_type", String(64), primary_key=True),
    Column("channel_user_id", String(255), primary_key=True),
    Column("platform_user_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_channel_identity_platform_user",
    channel_identities.c.tenant_id,
    channel_identities.c.platform_user_id,
)

bind_codes = Table(
    "bind_codes",
    metadata,
    Column("bind_code_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("platform_user_id", String(128), nullable=False),
    Column("code_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("failed_attempts", Integer, nullable=False),
    Column("frozen_at", DateTime(timezone=True), nullable=True),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index("idx_bind_code_tenant_expiry", bind_codes.c.tenant_id, bind_codes.c.expires_at)

# 会话记忆持久化：Runtime 无状态的关键——L1/L2/SessionContextSummary 记录外置到共享 Registry。
# ADR-MEM-001 taxonomy 收紧 level 取值：l1（session raw）、l2（legacy user-raw，停双写）、
# session_context_summary（SessionContextSummary，session compaction，不进 user-level retrieval）。
# read_l1 含 session_context_summary；read_l2 只读 l2（cross-read 已删）。
session_memory = Table(
    "session_memory",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(128), nullable=False),
    Column("user_id", String(128), nullable=False),
    Column("session_id", String(128), nullable=False),
    Column("execution_id", String(128), nullable=False),
    Column("role", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("tokens", Integer, nullable=False),
    Column("level", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_memory_l1",
    session_memory.c.tenant_id,
    session_memory.c.session_id,
    session_memory.c.level,
    session_memory.c.id,
)

Index(
    "idx_memory_l2",
    session_memory.c.tenant_id,
    session_memory.c.user_id,
    session_memory.c.level,
    session_memory.c.id,
)

# ADR-MEM-001：user-scoped personal memory（Episodic/Semantic）。写侧唯一入口是
# MemoryLearner.commit（learning_enabled gate + Policy/Consent）；用户可见操作
# 只有 查看/纠正/删除（NFR-PRIV-01）。embedding Phase 0 存 JSON（SQLite/
# PostgreSQL 共享 schema，可移植）；pgvector ivfflat 是 Phase 1 FEAT-17 范围。
personal_memory = Table(
    "personal_memory",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(128), nullable=False),
    Column("user_id", String(128), nullable=False),
    Column("memory_type", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("embedding", JSON, nullable=True),
    Column("source_session_id", String(128), nullable=False),
    Column("source_range_hash", String(64), nullable=True),
    Column("learning_enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# NFR-SEC-01 tenant 隔离：所有查询按 tenant_id + user_id scope、按 id 排序。
# 不在 tenant/user 与 id 之间夹其他列（同 idx_audit_tenant_created 的 F8 教训）。
Index(
    "idx_personal_memory_user",
    personal_memory.c.tenant_id,
    personal_memory.c.user_id,
    personal_memory.c.id,
)

# ADR-SNAPSHOT-001：pinned 版本的 active 引用追踪（谁在引用：execution/workflow/
# plugin_package）。选独立表而非 resource_definitions 计数字段——hard-delete guard
# 与卸载语义需要精确 owner 查询（rule 3/7）；复合 PK (tenant,kind,resource_id,
# version,ref_type,ref_id) 天然服务 check 的版本坐标前缀查询（§3.4 P95≤5ms 索引
# 路径）。ref_type 入 PK（REVIEW-D）：同一 ref_id 可被不同类型引用（execution/
# workflow/plugin_package）独立计为多条，release 按 ref_type+ref_id 精确解绑。
active_references = Table(
    "active_references",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("kind", String(64), primary_key=True),
    Column("resource_id", String(255), primary_key=True),
    Column("version", String(64), primary_key=True),
    Column("ref_type", String(32), primary_key=True),
    Column("ref_id", String(128), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# ref_type 过滤（check_active_references 的可选过滤路径）
Index(
    "idx_active_reference_scope",
    active_references.c.tenant_id,
    active_references.c.kind,
    active_references.c.resource_id,
    active_references.c.version,
    active_references.c.ref_type,
)

# retention period 判断（TTL 兜底清理按 created_at 扫描，Phase 3+ 接线）
Index("idx_active_reference_tenant_created", active_references.c.tenant_id, active_references.c.created_at)



# ---- User Domain（Gate 1B / TASK-U102..U105，backend brief §3.3）----
# Profile 带版本（幂等读取取最新版本）；Preference 单行覆盖；Grant 行级撤销。

user_profiles = Table(
    "user_profiles",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("platform_user_id", String(128), primary_key=True),
    Column("version", Integer, nullable=False, primary_key=True),
    Column("profile_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_user_profiles_latest",
    user_profiles.c.tenant_id,
    user_profiles.c.platform_user_id,
    user_profiles.c.version.desc(),
)

user_preferences = Table(
    "user_preferences",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("platform_user_id", String(128), primary_key=True),
    Column("preference_json", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

profile_attributes = Table(
    "profile_attributes",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("platform_user_id", String(128), primary_key=True),
    Column("key", String(128), primary_key=True),
    Column("value", String(4096), nullable=False),
    Column("source", String(16), nullable=False),
    Column("source_ref", String(255), nullable=True),
    Column("confidence", Float, nullable=False),
    Column("is_explicit", Boolean, nullable=False),
    Column("user_editable", Boolean, nullable=False),
    Column("visibility", String(16), nullable=False),
    Column("valid_from", String(64), nullable=True),
    Column("valid_until", String(64), nullable=True),
    Column("superseded_by", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_profile_attributes_user",
    profile_attributes.c.tenant_id,
    profile_attributes.c.platform_user_id,
)

capability_grants = Table(
    "capability_grants",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(128), nullable=False),
    Column("platform_user_id", String(128), nullable=False),
    Column("capability_ref", String(255), nullable=False),
    Column("capability_kind", String(16), nullable=False, server_default="skill"),
    Column("granted_scope", String(32), nullable=False),
    Column("version_pin", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_capability_grants_user",
    capability_grants.c.tenant_id,
    capability_grants.c.platform_user_id,
    capability_grants.c.capability_ref,
)
