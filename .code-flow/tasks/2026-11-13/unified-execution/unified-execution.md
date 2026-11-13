---
{"version":1,"requirement":"unified-execution"}
---
# Tasks: unified-execution

## TASK-001: 统一执行入口和平台会话

- **Status**: in-progress
- **Depends**:
- **Source**: 用户对话确认的统一执行方案
- **Scenarios**: 正式 Skill、草稿测试、工作区脚本；登录态获取与拒绝
- **Spec-Refs**: artifact/harness-skill, platform/harness-project-platform, secret/harness-secret, snapshot/harness-snapshot
- **Paths**: apps/agent-runtime/**, packages/agent-core/**, packages/platform-sdk/**, tests/**
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {"id":"runtime", "argv":["uv","run","pytest","-q","tests/agent_runtime/test_skill_tools.py","tests/agent_runtime/test_skill_argv.py","tests/agent_runtime/test_skill_operation_identity.py","tests/agent_runtime/test_task_family_scope.py","tests/acceptance/user_skill_sandbox/test_draft_shell.py"],"timeout":300}
]
```

### Description

统一模型执行入口，保留 Skill 同步与异步执行、草稿回写和工作区语义；宿主侧复用平台 Session。

### Checklist

- [ ] 统一模型工具与执行目标分派，保留授权和异步语义。
- [ ] 覆盖正式 Skill、草稿及工作区调用和错误路径。
- [ ] 完成定向测试并记录真实平台验证边界。

### Spec Delta

```json
[]
```

### Log

- [2026-11-13] 用户要求免 full 文档流程，直接实施统一入口。

### Verification

- 2026-11-13：使用独立 PostgreSQL 和 Redis 的定向回归 115 passed；包含真实 Worker HTTP 与数据库交接，Session 部分使用 Console/平台测试替身。配置清单 4 passed；Ruff 和四个受影响源码模块的 mypy 通过。
- `code-flow task finish` 未运行成功：当前全局 CLI 为 0.8.1，只识别旧版 `config.yml`，本工作区使用 v1 的 `config.json`。未标记任务完成。
- 2026-11-13：本机浏览器经真实 Gateway/Runtime/Console、真实 Redis 和本地模拟业务平台分别执行统一入口的 workspace、skill 和 draft，均获得 Session；正式 Skill 首次多传 `platforms` 被拒绝，第二次成功。此链路的沙箱模式为 dev-unsafe。
- 2026-11-13：Linux 容器内真实 bwrap 对正式 Skill、工作区及草稿分别运行，三者经真实 Console、Redis 和本地模拟平台获取 Session 均成功；草稿存储为测试替身，执行入口与浏览器端到端分开验证。
- 真实 MSSP/MSSW、生产 K8s、多 Pod、原生 MCP 和开启出口限制的端到端尚未验证。
- 2026-11-13：重新启动当前工作树整套服务后，先发现测试副本缺少 Skill artifact，补齐制品目录并保持 `egress.enabled=false` 后，日常自然语言请求在真实 Runtime 审计中完成 `load_skill=OK`、`execute(target=skill)=OK`，模拟平台累计收到 `/api/v1/ping` 与 `/api/v1/targets` 请求各 6 次。浏览器页面因 Runtime 热加载期间连接重置显示过一次网络错误，但数据库 Run 最终为 `COMPLETED`。
