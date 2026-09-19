# TASK-008 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-ui#RULE-ui-001`: Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计。
  - rule_sha256=6280b112348e099ad3161721e93e4ec4c59a76db5ac330fd55ca9c0a5205b9d6 verifier=harness-ui#RULE-ui-001; artifacts=07-agent-management.frontend.design.md,07-agent-management.md
- `harness-frontend#RULE-front-001`: 前端 API 调用只经 `src/api/`（services）层，组件禁止裸用 axios/fetch；所有文案只使用 i18n key（zh-CN/en-US）；列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。
  - rule_sha256=440c5905981dfbff9549ae224b77391ff6213fea051e1a0b5914099cb0db15fc verifier=harness-frontend#RULE-front-001; artifacts=07-agent-management.frontend.design.md,07-agent-management.md
- `harness-i18n#RULE-i18n-001`: 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。
  - rule_sha256=9c55063508523538b2ea446555d14c72d2deefcf6209c6624a019477974ff09c verifier=harness-i18n#RULE-i18n-001; artifacts=07-agent-management.frontend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-10 | E2E | 真实浏览器 + 后端 + DB | Popconfirm 删除后行移除、详情关闭 | e2e/tests/agent-management/agent-management.spec.ts | npm --prefix e2e test -- --config playwright.agent.config.ts --grep "S-10" | planned |
| E-09 | integration | 组件源码契约 | key 冲突 Modal 保留 + i18n 提示 | tests/frontend/test_agent_form_contract.py -k key_conflict | uv run pytest -q tests/frontend/test_agent_form_contract.py -k key_conflict | planned |
| RULE-ui-001 | E2E | 真实浏览器渲染 | 布局结构/词典字段 | e2e spec + contract | uv run pytest -q tests/frontend/test_agent_module_contract.py | planned |
| RULE-front-001 | integration | services 层 | 无裸 axios/fetch | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_agent_module_contract.py && uv run python scripts/check_frontend_api_usage.py | planned |
| RULE-i18n-001 | integration | locale 资源 | 双语词条 | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_agent_module_contract.py && uv run python scripts/check_frontend_i18n.py | planned |
