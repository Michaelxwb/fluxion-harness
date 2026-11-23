---
{"version":1,"requirement":"egress-management-ui"}
---
# 出站管理实施任务

## TASK-001: 独立出站管理入口并隔离设置回滚

- **Status**: done
- **Depends**:
- **Source**: egress-management-ui.frontend.design.md、egress-management-ui.backend.design.md
- **Scenarios**: S-01, S-02, E-01, B-01, B-02
- **Spec-Refs**: persistence/harness-data, auth/harness-auth, test/harness-test
- **Paths**: apps/console-platform/frontend/src/**, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_service.py, tests/frontend/test_console_shell_contract.py, tests/frontend/test_overview_services_contract.py, tests/frontend/test_overview_routing_contract.py, tests/frontend/test_overview_page_contract.py, e2e/tests/settings-ui.spec.ts, e2e/tests/sidebar.spec.ts, tests/frontend/test_webchat_code_splitting.py, tests/frontend/test_capacity_webchat_webchat_layout.py, tests/console_platform/test_platform_settings_service.py, tests/console_platform/test_platform_settings_api.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {"id":"console-settings","argv":["uv","run","python","-m","pytest","-q","tests/frontend/test_console_shell_contract.py","tests/frontend/test_overview_services_contract.py","tests/frontend/test_overview_routing_contract.py","tests/frontend/test_overview_page_contract.py","tests/frontend/test_webchat_code_splitting.py","tests/frontend/test_capacity_webchat_webchat_layout.py","tests/console_platform/test_platform_settings_service.py","tests/console_platform/test_platform_settings_api.py"],"timeout":300,"parallel":false},
  {"id":"console-build","argv":["npm","--prefix","apps/console-platform/frontend","run","build"],"timeout":240,"parallel":false}
]
```

### Description

移动监控和规则入口，沿用设置保存契约。Console 后端保留当前出站配置，系统设置回滚其余分组。

### Checklist

- [x] 新导航、页签、旧链接、编辑权限和规则保存已接线。
- [x] 回滚保留当前出站配置和已有历史，具备回归覆盖。
- [x] Console 生产构建通过。

### Spec Delta

```json
[
  {"id":"DELTA-001","op":"add","target":"persistence/harness-data","rule":"系统设置回滚将目标版本的系统设置与当前版本的出站配置合成，按现有并发保护追加新版本并写入审计。成功回滚保留当前出站总开关及规则。","evidence":"FEAT-04，数据库服务回归与人工场景 B-02。"}
]
```

### Log

- [2026-11-23] 用户确认直接实施，采用定向验证。
- [2026-11-23T02:43:42.050Z] started tests=0304f18f9ed4
- [2026-11-23T02:47:46.173Z] reopen: 复验新增入口对导航和前端拆包契约的影响

### Verification

- 尚未执行。
- 2026-11-23T02:47:01.865Z PASS commands=2/2 executed=2 deduped=0 elapsed=32782ms tests=f9276bd2e439 head=1e4c8c9119c7 dirty=1 tests-changed
  - console-settings PASS exit=0
  - console-build PASS exit=0
- 2026-11-23T02:48:23.785Z PASS commands=2/2 executed=2 deduped=0 elapsed=37431ms tests=81a5ffa43d2d head=1e4c8c9119c7 dirty=1 tests-changed
  - console-settings PASS exit=0
  - console-build PASS exit=0
