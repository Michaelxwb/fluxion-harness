# TASK-012 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules
- `harness-skill#RULE-skill-001`: Artifact Store 为 NFS-backed RWX PVC（V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight；Artifact 写入不可变（temp+`os.replace` 原子写，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（默认 1h，保护进行中事务）。
  - rule_sha256=efdf9e292f2ca509cc2139d1e57978d17ab8fd4467fba7ccd5d72a77db6aaeb2 verifier=harness-skill#RULE-skill-001; artifacts=08-runtime-execution.backend.design.md,08-runtime-execution.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | emptyDir→真实 NFS 挂载 | 同 checksum 第二次命中 READY，不发生 NFS IO；singleflight；只执行本地已校验文件 | tests/test_skill_artifact_cache.py -k s03（planned） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","s03"] | planned |
| E-01 | integration | Runtime cache→真实 NFS 故障边界 | cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误 | tests/test_skill_artifact_cache.py -k e01（planned） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py","-k","e01"] | planned |
| RULE-skill-001 | integration | emptyDir→真实 NFS 挂载；Runtime cache→真实 NFS 故障边界＋原 verifier 边界 | 同 checksum 第二次命中 READY，不发生 NFS IO；singleflight；只执行本地已校验文件；cache miss 且存储不可用返回 SKILL_ARTIFACT_UNAVAILABLE；无半成品执行；checksum mismatch 使用已登记错误；原 verifier 全部通过 | tests/test_skill_artifact_cache.py＋tests/agent_runtime/test_artifact_results.py＋tests/console_skill/test_orphan_cleanup.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/test_skill_artifact_cache.py' && uv run pytest -q tests/test_skill_artifact_cache.py tests/agent_runtime/test_artifact_results.py tests/console_skill/test_orphan_cleanup.py"] | planned |
