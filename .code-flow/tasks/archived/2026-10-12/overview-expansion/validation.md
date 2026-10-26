# 概览指标扩充验证记录

2026-10-11 完成用户授权的首版概览扩充。接口契约见同目录 design，产品协议见 docs/07 §10.12。

## 实现

- 单一 GET /api/v1/overview?days= 驱动全页；一次捕获时刻、平台自然日、租户与软删除隔离。
- 六项 KPI、资源摘要、当前状态与期间异常、四块图表；运行/任务切换复用快照。
- 模型重试与摘要计入请求数，保留缺失用量、显示完整记录覆盖率。
- 首次失败可重试；刷新失败保留快照；迟到响应不覆盖新期间。
- Agent/模型审计、后台任务状态入口使用现有列表可见筛选。当前后台任务汇总仅业务主任务。
- 中英词条、深浅主题与响应布局；不新增数据表或快照。

## 验证

| 检查 | 实际结果 |
|---|---|
| tests/console_platform/test_overview_api.py | 37 passed：真实 PG/ASGI、时间窗/时区、成功率、耗时、主子任务、用量缺失、租户、排序、格式、鉴权与协议 |
| tests/acceptance/overview | 9 passed：独立库、真实 Console HTTP、独立 SQL 回读、加行后查询数不增长、Redis 不可达 |
| tests/frontend + tests/overview_dashboard_inventory.py | 313 passed；新增浏览器边界登记后，overview_e2e_fixture_contract 再验证 4 passed |
| 审计 query/agent_filter/detail/export/download/audits 回归 | 29 passed |
| API/i18n/error catalog/Foundation Envelope/Foundation i18n | 24 passed |
| make acceptance-e2e DOMAIN=overview-dashboard | 12 passed；真实后端与构建产物；成功路径不拦截；种子清理 counts 全零；独立库已 drop |
| Ruff / 6 个后端产品文件 mypy / 前端 typecheck + build | passed |
| frontend API usage / i18n（1088 keys） | passed |
| code-flow validate --no-heavy（显式变更文件） | decision=pass；全局 heavy 套件未执行，本需求真实验收另行执行如上 |
| code-flow spec validate | 6 bindings 有效 |
| git diff --check | passed |

## 浏览器场景

S-02 图表数据与切换；S-03 单次聚合与指标；S-04 筛选跳转；S-05 期间/刷新；S-06 1440/1920/768、中英/深浅、无横向溢出。
E-02/E-04 失效目标；E-03 首次失败重试；E-05 刷新失败保留数据；E-06 旧响应竞态；E-07 全缺失用量；E-08 无运行/模型请求。

截图人工检查：深色中文 1440、浅色英文 1920，图表实际绘制、字号与间距正常；减少动效模式下截图避免截取初始动画。

## 提交前检查

用户于 2026-10-11 授权提交并推送所有工作区改动，包含此前的列表/详情、运行事件明细及本次概览扩充。

- 相关前端契约、运行事件/模型明细、运行时审计、概览接口与真实验收合跑：436 passed。
- 所有本次修改/新增 Python 文件 Ruff 通过，15 个后端产品文件 mypy 通过，前端 typecheck 通过。
- `code-flow validate --no-heavy` 全工作区范围 decision=pass；API usage/i18n 与 diff whitespace 检查通过。
- `cf_task_workflow.py finish` 实际返回 `decision=block, error="task file missing or ambiguous"`：目录有设计与 Context，没有 TASK 文档及 active marker。此结果不能写成 Done Gate pass；提交说明如实记录该工作流结果。
