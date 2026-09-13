# 验证记录

日期：2026-09-14。对应 [修复设计](design-contract-repair.design.md) 与 [修复记录](../../../../docs/05-变更记录/13-V1.14.3-设计契约闭环修复.md)。

## 自动检查

| 检查 | 结果 | 证据范围 |
|---|---|---|
| `REQUIRE_PG=1 .venv/bin/python -m pytest --tb=short -q` | 213 passed，无跳过 | 架构、单元、集成全部用例；真实 PostgreSQL 可用 |
| `.venv/bin/python -m mypy <本次全部 10 个 Python 文件>` | 通过 | 模型、迁移、测试、索引生成脚本 |
| `.venv/bin/python -m py_compile <本次全部 10 个 Python 文件>` | 通过 | Python 语法 |
| `.venv/bin/ruff check <本次除迁移外的 9 个 Python 文件>` | 通过 | 新增测试及修改的模型/检查脚本 |
| 连续运行 `regenerate_indexes.py`，比较三个索引文件 SHA-256 | 不变 | 生成稳定，重复运行不累积空行 |
| `git diff --check` | 通过 | 无空白错误 |
| `cf_spec_context.py validate` | 通过，5 项绑定 | Context 结构有效，应用来源为用户授权与路径路由 |

未将全文件 Ruff 标记为通过：`0001_initial_schema.py` 沿用既有单行 SQL 字符串形式，全文件检查仍报告 E501 长行。没有关闭规则或改动项目检查配置。

## 测试内容

- `tests/unit/test_design_contracts.py`：直接读取 Owner JSON Schema，校验四类能力表单、共享凭据位置/认证类型、支持模式正反例及 Agent 行内绑定保留语义。
- `tests/architecture/test_design_flow_consistency.py`：候选/提交权限策略、仅异步能力直调限制、Admin 平台写入、前端字段/状态/动作、场景唯一性/表格、语义关联和接口 Owner 索引。
- `tests/integration/test_design_delivery_contract.py`：在隔离 PostgreSQL schema 执行文档 CAS，验证同一许可并发仅一个成功，拒绝重放、过期、跨租户、旧 epoch 与非法状态，并验证新 CHECK。
- 原架构用例继续验证设计字段、ORM 和 `0001` 迁移的一致性。测试临时 schema 自建自清理；未执行应用数据库的升级或重建。

## 规范应用

| required Rule | 本次应用与审阅证据 |
|---|---|
| RULE-backend-quality-001 | 新增函数有类型且不超过 50 行；数据库连接设置超时并显式处理不可用；Schema/并发/错误分支测试通过，架构测试无数据库依赖 |
| RULE-backend-database-001 | 参数化许可 SQL；模型与迁移一致；提交事务后才调用渠道；隔离测试 schema；未上线建库基线变更，已有部署需后续增量迁移 |
| RULE-backend-directory-001 | 模型位于 adapters/postgres，迁移位于 migrations/versions，测试按 unit/architecture/integration 放置；生成脚本属于本次任务产物 |
| RULE-backend-logging-001 | 没有新增业务日志或输出 Secret；测试使用虚构引用，数据库连接失败只记录异常类型 |
| RULE-backend-platform-001 | 保持既有 API Envelope 与错误码 Owner；不新增部署角色；不执行部署；接口变更处于设计阶段 |

上述是代理审阅记录，不是 project-owner 的人工确认。五项 Spec 均配置 `type: manual`，对应设计/代码/审阅应用记为 applied，尚未获得人工 verifier 证据，未将阶段 Gate 标记为通过。

## 证据边界

没有执行真实 MSS/WeCom 的完整 S-SVC-18、Gateway 网络发送或故障注入。PG 测试证明许可 SQL 的竞争结果；它不证明整套业务运行面已经实现，也不证明外部渠道端到端 exactly-once。
