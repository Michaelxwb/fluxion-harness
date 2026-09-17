# 16 数据库、接口与 Artifact Store 复审修订说明

## 1. 固定结论

```text
Model: 无平台默认模型
Artifact Store: NFS-backed RWX PVC
Runtime/Worker Skill Cache: local emptyDir
DB/API Artifact Locator: storage_key
MCP Tool Catalog: PostgreSQL 最近成功快照
Schedule/Task Skill Reference: skill_id
Shared Credential: 每 ProjectPlatform 0..1；Console 展示为“共享凭据：已配置/未配置”（来源 shared_credential_ref，无 priority）
TaskType V1: SKILL/BATCH
```

## 2. Skill 真正生效链路

```text
上传 skill.zip
 -> 校验
 -> 写 NFS Artifact Store
 -> insert skill_artifact(storage_key/checksum)
 -> current_artifact_id
 -> agent_skill_binding
 -> agent_access_grant + user_scope
 -> resolve-definition
 -> RuntimeSnapshot
 -> SkillArtifactCache.ensure
 -> local SkillExecutor
```

把 ZIP 放入 NFS 本身不会自动让 Agent 获得 Skill。

## 3. Cache Resolve

每次执行都会做轻量 resolve，但不是每次访问 NFS：

```text
memory hit
 -> local READY hit
 -> cache miss 才 copy from NFS
```

## 4. 明确不做

```text
Model is_default
MinIO/S3（V1）
NFS Server 业务 Pod
直接从 NFS 执行 Python
动态 pip install
MCP Tool 级用户授权
MCP Tool 级启停
共享凭据池
User-Agent-Skill 三元授权
EXTERNAL/AGENT_STEP TaskType
```
