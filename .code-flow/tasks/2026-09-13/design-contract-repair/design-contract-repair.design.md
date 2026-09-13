# 设计契约修复

## 范围
修复已确认的七项设计断点：投递许可、Agent 绑定、能力表单与凭据 Schema、同步/异步模式、测试用户候选、执行视图、需求追溯。保持当前运行角色与共享 Application，不实现整套尚未完成的业务运行面。

## 契约决策
- 投递：Worker 领取后，Gateway 经 agent-runtime 的 CH-DATA-03 原子消费许可并读取冻结负载；permit_consumed_at 为本次 epoch 的单次消费事实。
- Agent 绑定：按请求中出现的维度整体替换，带 revision；行内控件也发送该维度完整集合。
- 能力：supported_execution_modes 非空去重集合，替代 async_submittable；shared_secret_ref 只在 implementation 顶层；Schema 检查认证类型与实现类型。
- 测试候选：Service 与 Capability 的只读适配入口共用受控用户选择 Application，按对象鉴权；Builder 仅自身，Admin 可选择同租户 ACTIVE 用户。
- 执行视图：scope_refs/resource_scope_summary、RETRY_PENDING 和 available_actions 统一。
- 追溯：每个总设主题对应真实语义场景；补跨模块契约用例。

## 规范应用
| Spec | 应用 | 验证 |
|---|---|---|
| backend-code-quality-performance#RULE-backend-quality-001 | 新增测试完整类型、函数不超过 50 行；架构检查不连接数据库 | pytest、mypy、ruff |
| backend-database#RULE-backend-database-001 | 许可 SQL 参数化、提交后发网络；ORM 与基线迁移锁步，不重建用户数据库 | SQL/Schema 用例、设计字段一致性 |
| backend-directory-structure#RULE-backend-directory-001 | 测试放 tests，模型只声明结构 | 静态检查 |
| backend-logging#RULE-backend-logging-001 | 不打印真实凭据，不增加敏感日志 | 示例仅使用虚构 Secret 引用 |
| backend-platform-rules#RULE-backend-platform-001 | 设计阶段修订；统一 Envelope 与错误码，不发布 API | 契约测试与错误码索引 |

## 验收合同
1. 四类能力合法样例通过；缺地址/Secret/非法认证与模式失败。
2. 单行绑定样例保留其他绑定；未知 delta 字段失败。
3. 重复/并发/过期投递许可只允许一次消费，网络发送在提交之后。
4. 能力候选不使用 Service ID，跨用户/跨租户边界明确。
5. 前端使用后端实际字段、枚举和动作；追溯场景匹配需求。
6. 架构、单元、集成测试与类型检查结果记录于本目录。

## 收尾

已修复相关授权残留（PLAT-API-02/04 整接口 Admin）、重复场景编号及生成器空行累积。自动检查与尚待人工/真实系统验收的边界见 [verification.md](verification.md)。没有激活编码 TASK，也没有代替负责人签署 manual verifier。
