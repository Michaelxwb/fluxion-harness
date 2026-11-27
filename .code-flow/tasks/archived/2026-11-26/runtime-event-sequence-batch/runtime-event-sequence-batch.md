---
{"version":1,"requirement":"runtime-event-sequence-batch"}
---
# Runtime 事件序号批量分配

## TASK-001: 每批一次分配会话事件序号

- **Status**: done
- **Depends**:
- **Source**: 用户批准直接实施的事件序号批量更新
- **Scenarios**:
- **Spec-Refs**: api/harness-api, persistence/harness-data, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/event_batcher.py, tests/agent_runtime/test_capacity_webchat_durable_deltas.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-event-regression",
    "argv": ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_durable_deltas.py", "tests/agent_runtime/test_capacity_webchat_reply_generations.py", "tests/agent_runtime/test_capacity_webchat_shared_tail.py"],
    "timeout": 300
  }
]
```

### Description

在已校验租约和幂等性的事件提交批次中，用一条参数化 PostgreSQL 更新分配所有会话的序号区间。每个区间按批内顺序分配，序号和 canonical 事件同事务提交。使用相同启动配置复测本地吞吐。

### Checklist

- [x] 跨会话批次只执行一次序号更新，按租户和会话映射分配结果。
- [x] 用真实 PostgreSQL 验证并发、幂等、租约隔离与整批回滚。
- [x] 用相同配置采集改造前后的本地压测数据。

### Spec Delta

```json
[]
```

### Log

- [2026-11-26] 用户确认直接修改第一项。
- [2026-11-26T14:48:13.557Z] started tests=76af5467336d

### Verification

- PostgreSQL 定向用例在旧实现观察到 2 条序号更新。改造后同批更新 1 次，4 个定向用例通过。
- 本地单进程在相同配置下发压 8 Run/s，持续 42 秒，统计后 27 秒。完成吞吐从 7.667 到 7.704 QPS，首字 p95 从 1798.7 到 1434.6ms，两组均完成 336 个负载 Run，系统错误为零。
- 两组完整 smoke 都触发了空闲事务门槛，峰值分别为 22 和 23，阈值为 20。单次对照未证实用户容量提升。
- 2026-11-26T18:34:59.902Z PASS commands=1/1 executed=1 deduped=0 elapsed=35000ms tests=76af5467336d head=3910db0ec063 dirty=1
  - runtime-event-regression PASS exit=0
