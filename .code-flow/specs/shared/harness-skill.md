---
id: harness-skill
description: Agent Harness 通用平台规则：skill
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-skill-001
  type: manual
  config:
    checklist: 确认 Artifact Store 为 NFS-backed RWX PVC、DB 仅 storage_key、Runtime/Worker
      emptyDir 缓存、禁止从 NFS 直接执行。
    owner: project-owner
---

# harness-skill

## Rules

- [RULE-skill-001] Artifact Store 为 NFS-backed RWX PVC（V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
