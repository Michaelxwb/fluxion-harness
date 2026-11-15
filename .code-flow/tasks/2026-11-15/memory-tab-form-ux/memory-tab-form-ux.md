---
{"version":1,"requirement":"memory-tab-form-ux"}
---

# Tasks: memory-tab-form-ux

## TASK-001: 记忆 Tab 的表单可用性与操作列宽度

- **Status**: done
- **Depends**:
- **Source**: 用户在 Console 用户详情「用户记忆」Tab 的实机反馈：新增表单字段含义不明、操作列偏宽、编辑不回显
- **Scenarios**:
- **Spec-Refs**: memory/harness-memory, frontend/harness-frontend, i18n/harness-i18n, test/harness-test
- **Paths**: apps/console-platform/frontend/src/modules/user-identity/MemoryTab.tsx, apps/console-platform/frontend/src/modules/user-identity/memoryTaxonomy.ts, apps/console-platform/frontend/src/components/common/FormModal.tsx, apps/console-platform/frontend/src/locales, tests/frontend/test_memory_taxonomy_parity.py, e2e/tests/agent-management/memory-redesign.spec.ts, e2e/tests/user-detail-layout.spec.ts
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {"id": "lint", "argv": ["uv", "run", "ruff", "check", "."], "timeout": 300},
  {"id": "frontend-typecheck", "argv": ["npm", "--prefix", "apps/console-platform/frontend", "run", "typecheck"], "timeout": 600},
  {"id": "taxonomy-parity", "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_memory_taxonomy_parity.py", "tests/frontend/test_memory_tab_contract.py"], "timeout": 600},
  {"id": "component-layout", "argv": ["make", "acceptance-e2e", "DOMAIN=form-layout"], "timeout": 1200},
  {"id": "e2e-agent", "argv": ["make", "acceptance-e2e", "DOMAIN=agent"], "timeout": 1800}
]
```

### Description

三处实机反馈的修复，全部落在 Console 展示层，不改运行时契约：

1. 新增记忆是首版范围（PRD FR-08 / FEAT-02）：管理员本来就能新增，受控的是「记忆类型」词表而不是「能不能新增」。旧表单让管理员手填 `predicate`、`category` 之类的原始码，等于把内部键名暴露成自由文本。改为 `Form.Select` 从 `MEMORY_PREDICATES`（`packages/contracts/.../memory_taxonomy.py` 的只读镜像）里选，`适用范围` 由 ABSENT/EXPLICIT 单选控制，只在类型声明了 `audience`/`region` 维度时才展开对应下拉；内容提示按 kind 切换（NOTE=正文，其余=短取值），并补上标题/摘要/内容/标签/有效期的填写说明。
2. 操作列 196 → 120：三个行内动作改成图标按钮（历史/启停用/删除/恢复），用 `aria-label` 提供可访问名、`title` 提供悬停提示；腾出的宽度给内容列，列宽总和仍是 880（`table-layout: fixed` 下总和小于表宽会按比例摊分，宽度断言会失真）。不用 Semi `Tooltip`：它会在触发器 keydown 时 `stopPropagation`，第一次 Esc 被吃掉、历史面板关不掉。`user-detail-layout` 的按钮配色助手改为「可见文本为空时取 aria-label」，危险动作仍按语义断言 `danger`。`expectContentFits`/`expectFixedActions` 在 1280 与 390 两种视口下逐按钮断言不出格。
3. 编辑弹窗回显：`FormModal` 新增 `initValues`，编辑弹窗按 `editing.id` 重挂载，打开即带出标题/摘要/内容；类型与范围是创建归属，只读展示不可改。

另外：切换记忆类型时若新类型没有 scope 维度，范围状态自动回到「不限定」，避免留下不可提交的 EXPLICIT 状态；记忆命令失败按服务端错误码映射到已登记词条（`MEMORY_ALREADY_EXISTS`/`MEMORY_TAXONOMY_INVALID`/`MEMORY_SCOPE_INVALID` 等），未知码仍回落通用失败。

新增 `tests/frontend/test_memory_taxonomy_parity.py` 逐项核对前端镜像与 contracts 的 key/kind/cardinality/scope 维度，防止两处词表漂移。

### Checklist

- [x] 新增表单：类型为受控枚举选择；范围维度随类型与「指定范围」联动；字段都有填写说明。
- [x] 编辑弹窗回显标题/摘要/内容，身份字段只读。
- [x] 操作列宽度 120（图标动作 + aria-label），桌面与窄屏按钮不出格且配色断言仍分 danger。
- [x] 前端词表镜像与 contracts 一致性测试。
- [x] 类型切换复位范围状态；记忆命令错误码映射到词条。
- [x] `form-layout` / `agent` 两个 e2e 域通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-15] 按用户实机反馈实现：结构化新增表单、编辑回显、操作列收窄。
- [2026-11-15T01:16:05.485Z] started tests=63354ae5349b

### Verification
- 2026-11-15T01:17:12.980Z FAIL commands=3/5 executed=4 deduped=0 elapsed=64678ms tests=63354ae5349b head=04be955c9c65 dirty=1
  - lint PASS exit=0
  - frontend-typecheck PASS exit=0
  - taxonomy-parity PASS exit=0
  - component-layout FAIL exit=2
  - e2e-agent NOT-RUN
- 2026-11-15T01:24:45.427Z PASS commands=5/5 executed=5 deduped=0 elapsed=99840ms tests=63354ae5349b head=04be955c9c65 dirty=1
  - lint PASS exit=0
  - frontend-typecheck PASS exit=0
  - taxonomy-parity PASS exit=0
  - component-layout PASS exit=0
  - e2e-agent PASS exit=0
