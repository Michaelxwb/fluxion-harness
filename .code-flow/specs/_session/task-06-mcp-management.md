# TASK-006 Spec Context

- Context-SHA256: `5acaf4db8dcc7b1d8cccab5eba0aba64caded4b255f9b009f146ad7c4b84b798`

## Required Rules
- `harness-ui#RULE-ui-001`: Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计。
  - rule_sha256=6280b112348e099ad3161721e93e4ec4c59a76db5ac330fd55ca9c0a5205b9d6 verifier=harness-ui#RULE-ui-001; artifacts=06-mcp-management.frontend.design.md,06-mcp-management.md
- `harness-frontend#RULE-front-001`: 前端 API 调用只经 `src/api/`（services）层，组件禁止裸用 axios/fetch；所有文案只使用 i18n key（zh-CN/en-US）；列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。
  - rule_sha256=440c5905981dfbff9549ae224b77391ff6213fea051e1a0b5914099cb0db15fc verifier=harness-frontend#RULE-front-001; artifacts=06-mcp-management.frontend.design.md,06-mcp-management.md
- `harness-i18n#RULE-i18n-001`: 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。
  - rule_sha256=9c55063508523538b2ea446555d14c72d2deefcf6209c6624a019477974ff09c verifier=harness-i18n#RULE-i18n-001; artifacts=06-mcp-management.frontend.design.md,06-mcp-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实浏览器、探针 MCP、DB | 注册+连接测试状态与工具数展示 | e2e/tests/mcp-management/mcp-management.spec.ts | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "S-07" | planned |
| E-08 | integration | 组件源码契约 | 失败不覆盖表单 + Toast | tests/frontend/test_mcp_form_contract.py | uv run pytest -q tests/frontend/test_mcp_form_contract.py -k config_invalid | planned |
| E-09 | integration | Form 校验源码契约 | transport 本地拦截不提交 | 同上 | uv run pytest -q tests/frontend/test_mcp_form_contract.py -k transport | planned |
| RULE-ui-001 | E2E | 真实浏览器渲染 | 布局结构与词典字段 | e2e spec + contract | uv run pytest -q tests/frontend/test_mcp_module_contract.py | planned |
| RULE-front-001 | integration | 源码 + services 层 | 无裸 axios/fetch、全 i18n | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_mcp_module_contract.py && uv run python scripts/check_frontend_api_usage.py | planned |
| RULE-i18n-001 | integration | locale 资源 | 双语词条 | 同上 + check 脚本 | uv run pytest -q tests/frontend/test_mcp_module_contract.py && uv run python scripts/check_frontend_i18n.py | planned |
