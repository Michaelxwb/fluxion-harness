import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class PlatformUser(StandardColumnsMixin, Base):
    __tablename__ = "platform_user"
    __table_args__ = (
        sa.Index(
            "uq_platform_user_tenant_user_code",
            "tenant_id",
            "user_code",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_platform_user_tenant_status", "tenant_id", "status"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_code: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


class ModelDefinition(StandardColumnsMixin, Base):
    __tablename__ = "model_definition"
    __table_args__ = (
        sa.Index(
            "uq_model_definition_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'OPENAI'"),
    )
    model_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    api_key: Mapped[str | None] = mapped_column(sa.Text())
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    last_test_status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'UNTESTED'"),
    )
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AgentAccessGrant(StandardColumnsMixin, Base):
    __tablename__ = "agent_access_grant"
    __table_args__ = (
        sa.Index(
            "uq_agent_access_grant_user_agent",
            "user_id",
            "agent_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_agent_access_grant_agent_user", "agent_id", "user_id"),
        {"schema": "control"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.agent_definition.id"),
        nullable=False,
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


class Skill(StandardColumnsMixin, Base):
    __tablename__ = "skill"
    __table_args__ = (
        sa.Index(
            "uq_skill_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_skill_user_scope_enabled", "user_scope", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    platform_label: Mapped[str | None] = mapped_column(sa.String(128))
    user_scope: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'SELECTED'"),
    )
    current_artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )


class SkillArtifact(StandardColumnsMixin, Base):
    __tablename__ = "skill_artifact"
    __table_args__ = (
        sa.Index(
            "uq_skill_artifact_skill_version",
            "skill_id",
            "version",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_skill_artifact_skill_checksum",
            "skill_id",
            "checksum",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_skill_artifact_skill_create_time", "skill_id", sa.text("create_time DESC")),
        {"schema": "control"},
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.skill.id"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    frontmatter_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    execution_mode: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'SYNC'"),
    )
    instructions: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("''"),
    )
    default_script: Mapped[str | None] = mapped_column(sa.String(256))
    package_size: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    validation_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    validation_message: Mapped[str | None] = mapped_column(sa.Text())
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class AgentSkillBinding(StandardColumnsMixin, Base):
    __tablename__ = "agent_skill_binding"
    __table_args__ = (
        sa.Index(
            "uq_agent_skill_binding_agent_skill",
            "agent_id",
            "skill_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.agent_definition.id"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.skill.id"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )


class SkillUserGrant(StandardColumnsMixin, Base):
    __tablename__ = "skill_user_grant"
    __table_args__ = (
        sa.Index(
            "uq_skill_user_grant_skill_user",
            "skill_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_skill_user_grant_user_skill", "user_id", "skill_id"),
        {"schema": "control"},
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.skill.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class SkillImportIdempotency(StandardColumnsMixin, Base):
    """按 (tenant, Idempotency-Key, endpoint) 记录导入首次结果，供重放返回。"""

    __tablename__ = "skill_import_idempotency"
    __table_args__ = (
        sa.Index(
            "uq_skill_import_idempotency_tenant_key_endpoint",
            "tenant_id",
            "idempotency_key",
            "endpoint",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ConfigAuditLog(StandardColumnsMixin, Base):
    __tablename__ = "config_audit_log"
    __table_args__ = (
        sa.Index(
            "ix_config_audit_log_resource_create_time",
            "resource_type",
            "resource_id",
            sa.text("create_time DESC"),
        ),
        sa.Index(
            "ix_config_audit_log_actor_create_time",
            "actor_user_id",
            sa.text("create_time DESC"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))
    source_ip: Mapped[str | None] = mapped_column(sa.String(64))


class AuditExportJob(StandardColumnsMixin, Base):
    """审计导出任务事实（API-05 创建）。

    `Idempotency-Key` 的唯一性由共享幂等表
    `control.skill_import_idempotency (tenant_id, idempotency_key, endpoint)`
    的 partial unique 承载（RULE-09），本表不重复承担该约束。
    """

    __tablename__ = "audit_export_job"
    __table_args__ = (
        sa.Index(
            "ix_audit_export_job_tenant_status_create_time",
            "tenant_id",
            "status",
            sa.text("create_time"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    export_format: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    # 与 API-01 一致的筛选条件（规范化后落库，同一份内容参与指纹）
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'PENDING'"),
    )
    row_count: Mapped[int | None] = mapped_column(sa.BigInteger())
    artifact_ref: Mapped[str | None] = mapped_column(sa.String(256))
    error_code: Mapped[str | None] = mapped_column(sa.String(64))


class AgentDefinition(StandardColumnsMixin, Base):
    __tablename__ = "agent_definition"
    __table_args__ = (
        sa.Index(
            "uq_agent_definition_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_agent_definition_model_enabled", "model_id", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text())
    instructions: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.model_definition.id"),
        nullable=False,
    )
    runtime_config: Mapped[dict[str, Any]] = mapped_column(
        "runtime_config_json",
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )


class ProjectPlatform(StandardColumnsMixin, Base):
    __tablename__ = "project_platform"
    __table_args__ = (
        sa.Index(
            "uq_project_platform_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_project_platform_adapter_key_enabled", "adapter_key", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    resolver_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    resolver_config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    adapter_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    adapter_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    adapter_schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'1'"),
    )
    credential_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    auth_secret: Mapped[str | None] = mapped_column(sa.Text())


class UserCredentialRef(StandardColumnsMixin, Base):
    __tablename__ = "user_credential_ref"
    __table_args__ = (
        sa.Index(
            "uq_user_credential_ref_user_platform",
            "user_id",
            "platform_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    platform_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.project_platform.id"),
        nullable=False,
    )
    credential_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    credential_schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'1'"),
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class SharedCredentialRef(StandardColumnsMixin, Base):
    __tablename__ = "shared_credential_ref"
    __table_args__ = (
        sa.Index(
            "uq_shared_credential_ref_platform_id",
            "platform_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_shared_credential_ref_platform_status", "platform_id", "status"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.project_platform.id"),
        nullable=False,
    )
    credential_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    credential_schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'1'"),
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )


class InboundAudit(StandardColumnsMixin, Base):
    """入站事件审计（设计 §3.3 / API-10）：接收 / 拒绝 / 失败各一条。

    **刻意没有自由 JSON 列** —— 字段全部枚举化，取件凭据（`aes_key`、媒体 URL）在结构上
    无处可放。RULE-secret-001 的审计腿因此由类型保证，而不是靠写入前的运行时脱敏。

    幂等由 partial unique `(tenant_id, channel, external_message_id, outcome)` 承载：企微会
    重投（与 E-07 同源），同一条消息的同一结局只留一行（RULE-data-001 的软删唯一口径）。
    索引按"某租户最近的入站事件"与"按消息 id 追一条"两种查法建。
    """

    __tablename__ = "im_inbound_audit"
    __table_args__ = (
        sa.Index(
            "ix_im_inbound_audit_tenant_create_time",
            "tenant_id",
            sa.text("create_time DESC"),
        ),
        sa.Index("ix_im_inbound_audit_external_message_id", "external_message_id"),
        sa.Index(
            "uq_im_inbound_audit_message_outcome",
            "tenant_id",
            "channel",
            "external_message_id",
            "outcome",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    bot_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    external_message_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    external_user_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    #: RECEIVED / REJECTED / FAILED（设计 API-10 的三种结局）
    outcome: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    #: 拒绝/失败原因码（接收时为空串）—— 用户可见文案由消息目录取，不落在这里
    reason_code: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, server_default=sa.text("''")
    )
    attachment_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    accepted_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    total_bytes: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("0")
    )
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))


class ArtifactDeliveryAudit(StandardColumnsMixin, Base):
    """交付审计（设计 §3.3 / API-03）："谁在何时把哪个产物交付给哪个路由、结果如何"。

    **与 `InboundAudit` 刻意不同形**：路由用 `(channel, route_key)` 而不是
    `channel + bot_id + external_user_id`。`route_key` 是适配器产出的**可读不透明串**
    （企微 = `{bot_id}:{userid}`，未来 web chat = `session:{id}`）—— 接 web chat 时那一列
    仍然填得出真值，而渠道私有的两列会当场填不出。不一致是**有意的**（上期已归档的表不动）。

    **没有自由 JSON 列**：交付凭据与取件令牌在结构上无处可放（RULE-secret-001 的审计腿）。

    幂等键 = **partial unique `(tenant_id, artifact_id, route_key)`**（设计 §3.3 的权威定义）：
    "同一**产物**对同一**路由**只交付一次"。刻意**不含** `delivery_key`（会话内与后台是两条
    传输路径，但"某产物已交付给某路由"是同一个事实）与 `outcome`（它是该行的**当前状态**：
    失败重试成功 = 更新同一行，而不是新增一行）。
    """

    __tablename__ = "artifact_delivery_audit"
    __table_args__ = (
        sa.Index(
            "ix_artifact_delivery_audit_tenant_time",
            "tenant_id",
            sa.text("create_time DESC"),
        ),
        sa.Index("ix_artifact_delivery_audit_artifact", "artifact_id"),
        sa.Index("ix_artifact_delivery_audit_route", "tenant_id", "channel", "route_key"),
        sa.Index("ix_artifact_delivery_audit_delivery_key", "delivery_key"),
        sa.Index(
            "uq_artifact_delivery_audit_target",
            "tenant_id",
            "artifact_id",
            "route_key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: 被交付的产物。control 与 runtime 是**不同 Owner Schema** ⇒ 只做逻辑 UUID 引用，不建物理 FK
    artifact_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    #: 适配器产出的可读不透明串；**刻意不拆成渠道私有列**（见类文档）
    route_key: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    #: 传输层幂等键（调用方给出）：留痕用，**不参与唯一约束**
    delivery_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    #: DELIVERED / FAILED / DEGRADED；`DELIVERED` 是终态，不被后续写覆盖
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, server_default=sa.text("''")
    )
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))


class PlatformSetting(StandardColumnsMixin, Base):
    """平台设置版本表（设计 §3.3 / ADR-02）：append-only 的权威设置源。

    每次保存插入一行新版本，当前版本 = 该租户最大 `revision`（`revision` 从 1 起）；
    `settings_json` 存整份设置文档，唯一查询键是独立的 `tenant_id + revision` 两列。

    乐观并发**不用行锁**：落败的 `INSERT (tenant_id, revision=expected+1)` 撞
    `uq_platform_setting_tenant_revision`（partial unique `WHERE is_deleted = false`）
    即返回版本冲突。

    `actor_user_id` 是 `console_account.id` 的**逻辑引用，不建物理 FK**：版本行不可变、
    永不清理，物理 FK 会把保存过设置的管理员账号永久钉住；同 Schema 的既有先例为
    ``ConfigAuditLog.actor_user_id``。
    """

    __tablename__ = "platform_setting"
    __table_args__ = (
        sa.Index(
            "uq_platform_setting_tenant_revision",
            "tenant_id",
            "revision",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        # 读当前版本 / 版本历史分页；`sa.column` 形式的 DESC 让 model 与迁移建出的
        # `(tenant_id, revision DESC)` 在反射下同形（`sa.text` 形式会被比对丢掉该列）。
        sa.Index("ix_platform_setting_tenant_revision_desc", "tenant_id", sa.desc(sa.column("revision"))),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    revision: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: 保存者（`console_account.id` 的逻辑引用；未认证写入路径留空）
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class PlatformSettingIdempotency(StandardColumnsMixin, Base):
    """按 (tenant, Idempotency-Key, endpoint) 记录平台设置首次提交结果，供重放返回。

    与 `SkillImportIdempotency` 同形；`endpoint` 存路由路径（save/restore），partial unique
    `(tenant_id, idempotency_key, endpoint) WHERE is_deleted = false` 兜底并发提交。
    """

    __tablename__ = "platform_setting_idempotency"
    __table_args__ = (
        sa.Index(
            "uq_platform_setting_idempotency_tenant_key_endpoint",
            "tenant_id",
            "idempotency_key",
            "endpoint",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
