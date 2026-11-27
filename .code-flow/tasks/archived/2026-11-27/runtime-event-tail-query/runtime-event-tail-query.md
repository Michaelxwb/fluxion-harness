---
{"version":1,"requirement":"runtime-event-tail-query"}
---
# Runtime SSE 追尾查询合并

## TASK-001: 一次查询读取会话水位和事件页

- **Status**: done
- **Depends**:
- **Source**: 用户批准实施的 SSE 追尾查询合并
- **Scenarios**:
- **Spec-Refs**: api/harness-api, persistence/harness-data, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py, tests/agent_runtime/test_capacity_webchat_shared_tail.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-tail-regression",
    "argv": ["uv", "run", "pytest", "-q", "tests/agent_runtime/test_capacity_webchat_shared_tail.py", "tests/agent_runtime/test_capacity_webchat_durable_deltas.py", "tests/agent_runtime/test_capacity_webchat_reply_generations.py"],
    "timeout": 300
  }
]
```

### Description

会话追平后使用单条有界 PostgreSQL 查询取得最新已提交水位和事件页。已知水位内的续页沿用分页读取。订阅按真实已提交事件的序号推进，Redis 通知维持现有格式并继续支持 PostgreSQL 补查。

### Checklist

- [x] 真实 PostgreSQL 用例验证首轮追尾和续页的 SQL 次数。
- [x] 验证持久水位、跨租户隔离、断线续订和终态顺序。
- [x] 以相同资源、参数和发压口径验证吞吐和追尾 SQL。

### Spec Delta

```json
[]
```

### Log

- [2026-11-27] 用户确认开始实施 SSE 追尾查询合并。
- [2026-11-26T18:43:38.796Z] started tests=a517f109c16e

### Verification

- 真实 PostgreSQL 用例中，旧实现唤醒后首轮发出两条 SQL。改后首轮和续页各发出一条 SQL，持久水位与租户隔离用例通过。
- 同配置单进程发压 8.5 Run/s，发压 42 秒，统计后 27 秒。追尾 SQL 从 25,947 到 19,063 条，完成吞吐从 8.074 到 8.148 QPS，首字 p95 从 2252.3 到 1703.9ms。两组均完成 357 个负载 Run，系统错误为零。
- 两组完整 smoke 的空闲事务峰值均为 22，阈值为 20。一次对照只多完成两个 Run，未证实容量进一步上升。
- 性能对照两侧均包含上一项序号批量分配的工作区变更。
- 2026-11-26T18:49:17.419Z PASS commands=1/1 executed=1 deduped=0 elapsed=35635ms tests=a517f109c16e head=3910db0ec063 dirty=1
  - runtime-tail-regression PASS exit=0
