# TASK-026 Spec Context

- Context-SHA256: `6a7fdfd3301e0c7682a285bde5d53196a848dc2993d1762301d5570eaed88b0a`

## Required Rules
- `harness-arch#RULE-arch-001`: 固定四个部署单元（console-platform / agent-runtime / agent-worker / im-gateway）；Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行。
  - rule_sha256=c8b7610b0825011eeae758ed0a4d83d963e18a4ca0239398e59dad840663f35f verifier=harness-arch#RULE-arch-001; artifacts=08-runtime-execution.backend.design.md,08-runtime-execution.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B | 真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session | tests/acceptance/runtime/test_multipod_recovery.py -k s04（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","s04"] | planned |
| E-07 | E2E | 真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run | Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消 | tests/acceptance/runtime/test_multipod_recovery.py -k e07（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_multipod_recovery.py","-k","e07"] | planned |
| RULE-arch-001 | E2E | 真实 Runtime A→PostgreSQL/Artifact Store→Runtime B；真实 Gateway SSE→Runtime 进程终止→Reaper→GET Run＋原 verifier 边界 | 真实结束 A 后第二轮 B 重建会话和 Memory；无 sticky session；Gateway 提示重发；lease 过期失败 RUN_ABANDONED；GET 查到终态；不把断流直接改成取消；原 verifier 全部通过 | tests/architecture＋tests/acceptance/runtime/test_multipod_recovery.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests/architecture' && uv run pytest -q tests/acceptance/runtime/test_multipod_recovery.py"] | planned |
