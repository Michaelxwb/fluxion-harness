---
id: harness-project-platform
description: Agent Harness 通用平台规则：platform
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-platform-001
  type: manual
  config:
    checklist: 确认 Adapter SPI（无 refresh）、credential_mode 选择语义、Redis Session key 与失效清理。
    owner: project-owner
---

# harness-project-platform

## Rules

- [RULE-platform-001] ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据只存 SecretRef，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id}:{platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}` 并以 Set 索引清理；更换 adapter_key 必须使旧凭据与会话失效。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
