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
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_skill_artifact_cache.py
    cwd: .
    timeout: 300
---

# harness-skill

## Rules

- [RULE-skill-001] Artifact Store 为 NFS-backed RWX PVC（V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight；Artifact 写入不可变（temp+`os.replace` 原子写，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（默认 1h，保护进行中事务）。

✅ 不可变写入 + 孤儿清理（`infrastructure/skill_artifact_store.py`）：

```python
if target.exists():
    raise FileExistsError(f"artifact already exists and is immutable: {storage_key}")
...
removed = cleanup_orphan_files(known_keys, grace_seconds=3600)  # DB 无记录且早于宽限期
```

❌ 覆盖写 / 无宽限期清理（可能删掉进行中事务刚写入的文件）：

```python
target.write_bytes(data)                 # 覆盖已有不可变 Artifact
cleanup_orphan_files(known_keys, grace_seconds=0)  # 竞态删除进行中导入
```

## Conventions

- **非 Skill 产物必须用 `skills/` 之外的前缀**：孤儿清理只扫描 `skills/**`（`skills/*/*/skill.zip` 与 `skills/*/*/.tmp-*`），因此任何非 Skill 产物写进 `skills/` 都会被 `cleanup_orphan_files` 误回收。既有实例：审计导出用 `exports/{tenant_id}/{export_id}/audits.{csv|json}`（`application/audit_export_service.py` 的 `EXPORT_ARTIFACT_PREFIX`）。新增产物类型必须显式声明自己的前缀，不得复用 `skills/`。
- **解包与执行的逃逸防护**：zip 解包必须做 zip-slip 校验，越界成员按 `SKILL_ARTIFACT_UNAVAILABLE` 拒绝；脚本选择必须校验路径落在 READY 目录内，越界抛 `SkillExecutionError("script path escapes the skill package")`；Skill 子进程环境变量只透传 `PATH/HOME/LANG` 白名单（`packages/artifact-store/.../skill_cache.py`、`packages/agent-core/.../skill/executor.py`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
