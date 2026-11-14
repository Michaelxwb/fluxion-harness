---
{"version":1,"requirement":"ci-static-lint-fix"}
---
# Tasks: ci-static-lint-fix

## TASK-001: 修复全仓 ruff 告警

- **Status**: done
- **Depends**:
- **Source**: CI run f38c6510 的 backend-static 失败
- **Scenarios**:
- **Spec-Refs**: test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime, apps/console-platform/backend/src/muad_console_platform/application/memory_service.py, apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx, apps/console-platform/frontend/src/components/common/ConfirmAction.tsx, packages/contracts/src/muad_contracts/memory.py, tests/console_platform/test_memory_create_normalization.py, tests/test_memory_*.py, tests/migrations/test_memory_schema.py, tests/acceptance/datastores.py, tests/conftest.py, docs/configuration-inventory.csv, e2e/tests/agent-management/memory-redesign.spec.ts, e2e/tests/user-detail-layout.spec.ts, build/INTRANET-K8S-DEPLOY.md, build/INTRANET-SETUP.md
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {"id": "lint", "argv": ["uv", "run", "ruff", "check", "."], "timeout": 300},
  {"id": "typecheck", "argv": ["uv", "run", "mypy", "apps", "packages"], "timeout": 900},
  {"id": "contracts", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_configuration_inventory.py", "tests/test_error_catalog.py", "tests/frontend/test_e2e_suite_contract.py", "tests/frontend/test_detail_sidesheet_contract.py"], "timeout": 600},
  {"id": "memory-unit", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform/test_memory_create_normalization.py", "tests/test_memory_contracts.py", "tests/test_memory_taxonomy.py", "tests/test_memory_eval_data.py", "tests/test_memory_evaluation.py", "tests/migrations/test_memory_schema.py"], "timeout": 600},
  {"id": "component-layout", "argv": ["make", "acceptance-e2e", "DOMAIN=form-layout"], "timeout": 1200},
  {"id": "memory-acceptance", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/acceptance/im_gateway/test_memory_flow.py", "tests/acceptance/im_gateway/test_memory_redesign_security.py", "tests/acceptance/im_gateway/test_memory_redesign_e2e.py", "tests/acceptance/test_foundation_schema_parity.py"], "timeout": 1800},
  {"id": "e2e-agent", "argv": ["make", "acceptance-e2e", "DOMAIN=agent"], "timeout": 1800}
]
```

### Description

CI 首轮（f38c6510）在 11 个 check 上红。本任务把「本地从未跑过的全仓门禁」一次性收口：

- `backend-static`：9 条 ruff 告警 + 76 条 strict mypy 错误（memory_writer/service/api/context 等，均为本轮记忆需求引入）。
- `backend-tests (fast)`：`MEMORY_INVALID` 未注册错误码、配置盘点漂移、e2e spec 引用了已删除的 testid。
- `component` / `form-layout`：记忆表列契约（内容/类别/来源/状态/更新时间/操作；操作列 196）与 v1 交互稿不符；详情侧板宽度契约 920；mock 夹具仍是 v1 形状。
- `e2e / agent`：`memory-redesign.spec.ts` 仍是 v1 管理面（memory_key/category/value），需按 v2 单入口命令重写。
- `backend-tests (worker)`：capacity 的 teardown 断言把「每测试克隆库」误认成 acceptance 残留；克隆前缀拆分为 `muad_tst_*`。
- 内网部署文档补「用户记忆 v2（0049）」说明：无新增部署步骤；升级会排空旧非终态提炼任务。

顺带修复：Console 创建记忆的候选归一化（predicate 推导 kind、value/text 互转）；WebChat/Console 命令重试沿用同一 Idempotency-Key；历史面板 ESC 只关最上层、分页 15。

### Checklist

- [x] `ruff check .` 0 error，`mypy apps packages` 0 error。
- [x] 配置盘点/错误码/e2e spec testid 契约全绿。
- [x] 记忆表列契约与 mock 夹具对齐，`form-layout` 域全绿。
- [x] `memory-redesign.spec.ts` 重写为 v2 并通过 `agent` 域。
- [x] capacity 克隆库前缀与 acceptance 临时库区分，teardown 断言不再误报。

### Spec Delta

```json
[]
```

### Log

- [2026-11-14T19:06:25.581Z] started tests=37cc6dcd3ecc

### Log

- [2026-11-15] created from CI f38c6510 的首轮红单（11 个 check）。
- [2026-11-15] 本地复现并修复：ruff 9→0、mypy 76→0；fast 分片 3 条（错误码/配置盘点/e2e testid 契约）；component form-layout 115 全绿；agent domain 12 全绿；capacity 克隆库前缀拆分；memory 回归 7 条测试夹具补 `updated_at`；b226/b256 的 v1 断言改 v2；e20 recall 预算语义（装不下条目→truncated；空封套越界→MEMORY_RESULT_BUDGET_EXCEEDED）；sdk 探针 Redis db 对齐；schema parity 漂移注入改跑独立临时库 + 逃生阀；dfx/rest 空机复跑全绿。
- [2026-11-15] `docs/configuration-inventory.csv` 按 AST 重新生成（同一套口径），2093 行。

### Verification

- 2026-11-14T22:03:58.672Z PASS commands=7/7 executed=7 deduped=0 elapsed=296315ms tests=2de3763d3a21 head=f38c6510e49f dirty=1 tests-changed
  - lint PASS exit=0
  - typecheck PASS exit=0
  - contracts PASS exit=0
  - memory-unit PASS exit=0
  - component-layout PASS exit=0
  - memory-acceptance PASS exit=0
  - e2e-agent PASS exit=0
