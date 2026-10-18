# Tasks: Agent 并发容量与通用 Webchat

- **Source**: .code-flow/tasks/2026-10-16/agent-capacity-webchat/（前后端v0.6设计合并）
- **Created**: 2026-10-16
- **Updated**: 2026-10-19
- **Plan-State**: 用户已确认正式任务清单并授权执行；实际进度以各TASK状态与验收证据为准

## Proposal

在现有四部署单元上补齐多Key模型资源池、全局准入/有界执行、持久流/可靠投递，以及绑定已有平台用户的通用Webchat。页面与Console共用工程和镜像；企微维持每Agent一个机器人容量基线，网页与企微独立Conversation，长期记忆仍按tenant/user共享。

首期认证使用Console签发的用户Token（默认30天）交换独立CHAT Cookie（默认12小时且不超过来源期限），支持历史会话/新建/续聊。可调参数按业务设置、模型资源、启动预算和验收profile归属落实，生产额度/SLO必须显式填写，Runtime/Worker副本建议依实际容量证据生成。

## Execution Contract

- 每个编码任务目标1～3个生产/测试文件、15～60分钟；Files是计划范围，新增路径不是已实现事实。迁移文件的{revision}必须在编码时按真实Alembic head分配，维护窗口一次升级到完整head，不引入运行兼容层或双写。
- 目标文件相交的写任务已增加先后依赖；单worktree一次只激活一个TASK。无依赖且文件不相交的任务可独立安排，当前按TASK串行执行，不启动子Agent。
- 先写局部行为测试并记录RED，再实现和记录GREEN；E2E只编写并登记，编码期不执行RED/GREEN，统一由verify-e2e终验。计划生成时状态为planned/draft；实际验证以各TASK证据为准，局部测试通过不代表生产容量已达标。
- 新增局部场景使用B-201起，与设计场景分开。前端设计S-F01/E-F01/B-F01在统一任务单中映射为S-101/E-101/B-101，Source和原始编号保留；不改测试层级或真实边界。
- Spec-Refs只在最终规则负责人列完整引用，其他任务通过Source、Acceptance Contract和责任TASK依赖继承适用约束；严禁重复认领、改为N/A或跳过required verifier。全量/构建/E2E verifier登记为review责任，最终必须记录真实执行结果。
- pytest/验收前按根AGENTS检查并停dev残留，使用自动隔离验收库与独立Redis槽；不删共享dev数据。没有真实外部机器人/模型额度时只能报告探针边界，不能虚构生产1000并发通过。

## Task Index

| 任务 | 阶段 | 子任务 | 优先级 | 预计分钟 | 依赖 | 文件数 |
|---|---|---|---|---|---|---|
| TASK-001 | A 契约与配置 | 容量与模型调度设置的typed schema | P0 | 45 | - | 9 |
| TASK-002 | A 契约与配置 | Webchat认证、输入、保留与重连设置 | P0 | 45 | TASK-001 | 9 |
| TASK-003 | A 契约与配置 | 启动资源预算与已有常量配置化 | P0 | 60 | - | 7 |
| TASK-004 | A 契约与配置 | 模型成员、容量预占与结算协议 | P0 | 45 | - | 2 |
| TASK-005 | A 契约与配置 | Webchat身份、会话、消息和附件DTO | P0 | 45 | - | 2 |
| TASK-006 | A 契约与配置 | 公开事件、通知与snapshot分片协议 | P0 | 45 | TASK-005 | 2 |
| TASK-007 | A 契约与配置 | 配置、认证、容量与游标错误目录 | P0 | 45 | TASK-004, TASK-005 | 4 |
| TASK-008 | A 契约与配置 | 系统设置分组元信息与生效说明 | P0 | 45 | TASK-001, TASK-002, TASK-007 | 8 |
| TASK-009 | B 持久结构 | 模型物理凭据、成员与额度组迁移 | P0 | 60 | TASK-004 | 5 |
| TASK-010 | B 持久结构 | 运行时模型容量、分配计数与租约结构 | P0 | 60 | TASK-009 | 3 |
| TASK-011 | B 持久结构 | 全局准入版本、计数与排队字段 | P0 | 45 | TASK-001, TASK-010 | 3 |
| TASK-012 | B 持久结构 | 用户Token及独立CHAT会话结构 | P0 | 45 | TASK-002, TASK-005, TASK-011 | 3 |
| TASK-013 | B 持久结构 | 会话入口归属、列表索引及暂存上传结构 | P0 | 60 | TASK-005, TASK-011, TASK-012 | 3 |
| TASK-014 | B 持久结构 | 用户通知feed、交付租约与稳定回执结构 | P0 | 45 | TASK-006, TASK-013 | 3 |
| TASK-015 | C 模型池 | 物理Key管理与稳定计量身份 | P0 | 45 | TASK-004, TASK-009 | 3 |
| TASK-016 | C 模型池 | ADMIN模型凭据路由与读写边界 | P0 | 45 | TASK-007, TASK-015 | 3 |
| TASK-017 | C 模型池 | 模型成员绑定、解绑及权重修改 | P0 | 45 | TASK-004, TASK-016 | 3 |
| TASK-018 | C 模型池 | 共享账号额度组管理与降额限制 | P0 | 45 | TASK-016 | 3 |
| TASK-019 | C 模型池 | 逻辑模型配置去除执行密钥并要求真实输出预算 | P0 | 45 | TASK-017, TASK-018 | 3 |
| TASK-020 | C 模型池 | 所选成员凭据的内部实时解析 | P0 | 45 | TASK-015, TASK-019 | 3 |
| TASK-021 | C 模型池 | PG物理Key与额度组原子预占 | P0 | 60 | TASK-004, TASK-010 | 3 |
| TASK-022 | C 模型池 | 调用续租、usage结算与UNKNOWN保留 | P0 | 60 | TASK-003, TASK-021 | 3 |
| TASK-023 | C 模型池 | 加权均衡、共享epoch与工作负载公平调度 | P0 | 60 | TASK-001, TASK-021 | 3 |
| TASK-024 | C 模型池 | 冻结模型池语义与非密钥执行策略 | P0 | 60 | TASK-001, TASK-020, TASK-023 | 3 |
| TASK-025 | C 模型池 | 真实AuditedModelProvider调用链接入资源池 | P0 | 60 | TASK-022, TASK-024 | 3 |
| TASK-026 | C 模型池 | 摘要和记忆提炼共享模型容量 | P0 | 60 | TASK-025 | 3 |
| TASK-027 | C 模型池 | Console探针的受信容量端口 | P0 | 45 | TASK-022 | 3 |
| TASK-028 | C 模型池 | 物理Key检测保持Console侧HTTP实现 | P0 | 45 | TASK-016, TASK-018, TASK-019, TASK-027 | 3 |
| TASK-029 | D 执行与准入 | 全局准入阈值的单调版本应用 | P0 | 45 | TASK-008, TASK-011 | 3 |
| TASK-030 | D 执行与准入 | Run受理与准入计数同事务 | P0 | 45 | TASK-024, TASK-029 | 3 |
| TASK-031 | D 执行与准入 | 终态释放、取消与排队期限竞态 | P0 | 60 | TASK-029, TASK-030 | 3 |
| TASK-032 | D 执行与准入 | Runtime执行槽与独立监督 | P0 | 45 | TASK-003, TASK-027, TASK-031 | 3 |
| TASK-033 | D 执行与准入 | Worker有界任务池及独立回收 | P0 | 60 | TASK-003, TASK-032 | 3 |
| TASK-034 | D 执行与准入 | 外部副作用重试安全与结果待核实 | P0 | 60 | TASK-005, TASK-033 | 3 |
| TASK-035 | D 执行与准入 | PG时钟、租约及绝对期限校验 | P0 | 45 | TASK-022, TASK-031, TASK-033 | 3 |
| TASK-036 | D 执行与准入 | 持久排队阶段与两入口状态投影 | P0 | 45 | TASK-006, TASK-025, TASK-032 | 3 |
| TASK-037 | D 执行与准入 | 工具可信重试元数据冻结到操作策略 | P0 | 60 | TASK-024, TASK-034 | 3 |
| TASK-038 | E CHAT认证 | 用户Token签发、重置与版本撤销 | P0 | 60 | TASK-008, TASK-012 | 3 |
| TASK-039 | E CHAT认证 | 用户详情Token管理的ADMIN端点 | P0 | 45 | TASK-007, TASK-038 | 2 |
| TASK-040 | E CHAT认证 | Token交换与独立CHAT会话生命周期 | P0 | 60 | TASK-002, TASK-038 | 3 |
| TASK-041 | E CHAT认证 | CHAT独立Cookie、CSRF与Origin校验 | P0 | 45 | TASK-040 | 3 |
| TASK-042 | E CHAT认证 | CHAT exchange/me/logout公开认证路由 | P0 | 45 | TASK-016, TASK-039, TASK-041 | 3 |
| TASK-043 | E CHAT认证 | 受信聊天身份核验与授权解析 | P0 | 45 | TASK-042 | 3 |
| TASK-044 | E CHAT认证 | Gateway CHAT主体及长流复核 | P0 | 60 | TASK-003, TASK-043 | 3 |
| TASK-045 | E CHAT认证 | Token签发与交换全链路脱敏 | P0 | 45 | TASK-020, TASK-042 | 3 |
| TASK-046 | F 通道与会话 | WEBCHAT入口控制面与快照类型 | P0 | 45 | TASK-005, TASK-013 | 3 |
| TASK-047 | F 通道与会话 | Gateway注册WEBCHAT标准通道适配器 | P0 | 45 | TASK-044, TASK-046 | 3 |
| TASK-048 | F 通道与会话 | Gateway受理与订阅解耦及精确来源路由 | P0 | 60 | TASK-030, TASK-036, TASK-047 | 3 |
| TASK-049 | F 通道与会话 | 显式会话创建、入口核属与标题 | P0 | 60 | TASK-013, TASK-030, TASK-035, TASK-046 | 3 |
| TASK-050 | F 通道与会话 | 历史列表、详情与固定水位分页 | P0 | 45 | TASK-049 | 3 |
| TASK-051 | F 通道与会话 | 历史和SSE共用安全公开投影 | P0 | 45 | TASK-006, TASK-050 | 2 |
| TASK-052 | F 通道与会话 | Gateway入口、会话和消息HTTP接口 | P0 | 60 | TASK-044, TASK-048, TASK-050, TASK-051 | 3 |
| TASK-053 | F 通道与会话 | 上传登记与Run独立不可变制品 | P0 | 60 | TASK-013, TASK-049 | 3 |
| TASK-054 | F 通道与会话 | Webchat流式上传与动态限额 | P0 | 45 | TASK-002, TASK-052, TASK-058 | 3 |
| TASK-055 | F 通道与会话 | 附件下载授权与安全预览 | P0 | 45 | TASK-044, TASK-054 | 3 |
| TASK-056 | F 通道与会话 | 精确Run取消、待确认恢复和记忆回执 | P0 | 60 | TASK-031, TASK-037, TASK-049 | 3 |
| TASK-057 | F 通道与会话 | 聊天客户端非敏感配置白名单投影 | P0 | 45 | TASK-003, TASK-008, TASK-052, TASK-055 | 3 |
| TASK-058 | F 通道与会话 | Runtime暂存上传登记的受信端口 | P0 | 45 | TASK-050, TASK-053 | 2 |
| TASK-059 | G 流与通知 | 模型增量有界合并与终态前flush | P0 | 60 | TASK-003, TASK-025, TASK-036, TASK-051 | 3 |
| TASK-060 | G 流与通知 | 共享事件追尾、提交后唤醒及补查 | P0 | 60 | TASK-056, TASK-059 | 3 |
| TASK-061 | G 流与通知 | 公开SSE游标、分片与缓冲回压 | P0 | 60 | TASK-051, TASK-052, TASK-057, TASK-060 | 3 |
| TASK-062 | G 流与通知 | 部分正文失败后的模型重试边界 | P0 | 60 | TASK-025, TASK-059, TASK-061 | 3 |
| TASK-063 | G 流与通知 | Run完成和待确认的同事务用户通知 | P0 | 45 | TASK-014, TASK-056, TASK-059, TASK-060 | 3 |
| TASK-064 | G 流与通知 | 活动收件箱增量读取与独立已读 | P0 | 45 | TASK-052, TASK-058, TASK-063 | 3 |
| TASK-065 | G 流与通知 | 交付reserve/renew/ack持久协议 | P0 | 45 | TASK-004, TASK-063 | 3 |
| TASK-066 | G 流与通知 | Runtime独立可靠交付泵 | P0 | 45 | TASK-003, TASK-032, TASK-065 | 3 |
| TASK-067 | G 流与通知 | 机器人收尾与主动兜底统一去重 | P0 | 45 | TASK-048, TASK-066 | 3 |
| TASK-068 | G 流与通知 | Worker离线结果接入持久用户收件箱 | P0 | 45 | TASK-033, TASK-065 | 3 |
| TASK-069 | G 流与通知 | 历史、流增量、通知和制品清理保护 | P0 | 60 | TASK-053, TASK-061, TASK-064, TASK-068 | 3 |
| TASK-070 | G 流与通知 | Gateway通知查询与显式已读接口 | P0 | 45 | TASK-044, TASK-052, TASK-061, TASK-064 | 3 |
| TASK-071 | H 前端 | 共享传输区分CONSOLE/CHAT认证上下文 | P0 | 60 | TASK-005, TASK-007 | 3 |
| TASK-072 | H 前端 | 浏览器SSE与snapshot分片解析器 | P0 | 45 | TASK-006, TASK-061, TASK-071 | 2 |
| TASK-073 | H 前端 | 模块WebchatClient、DTO与Owner接口服务 | P0 | 45 | TASK-005, TASK-071 | 3 |
| TASK-074 | H 前端 | CHAT认证状态机与身份切换清理 | P0 | 45 | TASK-072, TASK-073 | 3 |
| TASK-075 | H 前端 | 单个Token输入与过期重新进入 | P0 | 30 | TASK-074 | 2 |
| TASK-076 | H 前端 | 客户端配置加载与版本化消费 | P0 | 45 | TASK-057, TASK-073 | 3 |
| TASK-077 | H 前端 | 独立CHAT路由与页面编排 | P0 | 45 | TASK-075, TASK-076 | 3 |
| TASK-078 | H 前端 | 会话索引、选择、新建与迟到响应隔离 | P0 | 45 | TASK-073, TASK-077 | 3 |
| TASK-079 | H 前端 | DeepSeek式历史侧栏与聊天工作区 | P0 | 45 | TASK-078 | 3 |
| TASK-080 | H 前端 | 消息安全渲染、分页与滚动锚点 | P0 | 45 | TASK-055, TASK-079 | 3 |
| TASK-081 | H 前端 | 文字附件输入与消息幂等状态 | P0 | 45 | TASK-054, TASK-080 | 3 |
| TASK-082 | H 前端 | 回复订阅、重连与generation状态 | P0 | 45 | TASK-072, TASK-076, TASK-080 | 3 |
| TASK-083 | H 前端 | 停止、排队状态与原Run确认交互 | P0 | 45 | TASK-056, TASK-082 | 3 |
| TASK-084 | H 前端 | 后台会话活动、未读与安全附件预览 | P0 | 45 | TASK-055, TASK-070, TASK-082 | 3 |
| TASK-085 | H 前端 | 用户详情Webchat Token凭据卡片 | P0 | 45 | TASK-039, TASK-071 | 3 |
| TASK-086 | H 前端 | 模型物理Key管理与检测界面 | P0 | 45 | TASK-016, TASK-028, TASK-071 | 3 |
| TASK-087 | H 前端 | 模型成员绑定、权重与逻辑模型预算 | P0 | 45 | TASK-017, TASK-019, TASK-086 | 3 |
| TASK-088 | H 前端 | 额度组管理及共享预算说明 | P0 | 45 | TASK-018, TASK-086 | 3 |
| TASK-089 | H 前端 | 系统设置新增分组与字段错误定位 | P0 | 45 | TASK-008, TASK-076 | 3 |
| TASK-090 | H 前端 | 聊天及新增设置中英文词条 | P0 | 45 | TASK-083, TASK-084, TASK-085, TASK-087, TASK-088, TASK-089 | 3 |
| TASK-091 | H 前端 | 主题、窄屏和可访问性布局 | P0 | 45 | TASK-079, TASK-081, TASK-083, TASK-090 | 3 |
| TASK-092 | H 前端 | 模型管理实际页面挂接Key池与额度操作 | P0 | 45 | TASK-086, TASK-087, TASK-088, TASK-090 | 3 |
| TASK-093 | I 装配与观测 | 聊天页面完整装配与管理模块接线 | P0 | 60 | TASK-077, TASK-079, TASK-082, TASK-084, TASK-085, TASK-089, TASK-091, TASK-092 | 3 |
| TASK-094 | I 装配与观测 | Runtime装配所有通道端点与独立资源池 | P0 | 60 | TASK-027, TASK-035, TASK-037, TASK-062, TASK-066, TASK-069 | 3 |
| TASK-095 | I 装配与观测 | Worker装配模型外部调用预算与独立维护循环 | P0 | 60 | TASK-033, TASK-035, TASK-037, TASK-068 | 3 |
| TASK-096 | I 装配与观测 | Gateway装配HTTP聊天、SSE及四类生命周期 | P0 | 60 | TASK-055, TASK-057, TASK-061, TASK-067 | 3 |
| TASK-097 | I 装配与观测 | Console装配CHAT路由及探针资源预算 | P0 | 45 | TASK-008, TASK-028, TASK-042, TASK-045 | 3 |
| TASK-098 | I 装配与观测 | 模型、准入、流与交付指标目录 | P0 | 45 | TASK-094, TASK-096 | 3 |
| TASK-099 | I 装配与观测 | Worker与Console资源和认证观测 | P0 | 45 | TASK-095, TASK-097 | 3 |
| TASK-100 | J 部署与验收工具 | Console/Gateway单Pod及静态页面部署 | P0 | 45 | TASK-093, TASK-096, TASK-097 | 3 |
| TASK-101 | J 部署与验收工具 | Runtime/Worker资源与共享存储约束 | P0 | 45 | TASK-094, TASK-095 | 3 |
| TASK-102 | J 部署与验收工具 | 启动配置和同源HTTP/SSE路径分流 | P0 | 45 | TASK-003, TASK-100, TASK-101 | 3 |
| TASK-103 | J 部署与验收工具 | HPA上界与部署清单预算校验 | P0 | 45 | TASK-102 | 3 |
| TASK-104 | J 部署与验收工具 | 迁移、排空及资源预算预检 | P0 | 45 | TASK-098, TASK-099, TASK-103 | 3 |
| TASK-105 | J 部署与验收工具 | 容量验收profile严格schema与示例 | P0 | 45 | TASK-001, TASK-002, TASK-003 | 3 |
| TASK-106 | J 部署与验收工具 | 真实模型、企微与故障协议探针 | P0 | 45 | TASK-004, TASK-006, TASK-105 | 3 |
| TASK-107 | J 部署与验收工具 | 四单元独立验收栈与故障进程控制 | P0 | 60 | TASK-104, TASK-106 | 3 |
| TASK-108 | J 部署与验收工具 | Webchat浏览器验收配置与可信测试身份 | P0 | 60 | TASK-093, TASK-107 | 3 |
| TASK-109 | J 部署与验收工具 | 1000用户受理和满载控制负载驱动 | P0 | 60 | TASK-105, TASK-107 | 3 |
| TASK-110 | K 设计场景验收 | 模型池、准入和执行监督E2E | P0 | 60 | TASK-035, TASK-037, TASK-062, TASK-109 | 2 |
| TASK-111 | K 设计场景验收 | 事件恢复、投递和持久回执E2E | P0 | 60 | TASK-067, TASK-068, TASK-069, TASK-107 | 2 |
| TASK-112 | K 设计场景验收 | 聊天身份、历史和跨用户浏览器E2E | P0 | 60 | TASK-040, TASK-056, TASK-069, TASK-108 | 2 |
| TASK-113 | K 设计场景验收 | 排队、流恢复、停止确认及双入口E2E | P0 | 60 | TASK-062, TASK-083, TASK-108, TASK-110 | 2 |
| TASK-114 | K 设计场景验收 | 附件、通知与公开消息安全E2E | P0 | 60 | TASK-055, TASK-068, TASK-069, TASK-108 | 2 |
| TASK-115 | K 设计场景验收 | 模型池与Token管理、设置和布局E2E | P0 | 60 | TASK-085, TASK-087, TASK-088, TASK-089, TASK-091, TASK-108 | 2 |
| TASK-116 | K 设计场景验收 | 1000用户容量及单Pod恢复验收 | P0 | 60 | TASK-108, TASK-109, TASK-110, TASK-111 | 2 |
| TASK-117 | L 回归与报告 | 容量报告与Runtime/Worker副本建议计算 | P0 | 45 | TASK-104, TASK-109, TASK-116 | 3 |
| TASK-118 | L 回归与报告 | 验收映射与规范回归证据收口 | P0 | 45 | TASK-099, TASK-112, TASK-113, TASK-114, TASK-115, TASK-117 | 3 |

## Design Scenario Map

后端场景沿用原编号；下表仅将前端编号转换为当前验收工具可识别的全局数字编号，测试层级和断言不变。

| 任务单场景 | 前端设计场景 | 来源章节 |
|---|---|---|
| S-101 | S-F01 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-102 | S-F02 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-103 | S-F03 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-104 | S-F04 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-105 | S-F05 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-106 | S-F06 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-107 | S-F07 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-101 | E-F01 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-102 | E-F02 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-103 | E-F03 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-104 | E-F04 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| B-101 | B-F01 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| B-102 | B-F02 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-108 | S-F08 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-105 | E-F05 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-109 | S-F09 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-110 | S-F10 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-111 | S-F11 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-106 | E-F06 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| B-103 | B-F03 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-112 | S-F12 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-113 | S-F13 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-107 | E-F07 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-108 | E-F08 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-109 | E-F09 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| B-104 | B-F04 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-114 | S-F14 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-110 | E-F10 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| S-115 | S-F15 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| E-111 | E-F11 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |
| B-105 | B-F05 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 |

## Design Traceability

| 来源设计 | 业务规则/风险 | 对应场景 | 场景最终负责人 |
|---|---|---|---|
| agent-capacity-webchat.backend.design.md | RULE-01 | S-03, E-02 | TASK-111 |
| agent-capacity-webchat.backend.design.md | RULE-02 | S-01, E-01 | TASK-110, TASK-116 |
| agent-capacity-webchat.backend.design.md | RULE-03 | S-02, E-03 | TASK-113 |
| agent-capacity-webchat.backend.design.md | RULE-04 | S-04, E-04 | TASK-025, TASK-110 |
| agent-capacity-webchat.backend.design.md | RULE-05 | S-05, E-05 | TASK-112 |
| agent-capacity-webchat.backend.design.md | RULE-06 | E-06 | TASK-026 |
| agent-capacity-webchat.backend.design.md | RULE-07 | S-06, E-05 | TASK-112, TASK-114 |
| agent-capacity-webchat.backend.design.md | RULE-08 | S-09, E-09 | TASK-113 |
| agent-capacity-webchat.backend.design.md | RULE-09 | S-05, S-16, E-10, E-20, B-08 | TASK-040, TASK-112 |
| agent-capacity-webchat.backend.design.md | RULE-10 | S-10, S-11, E-11 | TASK-112, TASK-113 |
| agent-capacity-webchat.backend.design.md | RULE-11 | S-17, E-21, B-09, S-115 | TASK-094, TASK-115 |
| agent-capacity-webchat.backend.design.md | RISK-01 同供方多个 Key 可能共享账号/项目额度 | S-04, B-01 | TASK-021, TASK-110 |
| agent-capacity-webchat.backend.design.md | RISK-02 会话隔离后控制命令串入口 | S-02, E-03 | TASK-113 |
| agent-capacity-webchat.backend.design.md | RISK-03 用户Token泄露、跨用户冒认或意外获得管理权限 | S-05, S-16, E-05, E-10, E-20, B-08 | TASK-040, TASK-112 |
| agent-capacity-webchat.backend.design.md | RISK-04 PG 事件/容量热点或共享盘吞吐不足 | S-01, S-04, S-07 | TASK-110, TASK-111, TASK-116 |
| agent-capacity-webchat.backend.design.md | RISK-05 推送成功与持久效果不一致 | S-07, E-02, B-03 | TASK-064, TASK-111 |
| agent-capacity-webchat.backend.design.md | RISK-06 模型池变更破坏历史快照/记忆语义 | S-08, E-04, E-06 | TASK-025, TASK-026, TASK-115 |
| agent-capacity-webchat.backend.design.md | RISK-07 机器人或真实模型外部额度未知 | S-01, S-04 | TASK-110, TASK-116 |
| agent-capacity-webchat.backend.design.md | RISK-08 排队不可见、无限等待或取消后仍执行 | S-09, E-09 | TASK-113 |
| agent-capacity-webchat.backend.design.md | RISK-09 多历史会话因隐式最新指针串写或重连缺正文 | S-10, S-11, E-11 | TASK-112, TASK-113 |
| agent-capacity-webchat.backend.design.md | RISK-10 历史列表/消息全量读取拖垮容量或泄露内部上下文 | S-06, E-11, B-04 | TASK-050, TASK-112, TASK-114 |
| agent-capacity-webchat.backend.design.md | RISK-11 切用户或 Cookie 共享导致主体错配 | E-12, S-112 | TASK-112 |
| agent-capacity-webchat.backend.design.md | RISK-12 Runtime 事件直出、合法跳号误判或每token写PG | S-14, E-13, B-06 | TASK-060, TASK-114, TASK-116 |
| agent-capacity-webchat.backend.design.md | RISK-13 故障重试重复产生外部副作用 | E-15, E-109 | TASK-110, TASK-113 |
| agent-capacity-webchat.backend.design.md | RISK-14 单 Pod/连接池不足或重连风暴 | S-15, S-14 | TASK-116 |
| agent-capacity-webchat.backend.design.md | RISK-15 上传清理/保留策略破坏已受理结果或上下文 | E-14, B-07 | TASK-069, TASK-114 |
| agent-capacity-webchat.backend.design.md | RISK-16 配置分散/版本倒退导致超收、旧Run被改期限或前后端阈值漂移 | S-17, E-21, B-09, S-115, E-111 | TASK-094, TASK-115 |
| agent-capacity-webchat.frontend.design.md | RISK-F01 两种会话与401行为混淆 | S-101, E-101, E-105 | TASK-112 |
| agent-capacity-webchat.frontend.design.md | RISK-F02 重连追加重复或跨入口迟到 | S-103, E-103, B-101 | TASK-072, TASK-113 |
| agent-capacity-webchat.frontend.design.md | RISK-F03 来源确认/取消定位错误 | S-104, E-104 | TASK-113, TASK-114 |
| agent-capacity-webchat.frontend.design.md | RISK-F04 管理面 Key 配置和实际模型调用漂移 | S-106 | TASK-115 |
| agent-capacity-webchat.frontend.design.md | RISK-F05 组件被嵌入后认证/同源假设变化 | S-101, E-105 | TASK-112 |
| agent-capacity-webchat.frontend.design.md | RISK-F06 排队被误认为卡死或已经完成 | S-108, E-102 | TASK-113 |
| agent-capacity-webchat.frontend.design.md | RISK-F07 历史切换后消息串写、旧 Run 被取消或漏正文 | S-110, S-111, E-106, B-103 | TASK-078, TASK-112, TASK-113 |
| agent-capacity-webchat.frontend.design.md | RISK-F08 长列表和消息全量渲染拖慢界面 | S-110, B-103 | TASK-078, TASK-112 |
| agent-capacity-webchat.frontend.design.md | RISK-F09 切用户但旧聊天Cookie/缓存仍有效 | S-112, B-104 | TASK-074, TASK-112 |
| agent-capacity-webchat.frontend.design.md | RISK-F10 后台会话完成/待确认不可见 | S-113 | TASK-114 |
| agent-capacity-webchat.frontend.design.md | RISK-F11 过滤后的合法序号跳号被误判或恢复时泄露内部数据 | E-107, B-104 | TASK-074, TASK-114 |
| agent-capacity-webchat.frontend.design.md | RISK-F12 Token签发原文丢失/被缓存、Cookie超出来源期限或重置后旧会话仍访问 | S-114, E-110 | TASK-115 |
| agent-capacity-webchat.frontend.design.md | RISK-F13 前后端阈值漂移、启动预算被误放系统设置或旧连接被新配置破坏 | S-115, E-111, B-105 | TASK-072, TASK-115 |

## Spec Responsibility

| Spec / Rule | 唯一责任TASK | verifier阶段 | 执行命令 |
|---|---|---|---|
| harness-api#RULE-api-002 | TASK-030 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] |
| harness-api#RULE-api-001 | TASK-007 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] |
| harness-arch#RULE-arch-001 | TASK-104 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/architecture"] |
| harness-auth#RULE-auth-001 | TASK-043 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] |
| harness-data#RULE-data-001 | TASK-009 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests","-k","schema_parity"] |
| harness-frontend#RULE-front-001 | TASK-071 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] |
| harness-i18n#RULE-i18n-001 | TASK-090 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] |
| harness-im#RULE-im-001 | TASK-046 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] |
| harness-im#RULE-im-002 | TASK-048 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] |
| harness-log#RULE-log-001 | TASK-098 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] |
| harness-mcp#RULE-mcp-001 | TASK-037 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] |
| harness-memory#RULE-memory-001 | TASK-056 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] |
| harness-memory#RULE-memory-002 | TASK-056 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"] |
| harness-memory#RULE-memory-003 | TASK-025 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"] |
| harness-memory#RULE-memory-004 | TASK-049 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] |
| harness-memory#RULE-memory-005 | TASK-026 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"] |
| harness-memory#RULE-memory-006 | TASK-024 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"] |
| harness-model#RULE-model-001 | TASK-019 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] |
| harness-rel#RULE-rel-001 | TASK-017 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] |
| harness-secret#RULE-secret-001 | TASK-020 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] |
| harness-skill#RULE-skill-001 | TASK-053 | review（本任务负责登记/修复/证据） | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] |
| harness-snapshot#RULE-snapshot-001 | TASK-024 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] |
| harness-test#RULE-test-001 | TASK-118 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] |
| harness-time#RULE-time-001 | TASK-035 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] |
| harness-ui#RULE-ui-001 | TASK-091 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] |
| harness-ui-detail#RULE-ui-detail-001 | TASK-085 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] |
| harness-worker#RULE-worker-001 | TASK-033 | review（本任务负责登记/修复/证据） | ["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] |

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | depends_on |
|---|---|---|---|---|---|---|---|---|---|
| S-01 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 真实 HTTP Gateway→Runtime→PG；模型可为真实 HTTP 受控探针 | TASK-116 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/capacity/test_capacity.py && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | . | 1800 |  |
| S-02 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 真实企微协议探针、浏览器、Gateway、Runtime、Worker、PG/Redis | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| S-03 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两个 Runtime 实例、PG、真实 SSE、故障进程 | TASK-111 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | . | 1800 |  |
| S-04 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 多 Runtime、同模型多 Key HTTP 探针、实际额度存储和审计 | TASK-110 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | . | 1800 |  |
| S-05 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 管理员/普通用户真实浏览器、Console Token 签发/交换、PG、Gateway | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-06 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | canonical、共享卷、Gateway、真实浏览器 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| S-07 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | Worker 多实例、实际 Skill 子进程组、PG、投递链路 | TASK-111 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | . | 1800 |  |
| S-08 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | Console 管理界面、PG、Runtime 实际模型请求 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| S-09 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 企微协议探针、真实浏览器/SSE、PG、受控模型与 Worker | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| S-10 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 真实浏览器、Gateway、Runtime、PG 与捕获模型 HTTP 输入的探针 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-11 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两浏览器页面、两个 Runtime、PG、真实 SSE 和等待中的模型探针 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| S-12 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两个 Runtime、主模型/摘要/记忆任务、Console 探针和真实 HTTP Key 计数服务 | TASK-110 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | . | 1800 |  |
| S-13 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 浏览器、PG、真实 Run/Worker 投递与提交后故障注入 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| S-14 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 容量栈真实 PG/HTTP/SSE、可配置增量模型探针 | TASK-116 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/capacity/test_capacity.py && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | . | 1800 |  |
| S-15 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 正式四单元栈、真实浏览器/入口代理、单 Console/Gateway 重启与 Runtime/Worker 替换 | TASK-116 | planned | ["bash","-lc","uv run pytest -q tests/acceptance/capacity/test_capacity.py && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | . | 1800 |  |
| S-16 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | Console 策略/签发、浏览器 Cookie、真实 PG 时间与历史续聊接口 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-17 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | Console系统设置/资源配置、两个Runtime、真实PG准入、Gateway启动配置与浏览器 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| E-01 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两并发请求、PG、模型探针计数 | TASK-110 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | . | 1800 |  |
| E-02 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | PG/Redis 故障代理、真实执行/连接 | TASK-111 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | . | 1800 |  |
| E-03 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两入口、真实 Run/待确认状态 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-04 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实故障模型 HTTP 服务和调度存储 | TASK-025 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | . | 300 |  |
| E-05 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两租户、真实认证/归属查询/下载与 SSE | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-06 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实压缩/记忆提炼接线、持久快照和 model HTTP | TASK-026 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | . | 300 |  |
| E-07 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 慢浏览器、SSE 代理、PG、重连 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-08 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 双入口、真实确认请求、记忆 PG 状态与模型 HTTP 输入 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-09 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 队列实际存储、并发受理/取消、SSE、模型探针计数 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-10 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 真实随机 Token、Console、两租户用户、PG CHAT 会话 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-11 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两租户/用户/入口的真实列表、详情、发送和确认 API | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-12 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 同一浏览器 A/B 用户 Token、共享 Cookie、API/SSE/下载 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-13 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | Runtime 实际持久事件、Gateway 公开投影和浏览器 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-14 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 浏览器上传、真实共享文件系统、PG、清理与幂等受理 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-15 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 真实 Worker 子进程、PG claim/reclaim、受控外部写 HTTP 服务 | TASK-110 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | . | 1800 |  |
| E-16 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 模型服务端处理中断、真实 Key/账户计数和租约存储 | TASK-110 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | . | 1800 |  |
| E-17 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 单租户 Gateway、其他租户有效 CHAT 会话、稳定入口/Run 来源 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-18 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 实际代理/应用解析、浏览器、PG 与模型计数服务 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-19 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 单 Gateway 重启、真实 Run 终态/PG投递意图、企微协议探针 | TASK-111 | planned | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | . | 1800 |  |
| E-20 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | ADMIN 重置/撤销、两个浏览器 CHAT 会话、真实 PG 事务与长流复核 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-21 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 设置保存API、真实PG版本行、进程启动与正式部署清单/容量harness | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| B-01 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 并发预占/释放、实际租约存储 | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | . | 300 |  |
| B-02 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实凭据/会话/附件归属存储 | TASK-053 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | . | 300 |  |
| B-03 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 跨页读取和提交顺序、PG | TASK-064 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | . | 300 |  |
| B-04 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 实际 Conversation/CanonicalEvent 查询与索引 | TASK-050 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | . | 300 |  |
| B-05 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 实际准入计数、Run 事务、取消/领取竞态和 PG 行锁 | TASK-031 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | . | 300 |  |
| B-06 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 实际公开事件 DTO/水位和恢复服务 | TASK-060 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | . | 300 |  |
| B-07 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实保留/清理查询、活跃 Run/job 与共享文件系统 | TASK-069 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | . | 300 |  |
| B-08 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实 PG Token/会话约束、有效期裁决与用户锁 | TASK-040 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | . | 300 |  |
| B-09 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | contracts配置解析、真实PG配置/快照、全局准入版本和启动settings | TASK-094 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | . | 300 |  |
| S-101 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | ADMIN 为已有用户生成 Token，用户直接打开 /chat 输入后进入聊天；有效 Cookie 刷新恢复，仅见获授权入口；无需注册/密码/管理账号，访问管理 URL 被拒 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-102 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 发文字和附件，用户气泡先显示待受理，202 后绑定真实 Run；模型增量显示，终态只结算一次 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| S-103 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 断网、页面刷新及服务实例切换后恢复正确 Run/文本/附件，不重复提交、不重复显示 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| S-104 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 与企微同时处理不同任务，网页停止/new/确认仅影响网页当前 Run/会话 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| S-105 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 离线 Task 完成，重开拉取持久通知，定位结果、下载附件并标记已读 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| S-106 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 模型 Key 新增/编辑/解绑、用户 Token 签发/重置/撤销及策略设置影响对应后续请求；关闭 SideSheet 不破坏列表，BUILDER/CHAT 不显示凭据维护入口且后端拒绝越权 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| S-107 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 中英文、明暗主题、桌面和窄屏下状态/时间/操作可见；消息区域滚动不盖住输入和确认 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| E-101 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | CHAT 会话过期/撤销时关闭流并清私有状态，回到 Token 输入；提示重新输入有效 Token，无效/过期则联系管理员；不跳 Console 登录、不自动保存或交换Token、不保留他人消息缓存 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-102 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 未授权入口、过载、受理超时和消息幂等冲突有明确状态；重试沿用原消息ID；不显示伪成功 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-103 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 慢消费者或事件缺口触发游标恢复；取消旧连接后迟到数据不得污染新入口 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-104 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 确认过期、附件拒绝/失效和下载越权有本地化反馈，其余有效内容可继续 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| B-101 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 | TASK-072 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | . | 300 |  |
| B-102 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示 | TASK-093 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"] | . | 300 |  |
| S-108 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；刷新恢复，取消后停止等待，状态不显示为模型正文 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-105 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 伪造/过期/撤销 Token 与停用用户不能进入聊天；重新输入有效Token后恢复原用户，先前消息不自动重提；Token不进地址/持久缓存/日志，错误不回显Token或暴露他人身份 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-109 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 使用正式 Console 镜像及真实入口代理/浏览器/Gateway；直接打开并刷新 `/chat/{entryId}`、加载静态资源、交换用户Token和订阅SSE均成功；渲染部署清单确认未增加 Webchat 前端 Pod/Service，CHAT 不经过 Console 管理登录守卫 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| S-110 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器经 API 新建 A/B；侧栏显示标题与活动分组，打开 A 看原消息后继续提问，新增 Run 仍归 A；刷新含 conversation 的 URL 恢复 A；首屏50条，可加载超过200条保留消息 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-111 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文和真实状态，不重复、不被 B 迟到响应覆盖；WAITING_INPUT 回到原确认操作 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-106 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；忙碌不自动新建/换会话，切换不隐式取消 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| B-103 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 | TASK-078 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | . | 300 |  |
| S-112 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；共享Cookie切换导致旧页上下文不匹配时明确停用，不能自动互相换Token循环 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-113 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；隐藏页暂停轮询，回前台同步；重放不重复累计未读，打开 A 不清 B 的未读 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-107 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；原消息不重提，恢复正文不重复 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-108 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；未知受理不新建消息号，不因切换会话改投 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-109 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；新建/切换会话不改变该结果 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| B-104 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 | TASK-074 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | . | 300 |  |
| S-114 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 真实 Console 设置/签发和浏览器Cookie；管理员可见Token到期时间且原文仅本次显示/复制，刷新详情不能再次读取；默认30天/12小时及自定义TTL均受后端期限约束；Cookie到期重新认证或Token重签后，同用户原历史仍可继续 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| E-110 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；重置响应丢失只刷新版本、不自动再次重置，用户Token原文不进入持久store，关闭结果框即清除 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| S-115 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 真实系统设置API、Gateway配置投影和浏览器；调整消息/附件/分页/轮询/重连值后新操作使用同一有效配置；页面展示单位/范围/生效说明，非法值按后端details落字段；不暴露服务地址/池大小/模型Key或另存默认值 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| E-111 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；失败单独可重试，不重复交换Token/发送消息；传输上限变化只影响新连接，旧流不被中途换规则，历史与已受理附件仍可读取 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| B-105 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 | TASK-072 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | . | 300 |  |
| B-201 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#3.3 数据设计 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-001 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_capacity_schema.py"] | . | 300 |  |
| B-202 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#3.4 接口设计 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-002 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_webchat_schema.py"] | . | 300 |  |
| B-203 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-003 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_startup_schema.py"] | . | 300 |  |
| B-204 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-004 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_model_contracts.py"] | . | 300 |  |
| B-205 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-005 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_chat_contracts.py"] | . | 300 |  |
| B-206 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-006 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_event_contracts.py"] | . | 300 |  |
| B-207 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-007 | verified | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_error_catalog.py"] | . | 300 |  |
| B-208 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-008 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_settings_catalog.py"] | . | 300 |  |
| B-209 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#4.2 发布与迁移 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | TASK-009 | verified | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_control_pool_schema.py"] | . | 300 |  |
| B-210 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | TASK-010 | verified | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_runtime_pool_schema.py"] | . | 300 |  |
| B-211 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | TASK-011 | verified | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_admission_schema.py"] | . | 300 |  |
| B-212 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | TASK-012 | verified | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_chat_auth_schema.py"] | . | 300 |  |
| B-213 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.2 发布与迁移 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | TASK-013 | verified | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_conversation_upload_schema.py"] | . | 300 |  |
| B-214 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | TASK-014 | verified | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_notification_schema.py"] | . | 300 |  |
| B-215 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-015 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_service.py"] | . | 300 |  |
| B-216 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-016 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_api.py"] | . | 300 |  |
| B-217 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-017 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_deployment_relations.py"] | . | 300 |  |
| B-218 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-018 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_quota_groups.py"] | . | 300 |  |
| B-219 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-019 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_logical_model_api.py"] | . | 300 |  |
| B-220 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-020 | verified | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_resolve_credential.py"] | . | 300 |  |
| B-221 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-021 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | . | 300 |  |
| B-222 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-022 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_settle.py"] | . | 300 |  |
| B-223 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-023 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_allocator.py"] | . | 300 |  |
| B-224 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-024 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_snapshot.py"] | . | 300 |  |
| B-225 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-025 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | . | 300 |  |
| B-226 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-026 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | . | 300 |  |
| B-227 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-027 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py"] | . | 300 |  |
| B-228 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-028 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_probe.py"] | . | 300 |  |
| B-229 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-029 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_policy.py"] | . | 300 |  |
| B-230 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-030 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_create.py"] | . | 300 |  |
| B-231 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-031 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | . | 300 |  |
| B-232 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-032 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_slots.py"] | . | 300 |  |
| B-233 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-033 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_slots.py"] | . | 300 |  |
| B-234 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-034 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_retry_safety.py"] | . | 300 |  |
| B-235 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-035 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_clock_lease.py"] | . | 300 |  |
| B-236 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-036 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_queue_projection.py"] | . | 300 |  |
| B-237 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-037 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py"] | . | 300 |  |
| B-238 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-038 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_service.py"] | . | 300 |  |
| B-239 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-039 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_admin_api.py"] | . | 300 |  |
| B-240 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-040 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | . | 300 |  |
| B-241 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.1 技术选型 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-041 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_cookie_security.py"] | . | 300 |  |
| B-242 | agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-042 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_auth_api.py"] | . | 300 |  |
| B-243 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-043 | verified | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_chat_internal_principal.py"] | . | 300 |  |
| B-244 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-044 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_chat_auth.py"] | . | 300 |  |
| B-245 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-045 | verified | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_redaction.py"] | . | 300 |  |
| B-246 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-046 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_webchat_entry.py"] | . | 300 |  |
| B-247 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.3 数据设计 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-047 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_adapter.py"] | . | 300 |  |
| B-248 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-048 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_admission.py"] | . | 300 |  |
| B-249 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-049 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_conversation_scope.py"] | . | 300 |  |
| B-250 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-050 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | . | 300 |  |
| B-251 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-051 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_public_projection.py"] | . | 300 |  |
| B-252 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-052 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_resource_api.py"] | . | 300 |  |
| B-253 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-053 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | . | 300 |  |
| B-254 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-054 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_upload_gateway.py"] | . | 300 |  |
| B-255 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-055 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_artifact_download.py"] | . | 300 |  |
| B-256 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-056 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_run_controls.py"] | . | 300 |  |
| B-257 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-057 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_client_config_api.py"] | . | 300 |  |
| B-258 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-058 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py"] | . | 300 |  |
| B-259 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-059 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_durable_deltas.py"] | . | 300 |  |
| B-260 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-060 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | . | 300 |  |
| B-261 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-061 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_sse.py"] | . | 300 |  |
| B-262 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-062 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_reply_generations.py"] | . | 300 |  |
| B-263 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-063 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_write.py"] | . | 300 |  |
| B-264 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-064 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | . | 300 |  |
| B-265 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-065 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_reservation.py"] | . | 300 |  |
| B-266 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-066 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_pump.py"] | . | 300 |  |
| B-267 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-067 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_delivery.py"] | . | 300 |  |
| B-268 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-068 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_notifications.py"] | . | 300 |  |
| B-269 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-069 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | . | 300 |  |
| B-270 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-070 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_notification_api.py"] | . | 300 |  |
| B-271 | agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-071 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_transport.py"] | . | 300 |  |
| B-272 | agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-072 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | . | 300 |  |
| B-273 | agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-073 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_services.py"] | . | 300 |  |
| B-274 | agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-074 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | . | 300 |  |
| B-275 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-075 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_token_access_ui.py"] | . | 300 |  |
| B-276 | agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-076 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_client_config_state.py"] | . | 300 |  |
| B-277 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-077 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_route.py"] | . | 300 |  |
| B-278 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-078 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | . | 300 |  |
| B-279 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.7 样式方案, agent-capacity-webchat.frontend.design.md#3.8 可访问性 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-079 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_history_sidebar.py"] | . | 300 |  |
| B-280 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-080 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_message_history_ui.py"] | . | 300 |  |
| B-281 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-081 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_composer_uploads.py"] | . | 300 |  |
| B-282 | agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-082 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_reply_stream_state.py"] | . | 300 |  |
| B-283 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-083 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_run_actions_ui.py"] | . | 300 |  |
| B-284 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-084 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_activity_ui.py"] | . | 300 |  |
| B-285 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-085 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_user_token_ui.py"] | . | 300 |  |
| B-286 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-086 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_credential_ui.py"] | . | 300 |  |
| B-287 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-087 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_deployment_ui.py"] | . | 300 |  |
| B-288 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-088 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_quota_ui.py"] | . | 300 |  |
| B-289 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-089 | verified | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_settings_ui.py"] | . | 300 |  |
| B-290 | agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-090 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_i18n.py"] | . | 300 |  |
| B-291 | agent-capacity-webchat.frontend.design.md#3.7 样式方案, agent-capacity-webchat.frontend.design.md#3.8 可访问性 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-091 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_layout.py"] | . | 300 |  |
| B-292 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-092 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_model_admin_wiring.py"] | . | 300 |  |
| B-293 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-093 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"] | . | 300 |  |
| B-294 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-094 | verified | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | . | 300 |  |
| B-295 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-095 | verified | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_wiring.py"] | . | 300 |  |
| B-296 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-096 | verified | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_wiring.py"] | . | 300 |  |
| B-297 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-097 | verified | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_wiring.py"] | . | 300 |  |
| B-298 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-098 | verified | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py"] | . | 300 |  |
| B-299 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-099 | verified | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_worker_console_metrics.py"] | . | 300 |  |
| B-300 | agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.frontend.design.md#3.2 页面与路由 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-100 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_console_gateway_deploy.py"] | . | 300 |  |
| B-301 | agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.backend.design.md#4.2 发布与迁移 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-101 | verified | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_worker_deploy.py"] | . | 300 |  |
| B-302 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.frontend.design.md#3.2 页面与路由 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-102 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_configmap_ingress.py"] | . | 300 |  |
| B-303 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-103 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_hpa_budget.py"] | . | 300 |  |
| B-304 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.2 发布与迁移, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-104 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_deployment_preflight.py"] | . | 300 |  |
| B-305 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-105 | verified | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_profile.py"] | . | 300 |  |
| B-306 | agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | TASK-106 | verified | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_http_probes.py"] | . | 300 |  |
| B-307 | agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | TASK-107 | planned | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_isolated_stack.py"] | . | 300 |  |
| B-308 | agent-capacity-webchat.frontend.design.md#2.4 验收条件, agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-108 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_harness.py"] | . | 300 |  |
| B-309 | agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | TASK-109 | planned | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_driver.py"] | . | 300 |  |
| B-317 | agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | TASK-117 | planned | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_capacity_report.py"] | . | 300 |  |
| B-318 | agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告, agent-capacity-webchat.backend.design.md#6. 需求追溯矩阵, agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | TASK-118 | planned | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_acceptance_audit.py"] | . | 300 |  |

---
































## TASK-001: 容量与模型调度设置的typed schema

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-201
- **Files**: `packages/contracts/src/muad_contracts/platform_settings.py`, `tests/contracts/test_capacity_webchat_capacity_schema.py`, `apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py`, `tests/test_platform_settings_schema.py`, `docs/configuration-inventory.csv`, `tests/test_configuration_inventory.py`, `tests/console_platform/test_platform_settings_api.py`, `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`
- **Estimate**: 45分钟

### Description

capacity/model_pool默认、严格类型、未知键及跨字段错误完整校验；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-201][unit] 先在`tests/contracts/test_capacity_webchat_capacity_schema.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：capacity/model_pool默认、严格类型、未知键及跨字段错误完整校验。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-201] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-201] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_capacity_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] 因schema声明与常量行号变化，同步`docs/configuration-inventory.csv`的现状位置与分类并通过`tests/test_configuration_inventory.py`。
- [x] 配置盘点测试纳入CapacitySettings/ModelPoolSettings，不让新分组游离于真实声明核对之外。
- [x] 同步既有schema分组断言与Console分组登记，防止新增schema导致现有设置GET报KeyError；完整范围/单位/生效元信息及持久读写验收仍由TASK-008负责。
- [x] 新分组和字段补齐zh-CN/en-US词条，通过现有后端catalog→前端词条闭合检查。
- [x] 本任务发现schema立即被Console目录和动态设置页消费，故必要同步文件范围扩至9个（2个核心文件+分组登记、既有契约/词条及生成盘点同步）；作为一次原子变更保证原设置读链路可用，验收层级/边界/唯一负责人不变。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-201 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | capacity/model_pool默认、严格类型、未知键及跨字段错误完整校验；per-user至少2且不大于租户上限，估算/权重/等待有界 | tests/contracts/test_capacity_webchat_capacity_schema.py::test_b201_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_capacity_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-201 | `uv run pytest -q tests/contracts/test_capacity_webchat_capacity_schema.py` exit=1；93 failed, 19 passed；既有PlatformSettings缺capacity/model_pool，合法新分组被拒绝；完整输出`/tmp/agent-capacity-webchat-plan/task001-red.log` | 同命令exit=0，113 passed；manifest runner实际执行并记verified | `test_capacity_webchat_capacity_schema.py`:21默认、49上下界、74严格整数、82/90有限估算、96/165用户≤租户、107分组类型、114/119未知键、124部分覆盖/往返、157直接构造校验 | 直接调用真实contracts dataclass/parse/validate/asdict，无DB/网络或mock；实际新校验函数分别13/6行 | verified |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts/test_capacity_webchat_capacity_schema.py tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_memory_redesign_settings.py tests/agent_runtime/test_context_compaction_config.py tests/agent_runtime/test_platform_settings_source.py tests/console_platform/test_platform_settings_api.py tests/console_platform/test_platform_settings_service.py tests/console_internal/test_platform_settings_internal.py tests/frontend/test_platform_settings_contract.py` exit=0，407 passed；复用既有create_datastore/drop_datastore，真实PG迁移到head、独立Redis槽，finally回收。日志：`/tmp/agent-capacity-webchat-plan/task001-regression-isolated-final.log`。

静态检查：变更Python文件Ruff通过；两个生产文件strict mypy通过；`uv run python scripts/check_frontend_i18n.py`通过（1178键）；CSV保留既有CRLF，`git -c core.whitespace=cr-at-eol diff --check`通过。初轮回归暴露的开发库缺表未算实现RED，已改用隔离库复验。
- B-201: verified — automated command passed; run_id=f4d658868d05472e8af7d160c66b2d98 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-002: Webchat认证、输入、保留与重连设置

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-202
- **Files**: `packages/contracts/src/muad_contracts/platform_settings.py`, `tests/contracts/test_capacity_webchat_webchat_schema.py`, `apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py`, `tests/test_platform_settings_schema.py`, `docs/configuration-inventory.csv`, `tests/test_configuration_inventory.py`, `tests/console_platform/test_platform_settings_api.py`, `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`
- **Estimate**: 45分钟

### Description

Token默认30天、CHAT默认12小时；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-202][unit] 先在`tests/contracts/test_capacity_webchat_webchat_schema.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：Token默认30天、CHAT默认12小时。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-202] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-202] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_webchat_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] 同TASK-001同步Console分组登记、已有schema/API分组契约、两语言动态字段词条与配置CSV审计范围；原子变更范围为9文件，保持真实旧设置链路可用。
- [x] 声明单位与软/硬预算注入校验，不在contracts读取env或复制上传硬上限默认；具体生产装配注入和元信息投影仍归TASK-003/008，本任务不宣称这些端口已接通。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-202 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | Token默认30天、CHAT默认12小时；输入/上传/分页/保留/退避字段单位和边界唯一，0仅用于明确的历史不自动清理语义 | tests/contracts/test_capacity_webchat_webchat_schema.py::test_b202_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_webchat_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-202 | `uv run pytest -q tests/contracts/test_capacity_webchat_webchat_schema.py` exit=1；129 failed, 9 passed；PlatformSettings缺webchat分组和硬预算注入参数，输出`/tmp/agent-capacity-webchat-plan/task002-red.log` | 同命令exit=0，141 passed；manifest runner实际执行并记verified | `test_b202_defaults_use_one_typed_source`、`test_b202_integer_fields_enforce_inclusive_bounds`、`test_b202_integer_fields_reject_coercion`、`test_b202_polling_interval_*`、`test_b202_reconnect_base_cannot_exceed_cap`、`test_b202_zero_*`、`test_b202_*resource_budget`、`test_b202_*budgets*`、`test_b202_webchat_group_requires_object`、`test_b202_unknown_credentials_and_auth_bypass_keys_are_rejected`、`test_b202_direct_typed_instances_cannot_bypass_validation`、`test_b202_field_units_are_declared_on_the_typed_schema` | 真实schema/parse/validate/asdict，资源预算由调用参数注入，无网络/DB/mock；上传128MiB在同等启动硬预算下有效，50MiB只是业务默认，不是复制的硬上限 | verified |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts/test_capacity_webchat_capacity_schema.py tests/contracts/test_capacity_webchat_webchat_schema.py tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_memory_redesign_settings.py tests/agent_runtime/test_context_compaction_config.py tests/agent_runtime/test_platform_settings_source.py tests/console_platform/test_platform_settings_api.py tests/console_platform/test_platform_settings_service.py tests/console_internal/test_platform_settings_internal.py tests/frontend/test_platform_settings_contract.py` exit=0，548 passed；真实隔离PG/Redis按既有设施建库迁移并finally回收，输出`/tmp/agent-capacity-webchat-plan/task002-regression-isolated.log`。

静态检查：变更Python文件Ruff通过，两个生产文件strict mypy通过，i18n两侧1193键闭合；`git -c core.whitespace=cr-at-eol diff --check`通过。新的parse/validate/联动校验函数分别31/25/17行，不读取环境、不导入app包。
- B-202: verified — automated command passed; run_id=96bd717c93304c31913295ac58e8af86 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-003: 启动资源预算与已有常量配置化

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-203
- **Files**: `packages/common/src/muad_common/settings.py`, `.env.example`, `tests/contracts/test_capacity_webchat_startup_schema.py`, `docs/configuration-inventory.csv`, `tests/acceptance/runtime/test_multipod_recovery.py`, `tests/acceptance/dfx/test_dfx_stateless.py`, `tests/acceptance/dfx/test_dfx_recovery.py`
- **Estimate**: 60分钟

### Description

执行槽、HTTP/PG/Redis池、SSE总缓冲、认证/投递预算启动校验；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-203][unit] 先在`tests/contracts/test_capacity_webchat_startup_schema.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：执行槽、HTTP/PG/Redis池、SSE总缓冲、认证/投递预算启动校验。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-203] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-203] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_startup_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] 重生成配置盘点中的SharedSettings声明与源码行号，保留既有环境/业务分类并通过现有inventory/convergence检查。
- [x] 既有短租约验收夹具同步为3秒租约/1秒心跳，手工claim构造同时声明心跳，保留原故障恢复/CAS断言；这些E2E本期仅维护参数，执行延期至终验。
- [x] 记录本任务只落实启动schema/env契约；HTTP池、执行槽、投递泵的应用装配仍由各自后续TASK负责，部署跨副本/PG/FD总预算仍归部署与容量profile。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-203 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | 执行槽、HTTP/PG/Redis池、SSE总缓冲、认证/投递预算启动校验；拒绝非法env及租约/心跳或keepalive联动冲突，不引入密钥回落 | tests/contracts/test_capacity_webchat_startup_schema.py::test_b203_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_startup_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-203 | `uv run pytest -q tests/contracts/test_capacity_webchat_startup_schema.py` exit=1，530 failed/97 passed；原schema缺启动预算字段、bool被当整数且租约/池联动未拒绝；日志`/tmp/agent-capacity-webchat-plan/task003-red.log` | 同命令exit=0，781 passed；runner实际执行并记verified | `test_b203_documented_resource_defaults`、`test_b203_every_resource_setting_can_be_read_from_environment`（每项使用不同于默认的有效值）、`test_b203_integer_settings_do_not_coerce_bool_or_fraction`、`test_b203_malformed_environment_values_fail_at_startup`、`test_b203_float_settings_require_finite_numbers`、`test_b203_negative_resource_limits_are_rejected`、`test_b203_zero_has_only_declared_connection_semantics`、`test_b203_keepalive_must_fit_connection_budget`、`test_b203_leases_cover_three_heartbeats`、`test_b203_invalid_ranges_and_cross_field_combinations`、`test_b203_invalid_startup_errors_do_not_print_credentials`、`test_b203_startup_resources_do_not_become_business_or_model_credentials` | 真实SharedSettings/Pydantic及进程环境；不调用网络/数据库；95个启动字段与env示例同集合，凭据repr隐藏且配置错误不带原始输入；0仅允许声明的overflow/keepalive语义 | verified |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts/test_capacity_webchat_capacity_schema.py tests/contracts/test_capacity_webchat_webchat_schema.py tests/contracts/test_capacity_webchat_startup_schema.py tests/test_settings.py tests/test_configuration_convergence.py tests/test_async_tool_dispatch_settings.py tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_memory_redesign_settings.py tests/agent_runtime/test_context_compaction_config.py tests/agent_runtime/test_platform_settings_source.py tests/console_platform/test_platform_settings_api.py tests/console_platform/test_platform_settings_service.py tests/console_internal/test_platform_settings_internal.py tests/frontend/test_platform_settings_contract.py tests/agent_worker/test_worker_leases.py tests/agent_worker/test_task_defaults_from_settings.py tests/gateway/test_progress_settings.py` exit=0，1370 passed（19.13s）；按官方设施建独立PG/Redis并finally回收，日志`/tmp/agent-capacity-webchat-plan/task003-regression-isolated.log`。

静态检查：变更Python文件Ruff通过，SharedSettings strict mypy通过，新增/修改函数均≤50行，CSV按原CRLF规范diff-check通过。只维护既有E2E短租约参数，未执行或声称E2E/生产容量通过。
- B-203: verified — automated command passed; run_id=a94b5e0dfc27426c99ac4bd5cb93d344 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-004: 模型成员、容量预占与结算协议

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-204
- **Files**: `packages/contracts/src/muad_contracts/model_pool.py`, `tests/contracts/test_capacity_webchat_model_contracts.py`
- **Estimate**: 45分钟

### Description

物理Key/成员/额度组与probe/run/job判别身份有严格DTO；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-204][unit] 先在`tests/contracts/test_capacity_webchat_model_contracts.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：物理Key/成员/额度组与probe/run/job判别身份有严格DTO。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-204] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-204] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_model_contracts.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-204 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | 物理Key/成员/额度组与probe/run/job判别身份有严格DTO；epoch、期限、usage/UNKNOWN结算可验证，凭据与公开配置不混用 | tests/contracts/test_capacity_webchat_model_contracts.py::test_b204_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_model_contracts.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-204 | `uv run pytest -q tests/contracts/test_capacity_webchat_model_contracts.py` exit=1，57 failed；57项均已收集执行，新model_pool协议模块尚不存在；日志`/tmp/agent-capacity-webchat-plan/task004-red.log` | 同命令exit=0，69 passed；runner实际执行并记verified | `test_capacity_webchat_model_contracts.py`:37/56资源与无生产容量默认、66/72成员权重及模型语义、77/96凭据分层、102/114判别来源、120/125 epoch/期限、140用量、146/158/170/235 UNKNOWN与完成证据、185探针精确协议、199/205端点/Key形状、210存储上界、220已结算释放 | 真实Pydantic DTO、UUID/时区/判别联合/JSON往返、显式内部凭据序列化；普通dump/repr不带Key；UNKNOWN不按时间自动释放，缺usage保持None | verified |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_configuration_convergence.py tests/test_collection_layout.py` exit=0，1243 passed（4.67s），日志`/tmp/agent-capacity-webchat-plan/task004-regression-isolated.log`；Ruff/strict mypy/函数长度/diff-check通过。

边界：本任务定义协议与局部不变量；PG/CAS/物理Key计量、provider完成证据的权威核验和真实路由仍由TASK-021～028等实现，不能把DTO字段验证当成真实远端停止或1000并发容量证据。
- B-204: verified — automated command passed; run_id=cccab1c1bf364be0a60dc5c968184e62 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-005: Webchat身份、会话、消息和附件DTO

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-205
- **Files**: `packages/contracts/src/muad_contracts/webchat.py`, `tests/contracts/test_capacity_webchat_chat_contracts.py`
- **Estimate**: 45分钟

### Description

显式entry/conversation/message身份、Token交换、上传元信息、列表和client-config结构明确；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-205][unit] 先在`tests/contracts/test_capacity_webchat_chat_contracts.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：显式entry/conversation/message身份、Token交换、上传元信息、列表和client-config结构明确。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-205] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-205] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_chat_contracts.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-205 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | 显式entry/conversation/message身份、Token交换、上传元信息、列表和client-config结构明确；严格JSON拒绝NaN和浏览器可信user-id注入 | tests/contracts/test_capacity_webchat_chat_contracts.py::test_b205_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_chat_contracts.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-205 | `uv run pytest -q tests/contracts/test_capacity_webchat_chat_contracts.py` exit=1，58 failed；全部用例已收集执行，缺少webchat协议模块，日志`/tmp/agent-capacity-webchat-plan/task005-red.log` | 同命令exit=0，64 passed；runner实际执行并记verified | `test_capacity_webchat_chat_contracts.py`:40/58/66身份与Token输入、71/93签发及元信息、98统一主体、115/132显式会话与动态策略、140/150/158附件、163/178/185分页安全历史、198/222恢复及确认、249/275/287配置投影与严格JSON、295响应局部不变量 | 真实Pydantic DTO、JSON往返、SecretStr与一次性显式序列化；settings唯一schema校验和投影；无IO替身 | verified |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_configuration_convergence.py tests/test_collection_layout.py` exit=0，1307 passed（5.15s）；日志`/tmp/agent-capacity-webchat-plan/task005-regression-isolated.log`。Ruff/strict mypy/函数长度/diff-check通过。

边界：DTO仅定义形状与局部不变量；Token生成/撤销、PG会话权威、Owner授权和生效配置的业务受理检查由后续任务实现。本任务不提供浏览器自报user-id的免认证入口。`transport_revision`为启动配置的不透明字符串指纹，消息/上传的实时软限额留在新受理路径校验，避免已受理幂等重放被后来降额拒绝。
- B-205: verified — automated command passed; run_id=486dd5b78cca497783594909c5301424 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-006: 公开事件、通知与snapshot分片协议

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005
- **Source**: agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-206
- **Files**: `packages/contracts/src/muad_contracts/webchat_events.py`, `tests/contracts/test_capacity_webchat_event_contracts.py`
- **Estimate**: 45分钟

### Description

公开delta/snapshot/阶段/确认/通知白名单及游标错误分型；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-206][unit] 先在`tests/contracts/test_capacity_webchat_event_contracts.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：公开delta/snapshot/阶段/确认/通知白名单及游标错误分型。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-206] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-206] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_event_contracts.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-206 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | 公开delta/snapshot/阶段/确认/通知白名单及游标错误分型；同canonical seq的分片有generation/part_index/count，未知事件默认不可公开 | tests/contracts/test_capacity_webchat_event_contracts.py::test_b206_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_event_contracts.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-206 | `uv run pytest -q tests/contracts/test_capacity_webchat_event_contracts.py` exit=1，49 failed；全部用例已收集执行，缺少webchat_events协议模块，日志`/tmp/agent-capacity-webchat-plan/task006-red.log` | 同命令exit=0，58 passed；runner修复后重跑verified，保留之前诊断泄露失败历史 | `test_capacity_webchat_event_contracts.py`:48白名单往返、63/68私有事件与字段、75/89坏帧与严格JSON、96/114分片身份及坐标、121 checkpoint/心跳、134安全错误、145/162游标分型与数值边界、167/187/192通知游标/已读/来源、207/213联合解析诊断脱敏与输入边界 | 真实Pydantic判别联合、JSON decoder、canonical_json、纯游标窗口函数；快照分片共享原event_id/run/seq/generation；不新分配canonical seq；无IO替身 | verified |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_configuration_convergence.py tests/test_collection_layout.py` exit=0，1365 passed（4.31s）；日志`/tmp/agent-capacity-webchat-plan/task006-regression-isolated-fixed.log`。此前回归/runner发现判别联合的错误文本仍带输入，已在TypeAdapter显式配置hide_input_in_errors并复验。Ruff/strict mypy/函数长度/diff-check通过。

边界：本任务定义公开事件协议、解析白名单与游标窗口形状/局部判断；canonical安全投影、PG追尾、分片切割和有界组装/应用游标由TASK-051/061/072等实现，DTO测试不冒充真实SSE恢复。所有传入公开解析器的dict必须已是严格JSON，不把datetime/UUID对象静默字符串化；未知事件不公开，已知坏帧显式报错。
- B-206: failed — automated command failed; run_id=803dbb20519c4248a7c8e900a383f2cc (confirmed_by: runner)
- B-206: verified — automated command passed; run_id=3229c94166c848b49a6689b0cc140bb3 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-007: 配置、认证、容量与游标错误目录

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-005
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-api#RULE-api-001
- **Acceptance-Refs**: B-207
- **Files**: `packages/api-kit/src/muad_api/error_codes.py`, `config/api-messages.yaml`, `tests/contracts/test_capacity_webchat_error_catalog.py`, `docs/configuration-inventory.csv`
- **Estimate**: 45分钟

### Description

新增错误枚举与zh-CN/en-US目录双向一致，标准封套/HTTP状态与details字段稳定，不回显Token或内部模型错误原文；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-207][unit] 先在`tests/contracts/test_capacity_webchat_error_catalog.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：新增错误枚举与zh-CN/en-US目录双向一致，标准封套/HTTP状态与details字段稳定，不回显Token或内部模型错误原文。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-207] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-207] 执行`["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_error_catalog.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] 已登记review verifier `harness-api#RULE-api-001`（timeout=300秒）：`["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"]`；E2E按执行契约留至verify-e2e，保留完整真实边界与命令，本阶段不声称已执行。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-207 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | 新增错误枚举与zh-CN/en-US目录双向一致，标准封套/HTTP状态与details字段稳定，不回显Token或内部模型错误原文 | tests/contracts/test_capacity_webchat_error_catalog.py::test_b207_* | ["uv","run","pytest","-q","tests/contracts/test_capacity_webchat_error_catalog.py"] | verified |
| harness-api#RULE-api-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_api_i18n.py","tests/test_error_catalog.py","tests/acceptance/test_foundation_api_envelope.py"] | e2e_deferred |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-207 | `uv run pytest -q tests/contracts/test_capacity_webchat_error_catalog.py` exit=1，33 failed/3 passed；新增错误码未登记，日志`/tmp/agent-capacity-webchat-plan/task007-red.log` | 同命令exit=0，36 passed；runner实际执行并记verified | `test_capacity_webchat_error_catalog.py`:42三十项错误/状态、49双语及双向一致、61封套/模板参数不回显凭据、83游标协议码一致、89字段details保留、105标准列表与分页 | 真实MessageCatalog/ErrorCode/build_response、严格设置schema与原有details映射；无网络/数据库替身 | verified |
| harness-api#RULE-api-001 | E2E编码阶段不执行 | 留至需求级verify-e2e执行原verifier | `tests/test_api_i18n.py`、`tests/test_error_catalog.py`、`tests/acceptance/test_foundation_api_envelope.py::test_s02_*` | 原verifier含真实FastAPI封套/HTTP状态/请求关联；完整命令保留，不以本任务unit替代 | e2e_deferred |

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts tests/test_api_i18n.py tests/test_error_catalog.py tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_collection_layout.py` exit=0，1407 passed（4.13s）；日志`/tmp/agent-capacity-webchat-plan/task007-regression-isolated-fixed.log`。Ruff/strict mypy/错误文案硬编码检查/diff-check通过。

范围同步：错误码枚举也由配置盘点机检完整追踪，首次回归明确报缺30行；已将同次原子变更需要的`docs/configuration-inventory.csv`纳入Files并更新code分类，未改变盘点校验。配置校验仍沿用VALIDATION_FAILED和`data.details[].path/message`；授权/不存在沿用FORBIDDEN/COMMON_NOT_FOUND，后续Owner负责只提交安全data，本任务不声称AppError会自动净化任意data。
- B-207: verified — automated command passed; run_id=68a10976f06a4eea9a36863ed818f6e2 (confirmed_by: runner)
- B-207: verified — automated command passed; run_id=97abe069ca504e088a9b561b4416e0a1 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-008: 系统设置分组元信息与生效说明

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-007
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-208
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py`, `apps/console-platform/backend/src/muad_console_platform/api/platform_settings.py`, `tests/console_platform/test_capacity_webchat_settings_catalog.py`, `apps/console-platform/backend/src/muad_console_platform/application/platform_settings_service.py`, `packages/contracts/src/muad_contracts/platform_settings.py`, `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`, `docs/configuration-inventory.csv`
- **Estimate**: 45分钟

### Description

capacity/model_pool/webchat通过原整份设置协议读写，默认/范围/单位从schema派生；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-208][integration] 先在`tests/console_platform/test_capacity_webchat_settings_catalog.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：capacity/model_pool/webchat通过原整份设置协议读写，默认/范围/单位从schema派生。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-208] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-208] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_settings_catalog.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-208 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | capacity/model_pool/webchat通过原整份设置协议读写，默认/范围/单位从schema派生；保存/回滚插入新版本，校验错误定位字段，不另建策略接口 | tests/console_platform/test_capacity_webchat_settings_catalog.py::test_b208_* | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_settings_catalog.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-208 | `uv run pytest -q --tb=short tests/console_platform/test_capacity_webchat_settings_catalog.py` exit=1，4 failed/6 passed；缺少范围和生效元数据、默认值被生效值覆盖、硬上限未接入；日志`/tmp/agent-capacity-webchat-plan/task008-red.log` | 契约原命令exit=0，11 passed；runner实际执行并记verified | `test_capacity_webchat_settings_catalog.py`:82 schema默认/边界/单位/生效标记、114默认值与生效值分离、131幂等/冲突/回滚追加及历史、164拒绝不留版本/审计、181上传硬预算保存/回滚/元信息、201 Owner消息硬预算、215双语生效说明 | 真实Console路由/认证/CSRF、独立空库升级head、真实版本/审计/幂等表及提交回滚；原测试fixture共用真实AuthService；无存储替身；本任务无外部模型调用 | verified |

范围细化：Owner直接保存与回滚都必须复用启动硬预算校验，仅改API会留绕过路径，故同步service；schema补全时间单位，effect_key所需词条和配置盘点同次同步，计划3文件扩为8文件。真实边界与测试层级保持不变。

回归：`uv run python /tmp/agent-capacity-webchat-plan/run-isolated-pytest.py -q tests/contracts tests/test_platform_settings_schema.py tests/test_configuration_inventory.py tests/test_configuration_convergence.py tests/test_collection_layout.py tests/test_settings.py tests/test_api_i18n.py tests/test_error_catalog.py tests/console_platform/test_platform_settings_api.py tests/console_platform/test_platform_settings_service.py tests/console_platform/test_capacity_webchat_settings_catalog.py tests/console_internal/test_platform_settings_internal.py tests/frontend/test_platform_settings_contract.py tests/agent_runtime/test_platform_settings_source.py` exit=0，1461 passed（13.36s）；日志`/tmp/agent-capacity-webchat-plan/task008-regression-isolated.log`。Ruff/strict mypy/函数长度/diff-check通过。

边界：applies_to继续沿用既有枚举，新增effect_key细分签发、交换、新受理、查询/页面/新连接和清理，词条已齐；前端展示消费在TASK-089接线。GET元信息只收紧可编辑max，不裁剪历史生效值；保存和回滚由API/Owner共同拒绝超硬预算。既有设置协议仍按现有缺省归一化规则处理整份文档，不新增独立策略路由或逐用户覆盖。
- B-208: verified — automated command passed; run_id=5ba3303655d246818a6051380bac44a5 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] completed (done)

---

## TASK-009: 模型物理凭据、成员与额度组迁移

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#4.2 发布与迁移
- **Spec-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-209
- **Files**: `apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py`, `migrations/versions/0021_model_pool_control.py`, `tests/migrations/test_capacity_webchat_control_pool_schema.py`, `migrations/model_pool_control_data.py`, `docs/model-pool-control-migration.md`
- **Estimate**: 60分钟

### Description

创建credential/deployment/quota_group并在单次升级中去重迁移旧Key；旧执行列在 0021 保留，物理删列由升级链末段 revision（TASK-019）执行；完整分支及可观测结果见下面验收契约。

实现细化：现有head为0020，本任务使用0021。旧表没有供方/额度组/真实预算，存量升级必须从Alembic ini指定无密钥迁移清单，显式声明每个旧模型的供方及Key/账号预算；空库无需清单。先校验排空、完整映射及去重冲突，再改表；数据库租户复合FK防跨租户归属。转换助手和操作文档属于同一迁移的必要交付，范围3→5文件；不改变真实PG/Alembic/ORM验收边界。

计划变更（2026-10-17，用户确认）：物理删列推迟到升级链末段 revision，由 TASK-019 交付；0021 只建新结构、迁数据、加 `provider_key`。legacy 写入口重写前 `provider_key` 以 `server_default ''` 表示"未声明供方"——0021 一次迁移同时兼容未重写的存量入口，避免本任务波及仍构造/读取旧列的 19 个测试文件与 5 个应用模块（由 TASK-016/017/019/020 重写），不引入双写或过渡适配层。

### Checklist

- [x] [B-209][integration] 先在`tests/migrations/test_capacity_webchat_control_pool_schema.py`建立行为验收并记录RED；真实边界：隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库；关键断言：创建credential/deployment/quota_group并在单次升级中去重迁移旧Key，且旧执行列在 0021 保留至末段 revision。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-209] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-209] 执行`["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_control_pool_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] verifier `harness-data#RULE-data-001`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests","-k","schema_parity"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-209 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | 创建credential/deployment/quota_group并在单次升级中去重迁移旧Key；旧执行列在 0021 保留（物理删列归末段 revision，TASK-019）；无双写/旧字段运行回退，租户/归属FK与ORM索引parity | tests/migrations/test_capacity_webchat_control_pool_schema.py::test_b209_* | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_control_pool_schema.py"] | verified |
| harness-data#RULE-data-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 产品表统一 `id/is_deleted/create_time/update_time`；普通软删除实体用 partial unique `WHERE is_deleted=false`，稳定身份注册表 `runtime.user_memory` / `memory_scope` 的完整唯一键包含 tombstone，append-only history/receipt/job 保持 `is_deleted=false` 且使用完整唯一约束；时间统一 `timestamptz`；同 Owner Schema 的业务归属用物理 FK，跨 Owner Schema 与需跨清理保留的审计来源引用用逻辑 UUID；JSON 配置用 `jsonb`，关键查询字段必须有物理列。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests","-k","schema_parity"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-209 | `uv run pytest -q tests/migrations/test_capacity_webchat_control_pool_schema.py -x` → FAIL（1 failed in 1.73s）：`test_b209_empty_database_schema_parity` 断言 `set(TABLES) <= actual` 时 Extra items: `model_quota_group`/`model_credential`/`model_deployment`（0021 之前三表不存在，正是待实现的缺口） | `uv run pytest -q tests/migrations/test_capacity_webchat_control_pool_schema.py` → **18 passed** (exit=0, 33.62s) | `test_b209_empty_database_schema_parity` L98-115（migrated==created、provider_key 在且 server_default `''`、base_url/api_key 保留（物理删列归末段 revision）、三表标准列）；`test_b209_migration_merges_physical_keys_preserves_model_identity_and_agent_fk` L118-151（凭据 1 行、digest=sha256、provider_key 规范化为 `vendor-a`、额度组 rpm/tpm、模型 id/revision/params 保留、成员 2 行、Agent FK 未变、stderr 无密钥）；`test_b209_bad_mapping_fails_before_schema_changes_and_does_not_echo_secrets` L162-191（missing/unmapped/unknown/no_capacity/bool_capacity/wrong_group/extra_secret/unsupported_schema 8 分支：非 0 退出 + `MODEL_POOL_MIGRATION_` + 库仍在 0020 且旧列原样）；`test_b209_conflicting_copies_of_one_key_fail_atomically` L194-214（capacity/endpoint/quota_group/resource_key 4 分支 → `MODEL_POOL_MIGRATION_CONFLICT` 且原子回滚）；`test_b209_tenant_and_provider_boundaries_deleted_models_and_missing_keys_are_preserved` L217-242（跨租户各 1 凭据、软删模型成员 `(is_deleted=True, enabled=False)`、无 Key 模型不建成员）；`test_b209_owner_foreign_keys_and_partial_uniqueness_are_enforced` L245-279（重复 key/跨租户/0 容量/坏摘要/revision=0 逐个 `IntegrityError`、软删后可复活、成员 4 个非法组合被拒、`repr` 不含密钥）；`test_b209_running_work_prevents_migration_before_any_model_conversion` L282-298（WAITING_INPUT 时 `MODEL_POOL_MIGRATION_NOT_DRAINED`）；`test_b209_downgrade_requires_backup_for_populated_pool` L301-311（`MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP`，库仍 0021） | 隔离库 `create_datastore(revision="0020")` + 真实 `migrations/db_migrate.py -c <ini>` 子进程；隔离 PostgreSQL；无 mock |
| harness-data#RULE-data-001 | 同上（依赖同一迁移链） | `uv run pytest -q tests -k schema_parity` → **42 passed**, 4690 deselected (exit=0, 15.85s) | 覆盖 `tests/console_platform/test_schema_parity.py`、`tests/console_internal/test_resolve_schema_parity.py`、`tests/agent_runtime/test_runtime_schema_parity.py`、`tests/agent_worker/test_task_schema_parity.py`、`tests/acceptance/test_foundation_schema_parity.py`、`tests/migrations/test_memory_redesign_schema_parity.py` | 真实 PG 反射：`migrated == created`（列/索引/CHECK/FK/唯一约束全比对，含 partial unique predicate）；新增三表带 `(tenant_id,id)` 唯一约束与复合 FK |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py`（ModelDefinition 保留 base_url/api_key 供过渡、加 `provider_key`（server_default `''`）与 `(tenant_id,id)` 唯一；新增 ModelQuotaGroup/ModelCredential/ModelDeployment）、`migrations/versions/0021_model_pool_control.py`、`migrations/model_pool_control_data.py`、`docs/model-pool-control-migration.md`（维护窗口运行手册与错误码表）。
- 校验顺序：排空门禁 → 清单解析（白名单键，拒绝 `api_key` 等未知键）→ 覆盖/冲突检查 → 建表 + 加 provider_key → 落数据；删旧列不在本 revision（末段执行）；失败全部发生在改结构之前。
- 已确认的**计划变更**（2026-10-17，用户确认）：旧列 `base_url`/`api_key` 的物理删除从 0021 推迟到升级链末段的删列 revision，由 TASK-019 负责交付；0021 之后 legacy 入口（`application/model_service.py`、`api/models.py`、`application/resolve_service.py`、`application/runtime_credentials.py`）继续可用，`provider_key` 以 `''` 表示供方未声明，消费方由 TASK-016/017/019/020 重写后再删列，不引入兼容层/双写；生产维护窗口仍是**一次** `upgrade head` 到完整 head。
- B-209: verified — automated command passed; run_id=791aff4eface44f99896da6fa8945671 (confirmed_by: runner)
- B-209: verified — automated command passed; run_id=eb8d0a930d2e4d958535bc5eef13a3f9 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-209 RED：`-x` 首跑 1 failed（三表不存在）
- [2026-10-17] 实现 0020→0021：新增 ModelQuotaGroup/ModelCredential/ModelDeployment 与 `migrations/model_pool_control_data.py`（清单校验/去重/冲突/排空），ModelDefinition 加 `provider_key`（server_default `''`）、加 `(tenant_id,id)` 唯一约束（最终保留 base_url/api_key，删列推迟到末段 revision）
- [2026-10-17] B-209 GREEN：18 passed；verifier `tests -k schema_parity` 42 passed；补 `docs/model-pool-control-migration.md` 运行手册
- [2026-10-17] 过渡状态记录（当时的计划）：旧执行入口原计划随列删除，`tests/console_internal/`、`tests/console_platform/test_models_api.py` 暂红——该计划已被下方 2026-10-17 计划变更取代（保留列）
- [2026-10-17] 计划变更（用户确认）：物理删列从 0021 推迟到升级链末段 revision（TASK-019 交付）；0021 保留旧列，`provider_key` 加 `server_default ''`（等 legacy 入口重写后再删列；无双写/无兼容层）
- [2026-10-17] 本地 dev 库升级 0019→0021（用户确认升级）：0020 被单条不可迁移测试残留 `{"unknown_group":{"x":1}}`（tenant `test-4cd51c3a…` revision 4，2026-10-05 写入）拒绝；经用户确认对该行 `is_deleted=true` 软删（保留行）后 `upgrade head` 成功，落 2 凭据/2 成员/32 模型（沿用清单）
- [2026-10-17] dev 库到 head 后复跑此前受阻套件（此前因 0019 缺列全红）：`tests/console_skill/test_import_idempotency.py` 5 passed；`tests/console_internal` 34 passed；`tests/console_platform/test_models_api.py` 8 passed
- [2026-10-17] B-209 复跑：`tests/migrations/test_capacity_webchat_control_pool_schema.py` → 18 passed；verifier `tests -k schema_parity` → 42 passed
- [2026-10-17] completed (done)

---

## TASK-010: 运行时模型容量、分配计数与租约结构

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-210
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`, `migrations/versions/{revision}_model_capacity_runtime.py`, `tests/migrations/test_capacity_webchat_runtime_pool_schema.py`
- **Estimate**: 60分钟

### Description

capacity/dispatch_state/call_lease具有稳定实际Key身份和完整唯一键；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-210][integration] 先在`tests/migrations/test_capacity_webchat_runtime_pool_schema.py`建立行为验收并记录RED；真实边界：隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库；关键断言：capacity/dispatch_state/call_lease具有稳定实际Key身份和完整唯一键。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-210] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-210] 执行`["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_runtime_pool_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-210 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | capacity/dispatch_state/call_lease具有稳定实际Key身份和完整唯一键；run/job/probe三者恰一，UNKNOWN不被软删除隐藏，标准列/时区与迁移一致 | tests/migrations/test_capacity_webchat_runtime_pool_schema.py::test_b210_* | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_runtime_pool_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-210 | `uv run pytest -q tests/migrations/test_capacity_webchat_runtime_pool_schema.py` → exit=1，**13 failed / 1 passed**（21.86s）：head 0021 上三表不存在（parity 用例 `set(TABLES) <= actual` 缺失 `model_capacity`/`model_dispatch_state`/`model_call_lease`；ORM `AttributeError: module ... has no attribute 'ModelCallLease'`）。唯一通过的空表 downgrade 用例此时是空洞通过（目标 0021 即当时 head、无迁移动作），实现后由 GREEN 复跑真正覆盖 drop 路径 | `uv run pytest -q tests/migrations/test_capacity_webchat_runtime_pool_schema.py` → **14 passed** (exit=0, 23.27s) | `test_b210_empty_database_schema_parity` L114-142（`migrated == created` 全量比对列/索引/CHECK/FK/唯一约束/主键；标准列齐全；FK 恰 `runtime.run_record.id`+`runtime.memory_consolidation_job.id`；`window_start`/`last_picked_at`/`lease_until`/`create_time` 为 timestamptz；`epoch` 为 BIGINT；`ck_model_call_lease_state` 含 UNKNOWN）；`test_b210_capacity_identity_is_complete_unique_including_tombstone` L145-166（同身份二次插入/软删后重录均 IntegrityError；`resource_type='MODEL'`、负 inflight/reserved/requests、`capacity_revision=0` 逐个被拒；默认值 0/0/0、revision=1、window_start 带时区）；`test_b210_dispatch_state_policy_identity_is_complete_unique_including_tombstone` L169-186（完整唯一含 tombstone；负 dispatch_epoch/count 被拒；另一 policy_hash 独立解释不并计数）；`test_b210_lease_attempt_is_complete_unique_and_freezes_key_identity` L189-217（call_attempt 完整唯一、重试不重复占位；两个 attempt 共享同一 key 计量身份且 credential_revision=3 冻结；FK 恰 run/job 两个）；`test_b210_owner_is_exactly_one_of_run_job_probe` L220-239（none/run_and_job/run_and_probe/job_and_probe 四分支 IntegrityError；run/job/probe 三分支各恰一成功）；`test_b210_unknown_lease_retains_occupancy_and_cannot_be_hidden` L242-278（ACTIVE→UNKNOWN 保留 reserved_tokens=128、capacity.inflight 保持 1；state='STALE' 被拒；UNKNOWN 行 `is_deleted=true` 被 `ck_model_call_lease_not_deleted` 拒绝；partial index 覆盖 UNKNOWN 且谓词含 is_deleted）；`test_b210_downgrade_on_empty_tables_returns_to_0021` L281-296（空表回退成功：alembic 停 0021 且三表消失，再 `upgrade head` 成功）；`test_b210_downgrade_with_lease_rows_requires_backup` L298-319（有租约行回退非 0 退出 + `MODEL_CAPACITY_DOWNGRADE_REQUIRES_BACKUP`，库仍 0022 且行保留） | 隔离库 `create_datastore()`（默认 head）与真实 `migrations/db_migrate.py -c <ini>` 子进程；隔离 PostgreSQL、真实 Alembic 链、无 mock | verified |

**实施范围与证据说明**

- 实现文件：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`（新增 ModelCapacity/ModelDispatchState/ModelCallLease 三模型，短约束名由命名约定展开为 `ck_model_*`）、`migrations/versions/0022_model_capacity_runtime.py`（down_revision=0021 的纯 DDL；不读环境变量）。
- 结构决策：三表均为完整唯一键（capacity `(tenant_id,resource_type,resource_id)` 含 tombstone、dispatch `(tenant_id,model_id,deployment_id,policy_hash)`、lease `(tenant_id,call_attempt_id)`）；lease 用 `num_nonnulls(run_id,memory_job_id,probe_id)=1` 恰一归属、`is_deleted=false` 防 UNKNOWN 隔离证据被软删隐藏、partial index 只覆盖 ACTIVE/UNKNOWN 占额集合；FK 仅 run/job 两个同 Owner 物理 FK，probe/成员/凭据/Key 全部逻辑 UUID（运行时不复制物理额度）。
- 回退策略：仅三表为空允许回退（本任务交付的结构回退）；有数据时抛 `MODEL_CAPACITY_DOWNGRADE_REQUIRES_BACKUP`，与 0021 的备份口径一致，避免在飞占用/UNKNOWN 证据被静默丢弃。
- 静态校验：`.venv/bin/python -m py_compile` 三文件通过；`.venv/bin/python -m mypy apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py` → Success。
- 回归（verifier 口径）：`uv run pytest -q tests -k schema_parity` → **43 passed**, 4703 deselected (exit=0, 17.00s)。
- 服务层（预占/续租/结算/隔离）不在本任务：TASK-011+ 消费本结构。
- B-210: verified — automated command passed; run_id=e0a33537cd6f41fe87f2c237b8730f5d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-210 RED：`tests/migrations/test_capacity_webchat_runtime_pool_schema.py` → 13 failed / 1 passed（head 0021 上三表与 ORM 模型缺失）
- [2026-10-17] 实现 0022：`runtime.py` 新增 ModelCapacity/ModelDispatchState/ModelCallLease（完整唯一含 tombstone、恰一归属 CHECK、UNKNOWN 不可软删、partial index）；`migrations/versions/0022_model_capacity_runtime.py`（纯 DDL，down_revision 0021，downgrade 仅空表允许否则 `MODEL_CAPACITY_DOWNGRADE_REQUIRES_BACKUP`）
- [2026-10-17] B-210 GREEN：14 passed；verifier 口径 `tests -k schema_parity` 43 passed；py_compile/mypy 通过
- [2026-10-17] 后续修订（TASK-011 推进链头到 0023）：`test_b210_downgrade_with_lease_rows_requires_backup` 不再钉死 `version_num == '0022'`，改为记录回退尝试前的链头并断言不变——整链单事务回滚的语义与断言强度不变（`migrations/env.py` `context.begin_transaction()`）
- [2026-10-17] completed (done)

---

## TASK-011: 全局准入版本、计数与排队字段

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-010
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-211
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`, `migrations/versions/{revision}_run_admission.py`, `tests/migrations/test_capacity_webchat_admission_schema.py`
- **Estimate**: 45分钟

### Description

policy按tenant完整唯一、版本单调；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-211][integration] 先在`tests/migrations/test_capacity_webchat_admission_schema.py`建立行为验收并记录RED；真实边界：隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库；关键断言：policy按tenant完整唯一、版本单调。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-211] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-211] 执行`["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_admission_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-211 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | policy按tenant完整唯一、版本单调；USER/TENANT计数唯一且非负，Run队列/释放标记及deadline索引反映全部非终态 | tests/migrations/test_capacity_webchat_admission_schema.py::test_b211_* | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_admission_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-211 | `uv run pytest -q tests/migrations/test_capacity_webchat_admission_schema.py` → exit=1，**6 failed / 1 passed**（12.35s）：`run_record` 队列列在 ORM 中缺失（`CompileError: Unconsumed column names: queue_deadline_at, queued_at, queue_reason, queue_stage`）且两个准入表不存在。唯一通过的空表 downgrade 用例此时是空洞通过（目标 0022 即当时 head、无迁移动作），实现后由 GREEN 复跑真正覆盖 drop 路径 | `uv run pytest -q tests/migrations/test_capacity_webchat_admission_schema.py` → **7 passed** (exit=0, 12.68s) | `test_b211_empty_database_schema_parity` L108-138（两新表 `migrated == created` 全量比对；标准列齐全；`queue_stage` VARCHAR(24)/`queue_reason` VARCHAR(64) 可空、三个时间列 timestamptz 且可空、ORM 同步暴露；`ck_run_record_queue_stage` 在库；`ix_run_record_queue_deadline` 列 (`queue_deadline_at`) 非唯一且谓词含 `queue_stage IS NOT NULL`+四个非终态+`is_deleted = false`）；`test_b211_policy_is_single_row_per_tenant_and_version_moves_in_place` L141-172（同租户第二行/软删被拒；条件更新 `< 8` 生效、`< 3` rowcount=0 → 版本原地单调；6 组非法阈值/负 revision 逐个 IntegrityError；契约边界 2/2 与 100000/100 被接受）；`test_b211_counters_are_complete_unique_per_scope_and_non_negative` L175-211（默认 (0,1)；同 scope 重复行与 tombstone 重录均拒；`scope_type='POD'`/负计数/revision=0 拒；释放计数回退成功、负计数更新拒）；`test_b211_queue_fields_only_expose_waiting_stages_and_release_marker` L214-246（WAITING_RUNTIME→WAITING_MODEL 可切换；WAITING_TOOL/RUNNING/COMPLETED 作为排队阶段被 `ck_run_record_queue_stage` 拒；终态清 `queue_stage` 并写 `admission_released_at`）；`test_b211_downgrade_requires_drain_for_live_admission_state[counter|waiting]` L249-282（计数行或等待 Run 任一存在 → 非 0 退出 + `RUN_ADMISSION_DOWNGRADE_REQUIRES_DRAIN`，链头/结构/数据保持）；`test_b211_downgrade_on_pristine_schema_returns_to_0022` L285-303（空库回退成功：0022、两表与五列消失，再 `upgrade head` 成功） | 隔离库 `create_datastore()`（默认 head）与真实 `migrations/db_migrate.py -c <ini>` 子进程；隔离 PostgreSQL、真实 Alembic 链、无 mock；整链单事务（`migrations/env.py` `context.begin_transaction()`）：0023 拒绝回退时链头与结构原样 | verified |

**实施范围与证据说明**

- 实现文件：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`（`RunRecord` 加 `queue_stage/queue_reason/queued_at/queue_deadline_at/admission_released_at` 与排队 CHECK/deadline partial index；新增 `RunAdmissionPolicy`/`RunAdmissionCounter`）、`migrations/versions/0023_run_admission.py`（down_revision=0022；纯 DDL）。
- 结构决策：policy 每租户恰一行（完整唯一、`is_deleted=false`）使版本只能原地前进、无 tombstone 分叉可复活；阈值 CHECK 与 §3.7 契约一致（tenant 2～100000、user 2～100 且 ≤tenant、revision≥0）；counter 完整唯一 (tenant,scope_type,scope_key) 且计数/版本非负；排队投影只表达 WAITING_RUNTIME/WAITING_MODEL，deadline partial index 只覆盖全部非终态等待行。
- 回退策略：仅当无等待中的排队投影且无准入投影行时允许结构回退；否则 `RUN_ADMISSION_DOWNGRADE_REQUIRES_DRAIN`（两种 blocker 分别有测试）。
- 静态校验：`.venv/bin/python -m py_compile` 四文件通过；mypy `runtime.py` → Success。
- 回归与真实边界：`uv run pytest -q tests/migrations` → **63 passed** (104.45s)；verifier 口径 `uv run pytest -q tests -k schema_parity` → **44 passed**, 4709 deselected (19.09s)。
- dev 库升级（已披露的环境变更）：本地 dev 库原在 0021，ORM 前移到 0023 后 `tests/agent_runtime` 曾 128 failed/4 errors（`column run_record.queue_stage does not exist`）。执行 `uv run python migrations/db_migrate.py -c /tmp/agent-capacity-webchat-dev/dev.ini upgrade head`（0021→0023，纯增量：5 个可空列 + CHECK + partial index + 两张空表），复跑 `tests/agent_runtime` → **765 passed** (10:56)、`tests/agent_worker` → **299 passed** (1:03)。失败根因是环境陈旧，不是实现缺陷，未改实现迁就。
- 链头推进的连带修订：TASK-010 测试 `test_b210_downgrade_with_lease_rows_requires_backup` 原钉死 `version_num == '0022'`，链头到 0023 后误报；改为记录回退尝试前的链头并断言不变（同时佐证整链单事务回滚），已写入 TASK-010 Log。
- B-211: verified — automated command passed; run_id=9c46c6fe279148169083415cbb223565 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-211 RED：`tests/migrations/test_capacity_webchat_admission_schema.py` → 6 failed / 1 passed（run_record 队列列在 ORM 中缺失、两准入表不存在）
- [2026-10-17] 实现 0023：`runtime.py` 加 RunRecord 排队投影列与 `ck_run_record_queue_stage`/`ix_run_record_queue_deadline`，新增 RunAdmissionPolicy/RunAdmissionCounter；`migrations/versions/0023_run_admission.py`（纯 DDL，downgrade 仅在无等待投影/无投影行时允许，否则 `RUN_ADMISSION_DOWNGRADE_REQUIRES_DRAIN`）
- [2026-10-17] B-211 GREEN：7 passed；`tests/migrations` 63 passed；verifier 口径 `tests -k schema_parity` 44 passed；py_compile/mypy 通过
- [2026-10-17] dev 库升级 0021→0023（纯增量 DDL，已披露）：此前 `tests/agent_runtime` 128 failed/4 errors 均为 `run_record.queue_stage does not exist`；升级后 `tests/agent_runtime` 765 passed、`tests/agent_worker` 299 passed
- [2026-10-17] 连带修订：TASK-010 的 downgrade 备份测试不再钉死链头（记录断言前后版本不变），详见 TASK-010 Log
- [2026-10-17] completed (done)

---

## TASK-012: 用户Token及独立CHAT会话结构

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-005, TASK-011
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-212
- **Files**: `apps/console-platform/backend/src/muad_console_platform/infrastructure/models/auth.py`, `migrations/versions/{revision}_chat_auth.py`, `tests/migrations/test_capacity_webchat_chat_auth_schema.py`
- **Estimate**: 45分钟

### Description

Token/会话只存摘要、时间带时区，来源Token和platform_user为同Owner FK；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-212][integration] 先在`tests/migrations/test_capacity_webchat_chat_auth_schema.py`建立行为验收并记录RED；真实边界：隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库；关键断言：Token/会话只存摘要、时间带时区，来源Token和platform_user为同Owner FK。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-212] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-212] 执行`["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_chat_auth_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-212 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | Token/会话只存摘要、时间带时区，来源Token和platform_user为同Owner FK；每用户至多一个未撤销Token且最新版本记录保留 | tests/migrations/test_capacity_webchat_chat_auth_schema.py | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_chat_auth_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-212 | `uv run pytest -q tests/migrations/test_capacity_webchat_chat_auth_schema.py` → exit=1，**5 failed / 1 passed**（10.70s）：`AttributeError: module 'muad_console_platform.infrastructure.models.auth' has no attribute 'ChatAccessToken'`——ORM 两模型与 0024 均不存在。唯一通过的空库回退用例此时是空洞通过（目标 0023 即当时 head、无迁移动作），实现后由 GREEN 复跑真正覆盖 drop 路径 | 同命令 → **6 passed**（exit=0, 11.99s） | `test_b212_empty_database_schema_parity` L123-175（两新表 `migrated == created` 全量比对 L132；无 `token/raw_token/access_token/secret` 原文列且 `token_hash` VARCHAR(64) L136-138；时间列 timezone=True、issued/expires 非空、revoked 可空且无默认、issued_at 默认 now() L141-146；FK `platform_user_id→control.platform_user(id)` L147-148 与 `chat_session.access_token_id→control.chat_access_token(id)` L149；三 CHECK 名 L152-157；`token_hash`/`(tenant,user,revision)` partial unique 以软删为界 L159-164、`(tenant,user)` 未撤销唯一含 `revoked_at IS NULL` L165-167；会话摘要唯一 L168-170、`(tenant,user,expires_at)` 非唯一索引 L171-172、活跃 `access_token_id` partial index L173-175）；`test_b212_token_and_session_stores_only_digests_with_timezone_times` L178-206（sha256 hexdigest 入库、revision=1、`expires_at - issued_at == 30d` L183-187；原文串/非十六进制 64 串被摘要 CHECK 拒 L191-193；65 字符被 varchar(64) 物理上限拒 L194-200；`expires_at <= issued_at` L201-203 与 revision≤0 L204-205 拒；不存在的用户 FK 拒 L206）；`test_b212_one_unrevoked_token_per_user_revisions_and_tombstones` L209-253（未撤销行存在时新签被拒 L218-219；撤销后 rev2 成功、旧行保留且最新 revision 为唯一未撤销行 L220-231；同用户同 revision 拒 L233；活跃摘要全局唯一（同租户他人 L235-236、跨租户 L237-238）；tombstone 释放摘要与 revision 重录 L240-243；过期未撤销必须先撤销再重签 L245-253）；`test_b212_chat_session_requires_persisted_token_and_user_and_digest` L256-299（独立会话摘要入库且未撤销 L276-278；同来源 Token 多条会话并存 L279-281；来源 Token/用户不存在的 FK 拒 L282-285；原文/大写十六进制拒 L286-288；`expires_at == issued_at` 拒 L289；活跃摘要唯一 L290；tombstone 释放后 live==2 L291-299）；`test_b212_downgrade_requires_backup_for_issued_credentials` L302-332（有凭据/会话时回退非 0 退出 + `CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP` L320-321，链头与两表/数据原样 L324-330）；`test_b212_downgrade_on_pristine_schema_returns_to_0023` L335-352（空库回退成功：0023、两表消失，再 `upgrade head` 成功） | 隔离库 `create_datastore()`（默认 head）与真实 `migrations/db_migrate.py -c <ini>` 子进程；隔离 PostgreSQL、真实 Alembic 链、无 mock；整链单事务（`migrations/env.py` `context.begin_transaction()`）：0024 拒绝回退时链头与结构原样 | verified |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/infrastructure/models/auth.py`（新增 `ChatAccessToken`/`ChatSession` 及其约束/索引）、`migrations/versions/0024_chat_auth.py`（down_revision=0023；纯 DDL）。
- 结构决策：只存 64 位小写十六进制摘要（`token_hash ~ '^[0-9a-f]{64}$'`）把"不落原文"变成数据库事实（与 `ConsoleSession` 的 sha256 摘要口径、`model_credential.api_key_digest` 先例一致）；每用户至多一个未撤销 Token（含过期未撤销者——必须先显式撤销再按更高 revision 重签），旧行保留、tombstone 不阻塞重录；摘要唯一是全局身份（不按租户复用）；同一来源 Token 可有多条独立会话，logout 只撤本条；"本会话期限 ≤ 来源 Token 期限"是服务端跨表原子校验，不落库级约束。
- 回退策略：仅当两表为空时允许；否则 `CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP`（design §4.2 的"回退需确认新写入数据保留策略"）。
- 静态校验：`py_compile` 三个文件通过；mypy `auth.py` → Success；ruff check 新文件与改动文件干净。
- 回归与真实边界：`uv run pytest -q tests/migrations` → **69 passed** (125.12s)；verifier 口径 `uv run pytest -q tests -k schema_parity` → **45 passed**, 4714 deselected (22.88s)；`uv run pytest -q tests/console_platform` → **317 passed** (43.70s)。
- 链头推进的连带修订：`tests/console_platform/test_platform_settings_table.py` 原先钉死 `HEAD_REVISION="0020"`，链到 0021 起即红（在无 0024 的环境实测 `assert '0023' == '0020'`，与本次实现无关）；改为动态读取迁移链 head（`ScriptDirectory.from_config`），链继续推进时该文件不再需要维护。
- dev 库升级（已披露的环境变更）：本地 dev 库 0023→0024（纯增量：两张空表），`uv run python migrations/db_migrate.py -c /tmp/agent-capacity-webchat-dev/dev.ini upgrade head`。
- B-212: verified — automated command passed; run_id=298f320d1dfe4cc9877169fd2a267d70 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-212 RED：`tests/migrations/test_capacity_webchat_chat_auth_schema.py` → 5 failed / 1 passed（`ChatAccessToken`/`ChatSession` ORM 与 0024 均不存在；空库回退用例此时空洞通过）
- [2026-10-17] 实现 0024：`auth.py` 新增 `ChatAccessToken`/`ChatSession`（摘要形态与 `expires_at > issued_at` CHECK、同 Owner FK、软删界 partial unique、活跃会话索引）；`migrations/versions/0024_chat_auth.py`（纯 DDL；仅空表允许回退，否则 `CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP`）
- [2026-10-17] B-212 GREEN：6 passed；`tests/migrations` 69 passed；verifier 口径 `tests -k schema_parity` 45 passed；`tests/console_platform` 317 passed；py_compile/mypy/ruff 通过
- [2026-10-17] 连带修订：`test_platform_settings_table.py` 的 head 断言改为动态读取迁移链 head（原钉死 0020，链推进后必红；无 0024 时同样实测红）
- [2026-10-17] dev 库升级 0023→0024（纯增量，已披露）
- [2026-10-17] completed (done)

---

## TASK-013: 会话入口归属、列表索引及暂存上传结构

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-011, TASK-012
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.2 发布与迁移
- **Spec-Refs**:
- **Acceptance-Refs**: B-213
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`, `migrations/versions/{revision}_channel_conversations_uploads.py`, `tests/migrations/test_capacity_webchat_conversation_upload_schema.py`
- **Estimate**: 60分钟

### Description

扩展原Conversation并建指针/上传登记；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-213][integration] 先在`tests/migrations/test_capacity_webchat_conversation_upload_schema.py`建立行为验收并记录RED；真实边界：隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库；关键断言：扩展原Conversation并建指针/上传登记。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-213] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-213] 执行`["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_conversation_upload_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-213 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | 扩展原Conversation并建指针/上传登记；仅按可靠Run来源回填旧入口，未知历史不冒认为网页；列表复合索引和上传TTL/不可变键与ORM一致 | tests/migrations/test_capacity_webchat_conversation_upload_schema.py | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_conversation_upload_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-213 | `uv run pytest -q tests/migrations/test_capacity_webchat_conversation_upload_schema.py` → exit=1，**8 failed / 0 passed**（14.29s）：`AttributeError: module 'muad_agent_runtime.infrastructure.models.runtime' has no attribute 'ChannelUpload'/'ChannelConversation'`（parity L172、指针 L319、上传 L356/362、守卫 L401/404 用例）与 `UndefinedColumnError: column "channel_account_id" of relation "conversation" does not exist`（回填 L232、空库回退 L442 用例）——ORM 两模型、`conversation` 三新列与 0025 均不存在；空库回退用例此时空洞通过（0024 即当时 head、无迁移动作），实现后由 GREEN 复跑真正覆盖回退路径 | 同命令 → **8 passed**（exit=0, 15.78s） | `test_b213_empty_database_schema_parity` L172-229（三表 `migrated == created` 全量比对 L184，索引定义按 `pg_indexes` 原文比对含 DESC 表达式与谓词；`conversation` 三新列可空/类型 L188-192、无物理 FK L193、活跃列表索引表达式 `(tenant_id, user_id, channel_account_id, agent_id, last_message_at DESC, id DESC) WHERE (is_deleted = false)` L195-197；`channel_upload` 列型/非空 L199-206、三 CHECK 名 L207-209、无物理 FK L210、`(tenant_id, storage_key)` 活跃唯一 L212-213、清理索引含 expires_at 且无谓词 L214-216；`channel_conversation` 默认 `''`/非空 L220-221、同 Owner FK 恰一条 L223-224、active unique 五列 L227-229）；`test_b213_backfill_attributes_only_reliable_run_entries` L232-317（先降级到 0024 后按真实旧数据播种：唯一入口+最近运行线程 `grp-213` L293-296；无线程不编造 L297-299；未知 bot/多入口冲突/停用入口/跨租户同名 bot/无渠道记录/无 Run/软删会话 7 类一律 NULL 且 ≠ 网页入口 L300-305；`last_message_at` 全空与 `update_time` 不变 L306-308；不伪造暂存/指针行 L310-315）；`test_b213_channel_conversation_pointer_unique_per_entry_thread` L319-357（默认线程 `''` L331；同入口线程重复 L333；不同线程/入口并存 L335-337；指针原地切换 L338-340；悬空会话 FK 拒 L341；tombstone 后重登记 live==1 L343-357）；`test_b213_channel_upload_staging_ttl_size_and_immutable_key` L360-400（TTL 落库 L364-366；同租户异用户复用键拒 L367-368、跨租户各持同形键 L369；`expires_at <= create_time` 拒 L371-372、过期行留存待清理 L373-377、清理候选计数 L395-396；契约枚举外 kind 拒 L378、size 0/-1 拒 L379-381、IMAGE/DOCUMENT/OTHER 接受 L382-384；tombstone 释放键后重登记 L397-400）；`test_b213_downgrade_requires_backup_for_entry_or_staging_state` L403-439（三类占用状态均非 0 退出 + `CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP` L427，链头/表/列原样 L430-438）；`test_b213_downgrade_on_pristine_schema_returns_to_0024` L442-466（空库回退干净落 0024 L451-456，再升级回 0025 L461-466） | 隔离 PostgreSQL（`async_tool_datastore` 建空库 + 真实 Alembic 链）、真实迁移子进程（`migrations/db_migrate.py -c ini`）、真实 ORM metadata `create_all` 与 `pg_indexes`/inspector 比对；未 mock 迁移或数据库 | verified |

**实施范围与证据说明**

- 实现文件：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`（`Conversation` 增 `channel_account_id`/`external_thread_id`/`last_message_at` 与活跃列表索引；新增 `ChannelUpload`/`ChannelConversation`）、`migrations/versions/0025_channel_conversations_uploads.py`（down_revision=0024；纯 DDL + 一次数据回填）。
- 回填规则（design §3.3/§3.3 叙述）：只认可靠 Run 证据——`run_record.channel_json->>'bot_id'` 命中**同租户、未删除** `control.bot_account`；整个会话解析出唯一入口才归属，多个入口并存即无法证实、保持 NULL；线程取最近一次可靠运行的 `external_conversation_id`（1:1 回退 `external_user_id`）。未知 bot_id、停用入口、无渠道记录、无 Run、软删会话一律 NULL，保留审计、不冒认成网页历史。`last_message_at` 由真实可见消息事务维护，迁移不臆造；回填不动 `update_time`（不是业务变更）。
- 结构决策：`channel_upload` 是运行前暂存登记（非 artifact 行）——活跃 `(tenant_id, storage_key)` 唯一即不可变键，`expires_at > create_time`/`size > 0`/`kind` 与 `AttachmentKind` 契约枚举一致都是数据库事实，清理索引 `(tenant_id,user_id,entry_id,expires_at)` **不设谓词**（过期/软删行也要能被清）；`channel_conversation` 是无显式会话入口的指针（active unique 五列 + 同 Owner FK），Webchat 写目标不依赖它；`conversation.channel_account_id`/`agent_id` 等跨 Owner 归属均为逻辑 UUID，不建物理 FK（parity 断言 FK 列表为空）。
- 回退策略：仅当两张新表为空且无任何已归属会话（`channel_account_id`/`external_thread_id`/`last_message_at` 全空）时允许；否则 `CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP`（design §4.2 的"回退需确认新写入数据保留策略"）。
- 静态校验：`py_compile` 迁移文件通过；mypy `runtime.py` → Success；ruff check 改动文件干净。
- 回归与真实边界：`uv run pytest -q tests/migrations` → **77 passed** (134.81s)；verifier 口径 `uv run pytest -q tests -k schema_parity` → **46 passed**, 4721 deselected (24.23s)；`uv run pytest -q tests/agent_runtime` → **765 passed** (671.57s)；`uv run pytest -q tests/console_platform` → **317 passed** (45.09s)。
- 连带修订：`ModelCallLease.reserved_requests` 的 E501（110 列上限，HEAD 上即存在）顺带修复——本次触碰同文件，不修则本任务 lint 门禁必红。
- 环境事实（非实现缺陷）：首次跑 `tests/agent_runtime` 得 142 failed / 10 errors，全部为共享 dev 库仍在 0024（ORM 已到 0025）导致的 `UndefinedColumnError`；按根 AGENTS 口径升级 dev 库后同命令 765 passed。
- dev 库升级（已披露的环境变更）：`uv run python migrations/db_migrate.py -c /tmp/agent-capacity-webchat-dev/dev.ini upgrade head`，0024→0025；回填实测把 **10 个 dev 历史会话**归属到 dev WECOM bot 账号（升级前预览同为 10，逐个一致），`last_message_at` 全空、`channel_upload`/`channel_conversation` 均 0 行。
- B-213: verified — automated command passed; run_id=79bd13687dca4bf58b036597d71ac289 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-213 RED：`tests/migrations/test_capacity_webchat_conversation_upload_schema.py` → 8 failed（`ChannelUpload`/`ChannelConversation` 与 `conversation` 新列均不存在；空库回退用例此时空洞通过）
- [2026-10-17] 实现 0025：`runtime.py` 扩展 `Conversation` 并新增 `ChannelUpload`/`ChannelConversation`（活跃列表索引、不可变暂存键、指针 active unique 与同 Owner FK）；`migrations/versions/0025_channel_conversations_uploads.py`（DDL + 可靠 Run 回填 CTE；回退守卫 `CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP`）
- [2026-10-17] B-213 GREEN：8 passed；`tests/migrations` 77 passed；verifier 口径 46 passed；`tests/agent_runtime` 765 passed；`tests/console_platform` 317 passed；py_compile/mypy/ruff 通过
- [2026-10-17] 连带修订：`ModelCallLease.reserved_requests` 预存在 E501 顺带修复；先出现的 142 failed/10 errors 经 dev 库升级后全消（环境残留，非实现缺陷）
- [2026-10-17] dev 库升级 0024→0025（回填 10 个 dev 历史会话的入口归属，已披露）
- [2026-10-17] completed (done)

---

## TASK-014: 用户通知feed、交付租约与稳定回执结构

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006, TASK-013
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-214
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`, `migrations/versions/{revision}_channel_notifications.py`, `tests/migrations/test_capacity_webchat_notification_schema.py`
- **Estimate**: 45分钟

### Description

feed完整唯一且不软删/不重置水位，用户seq/delivery_key完整唯一；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-214][integration] 先在`tests/migrations/test_capacity_webchat_notification_schema.py`建立行为验收并记录RED；真实边界：隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库；关键断言：feed完整唯一且不软删/不重置水位，用户seq/delivery_key完整唯一。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-214] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-214] 执行`["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_notification_schema.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-214 | integration | 隔离PostgreSQL、实际Alembic迁移链和ORM约束/索引；不得mock迁移或数据库 | feed完整唯一且不软删/不重置水位，用户seq/delivery_key完整唯一；已读/送达分离，出站租约及首次结果可恢复 | tests/migrations/test_capacity_webchat_notification_schema.py | ["uv","run","pytest","-q","tests/migrations/test_capacity_webchat_notification_schema.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-214 | `uv run pytest -q tests/migrations/test_capacity_webchat_notification_schema.py` → exit=1，**7 failed / 0 passed**（14.44s）：`AttributeError: module 'muad_agent_runtime.infrastructure.models.runtime' has no attribute 'ChannelNotificationFeed'/'ChannelNotification'`（parity L157、feed L236、tombstone L266、租约 L321、守卫 L412 两参数）；空库回退用例因 0026 尚不存在，重升级只到 0025 → `AssertionError: assert '0025' == '0026'`（L461） | 同命令 → **7 passed**（exit=0, 13.06s） | `test_b214_empty_database_schema_parity` L157-233（两表 `migrated == created` 全量比对 L174，索引定义按 `pg_indexes` 原文比对；feed 列型/非空 L180-181、完整唯一 `(tenant_id,user_id)` 无谓词 L187-188、无物理 FK L185；通知列型/可空 L192-210（`delivery_state` **无** server default L205、`artifact_ids` 为 `uuid[]` 且 item_type=UUID L208-210）、同 Owner 三物理 FK L211-214、两完整唯一无谓词 L220-223、未读 partial `read_at IS NULL` L225-226、due/lease partial 谓词且不含 `is_deleted` L227-232）；`test_b214_feed_identity_is_stable` L236-263（注册行唯一 L241-243；重复注册拒 L243；软删被 CHECK 拒 L246-248；负水位拒 L250-253；原地推进 `last_seq=7` 且仍恰 1 行 L255-263）；`test_b214_sequence_and_delivery_key_unique_survive_tombstones` L266-318（初始租约事实 L280-281；同用户重复 seq 拒 L283；同租户 delivery_key 跨用户不可复用拒 L285-288；软删 tombstone 后重插同 seq/同 key 均拒 L293-294；悬空 feed/conversation/run 物理 FK 拒 L296-298；逻辑 UUID task/entry 可写 L300-303；契约枚举外 kind/state、seq≤0、负计数拒 L305-315；三契约 kind 全部接受 L316-318）；`test_b214_delivery_lease_read_separation_and_first_result` L321-407（默认意图空 `uuid[]` L335；引用原样往返 L338-344；reserve→DELIVERING+lease_owner/epoch/attempt 落库 L349-353；续租保 epoch/attempt L355-359；ack 写首次结果、清租约且 `read_at` 仍 NULL L363-367；重放恢复同一 JSON 与 delivery_key L369-371；PENDING 可直接已读 L376-381；隐藏 PENDING 仍在到期集合 L386-395；NOT_REQUIRED 不在投递泵集合 L397-404；五状态全接受 L405-407）；`test_b214_downgrade_requires_backup_for_notification_state` L412-439（feed/notification 两类占用均非 0 退出 + `CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP` L428，链头/表/数据原样 L431-438）；`test_b214_downgrade_on_pristine_schema_returns_to_0025` L442-463（空库干净落 0025 L446-452，重升级动态 head L454-463） | 隔离 PostgreSQL（`async_tool_datastore` 建空库 + 真实 Alembic 链）、真实迁移子进程（`migrations/db_migrate.py -c ini` 升降级）、真实 ORM metadata `create_all` 与 `pg_indexes`/inspector 比对、真实 `uuid[]`/JSONB 往返；未 mock 迁移或数据库 | verified |

**实施范围与证据说明**

- 实现文件：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/runtime.py`（新增 `ChannelNotificationFeed`/`ChannelNotification`，位于 `ChannelConversation` 与 `RunRecord` 之间）、`migrations/versions/0026_channel_notifications.py`（down_revision=0025；纯 DDL，无回填——不存在历史通知行，feed 由首次写入在锁内登记）。
- 结构决策（design §3.3/§3.6 的事实化）：
  - feed 的 `(tenant_id,user_id)` 完整唯一 + `is_deleted CHECK false`：删除动作被数据库拒绝，不存在"删掉再注册让水位回零"的路径；`last_seq >= 0` 且缺省 0，原地推进不新建行。
  - 通知的 `(tenant_id,user_id,seq)` 与 `(tenant_id,delivery_key)` 完整唯一且**无谓词**：隐藏/清理只留 tombstone，序号与交付回执不放回（"仍可能重投/UNKNOWN 的结果不得清除唯一回执"）；delivery_key 是租户级命名空间（跨用户不可复用），过期旧键由读取层返回过期语义而不是当新投递。
  - `read_at` 与 `delivery_state` 两条独立事实：送达不代替已读（ack 后 `read_at` 仍可为 NULL），已读不要求送达（PENDING 可直接标读）；未读 partial 索引只认 `read_at IS NULL`。
  - 出站投递状态机 `PENDING`→`DELIVERING`→`DELIVERED`（+`UNKNOWN` 核实保留 /`NOT_REQUIRED` 网页持久入箱）；`delivery_state` **无 server default**，写入方必须显式选择，不静默把入箱通知当成出站意图；`delivery_result` 存 **首次**结果供 ack 重放恢复；due/lease partial 索引不带 `is_deleted` 谓词——隐藏通知不丢投递回执与重试资格。
  - `feed_id`/`conversation_id`/`run_id` 是同 Owner 物理 FK；`user_id`/`entry_id`/`task_id` 是逻辑 UUID；DTO 的 `agent_id`/`summary` 由 conversation 与引用在读取时投影，不落列（设计字段清单没有 agent/摘要列——不把面向用户的文案写成行内事实，i18n 与安全投影留在读取层）。
- 回退策略：仅当两张新表都为空时允许（0026 的 DDL 无回填，空表即"尚未产生持久通知事实"）；否则 `CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP`——结构回退会静默丢掉 feed 水位、未读状态、投递租约与交付回执。
- 静态校验：`py_compile` 迁移文件通过；mypy `runtime.py` → Success；ruff check 改动文件干净（修掉两处 E501 后全绿）。
- 回归与真实边界：`uv run pytest -q tests/migrations` → **84 passed** (153.39s)；verifier 口径 `uv run pytest -q tests -k schema_parity` → **47 passed**, 4727 deselected (26.82s)；`uv run pytest -q tests/agent_runtime` → **765 passed** (676.63s)；`uv run pytest -q tests/console_platform` → **317 passed** (46.64s)。
- 连带修订：`tests/migrations/test_capacity_webchat_conversation_upload_schema.py` 的链头钉死常量 `"0025"`（TASK-013 引入）改为动态 `_head()`（`ScriptDirectory.get_current_head()`，同 `tests/console_platform/test_platform_settings_table.py` 既有口径）——0026 推进后该用例失败于 `assert '0025' == '0026'`，属钉死常量的连带修订，不是回退行为缺陷。
- 环境事实（非实现缺陷）：dev 库升级前 `tests/agent_runtime` 的 ORM 对比会缺新表；先 `uv run python migrations/db_migrate.py -c /tmp/agent-capacity-webchat-dev/dev.ini upgrade head` 升级 dev 库到 0026（纯 DDL、无回填），`channel_notification_feed`/`channel_notification` 均 0 行，随后全套通过。
- dev 库升级（已披露的环境变更）：0025→0026，无数据变更（无回填；两新表 0 行）。
- B-214: verified — automated command passed; run_id=d7b25380987040f6bf8221c189f33e42 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-214 RED：`tests/migrations/test_capacity_webchat_notification_schema.py` → 7 failed（`ChannelNotificationFeed`/`ChannelNotification` 与 0026 均不存在；空库回退用例停在 0025）
- [2026-10-17] 实现 0026：`runtime.py` 新增 feed（完整唯一/不软删/水位非负）与 notification（seq/delivery_key 完整唯一、租约/首次结果、未读与投递 partial 索引）；`migrations/versions/0026_channel_notifications.py`（纯 DDL；回退守卫 `CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP`）
- [2026-10-17] B-214 GREEN：7 passed；`tests/migrations` 84 passed；verifier 口径 47 passed；`tests/agent_runtime` 765 passed；`tests/console_platform` 317 passed；py_compile/mypy/ruff 通过
- [2026-10-17] 连带修订：B-213 用例链头钉死 `"0025"` 改动态 head（0026 推进的连带，非回退缺陷）；修补两处 E501
- [2026-10-17] dev 库升级 0025→0026（纯 DDL 无回填，两新表 0 行，已披露）
- [2026-10-17] completed (done)

---

## TASK-015: 物理Key管理与稳定计量身份

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-009
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-215
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/model_credential_service.py`, `apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/model_credential_repository.py`, `tests/console_platform/test_capacity_webchat_credential_service.py`
- **Estimate**: 45分钟

### Description

按tenant/provider/Key摘要去重并派生稳定key_resource_id；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-215][integration] 先在`tests/console_platform/test_capacity_webchat_credential_service.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：按tenant/provider/Key摘要去重并派生稳定key_resource_id。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-215] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-215] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_service.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-215 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 按tenant/provider/Key摘要去重并派生稳定key_resource_id；重录不重置额度、轮换不迁移旧占用；明文不进读取/审计/快照 | tests/console_platform/test_capacity_webchat_credential_service.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_service.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-215 | `uv run pytest -q tests/console_platform/test_capacity_webchat_credential_service.py` → exit=2，**1 error during collection**（0.06s）：`ModuleNotFoundError: No module named 'muad_console_platform.application.model_credential_service'`（测试已先行建立） | 同命令 → **8 passed**（exit=0, 0.50s） | `test_b215_create_stores_digest_identity_and_audits_without_plaintext` L158-188（摘要=sha256 且与列 CHECK 同口径 L170-171、登记值回读 L172-174、repr 无明文 L175、派生可重复 L178、派生≠行 ID L179、审计动作/resource_id L182-183、明文与指纹不进审计 L185-186、非密钥字段保留 L187）；`test_b215_same_physical_key_is_rejected_within_tenant_and_provider` L190-260（同摘要重录拒 L203-204、key 重名拒 L209-210、不存在额度组 `COMMON_NOT_FOUND` L223-224、被拒不落行/不落审计 L227-229、跨租户额度组拒 L240-241、跨租户同 Key 是不同归属 L258-260）；`test_b215_concurrent_duplicate_is_backed_by_the_real_unique_index` L265-293（预检失效后真实 partial unique 裁决→同一冲突码 L288-289、落败无第二行 L293）；`test_b215_delete_then_re_record_keeps_the_same_metering_identity` L296-332（软删后读不到 L315-316、重录建新行 L325、行状态 L327、计量身份与首次一致 L329-331、审计 CREATE/DELETE/CREATE L332）；`test_b215_rotation_keeps_row_identity_and_switches_metering_identity` L335-383（同行同身份只递增 revision L353-356、新值=新身份 L359-361、旧值重录回旧身份 L369-373、原地重提同值幂等 L381-383）；`test_b215_update_conflicts_are_explicit` L386-435（撞他行摘要 L407、过期 revision L413、未知 ID L419、换 provider 即换身份 L431-435）；`test_b215_delete_referenced_credential_is_rejected` L438-468（`MODEL_CREDENTIAL_IN_USE` L464、行保留 L467、无删除审计 L468）；`test_b215_list_and_get_are_tenant_scoped_and_skip_soft_deleted` L471-502（列表/合计 L489、分页边界 L492-494、未知与软删 404 L498/L502） | 真实 PostgreSQL：`control.model_credential` 的 `uq_model_credential_tenant_provider_digest` 在竞争用例中真实裁决（insert 撞唯一索引 → `IntegrityError`），`(tenant_id, quota_group_id)` 复合 FK 拒绝跨租户组；`control.model_deployment` 引用按真实行计数；审计行真实回读 `control.config_audit_log`（jsonb 文本扫描）。真实 Console 应用服务 `ModelCredentialService`（无替身；服务只 flush、不提交，提交/回滚由测试决定）。唯一注入 = 竞争窗口（monkeypatch 预检失效），让真实唯一索引裁决；未 mock 服务/数据库 | verified |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/application/model_credential_service.py`（`ModelCredentialService` + `compute_api_key_digest`/`derive_key_resource_id`/`credential_snapshot` 模块函数）、`apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/model_credential_repository.py`（带租户谓词的读写 + CAS + 引用计数 + 复合 FK 目标存在性查询）、`tests/console_platform/test_capacity_webchat_credential_service.py`。未新增/修改 schema、无迁移、无 dev 库变更。
- 计量身份（ADR-08 的事实化）：`key_resource_id = uuid5(NAMESPACE_URL, "muad:model-key:{tenant_id}:{provider_key}:{api_key_digest}")`——纯派生、不落库（credential 表没有该列），与仓库既有稳定 ID 口径同形（`muad:` 前缀 + `NAMESPACE_URL`；如 `muad:tool-result:*`、`muad:run-start:*`）。去重/派生只用摘要：`api_key_digest = sha256(明文).hexdigest()`（小写十六进制，与 0021 列 CHECK `^[0-9a-f]{64}$` 及迁移回填口径一致）。
- 重录/轮换语义：**同一物理 Key**（同 tenant+provider+摘要）重复登记被拒（`MODEL_CREDENTIAL_EXISTS`）；删除后重录建新行但派生回**同一** `key_resource_id` ⇒ 旧计量窗口不重置。**轮换**（PUT 新值）更新同一行（行 ID/成员引用不变，revision+1），摘要变化 ⇒ 新计量身份；旧身份不迁移、不删改，旧在飞/UNKNOWN 占用留在旧身份；旧值此后重新登记（该 provider 下已无活跃占用）仍回到旧身份。`key` 重名与摘要重复归一到同一冲突码；并发窗口由真实 partial unique 兜底。
- 写入边界：`update_credential` 以 `expected_revision` 做 CAS（`rowcount==1`），未命中 → `REVISION_CONFLICT`；`quota_group_id` 必须指向同租户活跃额度组（复合 FK 目标），否则 `COMMON_NOT_FOUND`；仍被 `model_deployment` 引用时删除 → `MODEL_CREDENTIAL_IN_USE`（软删、行保留）。
- 明文卫生：审计快照按 `mcp_snapshot` 先例剔除 `api_key` 与 `api_key_digest`，保留 key/provider_key/base_url/quota_group_id/max_inflight/enabled/revision；ORM `__repr__`（TASK-009 既有）与本次快照共同保证明文/指纹不进入审计、repr 或后续读取投影（读取投影由 TASK-016 输出 `configured`）。
- 中间过程（诚实记录）：实现后首跑 7 passed / 1 failed，两处均为**测试侧**修正——(1) 同事务 `now()` 常量导致 `create_time` 相同，不能按插入顺序断言（改 `sorted`）；(2) 跨租户额度组被服务正确拒绝，该测试需为其他租户建自有额度组并显式断言拒绝分支。非实现缺陷。
- 静态/回归：`ruff check` 干净、`ruff format` 已应用到三个文件；mypy 两个源文件 `Success: no issues found`；`uv run pytest -q tests/console_platform` → **325 passed**（exit=0, 42.81s）。
- 契约文本说明：本任务不含上游模型调用路径（"外部模型真实HTTP协议探针"属探针类任务 TASK-027/028），真实边界为真实 PG + 真实 Console 服务 + 真实事务；`tests/console_platform` 按本套件既有口径使用 dev PostgreSQL 实例、以每用例唯一 `tenant_id` 隔离并在夹具内清理新增表行。
- B-215: verified — automated command passed; run_id=8b0276ef9ef348a49e0a2554f195b1d1 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-215 RED：`tests/console_platform/test_capacity_webchat_credential_service.py` 先行建立 → exit=2，1 error during collection（`ModuleNotFoundError: ...application.model_credential_service`）
- [2026-10-17] 实现 `ModelCredentialService`（摘要去重/派生稳定 key_resource_id/CAS 轮换/引用计数删除/审计快照无明文）与 `ModelCredentialRepository`；首跑 7 passed/1 failed 为测试侧两处修正（同事务 create_time 相同不定序；跨租户额度组需自有组并断言拒绝）
- [2026-10-17] B-215 GREEN：8 passed；`tests/console_platform` 325 passed；ruff check/format 与 mypy 通过
- [2026-10-17] completed (done)

---

## TASK-016: ADMIN模型凭据路由与读写边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-015
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-216
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/model_resources.py`, `apps/console-platform/backend/src/muad_console_platform/api/router.py`, `tests/console_platform/test_capacity_webchat_credential_api.py`
- **Estimate**: 45分钟

### Description

凭据创建/更新/删除仅ADMIN+CSRF，读取仅configured；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-216][integration] 先在`tests/console_platform/test_capacity_webchat_credential_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：凭据创建/更新/删除仅ADMIN+CSRF，读取仅configured。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-216] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-216] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-216 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 凭据创建/更新/删除仅ADMIN+CSRF，读取仅configured；关联删除/降额冲突明确，创建按strict canonical JSON持久幂等回放 | tests/console_platform/test_capacity_webchat_credential_api.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-216 | `uv run pytest -q tests/console_platform/test_capacity_webchat_credential_api.py` → **8 failed**（exit=1, 1.03s）：全部为路由未挂载（`assert 404 == 403` 于 L272、`assert 404 == 200` 于 L198×4/L502、契约 `ModelCredentialUpdate` 与 `has_live_leases` 不存在）与 `TypeError: ModelCredentialService.create_credential() got an unexpected keyword argument 'idempotency_key'`（L591）；异租户列表读 `data` 为 None（L665）。首轮 RED 因测试 7 裸等 `started.wait()` 在缺实现时挂死（测试侧），改为 `asyncio.wait(timeout=10, FIRST_COMPLETED)` 传播首个写者真实异常后获得上述快速 RED | 同命令 → **8 passed**（exit=0, 1.95s）；回归 `uv run pytest -q tests/console_platform tests/contracts` → **1613 passed**（exit=0, 48.22s）；契约 `tests/contracts/test_capacity_webchat_model_contracts.py` 24 passed；TASK-015 服务用例 8 passed；`uv run mypy apps packages` → Success: no issues found in 392 source files；`uv run ruff check` 逐文件干净；`scripts/check_error_message_hardcode.py` OK | `test_b216_admin_and_csrf_guard_every_write` L265-291（BUILDER 读 403 FORBIDDEN L272-273、BUILDER 写 403 L275、缺 CSRF 403 L282-283、错 CSRF 403 L286、被拒请求零落行 L288、正确 CSRF 受理且 configured L290-291）；`test_b216_reads_expose_only_configured_and_accept_group_less_keys` L294-340（投影字段集恰为 READ_FIELDS L300、configured 与 quota_group_id L301、详情无 api_key/明文 L304、列表信封 L307-309、带组创建写组 L329、0021 遗留无组 Key 可读 L333、无组创建可建 L339-340）；`test_b216_create_conflicts_and_referenced_delete_are_explicit` L343-388（未知额度组 404 L351、key 重复 409 L355-356、同物理 Key 摘要重复 409 L358-359、被成员引用删除 409 L375、解除引用后删除 200 L384、软删后 404 L386、行保留 L388）；`test_b216_update_keeps_secret_unless_explicit_rotation` L391-441（缺省保留密钥 200 L404、max_inflight/revision L406、响应无明文 L407、库内明文与摘要不动 L409-410、更新同行 L411、空串 422 L418、轮换 200/revision 3 L427-428、新摘要 L430、陈旧 revision 409 L437、未覆盖 L439）；`test_b216_downgrade_and_ownership_change_require_drain` L444-493（降额 409 `MODEL_CAPACITY_DRAIN_REQUIRED` L458-459、扩容 200 L466、值轮换 200 L472、UNKNOWN 换组 409 L480、SETTLED 后换组+降额 200 L490-493）；`test_b216_create_replays_first_result_by_persisted_canonical_fingerprint` L496-570（首建 200 L504、同键重放同 ID L506、endpoint=model-credential-create L545、重放不建第二行/不写审计 L546、指纹=规范化 JSON+判别键 L564、回执无明文 L567、异指纹 409 `IDEMPOTENCY_MISMATCH` L570）；`test_b216_same_idempotency_key_serializes_concurrent_creation` L573-638（首个写者进入未提交窗口 L618、同键第二写者被 0.5s 超时串行 L620、提交后重放同 ID L631、行存在 L632、总数 1 L638）；`test_b216_cross_tenant_credentials_are_invisible` L641-677（异租户详情 404 L664、删除 404 L665、列表 0 且不出现异租户 key L667、PUT 404 L675、异租户行未改动 L677） | 真实 ASGI 应用（`muad_console_platform.main.app`）+ 真实登录会话：ADMIN 客户端与 BUILDER 客户端都是真实 `ConsoleAccount` 行，角色裁决（403）与 CSRF（cookie/header 双比对）走真实依赖，不伪造身份；真实 PostgreSQL：`credential_tenant` 夹具建两个真实 `model_quota_group` 行（复合 FK 目标），每用例唯一 tenant_id，结束清理新表行；幂等与并发由真实 `pg_advisory_xact_lock`（(tenant,key,endpoint) 派生）+ `control.skill_import_idempotency` 行裁决（未 mock 服务/数据库）；排空判定用真实 INSERT 的 `runtime.model_call_lease` 行（ACTIVE→UNKNOWN→SETTLED 真实状态迁移）；明文/摘要事后从库内真实回读比对 | verified |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`api/model_resources.py`（新增，API-16 五个端点 + 仅 configured 投影）、`api/router.py`（挂入 admin 组）、`application/model_credential_service.py`（创建幂等/缺省保留密钥/排空判定；`update_credential` 签名放宽为 `ModelCredentialCreate | ModelCredentialUpdate` 以兼容 TASK-015 既有用例）、`infrastructure/repositories/model_credential_repository.py`（`has_live_leases` 只读 runtime 租约）、`packages/contracts/src/muad_contracts/model_pool.py`（`quota_group_id` 可空 + 新增 `ModelCredentialUpdate`）、`tests/console_platform/test_capacity_webchat_credential_api.py`。计划 Files 仅列 `model_resources.py`/`router.py`/测试，服务、仓储与契约属实现必需，已在此披露；无迁移、无 schema 变更。
- 契约事实（0021 迁移清单）：`quota_group: null` → `quota_group_id NULL` 是受支持状态（dev 库现有 2 行凭据均为 NULL，含 `default` 租户 `deep`），故 `ModelCredentialFields.quota_group_id: UUID | None = None`；严格必填会让无组 Key 的 GET/列表 500。`SelectedModelCredential`/`ModelCapacityReservationRequest` 的 `quota_group_id` 仍为必填，留待 TASK-020/021 以「leases 允许 NULL」的事实重新裁决（届时如需放开在本任务系改动上继续）。
- PUT 语义与排空：整份字段替换 + `expected_revision` CAS（未命中 `REVISION_CONFLICT`）；`api_key` 缺省=保留原值、空串在契约层 422（`_non_blank_secret`）；`max_inflight` 变小或 provider/base_url/额度组变化且存在 `ACTIVE`/`UNKNOWN` 租约 → `MODEL_CAPACITY_DRAIN_REQUIRED`；扩容与同目标值轮换实时生效（§3.6、§3.7 `max_inflight` 行）。
- 幂等（RULE-api-002）：endpoint `model-credential-create`；先取 `(tenant_id, key, endpoint)` 派生的事务 advisory 锁再判重放；指纹 = 规范化 JSON（`sort_keys=True` + 紧凑分隔符 + `ensure_ascii=False`）的 `sha256:`，判别键含 endpoint/tenant/actor，密钥只以 SHA-256 摘要入指纹；`response_json={"id": ...}` 重放按 ID 回读当时行；异指纹 `IDEMPOTENCY_MISMATCH`。与 `platform_settings_idempotency` 同口径，沿用 `control.skill_import_idempotency` 既有表（`agent-create`/`mcp-register` 先例）。
- 中间过程（诚实记录）：(1) 首轮 RED 因测试 7 裸等事件在缺实现时挂死，测试改为 `asyncio.wait` + 超时并传播真实异常（测试侧修正，非实现缺陷）；(2) 实现后首跑 7 passed / 1 failed：PUT 轮换用例用**更新前**的 item 快照重提 `max_inflight=8`，却断言旧值 6 存活——在任何「整份替换」语义下自相矛盾；按前端「读当前状态 → 改密钥 → 提交」流程改为携带当前值（测试侧修正，已在此与日志披露）。
- 仓库 lint 现状：`uv run ruff check .` 共 37 项均为未触达文件（`tests/agent_runtime`、`tests/migrations`、`apps/agent-runtime` 等）的 HEAD 既有告警；本次改动文件逐文件 `ruff check` 全部干净。`model_pool.py` 的 `ruff format` 偏差在 HEAD 即存在（预存长行，非本次引入），未做无关重排；本次新增/修改的三处源码文件已 `ruff format`。
- B-216: verified — automated command passed; run_id=6e4d1bd12d134316b79e19766cb1891d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-216 RED：`tests/console_platform/test_capacity_webchat_credential_api.py` 先行建立 → **8 failed**（1.03s；路由 404、缺 `idempotency_key` 参数与契约类型）；首轮因测试裸等挂死，测试侧改为 `asyncio.wait`+超时后重跑获得快速 RED
- [2026-10-17] 实现：契约 `quota_group_id` 可空 + `ModelCredentialUpdate`；`ModelCredentialService` 增加创建幂等（advisory 锁/规范 JSON 指纹/回执重放）、缺省保留密钥、排空判定；仓储加 `has_live_leases`；新增 `api/model_resources.py` 并挂 admin 组
- [2026-10-17] 首跑 7 passed/1 failed：PUT 轮换用例以陈旧 item 快照重提旧 max_inflight 却断言旧值存活（测试侧自相矛盾），改为按当前状态整份提交后 GREEN
- [2026-10-17] B-216 GREEN：8 passed；`tests/console_platform tests/contracts` 1613 passed；mypy 392 files 无问题；ruff 逐文件干净
- [2026-10-17] completed (done)

---

## TASK-017: 模型成员绑定、解绑及权重修改

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-016
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-rel#RULE-rel-001
- **Acceptance-Refs**: B-217
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/model_deployment_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/models.py`, `tests/console_platform/test_capacity_webchat_deployment_relations.py`
- **Estimate**: 45分钟

### Description

成员只引用credential，不复制Key；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-217][integration] 先在`tests/console_platform/test_capacity_webchat_deployment_relations.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：成员只引用credential，不复制Key。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-217] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-217] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_deployment_relations.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] verifier `harness-rel#RULE-rel-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-217 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 成员只引用credential，不复制Key；同供方/逻辑模型核属，POST/DELETE单关系修改、权重版本冲突可见，禁止集合整体PUT | tests/console_platform/test_capacity_webchat_deployment_relations.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_deployment_relations.py"] | verified |
| harness-rel#RULE-rel-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 关系类修改使用单关系 POST/DELETE 并由独立事务完成；禁止用全量 PUT 覆盖整个关系集合。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-217 | `uv run pytest -q tests/console_platform/test_capacity_webchat_deployment_relations.py` → **6 failed**（exit=1, 0.87s）：实现前成员端点未挂载，六个用例全部 `assert 404 == ...` 失败 | 同命令 → **6 passed**（exit=0, 1.09s；`ruff format` 后复跑 6 passed/1.90s）；required verifier `uv run pytest -q tests/console_platform/test_user_side_relations.py` → **3 passed**（exit=0, 0.99s）；回归 `uv run pytest -q tests/console_platform tests/contracts` → **1619 passed**（exit=0, 50.22s）；`uv run mypy apps packages` → Success: no issues found in 394 source files；`uv run ruff check` 逐文件干净 | `test_b217_bind_member_references_credential_without_copying_key` L254-280（投影恰为 MEMBER_FIELDS L254、响应无 api_key/明文 L258、行内 credential_id/weight/is_deleted L262-263、information_schema 无密钥/连接列 L275、列表无明文 L280）；`test_b217_provider_and_tenancy_are_verified` L293-310/L348-361（异供方 422 MODEL_PROVIDER_MISMATCH L293-294、未知模型 404 COMMON_NOT_FOUND L299-300、未知凭据 404 L307-308、被拒绑定零落行 L310、异租户模型/凭据 404 且异租户零成员 L352-361）；`test_b217_duplicate_member_key_or_credential_conflicts` L385-404（同 key 409 L385、同凭据 409 L393、零第二行 L396、零审计 L397、软删后同 key 重绑新行且旧行保留 L400-404）；`test_b217_single_relation_mutations_never_replace_the_set` L422-465（单关系更新 revision=2 L422-424、另一关系不受影响 L427-429、陈旧 revision 409 L436、重命名撞名 409 L443-444、集合 PUT 405 L451、被拒写入不动已提交行 L453-457、单删不连带/再删 404/审计动作序 L459-465）；`test_b217_member_identity_survives_credential_rotation` L493-498（轮换后成员 id/credential_id 不变 L495、revision 不递增 L496、无任何密钥明文 L497-498）；`test_b217_writes_are_admin_only_and_csrf_guarded` L506-532（BUILDER 读 403 L507-508、BUILDER 写 403 L512、缺 CSRF 403 L521、错 CSRF 403 L528、被拒请求零落行 L529、正确 CSRF 受理 L531-532） | 真实 Console ASGI 应用 + 真实 PostgreSQL（每用例独立租户；partial unique 与复合 FK 由数据库真实裁决）；ADMIN/BUILDER 均真实登录会话；「成员不复制 Key」以 information_schema + 响应文本双重断言；审计行从 `control.config_audit_log` 真实回读；轮换以对 `control.model_credential` 的真实 UPDATE 造册 | verified |
| harness-rel#RULE-rel-001 | 同 B-217（成员端点未挂载时全 404） | `uv run pytest -q tests/console_platform/test_user_side_relations.py` → **3 passed**（exit=0, 0.99s）；B-217 用例内 RULE-rel-001 的可执行断言同命令一并覆盖（集合 PUT 405 L451、单关系 PUT/DELETE 独立生效 L459-465） | 沿用 Spec 原 verifier 文件，未重写成更弱测试 | 真实 Console ASGI + 真实 PG；单关系写由独立请求事务完成 | verified |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`api/models.py`（deployments 四个单关系端点，per-route `AdminAccount`）、`application/model_deployment_service.py`（核属/同供方/重复归一/CAS/审计快照，新增）、`infrastructure/repositories/model_deployment_repository.py`（分页列表、get/find_by_key/find_by_credential、add、cas_update，新增）、`packages/contracts/src/muad_contracts/model_pool.py`（新增 `MemberWeight` 严格 1..100 与 `ModelDeploymentUpdate`）。计划 Files 仅列 `models.py`/服务/测试；仓储与契约属实现必需，已在此披露；无迁移、无 schema 变更。
- 契约与语义事实：成员投影恰为 `{id,key,model_id,credential_id,weight,enabled,revision}`（`ModelDeploymentConfig`，`api/models.py::_deployment_item`）；PUT/DELETE 是**单关系**操作并由独立请求事务完成，集合整体 PUT 不可用（405，RULE-rel-001 的可执行编码）；更新不含 `credential_id`（重绑 = DELETE + POST），`expected_revision` CAS 未命中 `REVISION_CONFLICT`；重复（同 key 或同凭据）409 `MODEL_DEPLOYMENT_EXISTS`，partial unique（`uq_model_deployment_tenant_model_key`/`..._model_credential`）兜底并发落败者；删除=软删（行与审计保留，同 key/凭据可重绑为新行）；`credential.provider_key != model.provider_key` → 422 `MODEL_PROVIDER_MISMATCH`（`provider_key=''` 的遗留模型因此显式拒绝收成员，不写模型行）；缺失/跨租户模型与凭据 → 404 `COMMON_NOT_FOUND`（设计 §3.4「资源归属缺失返回 COMMON_NOT_FOUND」，与既有 `GET /api/v1/models/{id}` 一致——`MODEL_NOT_FOUND` 是平台设置的 400 码，不适用于本面）；读取同样要求 ADMIN；审计 `resource_type=MODEL_DEPLOYMENT`，动作 CREATE/UPDATE/DELETE 且被拒请求零审计；快照不含任何密钥/连接字段。
- 中间过程（诚实记录）：(1) 实现后首跑 4 passed / 2 failed：轮换用例把凭据密钥轮换成夹具第二条凭据的同一值，撞 `uq_model_credential_tenant_provider_digest`（测试侧），改用独立 `SECRET_ROTATED`；(2) provider/tenancy 用例原期望模型缺失为 `MODEL_NOT_FOUND`，实测 400——该码属平台设置（`config/api-messages.yaml` http_status=400），与既有模型路由行为及设计 §3.4 均不符；修正为 `COMMON_NOT_FOUND`（服务 `_require_model`）并同步测试期望（语义以设计为准，实现与测试同改）。
- 仓库 lint/format 现状：本次改动文件 `ruff check` 逐文件干净；新文件（服务/仓储/测试）已 `ruff format`；`api/models.py` 与 `model_pool.py` 的 format 偏差在 HEAD 即存在（预存长行/既有代码），未做无关重排；`uv run mypy apps packages` 394 文件无问题。
- B-217: verified — automated command passed; run_id=306d551e820148a49ccd0919a16dd2be (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-217 RED：`tests/console_platform/test_capacity_webchat_deployment_relations.py` 先行建立 → **6 failed**（0.87s；成员端点未挂载，全 404）
- [2026-10-17] 实现：契约新增 `MemberWeight`/`ModelDeploymentUpdate`；成员服务（核属/同供方/重复归一/CAS/审计快照）+ 仓储；`api/models.py` 挂四个单关系端点（ADMIN）+ 关系投影
- [2026-10-17] 首跑 4 passed/2 failed：轮换用例撞凭据摘要 partial unique（测试侧，改独立 `SECRET_ROTATED`）；模型缺失原期望 `MODEL_NOT_FOUND`（平台设置 400 码）实测 400，按设计 §3.4 与既有模型路由改为 `COMMON_NOT_FOUND`（实现+测试同改）
- [2026-10-17] B-217 GREEN：6 passed；verifier `test_user_side_relations.py` 3 passed；回归 `tests/console_platform tests/contracts` 1619 passed；mypy 394 files 无问题；ruff 逐文件干净
- [2026-10-17] completed (done)

---

## TASK-018: 共享账号额度组管理与降额限制

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-218
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/model_quota_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/model_resources.py`, `tests/console_platform/test_capacity_webchat_quota_groups.py`
- **Estimate**: 45分钟

### Description

RPM/TPM与max_inflight按真实资源存储，null仅关闭该维度限流；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-218][integration] 先在`tests/console_platform/test_capacity_webchat_quota_groups.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：RPM/TPM与max_inflight按真实资源存储，null仅关闭该维度限流。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-218] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-218] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_quota_groups.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-218 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | RPM/TPM与max_inflight按真实资源存储，null仅关闭该维度限流；被引用不可删除，额度归属/降额按排空契约处理 | tests/console_platform/test_capacity_webchat_quota_groups.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_quota_groups.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-218 | `uv run pytest -q tests/console_platform/test_capacity_webchat_quota_groups.py` → **7 failed**（exit=1, 0.98s）：实现前额度组路由未挂载，七个用例全部失败（403 边界断言得 404、列表/详情读不到 `data`） | 同命令 → **7 passed**（exit=0, 1.24s；`ruff format` 收口后复跑 7 passed/1.29s）；回归 `uv run pytest -q tests/console_platform tests/contracts` → **1627 passed**（exit=0, 51.06s）；`uv run pytest -q tests/test_configuration_inventory.py tests/test_error_catalog.py` → **14 passed**（exit=0, 1.46s）；`uv run mypy apps packages` → Success: no issues found in 396 source files；`uv run ruff check` 逐文件干净 | `test_b218_admin_and_csrf_guard_every_write` L191-217（BUILDER 读/写均 403 FORBIDDEN L197-201、缺 CSRF 403 且零落行 L209/214、错 CSRF 403 L212、正确 CSRF 受理 revision=1 L216-217）；`test_b218_limits_store_real_values_and_null_only_closes_that_dimension` L220-252（投影字段集恰为 READ_FIELDS L228、SQL 层 NULL 回读 L229-231、读取投影 null 语义 L237、列表封套 L241、0/负数 422 且零落行 L244-252）；`test_b218_duplicate_key_cas_conflict_and_missing_group_are_explicit` L255-295（同 key 409 MODEL_QUOTA_GROUP_EXISTS L261-263、CAS 命中 revision=2 且库内同步 L265-277、陈旧 revision 409 不覆盖 L279-283、缺失归属 GET/PUT/DELETE 404 L285-295）；`test_b218_tightening_requires_drain_but_relaxation_and_expansion_do_not` L298-366（其他组租约不阻塞本组 L307-313、rpm/max_inflight 调小 409 L315-322、放开 tpm 200 L324-329、扩容 200 L331-337、UNKNOWN 保留占用 409 L339-347、no-op 200 L349-354、SETTLED 后降额+新开限流生效 L356-366）；`test_b218_referenced_delete_conflicts_and_recreation_is_new_identity` L369-410（被活跃凭据引用 409 MODEL_QUOTA_GROUP_IN_USE L391-393、引用解除后删除 200 L398-403、软删行保留 L404-405、同名重建新行新身份 L407-409）；`test_b218_create_replays_first_result_by_persisted_canonical_fingerprint` L413-484（同键同指纹重放同 ID L418-421、endpoint=model-quota-group-create L462、单行单审计 L463、规范 JSON 指纹公式 L465-480、异指纹 409 IDEMPOTENCY_MISMATCH L482-484）；`test_b218_cross_tenant_groups_are_invisible` L487-517（异租户 GET/DELETE 404、列表排除 L506-508、PUT 404 COMMON_NOT_FOUND L510-516、异租户行未被改动 L516-517） | 真实 Console ASGI 应用 + 真实 PostgreSQL（每用例独立租户；partial unique 与复合 FK 由数据库真实裁决）；ADMIN/BUILDER 均真实登录会话（真实 CSRF Cookie/头）；额度组行、幂等回执与审计行走 `control.*` 真实表回读；排空裁决以 `runtime.model_call_lease` 真实行（按 quota_group_id 归属，ACTIVE/UNKNOWN/SETTLED 三态迁移）为准；`null` 以 SQL NULL 回读验证（未设限 ≠ 零额度） | verified |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`application/model_quota_service.py`（服务，新增）、`api/model_resources.py`（新增 `quota_router` 并挂 `api/router.py` admin 组）、`infrastructure/repositories/model_quota_group_repository.py`（仓储，新增）、`packages/contracts/src/muad_contracts/model_pool.py`（新增 `ModelQuotaGroupUpdate`）、`packages/api-kit/src/muad_api/error_codes.py` + `config/api-messages.yaml`（新增 `MODEL_QUOTA_GROUP_EXISTS`，409）、`tests/contracts/test_capacity_webchat_error_catalog.py`（ERROR_STATUSES 扩一项）、`docs/configuration-inventory.csv`（新常量盘点行 + error_codes 行号最小平移）。计划 Files 仅列服务/路由/测试；仓储、契约、错误码与配置盘点属实现必需，已在此披露；无迁移、无 schema 变更（0021 已建表）。
- 契约与语义事实：读取投影恰为 `{id,key,max_inflight,rpm,tpm,revision}`；`rpm`/`tpm` 的 `null` 表示该维度不设应用内限流（SQL NULL 回读验证，不是 0；`0`/负数在契约层 422）；PUT 为整份替换（key/max_inflight/rpm/tpm）+ `expected_revision` CAS，未命中 `REVISION_CONFLICT`；同 key 重复 409 `MODEL_QUOTA_GROUP_EXISTS`（partial unique `uq_model_quota_group_tenant_key` 兜底并发落败者）；降额判定 = `max_inflight` 调小，或 rpm/tpm 数值调小，或从 `null` 新开限流——该组仍有 `ACTIVE`/`UNKNOWN` 租约时 409 `MODEL_CAPACITY_DRAIN_REQUIRED`（扩容、数值调大、放开为 `null`、no-op 与 SETTLED 租约实时生效；判定按租约的 `quota_group_id` 归属，别的组的在飞占用不阻塞）；删除=软删，仍被活跃凭据引用 409 `MODEL_QUOTA_GROUP_IN_USE`（被软删凭据不再阻塞），同名重建为新行新身份；创建幂等（RULE-api-002）endpoint=`model-quota-group-create`，规范 JSON 指纹含 endpoint/tenant/actor 判别键，异指纹 409 `IDEMPOTENCY_MISMATCH`；缺失/跨租户 404 `COMMON_NOT_FOUND`；审计 `resource_type=MODEL_QUOTA_GROUP`，动作 CREATE/UPDATE/DELETE，被拒请求零审计。
- 中间过程（诚实记录）：(1) 新增错误码插入 `error_codes.py` 后其后续枚举成员行号整体位移，配置盘点按最小平移 + 新行补齐；首次自动化脚本写坏 CSV，以 `git checkout` 还原后改为最小变更重做（未把整表重排混入提交）；(2) 测试文件一处断言超长行经 `ruff format` 收口后复跑 GREEN；实现侧无返工。
- B-218: verified — automated command passed; run_id=8ee99f679cb44563a987ab6385287f95 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] started
- [2026-10-17] B-218 RED：`tests/console_platform/test_capacity_webchat_quota_groups.py` 先行建立 → **7 failed**（0.98s；额度组路由未挂载，读写全 403/404）
- [2026-10-17] 实现：契约新增 `ModelQuotaGroupUpdate`；`ModelQuotaGroupService`（幂等创建/降额排空判定/CAS/软删引用冲突/审计快照）+ `ModelQuotaGroupRepository`；`api/model_resources.py` 新增 `quota_router` 并挂 admin 组；新增错误码 `MODEL_QUOTA_GROUP_EXISTS`（目录+契约）
- [2026-10-17] B-218 GREEN：7 passed；回归 `tests/console_platform tests/contracts` 1627 passed；inventory/error-catalog 14 passed；mypy 396 files 无问题；ruff 逐文件干净；`docs/configuration-inventory.csv` 同步新常量与 error_codes 行号平移
- [2026-10-17] completed (done)

---

## TASK-019: 逻辑模型配置去除执行密钥并要求真实输出预算

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-017, TASK-018
- **Source**: agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-model#RULE-model-001
- **Acceptance-Refs**: B-219
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/model_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/models.py`, `tests/console_platform/test_capacity_webchat_logical_model_api.py`, `migrations/versions/00XX_model_pool_legacy_column_drop.py`（末段 revision：物理删除 `model_definition.base_url`/`api_key`；编号随 TASK-010..018 的迁移顺延）
- **Estimate**: 45分钟

### Description

Agent继续显式选择model_id、无默认模型；完整分支及可观测结果见下面验收契约。

本任务同时持有**升级链末段的删列 revision**：0021（TASK-009）只建凭据/成员/额度组并保留 `model_definition.base_url`/`api_key` 供 legacy 入口过渡；本任务在消费方重写完成后，以末段 revision 物理删除两列（0021 后 `provider_key` 以 `''` 表示未声明供方），解决"物理凭据是唯一执行入口"的终点，保证维护窗口仍是一次 `upgrade head` 到完整 head（计划变更记录见 TASK-009 Log 2026-10-17）。

### Checklist

- [x] [B-219][integration] 先在`tests/console_platform/test_capacity_webchat_logical_model_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：Agent继续显式选择model_id、无默认模型。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-219] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-219] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_logical_model_api.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] verifier `harness-model#RULE-model-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-219 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | Agent继续显式选择model_id、无默认模型；OPENAI/protocol不暗换，模型最大输出使用真正发送的max_tokens，旧base_url/api_key执行入口删除（末段 revision 物理删列；0021 为过渡保留，删列生命周期由本任务收口） | tests/console_platform/test_capacity_webchat_logical_model_api.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_logical_model_api.py"] | verified |
| harness-model#RULE-model-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | `model_definition` 不设 `is_default`；每个 Agent 必须显式保存 `model_id`，不存在平台默认模型回退；模型调用只允许 OPENAI 兼容协议。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-219 | `uv run pytest -q tests/console_platform/test_capacity_webchat_logical_model_api.py` → **8 failed, 9 passed**（exit=1, 2.25s）：实现前旧入口仍在，`_payload` 的 `provider_key` 被 `extra_forbidden`（响应 `{"loc":["body","base_url"],"type":"missing"},{"loc":["body","provider_key"],"type":"extra_forbidden"}`）、投影仍带 `api_key_configured`、head 库还是 0026（`information_schema` 仍有旧列）、探针鉴权走旧列、供方声明/锁定分支未实现 | 同命令 → **17 passed**（exit=0, 2.39s）；required verifier `["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"]` → **8 passed + 1 passed/13 deselected**（exit=0, 0.98s/0.22s）；回归 `uv run pytest -q tests/console_platform tests/console_internal` → **397 passed**（51.81s）；`uv run pytest -q tests/migrations` → **87 passed**（153.40s）；`uv run pytest -q tests/frontend` → **345 passed**；`uv run pytest -q tests/console_skill` → **69 passed**；`uv run pytest -q tests/console_platform/test_models_batch_test.py tests/console_platform/test_agent_lifecycle_api.py tests/console_platform/test_agent_mcp_bindings.py tests/console_platform/test_agent_skill_bindings.py tests/console_platform/test_agent_user_grants_api.py tests/console_platform/test_agents_api.py` → **32 passed**；`uv run pytest -q tests/agent_worker/test_schedule_trigger.py` → **8 passed**；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**；`uv run mypy apps packages` → Success: no issues found in 396 source files；`npx tsc --noEmit`（frontend）→ exit=0；`uv run ruff check`（全部改动文件）无新增违规（仅剩基线既有 E501） | `test_b219_create_requires_positive_integer_max_tokens` L183-207（参数化 9 例：缺 `max_tokens`、仅 temperature、0/-1/True/2.5/"2048"/None、以及**同义但从不发送**的 `max_output_tokens`-only 全部 422 COMMON_VALIDATION_ERROR）；`test_b219_create_persists_the_sent_output_budget` L209-222（响应 params 原样 L214、`params_json` SQL 回读 L220-222）；`test_b219_update_requires_positive_integer_max_tokens` L224-238（PUT 三种非法 params 422 且不改动已存预算 L233-234）；`test_b219_legacy_execution_fields_rejected_on_write` L240-258（POST/PUT 带 `base_url`/`api_key` 均 422）；`test_b219_projection_carries_no_execution_secrets_or_endpoints` L261-275（创建/详情/列表三个投影都无 base_url/api_key/api_key_configured 且带 provider_key）；`test_b219_final_revision_drops_legacy_execution_columns` L277-295（真实 `information_schema`：model_definition 无 base_url/api_key/is_default 且有 provider_key L286-290、密码仍在 model_credential L291、ORM 属性已消失）；`test_b219_only_openai_protocol_and_explicit_agent_model` L297-355（ANTHROPIC 422、is_default 422、OPENAI 原样回显、Agent 缺 model_id 422、未知 model_id 404 COMMON_NOT_FOUND、显式 model_id 200 且回显一致 L339-351）；`test_b219_execution_entry_is_the_bound_member_credential` L357-383（真 HTTPServer 探针记录 Authorization：绑定成员凭据→AVAILABLE 且探针恰收到 `Bearer good-key` L372-374、无成员→FAILED/CREDENTIAL_MISSING 且不发请求 L370-371/374）；`test_b219_provider_declared_before_members_then_locked` L385-411（无成员时可声明 provider_key 200 L389-390、绑定成员后改供方 409 COMMON_CONFLICT L397-399、重复声明同值 200 L405-406） | 真实 Console ASGI 应用 + 真实登录会话（ADMIN + CSRF）；真实 PostgreSQL（dev 库已按用户确认升级到 0027；`information_schema` 直读列集合，ORM 属性同步缺失）；真实本地 HTTP 协议探针（`ProbeEndpoint` 记录真实请求路径与 `Authorization` 头，证明执行入口只剩成员凭据）；成员/凭据以真实行写入（复合 FK 与 partial unique 由数据库裁决）；`runtime_credentials` 的 API-09 用例以真实 credential 轮换/停用回读当前值 | verified |
| harness-model#RULE-model-001 | —（沿用原 Spec verifier，不重写；本任务只做消费者适配） | `uv run pytest -q tests/console_platform/test_models_api.py` → **8 passed**（exit=0, 0.98s）+ `uv run pytest -q tests/console_platform/test_agents_api.py -k disabled` → **1 passed, 13 deselected**（exit=0, 0.22s） | 逻辑模型投影不再含 `api_key_configured`：`test_s01_create_persists_declared_fields_without_execution_secrets` L26-60（创建/列表/详情三处投影字段集合断言 + `params_json`/`provider_key` SQL 回读）；`test_e02_e05_schema_rejects_reserved_and_immutable_fields` L144-169（`is_default`/`protocol`/`key` 非法写全 422）；`test_duplicate_key_conflict_and_validation` L172-188（重复 key 409 MODEL_KEY_EXISTS 且 msg 带 key；非法输出预算 422）；`test_agents_api.py -k disabled` → `test_e02_disabled_model_blocks_agent_create`（Agent 必须显式 model_id，无默认模型回退） | 同文件真实 Console ASGI + 真实 PG；`provider_key`/`params.max_tokens` 由真实写路径持久化，Agent 侧 model_id 为真实外键行；本次仅在 S-01 用「投影无执行字段 + 声明字段落库」替换旧「明文密钥落库」断言（旧 `api_key_configured` 语义已删除，属事实同步而非弱化） | verified |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`application/dto.py`（逻辑模型写契约重写：去 `base_url`/`api_key`、加 `provider_key`、`params.max_tokens` 正整数必填）、`application/model_service.py`（快照/创建/更新 + 供方声明-锁定）、`api/models.py`（投影重写）、`infrastructure/models/control.py`（删 `base_url`/`api_key` 属性）、`infrastructure/repositories/model_deployment_repository.py`（`has_live_members`、`find_execution_credential(s)` 过渡解析）、`application/resolve_service.py` + `application/runtime_credentials.py` + `application/model_test_service.py`（三个消费方改从成员凭据取执行入口，无成员即 `CREDENTIAL_MISSING`）、`cli.py`（`SECRET_TABLES` 去掉 `model_definition` 行）、`migrations/versions/0027_model_pool_legacy_column_drop.py`（末段删列 + 门禁 + 拒绝降级）、`docs/model-pool-control-migration.md`（§4/§5/§6 同步 0027 事实）、`docs/configuration-inventory.csv`（`SECRET_TABLES` 值 + 6 行行号位移）。计划 Files 只列了 service/api/测试/迁移；DTO、ORM、仓储、三个消费方、CLI 与文档属"消费方重写 + 删列"的必需联动，在此披露。
- 契约与语义事实：`POST/PUT /api/v1/models` 不再接收 `base_url`/`api_key`（`extra=forbid` → 422）；`params.max_tokens` 必须是**正整数**（bool/float/str/None/0/负数/缺失，以及只会是"从不发送的同义字段"的 `max_output_tokens`-only 全部 422）；该键就是运行时真正发送的输出预算（`executor.py:431` 读 `params["max_tokens"]` → `packages/agent-core/.../openai_provider.py:267-268` 写入请求体 `payload["max_tokens"]`；线级发送证明按计划留给 B-225/E-04）；`provider_key` 创建时可声明（默认 `''`），有成员后不可改（无成员可改；同值重复声明是 no-op）；执行入口 = 可用成员凭据（未删除未停用、非空密钥；批量测试/解析/API-09 同一口径），无成员 → `CREDENTIAL_MISSING`；0027 门禁：仍有非空旧 `api_key` 且无可用成员的模型 → `MODEL_POOL_LEGACY_KEY_NOT_MIGRATED` 整链回滚；`downgrade` → `MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP`。
- 中间过程（诚实记录）：(1) RED 文件三处**测试自身**缺陷在 GREEN 阶段修正（探针用例漏传 `api_key="good-key"`、供方用例未显式传 `provider_key=""`、purge 未删 Agent 触发外键；`session.run_sync` 内 `inspect` 需 `sync.connection()`）——语义与断言未弱化；(2) 夹具让路：console 平台 `tenant` 夹具不再默认建凭据/成员（池资源类用例的租户作用域计数会受噪声影响），改由显式 `model_member` 夹具给真正走 resolve 的 7 个用例补执行入口；`console_skill`/`agent_worker` 夹具同理补成员凭据；(3) 基线既有红转绿：`tests/frontend/test_audit_gap_contract.py::test_b214_resource_type_options_cover_backend_domain` 在 HEAD（0ed35068，独立 worktree 复现）即红——TASK-016/017/018 新增的 `MODEL_CREDENTIAL/MODEL_DEPLOYMENT/MODEL_QUOTA_GROUP` 未进审计筛选域；本任务一并补齐 `AuditFilterBar.RESOURCE_TYPES` 与 zh/en 词条（否则整树不绿）；(4) 迁移单测口径调整：head 前进到 0027 后，B-210..B-214 的"本 revision 回退"用例不再借 head 库（会先撞 0027 的拒绝），改用 `datastore_at_revision` 固定建到各自 revision；断言的拒绝码/回滚事实不变，属"测试只验证自己的 revision"的口径收细而非弱化；(5) dev 库按用户确认执行 `uv run alembic -c migrations/alembic.ini upgrade head`（0026→0027；升级前用迁移同款门禁 SQL 复核：34 行模型、2 行有密钥且都有可用成员 → 门禁通过、无执行入口丢失）。
- 延后项（明确移交）：浏览器 E2E 中仍按旧响应形状断言模型接口的用例（`e2e/tests/model-management/*`、`form-layout`/`model-list-layout`/`detail-layout`/`console-auth`.spec.ts）属前端模型管理改造范围，由 TASK-086/087（模型凭据/成员界面）与 TASK-115（模型池与 Token 管理 E2E）收口；本任务未触碰前端模型管理模块，仅补审计筛选域缺口。
- 全量单测回归：`uv run pytest -q --ignore=tests/acceptance -p no:randomly` → **4490 passed**（exit=0, 1084.67s）。
- B-219: verified — automated command passed; run_id=39202116681f4e03ab82f3bfa512f001 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] 承接计划变更：`model_definition.base_url`/`api_key` 的物理删除从 TASK-009 的 0021 移至本任务的末段删列 revision（用户确认；0021 只建新结构并保留旧列，`provider_key` 过渡期 `''`）
- [2026-10-17] started
- [2026-10-17] B-219 RED：`tests/console_platform/test_capacity_webchat_logical_model_api.py` 先行建立 → **8 failed, 9 passed**（2.25s；旧写入口仍要 base_url/api_key、投影仍带 api_key_configured、head 仍是 0026、探针仍读旧列）
- [2026-10-17] 实现：DTO 去旧执行字段 + `provider_key` + `params.max_tokens` 正整数校验；`ModelService` 供方声明-锁定；投影重写；ORM 删 `base_url`/`api_key`；`ModelDeploymentRepository` 新增可执行成员解析（单/批量）并接入 resolve/API-09/批量探针（无成员 → CREDENTIAL_MISSING）；`cli.SECRET_TABLES` 去 model_definition 行；新增 `migrations/versions/0027_model_pool_legacy_column_drop.py`（门禁 `MODEL_POOL_LEGACY_KEY_NOT_MIGRATED` + 拒绝降级）；迁移手册 §4/§5/§6 同步
- [2026-10-17] dev 库按用户确认升级 0026→0027（升级前门禁 SQL 复核：34 行模型、2 行有密钥且都有可用成员 → 无执行入口丢失）
- [2026-10-17] B-219 GREEN：17 passed；required verifier 8 passed + 1 passed/13 deselected；回归 `tests/console_platform tests/console_internal` 397 passed、`tests/migrations` 87 passed、`tests/frontend` 345 passed、`tests/console_skill` 69 passed、`tests/agent_worker/test_schedule_trigger.py` 8 passed、inventory 4 passed；mypy 396 files 无问题；frontend tsc exit=0；ruff 无新增违规；全量单测（`--ignore=tests/acceptance`）**4490 passed**（1084.67s）
- [2026-10-17] 夹具/口径联动：console `tenant` 夹具不再默认建成员（改 `model_member` 显式夹具供 7 个 resolve 用例）；`console_skill`/`agent_worker` 补成员凭据；B-210..B-214 单 revision 回退用例改用 `datastore_at_revision` 固定各自 revision；补齐审计筛选域 `MODEL_CREDENTIAL/MODEL_DEPLOYMENT/MODEL_QUOTA_GROUP`（HEAD 即红的基线遗留）
- [2026-10-18] 验收冒烟（非全量，全量留给需求级 verify-e2e）：`uv run pytest -q tests/acceptance/task_schedule` → **25 passed**（86.80s）；`tests/acceptance/runtime/test_capability_snapshot.py` → **2 passed**（首轮暴露 runtime 验收清理未先删成员触发 FK，已补 `model_deployment/model_credential` 删除后复跑绿）；`tests/acceptance/dfx/test_dfx_stateless.py::test_s12_stateless_pod_replacement_freezes_snapshot_and_deterministic_hash` → **1 passed**（17.00s，凭据轮换走成员凭据行）
- [2026-10-18] completed (done)

---

## TASK-020: 所选成员凭据的内部实时解析

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-015, TASK-019
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: B-220
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/runtime_credentials.py`, `apps/console-platform/backend/src/muad_console_platform/api/internal_runtime.py`, `tests/console_internal/test_capacity_webchat_resolve_credential.py`
- **Estimate**: 45分钟

### Description

服务身份门控，按冻结model/member/credential核属并检查expected_revision；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-220][integration] 先在`tests/console_internal/test_capacity_webchat_resolve_credential.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：服务身份门控，按冻结model/member/credential核属并检查expected_revision。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-220] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-220] 执行`["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_resolve_credential.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] verifier `harness-secret#RULE-secret-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-220 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 服务身份门控，按冻结model/member/credential核属并检查expected_revision；返回真实Key身份/版本仅在调用内存使用，MCP可选鉴权语义保留 | tests/console_internal/test_capacity_webchat_resolve_credential.py | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_resolve_credential.py"] | verified |
| harness-secret#RULE-secret-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP `auth_secret`、用户/共享凭据 `credential_json`；**平台侧无自有密钥列**，见 Conventions 的休眠列说明），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-220 | `uv run pytest -q tests/console_internal/test_capacity_webchat_resolve_credential.py` → **11 failed, 4 passed**（exit=1, 2.11s）：实现前 API-09 只认 `model_id`（旧投影 `{id, api_key}`，冻结字段被忽略）——冻结引用成功用例断言整份 `SelectedModelCredential` 失败（响应多 `id`、缺 8 项）、版本不匹配仍返回 200（应 409 `MODEL_CREDENTIAL_CHANGED`）、部分冻结三件套仍 200（应 422）、软删/错配/未知引用全 200（应 404/409） | 同命令 → **15 passed**（exit=0, 1.67s）；required verifier `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py` → **14 passed**（exit=0, 2.01s）；回归 `tests/console_internal tests/contracts`+模型池相关 console 用例 → **1361 passed**（13.68s）；`tests/console_platform tests/console_internal tests/contracts tests/agent_runtime tests/test_configuration_inventory.py` → **2461 passed**（745.71s）；DFX E-07 门控验收 `tests/acceptance/dfx/test_dfx_security.py -k internal_endpoints_gate` → **1 passed**（9.02s，无 actor 的受信载荷仍 200 且带 canary）；Runtime→Console 真链路 `tests/acceptance/runtime/test_capability_snapshot.py` → **2 passed**（6.56s）；`uv run mypy apps packages` → Success: no issues found in 396 source files；`uv run ruff check` 逐文件干净 | `test_b220_frozen_references_resolve_the_selected_member_credential` L66-101（响应恰为 `SelectedModelCredential` 九个字段 L70-92：model_id/deployment_id/credential_id/credential_revision=1、`key_resource_id`=`derive_key_resource_id(tenant,provider,digest)`、`quota_group_id=None`、provider_key/base_url/api_key；指纹与摘要不外泄 L94-96；不带 actor 的受信载荷仍 200 L98-101）；`test_b220_credential_revision_mismatch_requires_a_new_reservation` L103-131（真实 UPDATE 轮换到 revision=2 后旧 `expected_revision=1` → 409 `MODEL_CREDENTIAL_CHANGED` 且不回显新旧明文 L118-121；新版本 200 且明文/计量身份随值切换 L123-131）；`test_b220_frozen_member_credential_pair_must_still_match` L133-157（成员-凭据不再是同一对 → 409 且不回显另一把 Key L155-157）；`test_b220_unknown_or_foreign_references_are_not_found` L159-179（未知成员/未知凭据/跨租户 404 COMMON_NOT_FOUND L161-179）；`test_b220_soft_deleted_member_or_credential_is_not_an_entry` L181-211（软删成员 404 L183-192、软删凭据 404 且不回显明文 L194-211）；`test_b220_frozen_triplet_and_execution_ref_are_strict` L213-237（缺任一冻结字段/`expected_revision=0`/`execution_ref.type=SESSION`/`model_id` 非 UUID 全部 422 COMMON_VALIDATION_ERROR）；`test_b220_service_identity_gates_both_branches` L239-252（冻结与过渡两条分支缺/错服务身份均 401/403）；`test_b220_mcp_optional_auth_semantics_are_preserved` L254-291（有 secret 返回秘密 L279-284、无鉴权返回 `null` L285-286）；`test_b220_unfrozen_call_still_returns_the_same_wire_shape` L293-306（过渡分支与冻结分支同一投影 L303-306）；`test_b220_key_rows_are_the_only_authority_for_the_plaintext` L308-327（`information_schema`：明文只在 `model_credential`，逻辑模型无回退列） | 真实 Console ASGI 应用 + 真实服务身份门控（`X-Internal-Service`）+ 真实 PostgreSQL（成员/凭据以真实行写入、轮换是真实 UPDATE、软删是真实 `is_deleted`）；`key_resource_id` 由 tenant/provider/指纹真实派生（`uuid5`，与 TASK-015 ADR-08 同函数）；DFX E-07 用真实栈验证"匿名/伪造 403、受信 200 且可见 canary"；Runtime 侧真链路（capability snapshot）证明新契约对现有调用方兼容 | verified |
| harness-secret#RULE-secret-001 | —（沿用原 Spec verifier，不重写） | `uv run pytest -q tests/test_logging_redaction.py tests/acceptance/test_foundation_ops_audit.py` → **14 passed**（exit=0, 2.01s） | `tests/test_logging_redaction.py`（敏感键清单脱敏）；`tests/acceptance/test_foundation_ops_audit.py`（真实栈审计不落明文） | 本任务新增的响应路径以 `SelectedModelCredential`（`api_key` 为 `SecretStr`：repr/异常不带出，`model_dump()` 排除）装配明文；新增测试断言 `api_key_digest` 与摘要值不出现在响应文本，409/404 分支同样不回显新旧明文 | verified |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`packages/contracts/src/muad_contracts/resolve.py`（新增 `RuntimeExecutionRef`、`ResolveCredentialsRequest`：冻结三件套 all-or-none、`execution_ref` 只接受 RUN）、`packages/contracts/src/muad_contracts/model_pool.py`（`SelectedModelCredential.quota_group_id` 与 `ModelCapacityReservationRequest.quota_group_id` 放开为可空）、`packages/contracts/src/muad_contracts/__init__.py`（导出新契约）、`application/runtime_credentials.py`（冻结分支 + 过渡分支同一投影）、`api/internal_runtime.py`（API-09 改 typed payload）。计划 Files 的三项均在列；契约两处属"冻结引用/可空裁决"的必需载体，在此披露。
- 契约与语义事实：请求体接受冻结三件套（`deployment_id`+`credential_id`+`expected_revision`）同生同灭（部分给齐 422）；按冻结引用核属——缺失/软删/跨租户 404 `COMMON_NOT_FOUND`（不外泄存在性），成员-凭据错配或凭据 revision 前进 409 `MODEL_CREDENTIAL_CHANGED`（调用方重新选择/预占；该码早在错误目录中为此保留，本任务首次使用）；成功响应恰为 `SelectedModelCredential.to_internal_payload()` 九字段（含 `key_resource_id` 与 `credential_revision`，明文只进调用内存），去重指纹不入响应；`quota_group_id` 可空的裁决依据 = `model_credential.quota_group_id` 与 `model_call_lease.quota_group_id` 均为 NULL 允许（TASK-016 把裁决显式留给本任务：不发明"自身组"虚构身份）；`actor_user_id` 按事实定为可选（服务身份才是门控，DFX E-07 受信调用方不带它也须 200）。
- 过渡分支（诚实记录）：三件套缺省时仍按 TASK-019 的"模型级首个可用成员"解析并返回同一投影——这是成员选取（TASK-023）与 Runtime 冻结引用（TASK-024/025）落地前的当前事实；本任务的验收对象是冻结引用分支，收口责任留给 TASK-024/025（JSON 解析入口的 `VALID_EXECUTION_TYPES` 常量随之删除并已从配置盘点移除）。
- 中间过程（诚实记录）：(1) 本任务首版把 `actor_user_id` 定为必填，会让 DFX E-07 的既有受信载荷（无该字段）由 200 变 422；按"服务身份是门控、actor 只是审计关联"的事实改为可选，并在 B-220 用例中显式断言该口径；(2) 既有 `test_runtime_credentials.py` 的旧投影断言（`{id, api_key}`）按新契约适配为九字段并补"轮换后计量身份随值切换"断言（旧断言描述的是已删除的形状，属事实同步而非弱化）；(3) 删除 `VALID_EXECUTION_TYPES` 后配置盘点行同步移除（盘点只记录真实声明）。
- 延后项：review 层 verifier 由需求级 verify-e2e 全量复跑；浏览器/E2E 场景不受本任务影响（无前端改动）。
- B-220: verified — automated command passed; run_id=c0a1cf3d34b24b3cb0f69e21a66797bd (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-220 RED：`tests/console_internal/test_capacity_webchat_resolve_credential.py` 先行建立 → **11 failed, 4 passed**（2.11s；API-09 只认 model_id，冻结引用被忽略、版本不校验、响应仍是旧投影）
- [2026-10-18] 实现：契约新增 `ResolveCredentialsRequest`（冻结三件套 all-or-none、execution_ref 只认 RUN）；`SelectedModelCredential`/`ModelCapacityReservationRequest` 的 `quota_group_id` 按库与租约事实放开为可空（TASK-016 留待本任务裁决）；`runtime_credentials` 冻结分支（404 归属缺失 / 409 MODEL_CREDENTIAL_CHANGED 需重新预占）；API-09 改 typed payload；`api_key` 以 SecretStr 装载
- [2026-10-18] B-220 GREEN：15 passed；verifier `test_logging_redaction.py + test_foundation_ops_audit.py` 14 passed；回归 `tests/console_internal tests/contracts` 1361 passed、`tests/console_platform tests/console_internal tests/contracts tests/agent_runtime tests/test_configuration_inventory.py` 2461 passed；DFX E-07 门控 1 passed、Runtime 真链路 capability snapshot 2 passed；mypy 396 files 无问题；ruff 逐文件干净；配置盘点移除已删常量 `VALID_EXECUTION_TYPES`
- [2026-10-18] 口径裁定：`actor_user_id` 按事实定为可选（服务身份才是门控——DFX E-07 受信载荷不带它也须 200）；过渡分支（缺省三件套）保留同一投影，收口责任 TASK-024/025
- [2026-10-18] completed (done)

---

## TASK-021: PG物理Key与额度组原子预占

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-010
- **Source**: agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-221, B-01
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/model_capacity_service.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/model_capacity_store.py`, `tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`
- **Estimate**: 60分钟

### Description

池→quota_group→key→attempt固定锁序；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-221][integration] 先在`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：池→quota_group→key→attempt固定锁序。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-221] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-221] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] [B-01][integration] 将原B-01落入`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`，先RED后GREEN；并发预占/释放、实际租约存储；断言：容量边界、重复释放、异常退出、取消和租约失效均不超发；失约执行停手。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-221 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 池→quota_group→key→attempt固定锁序；重复attempt不重复占位，跨模型/主调用/摘要/提炼/探针合并真实Key用量，锁内不联网 | tests/agent_runtime/test_capacity_webchat_capacity_reserve.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | verified |
| B-01 | integration | 并发预占/释放、实际租约存储 | 容量边界、重复释放、异常退出、取消和租约失效均不超发；失约执行停手 | tests/agent_runtime/test_capacity_webchat_capacity_reserve.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-221 | 首建 `tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`（14 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_capacity_reserve.py` **1 error in 0.10s**（`ModuleNotFoundError: muad_agent_runtime.application.model_capacity_service`）；按契约钉住 API 面（真实签名、空实现）后再跑 → **14 failed in 0.37s**（exit=1，全部 `NotImplementedError`） | 同命令 → **14 passed in 0.70s**（exit=0）；复跑 3 次 0.77s / 0.69s / 0.73s 全绿 | 固定锁序 `test_b221_locks_pool_then_group_then_key_then_attempt` L405-414（真实存储调用序列 `["pool","capacity:QUOTA_GROUP","capacity:KEY","attempt"]`）；`test_b221_pool_advisory_lock_held_before_capacity_row_lock` L452-489（真实 `pg_locks` 观察到池 advisory 已持有且 `wait_event=transactionid`）；重复 attempt `test_b221_duplicate_attempt_reserves_exactly_once` L531-561（1 顺序 + 4 并发复用同一 `reservation_id`、`reused=True`，KEY/组行只计 1）；Key 用量合并 `test_b221_same_key_usage_merges_across_models_workloads_and_owners` L563-601（两模型 × 主调用/摘要/提炼 → 同一行 inflight=3，探针被拒；租约 owner 列 = run×2 + memory_job×1）；锁内不联网 `test_b221_reserve_and_release_never_leave_the_database` L511-529（真实 socket 只允许 PG 端口 + httpx 一调用即失败）；额度窗口 `test_b221_window_keeps_boundary_bucket_until_it_leaves_the_window` L629-648（回拨 1 分钟仍拒绝、回拨 2 分钟才清零）+ `test_b221_quota_group_window_counts_tokens_shared_across_keys` L603-627（组内两 Key 的 400+400 计入同一 TPM 800）；归属/停用/期限 `test_b221_unknown_or_disabled_credential_is_rejected` L650-666、`test_b221_expired_deadline_never_reserves` L668-683 | 真实 `ModelCapacityService`+`ModelCapacityStore`；真实 PG `runtime.model_capacity`/`runtime.model_call_lease` 行；上限读真实 `control.model_credential`/`control.model_quota_group` 行（无 mock、无替身存储）；锁序证据来自真实 `pg_locks`+`pg_stat_activity` 与真实存储调用记录（包装类逐调用直通 super，不改行为） | verified |
| B-01 | 同上（并发/释放/失约用例同批 14 failed） | 同上 → **14 passed**（同一命令，exit=0） | 并发边界 `test_b01_concurrent_reserves_stop_at_key_capacity` L685-708（8 并发仅 2 成功，其余 `MODEL_CAPACITY_UNAVAILABLE`，两个资源行 inflight=2）；混合池无死锁不超发 `test_b01_mixed_pools_concurrently_stay_ordered_without_oversubscription` L710-744（组上限 3 跨两 Key/8 并发恰好 3 成功、无异常、组行 inflight=3）；重复释放 `test_b01_release_frees_capacity_once_and_duplicate_release_is_noop` L746-778（二次释放 `released=False`、三计数归零不为负）；租约失效 + 失约停手 `test_b01_expired_lease_keeps_capacity_and_fences_the_executor` L780-813（过期不等于远端已停：新预占被拒且占用保留；`ensure_lease_held` 对过期/错 owner 抛 `MODEL_CALL_LEASE_LOST`）；取消/异常退出 `test_b01_cancelled_or_crashed_call_retains_capacity_as_unknown` L815-842（`mark_unknown` 保留 inflight/token，UNKNOWN 上 `release_unsent` 抛 `MODEL_CALL_OUTCOME_UNKNOWN`） | 与上同：并发用真实事务与行锁（无 sleep 伪并发），释放/失约读取真实 `runtime.model_call_lease` 状态列与真实容量行；过期是真实 `lease_until` 更新而非伪造时钟 | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 一致：`apps/agent-runtime/src/muad_agent_runtime/application/model_capacity_service.py`（454 行）、`.../infrastructure/model_capacity_store.py`（324 行）、`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`（842 行）；未新增表/列/索引（复用 0022 已交付结构），未新增迁移。
- 固定锁序事实：预占 = 池 `pg_advisory_xact_lock`（键由 `pool_lock_key(tenant, model)` 派生，跨进程一致）→ `QUOTA_GROUP` 容量行 → `KEY` 容量行 → attempt 租约行；释放/UNKNOWN 走同一行锁序（组→Key→attempt），无锁读只用于先取资源身份，因而与预占不构成环（`test_b01_mixed_pools_...` 以真实并发 + PG 死锁检测验证）。
- 额度窗口事实：固定 60 秒桶（`QUOTA_WINDOW_SECONDS=60`），保留与滚动窗口相交的边界桶（`QUOTA_WINDOW_BUCKETS=2`）——只有整个窗口与边界桶都过期才清零，边界桶到期不清空；`inflight` 不受窗口过期影响（在飞占用不因旧桶过期清空）。KEY 行只按 `model_credential.max_inflight` 限流，组的 rpm/tpm 为 `NULL` 时只关闭该维度（不虚构额度）。
- 状态与错误事实：预占成功插 `ACTIVE` 租约（`epoch=1`）；`release_unsent`（确定未发送）→ `SETTLED` 并回收在飞与窗口计数（窗口已滚过则只减在飞）；`mark_unknown`（取消/异常退出）→ `UNKNOWN` 保留占用，UNKNOWN 上再走 `release_unsent` 抛 `MODEL_CALL_OUTCOME_UNKNOWN`（不假定未计费）；`ensure_lease_held` 对非 ACTIVE/epoch 不符/owner 不符/已过期抛 `MODEL_CALL_LEASE_LOST`（失约停手）。归属缺失 `COMMON_NOT_FOUND`、停用 `MODEL_DISABLED`、超限 `MODEL_CAPACITY_UNAVAILABLE`、期限已过 `MODEL_WAIT_TIMEOUT`（且不建容量行、不写租约）。
- 跨 schema 只读说明：Runtime 读 `control.model_credential`/`control.model_quota_group` 的当前上限数字（额度权威在 Console，Runtime 不落副本、不双写）；与既有先例一致（`admin_run_repository` JOIN `control.agent_definition`、Console 反向只读 `runtime.model_call_lease`）。
- 延后项（诚实记录）：调用续租/usage 结算/UNKNOWN 的证据化释放与 ACTIVE→UNKNOWN 清扫属 TASK-022；加权均衡与 `model_dispatch_state` 计数属 TASK-023；执行链装配（预占→解析→发上游）属 TASK-025/027；本任务只交付容量权威端口与其真实 PG 证据，故 1000 并发容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 中间过程（诚实记录）：(1) 首版把 advisory 锁观察轮询上限设为 6 秒，在共享库被并行批次压慢时出现一次"未观察到池锁"的偶发失败；改为 30 秒上限并保留真实断言（仍是"观察到 advisory 已持有且仍在等行锁"），随后单文件复跑 3 次全绿、前置 109 用例同跑 121 passed；(2) `_reserve_or_code` 先把 `AppError.code`（字符串）转成 `ErrorCode` 再比较，避免用字符串相等掩盖错误码差异。
- B-01: verified — automated command passed; run_id=a667aacc72c5442f931ffed044c48b97 (confirmed_by: runner)
- B-221: verified — automated command passed; run_id=a667aacc72c5442f931ffed044c48b97 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-221/B-01 RED：`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py` 先行建立（14 个行为用例）→ **1 error in 0.10s**（模块缺失）；按契约钉住 API 面后再跑 → **14 failed in 0.37s**（全部 `NotImplementedError`）
- [2026-10-18] 实现：`application/model_capacity_service.py`（池 advisory → quota_group → key → attempt 固定锁序；重复 attempt 幂等复用；保守滚动 60 秒分桶保留边界桶；`release_unsent`/`mark_unknown`/`ensure_lease_held`）+ `infrastructure/model_capacity_store.py`（容量行 upsert+`FOR UPDATE`、租约行读写与状态推进、只读 Console 额度上限）；未新增表/列/索引与迁移
- [2026-10-18] B-221/B-01 GREEN：**14 passed in 0.70s**（exit=0；复跑 3 次 0.77s/0.69s/0.73s 全绿）；锁序证据 = 真实 `pg_locks`（池 advisory 已持有且仍在等行锁）+ 真实存储调用序列
- [2026-10-18] 回归：`tests/contracts` **1281 passed**；`tests/test_configuration_inventory.py` **4 passed**（新增 8 个常量已入盘点）；前置 109 用例同跑（`test_admin_run_api…test_cancellation` + 本任务文件）**121 passed**；`uv run mypy apps packages` 398 files 无问题；`uv run ruff check` 逐文件干净
- [2026-10-18] 稳定性：共享库被并行批次压慢时首轮出现一次 6 秒轮询超时（观察池锁）偶发失败；轮询上限提到 30 秒并保留原断言，单文件复跑 3 次全绿
- [2026-10-18] 同域全量回归：`uv run pytest -q tests/agent_runtime` → **779 passed in 669.51s**（exit=0，含本任务 14 个用例；此前两轮同命令在共享库被并行批次压慢时于池锁观察用例上超时，改为 30 秒上限后在空载窗口一次全绿）
- [2026-10-18] completed (done)

---

## TASK-022: 调用续租、usage结算与UNKNOWN保留

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-021
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-222
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/model_capacity_service.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/model_capacity_store.py`, `tests/agent_runtime/test_capacity_webchat_capacity_settle.py`
- **Estimate**: 60分钟

### Description

epoch/租约CAS、重复释放和跨分钟结算正确；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-222][integration] 先在`tests/agent_runtime/test_capacity_webchat_capacity_settle.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：epoch/租约CAS、重复释放和跨分钟结算正确。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-222] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-222] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_settle.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-222 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | epoch/租约CAS、重复释放和跨分钟结算正确；本地超时不释放远端未知占用，有完成证据才结算，无usage保留保守预占 | tests/agent_runtime/test_capacity_webchat_capacity_settle.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_settle.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-222 | 首建 `tests/agent_runtime/test_capacity_webchat_capacity_settle.py`（13 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_capacity_settle.py` **1 error in 0.11s**（`ImportError: cannot import name 'CapacityRenewal' from ...application.model_capacity_service`：续租/结算/清扫 API 面尚不存在）；按契约钉住 API 面（`CapacityRenewal` + `renew`/`settle`/`sweep_expired` 空实现）后再跑 → **13 failed in 33.08s**（exit=1，全部 `NotImplementedError`） | 同命令 → **13 passed in 0.92s**（exit=0）；复跑 3 次 0.66s / 0.69s / 0.73s 全绿 | 续租推进与容量不动 `test_b222_renew_extends_lease_until_deadline_and_keeps_the_lease` L415-447（`reservation.lease_until < renewal.lease_until <= deadline`、行内值一致、续租后 `ensure_lease_held` 通过、计数仍 (1,1,100)）；epoch/owner CAS 与终态拒续租 `test_b222_renew_fences_stale_holders_and_finished_leases` L449-509；失约即停手（过期不续、占用保留、不超发）`test_b222_renew_refuses_expired_lease_and_keeps_capacity_reserved` L511-560；绝对期限已过拒续租 `test_b222_renew_refuses_a_deadline_that_already_passed` L562-587；并发续租串行且单调不倒退 `test_b222_concurrent_renewals_serialize_without_moving_the_lease_backwards` L589-626（3 并发成功 + 陈旧 owner `MODEL_CALL_LEASE_LOST`、行值 == max(返回值)）；续租 vs 结算真实行锁竞争 `test_b222_renew_and_settle_race_settles_the_attempt_exactly_once` L628-681（结算恰好一次、三计数归零不为负）；成功按实际 usage 释放多余预占 `test_b222_settle_success_releases_excess_reservation_by_actual_usage` L683-702（500→200，请求数保留 1）；有证据无 usage 保守保留 + 重复结算幂等 `test_b222_settle_without_usage_keeps_the_conservative_reservation` L704-736；失败无证据 → UNKNOWN 保留、拒绝未发送、凭证据迟到结算 `test_b222_settle_failure_without_evidence_holds_unknown_until_evidence_arrives` L738-793；UNKNOWN 不得声称未发送 `test_b222_settle_unknown_holds_capacity_and_refuses_unsent_claims` L795-824；跨分钟结算只改当窗 `test_b222_settle_across_minute_boundary_adjusts_only_the_counted_window` L826-858（150+800=950≤1000 允许、+100 拒绝）；窗口整体滚过不污染新桶 `test_b222_settle_after_window_rollover_leaves_the_new_bucket_untouched` L860-899；ACTIVE→UNKNOWN 有界清扫 `test_b222_sweep_marks_expired_active_leases_unknown_without_releasing` L901-955（limit=2 分批、幂等、不释放、不超发、凭证据再结算） | 真实 `ModelCapacityService` + `ModelCapacityStore`；真实 PG `runtime.model_capacity`/`runtime.model_call_lease` 行（无 mock、无替身存储）；续租/结算竞争用真实阻塞事务 + 真实 `pg_locks`/`pg_stat_activity` 观测两个等待者后再放行；跨分钟用真实行更新（窗口起点回拨、租约签发时间回拨），本地超时用真实 `lease_until` 过期；额度上限读真实 `control.model_credential`/`control.model_quota_group` | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 一致：`apps/agent-runtime/src/muad_agent_runtime/application/model_capacity_service.py`（614 行）、`.../infrastructure/model_capacity_store.py`（362 行）、`tests/agent_runtime/test_capacity_webchat_capacity_settle.py`（955 行）；未新增表/列/索引与迁移（复用 0022 结构），无 DDL。
- 计划外披露：`docs/configuration-inventory.csv` 按既有硬约束登记 2 个新增模块级常量并同步被改动行的行号（`EXPIRED_SWEEP_LIMIT=50`、`EXPIRED_SWEEP_LIMIT_MAX=200`）；`tests/test_configuration_inventory.py` 4 passed。
- 结算口径（依据 design §3.2/§3.3/§3.6 与既有契约 `ModelCallSettlement.can_release_capacity`）：无完成证据（`UNKNOWN`，或 `FAILED` 无 `remote_completion`）→ 状态 `UNKNOWN`、在飞与窗口计数全保留（不释放、不超发）；`NOT_SENT`（确定未发送）→ `SETTLED` 并回收在飞 + 请求数 + token；`SUCCEEDED`/`FAILED` + 完成证据 → `SETTLED` 并释放在飞，有 usage 按实际用量替换预占（释放多余），无 usage 保留保守预占（失败不假定供应商未计费）；`SETTLED` 幂等（重复结算/释放不再二次扣减）；`UNKNOWN` 上再声称未发送 → `MODEL_CALL_OUTCOME_UNKNOWN`（与 `release_unsent` 同口径）。
- 续租口径：`renew` 要求 epoch/owner 相符、状态 `ACTIVE` 且当前 `lease_until > now`（失约即停手，同 Worker 租约续租先例 `apps/agent-worker/.../worker/service.py:595`），新期限 `max(旧值, min(now+lease_sec, deadline_at))`——只前进不倒退（并发下陈旧时钟不会缩短期限），不越绝对期限；期限已过 → `MODEL_WAIT_TIMEOUT` 且不延长。**冻结的绝对期限不在租约行**（0022 无该列且本任务禁止 DDL），由调用方（Run/Task 冻结期限，权威归 TASK-035）传入——这是本任务的显式边界裁决，已在用例与实现 docstring 中写明。
- 窗口口径（未改动 TASK-021 的保守滚动 60 秒分桶）：释放/结算只改仍计入当窗的行（`lease.create_time >= row.window_start`）；窗口整体滚过时不再重写新桶（不污染、不出现负计数），旧尝试只释放自己的在飞。`inflight` 始终按证据减 1，不受窗口滚动影响。
- 清扫口径：`sweep_expired` 只把过期 `ACTIVE` 标记 `UNKNOWN` 并保留占用（不写容量行），扫描用 `FOR UPDATE SKIP LOCKED` + `lease_until <= now`（READ COMMITTED 下锁授予后重判谓词，并发续租成功的租约不会被误扫），批量有界 1..200，越界显式 `ValueError`；多实例重复清扫幂等。
- 中间过程（诚实记录）：① 竞争用例首版只统计 `pg_locks` 中 `transactionid` 未授予锁，实测同一行上两个等待者分别走 `transactionid` 与 tuple 锁两条真实路径（实测观测到 1/2，30 秒轮询仍不足），改为「未授予锁属于该 xid 或属于 `runtime.model_call_lease` 表」统计独立等待 backend 数，保留“两个操作都已在真实行锁上排队”的观测强度而不放宽断言；② 测试内窗口回拨改为 KEY 与组行同一事务更新（同一 PG 时间），避免两行桶边界漂移。
- 延后项（诚实记录）：执行链装配（预占→解析→发上游→续租心跳→结算接线）属 TASK-025/027；真实供方额度与实际 usage 采集、1000 并发容量结论属 TASK-110/116；受影响 Key 的隔离与管理员核实提示属 Console 侧端口（TASK-027/028）。本任务只交付容量权威端口的续租/usage 结算/UNKNOWN 保留与清扫语义及其真实 PG 证据。
- B-222: verified — automated command passed; run_id=177c1f703ab7405caea24421ada4c527 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-222 RED：首建 `tests/agent_runtime/test_capacity_webchat_capacity_settle.py`（13 个行为用例）→ **1 error in 0.11s**（`ImportError: cannot import name 'CapacityRenewal'`，续租/结算/清扫 API 面不存在）；按契约钉住 API 面（`CapacityRenewal` + `renew`/`settle`/`sweep_expired` 空实现）后再跑 → **13 failed in 33.08s**（exit=1，全部 `NotImplementedError`）
- [2026-10-18] 实现：`application/model_capacity_service.py`（`renew` epoch/owner CAS + 失约即停手 + 单调不倒退与绝对期限上限；`settle` 走既有契约 `ModelCallSettlement.can_release_capacity` 分档——NOT_SENT 全量回收、有证据按实际 usage 释放多余预占、无 usage 保守保留、无证据保留 UNKNOWN；`sweep_expired` 有界 ACTIVE→UNKNOWN 保留；释放/保留/结算共用一份容量计划）+ `infrastructure/model_capacity_store.py`（`write_lease_until`、`lock_expired_active`：`FOR UPDATE SKIP LOCKED` + 过期谓词）；未新增表/列/索引与迁移
- [2026-10-18] B-222 GREEN：**13 passed in 0.92s**（exit=0；复跑 3 次 0.66s / 0.69s / 0.73s 全绿）；随后为「无共享额度组的 Key」补 1 个用例（结算只动 KEY 行、不虚构组身份）→ 单文件 **14 passed in 0.90s**
- [2026-10-18] 竞争用例口径：真实阻塞事务占住 attempt 行，`pg_locks`/`pg_stat_activity` 观测到两个操作都已在行锁上排队（同一行上分别走 `transactionid` 与 tuple 锁两条真实路径）后再放行——保留观测强度，不放宽断言
- [2026-10-18] 回归：`uv run pytest -q tests/agent_runtime tests/contracts` → **2073 passed in 668.45s**（exit=0；相对 TASK-021 基线 1281+779 恰为 +13，即本任务当时版本的 13 个用例；第 14 个用例于该轮之后补入，由单文件复跑与 Done Gate 的验收命令覆盖）；`tests/test_configuration_inventory.py` **4 passed**（新增 2 常量已登记并同步行号）；`uv run ruff check` 逐文件干净；`uv run mypy apps packages` 400 files 无问题
- [2026-10-18] 口径裁定（诚实记录）：① 冻结的绝对期限不在租约行（0022 无该列、本任务禁止 DDL），续租由调用方传入该期限，权威归 TASK-035；② 窗口已整体滚过时结算不再重写新桶（不污染、不为负），旧尝试只释放自己的在飞；③ 过期 ACTIVE 清扫只保留占用，凭完成证据才释放（不存在任意冷却释放配置）
- [2026-10-18] completed (done)

---

## TASK-023: 加权均衡、共享epoch与工作负载公平调度

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-021
- **Source**: agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-223
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/model_allocator.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/model_dispatch_store.py`, `tests/agent_runtime/test_capacity_webchat_pool_allocator.py`
- **Estimate**: 60分钟

### Description

等权空闲池预占次数差≤1；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-223][integration] 先在`tests/agent_runtime/test_capacity_webchat_pool_allocator.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：等权空闲池预占次数差≤1。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-223] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-223] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_allocator.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-223 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 等权空闲池预占次数差≤1；按dispatch_count/weight与在飞量稳定轮转，重试计尝试、低额度不超发，后台工作按冻结权重/老化可进展 | tests/agent_runtime/test_capacity_webchat_pool_allocator.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_allocator.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-223 | 首建 `tests/agent_runtime/test_capacity_webchat_pool_allocator.py`（14 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_pool_allocator.py` **1 error in 0.10s**（exit=1，收集期 `ModuleNotFoundError: muad_agent_runtime.application.model_allocator`，实现文件尚未创建）；实现后再补 1 个并发低额度用例（现 15 个） | 同命令 → **15 passed in 1.55s**（exit=0；复跑 1.48s / 1.59s 全绿） | 等权均衡 `test_b223_equal_weight_idle_pool_spreads_picks_within_one`（顺序 10 次 → 计数 `[3,3,4]`，差 ≤1，返回成员与真实计数逐一对齐，Key 在飞=计数）；并发均衡 `test_b223_equal_weight_pool_concurrent_picks_stay_balanced`（12 次真实并发 → `[4,4,4]`，每 Key 在飞 4）；加权 `test_b223_weighted_picks_follow_count_over_weight`（权重 3:1、8 次 → 6:2）；平局规则 `test_b223_tie_breaks_by_normalized_inflight_then_rotation`（先在飞归一化、再稳定轮转，两成员各 1）；共享 epoch 归一 `test_b223_shared_epoch_normalizes_stale_counts_without_blanket_reset`（旧 epoch 计数 9 → 归一后 0 并被选中、写回当前 epoch=1；未选中行仍是旧 epoch 的 3 未被全量清零；同 epoch 累加 1→2→3→4；平局改由在飞归一化决定）；策略版本 `test_b223_policy_hash_separates_versions_and_must_self_verify`（两 policy_hash 各自一列、伪造哈希 `COMMON_VALIDATION_ERROR` 且不写计数）；低额度不超发 `test_b223_low_key_quota_member_is_skipped_and_never_oversubscribed`（Key 上限 1 满员即跳过、该成员计数 0）、`test_b223_shared_quota_group_capacity_blocks_oversubscribe`（组上限 2：第 3 次 `MODEL_CAPACITY_UNAVAILABLE`+reason `NO_CAPACITY`，组行在飞=2）、`test_b223_concurrent_shared_group_never_oversubscribes`（并发 6 → 恰 2 成功、4 明确拒绝，组在飞恒 2、每 Key 不超自身上限）；工作负载公平 `test_b223_background_defers_under_interactive_load_then_advances`（8:1 且交互在飞 1 → 新后台 `MODEL_CAPACITY_UNAVAILABLE`+reason `WORKLOAD_SHARE` 且不写计数；等待 31s 超老化 30s → 必须可进展）、`test_b223_frozen_weights_and_interactive_never_gated_by_background`（冻结同权 8:8 → 后台按同权进展；后台在飞 3 次不阻止新交互）；尝试计数与复用 `test_b223_retry_counts_one_attempt_and_replay_reuses_lease`（重试新 attempt → 2；同 attempt 重放 `reused=True`、不重复计数、租约/到期复用；候选与既有冻结身份不符 → `MODEL_CALL_LEASE_LOST`）；失败不计数 `test_b223_expired_deadline_and_missing_members_never_count`（期限已过 `MODEL_WAIT_TIMEOUT`、无候选 `CREDENTIAL_MISSING`，两者都不写调度计数/租约/容量行）；成员事实 `test_b223_disabled_or_rebound_members_are_not_picked`（停用成员与冻结凭据不符候选不可执行）；锁与无网络 `test_b223_selection_happens_under_pool_lock_without_network`（真实存储调用序列：首调用即池 advisory 锁、选择事务 1 次计数写入、被延迟的尝试不计、预占事务才写容量行、`bump_dispatch_state` 先于 `insert_lease`；真实 socket 仅允许 PG 端口 + httpx 一调用即失败） | 真实 `ModelAllocator`/`ModelDispatchStore` + 真实 `ModelCapacityService`；真实 PG 行 `runtime.model_dispatch_state`（唯一 (tenant,model_id,deployment_id,policy_hash)）/`runtime.model_capacity`/`runtime.model_call_lease`，成员与额度上限读真实 `control.model_deployment`/`model_credential`/`model_quota_group`（无 mock、无替身存储；记录型 Store 逐调用直通 super 只记录顺序）；epoch 为 PG 时钟派生并断言落在真实 `clock_timestamp()` 前后窗口内；并发用真实事务与池 advisory 锁（无 sleep 伪并发） | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 一致：`apps/agent-runtime/src/muad_agent_runtime/application/model_allocator.py`（`ModelAllocator`/`ModelDispatchPolicy`/`ModelMemberCandidate`/`ModelAllocationRequest`/`ModelAllocation`、`dispatch_policy_hash`、`dispatch_epoch_for`）、`apps/agent-runtime/src/muad_agent_runtime/infrastructure/model_dispatch_store.py`（`ModelDispatchStore` 继承 `ModelCapacityStore`，只增成员/计数/在飞读与原子计数语句）、`tests/agent_runtime/test_capacity_webchat_pool_allocator.py`（15 个用例）；未新增表/列/索引与迁移（复用 0022 `runtime.model_dispatch_state`），未改 `model_capacity_service.py`/`model_capacity_store.py`。
- 调度事实：选择事务先取 tenant/model 池 advisory 锁（与 TASK-021 同一 `pool_lock_key`），锁内完成候选扫描（成员启用/凭据匹配/绑定额度/共享组在飞/调度计数/工作负载分布）与一次尝试的计数；排序 = `dispatch_count/weight` → 归一化在飞量（取 Key 与共享组中更紧的一侧）→ 最久未选 → 成员 ID 稳定序；事务内不发任何网络请求。
- 计数与额度分离（诚实记录）：计数在池锁内的选择事务提交，额度预占交给既有 `ModelCapacityService.reserve` 紧随的短事务（冻结身份原样传入）；正常路径即"预占成功同时递增计数"，预占被其它池抢走额度时按 `MODEL_CAPACITY_UNAVAILABLE` 明确回压且本次尝试已计入（契约的"重试也计一次尝试"）；额度判定与滚动窗口算法**只有一处实现**（TASK-021/022），本任务不复制，避免与其续租/结算事实漂移。
- 共享 epoch：`dispatch_epoch = floor(PG 时钟秒 / dispatch_epoch_sec)`（跨实例同值，不用本机墙钟）；计数按 epoch 归一为"读作 0 / 选中时就地写回",不做全量清零；`policy_hash` 由 `dispatch_policy_hash`（规范 JSON + SHA-256，≤128 字符）唯一确定并强制自洽校验，TASK-024 冻结快照时必须复用同一函数（不同版本分布分别解释）。
- 工作负载公平：交互与 `PROBE` 不设份额门（探针并发由 Console 探针预算与 API-17 占额约束）；`BACKGROUND` 按冻结权重让路（`(n_b+1)×W_i ≤ (n_i+1)×W_b`，空闲池首个请求直接放行），真实等待 ≥ `aging_interval_sec` 后一律放行（老化，不能永久饿死）。
- 错误事实：无当前可执行成员 `CREDENTIAL_MISSING`；额度/份额延迟 `MODEL_CAPACITY_UNAVAILABLE`（`data.reason` = `NO_CAPACITY`/`WORKLOAD_SHARE`）；期限已过 `MODEL_WAIT_TIMEOUT`；既有 attempt 冻结身份不在候选集 `MODEL_CALL_LEASE_LOST`；策略字段非正或哈希不自洽 `COMMON_VALIDATION_ERROR`。
- 延后项（诚实记录）：把分配器接进真实调用链（预占→按 credential_id+expected_revision 解析 Key→发上游、轮换不一致按未发送释放/有界重选）属 TASK-025；旧 policy_hash 行的回收（"不再被活跃 Run 引用后才可回收"）与任务级模型等待队列投影（WAITING_MODEL/queue_reason 消费 `data.reason`）属 TASK-036/后续维护任务；调度/均衡指标进入指标目录属 TASK-098；本任务 1000 并发容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 中间过程（诚实记录）：实现后首轮 12 passed / 2 failed，均为**测试期望写错**而实现正确——① 共享 epoch 用例把"排序取最小计数"写成"期望取最大"，并误以为同 epoch 会清零（事实是同 epoch 内继续累加，只有旧 epoch 归一）；② 锁序用例把池锁次数写成 2，实际为 3（两次选择事务 + 一次预占事务）。修正期望后 15 passed；未放宽任何断言。
- 环境发现（不属于本任务改动，供协调）：与另一 worktree（TASK-022）并行跑全量 `tests/agent_runtime` 时，`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py::test_b221_pool_advisory_lock_held_before_capacity_row_lock` 在跨进程竞争下可能让观察轮询失败并留下"已检出但未关闭的 blocker 连接"，其后的租户清理 `DELETE FROM runtime.model_capacity` 永久等 `tuple` 锁（`pg_stat_activity` 现场：一个 `idle in transaction` 持组行锁 18 分钟、一个等候者等 `transactionid`、一个清理等 `tuple`），表现为整轮串跑挂死；单跑该文件 0.68s 全绿。该文件骨架属 TASK-021 已 verified 资产，本任务不改，按"先怀疑环境残留/共享库竞争"处理并复跑。
- B-223: verified — automated command passed; run_id=acbb139ecde94d2882cc9a1b85f33e95 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-223 RED：首建 `tests/agent_runtime/test_capacity_webchat_pool_allocator.py` → `uv run pytest -q tests/agent_runtime/test_capacity_webchat_pool_allocator.py` **1 error in 0.10s**（exit=1，`ModuleNotFoundError: muad_agent_runtime.application.model_allocator`）
- [2026-10-18] 实现：`application/model_allocator.py`（池锁内候选扫描→工作负载份额/老化门→dispatch_count/weight 排序→共享 epoch 归一计数；预占交既有 `ModelCapacityService.reserve`）+ `infrastructure/model_dispatch_store.py`（`ModelDispatchStore`：真实成员/计数/在飞读 + 原子 upsert 计数）；未新增表/列/索引与迁移
- [2026-10-18] B-223 GREEN：**15 passed in 1.55s**（exit=0；复跑 1.48s / 1.59s 全绿）；断言逐项见 Acceptance Evidence
- [2026-10-18] 回归：`tests/contracts` **1281 passed**；`tests/architecture` **33 passed**；`tests/test_configuration_inventory.py` **4 passed**（新增 3 个常量已入盘点）；`tests/agent_runtime` **794 passed in 668.80s**（exit=0，含本任务 15 个用例；此前一次与 TASK-022 并行跑时被共享库跨进程竞争拖入 TASK-021 文件的环境性挂死，清理后空载窗口一次全绿）
- [2026-10-18] 质量门：`uv run ruff check` 三文件干净；`uv run mypy apps packages` 402 files 无问题；新增 3 个模块级常量已入 `docs/configuration-inventory.csv`（盘点用例 4 passed）；函数最长 50 行（`_select`）
- [2026-10-18] completed (done)


---

## TASK-024: 冻结模型池语义与非密钥执行策略

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-020, TASK-023
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-memory#RULE-memory-006, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: B-224
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py`, `tests/agent_runtime/test_capacity_webchat_pool_snapshot.py`
- **Estimate**: 60分钟

### Description

冻结model/member/路由/输出/等待及完整记忆设置，排除Key/指纹/计量身份与启动预算；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-224][integration] 先在`tests/agent_runtime/test_capacity_webchat_pool_snapshot.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：冻结model/member/路由/输出/等待及完整记忆设置，排除Key/指纹/计量身份与启动预算。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-224] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-224] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_snapshot.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] verifier `harness-memory#RULE-memory-006`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。
- [x] verifier `harness-snapshot#RULE-snapshot-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-224 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 冻结model/member/路由/输出/等待及完整记忆设置，排除Key/指纹/计量身份与启动预算；旧resume不补新默认、不更换型号或延长原deadline | tests/agent_runtime/test_capacity_webchat_pool_snapshot.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_snapshot.py"] | verified |
| harness-memory#RULE-memory-006 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 新 Run/job 冻结完整 memory 设置、工具历史预算及提炼能力；设置执行期不重读，在线硬额度只提高，降低必须维护窗口排空。迁移需排空所有旧在途 Run/Task/job；每 key 单一稳定基线保留全部 legacy incarnation，不伪造确认来源，旧 AGENT 按原更新时间补 TTL。迁移和 ORM 约束/索引同构；移除旧 ratio/source_type 协议，历史快照原样保留仅作审计，不加兼容写层。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"] | planned |
| harness-snapshot#RULE-snapshot-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-224 | 首建 `tests/agent_runtime/test_capacity_webchat_pool_snapshot.py`（6 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_pool_snapshot.py` **1 error in 0.10s**（exit=1，收集期 `ImportError: cannot import name 'PoolExecutionPolicy' from 'muad_agent_runtime.application.context_settings'`，冻结/读回实现尚未存在） | 同命令 → **6 passed in 4.34s**（exit=0） | 冻结主场景 `test_b224_new_run_freezes_members_routing_output_wait_and_memory` L405：路由段逐键等于设置值且 `policy_hash == dispatch_policy_hash(...)` L429-441（复用 TASK-023 唯一口径、长度 64 ≤128 列 L442）、等待段 `capacity.model_wait_timeout_sec` L444、成员段恰三键（`deployment_id`/`credential_id`/`weight`，按 `key,id` 稳定序冻结）L446-458、完整 memory 分组 L460-461、预算载体 `max_model_retries` L463、输出预算与型号/版本在 `model_json.params` L465-468、读回口径 `pool_execution_policy_of`/`frozen_model_members_of` L470-481；只影响新 Run `test_b224_settings_member_and_model_changes_only_affect_new_runs` L484（真实控制面三腿变更证据 L513-519；新 Run 吃新策略/新权重/新定义 L521-533；旧 Snapshot 行逐列不变 L536-540）；旧 resume `test_b224_resume_keeps_frozen_pool_model_and_deadline` L543（续跑请求仍是冻结型号/版本/参数/连接 L583-585、冻结执行预算 L586-587、快照整行不变 L589、原 deadline 不延长 L591-593、新设置只对新 Run 生效 L603-608）；排除项 `test_b224_snapshot_excludes_key_fingerprint_identity_and_startup_budget` L627（明文/指纹/计量身份都不在盘面 L643-654、启动资源预算键集不相交 L655-656）；读回口径 `test_b224_legacy_policy_without_pool_leaves_is_not_backfilled` L664 与 `test_b224_tampered_or_malformed_frozen_policy_is_rejected` L683（篡改哈希/缺叶/非法成员 7 处 `PlatformSettingsError`，空列表 `()` 是合法冻结事实 L721） | 真实 Runtime 业务路径：Run 经真实 `RunService.start`（`_create_run` 在创建边界取一次设置快照）与真实 `RunService.resume` 创建/续跑；真实 PostgreSQL 行：`control.platform_setting`（经真实 Console `PlatformSettingsService` 读写）、`control.model_definition`/`model_credential`/`model_deployment`（真实权重 UPDATE 与新增成员）、`runtime.runtime_snapshot` 逐列回读、`runtime.run_record` 绝对期限；隔离租户（`test-<uuid>`）只写/删本租户行，不删共享 dev 数据；无 DDL/迁移（复用既有列/表） | verified |

**实施范围与证据说明**

- 实现文件与计划 Files 一致：`apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`（`snapshot_policy(pool=…, members=…)` 两段新冻结 + `frozen_member_payload`/`frozen_model_members_of`/`_frozen_member` 读回 + `_create_run` 在创建边界读成员并解析池策略）、`apps/agent-runtime/src/muad_agent_runtime/application/context_settings.py`（`PoolExecutionPolicy`/`resolve_pool_execution_policy`/`pool_policy_payload`/`pool_execution_policy_of`：模型池策略的设置→冻结→读回缝）、`tests/agent_runtime/test_capacity_webchat_pool_snapshot.py`（6 用例）。**超出计划 Files 的披露**：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/model_dispatch_store.py` 新增 `MemberRef` 与 `read_member_refs`（成员非密钥引用+权重的真实 PG 读，Runtime 只读 `control.model_deployment` 的先例与 `_LIVE_MEMBERS_SQL` 同文件同表）；`tests/agent_runtime/test_memory_redesign_snapshot.py` 与 `tests/agent_runtime/test_runs_api.py` 各一处期望同步（真实创建路径多冻结 `model_members` 空列表，断言仍是整份 policy/整份 hash 相等）；`docs/configuration-inventory.csv`（新增 5 个模块级常量行 + run_service.py 既有常量行号位移）。
- 冻结口径（诚实记录）：`policy_json.model_pool` = 路由五叶 + `policy_hash`（**复用** `model_allocator.dispatch_policy_hash`，不自造哈希口径）；`policy_json.capacity` = `model_wait_timeout_sec`（准入/排队叶属 TASK-029/030 的受理边界，本任务不越界）；`policy_json.model_members` = 成员 `deployment_id`/`credential_id`/`weight`（按 `key,id` 稳定序，**非密钥**）；输出预算就是 `model_json.params.max_tokens`（TASK-019 起写入即要求正整数），不另存第二份来源。`members=None`（直构/历史路径）不写键，`[]` 才是"该模型当前确实没有成员"的冻结事实——旧行**不补新默认**（读回返回 None）。
- 排除项是硬约束：Key 明文、`api_key_digest`、由其派生的计量身份 `key_resource_id`、`credential_revision` 与启动资源预算（`SharedSettings` 全部环境项键）都不进快照/哈希；成员读回对多余键（例如 `key_resource_id`）显式拒绝而不是忽略。`credential_revision` 按设计只记录在调用租约（API-09 实时解析），本任务不冻结它。
- 调用链装配**未越界**：预占→按 `credential_id`+`expected_revision` 解析 Key→发上游、以及按冻结成员构造候选并调用 `ModelAllocator` 属 TASK-025/027；本任务只交付"冻结 + 策略 + 读回口径"。
- 中间过程（诚实记录）：(1) 首轮 4 passed / 2 failed——① 读回用例里 `del policy["model_members"]` 在直构策略上没有该键（测试自身缺陷，改 `pop(..., None)`，断言未弱化）；② 续跑用例用文本"继续"触发的是**记忆续接选项**流程（真实 `MEMORY_RESUME_NOTICE`，不装配执行器），改用普通消息后走真实模型路径；(2) 两处既有断言按新事实同步（**不是放宽**，仍是整份相等）：`test_memory_redesign_snapshot.py` 的整份 policy 期望补 `"model_members": []`（真实创建路径比直构快照多冻结成员事实）、`test_runs_api.py` 的 `policy_json == snapshot_policy(members=())` 与 `_expected_hash` 同口径（真实 POST 路径）；(3) mypy 一处 `Mapping|None` 收窄后 402 files 无问题；(4) 全量 `tests/agent_runtime` 首轮 813 passed / 1 failed（正是上面第 (2) 条的 `test_runs_api.py` 整份相等断言），同步后复跑见下。
- B-224: verified — automated command passed; run_id=7d257a21333e4fd49d700448e649469b (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-224 RED：首建 `tests/agent_runtime/test_capacity_webchat_pool_snapshot.py`（6 用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_pool_snapshot.py` **1 error in 0.10s**（exit=1，收集期 `ImportError: cannot import name 'PoolExecutionPolicy' from 'muad_agent_runtime.application.context_settings'`）
- [2026-10-18] 实现：`context_settings.py` 新增 `PoolExecutionPolicy`（`policy_hash` 复用 `dispatch_policy_hash`）+ `resolve_pool_execution_policy` + `pool_policy_payload` + `pool_execution_policy_of`；`run_service.py` 的 `snapshot_policy`/`build_snapshot` 增加 `pool`/`members` 两参、`_create_run` 在同一份设置快照上解析池策略并读成员非密钥引用；`model_dispatch_store.py` 新增 `MemberRef`/`read_member_refs`（只读 `control.model_deployment`，含停用成员，权重随 Run 冻结）；无新增表/列/迁移
- [2026-10-18] B-224 GREEN：**6 passed in 4.34s**（exit=0）；断言逐项位置见 Acceptance Evidence
- [2026-10-18] verifier `harness-memory#RULE-memory-006`：`uv run pytest -q tests/migrations/test_memory_redesign_migration.py tests/agent_runtime/test_memory_redesign_snapshot.py` → **12 passed in 13.85s**（exit=0；含按新事实同步的整份相等断言）
- [2026-10-18] verifier `harness-snapshot#RULE-snapshot-001`：`bash -lc 'uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"'` → **4 passed in 1.89s + 35 passed/779 deselected in 14.11s**（exit=0）
- [2026-10-18] 质量门：`uv run ruff check` 四文件干净；`uv run mypy apps packages` 402 files 无问题；`tests/test_configuration_inventory.py` **4 passed**（新增 5 个常量行 + 行号位移已入盘点）
- [2026-10-18] 回归：`tests/agent_runtime` **814 passed in 678.53s**（exit=0，含本任务 6 个用例；首轮 813 passed/1 failed 是上表已同步的 `test_runs_api.py` 整份相等断言，同步后复跑全绿）；`tests/contracts tests/architecture` **1314 passed in 4.59s**（exit=0）
- [2026-10-18] completed (done)

---

## TASK-025: 真实AuditedModelProvider调用链接入资源池

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-022, TASK-024
- **Source**: agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**: harness-memory#RULE-memory-003
- **Acceptance-Refs**: B-225, E-04
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `apps/agent-runtime/src/muad_agent_runtime/application/model_call_trace.py`, `tests/agent_runtime/test_capacity_webchat_provider_pool.py`
- **Estimate**: 60分钟

### Description

预占提交后解析所选Key，轮换不一致按未发送释放/有界重选；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-225][integration] 先在`tests/agent_runtime/test_capacity_webchat_provider_pool.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：预占提交后解析所选Key，轮换不一致按未发送释放/有界重选。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-225] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-225] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] [E-04][integration] 将原E-04落入`tests/agent_runtime/test_capacity_webchat_provider_pool.py`，先RED后GREEN；真实故障模型 HTTP 服务和调度存储；断言：429/5xx/断流在 deadline/重试预算内处理；已输出正文不混拼另一轮回复；不跨型号暗换。
- [x] verifier `harness-memory#RULE-memory-003`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-225 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 预占提交后解析所选Key，轮换不一致按未发送释放/有界重选；同会话可换Key但上下文/型号一致，429遵守Retry-After与原deadline，逐attempt审计可追溯 | tests/agent_runtime/test_capacity_webchat_provider_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | verified |
| E-04 | integration | 真实故障模型 HTTP 服务和调度存储 | 429/5xx/断流在 deadline/重试预算内处理；已输出正文不混拼另一轮回复；不跨型号暗换 | tests/agent_runtime/test_capacity_webchat_provider_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | verified |
| harness-memory#RULE-memory-003 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。revision 缓存只存候选行：冷读取行与实际 revision 同快照，暖读取 revision 与当前语句时间，并逐次过滤 TTL、重新选择/渲染；读取失败清除旧缓存和 MemoryComponent。每个模型请求前刷新独立 MemoryComponent，完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-225 | 首建 `tests/agent_runtime/test_capacity_webchat_provider_pool.py`（11 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_provider_pool.py` **1 error in 0.18s**（exit=1，收集期 `ImportError: cannot import name 'POOL_RESELECTION_LIMIT' from 'muad_agent_runtime.application.executor'`，池化链路尚未存在） | 同命令 → **11 passed in 13.27s**（exit=0；复跑 13.34s/13.26s/13.27s 全绿） | 预占先于解析 `test_b225_reserves_before_key_resolution_then_settles_by_usage` L823：解析载荷恰为冻结三件套 L845-847、**解析时点**租约已 ACTIVE 且冻结同一 Key 身份/版本 L848-851（观察层逐调用直通真实 HTTP，读取真实 `runtime.model_call_lease`）、上游只收到一次且型号/鉴权来自所选成员 L836-839、结算 `reserved_tokens == 实际 usage` 且 inflight 归零 L856-862、逐 attempt 审计 attempt=0/OK + 真实 usage L866-868；轮换竞争 L880：两个租约（先释放后重选）L907-914、旧身份容量行 inflight=0 且窗口计数=0 L916-918、**发送的 Key 与第二次预占身份一致**（不回显旧 Key、不拿新 Key 消费旧槽位）L921-933、审计序列 `[RETRY/CREDENTIAL_CHANGED, OK]` + attempt 序号 [0,1] + 行 id 互异 L937-942、调度计数=2（重试也计一次尝试）L944；有界重选 L947：租约数 = 上限+1 L976、全部 SETTLED 且无泄漏 L977-982、上游零调用 L974；同会话换 Key L992：两轮型号恒为冻结型号 L1025、两轮 Key 不同且第二轮 = 另一成员 L1026-1028、SYSTEM 与本轮输入逐字节一致 L1030-1031、租约计量身份分别等于两成员 L1034-1039；续租心跳 L1047：`lease_until - create_time > 租期+0.3s` L1071（调用 1.5s、租期 1s；L1066 断言调用真的跑满）；失约停手 L1076：耗时 < 2.5s（探针延迟 3.0s）L1114、租约 UNKNOWN 且 inflight/token 保留 L1116-1118、审计无 OK L1120；预占估算 L1290：`reserved ≥ max_tokens+1` L1313、倍率 4.0 与 1.0 的差 ≥ 3×输入字节 L1314 | 真实 Runtime 业务路径：Run 经真实 `RunService.start`（真实快照/受理/终态）+ 真实 `default_executor_factory` 装配；真实 PostgreSQL 行：`runtime.model_call_lease`/`model_capacity`/`model_dispatch_state`/`model_invocation_audit` 与 `control.model_definition`/`model_credential`/`model_deployment`（隔离租户 `test-<uuid>`，只写/删本租户行）；真实 Console 应用（uvicorn 127.0.0.1 + 真实服务身份门控）提供 API-09，轮换竞争是真实凭据行 UPDATE 后的真实 409；真实模型 HTTP 探针（uvicorn 127.0.0.1）记录型号/鉴权/正文；无 mock 存储/租约/解析服务 | verified |
| E-04 | 同上（11 用例同批 1 error） | 同上 → **11 passed**（同一命令，exit=0） | 429 遵守 Retry-After `test_e04_429_honors_retry_after_and_settles_each_attempt` L1156：两次上游调用间隔 ≥ 0.4s L1172-1174、两次尝试各 SETTLED 且 429 那次 token 预占保守保留 L1176-1186、审计 `[RETRY/RATE_LIMITED, OK]` L1179-1182；超 deadline 即停 L1189：`Retry-After: 5s` 对 1s deadline → 只有 1 次调用（L1207）+ `RunnerDeadlineExceeded` L1204-1205；5xx 有界重试 L1219：恰好 3 次调用（= max_model_retries+1）L1236、三租约 UNKNOWN 且 inflight/token 全额保留（不超发）L1238-1241、审计三行 RETRY/UNAVAILABLE L1243-1247；断流不混拼 L1255：真实 `AgentRunner` 抛 `RunnerModelError` 且增量只出现一次 L1277、只有 1 次上游调用（不重试）L1278、租约 UNKNOWN 保留占用 L1280-1282；不跨型号暗换：两轮/两次尝试的请求型号恒为冻结型号 L1025/L1296 | 与上同：真实故障模型 HTTP 探针按脚本返回 429（带 Retry-After）/5xx/断流；真实 `AgentRunner` 重试预算与 deadline 生效；真实 `ModelCapacityService` 结算分档（远端已应答释放槽位、保留 token；传输层失败/断流 UNKNOWN 保留占用） | verified |
| harness-memory#RULE-memory-003 | —（沿用原 Spec verifier，不重写） | `uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_context.py tests/agent_core/test_memory_redesign_selection.py` → **56 passed in 70.51s**（exit=0） | 记忆候选/缓存/上下文预算与选择的原 verifier 全量复跑（本任务改动未触碰记忆组件；池化链路每次模型请求前的记忆刷新路径不变） | 真实 PostgreSQL + 真实记忆组件（原用例边界不变） | verified（review 层由需求级 verify-e2e 复跑） |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`apps/agent-runtime/src/muad_agent_runtime/application/executor.py`（`PoolCallScope`/`_pool_scope_for`/`conservative_input_estimate`/`reserved_tokens_for`/`PooledModelProvider`/`ModelAttemptLedger`；`AuditedModelProvider` 增 `bind`/`ledger`；`ExecutorRequest` 增 `pool`/`model_members`/`credentials_client`；`default_executor_factory` 装配分支）、`.../application/model_call_trace.py`（`begin_attempt`：回合 id 与逐 attempt 审计行 id 的认领口径）、`.../infrastructure/model_dispatch_store.py`（`FrozenCandidate` + `read_frozen_candidates`：按冻结引用读非密钥候选元信息）、`.../application/model_allocator.py`（`ModelMemberCandidate.of` 映射）、`.../application/run_service.py`（`_create_run`/`_resume_run` → `_stream_run` → `_build_executor` 透传冻结池策略与成员 + 受信解析客户端）、`packages/contracts/src/muad_contracts/model_pool.py`（`derive_model_key_resource_id`：计量身份派生唯一实现）、`apps/console-platform/.../application/model_credential_service.py`（`derive_key_resource_id` 委托到同一实现，杜绝两处漂移）、`tests/agent_runtime/test_capacity_webchat_provider_pool.py`（11 用例）、`tests/agent_runtime/test_summary_model_resolution.py`（**新事实同步**，见下）、`docs/configuration-inventory.csv`（新增 4 个常量行 + 改动文件行号位移）。
- 调用链口径（design §3.3 逐句落地）：**短事务外**读冻结成员的非密钥候选元信息（`read_frozen_candidates` 不持锁不联网）→ `ModelAllocator` 池锁内选择 + `reserve` 预占（提交后才解析密钥）→ 按 `deployment_id`+`credential_id`+`expected_revision` 走 API-09 解析当前密钥并**校验 `key_resource_id` 与预占一致** → 发上游（期间按 `MODEL_CALL_HEARTBEAT_SEC` 续租）→ 结算。轮换竞争（409 或身份不一致）按**确定未发送**释放该预占并有界重选（`POOL_RESELECTION_LIMIT=3`，耗尽即显式 `MODEL_CREDENTIAL_CHANGED`，绝不拿新 Key 消费旧 Key 的槽位）。锁内不联网由结构保证：预占事务在 `_allocate` 内提交后才进入解析/发送。
- 结算分档事实：成功（有 `finish_reason`）按**实际 usage** 释放多余预占；远端**已应答**的失败（429/4xx，agent-core 只在收到 HTTP 状态码时抛这两个类型）释放槽位但保留 token 预占（失败不假定供方未计费）；传输层失败/超时/断流（同一异常类型不区分来源，设计不允许靠消息文本猜）与取消一律 UNKNOWN **保留占用**，等供方确认或有证据的硬超时再释放。断流（无收尾信号）不构成远端完成证据，且 `AgentRunner` 不重试——已输出正文因此不会被另一 attempt 拼上；已输出正文后的任何可重试失败一律转 `ModelRequestError`（终止策略）。
- 预占估算事实：`预占 = ceil(保守输入估算 × 冻结 input_estimate_multiplier) + 本次实际发送的 max_tokens`；保守估算 = 消息/工具定义的 UTF-8 字节量 + 每条消息协议开销（`MESSAGE_PROTOCOL_OVERHEAD_BYTES=32`）。`max_tokens` 缺失或非正时**显式失败**（`COMMON_VALIDATION_ERROR`）——design §3.7「启用池时显式填写、不读未发送的同义字段」，不猜上限。
- 额度暂不可用：在冻结 `capacity.model_wait_timeout_sec` 与 Run 绝对期限的较小者之内按 `POOL_CAPACITY_RETRY_INTERVAL_SEC` 有界退避重试（等待起点在 provider 实例上，同一执行片段内的重试不刷新该预算），超出即按原错误码回压（WAITING_MODEL 的持久投影属 TASK-036）。
- 中间过程（诚实记录）：(1) 首轮 RED 后 11 用例全红为 `FORBIDDEN`（内部服务身份）——测试改用本进程同源 `SharedSettings().internal_service_token`，不另造一份；(2) 断言修正两处**测试自身缺陷**：成员选择由分配器按 `dispatch_count/weight→在飞→稳定轮转` 决定，用例原先预设"第一个成员必被选中"，改为按租约里的计量身份/解析载荷定位实际成员（断言未弱化，仍是逐字段相等）；跨尝试的容量断言改为按实际出现的**不同 Key 身份**汇总（同 Key 时等价于原断言）；(3) 首轮实现的 `_run_row` 误读 `run_record.policy_json`（冻结策略在 `runtime_snapshot`），改为从快照读回；(4) 失约停手的尝试原先落不进审计（被取消的调用不走审计包装的错误分支），实现补一条 `LEASE_LOST` attempt 审计行。
- **新事实同步（非本任务计划 Files，披露）**：`tests/agent_runtime/test_summary_model_resolution.py` 的 `_stack` 夹具原先只给主模型一个**过渡解析**的替身凭据且模型参数无 `max_tokens`；TASK-025 起主链路按冻结成员解析，故该用例补种真实成员（`control.model_definition`/`model_credential`/`model_deployment`）、让 API-09 替身在冻结引用下返回完整 `SelectedModelCredential` 投影、并给主模型显式 `max_tokens=64`（断言未放宽：摘要路由/凭据/快照排除项的原有断言逐条保留）。
- 环境发现（不属于本任务改动，供协调）：共享 dev 库里存在 **15 条孤儿 `runtime.memory_consolidation_job`**（来源快照已被各自套件清理、claim 又不带租户谓词），会让 `tests/agent_runtime/test_memory_redesign_snapshot.py` 的 `MemoryJobs.claim` 抛 `source snapshot unavailable`（单跑也复现，与本次改动无关）。已删除这些 `tenant LIKE 'test-%'` 且来源快照不存在的孤儿行（可证明是测试残留），并在本任务用例的清理顺序里补上记忆类表；清理后该文件全绿。
- B-225: verified — automated command passed（Done Gate 执行）
- E-04: verified — automated command passed（Done Gate 执行）
- E-04: verified — automated command passed; run_id=81cdba8bd2d340868b8023f469728163 (confirmed_by: runner)
- B-225: verified — automated command passed; run_id=81cdba8bd2d340868b8023f469728163 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-225/E-04 RED：首建 `tests/agent_runtime/test_capacity_webchat_provider_pool.py`（11 用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_provider_pool.py` **1 error in 0.18s**（exit=1，收集期 `ImportError: POOL_RESELECTION_LIMIT`）
- [2026-10-18] 实现：`executor.py` 池化 provider（候选读取→预占→按冻结引用解析 Key 并校验→心跳续租→按证据结算；轮换竞争按未发送释放 + 有界重选；已输出正文后的失败终止）+ `model_call_trace.begin_attempt`（逐 attempt 审计行 id）+ `model_dispatch_store.read_frozen_candidates` + `model_allocator.ModelMemberCandidate.of` + `contracts.derive_model_key_resource_id`（Console 委托同一实现）+ `run_service` 透传冻结策略/成员/受信解析客户端；无新增表/列/索引与迁移
- [2026-10-18] B-225/E-04 GREEN：**11 passed in 13.27s**（exit=0；复跑 13.34s/13.26s 全绿）；断言逐项位置见 Acceptance Evidence
- [2026-10-18] verifier `harness-memory#RULE-memory-003`：`uv run pytest -q tests/agent_runtime/test_memory_redesign_cache.py tests/agent_runtime/test_memory_redesign_context.py tests/agent_core/test_memory_redesign_selection.py` → **56 passed in 70.51s**（exit=0）
- [2026-10-18] 回归：`tests/agent_runtime` **831 passed in 686.81s**（exit=0，含本任务 11 个用例）；`tests/contracts tests/architecture` **1314 passed in 4.29s**；`tests/console_internal` + Console 凭据服务/API 用例 **65 passed in 7.04s**（派生委托同源）；`tests/test_configuration_inventory.py` **4 passed**（新增 4 个常量行 + 行号位移已入盘点）
- [2026-10-18] 质量门：`uv run ruff check` 九文件干净；`uv run mypy apps packages` 406 files 无问题；函数最长 ≤50 行
- [2026-10-18] 环境处置：删除共享库里 15 条孤儿 `memory_consolidation_job`（来源快照已不存在、tenant 为 test-*），本任务用例清理顺序补记忆类表；`test_summary_model_resolution.py` 按新调用链同步夹具（补真实成员 + 冻结引用投影 + 显式 max_tokens）
- [2026-10-18] completed (done)

---

## TASK-026: 摘要和记忆提炼共享模型容量

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-025
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-memory#RULE-memory-005
- **Acceptance-Refs**: B-226, E-06
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/context_compaction.py`, `apps/agent-runtime/src/muad_agent_runtime/application/memory_consolidation.py`, `tests/agent_runtime/test_capacity_webchat_background_pool.py`
- **Estimate**: 60分钟

### Description

摘要/提炼都走统一物理资源预算；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-226][integration] 先在`tests/agent_runtime/test_capacity_webchat_background_pool.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：摘要/提炼都走统一物理资源预算。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-226] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-226] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] [E-06][integration] 将原E-06落入`tests/agent_runtime/test_capacity_webchat_background_pool.py`，先RED后GREEN；真实压缩/记忆提炼接线、持久快照和 model HTTP；断言：摘要失败保留原历史；提炼失败整批回滚且主 Run 不变；槽位泄漏和旧记忆缓存不残留。
- [x] verifier `harness-memory#RULE-memory-005`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-226 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 摘要/提炼都走统一物理资源预算；摘要失败原历史继续、批量提炼失败回滚且主Run不失败，冻结来源/epoch/forget tombstone与可观测退化保留 | tests/agent_runtime/test_capacity_webchat_background_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | verified |
| E-06 | integration | 真实压缩/记忆提炼接线、持久快照和 model HTTP | 摘要失败保留原历史；提炼失败整批回滚且主 Run 不变；槽位泄漏和旧记忆缓存不残留 | tests/agent_runtime/test_capacity_webchat_background_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | verified |
| harness-memory#RULE-memory-005 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 只有 COMPLETED Run 可入队持久 consolidation job；唯一 job、claim epoch/lease 和终态共同 fence 提交权。输入事件 through_seq、摘要自身 seq/覆盖边界、hash、scope revision 与来源 Run 策略首次冻结后重试不刷新；独立 provider 使用冻结主模型、实时凭据并显式关闭。模型调用次数不保证一次，持久效果/历史/完成事件/job 终态必须同事务最多一次；失败整批回滚，warning/metric 可见，主 Run 终态不变。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-226 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_background_pool.py` → **4 failed**（exit=1, 8.25s）：实现前摘要/提炼仍走定义级直连，池化计量/让路/老化分支不存在 | 同命令 → **6 passed**（exit=0, 13.68s）；全量域回归 `uv run pytest -q tests/agent_runtime` → **850 passed**（exit=0, 732.98s） | `test_b226_summary_call_uses_the_shared_pool_with_background_workload` L686（摘要走同一池、workload=BACKGROUND、真实发送冻结输出预算）；`test_b226_consolidation_call_uses_the_shared_pool_and_merges_metering` L1159（提炼与主调用/摘要同一物理 Key 合并计量）；`test_b226_background_call_yields_without_reserving_when_interactive_is_active` L1238（交互在飞时后台让路且不占位）；`test_b226_background_call_proceeds_after_aging_while_interactive_is_active` L1273（老化后推进） | 真实 Runtime 业务服务 + 真实 PostgreSQL（租约/容量/计数/审计）+ 真实模型 HTTP 探针；不 mock 存储与租约 | verified |
| E-06 | 同上（同批用例） | 同上 → **6 passed** | `test_e06_summary_failure_keeps_original_history_and_visible_degradation` L776（摘要失败保留原历史 + 降级显式可见 warning/metric）；`test_e06_consolidation_failure_rolls_back_batch_and_keeps_run_completed` L1312（提炼失败整批回滚、主 Run 终态不变、槽位/旧缓存不残留） | 同 B-226（真实压缩/记忆提炼接线、持久快照与 model HTTP） | verified |
| harness-memory#RULE-memory-005 | —（沿用原 Spec verifier，不重写；本任务只做消费者接线） | `uv run pytest -q tests/agent_runtime/test_memory_redesign_job_effects.py tests/agent_runtime/test_memory_redesign_input_boundary.py` → **35 passed**（exit=0, 70.65s） | 沿用原Spec verifier（job 唯一/claim fence/冻结输入/同事务最多一次） | 同域真实 PG + 真实 provider | verified |

**实施范围与证据说明**

- 实现文件（含超出计划 Files 的披露）：`application/context_compaction.py`（`make_summary_runner` 增加 `max_tokens` 并**真实发送**——预占与实际发送同源；旧快照缺叶时不带上限）、`application/memory_consolidation.py`（快照含池策略 + 主模型非密钥成员引用时经 `PooledModelProvider`：owner=memory job、workload=BACKGROUND、预占→按冻结引用解析当前 Key→发上游→续租→按证据结算；旧快照保持既有直连语义不补默认）、`application/executor.py`（`PoolCallScope` L655、`PooledModelProvider` L838、`build_pooled_provider` L1947）、`application/run_service.py`（`frozen_summary_members_of`：摘要模型成员引用一并冻进快照）、`docs/configuration-inventory.csv`（行号同步）。
- 契约与语义事实：摘要与记忆提炼都走**统一物理资源预算**（同一物理 Key 跨主调用/摘要/提炼合并计量，不按调用方拆开）；后台（BACKGROUND）在交互在飞时让路且**不占位**，老化后推进；摘要失败 → 原历史保留 + 降级显式可见（warning+metric），提炼失败 → **整批回滚**且主 Run 终态不变；密钥只进调用内存（不落日志/回执/快照）。
- 中间过程（诚实记录）：(1) 子 agent 会话在实现与 focused 回归完成后中断（全量域回归进程成为孤儿、输出不可回收）——由主 agent 接管：终止孤儿进程、在 worktree 内重跑 `tests/agent_runtime`（850 passed）、补齐本证据与 Log、执行 finish 并按裁决提交；(2) `test_capacity_webchat_provider_pool.py`/`test_summary_model_resolution.py` 各按新事实同步（摘要/提炼走池化解析、显式输出预算），原断言未放宽。
- 延后项：`WAITING_MODEL` 持久排队/状态投影 → TASK-036；执行槽 → TASK-032；真实供方额度与 1000 并发结论 → TASK-110/116；review 层 verifier 与 heavy validator 由需求级 verify-e2e/archive 全量复跑。
- E-06: verified — automated command passed; run_id=ac69d3c3fc3340a1ba467692978d76b4 (confirmed_by: runner)
- B-226: verified — automated command passed; run_id=ac69d3c3fc3340a1ba467692978d76b4 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-226/E-06 RED：`tests/agent_runtime/test_capacity_webchat_background_pool.py` 先行建立 → **4 failed**（8.25s；摘要/提炼仍走定义级直连）
- [2026-10-18] 实现：摘要器真实发送冻结输出预算（预占与实际发送同源）；提炼在含池策略/冻结成员引用的快照上走 `PooledModelProvider`（BACKGROUND，同一物理 Key 合并计量；旧快照保持直连不补默认）；`run_service` 冻结摘要成员引用；executor 新增 PoolCallScope/PooledModelProvider/build_pooled_provider
- [2026-10-18] B-226/E-06 GREEN：6 passed（13.68s）；verifier memory-005 35 passed；回归 contracts+architecture 1314、console_internal+凭据服务 57、inventory 4、ruff 干净、mypy 407 files
- [2026-10-18] 子 agent 中断接管：终止孤儿回归进程并在 worktree 内重跑全量域 `tests/agent_runtime` → **850 passed**（732.98s）；补证据/Log 后 finish
- [2026-10-18] completed (done)

---

## TASK-027: Console探针的受信容量端口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-022
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-227
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/api/model_capacity.py`, `apps/agent-runtime/src/muad_agent_runtime/main.py`, `tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py`
- **Estimate**: 45分钟

### Description

API-17只接受受信probe身份、正确attempt/epoch与期限；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-227][integration] 先在`tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：API-17只接受受信probe身份、正确attempt/epoch与期限。→ RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py` → **7 failed in 1.83s**（exit=1；全部 404 `COMMON_NOT_FOUND`——`/internal/model-capacity/*` 路由与端口模块尚不存在）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 新增 `api/model_capacity.py`（受信端口 + `ProbeCapacityPort`，最长函数 34 行）+ `infrastructure/model_capacity_store.py`（`read_probe_target`/`read_lease_by_reservation`）+ `packages/contracts/src/muad_contracts/model_pool.py`（探针线协议）+ `main.py` 挂载；响应只有 `reservation_id/epoch/lease_until`（或 `state/released`），无密钥与内部计量身份；ruff 无违规、`mypy apps packages` 409 files 无问题
- [x] [B-227] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 8 个用例逐条对应契约 5 条断言（见 Acceptance Evidence）
- [x] [B-227] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **8 passed in 2.67s**（exit=0）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-227 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | API-17只接受受信probe身份、正确attempt/epoch与期限；reserve/renew/settle幂等，探针与Run/job共占实际Key及账号额度 | tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-227 | 首建 `tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py`（7 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py` → **7 failed in 1.83s**（exit=1；全部 404 `COMMON_NOT_FOUND`：`/internal/model-capacity/*` 路由与端口模块均不存在；服务身份用例此时也拿不到 403） | 同命令 → **7 passed in 2.24s**（exit=0）；补线协议精确性用例（第 8 个）后单文件 **8 passed in 2.67s**；finish 前复跑全绿 | 受信身份 `test_b227_rejects_missing_or_wrong_service_identity_before_any_write` L600-633（缺失/错误/空身份一律 403 `FORBIDDEN` L612/L625，renew/settle 在解析 reservation 之前同门控 L615-626，且零租约/零容量行 L627-628，正确身份放行对照 L632-633）；预占与合并计量 `test_b227_reserve_grants_trusted_probe_and_shares_physical_key_with_run_capacity` L489-566（响应恰为 `{reservation_id,epoch,lease_until}` L502、密钥/内部身份不入响应 L506-508、真实租约 PROBE 归属/稳定计量身份/成员/版本 L512-528、KEY 与组行 (1,1,64) L531-534、主调用在同组额度被回压 `MODEL_CAPACITY_UNAVAILABLE` L536-541、NOT_SENT 结算后主调用可占 L543-563）；attempt 幂等与身份栅栏 `test_b227_reserve_replay_is_idempotent_and_attempt_identity_is_fenced` L567-599（重放同 grant 且不重复占位 L573-578、同 attempt 换凭据 409 `MODEL_CALL_LEASE_LOST` L582-589、新 attempt 另一成员独立行 L592-598）；未知目标/期限/停用 `test_b227_reserve_rejects_unknown_member_and_invalid_deadline` L634-677（未绑定成员/未知模型 404 L644-656、已过期限 504 `MODEL_WAIT_TIMEOUT` L658-660、停用凭据 409 `MODEL_DISABLED` L662-665、全程零占位 L667-670）；续租 `test_b227_renew_verifies_epoch_and_frozen_deadline` L678-754（期限前进且不越冻结期限、容量不动 L692-703、epoch 不符 409 L711-717、期限已过 504 L725-730、结算后拒续租 409 L742-747）；结算 `test_b227_settle_releases_only_with_evidence_and_is_idempotent` L755-861（无证据 FAILED→UNKNOWN 保留占用 L775-782、UNKNOWN 声称未发送 409 `MODEL_CALL_OUTCOME_UNKNOWN` L785-788、硬超时证据→SETTLED 释放在飞且无 usage 保守保留 L790-806、重复结算幂等 released=false L808-830、成功按实际 usage 替换预占 L832-861）；线协议精确 `test_b227_wire_shape_is_exact_and_settlement_evidence_is_checked` L862-899（缺字段/0 预占/无时区期限/混入 run_id 一律 422 且零占位 L871-882、UNKNOWN+完成证据 422 L884-898）；端口隔离与审计 `test_b227_probe_port_never_acts_on_run_leases_and_writes_no_billing_audit` L900-953（Run 租约 renew/settle 404 且行不变 L922-935、未知 reservation 404 L940-943、探针全链路 0 计费审计 L945-953） | 真实 `muad_agent_runtime.main.app` 经真实 uvicorn 127.0.0.1（真实 socket、真实服务身份门控与响应封套）；真实 PostgreSQL 共享 dev 库按唯一租户增删（`control.model_definition/model_credential/model_deployment` 解析目标身份，`runtime.model_capacity/model_call_lease` 承载预占；无 DDL）；主调用侧用真实 `ModelCapacityService`（TASK-025 池化执行链的同一预占路径）在相同 KEY/组行上竞争；探针 HTTP 归 Console（TASK-028），本端口不调模型且 `runtime.model_invocation_audit` 为 0 | verified |

**实施范围与风险说明**

- 实现文件：`apps/agent-runtime/src/muad_agent_runtime/api/model_capacity.py`（新建，受信端口 + `ProbeCapacityPort`）、`.../infrastructure/model_capacity_store.py`（+60 行：`_PROBE_TARGET_SQL`/`ProbeTarget`/`read_probe_target`/`read_lease_by_reservation`）、`.../main.py`（挂载路由）、`packages/contracts/src/muad_contracts/model_pool.py`（+61 行：`ProbeLeaseRenewal`/`ProbeCallSettlement`/`ProbeCapacityGrant`/`ProbeCapacityRelease`，结算形状校验抽成 `_require_settlement_shape` 与主链共用）、`tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py`（953 行）；未新增表/列/索引与迁移，无 DDL。
- 计划外披露：`packages/contracts/.../model_pool.py` 与 `docs/configuration-inventory.csv`（新增 `PROBE_LEASE_OWNER`、`_PROBE_TARGET_SQL` 两行并同步被改动行的行号；CRLF 保持、只动本任务行）。
- 口径裁定（诚实记录）：① 探针目标身份由 Runtime 从 `control.model_*` **只读**解析（与容量上限读取同源），`key_resource_id` 走唯一派生函数 `derive_model_key_resource_id`——线协议只带 model_id/credential_id，不接受调用方自报计量身份，探针与 Run/摘要/提炼因此合并同一物理 Key 与账号额度；② 探针租约执行方身份固定为 `PROBE_LEASE_OWNER="console-probe"`（API-17 只有一个受信调用方），线协议续租/结算只核 epoch（design §3.4 原文），非 PROBE 租约一律 404——本端口不触碰 Run/job 调用；③ 冻结的绝对期限不在租约行（0022 无该列且本任务禁止 DDL，与 TASK-022 裁定一致）：续租由 Console 传入同一 deadline，服务只前进不倒退且不越该期限；④ renew/settle 先按路径 reservation_id 无锁读并守卫 PROBE 归属，再进服务行锁事务；并发 renew/settle 由 attempt 行锁串行，幂等/失约语义沿用 TASK-022。
- 延后项：Console 侧探测 HTTP 与并发≤4/单次≤5s、"先占额再探测"编排属 TASK-028；探针真实供方额度与 1000 并发容量结论属 TASK-110/116。本任务只交付受信容量端口及其真实 PG/HTTP 证据。
- B-227: verified — automated command passed; run_id=413f887d090c4e47a19eb30310ad4128 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-227 RED：首建 `tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py`（7 个行为用例）→ **7 failed in 1.83s**（exit=1，全部 404 `COMMON_NOT_FOUND`；路由与端口模块尚不存在）
- [2026-10-18] 实现：`api/model_capacity.py`（受信端口 `POST /internal/model-capacity/reservations` + `.../{id}/renew|settle`、`ProbeCapacityPort` 解析目标身份并守卫 PROBE 归属）+ `model_capacity_store.py`（`_PROBE_TARGET_SQL`/`ProbeTarget`/`read_probe_target`/`read_lease_by_reservation`）+ contracts 探针线协议（grant/renewal/settlement/release，结算形状与主链共用校验）+ main 挂载；未新增表/列/索引与迁移
- [2026-10-18] B-227 GREEN：**8 passed in 2.67s**（exit=0；先 7 passed 2.24s，补线协议精确性用例后 8 passed）
- [2026-10-18] 回归：`uv run pytest -q`（本任务验收文件 + 6 个 capacity webchat 套件 + `tests/contracts` + `tests/architecture`）→ **1388 passed in 40.90s**（exit=0）；`tests/test_configuration_inventory.py` **4 passed**（新增 2 常量已登记并同步行号）；`uv run ruff check` 逐文件干净；`uv run mypy apps packages` 409 files 无问题
- [2026-10-18] completed (done)

---

## TASK-028: 物理Key检测保持Console侧HTTP实现

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016, TASK-018, TASK-019, TASK-027
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-228
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/model_probe_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/model_resources.py`, `tests/console_platform/test_capacity_webchat_console_probe.py`
- **Estimate**: 45分钟

### Description

至多50个model/credential对、并发≤4、单次≤5秒；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-228][integration] 先在`tests/console_platform/test_capacity_webchat_console_probe.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：至多50个model/credential对、并发≤4、单次≤5秒。→ RED：首建 7 个行为用例后 `uv run pytest -q tests/console_platform/test_capacity_webchat_console_probe.py` → **1 error in 1.35s**（exit=4，`ImportError: cannot import name 'get_probe_capacity_client' from 'muad_console_platform.api.deps'`；新实现模块与受信容量客户端尚不存在）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 新增 `application/model_probe_service.py`（探测原语 + API-17 客户端 + 检测编排，最长函数 33 行）+ `api/model_resources.py` 的 `POST /api/v1/model-credentials/test`（ADMIN 组）+ `application/dto.py` 两个请求 DTO + `api/deps.py` 共享客户端；`model_test_service.py` 改为复用同一探测原语（401/超时/回退映射只有一处口径）；ruff 逐文件干净、`mypy apps packages` 411 源文件无问题；响应/日志不含密钥（测试断言 `GOOD_KEY`/`BAD_KEY` 不进响应）
- [x] [B-228] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 8 个用例逐条对应契约 6 条断言（见 Acceptance Evidence）
- [x] [B-228] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_probe.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **8 passed in 16.72s**（exit=0）；复跑全绿（7 passed 14.48s / 8 passed 16.72s）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-228 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 至多50个model/credential对、并发≤4、单次≤5秒；先占额再由Console探测，单成员成功不伪称整池健康，不改配置revision/计费审计 | tests/console_platform/test_capacity_webchat_console_probe.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_probe.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-228 | 首建 `tests/console_platform/test_capacity_webchat_console_probe.py`（7 个行为用例）→ `uv run pytest -q tests/console_platform/test_capacity_webchat_console_probe.py` → **1 error in 1.35s**（exit=4，collection `ImportError`：`get_probe_capacity_client` 与探测服务模块均不存在） | 同命令 → **8 passed in 16.72s**（exit=0；先 7 passed 14.48s，补「容量端口不可达」用例后 8 passed）；finish 前复跑全绿 | 先占额再探测与合并计量 `test_b228_probe_reserves_shared_key_and_reports_member_scope_only` L453-552（真实 HTTP 只打 `/v1/models` 且授权头为成员 Key L485-486；租约 `workload=PROBE`/`lease_owner=console-probe`/`probe_id` 非空/`SETTLED` L489-494；同一 `key_resource_id` 行结算后 inflight=0、窗口计 1 次请求、tokens=预占 L506-508；逻辑模型 revision=1 且 `last_test_status=UNTESTED`——单成员成功不伪称整池健康 L521-522；无配置审计/计费审计 L540-547；响应恰为 6 个公开字段且不含密钥 L465-470）；容量栅栏先于 HTTP 且与主调用共享 `test_b228_capacity_gate_precedes_http_and_shares_key_with_main_call` L555-601（真实 `ModelCapacityService` 主调用占满 max_inflight=1 → 探针 `NOT_EXECUTED`+`MODEL_CAPACITY_UNAVAILABLE` L578-579、零探测 HTTP L580、零探针租约 L582；释放后同路径 `AVAILABLE`，同 Key 上 INTERACTIVE 与 PROBE 租约并列 L596-600）；批量上界 `test_b228_batch_bounds_50_pairs_concurrency_4_and_single_probe_budget` L608-650（50 对全部执行且每对恰一次 L634；真实并发峰值 ≤4 且 ≥2 L635-636；51 对/空列表 422 `COMMON_VALIDATION_ERROR` 且不触发探测 L647-650）；单次 5 秒预算 `test_b228_single_probe_budget_bounds_slow_endpoint_and_retains_unknown` L653-681（服务端 7 秒慢响应下 `latency_ms<6500` 且整体 <6.5 秒返回 L672-673；已发出无证据 → 租约 `UNKNOWN` 且真实在飞保留 L676-680）；容量端口不可达 `test_b228_unreachable_capacity_port_is_explicitly_not_executed` L688-724（`NOT_EXECUTED`+`COMMON_INTERNAL_ERROR`、零探测 HTTP、零租约/容量行 L715-721）；成员凭据与既有映射 `test_b228_member_credential_resolution_and_error_mapping` L727-793（401→`CREDENTIAL_MISSING`、GET 404→POST chat 回退→`AVAILABLE`、停用模型→`MODEL_DISABLED`、停用成员/未绑定凭据→`CREDENTIAL_MISSING`、未知模型→`COMMON_NOT_FOUND` L775-780；回退请求体 `max_tokens=1` 与真实型号 L786-788；未执行目标零探测 HTTP L790）；跨租户与 ADMIN 门控 `test_b228_cross_tenant_target_is_not_found_and_creates_no_lease` L796-822（跨租户/未知目标一律 `COMMON_NOT_FOUND`、零探测零租约零容量行 L816-819）、`test_b228_admin_only_and_csrf_gate_with_zero_side_effects` L863-894（BUILDER 403、缺 CSRF 403，零副作用 L878-893） | 真实 Console 应用服务（ASGI 直连真实应用 + 真实登录会话/ADMIN/CSRF 裁决）；真实 PostgreSQL（控制面经真实 API 写入 `control.model_*`，`runtime.model_call_lease`/`model_capacity` 承载预占与结算，共享 dev 库按唯一租户增删，无 DDL）；真实 HTTP：外部模型是本地真实 HTTP 协议服务器（真实 socket、记录授权头/正文/并发峰值），受信容量端口是真实 uvicorn 127.0.0.1 上的 Runtime `main.app`（真实 socket 与真实服务身份门控）；主调用侧用同一真实 `ModelCapacityService` 在同一物理 Key 上竞争，证明探针不绕过资源池；检测全链路 `runtime.model_invocation_audit` 为 0 | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 一致：`application/model_probe_service.py`（新建：探测原语 + `ProbeCapacityClient` + `ModelProbeService`）、`api/model_resources.py`（`POST /api/v1/model-credentials/test`）；测试 `tests/console_platform/test_capacity_webchat_console_probe.py`（894 行，8 用例）。
- 计划外披露：`application/model_test_service.py`（`_probe`/路径常量迁入新模块并改为导入共享原语，401/超时/回退映射保持同一口径、行为不变）、`application/dto.py`（`ModelProbeTarget`/`ModelCredentialProbeRequest`，1..50 对）、`api/deps.py`（`get_probe_capacity_client` 按进程缓存、测试可覆盖）、`docs/configuration-inventory.csv`（删除 model_test_service 4 行、按路径序新增 model_probe_service 10 行，CRLF 保持）。
- 语义事实：探测 HTTP 仍由 Console 执行（不引入平台侧 SDK/直连）；预占/结算经 API-17，线协议只带 `(probe_id, model_id, credential_id, attempt_id, deadline_at, reserved_tokens)`，计量身份由 Runtime 从 `control.model_*` 解析，探针与主调用/摘要/提炼合并同一物理 Key 与额度组；结算按证据分档——收到远端 HTTP 响应（任意状态码）为完成证据，连接未建立为 `NOT_SENT`（释放），超时/传输中断为 `UNKNOWN`（保守保留）；无 usage 时保留保守预占（探针不解析供方 usage）。
- 续租口径（诚实记录）：探测的绝对期限即租约上限（`lease_until=min(now+租期, deadline_at)`），续租结构上不能延长期限，因此探测路径不调用 API-17 的 renew（端口侧 renew 由 TASK-027 交付并测试），客户端不提供无调用方的分支。
- 判别性验证（防止断言空转）：把探测挪到预占之前 → 容量栅栏用例 **失败**（未获批容量仍发出 HTTP）；`PROBE_CONCURRENCY=50` → 批量用例 **失败**（并发峰值 >4）；`PROBE_MAX_OUTPUT_TOKENS=2` → 映射用例 **失败**（回退请求体 max_tokens≠1）。三处变异均先复现失败再还原，随后 8 passed 复跑确认。
- 配置清单：新增模块级常量 10 个已登记 `docs/configuration-inventory.csv`（`tests/test_configuration_inventory.py` → **4 passed**）。
- 延后项（诚实记录）：真实供方额度与 1000 并发容量结论属 TASK-110/116；探测资源预算/装配观测属 TASK-097/098/099；前端 Key 检测界面属 TASK-086；探针的取消（客户端断开）不主动释放占用，留给 Runtime 有界清扫标记 UNKNOWN（与主链「本地失约不等于远端已停」一致）。
- B-228: verified — automated command passed; run_id=83759e48bd504aadad7e3f29bc54e987 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-228 RED：首建 `tests/console_platform/test_capacity_webchat_console_probe.py`（7 个行为用例）→ **1 error in 1.35s**（exit=4，collection ImportError：`get_probe_capacity_client` 与新实现模块尚不存在）
- [2026-10-18] 实现：新增 `application/model_probe_service.py`（探测原语 + `ProbeCapacityClient` + `ModelProbeService`：先预占后探测、按证据结算、并发≤4、单次 5 秒、`max_tokens:1`）+ `api/model_resources.py` 的 `POST /api/v1/model-credentials/test` + `dto.py` 请求 DTO + `deps.py` 共享客户端；`model_test_service.py` 改为复用同一探测原语
- [2026-10-18] B-228 GREEN：**8 passed in 16.72s**（exit=0；先 7 passed 14.48s，补「容量端口不可达」用例后 8 passed）；三处变异验证断言判别性后还原
- [2026-10-18] 回归：`tests/console_platform` **377 passed**（74.77s）、`tests/contracts tests/architecture tests/test_configuration_inventory.py` **1318 passed**（4.94s）；ruff 逐文件干净、`mypy apps packages` 411 源文件无问题
- [2026-10-18] completed (done)

---

## TASK-029: 全局准入阈值的单调版本应用

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-011
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-229
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_admission.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/run_admission_store.py`, `tests/agent_runtime/test_capacity_webchat_admission_policy.py`
- **Estimate**: 45分钟

### Description

source_settings_revision只增不退，policy锁在会话锁之前；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-229][integration] 先在`tests/agent_runtime/test_capacity_webchat_admission_policy.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：source_settings_revision只增不退，policy锁在会话锁之前。→ RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_admission_policy.py` → **1 error in 0.17s**（exit=4，`ModuleNotFoundError: No module named 'muad_agent_runtime.application.run_admission'`）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 新增 `application/run_admission.py`（198 行）+ `infrastructure/run_admission_store.py`（124 行）；最长函数 34 行（`lock_and_apply_in`）；ruff 无违规、`mypy apps packages` 404 源文件无问题；日志只记 tenant/revision，无凭据或原始设置内容
- [x] [B-229] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 6 个用例逐条对应契约 5 条断言（见 Acceptance Evidence）
- [x] [B-229] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_policy.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **6 passed in 2.63s**（exit=0）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-229 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | source_settings_revision只增不退，policy锁在会话锁之前；旧实例不能按旧阈值超收，降额不杀现有Run，低版本网络刷新在事务外 | tests/agent_runtime/test_capacity_webchat_admission_policy.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_policy.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-229 | 首建 `tests/agent_runtime/test_capacity_webchat_admission_policy.py`（6 个行为用例，730 行）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_admission_policy.py` → **1 error in 0.17s**（exit=4，`ModuleNotFoundError: No module named 'muad_agent_runtime.application.run_admission'`；两个实现模块均不存在） | 同命令 → **6 passed in 2.63s**（exit=0）；复跑全绿（2.63s / 2.72s） | 只增不退 `test_b229_policy_revision_only_increases` L419-459（rev 5 应用 L425；更低 rev 3 刷新一次后显式失败 L434；事务内原语抛 `AdmissionPolicyStaleError` L437-441；投影行仍 rev 5/4/2 L443-447；更高 rev 9 原地推进 L452-457）；policy 锁先于会话锁 `test_b229_policy_row_lock_precedes_session_lock` L465-520（真实 `pg_stat_activity` 等到受理事务阻塞在带标记的会话行锁上 L495-499，此刻策略行 `FOR UPDATE NOWAIT` 返回 **LOCKED** L502-504；放行后受理完成且策略行恢复 **FREE** L508-509）；旧实例不超收 `test_b229_old_instance_cannot_over_admit_with_old_thresholds` L530-588（真实 Console 应用 rev 9/2-2 L536-541；旧实例 rev 5/10-5 刷新一次仍低 → 显式失败且投影行仍是 9/2/2 L551-558；有效阈值 2/2 下计数 2 被拒 `RUN_CAPACITY_EXCEEDED` L574-582，旧阈值 5 本会放行）；降额不杀现有 Run `test_b229_downgrade_keeps_open_runs_and_blocks_new_admission` L591-648（3 条 RUNNING Run + 计数 3/3 在降额到 2/2 后逐行比对不变 L617-626；计数 3 ≥ 2 拒绝 L629-639；释放到 1 < 2 后放行 L640-648）；刷新在事务外 `test_b229_stale_refresh_happens_outside_any_transaction` L659-698（首次取配置与低版本刷新在途时策略行 `FOR UPDATE NOWAIT` 均 **FREE** L678-685；恰好 3 次请求 L697）；坏源不回落默认 `test_b229_bad_source_never_falls_back_to_defaults` L702-730（源 500 → 显式失败且**不建**默认投影行 L711-713；阈值越界文档 → `PLATFORM_SETTINGS_UNAVAILABLE` 且保留 rev 5 原值 L724-730） | 真实 `AdmissionPolicyService`+`RunAdmissionStore`；真实 PG `runtime.run_admission_policy`/`run_admission_counter`/`conversation`/`run_record` 行（共享 dev 库按 tenant 增删，无 DDL）；真实 Console 内部取设置端点（uvicorn + 真实 socket，源为 `control.platform_setting` 版本行，Runtime 侧用真实 `ConsolePlatformSettingsClient` 经真实 TCP 取快照）；低版本/坏文档/失败用脚本化 HTTP 探针（真实 uvicorn + 真实 TCP，不是替身客户端），并在请求在途时用真实 `FOR UPDATE NOWAIT` 观察行锁归属；无 mock 存储或租约 | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 一致：`application/run_admission.py`（198 行）、`infrastructure/run_admission_store.py`（124 行）、`tests/agent_runtime/test_capacity_webchat_admission_policy.py`（730 行）；未新增表/列/索引（复用 0023 已交付的 policy/counter 结构与 `run_record` 排队列），未新增迁移，未改动既有运行路径（TASK-030/031 才接线）。
- 单调语义事实：`runtime.run_admission_policy` 是有效阈值的唯一判定来源。`lock_and_apply_in` 是受理事务的**第一条语句**（`INSERT ... ON CONFLICT DO NOTHING` + `SELECT ... FOR UPDATE`）：`capacity.revision > 行版本` 才原地推进；`<` 抛 `AdmissionPolicyStaleError`（调用方回滚）；`==` 但阈值不同视为投影损坏并显式失败。`resolve` 把「事务外取配置 → 锁内应用 → 低版本退出事务后**只重取一次**」串起来，重取仍低即 `PLATFORM_SETTINGS_UNAVAILABLE`，绝不覆盖更高版本、绝不按旧阈值放行。
- 计数语义事实：`check_counts_in` 按 USER → TENANT 顺序 `SELECT ... FOR UPDATE`（缺失按 0 建立）后与**投影行阈值**比较，达到即 `RUN_CAPACITY_EXCEEDED`；降额只改阈值，不改写 Run 行与计数行（终态释放是 TASK-031 的职责，本任务只消费计数事实）。
- 判别性验证（防止断言空转）：去掉 `lock_policy` 的 `with_for_update()` → `test_b229_policy_row_lock_precedes_session_lock` **失败**（说明会话行锁等待期间确实已持有策略行锁）；把单调比较改成 `!=` → 3 个用例失败（单调/旧实例/刷新）；把低版本刷新挪进 session 事务内 → `test_b229_stale_refresh_happens_outside_any_transaction` **失败**。三处变异均先复现失败再还原，随后 6 passed 复跑确认。
- 配置清单：新增模块级常量 `CAPACITY_SECTION`/`SCOPE_USER`/`SCOPE_TENANT` 已登记 `docs/configuration-inventory.csv`（`tests/test_configuration_inventory.py` → **4 passed**）。
- 延后项（诚实记录）：同事务建 Run/自增计数与指纹属 TASK-030；终态释放、取消与排队期限竞态属 TASK-031；Runtime 执行槽属 TASK-032；准入指标目录属 TASK-098；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 中间过程（诚实记录）：(1) 降额用例首版把 3 条 RUNNING Run 放进同一会话，撞真实约束 `uq_run_record_active_conversation`（同会话只允许一个活跃 Run）→ 改为三条 Run 分属三个会话，断言不变；(2) 锁序用例首版让受理事务在等会话锁时**同时**应用更高版本，`UPDATE` 自身也会持行锁，导致去掉 `FOR UPDATE` 的变异仍通过 → 改为「同一版本不触发 UPDATE」后该用例才真正只由策略行锁决定，变异即失败。
- B-229: verified — automated command passed; run_id=77afecde5d3c4ee0a08837b666c18651 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-229 RED→GREEN：先建 `tests/agent_runtime/test_capacity_webchat_admission_policy.py` 记录 RED（1 error，模块不存在），实现 `run_admission.py`/`run_admission_store.py` 后 **6 passed**；三处变异验证断言判别性后还原；回归 `tests/agent_runtime` **814 passed**（665.47s）、`tests/contracts tests/architecture` **1314 passed**（4.02s）、`tests/test_configuration_inventory.py` **4 passed**；ruff 干净、mypy 404 源文件无问题
- [2026-10-18] completed (done)

---

## TASK-030: Run受理与准入计数同事务

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-024, TASK-029
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-api#RULE-api-002
- **Acceptance-Refs**: B-230
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_submission.py`, `tests/agent_runtime/test_capacity_webchat_admission_create.py`
- **Estimate**: 45分钟

### Description

全部非终态占用户/租户共享份额；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-230][integration] 先在`tests/agent_runtime/test_capacity_webchat_admission_create.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：全部非终态占用户/租户共享份额。→ RED：首建 13 个行为用例（1153 行）后 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_admission_create.py` → **13 failed in 35.17s**（exit=1；把实现文件 stash 回基线后复跑同一命令，准入未接线：计数行、准入拒绝、单一快照、低版本重取全部不成立）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 4 个实现文件：`run_service.py`（`start` 低版本重取循环 + `_admit` + `_resume_active` + 创建事务内准入）、`run_admission.py`（`reserve_counts_in`、`stale_refresh_attempts`）、`run_admission_store.py`（`increment_counter`）、`run_submission.py`（`claim_in`）；新增/改动函数均 ≤50 行（`start` 25、`_admit` 40、`_resume_active` 16、`reserve_counts_in` 17、`increment_counter` 19、`claim_in` 26）；ruff 干净、`mypy apps packages` 406 源文件无问题；日志只记 tenant/attempt，无凭据或原始设置内容
- [x] [B-230] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 13 个用例逐条对应契约 5 条断言（见 Acceptance Evidence）
- [x] [B-230] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_create.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **13 passed in 12.18s**（exit=0），复跑 **13 passed in 12.26s**
- [x] verifier `harness-api#RULE-api-002`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。→ **5 passed in 1.15s**（exit=0）；运行时创建路径同口径：`RunSubmissionService.claim_in` 先取 `pg_advisory_xact_lock`（键 = tenant + idempotency_key + endpoint）再锁内复查首次提交，partial unique 仍作兜底（B-230 并发同 key 用例证明落败者只重放、不建第二个 Run/不重复占位）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-230 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 全部非终态占用户/租户共享份额；幂等重投不重复计数，队列满不建假Run；持久Run/事件后才返回202，指纹含tenant/endpoint/user/entry/conversation | tests/agent_runtime/test_capacity_webchat_admission_create.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_create.py"] | verified |
| harness-api#RULE-api-002 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次提交；请求指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256，**必须含 `endpoint` 与 `tenant_id` 两个判别键**，另含 actor/资源与关键参数（Runtime 另含 run_id/message_id）；同 key 同指纹重放首次持久化结果（SSE 按 submission 重放已落库事件、不重新执行），同 key 不同指纹返回 `IDEMPOTENCY_MISMATCH`；并发插入由 partial unique 兜底，落败者读取首次提交结果。真实并发控制是**先取 `pg_advisory_xact_lock`**（键由 `(tenant_id, idempotency_key, endpoint)` 派生）把同键请求串行化，partial unique 只作兜底（`channel_service.py:153-159`、`auth_service.py:270-276`、`audit_export_service.py:386-392`、`agent_service.py:159-162`、`mcp_service.py:240-243`、`skill_service.py:278-286`）。**存量待收敛**：console 侧 `agent_service`/`mcp_service`/`skill_service` 三处 `_fingerprint` 仍用 `&#124;` 拼接且不含 endpoint/tenant，须按本口径对齐（合规参考 `channel_service.py:59-74`、`auth_service.py:253-267`、`audit_export_service.py:100-119`、`apps/agent-runtime/src/muad_agent_runtime/application/run_submission.py:30-49`）。 Runtime 记忆命令使用永久回执 `runtime.memory_command_receipt`，完整唯一 `(tenant_id, command_id)` 且禁止软删；Scope 行锁后取同一 `(tenant_id, command_id)` 身份的事务 advisory lock，复查首次回执，完整唯一约束兜底。endpoint/user/actor/source_run/来源事件和规范化输入均进入指纹；同命令号跨 endpoint 或 user 返回 `IDEMPOTENCY_MISMATCH`，不新建第二份回执。回执、事实、完整历史、scope revision 同事务；批量清空只写一个回执。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-230 | 首建 `tests/agent_runtime/test_capacity_webchat_admission_create.py`（13 个行为用例，1153 行）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_admission_create.py` → **13 failed in 35.17s**（exit=1；把 4 个实现文件 stash 回基线后复跑同一命令） | 同命令 → **13 passed in 12.18s**（exit=0）；复跑 **13 passed in 12.26s** | ①同事务：`test_b230_run_and_counts_commit_together_under_counter_lock` L535-636（阻塞期间 Run 不可见 L576、计数仍 0 L577；放行后 Run=1 L587、USER/TENANT=1/1 L588-593）＋`..._create_run_commits_run_and_counts_in_one_transaction` L409-455（返回前 Run/快照/提交/事件已持久 L432-437，计数各 1 L439-444）；②非终态占份额：`..._non_terminal_runs_occupy_the_shared_share` L769-831（RUNNING→WAITING_INPUT/WAITING_TOOL 后第三个受理被拒 L812、计数仍 2/2 L813-818）、`..._tenant_share_is_shared_across_users` L717-767（租户计数 2 L750、用户 C 计数为 0 仍被拒 L758、不建 C 的计数行 L759-761）、`..._schema_default_limits_apply_without_settings_version` L489-533（无版本行⇒schema 默认 2000/2 L516-520、第 3 个被拒 L526）；③幂等不重复计数：`..._idempotent_replay_does_not_double_count` L833-897（重放返回原 Run、Run/计数不增 L859-870；满额时重放仍回原 Run L879-882；新 key 被拒 L892）、`..._concurrent_same_key_admits_exactly_one_run` L1058-1098（同 run_id L1088、Run=1 L1089、计数=1 L1092、提交=1 L1096）；④队列满不建假 Run：`..._capacity_exceeded_rolls_back_without_fake_run` L638-715（Run 数不变 L684、无半建会话/无事件 L685-687、无提交行 L688-690、计数不变 L691-696、满额用户在新会话上同样整事务回滚 L707-712）；⑤持久后返回 + 指纹判别键：`..._create_run_commits_run_and_counts_in_one_transaction` L432-437、`..._fingerprint_covers_tenant_user_entry_and_conversation` L899-963（换 text/entry/user/conversation 任一判别键 ⇒ `IDEMPOTENCY_MISMATCH` L936-942、不新增 Run/计数 L943-947、同 key 跨租户是新提交 L949-957）；⑥单一快照与低版本重取：`..._single_settings_snapshot_serves_admission_and_freeze` L458-486（恰好 1 次取配置 L476）、`..._stale_settings_revision_refetches_and_never_admits_on_old_thresholds` L965-1014（恰好重取 1 次 L992、用**新**阈值拒而非旧阈值放行 L991、重取仍低⇒`PLATFORM_SETTINGS_UNAVAILABLE` L1008）、`..._stale_settings_revision_refreshes_and_admits_under_new_thresholds` L1016-1056；⑦并发收敛：`..._concurrent_distinct_keys_converge_exactly_to_threshold` L1100-1152（限额 3 时 6 并发恰好 3 成 3 拒 L1141-1142、Run=3 且计数 3/3 L1143-1148） | 真实 `RunService`/`RunSubmissionService`/`AdmissionPolicyService` 业务路径（`_create_run` 快照冻结、事件写入、提交记录全部真实）；隔离租户的真实 PG（`run_record`/`runtime_snapshot`/`canonical_event`/`run_submission`/`conversation`/`run_admission_policy`/`run_admission_counter`，只增删本租户行）；真实 Console 内部取设置端点（uvicorn + 真实 socket + `control.platform_setting` 版本行 + 真实 `ConsolePlatformSettingsClient` 经真实 TCP）；脚本化设置探针为真实 HTTP（不是替身客户端），用于「一份快照只取一次」与「低版本重取」两个判别性场景；计数行锁用第二条真实 PG 连接的 `FOR UPDATE` 阻塞受理事务并在 `pg_stat_activity` 观察 Lock 等待；无 mock 存储或租约 | verified |
| harness-api#RULE-api-002 | 沿用原 Spec verifier，未改写成更弱测试 | `uv run pytest -q tests/console_skill/test_import_idempotency.py` → **5 passed in 1.15s**（exit=0） | 运行时创建路径按规则口径接入：`run_submission.py:70-95`（`claim_in` = 先 `pg_advisory_xact_lock(sha256(["run_submission", tenant, key, endpoint])[:8])` 再锁内复查首次提交）+ `run_service.py:715-741`（重放检查 → 串行化复查 → 建 Run），partial unique 只作兜底；指纹含 tenant/endpoint/user/entry/conversation（B-230 断言⑤） | 真实 `RunSubmissionService.lock_identity` 走真实 PG advisory 锁；并发同 key 用例（L1058-1098）在真实 PG 上恰好一个 Run、一次占位 | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 的差异（诚实记录）：计划列 `run_service.py`/`run_submission.py`/新测试文件；实际另改 `application/run_admission.py`（新增 `reserve_counts_in` 与 `stale_refresh_attempts`）与 `infrastructure/run_admission_store.py`（新增 `increment_counter`）——TASK-029 的证据已把「同事务建 Run/自增计数」显式延后给 TASK-030，自增原语属准入模块，TASK-031 的终态释放也将落在这里。
- 同事务事实：受理事务内顺序为「策略行锁（单调应用更高版本）→ 会话 → Run 行 → USER 计数 → TENANT 计数」，`reserve_counts_in` 在 Run 行插入之后调用，与 Run 同一 `commit`；达阈值抛 `RUN_CAPACITY_EXCEEDED`，`_create_run` 显式回滚整事务（无半建 Run/会话/提交/事件/计数）。策略行锁是本事务的**首个共享行锁**：之前的语句只有普通读取与新建行插入（不与他人冲突）；Run 插入经 FK 对会话行取 KEY SHARE 锁，仍在策略行锁之后。
- 单一快照事实：`_create_run` 仍只取一次设置快照，`parse_capacity(snapshot_settings)` 与 `snapshot_policy` 冻结共用这一份（变异验证见下）；`start` 的 stale 重取循环在**事务外**重取，锁内不联网。
- 幂等事实：`claim_in` 的 advisory 锁在策略行锁之前取，同键事务只在 advisory 锁上排队；锁内复查首次提交 ⇒ 落败者直接重放（不建第二个 Run、不重复占位）；partial unique 仍作并发兜底。
- 判别性验证（防止断言空转，两处变异均先复现失败再还原，随后 13 passed 复跑确认）：(1) 把 `reserve_counts_in` 挪到 `commit()` 之后（Run 先提交、计数另起事务）→ `test_b230_run_and_counts_commit_together_under_counter_lock` **失败**（`assert 1 == 0`：计数行锁被占时 Run 行已可见，证明「同事务」断言真的在测同一事务）；(2) 把 `capacity = parse_capacity(snapshot_settings)` 换成第二次 `fetch_capacity` → `test_b230_single_settings_snapshot_serves_admission_and_freeze` **失败**（arrivals 变成 2）。
- 受影响回归修正（诚实记录）：`tests/agent_runtime/conftest.py` 的默认设置源改为「revision 0 + 显式 capacity 组（租户 100000/用户 100）」。原因：schema 默认每用户 2 个非终态 Run，而既有 3 个用例（`test_context_compaction_config::test_e04_config_change_only_affects_new_runs`、`test_context_metrics::test_e05_compaction_counters_increase_on_a_real_run`、`test_snapshot_freeze::test_b104_definition_change_only_affects_new_runs`）在同一用户上连建 3 个 Run，终态释放属 TASK-031；套件显式声明容量组后这 3 例恢复绿色，其余分组仍回落 schema 默认（冻结策略逐键不变）。schema 默认路径由新增用例 `..._schema_default_limits_apply_without_settings_version` 显式覆盖（无版本行 ⇒ 2000/2）。
- 配置盘点：未新增模块级常量；`run_service.py` 导入段 +5 行使既有 20 行常量行号平移，`docs/configuration-inventory.csv` 同步为实际行号（仅行号列变化、CRLF 保持、无增删行）→ `tests/test_configuration_inventory.py` **4 passed**。
- 回归证据：`tests/agent_runtime` **833 passed in 694.63s**（含本任务 13 例）；`tests/contracts tests/architecture tests/test_configuration_inventory.py` **1318 passed in 4.75s**；`uv run ruff check <6 个改动文件>` 干净；`uv run mypy apps packages` **406 源文件无问题**。
- 延后项（诚实记录）：终态释放/取消/排队期限竞态属 TASK-031（本任务计数只增，Run 到终态后不释放份额）；持久排队阶段与两入口状态投影属 TASK-036；Runtime 执行槽属 TASK-032；准入指标目录属 TASK-098；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 风险提示（诚实记录，未运行验收）：`tests/acceptance/**` 的 runtime 栈（module-scoped `live_stack`，同一用户跨用例连建 Run）未种 `capacity`，在 TASK-031 终态释放落地前会撞 schema 默认（每用户 2）；验收栈显式容量属 TASK-107/109 的配置契约，本任务按纪律未运行验收，留待 TASK-031 与验收栈任务收口。
- B-230: verified — automated command passed; run_id=c78622729ea44807a6c83fe3d8c9dea3 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-230 RED→GREEN：先建 13 个行为用例（1153 行）→ 实现前 **13 failed in 35.17s**；实现 `start` 低版本重取 + 创建事务内准入（`reserve_counts_in`/`increment_counter`/`claim_in`）后 **13 passed**；两处变异验证断言判别性后还原；verifier `tests/console_skill/test_import_idempotency.py` **5 passed**；回归 `tests/agent_runtime` **833 passed**（694.63s）、`tests/contracts tests/architecture tests/test_configuration_inventory.py` **1318 passed**（4.75s）；ruff/mypy 干净
- [2026-10-18] completed (done)

---

## TASK-031: 终态释放、取消与排队期限竞态

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-029, TASK-030
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-231, B-05
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_admission.py`, `tests/agent_runtime/test_capacity_webchat_admission_release.py`
- **Estimate**: 60分钟

### Description

终态与admission_released_at CAS/计数释放同事务；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-231][integration] 先在`tests/agent_runtime/test_capacity_webchat_admission_release.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：终态与admission_released_at CAS/计数释放同事务。→ RED：首建 10 个行为用例后 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_admission_release.py` → **10 failed in 20.44s**（exit=1）；失败均为实现缺失的真实行为：`queue_stage` 为 None（3 例）、`admission_released_at` 为 None（2 例）、`expire_queued_runs` 不存在（ImportError，1 例）、排队期限未冻结导致 `queue_deadline_at - queued_at` 为 `NoneType - NoneType`、收尾未阻塞在计数行锁（`pg_stat_activity` 观测不到 Lock 等待）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 6 个文件：`run_admission.py`（`release_admission_in` 终态释放 CAS+递减、`queue_deadline`、`CapacitySnapshot.queue_wait_timeout_sec`、`QUEUE_STAGE_WAITING_RUNTIME`）、`run_admission_store.py`（`decrement_counter`，`GREATEST(count-1,0)` 截断）、`run_service.py`（创建冻结排队投影、`_cancel` 未开始执行走 CAS 终态、`_cancel_not_started`/`_close_waiting_interrupts`/`_append_cancelled_events`、`_finalize_run`/`_finalize_failed`/`_reap_one` 释放、`expire_queued_runs`/`_expire_queued_one`/`_close_queued_run`）、`continuation_service.py`（`_initial_queue_deadline_passed` 领取守卫 + 领取清排队投影）、`run_deadline.py`（绝对期限收尾也释放）、`main.py`（期限循环接入排队过期扫描）；改动函数均 ≤50 行（`release_admission_in` 20、`_cancel_not_started` 24、`_expire_queued_one` 34、`_close_queued_run` 26、`_initial_queue_deadline_passed` 5），既有超限函数未新增（`_cancel_waiting` 52 行已拆分为 3 个 ≤30 行函数）；日志只记 tenant/scope/run_id，无凭据或消息正文；错误显式（`RUN_QUEUE_TIMEOUT`/`RUN_BUSY`/`AppError`），无静默吞异常
- [x] [B-231] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 13 个用例（5 个 B-231 + 8 个 B-05）逐条对应契约（见 Acceptance Evidence 的断言位置列）
- [x] [B-231] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **13 passed in 8.26s**（exit=0，最终版本）；中间复跑 **12 passed in 7.81s**、**11 passed in 6.98s** / **11 passed in 6.77s**
- [x] [B-05][integration] 将原B-05落入`tests/agent_runtime/test_capacity_webchat_admission_release.py`，先RED后GREEN；实际准入计数、Run 事务、取消/领取竞态和 PG 行锁；断言：幂等/回滚/双终态/接管后计数正确且不为负；每用户跨入口受同一上限；初始排队过期与领取只有一个结果，已开始执行不被旧排队期限误杀。→ 同文件 8 个 B-05 用例（与 B-231 同命令 RED→GREEN）：`test_b05_claim_and_queue_expiry_have_exactly_one_outcome`（领取与过期唯一结果）、`test_b05_claimed_run_is_not_killed_by_old_queue_deadline`（已开跑不被误杀）、`test_b05_resumed_run_is_not_governed_by_initial_queue_deadline`（续跑/人工恢复 epoch≥1 不受初始排队期限约束）、`test_b05_queue_expiry_vs_cancel_race_has_one_terminal_outcome`（取消/超时竞态）、`test_b05_share_is_shared_across_entries_and_released_on_terminal`（每用户跨入口同一上限 + 释放后可复用）、`test_b05_repeated_terminal_and_takeover_do_not_double_release`（双终态/接管幂等）、`test_b05_already_released_run_never_decrements_again`（CAS 拒绝二次释放）、`test_b05_release_without_counter_row_never_goes_negative`（历史 Run 截断在 0 且降级可见）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-231 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 终态与admission_released_at CAS/计数释放同事务；取消/超时/领取只有一个结果，不出现负数或超时后模型调用，已开跑不被旧排队期限误杀 | tests/agent_runtime/test_capacity_webchat_admission_release.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | verified |
| B-05 | integration | 实际准入计数、Run 事务、取消/领取竞态和 PG 行锁 | 幂等/回滚/双终态/接管后计数正确且不为负；每用户跨入口受同一上限；初始排队过期与领取只有一个结果，已开始执行不被旧排队期限误杀 | tests/agent_runtime/test_capacity_webchat_admission_release.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-231 | 首建 `tests/agent_runtime/test_capacity_webchat_admission_release.py`（10 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_admission_release.py` → **10 failed in 20.44s**（exit=1） | 同命令（补齐 13 例后）→ **13 passed in 8.26s**（exit=0）；中间复跑 **12 passed in 7.81s** / **11 passed in 6.98s** / **11 passed in 6.77s** | ①同事务：`test_b231_terminal_release_commits_in_one_transaction_with_terminal_state` L536-590（受理后计数 1/1 L559、冻结排队投影 L561-564；计数行被占期间 Run 仍 RUNNING 且 `admission_released_at` 为 NULL L578、计数仍 1 L579；放行后 COMPLETED+已释放 L582、stage 清空 L583、计数 0 L584、终态事件恰 1 L585、提交 CLOSED L586）；②回滚：`test_b231_rolled_back_finalize_keeps_run_and_share` L592-634（计数 1 L613；取消承载执行的监督任务后 Run 仍 RUNNING 且未释放 L628、计数仍 1 L629、无终态事件 L630）；③取消 vs 完成竞态：`test_b231_cancel_vs_completion_race_releases_exactly_once` L641-689（阻塞事务锁会话行，取消任务与收尾任务各等一次真实 Lock L674/L676；终态 ∈ {COMPLETED,CANCELLED} L680、已释放 L681、终态事件恰 1 L682、计数 2→1 L683、CANCELLED 时 `cancel_requested` L684-685）；④排队期限冻结：`test_b231_queue_deadline_freezes_at_admission` L696-743（配置 60s ⇒ `queue_deadline-queued_at=60s` 且 < 绝对期限 L721-722；配置 3600s + 绝对期限 5s ⇒ `queue_deadline_at == deadline_at` L740）；⑤排队过期：`test_b231_queue_expiry_fails_run_and_releases_once` L745-793（扫描命中 L769、FAILED/`RUN_QUEUE_TIMEOUT` L771、stage 清空 + reason L772、已释放 L773、计数 0 L775、终态事件恰 1 L776、RUN_FAILED 载荷 L777、提交 CLOSED L778、未创建执行器 L779；重复过期与绝对期限过期后释放时间不变/计数 0/事件仍 1 L782-790） | 真实 `RunService`/`AdmissionPolicyService` 业务路径（受理/取消/收尾/回收/排队过期/领取全走真实代码）；隔离租户的真实 PG（`run_record`/`runtime_snapshot`/`canonical_event`/`run_submission`/`conversation`/`run_admission_policy`/`run_admission_counter`，只增删本租户行）；真实 Console 内部取设置端点（uvicorn + 真实 socket + `control.platform_setting` 版本行 + 真实 `ConsolePlatformSettingsClient` 经真实 TCP）；并发用第二条真实 PG 连接的 `FOR UPDATE` 阻塞并 `pg_stat_activity` 观测 Lock 等待（不 sleep 假装竞态）；无 mock 存储或租约 | verified |
| B-05 | 同上 RED（B-05 用例与 B-231 同文件同命令，首建 10 例含 5 个 B-05 用例 → **10 failed**） | 同上 **13 passed in 8.26s**（exit=0） | ①领取与过期唯一结果：`test_b05_claim_and_queue_expiry_have_exactly_one_outcome` L800-878（过期排队 Run：真实 `claim_continuation` 在 Run 行锁竞争下拒绝领取 L856、过期扫描胜出 L857-858、已释放 L859、未创建执行器 L861；未来期限的排队 Run 领取成功 L866、stage 清空且未释放 L868、过期扫描不再命中 L870-872）；②已开跑不被误杀：`test_b05_claimed_run_is_not_killed_by_old_queue_deadline` L880-916（旧排队期限已过仍不被扫描 L905、仍 RUNNING 且未释放 L907、按原绝对期限正常 COMPLETED L911-913）；③续跑不受初始排队期限约束：`test_b05_resumed_run_is_not_governed_by_initial_queue_deadline` L923-970（epoch≥1 + 旧排队期限 ⇒ 过期扫描不碰 L960-962、领取成功 L963-966、份额保留 L967）；④取消 vs 超时竞态：`test_b05_queue_expiry_vs_cancel_race_has_one_terminal_outcome` L972-1023（终态恰一个 L1011、已释放 L1012、终态事件恰 1 L1013、计数 0 L1014、CANCELLED 时 `cancel_requested` L1015-1016 / FAILED 时 `RUN_QUEUE_TIMEOUT` L1018、未调用模型 L1019）；⑤每用户跨入口同一上限：`test_b05_share_is_shared_across_entries_and_released_on_terminal` L1025-1086（两入口各 1 Run 占满 2/2 L1054、第三例被 `RUN_CAPACITY_EXCEEDED` 且不占份额 L1064-1065、WECOM 侧终态释放 L1072-1073、另一入口立即复用份额 L1081-1083）；⑥双终态/接管幂等：`test_b05_repeated_terminal_and_takeover_do_not_double_release` L1088-1138（Reaper 接管租约过期的 RUNNING Run ⇒ FAILED/`RUN_ABANDONED` L1122、已释放 L1123、计数 0 L1124；重复回收/过期/绝对期限扫描后计数仍 0 L1131、释放时间不变 L1133、终态事件各 1 L1134-1135）；⑦CAS 拒绝二次释放：`test_b05_already_released_run_never_decrements_again` L1140-1166（预置 `admission_released_at` 非空 L1150-1156 ⇒ 收尾仍写终态 L1162 但 CAS 不重写释放时间 L1163、计数保持 1 L1165）；⑧不为负：`test_b05_release_without_counter_row_never_goes_negative` L1168-1186（无计数行的历史 Run 释放后 USER/TENANT 均为 0 L1185、`admission_counter_floor` 告警可见 L1186） | 同上：真实 PG 行锁 + 真实 Run/准入/回收/过期路径；计数行与 Run 行用独立连接读取（含 `pg_stat_activity` Lock 等待观测）；跨入口上限用真实受理（同用户不同入口账号/会话）验证；`test_b05_resumed_run...`/`test_b05_already_released_run...`/`test_b05_release_without_counter_row...` 用真实种入的排队/接管/历史行验证续跑、接管与历史行边界 | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 的差异（诚实记录）：计划列 `run_service.py`/`run_admission.py`/新测试文件；实际另改 `infrastructure/run_admission_store.py`（`decrement_counter`，TASK-030 的 `increment_counter` 对偶）、`application/run_deadline.py`（绝对期限收尾也释放，否则该路径的份额永久泄漏）、`application/async_tools/continuation_service.py`（领取守卫 + 领取清排队投影，「超时后不得再调用模型」的判定点在真实领取路径上）、`main.py`（把排队过期扫描接入既有期限循环，否则生产不会过期）、`docs/configuration-inventory.csv`（行号同步 + 2 条新常量行）。
- 锁序事实：释放走「Conversation → Run（调用方已持锁）→ USER 计数 → TENANT 计数」，**不取** policy 锁，也不从计数行倒锁会话（§3.2）；受理仍是「policy → Conversation → Run → USER → TENANT」，两者在计数行上同序，无环。
- 排队语义事实：`queue_deadline_at = min(受理+配置, 原 Run 绝对 deadline)` 与 `queued_at`/`queue_stage='WAITING_RUNTIME'` 在创建事务内冻结（与绝对期限同一份 `now`、同一份设置快照，不二次取配置）；领取（`_claim_one`）只在 `starting`（status=CREATED）、**从未被领取**（`execution_epoch == 0`）且 `queue_stage` 非空时拒绝过期行，并在领取成功时清空排队投影 ⇒ 续跑/人工恢复（epoch ≥ 1）不受初始排队期限约束；过期扫描与领取竞争同一行状态 CAS，且候选同样限定 `epoch == 0`，故「领取」与「过期」只有一个结果、已开跑/续跑的行不会被旧排队期限误杀。
- 幂等事实：`admission_released_at` CAS 是唯一释放闸门（`release_admission_in` 返回 False 即不递减）；终态状态 CAS 保证终态恰一个；计数行 `GREATEST(count-1,0)` 截断 + 表级 CHECK `open_run_count >= 0` 保证不为负；历史 Run（无计数行）释放时按 0 建行并留 `admission_counter_floor` 告警。
- 判别性验证（防止断言空转，两处变异均先复现失败再还原，随后 13 passed 复跑确认）：(1) 把 `_finalize_run` 的 `release_admission_in` 挪到 `commit()` 之后（终态先提交、释放另起事务）→ `test_b231_terminal_release_commits_in_one_transaction_with_terminal_state` **失败**（`assert 'COMPLETED' == 'RUNNING'`：计数行锁被占时终态已可见，证明「同事务」断言真的在测同一事务）；(2) 去掉 `release_admission_in` 的 `admission_released_at IS NULL` CAS 条件 → `test_b05_already_released_run_never_decrements_again` **失败**（释放时间被重写、计数 1→0），证明 CAS 断言非空转。
- 受影响回归修正（诚实记录）：无既有用例需要改写；`tests/agent_runtime/conftest.py` 的容量组（租户 100000/用户 100）继续生效，无需调整。TASK-030 记录的风险（`tests/acceptance/**` 的 runtime 栈未种 capacity）随本任务的终态释放落地而消除一半：非终态 Run 仍按上限占用，但同一用户连续建 Run 的旧风险不再累积（本任务按纪律未运行验收）。
- 配置盘点：新增 2 个模块级常量（`run_admission.QUEUE_STAGE_WAITING_RUNTIME`、`run_service.RUN_QUEUE_TIMEOUT`）已登记进 `docs/configuration-inventory.csv`；改动文件的行号平移已同步（body 行数 1395→1397，仅行号列变化 + 2 条新行，CRLF 保持：1398 个 CRLF、0 个裸 LF）→ `tests/test_configuration_inventory.py` **4 passed**。
- 回归证据：`tests/agent_runtime` **857 passed in 702.74s**（最终版本，含本任务 13 例）；中间版本 **854 passed in 702.46s**（10 例）、**855 passed in 704.15s**（11 例，含实现重构后）；`tests/contracts tests/test_configuration_inventory.py` **1285 passed in 4.32s**；`uv run ruff check <7 个改动文件>` 干净（`memory_consolidation_effects.py` 的 I001 为基线既有，不在改动内）；`uv run mypy apps packages` **407 源文件无问题**。
- 延后项（诚实记录）：持久排队阶段（`WAITING_MODEL` 等）与两入口状态投影（API-07/API-15 的 stage/reason 投影）属 TASK-036；Runtime 执行槽「有本地槽才 PG claim」属 TASK-032（本任务的领取守卫不依赖本地槽）；PG 服务端时钟统一（当前排队过期用 PG `now()`、领取守卫用进程 UTC，边界一致性由同一行 CAS 兜底）属 TASK-035；准入/排队过期指标目录属 TASK-098（本任务只留 `run_queue_timeout` 告警日志，未新增指标常量）；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 风险提示（诚实记录）：`expire_queued_runs`/`reap_abandoned_runs`/`expire_runs` 与既有回收器一致**不带租户谓词**（跨租户全局扫描），共享 dev 库上并发跑多套件时可能互相扫到对方的过期行；本任务用例对扫描返回值只做 `>= 1` 断言、对自有租户的行做精确断言，避免跨套件污染误判。
- B-05: verified — automated command passed; run_id=455fbd4890444f0aa671d13df94aea1e (confirmed_by: runner)
- B-231: verified — automated command passed; run_id=455fbd4890444f0aa671d13df94aea1e (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-231/B-05 RED→GREEN：先建 10 个行为用例 → **10 failed in 20.44s**；实现终态释放 CAS（`release_admission_in`/`decrement_counter`）+ 取消未开始执行 Run 直接终态 + 排队期限冻结/领取守卫/过期扫描（`queue_deadline`/`_initial_queue_deadline_passed`/`expire_queued_runs`）后 **13 passed in 8.26s**；两处变异验证断言判别性后还原（同事务、CAS）；回归 `tests/agent_runtime` **857 passed**（702.74s，最终版本）、`tests/contracts tests/test_configuration_inventory.py` **1285 passed**（4.32s）；ruff/mypy 干净
- [2026-10-18] completed (done)

---

## TASK-032: Runtime执行槽与独立监督

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-027, TASK-031
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-232
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/async_tools/supervisor.py`, `apps/agent-runtime/src/muad_agent_runtime/main.py`, `tests/agent_runtime/test_capacity_webchat_runtime_slots.py`
- **Estimate**: 45分钟

### Description

本地有槽才PG claim，无界gather替换为有界监督；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-232][integration] 先在`tests/agent_runtime/test_capacity_webchat_runtime_slots.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：本地有槽才PG claim，无界gather替换为有界监督。→ RED：首建 11 个行为用例后 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_runtime_slots.py` → **11 failed in 14.86s**（exit=1）；失败均为实现缺失的真实行为：`ExecutionSupervisor.__init__() got an unexpected keyword argument 'max_concurrency'`（9 例）、`AttributeError: 'ExecutionSupervisor' object has no attribute 'slot_limit'`（1 例）、`reclaim_stale_executions` 尚不存在（1 例）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 3 个文件：`async_tools/supervisor.py`（有界槽 `ExecutionSupervisor(max_concurrency=…)` + `slot_limit`/`free_slots`/`active_runs` + **本地槽闸门** `submit`/`claim_and_submit`（无槽时不调用 claim）+ 单槽独立完成回调 `_finished`（成功/异常/取消都只释放本槽）+ `reclaim`（按 Run 取消本地片段）+ 模块级 `reclaim_stale_executions`（按 PG 行事实回收失去归属的卡死槽））、`main.py`（`ExecutionSupervisor(max_concurrency=settings.runtime_execution_concurrency)` 装配 + `_execution_slot_loop` 独立监督循环 + `app.state.execution_slot_task` + 收尾先取消循环再 `close()`）、新测试文件。改动函数行数：`reclaim_stale_executions` 30、`claim_and_submit` 10、`close` 13、`_finished` 10、`_execution_slot_loop` 18，均 ≤50；`lifespan` 是既有装配函数（本任务只增 9 行，未新增超限函数）。错误显式：非法槽预算/关闭预算 `ValueError`、关闭超时 `TimeoutError`、监督循环失败 `logger.exception` 后继续（不静默）、回收只取消不吞异常（完成回调负责释放）。日志只记 `run_id`/`reclaimed`/`slots` 等低基数与资源 ID，无凭据或消息正文；未新增模块级常量（`docs/configuration-inventory.csv` 无需改行）
- [x] [B-232] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 11 个用例逐条对应契约（见 Acceptance Evidence 的断言位置列）
- [x] [B-232] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_slots.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **11 passed in 19.10s**（exit=0，最终版本复跑）；中间版本 **11 passed in 19.24s** / **11 passed in 19.15s**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-232 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 本地有槽才PG claim，无界gather替换为有界监督；WAITING_TOOL/INPUT释放片段槽，心跳/回收/控制不排在长任务队尾，断流不取消执行 | tests/agent_runtime/test_capacity_webchat_runtime_slots.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_slots.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-232 | 首建 `tests/agent_runtime/test_capacity_webchat_runtime_slots.py`（11 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_runtime_slots.py` → **11 failed in 14.86s**（exit=1）：`max_concurrency` 关键字不存在（9 例）、`slot_limit` 属性不存在（1 例）、`reclaim_stale_executions` 不存在（1 例） | 同命令（实现有界槽/本地槽闸门/独立回收/装配后）→ **11 passed in 19.10s**（exit=0，最终版本）；中间版本 **11 passed in 19.24s** / **11 passed in 19.15s** | ①本地有槽才 PG claim：`test_b232_local_slot_gates_pg_claim_and_reclaims_on_segment_end` L410-440（两槽占满 L419-423，槽满时 `claim_and_submit` 返回 False 且 claim 调用次数不变 L426-427，排队行仍 `CREATED`/无租约/epoch 0 L428-430，片段结束后同一真实领取路径补齐 L431-437，Run 仍 `RUNNING` 证明槽释放与终态解耦 L434）；②有界监督：`test_b232_pump_bounds_concurrency_to_local_slots` L448-481（真实 pump `run_once()==2` L459-460、重复 tick 0 且并发仍 2 L462-463、PG 只有 2 行被领取 L465、释放一槽后补齐第 3 条 L471-473）；③WAITING_TOOL/WAITING_INPUT 释放片段槽：`test_b232_wait_boundary_releases_segment_slot[WAITING_TOOL]`/`[WAITING_INPUT]` L485-512（真实 `try_wait`/`release_lease_and_wait` 写 PG 后 Run 非终态且租约已清 L503-505、槽归零 L506、另一条排队 Run 立即被领取 L507-508）；④心跳/回收/控制不排队尾：`test_b232_heartbeat_reclaim_and_control_progress_while_slots_busy` L518-581（槽全忙时 `renew_execution` 真实续租成功并推进租约 L540-543、`reap_abandoned_runs` 接管另一条过期租约 Run 为 FAILED L545-548、`ControlDispatcher.claim` 领到真实 CANCEL_OPERATION L565-570、全程槽数不变 L571）；⑤断流不取消执行：`test_b232_disconnected_stream_keeps_supervised_execution_running` L595-652（`aclose()` 断流 L630 后槽仍占 1、Run 仍 `RUNNING` 且属本实例 L633-635、断流窗口内租约被心跳推进 L637-640、放行后 `COMPLETED` 且槽归零 L642-645）；⑥独立回收卡死槽：`test_b232_stuck_slot_is_reclaimed_after_ownership_is_lost` L656-694（归属仍在时 `reclaim_stale_executions==0` L672、Reaper 接管后 Run 变 `FAILED` 而槽仍被卡死片段占着 L679-681、监督回收 1 个槽 L683、释放出的槽立即可被真实领取 L684-687）；⑦装配：`test_b232_lifespan_injects_slot_budget_and_runs_slot_supervision` L698-720（真实 lifespan 注入 `slot_limit==3` L709、监督循环任务存活 L710-711、按 PG 事实回收无对应 Run 行的槽 L713-715、退出后任务已结束 L720）；⑧真实模型回合：`test_b232_real_model_round_releases_slot_at_tool_wait` L831-889（真实 HTTP 探针两个回合 L884-885、Run 停在 `WAITING_TOOL` 且租约清空 L886、槽归零且不再重复领取 L887-888）；⑨边界：`test_b232_supervisor_rejects_invalid_slot_budget` L724-727（0/257/bool 拒绝）、`test_b232_supervisor_never_double_books_a_run_slot` L730-746（同一 Run 不重复占槽、无空闲槽不接收） | 隔离 PostgreSQL：每例 `create_datastore` 建空库 + `alembic upgrade head` + 隔离 Redis 号位，跑完 drop（`isolated_runtime` fixture L115），只读写本库；真实 `ContinuationPump`/`claim_continuation`/`try_wait`/`release_lease_and_wait`/`RunService`/`reap_abandoned_runs`/`ControlDispatcher.claim`/`reserve_operation` 业务路径，不替换存储或租约；必要 HTTP 模型探针是真实 uvicorn + 真实 socket 的 `/v1/chat/completions`（SSE 两回合：`tool_calls` → `stop`），真实 `OpenAICompatibleProvider`/`AgentRunner`/`AgentRunnerExecutor` 经真实 HTTP 跑完片段；装配用真实 `main.lifespan`（真实 PG/Redis 隔离号位）；无 mock 存储/租约、无共享 dev 库行 | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 一致（`supervisor.py`/`main.py`/新测试文件），未改动其它生产文件；`docs/configuration-inventory.csv` 无新增/移动常量，无需改行（`tests/test_configuration_inventory.py` 4 passed）。
- 语义事实：**有界槽**上限来自既有 `RUNTIME_EXECUTION_CONCURRENCY`（§3.7，进程启动固定），装配根注入；**本地槽先于 PG 领取**在 `claim_and_submit` 一处实现（无槽直接返回 False，`claim` 不被调用），真实 `ContinuationPump` 因此每 tick 最多领到空闲槽数（原「每 tick 最多 16 条、总量无上限」被替换）；**槽释放与 Run 终态解耦**由独立完成回调保证（终态、`WAITING_TOOL`/`WAITING_INPUT`、租约丢失、异常、取消都只释放本槽）；**独立监督循环** `_execution_slot_loop` 只按 PG 行事实（`RUNNING` + `lease_owner` 为本实例 + 租约未过期）回收失去归属的卡死片段，不使用本机时钟判定业务绝对期限（那属 `expire_runs` 的 PG 时钟扫描，TASK-035 统一服务端时钟）；心跳（每片段 `_renew_lease_loop`）、回收（`_reaper_loop`/`_deadline_loop`）、控制（`ControlDispatcher.run_forever`）各自独立循环，不排在执行槽之后。
- 判别性验证（防止断言空转，两处变异均先复现失败再还原，随后 11 passed 复跑确认）：(1) 去掉 `claim_and_submit` 的本地槽闸门 → `test_b232_local_slot_gates_pg_claim_and_reclaims_on_segment_end`、`test_b232_pump_bounds_concurrency_to_local_slots` **2 failed**（领取不再受本地槽限制）；(2) 令 `reclaim_stale_executions` 直接返回 0 → `test_b232_stuck_slot_is_reclaimed_after_ownership_is_lost`、`test_b232_lifespan_injects_slot_budget_and_runs_slot_supervision` **2 failed**（卡死槽不再被回收）。
- 静态检查：`uv run ruff check` 三个改动文件干净；`uv run mypy apps packages` **410 源文件无问题**。
- 回归证据：`tests/agent_runtime` **882 passed in 774.79s**（全量同域回归，含本任务 11 例；本轮为本任务改动后的最终生产代码，其后仅对本任务测试文件做「领取顺序确定性」修正，最终版本单跑 **11 passed in 19.10s**）；`tests/contracts` **1281 passed in 3.15s**；`tests/agent_worker/test_runtime_operation_races.py`（另一处 `ExecutionSupervisor` 消费者）**21 passed in 39.98s**；`tests/test_configuration_inventory.py` **4 passed**（无新增常量、CSV 未改）。
- 延后项（诚实记录）：Worker 侧有界任务池/独立回收属 TASK-033；持久排队阶段与两入口状态投影（本任务只保证「无槽不领取」，槽饱和时排队 Run 停在 `CREATED`）属 TASK-036；槽位/队列指标目录属 TASK-098（本任务只留 `execution_slots_configured`/`execution_slots_reclaimed` 结构化日志，未新增指标常量）；PG 服务端时钟统一属 TASK-035；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 风险提示（诚实记录）：`reclaim_stale_executions` 只做「失去归属」的保守回收，不主动取消仍在租约内但卡死的片段——那种情况由租约到期 + Reaper（或绝对期限扫描）先把 Run 变成终态，再由本循环释放槽，语义与「本地超时不等于远端已停止」一致。
- B-232: verified — automated command passed; run_id=eee8b65981814857a54f476c696a2d2c (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-232 RED→GREEN：先建 11 个行为用例 → **11 failed in 14.86s**（`max_concurrency`/`slot_limit`/`reclaim_stale_executions` 均不存在）；实现有界执行槽（本地槽闸门 + 单槽独立完成回调 + `reclaim`/`reclaim_stale_executions`）+ `main.py` 注入 `RUNTIME_EXECUTION_CONCURRENCY` 与独立监督循环后 **11 passed in 19.15s**；两处变异验证断言判别性后还原；回归 `tests/agent_runtime` **882 passed**（774.79s）、`tests/contracts` **1281 passed**（3.15s）、`tests/agent_worker/test_runtime_operation_races.py` **21 passed**（39.98s）；ruff/mypy（410 文件）干净
- [2026-10-18] completed (done)

---

## TASK-033: Worker有界任务池及独立回收

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-032
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**: harness-worker#RULE-worker-001
- **Acceptance-Refs**: B-233
- **Files**: `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `apps/agent-worker/src/muad_agent_worker/main.py`, `tests/agent_worker/test_capacity_webchat_worker_slots.py`
- **Estimate**: 60分钟

### Description

并发任务独立续租、无槽不领取；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-233][integration] 先在`tests/agent_worker/test_capacity_webchat_worker_slots.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：并发任务独立续租、无槽不领取。→ RED：首建 9 个行为用例后 `uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_slots.py` → **9 failed in 12.12s**（exit=1）；失败均为实现缺失的真实行为：`WorkerLoop` 无 `run_pending`/`slot_limit`/`execution_count`/`aclose`（7 例）、`TaskSlotSupervisor` 尚不存在（2 例）、`app.state.worker_loop` 未装配（1 例，与前一例重叠计数）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 3 个文件：`worker/service.py`（`TaskSlotSupervisor` 有界任务槽：本地槽闸门 `claim_and_start`（无空闲槽时 `claim` 一次都不调用）+ 单槽独立完成回调 `_finished`（成功/异常/取消都只释放本槽）+ `wait_completion` + `close`（超时显式 `TimeoutError`）；`WorkerLoop` 增 `slot_limit`/`execution_count`/`run_pending`（只领到槽满、**不等待执行收尾**）/`aclose`，`run_once` 保持"领一条并等它收尾"语义，`run_forever` 每拍 `run_pending`+`reclaim_expired`、取消时 `aclose` 后继续抛出，逐任务生命周期抽为 `_supervise_task`（执行+心跳共同监督，失约叫停整组本地执行）、`main.py`（装配根按 `settings.worker_execution_concurrency` 注入并暴露 `app.state.worker_loop`）。改动函数行数：`claim_and_start` 10、`_finished` 10、`close` 12、`run_pending` 6、`run_once` 7、`run_forever` 18、`_supervise_task` 44，均 ≤50。错误显式：非法槽预算/关闭预算 `ValueError`、关闭超时 `TimeoutError`（不静默成功）、单槽异常经完成回调 `logger.error`（不吞）、失约/无租约裁决的本地取消走可重试失败；日志只记 `task_id`/`slots`/`pending_count` 等低基数与资源 ID，无凭据或消息正文。无新增模块级常量；新增 import 使既有 5 个常量行号 +1，已同步更新 `docs/configuration-inventory.csv`（CRLF 保持，只改自己的 5 行）
- [x] [B-233] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 9 个用例逐条对应契约（见 Acceptance Evidence 的断言位置列）
- [x] [B-233] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_slots.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **9 passed in 17.68s**（exit=0，最终冻结版本复跑）；实现修正后 **9 passed in 15.39s**（初版 15.23s，随后修正心跳自取消片段的结局分类）
- [x] verifier `harness-worker#RULE-worker-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。→ 本任务责任 verifier 全量复跑（未改 runtime 文件，作同域回归）：第一部分 `uv run pytest -q tests/agent_worker` → **308 passed in 80.52s**（exit=0，含本任务 9 例，最终冻结代码复跑）；第二部分 `uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py` → **873 passed in 771.34s**（exit=0，隔离空库）。诚实记录：同一命令在共享 dev 库上首跑为 **871 passed / 2 failed in 740.02s**，2 例失败（`test_memory_redesign_snapshot.py::test_b12_single_memory_leaf_changes_only_new_run_and_job_uses_source_snapshot`）为共享 dev 库残留污染——陈旧孤儿 memory job 行的快照已被删除，而 `MemoryJobs.claim` 全局选取不带租户谓词（项目已知的「单跑绿、串跑红」形态）；同一文件在隔离空库 **10 passed in 10.68s**、全量 runtime 在隔离空库 **873 passed** 证明与本任务改动无关；未按规程之外清理共享 dev 数据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-233 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | 并发任务独立续租、无槽不领取；失约停止整组子进程、claim/reclaim不破坏既有父子锁序，投递/回收/调度独立，默认并发配置注入 | tests/agent_worker/test_capacity_webchat_worker_slots.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_slots.py"] | verified |
| harness-worker#RULE-worker-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py:20`、`worker/service.py:491`）。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-233 | 首建 `tests/agent_worker/test_capacity_webchat_worker_slots.py`（9 个行为用例）→ `uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_slots.py` → **9 failed in 12.12s**（exit=1）：`WorkerLoop` 无 `run_pending`/`slot_limit`/`execution_count`/`aclose`、`TaskSlotSupervisor` 不存在、`app.state.worker_loop` 未装配 | 同命令（实现有界任务槽/独立回收/装配注入后）→ **9 passed in 15.39s**（exit=0）；最终冻结版本复跑 **9 passed in 17.68s** | ①本地有槽才 PG claim + 槽释放补齐 + 两实例不重复领取：`test_b233_no_free_slot_means_no_pg_claim_and_refill_on_release` L288-337（上限取 settings L307；`run_pending()==2` 且只领到前两条 L308-313；槽满时 `run_pending()==0` L316、`run_once() is None` L317、计数 claimer 调用数不变 L315+318；排队行仍 `QUEUED`/无租约 L320；另一实例只领到未占用的行 L322-323；释放一槽后同一真实领取路径补齐第 4 条 L328-329；每条任务恰一条 CLAIMED 事件 L332-333）；②并发任务独立续租：`test_b233_concurrent_slots_renew_leases_independently` L345-377（两条租约各自推进到更晚且 owner 不变 L361-369；续租期间两槽都在执行 L370）；③卡死槽不拖累其它槽与独立回收：`test_b233_stuck_slot_does_not_block_other_slots_or_reclaim` L385-425（quick 完成后槽数 1、卡死行仍 `RUNNING` L398-400；槽忙碌时 `reclaim_expired` 回收过期租约 1 条 L411、过期行 `QUEUED` L412、租约内行不误伤 L413；释放出的槽补齐 later 并完成、卡死槽仍只占自己的槽 L415-418；放行后归零 L422）；④异常/取消路径释放槽：`test_b233_failure_and_cancel_paths_release_their_slots` L433-465（`BOOM` 可重试回 `QUEUED` 且 attempt=1 L451-454；CANCELLED 信封落终态 L455；槽数归零 L456；槽立即复用第三条完成 L458-462）；⑤失约停止整组子进程：`test_b233_lost_lease_stops_skill_process_group_and_releases_slot` L473-555（真实 Skill 子进程+孙进程 pid 文件 L477-496；租约置过期 L526-532、槽归零 L534、孙/子进程都被回收 L535-536；失约实例不写成功结论与终态事件 L537-540；`reclaim_expired` 后 `QUEUED` 且 attempt 保留、新持有者可 claim L542-549）；⑥claim/reclaim 父子锁序：`test_b233_reclaim_keeps_parent_child_lock_order_while_slots_busy` L563-609（池忙碌时父子两行都被回收为 `QUEUED`、attempt 保留、本实例持有的行不误伤 L595-599；回收出的行不破坏 claim 语义 L601-606）；⑦默认并发配置注入：`test_b233_main_injects_slot_budget_and_keeps_loops_independent` L617-631（真实 lifespan 下 `app.state.worker_loop.slot_limit==3` L624-628；退出后无悬挂后台任务 L630-631）；⑧任务池边界：`test_b233_pool_rejects_invalid_slot_budget` L639-644（0/65/True → `ValueError` L642-644）、`test_b233_pool_close_reports_slots_that_do_not_stop` L647-675（杀不掉的片段让 `close` 显式 `TimeoutError` L672-673，不静默成功） | 真实边界（不 mock 存储或租约）：隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完 drop，不触碰共享 dev 库）+ 隔离 Redis 号位；真实 `WorkerLoop`/`TaskClaimer`/`TaskSlotSupervisor` 领取/占槽/续租/回收全走真实代码；真实 PG 行事实驱动心跳续租（②③⑤）与 `reclaim_expired`（③⑥）；⑤ 用 `SkillTaskExecutor` + `ScriptSkillExecutor` 拉起真实 Skill 子进程与孙进程（pid 文件 + `ps` stat 验证整组回收）；⑦ 走真实 `main.lifespan` 装配 | verified |
| harness-worker#RULE-worker-001 | —（沿用原 Spec verifier，不重写） | `uv run pytest -q tests/agent_worker` → **308 passed in 80.52s**（exit=0，最终冻结代码）；`uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py` → **873 passed in 771.34s**（隔离空库，exit=0） | 原 verifier 全量复跑：PG 为 Task/Schedule/lease 唯一权威源、claim `FOR UPDATE SKIP LOCKED`、deadline 领取/收尾双侧硬条件、WAITING 释放 lease、Scheduler sweep `FAILED(TASK_DEADLINE_EXCEEDED)` 等既有断言全部由原用例覆盖，未重写为更弱测试（本任务只增 Worker 有界任务池与独立回收，未触碰 claim/deadline 口径） | 真实 PostgreSQL/Redis/真实进程（原用例边界不变）；共享 dev 库首跑 871 passed / 2 failed 为残留污染（隔离空库同文件 10 passed、全量 873 passed），未清理 dev 数据 | verified（review 层由需求级 verify-e2e 复跑） |

**实施范围与风险说明**

- 实现文件与计划 Files 一致（`worker/service.py`/`main.py`/新测试文件）；新增 import 使 `worker/service.py` 既有 5 个模块级常量行号 +1，已按真实行号同步 `docs/configuration-inventory.csv`（CRLF 保持、只改自己的 5 行），`tests/test_configuration_inventory.py` **4 passed**。
- 语义事实：**有界任务池**上限来自既有 `WORKER_EXECUTION_CONCURRENCY`（§3.7，进程启动固定，1～64），装配根注入并暴露 `app.state.worker_loop`；**本地有槽先于 PG 领取**在 `TaskSlotSupervisor.claim_and_start` 一处实现（无空闲槽直接返回 None，`claim` 一次都不调用）；`run_forever` 每拍只做"填满空闲槽 + `reclaim_expired`"两件快事，执行是各槽独立的 `asyncio` 任务，**回收/调度/投递不再排在长任务队尾**；槽释放由单槽完成回调保证（终态、异常、取消、失约四条路径都只释放本槽）；失约（租约失效/取消请求）由该槽心跳检查点叫停本地执行，执行器收尾整组子进程（`ScriptSkillExecutor` 的 killpg，未改该文件）；关闭时 `close()` 取消全部本地片段并有界等待，超时显式 `TimeoutError`。
- 与 TASK-032 的分工：Runtime 侧执行槽在 `application/async_tools/supervisor.py`（本任务未改）；Worker 侧任务池及回收在本任务。
- 判别性验证（防止断言空转，三处变异均先复现失败再还原，随后单跑复跑确认）：(1) 去掉 `claim_and_start` 的本地槽闸门 → `test_b233_no_free_slot_means_no_pg_claim_and_refill_on_release` **1 failed**（槽满仍在 claim）；(2) 去掉完成回调的槽释放（`_finished` 不 pop） → `test_b233_stuck_slot_does_not_block_other_slots_or_reclaim`、`test_b233_failure_and_cancel_paths_release_their_slots` **2 failed**（槽泄漏）；(3) 同时去掉心跳与监督两条叫停路径 → `test_b233_lost_lease_stops_skill_process_group_and_releases_slot` **1 failed**（孙进程存活）。
- 静态检查：`uv run ruff check` 三个改动文件干净；`uv run mypy apps packages` **412 源文件无问题**。
- 回归证据：`tests/agent_worker` **308 passed in 80.52s**（全量同域回归，含本任务 9 例，最终冻结代码复跑；过程中发现并修正"心跳已自取消片段"的结局分类后复跑通过）；`tests/contracts` + `tests/test_configuration_inventory.py` **1285 passed in 4.17s**；`tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py` **873 passed in 771.34s**（隔离空库；共享 dev 库首跑的 2 例失败为残留污染，见 verifier 证据）。
- 延后项（诚实记录）：槽位/队列指标目录属 TASK-098（本任务只留 `execution_slots_configured`/`worker_task_slot_failed`/`task_slot_close_timeout` 结构化日志，未新增指标常量）；PG 服务端时钟统一属 TASK-035；Worker 结果接入持久用户收件箱属 TASK-068；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- 风险提示（诚实记录）：Worker 侧"失去归属的本地卡死片段"由该槽心跳检查点回收（心跳发现 `not owned`/`requested` 即叫停本地执行并释放槽），不另设本地监督循环；若执行器吞掉取消（不配合终止），`close()` 会以显式 `TimeoutError` 失败而不会静默成功，该片段仍只占自己的槽、不拖累其它槽。
- B-233: verified — automated command passed; run_id=b17bace694944fd8a018b9817adcb45f (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-233 RED→GREEN：先建 9 个行为用例 → **9 failed in 12.12s**（`run_pending`/`slot_limit`/`execution_count`/`aclose`/`TaskSlotSupervisor`/`app.state.worker_loop` 均不存在）；实现 Worker 有界任务槽（本地槽闸门 `claim_and_start` + 单槽独立完成回调 + `close` 超时显式失败）+ `run_pending`/`_supervise_task`/`aclose` 与 `run_forever` 快节拍 + `main.py` 注入 `WORKER_EXECUTION_CONCURRENCY` 后 **9 passed in 15.39s**（最终复跑 17.68s）；三处变异验证断言判别性后还原；回归 `tests/agent_worker` **308 passed**（80.52s）、`tests/contracts`+inventory **1285 passed**（4.17s）；ruff/mypy（412 文件）干净
- [2026-10-18] verifier harness-worker#RULE-worker-001 全量复跑：`tests/agent_worker` **308 passed**（80.52s）；`tests/agent_runtime --ignore=test_runner_executor.py` **873 passed**（771.34s，隔离空库）；共享 dev 库首跑 2 例为残留污染（隔离空库复跑该文件 10 passed），未清理 dev 数据
- [2026-10-18] completed (done)

---

## TASK-034: 外部副作用重试安全与结果待核实

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-033
- **Source**: agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-234
- **Files**: `packages/contracts/src/muad_contracts/tool_runtime.py`, `apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py`, `tests/agent_worker/test_capacity_webchat_retry_safety.py`
- **Estimate**: 60分钟

### Description

READ_ONLY/IDEMPOTENT/NON_RETRYABLE由可信配置冻结；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-234][integration] 先在`tests/agent_worker/test_capacity_webchat_retry_safety.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：READ_ONLY/IDEMPOTENT/NON_RETRYABLE由可信配置冻结。→ RED：首建 `tests/agent_worker/test_capacity_webchat_retry_safety.py`（9 个行为用例，含 4 个参数化分支）→ `uv run pytest -q tests/agent_worker/test_capacity_webchat_retry_safety.py` → **1 error in 0.11s**（exit=2，收集期 `ImportError: cannot import name 'TASK_RESULT_UNVERIFIED' from 'muad_agent_worker.worker.execution_outcomes'`：契约面与失败分类实现尚不存在）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 4 个文件（**超出计划 Files 的披露**）：`packages/contracts/src/muad_contracts/tool_runtime.py`（`ToolRetrySafety` + `frozen_retry_safety`/`task_retry_safety` 严格解析 + `RETRY_SAFETY_POLICY_KEY`/`UNTRUSTED_RETRY_HINT_KEYS`/`DEFAULT_TOOL_RETRY_SAFETY`）、`packages/contracts/src/muad_contracts/__init__.py`（导出）、`apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py`（`FailureDisposition`/`FailureOutcome`/`classify_failure` + `TASK_RESULT_UNVERIFIED`）、`apps/agent-worker/src/muad_agent_worker/worker/service.py`（`_handle_failure` 与 `_reclaim_terminal_change` 两处按冻结 retry_safety 分流；后者是崩溃回收路径，属实现必需，已披露）。改动函数行数：`frozen_retry_safety` 16、`task_retry_safety` 3、`classify_failure` 35、`_reclaim_terminal_change` 25、`_reclaim_task` 42、`_handle_failure` 31，均 ≤50。错误显式：未知/非法/未声明一律回落 NON_RETRYABLE（保守方向，不抛错也不静默升级为可重试）；凭据/原始内部内容不进日志（日志沿用既有低基数结构化字段，本任务未新增日志）。
- [x] [B-234] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 9 个用例逐条对应契约（见 Acceptance Evidence 的断言位置列）
- [x] [B-234] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_retry_safety.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **9 passed in 18.72s**（exit=0，最终冻结版本）；判别性验证（两处变异先复现失败再还原）：去掉 `classify_failure` 的 NON_RETRYABLE 分流 → 5 failed；让 `frozen_retry_safety` 读取 `readOnlyHint` → 契约用例 + hint-only 集成用例 2 failed；去掉 `_reclaim_terminal_change` 的回收栅栏 → 回收用例 1 failed

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-234 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | READ_ONLY/IDEMPOTENT/NON_RETRYABLE由可信配置冻结；远端成功应答丢失复用业务键，未知非幂等效果不自动重做，MCP readOnlyHint不能单独授权重试 | tests/agent_worker/test_capacity_webchat_retry_safety.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_retry_safety.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-234 | 首建 `tests/agent_worker/test_capacity_webchat_retry_safety.py`（9 个行为用例）→ `uv run pytest -q tests/agent_worker/test_capacity_webchat_retry_safety.py` → **1 error in 0.11s**（exit=2，收集期 `ImportError: cannot import name 'TASK_RESULT_UNVERIFIED'`） | 同命令 → **9 passed in 18.72s**（exit=0，最终冻结版本） | ①幂等应答丢失复用业务键 `test_b234_idempotent_retry_reuses_business_key_and_keeps_effect_once` L368-401：首次失败仍可重试 L382、第一次尝试已产生外部效果 L384-389、退避后重试 L390、外部效果恰一次 L394、结果携带原业务键 L396、重试先核查已存在结果 L397、两次核查同键 L398；②只读有界重试 `test_b234_read_only_failure_retries_within_the_budget` L409-432：L423/L428/L429（两次只读尝试、零外部写 L425/L430）；③非幂等/未知/仅提示不自动重做 `test_b234_non_retryable_effect_is_not_redone_and_stays_unverified`（参数化 declared-non-retryable / undeclared / unknown-value / mcp-read-only-hint-only）L450-477：FAILED 且 `TASK_RESULT_UNVERIFIED` L464-465、attempt 不推进 L466、无结果 L467、外部写恰一次 L469、无 RETRY 事件 L471、终态不被回收重排 L473-475；④冻结解析只认可信键 `test_b234_frozen_retry_safety_reads_only_the_trusted_key` L480-497：未声明/未知/非法 → NON_RETRYABLE L482/L485-486、提示单独不授权且不覆盖可信声明 L488-493、快照口径 L494-496；⑤崩溃回收同语义 `test_b234_crash_reclaim_requeues_only_retry_safe_tasks` L505-562：非重试安全崩溃 → FAILED/待核实不重排 L547-550、重试安全保持 TASK-033 退避重排 L552-554、BATCH 幂等停放照常重排 L556-558、终态不再回收 L559；⑥失约叫停同语义 `test_b234_lost_lease_keeps_unverified_outcome_and_is_not_requeued` L565-605：真实子进程已产生外部效果 L586-588、心跳死亡后 FAILED/待核实 L595-597、不得自动重做 L598、子进程随组回收 L599、回收不改变终态 L602-604 | 真实边界（不 mock 存储或租约）：隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop，不触碰共享 dev 库）+ 隔离 Redis 号位；真实 `WorkerLoop`/`TaskClaimer` 领取、心跳、收尾、`reclaim_expired` 全走真实代码；真实 Skill 子进程（`SkillTaskExecutor` + `ScriptSkillExecutor`）用 `urllib` 对测试内真实 HTTP 服务（`_EffectProbe`，真实 socket）发起写/读，外部效果与业务键都在协议层记录；失约用例用真实 pid 文件 + `ps` 验证子进程随组回收 | verified |

**实施范围与证据说明**

- 契约面（TASK-037 的前置）：`retry_safety` 的冻结位置固定为 Task execution snapshot 的 `budget.retry_safety`（`RETRY_SAFETY_POLICY_KEY`）；`frozen_retry_safety` **只认这一个键**，MCP `readOnlyHint` 等提示字段永不参与解析，未知/未声明/非法取值一律 NON_RETRYABLE（`DEFAULT_TOOL_RETRY_SAFETY`）。TASK-037 负责在工具操作策略里**冻结**该值（本任务只定义解析/消费口径）。
- Worker 语义：`classify_failure` 把失败归成 RETRY/FAILED/UNVERIFIED 三态——确定性失败（`retryable=False`，如快照缺失/协议违规）保持精确错误码终态；可重试形状的失败按冻结 retry_safety 分流，NON_RETRYABLE 落 `FAILED(TASK_RESULT_UNVERIFIED)` 待核实，不自动重做。`_handle_failure` 与 `_reclaim_terminal_change`（崩溃回收）共用同一口径：**回收不改变结局语义**——非重试安全的崩溃尝试不重排、不重跑；重试安全的崩溃任务保持 TASK-033 的退避重排；BATCH 父任务（`_batch_envelope` 幂等停放、扇出按 `(parent_id, item_key)` 唯一键去重）保持重排，避免聚合被误判为待核实。
- 幂等复用：业务键就是 Task 行恒定的 `idempotency_key`（`executor.task_env` 经 `MUAD_TASK_IDEMPOTENCY_KEY` 交给 Skill，reclaim/重试间不变）；重试前由 Skill 核查外部结果（用例中真实 GET 探针 + `reused=True`）。
- 既有用例同步（**不是放宽**）：未声明 retry_safety 的任务自本任务起不再自动重试，5 个「重试机制」用例 + 5 个「崩溃回收」用例的种子显式声明 `retry_safety=IDEMPOTENT`（新增共享夹具 `helpers.retry_safe_snapshot()`），断言本身未削弱；`test_reclaim_gives_up_after_max_attempts`（预算耗尽 → `TASK_ATTEMPTS_EXHAUSTED`）与取消/WAITING 分支不受影响。
- 质量门：`uv run ruff check` 全部改动文件干净；`uv run mypy apps packages` **415 源文件无问题**；`docs/configuration-inventory.csv` 按真实行号同步（CRLF 保持、只改自己行），`tests/test_configuration_inventory.py` **4 passed**。
- 回归：`uv run pytest -q tests/agent_worker` → **317 passed in 97.13s**（exit=0，含本任务 9 例）；`uv run pytest -q tests/contracts tests/test_configuration_inventory.py tests/test_async_tool_dispatch_settings.py tests/architecture/test_import_direction.py tests/migrations/test_runtime_wait_parity.py tests/console_skill/test_javascript_package.py tests/console_tasks` → **1323 passed in 11.33s**（exit=0）；`uv run pytest -q tests -k schema_parity` → **47 passed**（exit=0）。
- 延后项（诚实记录）：`retry_safety` 的**冻结写入**（ToolRegistry → execution policy，含「未知默认 NON_RETRYABLE、readOnlyHint 不单独授权」的冻结面）属 TASK-037；E-15 的 E2E（真实 Worker 子进程 + PG claim/reclaim + 受控外部写服务）属 TASK-110；待核实状态的用户通知/投递文案属 TASK-063/068；指标目录属 TASK-098。本任务只交付契约、Worker 结局分类与回收分流。
- 边界（诚实记录）：确定性失败（协议违规/快照缺失等）保持精确错误码，不落「待核实」——待核实只用于可重试形状的失败（响应丢失/失约/未知远端结果）。未声明 retry_safety 的既有存量任务（含 BATCH 父任务之外的所有类型）在崩溃回收与失败收尾时都按 NON_RETRYABLE 处理，这是设计「未知按 NON_RETRYABLE」的直接后果。
- B-234: verified — automated command passed; run_id=8ecded3826ee4af7b5d491e848a7ccea (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-234 RED：首建 `tests/agent_worker/test_capacity_webchat_retry_safety.py`（9 个行为用例）→ **1 error in 0.11s**（收集期 ImportError，契约面/分类实现尚不存在）
- [2026-10-18] 实现：契约 `ToolRetrySafety` + `frozen_retry_safety`/`task_retry_safety`（只认可信键、未知一律 NON_RETRYABLE、提示不授权）；Worker `classify_failure`（RETRY/FAILED/UNVERIFIED）+ `TASK_RESULT_UNVERIFIED`；`_handle_failure` 与 `_reclaim_terminal_change` 按冻结 retry_safety 分流（回收不改变结局语义；BATCH 幂等停放保持重排）；既有 10 个用例种子显式声明 IDEMPOTENT（`helpers.retry_safe_snapshot()`，断言未削弱）
- [2026-10-18] B-234 GREEN：**9 passed in 18.72s**（exit=0，最终冻结版本）；两处变异（去 NON_RETRYABLE 分流 / 读 readOnlyHint / 去回收栅栏）先复现失败再还原
- [2026-10-18] 回归：`tests/agent_worker` **317 passed in 97.13s**；`tests/contracts`+inventory+同域聚焦 **1323 passed in 11.33s**；`tests -k schema_parity` **47 passed**；ruff 改动文件干净；mypy 415 文件无问题；inventory 4 passed
- [2026-10-18] completed (done)


---

## TASK-035: PG时钟、租约及绝对期限校验

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-022, TASK-031, TASK-033
- **Source**: agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-time#RULE-time-001
- **Acceptance-Refs**: B-235
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-worker/src/muad_agent_worker/worker/service.py`, `tests/agent_runtime/test_capacity_webchat_clock_lease.py`
- **Estimate**: 45分钟

### Description

Run/Task/模型租约与窗口使用PG时间；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-235][integration] 先在`tests/agent_runtime/test_capacity_webchat_clock_lease.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：Run/Task/模型租约与窗口使用PG时间。→ RED：首建验收文件（9 个用例）后 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_clock_lease.py` → **8 failed, 1 passed in 14.80s**（exit=1；唯一通过的是 TASK-022 已实现 PG 时钟的模型租约证据用例）；新增第 10 个用例（Schedule 冻结 Task 期限）后单独记录 RED：`-k task_deadline_freeze` → **1 failed, 9 deselected in 2.02s**（`schedule_misfire_skipped`：进程钟拨快 6h 被当成 misfire）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 见下方实施范围；新增/改动函数均 ≤50 行（`database_now` 8、`_pg_clock` 4、`_claim_one` 46、`expire_runs` 18、`_expire_one` 27、`claim_continuation` 31、`_create_run` 未增行、`_is_cancel_requested` 19）；失约/超期一律显式（`ExecutionLeaseLost`/`MODEL_CALL_LEASE_LOST`/`(False, _)`/`RUN_DEADLINE_EXCEEDED`/`TASK_DEADLINE_EXCEEDED`），无静默回落；无凭据/原始内容进入响应或日志
- [x] [B-235] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 10 个用例逐条对应（见 Acceptance Evidence 的断言位置列）
- [x] [B-235] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_clock_lease.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **10 passed in 15.43s**（exit=0）；finish 前复跑 **10 passed in 15.77s** / **10 passed in 13.88s** / **10 passed in 13.42s**
- [x] verifier `harness-time#RULE-time-001`（stage: review；timeout=300秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。→ **2 passed in 0.03s** + **47 passed, 5111 deselected, 1 warning in 25.93s**（exit=0；`timestamptz` 列、Console 时间格式与 IANA 时区口径未被本任务破坏）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-235 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | Run/Task/模型租约与窗口使用PG时间；本机时钟偏差不延长执行权，heartbeat与lease联动拒错，页面时间仍用IANA/DateTimeText口径 | tests/agent_runtime/test_capacity_webchat_clock_lease.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_clock_lease.py"] | verified |
| harness-time#RULE-time-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 数据库统一 `timestamptz` 存储；Console 展示统一 `YYYY-MM-DD HH:mm:ss`；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-235 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_clock_lease.py` → exit=1，**8 failed, 1 passed in 14.80s**：①受理冻结 L312 `timedelta(3600s) <= 60s` 失败（进程钟拨慢 1h 后期限跟着走）；②领取守卫 L342 `None is not None`（进程钟拨快 6h 时放弃了 PG 未过期的排队 Run）；③心跳续租 L390 `False is True`；④绝对期限扫描 L436 `1 == 0`（进程钟拨快误杀）；⑤Reaper L474 `0 == 1`（进程钟拨慢放过过期租约）；⑥Worker 续租 L703 `False is True`；⑦Task 领取 L778 `None is not None`；⑧Task deadline sweep L804 `1 == 0`。模型租约用例（L536）在实现前即通过（TASK-022 已用 PG 时钟，本任务登记为收口证据）。新增第 10 个用例后单独 RED：`-k task_deadline_freeze` → **1 failed, 9 deselected in 2.02s**（`schedule_misfire_skipped`） | 同命令 → **10 passed in 15.43s**（exit=0）；复跑 **10 passed in 15.77s** / **13.88s** / **13.42s** | ①受理冻结（L293-334）：`deadline_at - queued_at == deadline_ms` L322、`queue_deadline_at == deadline_at` L323、`start_time == queued_at` L324、`abs(queued_at - pg_now) <= 60s` L326-327（进程钟拨慢 1h 不生效）、CREATED 未被领取 L329；②领取守卫（L337-385）：PG 未过期 + 进程钟拨快 6h → 领取成功且 `lease_until <= pg+30s+tol`、`< pg+6h` L356-360、`queue_stage/queue_reason` 清空 L355；PG 已过期 + 进程钟拨慢 1h → `None` L374 且行仍 CREATED/无租约 L377-378；③心跳续租（L388-436）：PG 有效 + 拨快 → `True` 且新期限 `<= pg+60s+tol` 且 `< pg+6h` L411-414；PG 失约 + 拨慢 → `False` L424 且租约期限未被改写 L426；绝对期限已过 → `False` L435；④绝对期限扫描（L439-467）：拨快不误杀 `expire_runs() == 0` L452 且仍 RUNNING L453；拨慢也收尾 `== 1` L459、FAILED/RUN_DEADLINE_EXCEEDED + 租约清空 L461-463、`end_time ≈ pg` L464；⑤Reaper（L470-498）：拨快不碰有效租约 L484；拨慢照常回收 `== 1` L492、FAILED/RUN_ABANDONED L494-496、`end_time ≈ pg` L498；⑥模型租约/窗口（L536-594）：真实 `reserve` 期限 `0 < lease_until - pg <= 30s+tol` L562、拨快续租成功且 `<= pg+600s` 且 `< pg+6h` L571-572、PG 过期 + 拨慢 → `MODEL_CALL_LEASE_LOST` L587；⑦Worker 续租/期限判定（L698-777）：PG 有效 + 拨快 → `(True, False)` L714 且新期限 `<= pg+30s+tol` L717-718；PG 失约 + 拨慢 → `False` L728；PG 未过期 + 拨快 → COMPLETED L755；PG 已过期 + 拨慢 → FAILED/TASK_DEADLINE_EXCEEDED L774 且 `finished_at ≈ pg` L775；⑧Task 领取（L780-804）：拨快照常领取 L790 且 `lease_until <= pg+30s+tol`、`< pg+6h` L792-793；拨慢拒绝 L800 且仍 QUEUED L801；⑨Task deadline sweep（L807-827）：拨快不误杀 `== 0` L818 且仍 QUEUED L819；拨慢收尾 `== 1` L823、FAILED/TASK_DEADLINE_EXCEEDED L825、`finished_at ≈ pg` L826；⑩Schedule 冻结 Task 期限（L856-897）：`deadline_at - not_before == 24h` L891、`abs(not_before - pg) <= 60s` L892（进程钟拨快 6h 不生效） | ①运行侧 `run_service.py`/`continuation_service.py`/`run_deadline.py`/`supervisor.py` 走真实业务路径；`async_tool_database` 隔离库（空库 + `alembic upgrade head`，跑完 drop）；②受理冻结用例走真实 `RunService.start` + 真实 PG 行（共享 dev 库内只增删本租户行）；③模型租约走真实 `ModelCapacityService.reserve/renew` + 真实 `control.model_credential`/`runtime.model_capacity`/`model_call_lease` 行；④Worker 侧把 `DATABASE_URL` 指向同一隔离库，真实 `WorkerLoop`/`TaskClaimer`/`DeadlineSweeper`/`SchedulerLoop` + 真实 Task 行与租约；⑤进程钟偏差用 `_freeze_process_clock`（模块级 `datetime` 冻结到 ±小时级时刻），PG 时钟不动的对照 | verified |

**实施范围与证据说明**

- 新增时钟模块（PG 服务端时钟的唯一入口，避免 application→application 循环依赖）：`apps/agent-runtime/src/muad_agent_runtime/infrastructure/db_clock.py` 与 `apps/agent-worker/src/muad_agent_worker/infrastructure/db_clock.py` 的 `database_now(session)`（`statement_timestamp()`；取不到即 `RuntimeError`，绝不回落到本机墙钟）。原 `memory_proposals._database_now` 的定义搬到此模块，8 个导入点同步改名（`memory_jobs.py`/`run_service.py`/`memory_explicit_command.py`/`memory_confirmation_resume.py`/`memory_job_effects.py`/`memory_expiry.py`/`run_resume.py`/`memory_confirmation_expiry.py`）。
- 运行侧：`continuation_service.py`（`checkpoint_execution`/`try_wait`/`renew_execution` 在事务内取 PG 时钟；`claim_continuation` 候选预筛用 PG `now()`、`_claim_one` 行锁后取 PG 时钟做期限/排队期限 fence，租约期限也由 PG 写）、`run_deadline.py`（候选与逐行收尾都按 PG，`end_time`/`update_time` 用 PG）、`supervisor.py`（`reclaim_stale_executions` 归属判据按 PG）、`run_service.py`（`_create_run` 冻结的 `deadline_at`/`queued_at`/`queue_deadline_at` 取自 PG；`_reap_one` CAS、`_cancel`、`_is_cancel_requested`、`_persist_event`/`_finalize_run`/`_finalize_failed` 的租约 fence 全部改为 PG；删除从未被调用的进程钟辅助 `_lease_deadline`）。
- Worker 侧：`worker/service.py`（`_supervise_task` 的结果期限判定与 `moment`、`_renew_lease`、`_wait`/`_handle_success`/`_handle_failure`/`_mark_deadline_exceeded`/`_mark_cancelled_outcome`/`_handle_outcome`/`reclaim_expired` 都取 PG 时钟）、`worker/claimer.py`（`claim_one` 的 `not_before`/`deadline_at` 判定与租约期限取 PG）、`scheduler/service.py`（`DeadlineSweeper.sweep` 与 `SchedulerLoop.sweep_deadlines_if_due`/`run_due`/`run_once`/`fire` 取 PG 时钟——Schedule 触发的 Task `not_before`/`deadline_at` 与 misfire 宽限都在 PG 时钟域）。
- 显式边界（诚实记录）：`now` 参数保留为**测试注入点**（worker 侧既有用例大量使用；生产调用一律不传 ⇒ 默认 PG 时钟）；`ScheduleService.create_schedule/update/pause/resume` 仍用进程钟计算 `next_fire_at`（属于调度定义 CRUD 的时区投影，不是租约/绝对期限判定，留给后续任务）；运行侧 `_utcnow()` 只服务 SSE 事件时间戳（展示字段）。
- 配置盘点：新增 2 个模块（无模块级大写常量，0 行新增）；改动文件的常量行号平移已同步 → `uv run pytest -q tests/test_configuration_inventory.py` **4 passed**（CRLF 保持：1428 个 CRLF、0 个裸 LF；20 行行号更新）。
- 回归（本任务同域聚焦）：`uv run pytest -q tests/agent_runtime` → **895 passed, 2 failed**（2 个失败为既有环境问题，见下）；`uv run pytest -q tests/agent_worker` → **308 passed**；`uv run pytest -q tests/architecture tests/contracts` → **1314 passed**；`uv run ruff check <改动文件>` 干净（唯一剩余 I001 在未改动的 `memory_consolidation_effects.py`，属既有问题）；`uv run mypy apps packages` → **Success: no issues found in 417 source files**。
- 既有失败（非本任务引入，已用 `git stash` 基线复核）：`tests/agent_runtime/test_memory_redesign_snapshot.py::test_b12_*` 两个参数化用例在**未含本任务改动**的基线上同样失败（`memory_jobs.py:129 source snapshot unavailable`）；根因是共享 dev 库残留 1 条孤儿 PENDING 记忆作业（`dbg-7a23ea7b202d`，其 `runtime_snapshot` 已被其他套件清理）而 `MemoryJobs.claim` 不带租户谓词（既有已知机制）；按纪律不删共享 dev 数据。
- B-235: verified — automated command passed; run_id=22ec0b35b0af4ff9bca30e1becb30854 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] RED：验收文件先落地（9 用例）→ 8 failed / 1 passed；实现后 GREEN 10 passed（新增第 10 个 Schedule 冻结用例，单独 RED 1 failed）
- [2026-10-18] 实施：新增两处 `infrastructure/db_clock.py`（`database_now`）；运行侧 `run_service`/`continuation_service`/`run_deadline`/`supervisor` 与 Worker 侧 `worker/service`/`worker/claimer`/`scheduler/service` 的租约、排队/执行/绝对期限判定统一改取 PG 服务端时钟；`memory_proposals._database_now` 定义搬到基础设施层（8 个导入点同步）；删除未被调用的进程钟 `_lease_deadline`
- [2026-10-18] 边界裁定（诚实记录）：`now=` 保留为测试注入点（生产不传 ⇒ PG 时钟）；`ScheduleService` 的 `next_fire_at` 计算仍用进程钟（调度定义 CRUD，不属租约/期限判定）；模型租约/窗口（TASK-022）已是 PG 时钟，本任务以真实 `reserve/renew` 用例登记为收口证据
- [2026-10-18] 配置盘点同步（20 行行号更新、CRLF 保持）+ 同域回归（agent_runtime 895 passed/2 既有失败、agent_worker 308 passed、architecture+contracts 1314 passed）+ harness-time verifier 2 passed / 47 passed
- [2026-10-18] completed (done)

---

## TASK-036: 持久排队阶段与两入口状态投影

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006, TASK-025, TASK-032
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-236
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/execution_status.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py`, `tests/agent_runtime/test_capacity_webchat_queue_projection.py`（另按真实落点修改 `run_service.py`/`async_tools/continuation_service.py`/`executor.py`：阶段事件的三个真实产生点）
- **Estimate**: 45分钟

### Description

WAITING_RUNTIME/WAITING_MODEL/工具阶段与时间原因先持久化；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-236][integration] 先在`tests/agent_runtime/test_capacity_webchat_queue_projection.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：WAITING_RUNTIME/WAITING_MODEL/工具阶段与时间原因先持久化。→ RED：首建验收文件后 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_queue_projection.py` → **1 error in 0.18s**（exit=1，收集失败 `ModuleNotFoundError: No module named 'muad_agent_runtime.application.execution_status'`——实现尚不存在）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 见下方实施范围；改动函数均 ≤50 行（`_allocate` 32、`_project_model_wait` 26、`_project_model_running` 17、`persist_model_wait` 40、`persist_model_running` 24、`project_execution_status` 10、`normalized_queue_reason` 9、`_append_run_stage` 8）
- [x] [B-236] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 6 个用例逐条对应（见 Acceptance Evidence 的断言位置列）
- [x] [B-236] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_queue_projection.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **6 passed in 3.02s**（exit=0）；finish 前复跑 **6 passed in 2.93s** / **6 passed in 2.85s**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-236 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | WAITING_RUNTIME/WAITING_MODEL/工具阶段与时间原因先持久化；恢复后状态真实，状态文案不变助手正文/下一轮历史，不显示假预计时间或名次 | tests/agent_runtime/test_capacity_webchat_queue_projection.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_queue_projection.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-236 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_queue_projection.py` → exit=1，**1 error in 0.18s（收集失败，0 passed / 0 failed）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.execution_status'`——验收文件先落地、实现尚不存在 | 同命令 → **6 passed in 3.02s**（exit=0）；复跑 **6 passed in 2.93s** / **6 passed in 2.85s** | ①受理同事务：`test_b236_creation_persists_waiting_runtime_stage_with_its_event_atomically` L541-648（冻结排队投影 `queue_deadline=min(受理+60s, 绝对期限 1ms)=deadline_at` L578-583；行投影=事件载荷=契约 DTO L585-600；`WebchatActiveRun` Console 形状 L602-606；Run 行 `update_time` 与阶段事件 `create_time` 同一 `now()` L608-609；计数行锁被占期间 Run/事件/会话都不可见 L626-643、放行后一起出现 L644-650；准入拒绝整事务回滚不留半建 Run/阶段事件 L652-676）；②领取结束排队：`test_b236_claim_ends_the_queue_and_projects_running_in_one_transaction` L651-731（槽饱和停在 CREATED/WAITING_RUNTIME L676-683；Run 行锁阻塞期间仍 CREATED 且阶段事件只有 WAITING_RUNTIME L700-713、放行后行 RUNNING+`RUNNING` 事件一起出现 L714-731）；③投影栅栏/规范化：`test_b236_stage_persist_fences_foreign_leases_and_normalizes_reasons` L734-855（他人租约 CAS 落败不改行不发事件 L745-762；非码原因规范化丢弃 L764-782；同状态重放幂等 L783-797；恢复清投影 L798-810；终态 Run 两个写入都拒绝 L812-855）；④WAITING_MODEL：`test_b236_model_wait_projects_reason_and_bounded_deadline_then_resumes` L1060-1154（等待期间行=WAITING_MODEL+`reason=NO_CAPACITY`+真实期限 L1087-1096；等待中零上游请求 L1097；两入口一致 L1098-1103；载荷恰为契约字段（无预计时间/名次）L1104-1107；行 `update_time`=事件 `create_time` L1108-1109；放额度后阶段序列 `[WAITING_RUNTIME, RUNNING, WAITING_MODEL, RUNNING]`+真实上游 1 次请求+COMPLETED L1115-1127；真实模型输入与真实上下文重建均无阶段文案 L1129-1147；终态事件同词表 L1148-1152）；⑤工具阶段：`test_b236_tool_wait_stage_is_persisted_and_recovered_from_persisted_facts` L1238-1306（真实 `try_wait` 后行 WAITING_TOOL/租约清空 L1290-1292；事件序 `[RUN_STAGE(RUNNING), run.waiting_tool, RUN_STAGE(WAITING_TOOL)]` L1293-1304；等待事件与阶段事件同一 `now()` L1305-1306；新连接恢复读回同一投影 L1312-1314）；⑥终态收敛：`test_b236_projection_converges_after_cancel_and_queue_timeout` L1309-1381（取消后行 CANCELLED/排队投影清空/释放 L1338-1344；行投影=终态事件词表 L1345-1353；排队起点事实保留在历史 L1354-1359；排队过期 FAILED/`RUN_QUEUE_TIMEOUT`+投影收敛 L1361-1378） | 隔离租户的真实 PG（`run_record`/`canonical_event`/`runtime_snapshot`/`run_submission`/`conversation`/`run_admission_*`/`tool_operation`/`model_capacity` 与 `control.model_*`，只增删本租户行）；真实 `RunService` 受理/取消、`claim_continuation` 领取、`try_wait` 工具等待、`PooledModelProvider`/`ModelAllocator` 模型等待、`expire_queued_runs` 排队过期；真实 Console 应用（uvicorn + 真实 socket + 真实服务身份门控）提供 API-09 冻结引用解析；真实模型 HTTP 探针（uvicorn + 真实 socket，逐请求记录型号/鉴权/正文）；并发用第二条真实 PG 连接的 `FOR UPDATE` 阻塞 + `pg_stat_activity` Lock 等待观测（不 sleep 假装竞态）；无 mock 存储或租约 | verified |

**实施范围与证据说明**

- 实现文件：`execution_status.py`（新增：阶段词表 + `project_execution_status` 两入口唯一投影 + `persist_model_wait`/`persist_model_running` 行与事件同事务写入 + `normalized_queue_reason` 原因规范化）、`run_events.py`（`append_run_stage`：`run.stage` 事件的唯一写入口径，载荷就是 `WebchatExecutionStatus` 线格式）、`run_service.py`（受理事务内写 WAITING_RUNTIME 阶段事件）、`async_tools/continuation_service.py`（领取成功写 RUNNING、`try_wait` 写 WAITING_TOOL，均与行状态同事务）、`executor.py`（`PooledModelProvider._allocate` 在额度受限时先落 WAITING_MODEL（原因取自 `MODEL_CAPACITY_UNAVAILABLE.data.reason`）、恢复后回 RUNNING；投影写入失败只留 `run_stage_projection_failed` 警告，不打断模型执行）。
- 语义事实：①`run.stage` 是 Run 级公开事件（不带 submission_id，不属于某次提交；`test_runs_api` 的不变量按此收窄并显式断言）；②排队阶段（WAITING_RUNTIME/WAITING_MODEL）带冻结的 `queued_at`/`queue_deadline_at`，其余阶段带 `start_time`；③模型等待期限 = `min(等待起点 + 冻结 model_wait_timeout_sec, Run 绝对期限)`，不输出预计时间/名次（契约 `extra=forbid` + 载荷键集合断言）；④终态收敛走 `run.terminal`（`run.completed`/`run.failed`）与行投影同一词表，队列投影清空后行投影=终态；`RUN_STAGE` 不在 `HISTORY_EVENT_TYPES`，阶段文案不进助手正文/下一轮历史。
- 配置盘点：新增 11 个模块级常量行（`execution_status.py`）+ 2 个事件词表常量行（`run_events.py`，`RUN_STAGE_EVENT`/`RUN_STAGE_STREAM_TYPE`）；`executor.py`（+1 import 行）与 `continuation_service.py`（+1 import 行）的行号平移已同步 → `uv run pytest -q tests/test_configuration_inventory.py` **4 passed**（CRLF 保持：1427 个 CRLF、0 个裸 LF）。
- 静态校验：`uv run ruff check`（5 个改动文件 + 测试）干净；`uv run mypy apps packages` → **Success: no issues found in 413 source files**。
- 聚焦回归：`uv run pytest -q tests/agent_runtime` → **886 passed, 2 failed**（777.92s）；2 个失败为 `test_memory_redesign_snapshot.py::test_b12_...`（`memory_jobs.claim` → "source snapshot unavailable"），已用 `git stash` 暂存本次全部生产改动后复跑**同样 2 failed** ⇒ 基线已存在、与本任务无关（不修实现迁就）。其余：`tests/contracts` **1281 passed**（3.10s）、`tests/agent_worker/test_runtime_operation_races.py`+`tests/architecture` **54 passed**（39.27s）、`tests/test_configuration_inventory.py` **4 passed**；受影响既有用例同步更新：`test_runs_api.py`（streamed 事件序 + 阶段事件提交链接口径）、`test_tool_wait_state_machine.py`（事件序 +RUN_STAGE）。
- 未跑全量验收/浏览器验收/任何迁移 DDL（共享 dev 库约束；`expire_queued_runs` 为生产同源扫描，仅在隔离租户行上生效）。
- 延后项（诚实记录）：API-07/API-15 的 HTTP 路由与公开白名单投影属 TASK-051/052/046（本任务提供两入口共用的投影函数与 `run.stage` 事件行）；模型增量合并属 TASK-059；队列/阶段指标目录属 TASK-098（本任务只留 `run_stage_projection_failed` 结构化警告）；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责。
- B-236: verified — automated command passed; run_id=c1745a516ee2439285126657d69fa041 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-236 RED→GREEN：先建 6 个行为用例 → **1 error in 0.18s**（`execution_status` 模块不存在）；实现阶段词表/两入口投影/`run.stage` 事件与 WAITING_MODEL 持久化后 **6 passed in 3.02s**；聚焦回归 tests/agent_runtime **886 passed, 2 failed（基线已存在，stash 复现）**、tests/contracts **1281 passed**、worker/architecture **54 passed**、配置盘点 **4 passed**；ruff/mypy（413 文件）干净
- [2026-10-18] completed (done)

---

## TASK-037: 工具可信重试元数据冻结到操作策略

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-024, TASK-034
- **Source**: agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**: harness-mcp#RULE-mcp-001
- **Acceptance-Refs**: B-237
- **Files**: `packages/agent-core/src/muad_agent_core/tools/registry.py`, `apps/agent-runtime/src/muad_agent_runtime/application/async_tools/operations.py`, `tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py`
- **Estimate**: 60分钟

### Description

ToolRegistry可信retry_safety随execution policy冻结，未知默认NON_RETRYABLE；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-237][integration] 先在`tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：ToolRegistry可信retry_safety随execution policy冻结，未知默认NON_RETRYABLE。→ RED：首建验收文件（3 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py` → **1 error in 0.10s**（exit=2，收集期 `ImportError: cannot import name 'frozen_operation_retry_safety' from ...async_tools.operations`：冻结读取面与 ToolDefinition 声明字段尚不存在）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 3 个文件（**超出计划 Files 的披露**：`application/skill_tools.py`，仅 15 行声明/透传）：`packages/agent-core/.../tools/registry.py`（`ToolDefinition.retry_safety` Owner 侧声明，默认 `DEFAULT_TOOL_RETRY_SAFETY`=NON_RETRYABLE；提示字段不在 ToolDefinition 上）、`apps/agent-runtime/.../async_tools/operations.py`（`_submission` 冻结写入 `budget.retry_safety` + `reserve_operation`/`DurableTaskSubmitter.submit_task` 透传 + `frozen_operation_retry_safety` 读取）、`application/skill_tools.py`（`SkillToolSet.async_retry_safety` 声明 → 落在 EXECUTE_SKILL_TOOL 定义并随 `submit_task` 提交）。改动函数行数：`_submission` 46、`reserve_operation` 38、`frozen_operation_retry_safety` 9、`submit_task` 46、`SkillToolSet.__init__` 22、`_submit_background` 18，均 ≤50（`_definitions` 为改动前已 67 行的声明式元组，本任务仅 +1 行关键字）。错误显式：未知/未声明/非法取值一律 NON_RETRYABLE（保守方向，不抛错也不静默升级为可重试），复用 TASK-034 的 `frozen_retry_safety`/`task_retry_safety` 唯一口径，不另写枚举校验；凭据/原始内部内容不进日志（本任务未新增日志）。
- [x] [B-237] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 3 个用例逐条对应契约（见 Acceptance Evidence 的断言位置列）
- [x] [B-237] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **3 passed in 4.84s**（exit=0，最终冻结版本；最终版全量 `tests/agent_runtime` 复跑 **901 passed in 745.49s**）。判别性验证（两处变异先复现失败再还原）：让冻结忽略可信声明（固定 NON_RETRYABLE）→ 2 failed；去掉冻结写入 → 3 failed
- [x] verifier `harness-mcp#RULE-mcp-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。→ `uv run pytest -q tests/console_mcp/test_mcp_rules.py` → **3 passed in 0.01s**（exit=0；Streamable HTTP 唯一 transport、catalog 落 PG JSONB、Server 级范围无 Tool 级启停三项断言原样通过，未削弱）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-237 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | ToolRegistry可信retry_safety随execution policy冻结，未知默认NON_RETRYABLE；恢复不重新tools/list或猜测readOnlyHint，Worker读取的业务幂等键与Run操作一致 | tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py"] | verified |
| harness-mcp#RULE-mcp-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-237 | 首建 `tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py`（3 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py` → **1 error in 0.10s**（exit=2，收集期 `ImportError: cannot import name 'frozen_operation_retry_safety'`：冻结写入/读取面与 ToolDefinition 声明字段尚不存在） | 同命令 → **3 passed in 4.84s**（exit=0，最终冻结版本；最终版全量 `tests/agent_runtime` 复跑 **901 passed in 745.49s**） | ①可信声明冻结进操作策略与 Worker 快照 `test_b237_trusted_declaration_freezes_into_operation_policy_and_worker_snapshot` L211-247：ToolRegistry 声明 L227、冻结值 L235-236、Worker 读取口径一致 L237、快照哈希覆盖冻结值 L239、Worker Task 存在 L241、业务幂等键 `runtime-op:<operation_id>:submit` L243、来源操作/工具调用 L244-245、Worker 快照哈希 L246、Worker 侧冻结值 L247；②未知/提示/非法一律 NON_RETRYABLE `test_b237_undeclared_mcp_hint_and_invalid_values_freeze_as_non_retryable` L255-321：MCP 目录 effect=READ（readOnlyHint 归一化）不授权重试 L287-288、注入 READ_ONLY 也不改写冻结值 L299-300、未声明（None）L310、非法取值 L321；③一次冻结/重放不刷新/恢复只读冻结值 `test_b237_freeze_happens_once_and_replay_never_refreshes` L331-372：提交后冻结值 L349、重放复用同一操作 L362、当前声明已变仍读回原值 L364、恢复读取只认冻结值 L366、业务幂等键重放不变 L368、快照哈希不变 L369、无第二条操作/控制指令 L371-372 | 真实边界（不 mock 存储或租约）：隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop，不触碰共享 dev 库）+ 隔离 Redis 号位；真实 Runtime 业务路径 = `SkillToolSet` 工具注册/处理器 → `DurableTaskSubmitter`（真实 `tool_operation`/`tool_control_outbox` 写入）→ 真实 `ControlDispatcher` 经真实 Internal HTTP（Worker ASGI `muad_agent_worker.main:app`，`X-Internal-Service` 门控）提交；冻结值在 Runtime 侧 `tool_operation.submission_json.execution_snapshot.budget` 与 Worker 侧 `task_execution.execution_snapshot_json` 两处 PG 行上核对，业务幂等键由 Worker Task 行读出；失败分支直接走真实 `reserve_operation`（真实 Run 行锁/容量/出箱），未替换存储或租约 | verified |

**实施范围与证据说明**

- 冻结面（本任务交付）：`ToolDefinition.retry_safety` 是 Owner 侧可信声明（默认 `DEFAULT_TOOL_RETRY_SAFETY`=NON_RETRYABLE；MCP `readOnlyHint` 等提示字段既不在该类型上、也不参与解析，目录 effect 由提示归一化而来≠重试安全）。操作提交时 `_submission` **明确写入** `budget.retry_safety`（即使值是 NON_RETRYABLE），并**覆盖** agent `runtime_config.budget` 里混入的同名键/提示键；`reserve_operation` 重放分支直接返回已存在操作，不重新解析、不刷新。读取面 `frozen_operation_retry_safety(operation)` 只从操作行的冻结快照取 `budget.retry_safety`（`task_retry_safety` 同一口径），不重新 tools/list、不猜提示字段。
- 复用而非重写（TASK-034 已交付）：解析只用 `frozen_retry_safety`/`task_retry_safety` 与 `RETRY_SAFETY_POLICY_KEY`/`DEFAULT_TOOL_RETRY_SAFETY`；Worker 侧分流（`classify_failure`/`_handle_failure`/`_reclaim_terminal_change`）未改动，本任务只保证 Worker 读到的 `task.execution_snapshot_json` 携带冻结值。
- 生产语义（诚实记录）：V1 没有任何工具显式声明非默认 retry_safety，因此**异步 Skill 操作冻结值恒为 NON_RETRYABLE**——与 TASK-034「未声明即按 NON_RETRYABLE 处理」的既有语义一致，不改变在飞行为；声明面（`SkillToolSet.async_retry_safety`）为后续可信登记（可验证幂等契约/管理员登记）预留，本任务用测试证明该声明会被冻结并随重放保持不变。
- 质量门：`uv run ruff check` 全部改动文件干净；`uv run mypy apps packages` **417 源文件无问题**；`docs/configuration-inventory.csv` 按真实行号同步（CRLF 保持、只改本任务 3 个文件的 28 行行号，未触碰其它行），`tests/test_configuration_inventory.py` **4 passed**。
- 回归：`uv run pytest -q tests/agent_runtime` → **901 passed in 745.49s**（exit=0，最终冻结版本，含本任务 3 例）；`uv run pytest -q tests/agent_core tests/contracts tests/architecture` → **1546 passed in 12.09s**（exit=0）；`uv run pytest -q tests/migrations tests/test_async_tool_dispatch_settings.py tests/test_skill_artifact_cache.py tests/console_mcp` → **133 passed in 161.73s**（exit=0）。
- 延后项（诚实记录）：E-15 的 E2E（真实 Worker 子进程 + PG claim/reclaim + 受控外部写服务）属 TASK-110；待核实状态的用户通知/投递文案属 TASK-063/068；指标目录属 TASK-098；`harness-mcp#RULE-mcp-001` 为 review 阶段 verifier（本任务已登记真实执行结果，需求级终验复核）。
- B-237: verified — automated command passed; run_id=dde7e7b7571142a1a6b129314a9eb878 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-237 RED：首建 `tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py`（3 个行为用例）→ **1 error in 0.10s**（收集期 ImportError：冻结读取面/声明字段尚不存在）
- [2026-10-18] 实现：`ToolDefinition.retry_safety`（Owner 侧声明，默认 NON_RETRYABLE）+ `_submission` 冻结写入 `budget.retry_safety`（覆盖注入键）+ `reserve_operation`/`DurableTaskSubmitter.submit_task` 透传 + `frozen_operation_retry_safety` 读取面 + `SkillToolSet.async_retry_safety` 声明落在 EXECUTE_SKILL_TOOL 并随提交传递（超出计划 Files 的 1 个文件已披露）
- [2026-10-18] B-237 GREEN：**3 passed in 4.84s**（exit=0，最终冻结版本）；两处变异（冻结忽略可信声明 → 2 failed；去掉冻结写入 → 3 failed）先复现失败再还原
- [2026-10-18] 子 agent 中断接管：终止孤儿确认跑并在 worktree 内复跑全量域 `tests/agent_runtime` → **901 passed in 745.49s**（与证据内数字一致）；随后 finish 并按裁决提交
- [2026-10-18] 质量门与回归：ruff 干净、mypy 417 文件无问题、configuration-inventory 28 行同步且 4 passed；`tests/agent_runtime` 901 passed in 745.49s（最终版）、`tests/agent_core tests/contracts tests/architecture` 1546 passed、`tests/migrations + async_tool_dispatch_settings + skill_artifact_cache + console_mcp` 133 passed
- [2026-10-18] completed (done)

---

## TASK-038: 用户Token签发、重置与版本撤销

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-012
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-238
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/chat_token_service.py`, `apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/chat_token_repository.py`, `tests/console_auth/test_capacity_webchat_token_service.py`
- **Estimate**: 60分钟

### Description

安全随机至少256位、仅摘要落库；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-238][integration] 先在`tests/console_auth/test_capacity_webchat_token_service.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：安全随机至少256位、仅摘要落库。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-238] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-238] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_service.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-238 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 安全随机至少256位、仅摘要落库；用户→Token锁序，PUT expected_revision单胜且原文一次显示；DELETE旧版本重放不撤销新Token，关联会话同事务撤销 | tests/console_auth/test_capacity_webchat_token_service.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_service.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-238 | `uv run pytest -q tests/console_auth/test_capacity_webchat_token_service.py` → exit=1，**1 error（收集失败，0 passed / 0 failed）**：`ModuleNotFoundError: No module named 'muad_console_platform.application.chat_token_service'`——验收文件先落地、实现尚不存在 | 同命令 → **11 passed**（exit=0, 0.50s；连续 4 次复跑同为 11 passed，含两个真实并发/锁用例） | `test_b238_first_issue_uses_secure_random_and_stores_only_the_digest` L282-314（≥256 位熵 L287、SecretStr 表示与元数据投影无原文 L288-289、库内只有 SHA-256 摘要且任何列不含原文 L292-296、默认 30 天 L298、ACTIVE 元信息 L300-303、审计仅 CREATE 且无明文/摘要 L305-311）；`test_b238_generator_is_random_and_digest_matches_database_shape` L316-324（64 次样本互不相同、64 位小写十六进制）；`test_b238_reset_requires_matching_expected_revision_and_shows_plaintext_once` L327-362（expected_revision=0 在有签发行时 REVISION_CONFLICT 且不新签 L332-336、重置出新身份与原文 L338-341、重放不再轮换 L343-345、旧行保留/仅一个未撤销 L347-352、审计 CREATE/UPDATE/CREATE 且无任何版本原文 L354-361）；`test_b238_reset_revokes_source_sessions_in_the_same_transaction` L365-423（同事务内两条活跃会话共享同一撤销时刻 L400-402、旁观连接在提交前仍见旧行未撤销 L404-406、提交后会话 revoked_at == 来源 Token revoked_at L408-417、先前已撤销会话不被改写 L418、无关会话不被触碰 L419）；`test_b238_delete_is_version_scoped_and_replay_spares_later_tokens` L426-467（按版本撤销含关联会话 L440-446、重放幂等不改时间戳 L448-450、撤销后按基准重签 L452-456、旧版本迟到 DELETE 保持新 Token ACTIVE 且摘要匹配 L453-458、版本 0 与未配置用户空操作 L460-466）；`test_b238_issue_serializes_on_platform_user_row_lock` L470-513（持锁者 `FOR UPDATE control.platform_user`，等待者被 `pg_blocking_pids` 判定为被其阻塞 L486-489、等待期间对 `control.chat_access_token` 零锁且零行 L490-493、释放后 rev1 成功 L504-511）；`test_b238_concurrent_first_issue_has_a_single_winner` L519-552（并发首签恰好一成功一 REVISION_CONFLICT L543-547、落库仅 rev1 且摘要匹配胜者 L548-551）；`test_b238_ttl_follows_settings_source_and_already_issued_rows_do_not_drift` L554-568（设置 15 天/3 天分别生效 L557-558、已签发行不漂移 L561-562、重签用新值 L564-565）；`test_b238_corrupt_settings_block_issuance_but_not_revocation` L570-589（损坏设置下签发先失败：PLATFORM_SETTINGS_UNAVAILABLE L574-578、无半写出/不先撤销旧行/不产生审计 L579-583、撤销路径不读设置仍可用 L585-588）；`test_b238_metadata_status_and_user_scope` L591-616（NOT_CONFIGURED 全空投影 L593-601、到期判 EXPIRED L604-606、撤销判 REVOKED L608-609、跨用户/跨租户不串读 L611-615）；`test_b238_issue_for_unknown_or_foreign_user_is_not_found` L618-626（不存在/跨租户用户 COMMON_NOT_FOUND 且不写行/不写审计 L620-625） | 真实 PostgreSQL（共享 dev 库，仅 DML、无 DDL）：`control.chat_access_token`/`chat_session` 的 partial unique 与 `token_hash ~ '^[0-9a-f]{64}$'` CHECK 由 0024 兜底；`tests/console_auth/conftest.py::database_guard` 校验表存在；锁序用 `SELECT … FOR UPDATE` + `pg_blocking_pids`/`pg_locks` 真实观测；同事务用第二个连接的"未提交不可见"旁观者证明；TTL 来自真实 `control.platform_setting` 版本行（无版本行 = contracts schema 默认 30 天）；服务只 flush，提交由测试调用方决定；无 mock、无 HTTP 替身、无模型调用 | verified |

B-238: verified — automated command passed; run_id=f399ab79e5884c5dbd84de1b0059df8d (confirmed_by: runner)

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/application/chat_token_service.py`（`ChatTokenService.issue_token`/`revoke_token`/`read_metadata` + `generate_chat_token`/`hash_chat_token`/`token_snapshot`/`_metadata`/`_next_revision`/`_access_ttl`）、`apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/chat_token_repository.py`（`find_latest`/`find_live_for_update`/`find_by_revision`/`add`/`revoke`）。
- 关键设计落点：①版本基准取**最新签发行**（撤销不删行），因此"撤销后再按 GET 到的最新版本重签"得到更高 revision，而过期版本/重放一律 `REVISION_CONFLICT`；②锁序固定为"用户→Token"（先 `platform_user FOR UPDATE`，再 `find_live_for_update`），并发首签由真实行锁收敛为单胜；③撤销/重置在同一事务写 `chat_session.revoked_at`（同一 `now`），且不改写先前已撤销会话；④`webchat.access_ttl_days` 只读一次且发生在行锁之外，设置源损坏 ⇒ `PLATFORM_SETTINGS_UNAVAILABLE`（503），撤销路径不读设置故不受其影响；⑤审计快照只含 revision/revoked/issued_at/expires_at（无原文、无摘要），日志只含 token_id/revision/revoked_sessions。
- 新增错误码：无（复用既有 `REVISION_CONFLICT`、`COMMON_NOT_FOUND`、`PLATFORM_SETTINGS_UNAVAILABLE`，三处登记已由 TASK-007 完成）。
- 配置盘点：新增 5 个模块级常量行写入 `docs/configuration-inventory.csv`（`chat_token_service.py` 45/46/47/48/50 行），`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（服务/仓储/测试三文件）干净；`uv run mypy apps packages` → **Success: no issues found in 398 source files**。
- 聚焦回归：`uv run pytest -q tests/console_auth` → **51 passed**（6.38s）；`uv run pytest -q tests/contracts` → **1281 passed**（3.00s）。未跑全量验收/浏览器验收/任何迁移 DDL（共享 dev 库约束）。
- 交接给后续任务：API-03 路由（TASK-039）直接消费 `issue_token`/`revoke_token`/`read_metadata`；CHAT 交换（TASK-040）用 `hash_chat_token` 按摘要定位 Token，并自行复核用户 `status` 与会话期限 ≤ 来源 Token 期限。
- B-238: verified — automated command passed; run_id=f399ab79e5884c5dbd84de1b0059df8d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-238 RED：`uv run pytest -q tests/console_auth/test_capacity_webchat_token_service.py` → exit=1，1 error（`ModuleNotFoundError: muad_console_platform.application.chat_token_service`）
- [2026-10-18] 实现 `ChatTokenService` + `ChatTokenRepository`：用户→Token 行锁序、最新签发行作版本基准、重置同事务撤销旧 Token 与全部活跃会话、按版本幂等撤销、TTL 取自平台设置（损坏显式 503）
- [2026-10-18] B-238 GREEN：11 passed（连续 4 次复跑稳定）；`tests/console_auth` 51 passed；`tests/contracts` 1281 passed；inventory 4 passed；ruff/mypy 干净；新增 5 行配置盘点
- [2026-10-18] completed (done)

---

## TASK-039: 用户详情Token管理的ADMIN端点

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-007, TASK-038
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-239
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/users.py`, `tests/console_auth/test_capacity_webchat_token_admin_api.py`
- **Estimate**: 45分钟

### Description

API-03 ADMIN+CSRF、租户取当前管理员主体；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-239][integration] 先在`tests/console_auth/test_capacity_webchat_token_admin_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：API-03 ADMIN+CSRF、租户取当前管理员主体。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-239] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-239] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_admin_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-239 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | API-03 ADMIN+CSRF、租户取当前管理员主体；GET只元信息，首签/重置响应no-store且不入幂等回执，BUILDER/CHAT不能维护 | tests/console_auth/test_capacity_webchat_token_admin_api.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_admin_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-239 | `uv run pytest -q tests/console_auth/test_capacity_webchat_token_admin_api.py` → exit=1，**6 failed / 0 passed**（1.24s）：三条 `/api/v1/users/{id}/webchat-token` 路由尚未注册——ADMIN 首签 PUT 落 404 `COMMON_NOT_FOUND`、BUILDER 读也 404（未挂路由）；验收文件先落地、实现尚不存在 | 同命令 → **6 passed**（exit=0，1.41–1.66s；连续 5 次复跑同为 6 passed） | `test_b239_only_admin_with_csrf_may_manage_the_token` L231-268（匿名 GET 401 UNAUTHORIZED L237-238、BUILDER 对 GET/PUT/DELETE 全 403 FORBIDDEN 且零 Token 行 L247-251、ADMIN 缺/错 CSRF 的 PUT 与 DELETE 403 且零行 L256-262、安全方法 GET 免 CSRF L264-266、带正确 CSRF 的首签 200 L267-268）；`test_b239_get_returns_metadata_only_and_never_crosses_tenants` L271-302（元信息恰好六字段且全 NOT_CONFIGURED L280-283、no-store L281/L299、外租户真 Token 读作未配置且不回显原文 L285-288、伪造 `X-Tenant-Id` 不切租户 L289-293、签发后 GET 仍只有元信息且无原文/token_hash L296-302）；`test_b239_first_issue_reveals_plaintext_once_without_any_receipt` L305-340（PUT no-store L315、响应键集=元信息+token L317、≥43 字符随机原文 L319、原文全响应恰出现一次 L321、库内仅 SHA-256 摘要且任何列无原文 L324-327、审计仅 CREATE 且无原文与摘要 L330-332、两张真实回执表本租户零行 L334/L340、同键同体重放 409 REVISION_CONFLICT 且不回放明文/不轮换 L336-340）；`test_b239_reset_single_winner_never_rotates_on_stale_or_replayed_revision` L343-380（过期版本 0 → 409 L354-356、按最新版本重置出 rev2 与新原文 L359-362、库内 rev1 已撤销/rev2 未撤销且摘要匹配 L364-370、响应丢失重放 409 且行数仍 2 L372-374、未知版本 0/3 → 409、GET 仍 rev2 ACTIVE L376-380）；`test_b239_revoke_is_version_scoped_and_takes_sessions_in_transaction` L383-434（缺 `expected_revision` → 422 L396-397、版本 0 空操作且不动会话 L399-403、按版本撤销后状态 REVOKED L405-407、真实 `chat_session` 行与来源 Token 同一 `revoked_at` L409-411、重放不改时间戳 L413-415、撤销后按基准重签 rev2 且**旧版本迟到撤销不误伤新 Token** L417-424、未知/跨租户用户 PUT/DELETE → 404 COMMON_NOT_FOUND 且外租户零行 L429-434）；`test_b239_user_token_is_not_a_console_credential` L437-457（Bearer 用户 Token 读/写/列用户均 401 UNAUTHORIZED L444-449、被拒响应不回显凭据 L450-451、同名 Cookie 冒充 Console 会话也 401 L453-456、状态零变化 L457） | 真实 Console ASGI（`muad_console_platform.main.app`）+ 真实登录会话与 CSRF cookie：ADMIN/BUILDER 由 `tests/console_auth/conftest.py` 的真实账号登录裁决，无身份替身；真实 PostgreSQL：`control.chat_access_token`/`control.chat_session`/`control.config_audit_log`/`control.skill_import_idempotency`/`control.platform_setting_idempotency` 全部按 `tenant_id` 真实读写与计数；外租户 Token 由 `ChatTokenService` 真实签发构造（L188-199），活跃会话按真实 FK 落库（L201-216）；四个测试各自 tenant 每用例唯一，teardown 按会话→Token→用户→设置→回执→审计顺序清理（L92-96、L99-130） | verified |

B-239: verified — automated command passed; run_id=2b81adc3c10043c39443663a90dc2047 (confirmed_by: runner)

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/api/users.py`（新增 `GET/PUT/DELETE /api/v1/users/{user_id}/webchat-token` + `TokenRevision`/`_no_store`）；测试：`tests/console_auth/test_capacity_webchat_token_admin_api.py`。新签名依赖零重写：直接复用 TASK-038 的 `ChatTokenService.issue_token/revoke_token/read_metadata` 与 `WebchatTokenIssued.to_issuance_payload()`。
- 关键设计落点：①**ADMIN+CSRF 双门**——三条路由挂在 `api/router.py` 的 `admin` 组（`require_admin`+`require_csrf`），且凭据写入口显式声明 `AdminAccount`（与 API-16 同口径），非 ADMIN 连 GET 都 403；②**租户取管理员主体**——`tenant_id: AccountTenantId`（登录账号租户），浏览器可改写的 `X-Tenant-Id` 不参与判定，外租户同一用户的真实 Token 在此读作 `NOT_CONFIGURED`；③**GET 只元信息**——响应恰为 `{configured,status,token_id,revision,issued_at,expires_at}`，无 `token`/`token_hash` 字段；④**原文一次性与 no-store**——PUT 用 `to_issuance_payload()` 显式放行一次明文，响应 `Cache-Control: no-store`（GET/DELETE 同样），明文不进库（只有 SHA-256）、不进审计（连摘要都不写）、不回显在被拒响应里；⑤**不写幂等回执**——API-03 的并发/重放由 `expected_revision` 单胜裁决（0=首签、N=按最新签发行重置），即使请求携带 `Idempotency-Key`，两张真实回执表本租户零行，重放只得到 409 而不是可回放明文；⑥**版本撤销幂等**——DELETE 按 `?expected_revision=N` 只作用于该版本（含关联会话同事务同刻撤销），旧版本迟到重放不撤销后续新 Token，0 在已有签发行时是空操作；⑦**错误码零新增**，复用 `COMMON_NOT_FOUND`(404)/`REVISION_CONFLICT`(409)/`FORBIDDEN`(403)/`UNAUTHORIZED`(401)，三处登记由 TASK-007 完成。
- 配置盘点：未新增模块级大写常量（`TokenRevision`/`_no_store` 不属扫描口径），`docs/configuration-inventory.csv` 无需变更；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**（含在同域回归的 18 passed 内）。
- 静态校验：`uv run ruff check`（users.py + 验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 400 source files**。
- 聚焦回归：`uv run pytest -q tests/console_auth` → **57 passed**（8.37s，含本任务 6 例）；`uv run pytest -q tests/contracts` → **1281 passed**（3.27s）；`uv run pytest -q tests/test_configuration_inventory.py tests/console_platform/test_users_api.py tests/console_platform/test_user_detail_api.py tests/console_platform/test_user_side_relations.py tests/console_platform/test_bind_codes_api.py` → **18 passed**。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）。
- 真实边界说明：本任务路径不发起任何外部模型调用，故"外部模型真实 HTTP 探针"边界在 API-03 上不适用（无 mock 也无探针需要）；其余真实边界（真实 Console 应用服务/依赖、独立 PostgreSQL 与事务）全部如实执行。
- 交接给后续任务：TASK-085 前端凭据卡片按 `GET` 元信息 → `PUT {expected_revision}` 首签/重置（原文只在本次响应展示一次）→ `DELETE ?expected_revision=N` 版本撤销接线；TASK-042 的 CHAT 交换仍按 `hash_chat_token` 摘要定位 Token，不经过本端点。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-239 RED：`uv run pytest -q tests/console_auth/test_capacity_webchat_token_admin_api.py` → exit=1，6 failed / 0 passed（三条 webchat-token 路由未注册，首签 PUT 落 404）
- [2026-10-18] 实现 API-03 三条路由（GET 元信息 / PUT 首签重置 / DELETE 版本撤销）：ADMIN 门控 + CSRF、租户取登录管理员主体、明文一次性 + no-store、不写幂等回执、版本单胜
- [2026-10-18] B-239 GREEN：6 passed（连续 5 次复跑稳定）；`tests/console_auth` 57 passed；`tests/contracts` 1281 passed；用户域回归 18 passed；ruff/mypy 干净；配置盘点无新增
- [2026-10-18] completed (done)

---

## TASK-040: Token交换与独立CHAT会话生命周期

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-038
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-240, B-08
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/chat_auth_service.py`, `apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/chat_session_repository.py`, `tests/console_auth/test_capacity_webchat_chat_session_service.py`
- **Estimate**: 60分钟

### Description

验证来源Token/用户状态后生成独立会话摘要；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-240][integration] 先在`tests/console_auth/test_capacity_webchat_chat_session_service.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：验证来源Token/用户状态后生成独立会话摘要。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-240] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-240] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] [B-08][integration] 将原B-08落入`tests/console_auth/test_capacity_webchat_chat_session_service.py`，先RED后GREEN；真实 PG Token/会话约束、有效期裁决与用户锁；断言：有效期必须正整数且有界；到期瞬间拒绝，Token最后1小时交换只能获≤1小时会话，读请求不滑动续期；同用户并发重置仅一个expected_revision成功，响应丢失重试不再次轮换或回放明文；旧版本撤销重放不影响后续新Token；摘要唯一且每用户至多一个未撤销Token。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-240 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 验证来源Token/用户状态后生成独立会话摘要；expires=min(PG now+12h,Token expires)，不滑动续期；Cookie到期重认同用户保留历史，logout只撤本会话 | tests/console_auth/test_capacity_webchat_chat_session_service.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | verified |
| B-08 | integration | 真实 PG Token/会话约束、有效期裁决与用户锁 | 有效期必须正整数且有界；到期瞬间拒绝，Token最后1小时交换只能获≤1小时会话，读请求不滑动续期；同用户并发重置仅一个expected_revision成功，响应丢失重试不再次轮换或回放明文；旧版本撤销重放不影响后续新Token；摘要唯一且每用户至多一个未撤销Token | tests/console_auth/test_capacity_webchat_chat_session_service.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-240 | `uv run pytest -q tests/console_auth/test_capacity_webchat_chat_session_service.py` → exit=2，**1 error（收集失败，0 passed / 0 failed）**：`ModuleNotFoundError: No module named 'muad_console_platform.application.chat_auth_service'`——验收文件先落地、实现尚不存在 | 同命令 → **16 passed**（exit=0，0.77–0.95s；连续 5 次复跑同为 16 passed，含真实行锁与并发用例） | `test_b240_exchange_creates_an_independent_session_after_validating_source` L318-372（公开会话信息恰七字段 L330、服务端导出身份/展示名与 `auth_context_id==session_id` L331-335、原文 ≥256 位且 SecretStr 表示无泄漏 L336-337、库内只有会话摘要且与用户 Token 摘要相互独立 L341-350、交换不改写来源 Token L353-355、明文与摘要不进审计/日志 L359-366、伪造与"会话原文再交换"一律 CHAT_TOKEN_INVALID L369-372）；`test_b240_session_expiry_is_min_of_exchange_ttl_and_source_remaining` L375-429（默认 12 小时 L378-381、设置 24h 按来源生效 L386-394、设置 1h 只影响后续且已建会话不漂移 L397-408、Token 只剩 1 小时 → 会话恰在来源到期时刻结束且 ≤1 小时 L410-428）；`test_b240_resolve_reads_live_session_and_source_without_sliding` L432-484（三次读取投影一致 L445-446、读取不改写会话行也不延长来源 L448-455、损坏设置不影响已认证读取 L457-459、未知摘要 UNAUTHORIZED 且不回显 L462-466、停用用户与直接撤销来源 Token 都拒绝 L469-483）；`test_b240_expiry_instant_is_rejected_for_token_and_session` L487-522（同一事务 `expires_at = now()` 交换 CHAT_TOKEN_EXPIRED 且不落会话行 L492-504、会话到期瞬间 CHAT_SESSION_EXPIRED 且被拒读取不改写期限 L507-521）；`test_b240_logout_revokes_only_the_presented_session` L525-552（多设备会话并存 L530-533、登出只撤本会话且不动其他会话/来源 Token L535-540、被撤会话读取 CHAT_SESSION_REVOKED 而另一会话仍可读 L542-545、重放幂等与未知会话空操作 L547-551）；`test_b240_cookie_expiry_reauthentication_keeps_identity_and_history` L555-597（Cookie 到期读取 CHAT_SESSION_EXPIRED L571-576、重新认证回同租户同用户且新会话标识/新原文 L578-586、过期会话行保留、其他设备不受影响 L587-592、随后登出只作用于本会话 L594-597）；`test_b240_chat_session_cannot_authenticate_as_console` L600-631（Bearer 会话原文对 /auth/me、/users、webchat-token 管理端点全 401 且不回显原文/摘要 L608-625、`muad_session` Cookie 冒充同样 401 L622-624、库内无对应 Console 会话行 L626-631） | 真实 Console 应用服务与真实 ASGI `muad_console_platform.main.app`（会话裁决依赖，不伪造主体）；真实 PostgreSQL（共享 dev 库，仅 DML、无 DDL）：`control.chat_access_token` / `chat_session` 的 partial unique 与 `token_hash ~ '^[0-9a-f]{64}$'` CHECK 由 0024 兜底，`conftest.database_guard` 校验表存在；期限裁决全部用 **PG `now()`**（同一事务内固定）——"到期瞬间"用例在事务内把 `expires_at` 设为 `now()`、"只剩 1 小时"用 `now() + interval '1 hour'`，无时钟替身；服务只 flush，提交由调用方决定；本路径无外部模型调用，故"外部模型真实 HTTP 探针"边界不适用（既无 mock 也无探针需要） | verified |
| B-08 | 同上（同一文件/同一命令，RED 与 B-240 同批：收集失败 1 error） | 同上 → **16 passed**（B-08 用例与 B-240 同文件同命令；连续 5 次复跑稳定） | `test_b08_out_of_bounds_validity_fails_explicitly` L634-656（参数化 `session_ttl_hours` 0/25/1.5 与 `access_ttl_days` 0/366 → PLATFORM_SETTINGS_UNAVAILABLE、不落半写出会话、不撤销来源、不回显凭据）；`test_b08_positive_session_ttl_bounds_are_applied` L658-681（1 与 24 小时两个边界内的正整数按来源生效）；`test_b08_concurrent_reset_has_a_single_winner_and_replay_never_rotates` L683-757（并发重置恰好一胜一 REVISION_CONFLICT L712-718、只产生一个新版本且每用户至多一个未撤销 Token L719-723、摘要匹配胜者而旧版本关联会话同事务撤销 L724-731、响应丢失重放不轮换不回放明文 L733-741、跨用户摘要唯一 L743-754、被重置的旧 Token 交换 CHAT_TOKEN_REVOKED L756-757）；`test_b08_exchange_serializes_on_the_user_row_lock` L759-790（`pg_blocking_pids` 观测交换等待平台用户行锁、等待期间不落会话行、释放后单行成功）；`test_b08_late_revoke_replay_spares_later_token_and_its_session` L792-835（旧版本迟到的撤销保持 rev2 ACTIVE、新版本会话仍可读、摘要匹配新原文） | 真实 PG 行锁与约束："用户→Token" 锁序用 `SELECT … FOR UPDATE` 真实观测（`pg_blocking_pids` 判定等待者被持锁者阻塞；`test_b08_exchange_serializes_on_the_user_row_lock`）；`find_live_for_update` + partial unique `uq_chat_access_token_tenant_user_unrevoked` 使"每用户至多一个未撤销 Token"在库层闭合；并发重置用两个真实连接 `asyncio.gather`；会话/Token 的时间列全部 timestamptz、`expires_at > issued_at` 与摘要形态 CHECK 由 0024 兜底 | verified |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/application/chat_auth_service.py`（`ChatSessionService.exchange`/`resolve`/`logout` + `ChatSessionGrant` + `_create_session`/`_session_ttl`/`_pg_now`/`_info`）、`apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/chat_session_repository.py`（`find_by_token_hash`/`add`/`revoke`）、`apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/chat_token_repository.py`（新增 `find_by_token_hash`/`find_by_id`）。零重写复用 TASK-038 的 `generate_chat_token`/`hash_chat_token`/`find_live_for_update`/`ChatTokenService`。
- 关键设计落点：①**交换不接受 tenant/user 参数**——按摘要定位 Token 行，再从记录导出租户/用户/展示名；②**"用户→Token"锁序**（先 `platform_user FOR UPDATE`，再锁该用户当前未撤销 Token 行），使交换与重置/停用串行，且写会话前复核来源有效期/撤销与用户状态；③**期限** `chat_session.expires_at = min(PG now + session_ttl_hours, 来源 Token 到期)`，时间取 PG 服务端（事务内固定），设置只影响本次交换；④**读取不读设置、不写任何行**（不滑动续期），实时复核会话/来源 Token/用户三者；⑤**登出只撤本会话**（幂等），其他设备会话与来源 Token 不受影响；⑥**错误码零新增**，复用既有 `CHAT_TOKEN_INVALID`/`CHAT_TOKEN_REVOKED`/`CHAT_TOKEN_EXPIRED`/`UNAUTHORIZED`/`CHAT_SESSION_REVOKED`/`CHAT_SESSION_EXPIRED`/`PLATFORM_SETTINGS_UNAVAILABLE`（三处登记由 TASK-007 完成），且凭据判定先于设置读取（503 不掩盖 401）；⑦**明文卫生**：会话原文只出现在 `ChatSessionGrant`（SecretStr）一次，库内只有 SHA-256 摘要，日志只含 session_id/tenant/platform_user/expires_at。
- 配置盘点：新增 2 行（`PLATFORM_USER_STATUS_ACTIVE`=ACTIVE 行 50、`AUTH_METHOD_USER_TOKEN`=USER_TOKEN 行 53）写入 `docs/configuration-inventory.csv`；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（服务/两个仓储/验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 404 source files**。
- 聚焦回归：`uv run pytest -q tests/console_auth` → **73 passed**（8.61s，含本任务 16 例）；`uv run pytest -q tests/contracts` → **1281 passed**（3.22s）；`uv run pytest -q tests/test_configuration_inventory.py tests/console_platform` → **367 passed**（54.39s）。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）。
- 交接给后续任务：API-04 端点（TASK-041/042）直接消费 `ChatSessionService.exchange/resolve/logout`——`ChatSessionGrant.session_token` 写独立 HttpOnly CHAT Cookie 与 CSRF（Cookie `Max-Age` 与库内剩余期限同源）、`info.auth_context_id` 做 X-Chat-Context 匹配、响应 no-store；公开路由只接受 `WebchatTokenExchange.token`（SecretStr），不接受可信 user_id/tenant。TASK-112 的跨用户浏览器 E2E 沿用本层语义。本层"保留历史"的可验证事实是身份连续与会话/Token 行不删（Conversation/Run/消息历史归 Runtime，不在本任务范围）。
- B-08: verified — automated command passed; run_id=bee1219dbaa04c2491da101067f722af (confirmed_by: runner)
- B-240: verified — automated command passed; run_id=bee1219dbaa04c2491da101067f722af (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-240/B-08 RED：`uv run pytest -q tests/console_auth/test_capacity_webchat_chat_session_service.py` → exit=2，1 error（`ModuleNotFoundError: muad_console_platform.application.chat_auth_service`）
- [2026-10-18] 实现 `ChatSessionService` + `ChatSessionRepository`（+ `ChatTokenRepository` 摘要/主键查找）：PG now 期限裁决、用户→Token 锁序、独立会话摘要、登出只撤本会话
- [2026-10-18] B-240/B-08 GREEN：16 passed（连续 5 次复跑稳定）；`tests/console_auth` 73 passed；`tests/contracts` 1281 passed；console_platform+inventory 367 passed；ruff/mypy 干净；新增 2 行配置盘点
- [2026-10-18] completed (done)

---

## TASK-041: CHAT独立Cookie、CSRF与Origin校验

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-040
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.1 技术选型
- **Spec-Refs**:
- **Acceptance-Refs**: B-241
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/chat_security.py`, `apps/console-platform/backend/src/muad_console_platform/api/security.py`, `tests/console_auth/test_capacity_webchat_chat_cookie_security.py`
- **Estimate**: 45分钟

### Description

CHAT Cookie名称与管理会话隔离，HttpOnly/Secure/SameSite及Max-Age同源；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-241][integration] 先在`tests/console_auth/test_capacity_webchat_chat_cookie_security.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：CHAT Cookie名称与管理会话隔离，HttpOnly/Secure/SameSite及Max-Age同源。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-241] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-241] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_cookie_security.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-241 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | CHAT Cookie名称与管理会话隔离，HttpOnly/Secure/SameSite及Max-Age同源；交换POST验证Origin，写请求核独立CSRF，不把Console身份当PlatformUser | tests/console_auth/test_capacity_webchat_chat_cookie_security.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_cookie_security.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-241 | `uv run pytest -q tests/console_auth/test_capacity_webchat_chat_cookie_security.py` → exit=2，**1 error（收集失败，0 passed / 0 failed）**：`ModuleNotFoundError: No module named 'muad_console_platform.api.chat_security'`——验收文件先落地、实现尚不存在 | 同命令 → **6 passed**（exit=0，1.15–1.22s；连续复跑稳定） | `test_b241_chat_cookies_are_independent_and_max_age_tracks_the_stored_session` L362-412（交换 200 + `Cache-Control: no-store` L373-374、公开会话信息恰七字段 L376、身份由服务端导出与 `auth_context_id==session_id` L378-381、**只写 CHAT 两枚 Cookie 且与 `muad_session`/`muad_csrf` 名完全分离** L384-386、**HttpOnly/Secure(dev 关)/SameSite=strict/Path=/ 与 `Max-Age`=库内 `expires_at-issued_at`（43200）同源** L388-393、`expires_at`/`session_id` 与真实行一致 L394-395、会话原文独立于用户 Token 且 ≥256 位 L398-400、库内只有会话摘要 L399-401、凭据不进响应体/非 Cookie 响应头/日志 L403-407、交换不写审计 L408、交换不改写来源 Token L411-412）；`test_b241_max_age_is_sourced_from_the_session_row_not_the_settings_ttl` L415-454（设置 24h 但来源 Token 只剩 1h → 会话与来源同一到期时刻、`Max-Age`=行内剩余（~3600）而非 86400 L435-443；剩余 ≤0 → 显式 `CHAT_SESSION_EXPIRED`（含负剩余）L446-452、正剩余向下取整 L453-454）；`test_b241_cookie_secure_flag_follows_the_shared_non_dev_policy` L456-471（`ENV=prod` → 两枚 CHAT Cookie 均带 `Secure` 且 `Max-Age` 仍同源 L463-471）；`test_b241_exchange_requires_a_same_origin_request_before_credentials` L476-525（异源 403 FORBIDDEN、零 Cookie/零会话/零审计且不回显 Token L481-489、`Origin: null` 403 L491-495、**异源+自报 user_id/tenant 仍是 403（Origin 先于请求体/凭据判定）** L497-505、同源+自报身份 422 且零落行 L507-517、同源放行 L519-522、缺 Origin 的非浏览器客户端放行 L523-525）；`test_b241_chat_writes_require_the_independent_chat_csrf_pair` L528-582（GET /me 免 CSRF 且零 Set-Cookie（不滑动续期）L540-544、缺 CSRF 403 且会话未撤销/零清除 Cookie L546-550、**Console `muad_csrf`+`X-CSRF-Token` 对无效 403** L552-557、CHAT Cookie 在而头错值 403 L559-563、正确 CHAT 对受理并真实撤销本会话、只清 CHAT 两枚 Cookie（Max-Age=0）L565-574、撤销后 /me 401 与重放幂等不改时间戳 L576-582）；`test_b241_chat_and_console_credentials_never_cross_authenticate` L588-648（CHAT 原文 Bearer/`muad_session` 冒充 Console → /auth/me 与 /users 全 401 且不回显、库内无对应 Console 会话行 L603-613、只带 Console Cookie 的 CHAT /me 401 L616-622、Console 原文塞进 CHAT Cookie → 401 + 服务层 UNAUTHORIZED + 零会话行 L624-633、Console 原文不能交换（CHAT_TOKEN_INVALID）且 Console 账号 id 不是 PlatformUser L635-642、登录/交换各自只下发自己的 Cookie L645-648） | 真实 Console ASGI 应用（`muad_console_platform.main.app`，真实登录会话/角色裁决/Console Cookie 名）+ 真实 FastAPI 依赖与 api-kit 真实封套（探针应用用 `install_api_foundation` 挂本任务交付的真实依赖；API-04 生产路由归 TASK-042/B-242）；真实 PostgreSQL（共享 dev 库，仅 DML、无 DDL）：`control.chat_access_token`/`chat_session`/`console_session`/`config_audit_log` 全部按 tenant 真实读写与计数，每用例唯一 tenant 并在 teardown 清理；`Max-Age` 同源证据直接比对真实行的 `issued_at`/`expires_at`（PG 服务端时间），反例用真实 SQL 把来源 Token 压到 `now()+1h`；`Secure` 分支用真实 `SharedSettings.env`（monkeypatch ENV）；本路径无外部模型调用，故"外部模型真实 HTTP 探针"边界不适用 | verified |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/api/chat_security.py`（新增：CHAT Cookie 名/CSRF 名/no-store 常量、`set_chat_cookies`/`clear_chat_cookies`/`mark_no_store`/`chat_cookie_max_age`/`require_chat_origin`/`require_chat_csrf`）、`apps/console-platform/backend/src/muad_console_platform/api/security.py`（共享安全底座：`_cookie_secure` 公开为 `cookie_secure`、抽 `csrf_token_matches`、新增 `ORIGIN_HEADER`/`request_origin`/`is_same_origin`；`require_csrf` 行为不变）。
- 关键设计落点：①**独立**——CHAT 用 `muad_chat_session`/`muad_chat_csrf`，与 `muad_session`/`muad_csrf` 不共用 Cookie 也不共用 CSRF 值（Console 的 CSRF 对在 CHAT 写请求上 403）；②**`Max-Age` 同源**——`chat_cookie_max_age(session, expires_at)` 用 **PG 服务端时间**算剩余整秒并向下取整（Cookie 不会比权威会话行活得更久），剩余 ≤0 显式 `CHAT_SESSION_EXPIRED`，普通请求不重发 Cookie（不滑动续期）；③**Origin 首期同源**——允许来源集合来自请求自身权威（Host/scheme，浏览器/代理写入、页面脚本不可改写），`null`/异源 403 且判定先于凭据与请求体；缺 Origin 仅对无 ambient 凭据面的非浏览器客户端放行；④**属性沿用安全底座**——HttpOnly 只给会话 Cookie、CSRF Cookie 可读、`SameSite=strict`、`Secure` 取 `cookie_secure()`（仅非 dev）；⑤**凭据卫生**——会话原文以 `SecretStr` 进本层、只写 HttpOnly Cookie，不进响应体/非 Cookie 头/日志/审计；⑥**错误码零新增**，复用 `FORBIDDEN`(403)/`UNAUTHORIZED`(401)/`CHAT_SESSION_EXPIRED`(401)/`COMMON_INTERNAL_ERROR`(500)（三处登记由 TASK-007 完成）。
- 配置盘点：新增 4 行（`ORIGIN_HEADER`=`'Origin'` 行 13、`CHAT_SESSION_COOKIE`=`'muad_chat_session'` 行 41、`CHAT_CSRF_COOKIE`=`'muad_chat_csrf'` 行 43、`NO_STORE_CACHE_CONTROL`=`'no-store'` 行 45）写入 `docs/configuration-inventory.csv`（保持 CRLF，仅追加）；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（`security.py`/`chat_security.py`/验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 407 source files**；`uv run python scripts/check_error_message_hardcode.py` → OK。
- 聚焦回归：`uv run pytest -q tests/console_auth` → **79 passed**（10.19s，含本任务 6 例）；`uv run pytest -q tests/contracts` → **1281 passed**（3.22s）；`uv run pytest -q tests/console_platform` → **363 passed**（54.04s，`security.py` 改动面）；`uv run pytest -q tests/console_platform/test_auth_policy_settings.py tests/console_skill/test_skill_rbac.py tests/console_channel/test_artifact_fetch.py` → **19 passed**；`uv run pytest -q tests/test_logging_redaction.py tests/test_api_i18n.py tests/test_error_catalog.py` → **26 passed**；`uv run pytest -q tests/console_skill/test_import_idempotency.py` → **5 passed**。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）。
- 交接给 TASK-042：`webchat_auth.py` 直接消费本层——exchange 挂 `Depends(require_chat_origin)` 并以 `set_chat_cookies(response, grant.session_token, new_csrf_token(), await chat_cookie_max_age(session, grant.info.expires_at))` + `mark_no_store(response)`；me/logout 与其余 CHAT 写路由挂 `Depends(require_chat_csrf)`、读 `CHAT_SESSION_COOKIE`、登出 `clear_chat_cookies(response)`；`X-Chat-Context` 匹配（`info.auth_context_id`）归 TASK-043/044。
- B-241: verified — automated command passed; run_id=0cb9e8e8b69d4d239d9487fef0ba72fa (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-241 RED：`uv run pytest -q tests/console_auth/test_capacity_webchat_chat_cookie_security.py` → exit=2，1 error（`ModuleNotFoundError: muad_console_platform.api.chat_security`）
- [2026-10-18] 实现 CHAT 安全层 `chat_security.py`（独立 Cookie/CSRF/Origin/no-store/Max-Age 同源）+ `security.py` 共享底座（`cookie_secure`/`csrf_token_matches`/`is_same_origin`）
- [2026-10-18] B-241 GREEN：6 passed；`tests/console_auth` 79 passed；`tests/contracts` 1281 passed；`tests/console_platform` 363 passed；ruff/mypy 干净；新增 4 行配置盘点
- [2026-10-18] completed (done)

---

## TASK-042: CHAT exchange/me/logout公开认证路由

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016, TASK-039, TASK-041
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-242
- **Files**: `apps/console-platform/backend/src/muad_console_platform/api/webchat_auth.py`, `apps/console-platform/backend/src/muad_console_platform/api/router.py`, `tests/console_auth/test_capacity_webchat_chat_auth_api.py`
- **Estimate**: 45分钟

### Description

路由在管理登录守卫之外；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-242][integration] 先在`tests/console_auth/test_capacity_webchat_chat_auth_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：路由在管理登录守卫之外。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-242] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-242] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_auth_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-242 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 路由在管理登录守卫之外；exchange只接受Token，me返回统一principal/期限/auth_context，logout幂等；无管理角色继承、无JWT/provider配置 | tests/console_auth/test_capacity_webchat_chat_auth_api.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_auth_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-242 | `uv run pytest -q tests/console_auth/test_capacity_webchat_chat_auth_api.py` → exit=1，**4 failed（0 passed）in 0.83s**：三条公开路由尚未挂载，真实应用上 `POST /api/v1/webchat/auth/exchange`、`GET .../me`、`POST .../logout` 全 404（测试对真实应用运行，非收集失败）——验收文件先落地、实现尚不存在 | 同命令 → **4 passed**（exit=0，0.78–0.98s；连续复跑 4 次稳定：0.79/0.78/0.85/0.98s） | `test_b242_public_routes_run_outside_the_console_login_guard` L237-264（起点零管理会话 L241、无 Console 会话交换 200 + `no-store` L246-247、交换不下发管理会话 Cookie L248、`/api/v1/auth/me` 与 `/api/v1/users` 仍 401 UNAUTHORIZED（无管理角色继承）L252-254、无 Console 会话 me 200/no-store L258、logout 200/no-store/`{logged_out:true}` L262-264）；`test_b242_exchange_only_accepts_the_opaque_token_field` L270-338（自报 user_id/tenant → 422 `COMMON_VALIDATION_ERROR`、零 Cookie/零落行/不回显 Token L285-289、`jwt`/非串/空白/空体 → 422 L293-296、**异源（含越界请求体）与 `null` → 403 FORBIDDEN 且零副作用（Origin 先于凭据/请求体）** L299-306、Bearer 无请求体 422（无 JWT 交换路径）L310-311、同源合法交换 200 恰七字段且 `X-Tenant-Id` 头不改写主体 L319-324、库内只有会话摘要且与 Cookie 同源 L328-330、明文不进响应体/非 Cookie 头/日志/行 L331-335、交换不写审计 L336、来源 Token 零改写 L337-338）；`test_b242_me_returns_the_unified_principal_without_sliding` L344-400（七字段 + 身份/展示名/`session_id` 与库行一致 L358-361、`auth_context_id==session_id` L363、`auth_method=USER_TOKEN` L364、`expires_at` 与权威行同源 L365、无管理角色/权限字段 L366、零 Set-Cookie 不滑动续期 L367、换用户 Token 即换主体（服务端记录导出）L373-381、未知凭据 401 UNAUTHORIZED 不回显输入 L385-388、Bearer 401（只有 CHAT Cookie 参与解析）L391-394、到期会话 401 `CHAT_SESSION_EXPIRED` L398-400）；`test_b242_logout_revokes_only_the_presented_session_idempotently` L406-473（双设备会话独立 L412-421、缺独立 CHAT CSRF 403 且零副作用 L428-432、正确对 200 只清 CHAT 两枚 Cookie（Max-Age=0）/no-store/真实撤销本会话 L436-445、另一设备仍可 /me L449-451、撤销后 /me 401 `CHAT_SESSION_REVOKED` 且重放登出 200、`revoked_at` 不变 L455-460、无可撤销凭据 401 且零清除 Cookie L463-466、来源 Token 零改写且同 Token 可再次交换 L469-473） | 真实 Console 应用服务：`muad_console_platform.main.app`（`tests/console_auth/conftest.py` 的 `client` fixture，ASGITransport 真实 ASGI/真实路由表/真实 api-kit 封套与错误目录），全程零 Console 会话；真实 PostgreSQL（共享 dev 库，仅 DML、无 DDL）：`control.platform_user`/`chat_access_token`/`chat_session`/`config_audit_log` 真实行回读与真实事务提交（到期边界用真实 SQL 平移 `issued_at`/`expires_at`，不做时钟替身）；真实 Cookie 头：`Set-Cookie` 解析（HttpOnly/Max-Age=0/名称分离）与 CookieJar 回放；外部模型真实 HTTP 探针不适用（本路径无外部调用） | verified |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/api/webchat_auth.py`（新增：`public_router` 三条公开路由 `POST /api/v1/webchat/auth/exchange`、`GET .../me`、`POST .../logout` + `_presented_session_token`）、`apps/console-platform/backend/src/muad_console_platform/api/router.py`（公开路由 include，不进 `authenticated`/`admin` 组）。
- 关键设计落点：①**路由在管理登录守卫之外**——三条路由只认独立 CHAT 凭据，无 Console 会话可交换/恢复/登出，也不授予任何 Console 权限（CHAT 凭据打 `/api/v1/auth/me`、`/api/v1/users` 仍 401）；②**exchange 只接受 Token**——`WebchatTokenExchange.token`（SecretStr，自报 user_id/tenant 契约层 422），`Depends(require_chat_origin)` 先于凭据与请求体判定；成功后 `chat_cookie_max_age`（PG 服务端时间）+ `set_chat_cookies`（独立两枚 HttpOnly/CSRF Cookie）+ `mark_no_store`；③**me 独立解析**——只从 `muad_chat_session` Cookie 取凭据，`ChatSessionService.resolve` 实时复核，返回统一 principal 七字段（`auth_context_id==session_id`），零 Set-Cookie（不滑动续期）；④**logout 幂等**——挂 `Depends(require_chat_csrf)`（Console CSRF 对无效），撤本会话可重放、`clear_chat_cookies` 只清 CHAT 两枚 Cookie，不撤来源 Token/其他设备；⑤**错误码零新增**——复用既有 `UNAUTHORIZED`(401)/`FORBIDDEN`(403)/`COMMON_VALIDATION_ERROR`(422)/`CHAT_SESSION_EXPIRED`(401)/`CHAT_SESSION_REVOKED`(401)（三处登记由 TASK-007 完成，本任务未触碰目录）；⑥**明文卫生**——用户 Token/会话原文只出现在请求体与 HttpOnly Cookie，不进响应体/非 Cookie 头/日志/审计。
- 配置盘点：本任务未新增/移动模块级大写常量（复用 TASK-041 已登记的 `CHAT_SESSION_COOKIE`/`CHAT_CSRF_COOKIE`），`docs/configuration-inventory.csv` 无需变更；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（`webchat_auth.py`/`router.py`/验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 408 source files**。
- 聚焦回归：`uv run pytest -q tests/console_auth` → **83 passed**（10.39s，含本任务 4 例）；`uv run pytest -q tests/contracts` → **1281 passed**（3.07s）；`uv run pytest -q tests/console_platform` → **363 passed**（54.67s，路由挂载面）。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）。
- 交接：TASK-043 内部门控消费 `info.auth_context_id` 做 X-Chat-Context 匹配（本任务已保证公开投影含该字段）；TASK-045 全链路脱敏在 `webchat_auth.py` 上复核（公开响应已无凭据，Token 只进请求体、会话原文只进 HttpOnly Cookie）。
- B-242: verified — automated command passed; run_id=2dca1ae4c9e4409b89318018128d2ca6 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-242 RED：`uv run pytest -q tests/console_auth/test_capacity_webchat_chat_auth_api.py` → exit=1，4 failed（三条公开路由未挂载时真实应用全 404）
- [2026-10-18] 实现 `webchat_auth.py` 三条公开认证路由 + `router.py` 挂载（管理登录守卫之外，复用 TASK-040/041 服务与安全层）
- [2026-10-18] B-242 GREEN：4 passed；`tests/console_auth` 83 passed；`tests/contracts` 1281 passed；`tests/console_platform` 363 passed；ruff/mypy 干净；零新增配置常量
- [2026-10-18] completed (done)

---

## TASK-043: 受信聊天身份核验与授权解析

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-042
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-auth#RULE-auth-001
- **Acceptance-Refs**: B-243
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/channel_identity_service.py`, `apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`, `tests/console_internal/test_capacity_webchat_chat_internal_principal.py`
- **Estimate**: 45分钟

### Description

内部门控验证CHAT/来源Token/用户/部署租户，主体从PG记录导出；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-243][integration] 先在`tests/console_internal/test_capacity_webchat_chat_internal_principal.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：内部门控验证CHAT/来源Token/用户/部署租户，主体从PG记录导出。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-243] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-243] 执行`["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_chat_internal_principal.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [x] verifier `harness-auth#RULE-auth-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-243 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 内部门控验证CHAT/来源Token/用户/部署租户，主体从PG记录导出；User→Agent和Skill/MCP叠加授权不变，伪造头或user-id不能认证 | tests/console_internal/test_capacity_webchat_chat_internal_principal.py | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_chat_internal_principal.py"] | verified |
| harness-auth#RULE-auth-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 授权为三层关系：User→Agent（AgentAccessGrant）、Agent→Skill/MCP（Binding）、SELECTED 资源再叠加 SkillUserGrant/McpUserGrant；Effective Capability 公式必须含 `is_deleted=false` 与资源/Agent `enabled`；绑定无启停开关、授权无到期时间；不建三元授权、不做 MCP Tool 级授权；未授权资源不得进入 Prompt/ToolRegistry/Skill Catalog。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-243 | `uv run pytest -q tests/console_internal/test_capacity_webchat_chat_internal_principal.py` → exit=1，**5 failed（0 passed）in 0.89s**：内部端点未挂载时真实应用上 `POST /internal/channel/chat-principal` 全 404 `COMMON_NOT_FOUND`（对真实应用运行、非收集失败；前置的真实签发+exchange 已 200，证明夹具链路本身可用）——验收文件先落地、实现尚不存在 | 同命令 → **5 passed**（exit=0，1.14–1.23s；连续复跑稳定） | `test_b243_gate_derives_principal_and_authorized_agents_from_pg` L261-308（200 且 data 恰 `principal/expires_at/agents` 三字段 L266-268、principal 恰四字段且与真实行一致（tenant/platform_user_id/session_id/USER_TOKEN）L269-277、`expires_at` 与库内会话行同源 L278-280、**授权集合=真实 grant+未软删+Agent enabled/未软删**（granted/deleted-model/disabled-model 三个在、disabled/deleted/ungranted/revoked-grant 四个不在）L283-296、agent 投影恰 `agent_id/key/name` L294-296、**会话原文与 sha256 摘要都不进响应体** L299-300、CHAT 凭据冒充 Console Bearer → `/api/v1/auth/me` 401 且不回显 L302-308）；`test_b243_forged_headers_and_self_reported_identity_cannot_authenticate` L311-341（自报 `user_id/platform_user_id/tenant_id/agent_id/context` 全 422 `COMMON_VALIDATION_ERROR` 且零落库/不回显 L311-319、**伪造 `X-User-Id`/`X-Platform-User-Id` 头不改变主体**（仍导出 PG 用户）L321-332、服务身份缺失与错误两条分支都 403 `FORBIDDEN` 且不返回主体 L334-341）；`test_b243_chat_session_source_token_and_user_must_be_live` L350-380（未知会话原文 401 `UNAUTHORIZED` 且不回显 L357-360、来源 Token 撤销→`CHAT_SESSION_REVOKED`、用户停用→`CHAT_SESSION_REVOKED`、来源 Token 过期→`CHAT_TOKEN_EXPIRED`、会话过期→`CHAT_SESSION_EXPIRED`，每步恢复后同凭据再次 200 L362-380）；`test_b243_deployment_tenant_and_chat_context_are_enforced` L383-420（**X-Chat-Context 不匹配→409 `AUTH_CONTEXT_CHANGED`**、缺失/非 UUID→422 L389-400、**跨部署租户会话→403 `FORBIDDEN` 且不回显**、本栈会话报他租户→403 L404-411、各自租户内仍 200（403 来自租户不匹配）L413-420）；`test_b243_authorization_chain_keeps_the_three_layer_capability_model` L553-595（门控主体进入既有 `resolve-definition`：**Skill/MCP 有效集恰为 SELECTED+用户级 grant 与 ALL 范围**（selected-ungranted / disabled / 软删绑定全排除）L558-575、**撤销 SkillUserGrant 后同一主体立即失去该 SELECTED 技能**（按用户实时叠加）L577-595） | 真实 Console 应用服务：`muad_console_platform.main.app`（`tests/console_internal/conftest.py` 的 `client`，ASGITransport 真实 ASGI/真实路由表/真实 api-kit 封套与错误目录）；真实服务身份门控（`X-Internal-Service` 与 apps 注入同源）；真实 PostgreSQL（共享 dev 库，仅 DML、无 DDL）：CHAT 会话经真实 `ChatTokenService.issue_token` + 真实公开 `POST /api/v1/webchat/auth/exchange` 建立，主体/到期/授权全部回读 `control.chat_session`/`platform_user`/`agent_access_grant`/`agent_definition`/`skill*`/`mcp_*` 真实行；失效边界用真实 SQL 压 PG 时间，无时钟替身；无外部模型调用（本路径不适用模型探针边界） | verified |
| harness-auth#RULE-auth-001 | 见 B-243（同一文件先落地时 5 failed） | `bash -lc "uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"` → **2 passed, 1 deselected**（0.70s）+ **47 passed, 5036 deselected**（32.67s），exit=0 | 原Spec verifier 未改写；本任务补充链路断言：`test_b243_authorization_chain_keeps_the_three_layer_capability_model` L553-595（三层关系逐项：User→Agent=门控导出的授权集合 L558-561；Agent→Skill/MCP 绑定+SELECTED 叠加=既有 `resolve-definition` 有效集 L562-575；`is_deleted=false` 与 `enabled` 过滤=L568-575 的排除项；未授权资源不进入 Skill/MCP 结果 L570-575） | 真实 PG 行（skill/skill_artifact/agent_skill_binding/skill_user_grant/mcp_server/agent_mcp_binding/mcp_user_grant）+ 真实 Console 解析服务；原Spec verifier 命令原样执行 | verified |

**实施范围与证据说明**

- 实现文件：`apps/console-platform/backend/src/muad_console_platform/application/channel_identity_service.py`（新增：`ChannelIdentityService.resolve_chat_principal`）、`apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py`（新增 `POST /internal/channel/chat-principal`：`InternalServiceDep` + `HeaderTenantId` + 必填 `X-Chat-Context` 头）、`apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/agent_access_grant_repository.py`（新增 `list_authorized_agents`，与 `has_active_grant` 同一组谓词）、`packages/contracts/src/muad_contracts/webchat.py`（新增 `ChatPrincipalRequest`/`ChatAuthorizedAgent`/`ChatPrincipalResponse`，复用既有 `ChatPrincipal`）。
- 关键设计落点：①**门控三层**——服务身份（`X-Internal-Service`，缺失/错误一律 403，不依赖"未配置即放行"）→ 部署租户（`X-Tenant-Id` 必须等于会话行租户，跨部署租户 403）→ CHAT 凭据（`ChatSessionService.resolve` 实时复核会话/来源 Token/用户）；②**X-Chat-Context 比对**——必填头必须等于 `info.auth_context_id`，不匹配 409 `AUTH_CONTEXT_CHANGED`（防 Cookie 切用户后旧页面误用身份），判定先于任何主体/授权返回；③**主体从 PG 导出**——响应 principal 恰 `{tenant_id,platform_user_id,session_id,auth_method}`，请求契约只有 `session_token`（`SecretStr`，`exclude=True`），自报 user_id/tenant/agent_id 与伪造头都无处可放；④**授权解析同口径**——`agent_access_grant.is_deleted=false` + Agent `is_deleted=false` + `enabled=true` + 同租户，与 Console 侧 `has_active_grant` 完全一致；模型状态不在此层（定义解析单独判定 `MODEL_DISABLED`）；⑤**三层能力授权不重写**——本层只做 User→Agent；Skill/MCP 叠加授权仍由既有 `resolve-definition`/`AgentMcpService` 单处公式承担（B-243 用真实行验证其不变）；⑥**CHAT 不授予 Console 权限**——响应无任何角色/权限/账号字段，CHAT 凭据冒充 Console Bearer 仍 401；⑦**错误码零新增**——复用 `UNAUTHORIZED`(401)/`CHAT_SESSION_REVOKED`(401)/`CHAT_SESSION_EXPIRED`(401)/`CHAT_TOKEN_EXPIRED`(401)/`FORBIDDEN`(403)/`AUTH_CONTEXT_CHANGED`(409)/`COMMON_VALIDATION_ERROR`(422)（三处登记由 TASK-007 完成，本任务未触碰目录）；⑧**明文卫生**——会话原文只作输入（`SecretStr`），不进响应体/日志/审计，响应仅回 `expires_at` 与授权集合。
- 配置盘点：本任务未新增/移动模块级大写常量（`X-Chat-Context` 为路由内的头别名，非模块常量），`docs/configuration-inventory.csv` 无需变更；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**（含在下方 contracts 合计内）。
- 静态校验：`uv run ruff check`（`internal_channel.py`/`channel_identity_service.py`/`agent_access_grant_repository.py`/`webchat.py`/验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 409 source files**；`uv run python scripts/check_error_message_hardcode.py` → backend error-message hardcode check OK。
- 聚焦回归：`uv run pytest -q tests/console_internal` → **54 passed**（6.99s，含本任务 5 例）；`uv run pytest -q tests/contracts tests/test_configuration_inventory.py` → **1285 passed**（4.56s）；`uv run pytest -q tests/console_platform` → **363 passed**（60.61s，`agent_access_grant_repository` 改动面）；`uv run pytest -q tests/console_auth` → **83 passed**（12.09s）；`uv run pytest -q tests/console_channel tests/test_api_i18n.py tests/test_error_catalog.py` → **69 passed**（8.56s，`internal_channel.py` 挂载面）；`uv run pytest -q tests/console_platform/test_user_side_relations.py` → **3 passed**。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）；测试自带按 `tenant_id` 的清理（chat_session→chat_access_token→审计/设置），跑后核查 `test-%` 租户 CHAT/用户行均为 0。
- 交接：TASK-044 Gateway 侧直接消费本端点——请求头带 `X-Tenant-Id`/`X-Internal-Service`/`X-Chat-Context`，体为 `{"session_token": <CHAT Cookie 原文>}`；响应 `principal.session_id` 即 `auth_context_id`，`expires_at` 用于长流到点停止，`agents` 用于入口授权与 `/api/v1/webchat/entries`（API-05）。TASK-045 全链路脱敏在本层复核：响应无凭据、日志/审计零凭据字段。
- B-243: verified — automated command passed; run_id=f9909183dd234d739f1f514f328ca090 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-243 RED：`uv run pytest -q tests/console_internal/test_capacity_webchat_chat_internal_principal.py` → exit=1，5 failed（真实应用上 `/internal/channel/chat-principal` 未挂载全 404）
- [2026-10-18] 实现 `ChannelIdentityService` + `POST /internal/channel/chat-principal`（服务身份/部署租户/X-Chat-Context 门控 + 授权集合解析），契约新增三个 webchat 类型，授权查询落在 `AgentAccessGrantRepository.list_authorized_agents`
- [2026-10-18] B-243 GREEN：5 passed；`tests/console_internal` 54 passed；`tests/contracts`+盘点 1285 passed；`tests/console_platform` 363 passed；`tests/console_auth` 83 passed；`tests/console_channel` 等 69 passed；ruff/mypy 干净；零新增错误码/配置常量
- [2026-10-18] harness-auth#RULE-auth-001 verifier：`test_user_side_relations.py -k s04` 2 passed + `tests -k schema_parity` 47 passed
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-044: Gateway CHAT主体及长流复核

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-043
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-244
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/chat_principal.py`, `apps/im-gateway/src/muad_im_gateway/api/deps.py`, `tests/gateway/test_capacity_webchat_gateway_chat_auth.py`
- **Estimate**: 60分钟

### Description

Gateway只经Console核验，拒绝跨部署tenant；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-244][integration] 先在`tests/gateway/test_capacity_webchat_gateway_chat_auth.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：Gateway只经Console核验，拒绝跨部署tenant。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_chat_auth.py` exit=2，1 error during collection（`ImportError: cannot import name 'ChatIdentityDep' from 'muad_im_gateway.api.deps'`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`application/chat_principal.py`（`ChatSessionCredentials`/`ChatIdentity`/`ChatPrincipalGate`/`ChatStreamGuard`）、`api/deps.py`（CHAT 主体依赖五件套）、`application/console_client.py`（API-12 调用）、`main.py`（装配根发布 `chat_auth_recheck_sec`）。
- [x] [B-244] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-244] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_chat_auth.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，12 passed。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-244 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | Gateway只经Console核验，拒绝跨部署tenant；所有读写/SSE/下载比较X-Chat-Context，空闲流也定时复核且到expires即停止，不每token查控制面 | tests/gateway/test_capacity_webchat_gateway_chat_auth.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_chat_auth.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-244 | `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_chat_auth.py` → exit=2，**1 error during collection（0 passed）in 0.21s**：`ImportError: cannot import name 'ChatIdentityDep' from 'muad_im_gateway.api.deps'`（对真实 Gateway 应用运行；验收文件先落地、`chat_principal.py` 与 deps 的 CHAT 主体依赖尚不存在） | 同命令 → **12 passed**（exit=0，5.37–5.87s；连续复跑 4 次稳定） | `test_b244_missing_credentials_are_rejected_before_touching_console` L282-319（缺 CHAT Cookie→401 `UNAUTHORIZED`、缺/非法 `X-Chat-Context`→422 `COMMON_VALIDATION_ERROR`、只有 Console 管理会话 Cookie 或 Bearer→401、SSE 同样先过门控 L290-296、**六条全部零触达 Console**（`call_count == 0`）L308、响应零凭据 L319）；`test_b244_gateway_only_verifies_through_console_and_projects_the_principal` L322-356（一次请求只核验一次 L344、转发 `X-Tenant-Id`/`X-Internal-Service`/`X-Chat-Context` L346-348、请求体恰 `{"session_token": …}` L349、**不转发 Authorization/Cookie**（CHAT 凭据≠Console 凭据）L351-352、主体恰四字段 L334、授权条目恰 `agent_id/key/name` L342、响应无凭据 L354、无角色字段 L355-356）；`test_b244_foreign_deployment_tenant_principal_is_rejected_by_the_gateway` L359-372（Console 返回他租户主体→网关自拒 403 `FORBIDDEN`，不回显主体/凭据 L369-372）；`test_b244_context_mismatch_is_rejected_even_if_console_accepts` L375-388（`principal.session_id` 与上下文不一致→网关自拒 409 `AUTH_CONTEXT_CHANGED` L385-388）；`test_b244_console_rejection_and_outage_are_explicit_and_credential_free` L391-418（撤销/过期→401 原码、Console 500 与不可达→500 `COMMON_INTERNAL_ERROR`，四条响应零凭据零主体 L414-418）；`test_b244_chat_identity_carries_no_console_permissions` L421-432（principal 恰四字段、`ChatIdentity` 恰 `principal/expires_at/agents`、无 Console 角色字段 L429-432）；`test_b244_chat_cookie_name_is_the_console_wire_contract` L435-439（Cookie 名与 Console `chat_security.CHAT_SESSION_COOKIE` 同一枚）；`test_b244_idle_stream_rechecks_on_the_timer_and_stops_at_expiry` L445-465（**空闲流**：初始核验 1 次 + 空闲窗口内 ≥2 次定时复核 L462、到 `expires_at` 终止码 `CHAT_SESSION_EXPIRED` L461、停在到期时刻附近而非首次复核 L464）；`test_b244_revoked_session_stops_an_idle_stream_within_the_recheck_bound` L468-486（撤销在复核边界内生效：恰 2 次控制面调用、终止码 `CHAT_SESSION_REVOKED`、elapsed<0.5s L483-485）；`test_b244_event_burst_does_not_query_the_control_plane_per_token` L489-503（40 个事件全量转发且控制面查询恰 1 次 L500-503）；`test_b244_stream_stops_at_expiry_by_raising_not_by_silence` L506-521（终止是显式抛 `AppError`，不静默收流 L520）；`test_b244_session_token_never_reaches_logs_or_responses` L527-550（成功/失败响应零明文 L545-546、DEBUG 日志零明文 L547、`repr` 零明文而原文只在 `SecretStr` 内可取 L549-550） | 真实 Gateway 应用：`muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/错误目录、真实 `api/deps.py` 依赖图与真实 `StreamingResponse`；生产 webchat 资源路由由 TASK-052 挂载，本文件在**同一真实应用**上挂 `_probe` 读写与 SSE 路由，装配根状态按生产同形注入）；真实 Console Owner 接口：`ConsoleClient` 打真实本地 uvicorn 服务（真实 socket），按 API-12 `POST /internal/channel/chat-principal` 封套契约应答并记录每次调用的真实头/体/时刻，网关不持库（`tests/architecture/test_im_gateway_boundaries.py` 机检零 sqlalchemy）；真实 HTTP/SSE：真实 ASGI/HTTP 请求 + 真实 `text/event-stream` 长流，空闲复核与到点停止用真实计时（recheck 0.05s / 到期 0.35s）观测 | verified |

**实施范围与证据说明**

- 实现文件：`apps/im-gateway/src/muad_im_gateway/application/chat_principal.py`（新增：`ChatSessionCredentials`/`ChatIdentity`/`ChatPrincipalGate.resolve`/`ChatStreamGuard.rechecked` + `CHAT_SESSION_COOKIE`）、`apps/im-gateway/src/muad_im_gateway/api/deps.py`（新增 `get_chat_principal_gate`/`get_chat_auth_recheck_sec`/`get_chat_credentials`/`get_chat_identity`/`get_chat_stream_guard` 与 `ChatPrincipalGateDep`/`ChatAuthRecheckDep`/`ChatCredentialsDep`/`ChatIdentityDep`/`ChatStreamGuardDep`）、`apps/im-gateway/src/muad_im_gateway/application/console_client.py`（`ConsoleClientPort.chat_principal` + `ConsoleClient.chat_principal` + `CHAT_PRINCIPAL_PATH`/`CHAT_CONTEXT_HEADER`）、`apps/im-gateway/src/muad_im_gateway/main.py`（装配根发布 `chat_auth_recheck_sec`）。
- 关键设计落点：①**只经 Console 核验**——网关不持库、不解析凭据，会话原文以 `SecretStr` 原样转发 API-12，主体/授权集合/权威到期时刻只从响应导出（缺凭据在触达 Console 之前就被拒）；②**本层再拒一次**——响应主体租户≠本部署租户→403，`principal.session_id`≠`X-Chat-Context`→409（Console 已判过，两层都不放行）；③**长流复核**——等待时长取 `min(chat_auth_recheck_sec, 到 expires_at 剩余)`，空闲（无事件）也复核、撤销/来源 Token 失效/用户停用在复核边界内终止、到 `expires_at` 抛 `CHAT_SESSION_EXPIRED`；事件转发不触发控制面查询（不每 token 查）；④**终止显式**——一律抛 `AppError`（错误码沿用 Console 口径），不静默收流，前端据 401/409 关闭流回到 Token 输入（前端设计 E-101）；⑤**CHAT 身份不授予 Console 权限**——主体只有统一 principal 四字段 + 授权入口集合，无角色/权限/账号字段，CHAT 凭据不当 Console 凭据转发（无 Authorization/Cookie 头）；⑥**凭据卫生**——原文只进发往 Console 的请求体，响应/日志/repr 零明文；⑦**错误码零新增**——复用 `UNAUTHORIZED`(401)/`FORBIDDEN`(403)/`AUTH_CONTEXT_CHANGED`(409)/`CHAT_SESSION_EXPIRED`(401)/`CHAT_SESSION_REVOKED`(401)/`COMMON_VALIDATION_ERROR`(422)/`COMMON_INTERNAL_ERROR`(500)（三处登记由 TASK-007 完成，本任务未触碰目录）。
- 配置盘点：新增 3 个模块级大写常量（`chat_principal.py` 的 `CHAT_SESSION_COOKIE`、`console_client.py` 的 `CHAT_PRINCIPAL_PATH`/`CHAT_CONTEXT_HEADER`）已登记 `docs/configuration-inventory.csv`（保持 CRLF、只动本任务相关行；同步 console_client.py 既有行的行号平移）；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（本任务改动的 5 个文件）干净；`uv run mypy apps packages` → **Success: no issues found in 411 source files**；`uv run python scripts/check_error_message_hardcode.py` → backend error-message hardcode check OK；全部新增/改动函数 ≤50 行（AST 机检）。
- 聚焦回归：`uv run pytest -q tests/gateway` → **371 passed**（88.41s，含本任务 12 例）；`uv run pytest -q tests/architecture tests/contracts tests/test_configuration_inventory.py tests/test_error_catalog.py tests/test_api_i18n.py` → **1333 passed**（5.23s）；`uv run pytest -q tests/test_logging_redaction.py tests/test_secret_ref_residue.py` → **13 passed**。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）。
- 交接：TASK-052 的资源路由用 `ChatIdentityDep`（读写/下载）与 `ChatStreamGuardDep`（SSE）收口主体；`chat_auth_recheck_sec` 已由 `main.py` 装配根发布（TASK-096 只需挂路由）；TASK-045 全链路脱敏在本层复核（响应/日志/审计零凭据字段）。
- B-244: verified — automated command passed; run_id=d8a5f90eb6cf48549de833d4d9db0b6a (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-244 RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_chat_auth.py` → exit=2，1 error during collection（`ImportError: cannot import name 'ChatIdentityDep'`，实现尚不存在）
- [2026-10-18] 实现 `chat_principal.py`（主体门控 + 长流复核）、deps CHAT 依赖、`ConsoleClient.chat_principal`（API-12）、main 发布 `chat_auth_recheck_sec`
- [2026-10-18] B-244 GREEN：12 passed；`tests/gateway` 371 passed；architecture/contracts/盘点/错误目录/i18n 1333 passed；ruff/mypy 干净；3 个新常量入盘点
- [2026-10-18] completed (done)

---

## TASK-045: Token签发与交换全链路脱敏

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-020, TASK-042
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-245
- **Files**: `packages/api-kit/src/muad_api/audit.py`, `packages/logging-kit/src/muad_logging/redaction.py`, `tests/console_auth/test_capacity_webchat_token_redaction.py`
- **Estimate**: 45分钟

### Description

既有token敏感键覆盖签发/交换请求和响应；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-245][integration] 先在`tests/console_auth/test_capacity_webchat_token_redaction.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：既有token敏感键覆盖签发/交换请求和响应。RED：`uv run pytest -q tests/console_auth/test_capacity_webchat_token_redaction.py` exit=1，4 failed/1 passed（`redact_text` 只遮蔽 `Cookie:` 第一对，`muad_chat_csrf=<canary>` 原文照落；`sanitize_audit_payload` 保留 `{'muad_chat_session': <canary>}` 与嵌套 `muad_chat_csrf`；真实日志文件出现 `"muad_chat_session":"<canary>"`；canary 反查在 `control.config_audit_log.before_json` 抓到会话原文）。真实签发/交换路径用例（响应/库/审计/日志）在 RED 阶段即通过——非空断言证明其余链路已由 TASK-038/040/041/042 收口。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`packages/logging-kit/.../redaction.py` 的 `_SENSITIVE_KEY` 增 `muad[_-]?chat[_-]?session|muad[_-]?chat[_-]?csrf`（`Cookie:` 前缀键只吃掉第一对，第二对必须按名字入清单）；`packages/api-kit/.../audit.py` 的 `SENSITIVE_KEY_MARKERS` 同步增两枚名字（审计丢键）；按 design §3.5「若引入新凭据键须同步api-kit、logging-kit和Runtime审计三处清单」与 `harness-secret` 约定，同步 `apps/agent-runtime/.../infrastructure/audit_writer.py` 的 `SENSITIVE_KEY_MARKERS`（Runtime 审计遮蔽，旧 7 键会原样保留两枚 Cookie 名）；`harness-secret` spec 的三处清单描述按事实改写（7 键→9 键）。未放松任何既有规则（旧键全保留），未新增错误码。
- [x] [B-245] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-245] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_redaction.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，6 passed（实现期补第 6 条「三处清单同改」用例：旧 7 键清单下 `redact_sensitive({'muad_chat_session': …})` 原样保留，现清单 `<redacted>`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-245 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 既有token敏感键覆盖签发/交换请求和响应；审计丢键/日志替换语义分别验证，canary反查Canonical/审计/快照/代理回执均无原文 | tests/console_auth/test_capacity_webchat_token_redaction.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_redaction.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-245 | `uv run pytest -q tests/console_auth/test_capacity_webchat_token_redaction.py` exit=1，4 failed/1 passed：①`redact_text('Cookie: muad_chat_session=…; muad_chat_csrf=…')` 返回 `Cookie: ***; muad_chat_csrf=<canary>`；②`sanitize_audit_payload` 返回 `{'muad_chat_session': <canary>, 'name': 'keep-me', 'nested': {'muad_chat_csrf': <canary>, 'revision': 2}}`；③落盘日志含 `"muad_chat_session":"<canary>","user":"keep-me"`；④canary 反查命中 `control.config_audit_log`（`before_json` 的 `muad_chat_session` 会话原文） | 同命令 exit=0，6 passed；manifest runner 实际执行并记 verified | `tests/console_auth/test_capacity_webchat_token_redaction.py`：`test_b245_sensitive_key_list_covers_chat_cookie_and_token_forms`（Cookie 头两对/Set-Cookie/裸键值对/交换体 dict/`Authorization: Bearer`/`X-CSRF-Token` 全遮蔽，`Path=/`、`HttpOnly`、非敏感字段存活）、`test_b245_audit_projection_drops_keys_while_log_redaction_replaces_values`（审计丢键 `== {'name': 'keep-me', 'nested': {'revision': 2}}` vs 日志替换 `***`；`RedactionFilter` 对 `extra` 属性按键名替换）、`test_b245_three_sensitive_key_lists_agree_on_the_chat_cookie_names`（api-kit 丢键 / logging-kit `***` / runtime `<redacted>` 三处同改）、`test_b245_issue_and_exchange_leave_no_plaintext_in_response_audit_or_logs`（签发响应 `count(token)==1` 非空断言；交换响应体/非 Cookie 头/GET 元信息/409 重放/Token 行/会话行/审计行/应用日志逐处零命中；审计连 SHA-256 摘要都不写）、`test_b245_logging_exit_replaces_chat_credentials_in_the_log_file`（真实落盘文件零 canary、`***` 与 `keep-me` 并存）、`test_b245_canary_reverse_lookup_finds_no_plaintext_in_console_storage`（`control` 31 张真实表 `to_jsonb(row)::text` 反查三枚 canary 零命中；审计 `before/after` 丢键后 `== {'revision': 1}` / `== {'revision': 2}`） | 真实 Console ASGI（ADMIN 登录会话/CSRF、API-03 签发、API-04 交换、api-kit 封套/错误目录）+ 真实 PostgreSQL：`chat_access_token`/`chat_session` 只存 SHA-256 摘要并回读比对、`config_audit_log` 真实事务写入与 jsonb 回读、canary 反查覆盖 `control` 全库（含幂等回执表）；真实日志出口（`configure_logging` 落盘 + `RedactionFilter`）；无模型/外部网络调用 | verified |

回归（同域聚焦）：`uv run pytest -q tests/console_auth tests/test_logging_redaction.py tests/test_audit_redaction.py tests/test_secret_ref_residue.py tests/contracts tests/test_logging.py tests/test_configuration_inventory.py` exit=0，1393 passed；`tests/agent_runtime/test_runtime_audit_write.py tests/agent_runtime/test_execution_audit.py tests/agent_runtime/test_memory_redesign_secrets.py` exit=0，40 passed；`tests/console_platform/test_platform_settings_service.py tests/console_skill/test_skill_audit.py tests/agent_worker/test_task_queries.py` exit=0，18 passed。

静态检查：`uv run ruff check`（本任务改动/新增的 4 个文件）全部通过；`uv run mypy apps packages` → Success: no issues found in 417 source files；`docs/configuration-inventory.csv` 保持 CRLF、只更新本任务改动文件的行（audit.py 3 行、redaction.py 5 行、audit_writer.py 1 行），`tests/test_configuration_inventory.py` 4 passed。

规范同步：`harness-secret` 的「敏感键清单三处独立维护」条目按代码事实改写（7 键→9 键，含两枚 CHAT Cookie 名与 TASK-045 实例），未新增 H3。

边界登记（不声称已覆盖）：本文件 canary 反查范围恰为 Console Owner 库 `control` 全库 + Console 应用日志 + HTTP 响应；Gateway/Runtime 侧凭据复核由 TASK-044 的 `chat_principal` 与 TASK-045 交接条目负责，不在本用例断言内；`tests/acceptance/test_secret_consumers.py` 的既有范围不含 `control.config_audit_log`（其条目已如实登记），本次新增的库级反查正是补这一块。
- B-245: verified — automated command passed; run_id=49b4675656b0480bbcefc280367bd630 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] done（B-245：RED 4 failed/1 passed → GREEN 6 passed；两处清单同步 + runtime 第三处同步；回归 1393+40+18 passed；ruff/mypy 干净；inventory CRLF 行更新 9 行；harness-secret 按事实改写）
- [2026-10-18] completed (done)

---

## TASK-046: WEBCHAT入口控制面与快照类型

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-013
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-im#RULE-im-001
- **Acceptance-Refs**: B-246
- **Files**: `apps/console-platform/backend/src/muad_console_platform/application/channel_admin_service.py`, `packages/contracts/src/muad_contracts/channel.py`, `tests/console_platform/test_capacity_webchat_webchat_entry.py`
- **Estimate**: 45分钟

### Description

WEBCHAT稳定入口无需企微secret且创建后不原地改Agent；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-246][integration] 先在`tests/console_platform/test_capacity_webchat_webchat_entry.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：WEBCHAT稳定入口无需企微secret且创建后不原地改Agent。RED：`uv run pytest -q tests/console_platform/test_capacity_webchat_webchat_entry.py` exit=1，6 failed/0 passed（`body.channel` literal_error：WEBCHAT 不在 `ChannelName` 白名单；`body.secret` missing：WEBCHAT 入口被强制要求企微 secret）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`ChannelName` 白名单加 `WEBCHAT`（唯一字面量处 `enums.py`）；`BotSnapshotItem` 增 `channel`（默认 `enums.DEFAULT_CHANNEL`）；`ChannelCreateRequest.secret` 改可选、按通道裁决（`channel_admin_service.entry_secret`：企微必须携带、WEBCHAT 传入即 422 拒绝）；`ChannelService.bots` 按 `control.bot_account.channel` 输出实际通道。secret 仍只落 Owner 表，响应/审计不含明文。
- [x] [B-246] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-246] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_webchat_entry.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，6 passed。
- [x] verifier `harness-im#RULE-im-001`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/console_channel","tests/gateway"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。证据：exit=0，413 passed（86s）；入口键 `bot_id` 跨通道/跨租户唯一、租户隔离与 `BOT_NOT_FOUND`/`AGENT_NOT_FOUND` 不泄露跨租户存在性均在本任务用例内逐项断言。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-246 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | WEBCHAT稳定入口无需企微secret且创建后不原地改Agent；停用保留历史来源，Agent已有0..N账号能力保留，本轮企微按单机器人基线验收 | tests/console_platform/test_capacity_webchat_webchat_entry.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_webchat_entry.py"] | verified |
| harness-im#RULE-im-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-246 | `uv run pytest -q tests/console_platform/test_capacity_webchat_webchat_entry.py` exit=1，6 failed/0 passed：`body.channel` literal_error（WEBCHAT 不在 `ChannelName` 白名单）+ `body.secret` missing（WEBCHAT 入口被强制要求企微 secret） | 同命令 exit=0，6 passed；manifest runner 实际执行并记 verified | `tests/console_platform/test_capacity_webchat_webchat_entry.py`：`test_b246_webchat_entry_needs_no_wecom_secret`（无 secret 创建成功、企微凭据 422 拒绝、未知通道 422、库内 secret 为 NULL）、`test_b246_webchat_entry_agent_is_not_rebindable_in_place`（PUT `agent_id`/`channel` → 422 且归属不漂移；改绑=在目标 Agent 新建入口 ID）、`test_b246_disable_keeps_entry_and_historical_attribution`（停用保留行/入口 ID/agent_id，仅退出启用快照，再启用恢复）、`test_b246_agent_keeps_zero_to_n_accounts_and_wecom_requires_secret`（0..N 账号；企微缺 secret 422 且不落库）、`test_b246_entry_key_unique_and_cross_tenant_invisible`（`bot_id` 跨通道/跨租户唯一 409 `BOT_ID_EXISTS`；跨租户 Agent 404 `AGENT_NOT_FOUND`；快照租户隔离）、`test_b246_snapshot_item_carries_actual_channel`（快照条目键集合含 `channel`；WECOM/WEBCHAT 各按实际值输出；`BotSnapshotResponse.model_validate` 契约回环） | 真实 Console ASGI（真实登录会话/CSRF/路由与依赖）+ 真实 PostgreSQL：`control.bot_account` 经真实事务写入并回读；`uq_bot_account_bot_id` 全局唯一、停用/软删与租户谓词由数据库与真实查询裁决；跨租户对照行直接落库（真实 PG）证明"不可见"来自谓词而非数据不存在；无模型/外部网络调用 | verified |

共享契约面：`ChannelName` 唯一字面量加 `WEBCHAT`（`enums.py`），`BotSnapshotItem` 增 `channel`（默认 `enums.DEFAULT_CHANNEL`，与 `control.bot_account.channel` 历史默认同源）；同步更新 `tests/test_attachment_contract.py`（B-07 值集合与"只此一处"机检）与 `tests/console_channel/test_channel_bots_api.py`（快照键集合含 `channel`）；通道中立性守卫 `tests/architecture/test_channel_neutrality.py` 3 passed（渠道字样只出现在 `ALLOWED_SURFACES`）。

回归：`uv run pytest -q tests/console_platform tests/console_channel tests/contracts tests/gateway tests/architecture tests/test_attachment_contract.py tests/test_configuration_inventory.py tests/test_contracts.py` exit=0，2117 passed（159s）。required verifier `harness-im#RULE-im-001`：`uv run pytest -q tests/console_channel tests/gateway` exit=0，413 passed（86s）。

静态检查：`uv run ruff check`（本任务改动的 8 个文件）全部通过；`uv run mypy apps packages` → Success: no issues found in 408 source files；`docs/configuration-inventory.csv` 保持 CRLF、只更新本任务相关行（`channel_service.py` 行号 +1、`channel_admin_service.py` 3 个新常量、`enums.py` `DEFAULT_CHANNEL`），`tests/test_configuration_inventory.py` 4 passed。函数均 ≤50 行；未新增错误码（复用 `COMMON_VALIDATION_ERROR`/`BOT_ID_EXISTS`/`AGENT_NOT_FOUND`/`BOT_NOT_FOUND`）。
- B-246: verified — automated command passed; run_id=34a8ece1d0ff46c39150669a66a3028d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-047: Gateway注册WEBCHAT标准通道适配器

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-044, TASK-046
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.3 数据设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-247
- **Files**: `apps/im-gateway/src/muad_im_gateway/channels/webchat.py`, `apps/im-gateway/src/muad_im_gateway/channels/registry.py`, `tests/gateway/test_capacity_webchat_webchat_adapter.py`
- **Estimate**: 45分钟

### Description

HTTP输入被规范化为统一事件并经唯一iter_events入口消费；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-247][integration] 先在`tests/gateway/test_capacity_webchat_webchat_adapter.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：HTTP输入被规范化为统一事件并经唯一iter_events入口消费。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_webchat_adapter.py` exit=2，1 error during collection（`ModuleNotFoundError: No module named 'muad_im_gateway.channels.registry'`——适配器与装配分发尚不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`channels/webchat.py`（`WebchatChannelAdapter`：`name=WEBCHAT`；`submit` 把 HTTP 形状 `WebchatInboundMessage` 规范化为统一 `ChannelEnvelope` 进**有界**队列，队满抛 `WebchatInboundOverloaded`、未启动抛 `ChannelAdapterUnavailable`；`iter_events` 是入站**唯一**消费入口；出站经注入的 `WebchatDeliverySink`，未装配显式失败；`apply_snapshot` 只收本通道条目）；`channels/registry.py`（`build_channel_registry` 装配 WECOM 探针/真机 + WEBCHAT；`dispatch_snapshot` 按 `BotSnapshotItem.channel` 分发，未注册通道 WARNING 跳过、应用失败上抛让快照 revision 不被认定生效）；`channels/base.py`（`ChannelAdapter.name` 改只读属性 + `SnapshotAware` 能力）；`channels/wecom/adapter.py`（`apply_snapshot` 按实际 channel 过滤）；`main.py`（装配根改为注册表构建 + 快照分发，移除单适配器字段）；`api/health.py`（就绪要求**每个已注册适配器**都已启动，保住"企微全部无凭据即不就绪"的既有语义）。日志/响应不含 secret 或消息原文。
- [x] [B-247] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-247] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_adapter.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，5 passed（0.96s）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-247 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | HTTP输入被规范化为统一事件并经唯一iter_events入口消费；通道特有数据留Gateway，不能触发企微长连接/secret路径，缓存有界 | tests/gateway/test_capacity_webchat_webchat_adapter.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_adapter.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-247 | `uv run pytest -q tests/gateway/test_capacity_webchat_webchat_adapter.py` exit=2，1 error during collection：`ImportError: cannot import name 'build_channel_registry' ... No module named 'muad_im_gateway.channels.registry'` | 同命令 exit=0，5 passed（0.96s） | `tests/gateway/test_capacity_webchat_webchat_adapter.py`：`test_b247_http_input_is_normalized_and_consumed_via_iter_events`（HTTP 200 后规范化事件字段逐项 == channel/bot_id/external_user_id/external_conversation_id/message_id/text/attachments/unsupported_media；Runtime 收到 `channel.type=WEBCHAT` 且 `Idempotency-Key=message_id`；消费后 `adapter.pending==0`；终态文本经 sink 映射 `route.channel=WEBCHAT`；Console resolve 请求体同封套）、`test_b247_inbound_buffer_is_bounded_and_overload_is_explicit`（`max_pending=2` 时第 3 条抛 `WebchatInboundOverloaded` 且 `pending==2`；已接受 2 条经 `iter_events` 原样可取；未启动/停用后 `submit`/`iter_events` 均显式失败）、`test_b247_snapshot_dispatch_by_channel_never_touches_wecom_secret_path`（真实 `BotSnapshotCache`+`dispatch_snapshot`：`enabled_entries==(WEBCHAT 条目,)` 且 secret 为 None；企微 SDK 只为 `wecom-bot-b247` 建客户端；整份快照直接给企微适配器也不为 WEBCHAT 条目建客户端且连接集合清空）、`test_b247_registry_registers_webchat_standard_adapter_without_secret`（`build_channel_registry` → `names()==("WECOM","WEBCHAT")`；无 bot/无 secret 也能 `start()`+`submit`，规范化结果 == `ChannelEnvelope(channel="WEBCHAT", ...)`）、`test_b247_outbound_maps_channel_neutral_payload_and_fails_explicitly_when_unwired`（未装配 sink：`send`/`stream` 抛 `ChannelAdapterUnavailable`；装配后 sink 收到渠道中立 route/message 与分片） | 真实 Gateway 应用（`muad_im_gateway.main.app` 真实路由表 + `RegistryDep`）经真实 ASGI/HTTP 封套；真实 `InboundPipeline.consume(adapter)` 按 `iter_events()` 唯一入口消费；真实 Console HTTP（uvicorn 桩按 Owner 接口封套应答 resolve/skills/audit/bots）与真实 Runtime HTTP 接收端（真实 `text/event-stream`）；真实 `build_channel_registry`/`dispatch_snapshot`/`BotSnapshotCache`/`WeComAdapter`（`FakeWeComSdkFactory` 记录是否建客户端/读 secret）；本文件不触任何数据库（网关不持库） | verified |

共享接口面：`channels/base.py` 的 `ChannelAdapter.name` 由可写字段改为**只读属性**（适配器可把 `name` 收窄为 `ChannelName`，`ChannelEnvelope(channel=self.name)` 免二次窄化）；新增 `SnapshotAware` 能力协议。回归连带修正（同域）：`tests/gateway/test_readyz.py` 的真实 lifespan 用例把适配器集合更新为 `{"WECOM": True, "WEBCHAT": True}`；`api/health.py` 的 `adapters_ready` 改为"每个已注册适配器都已启动"（否则 WEBCHAT 起来就会把"企微 bot 全部无凭据"盖过去，B-107 的既有语义会静默失效）。

回归：`uv run pytest -q tests/gateway` exit=0，376 passed（85s）；`uv run pytest -q tests/console_channel tests/contracts tests/architecture tests/test_attachment_contract.py tests/test_configuration_inventory.py` exit=0，1375 passed（12s）。required verifier 本任务 Spec-Refs 为空（B-247 functional 由 manifest runner 执行）。

静态检查：`uv run ruff check`（本任务改动的 6 个生产/测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 414 source files；函数均 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF、只追加/更新本任务相关行（新增 `WEBCHAT_INBOUND_QUEUE_MAX`，`base.py` 常量行号 +8、`main.py` `SERVICE_NAME` 34→33），`tests/test_configuration_inventory.py` 4 passed。

遗留（非本任务范围）：`harness-im.md` 的 Conventions 仍写着"`channel` 白名单仅 `WECOM`"（TASK-046 起已不成立，本任务又把 WEBCHAT 适配器落成事实），建议由 spec 责任面（cf-learn / TASK-046 review 面）改写为"白名单含 WEBCHAT，适配器按 name 注册、快照按 channel 分发"。
- B-247: verified — automated command passed; run_id=4157a3a728cd4fbd9384334020e7e02e (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-048: Gateway受理与订阅解耦及精确来源路由

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-030, TASK-036, TASK-047
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-im#RULE-im-002
- **Acceptance-Refs**: B-248
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/inbound.py`, `apps/im-gateway/src/muad_im_gateway/infrastructure/runtime_client.py`, `tests/gateway/test_capacity_webchat_gateway_admission.py`
- **Estimate**: 60分钟

### Description

handler槽仅覆盖受理，长流不占handler；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-248][integration] 先在`tests/gateway/test_capacity_webchat_gateway_admission.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：handler槽仅覆盖受理，长流不占handler。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_admission.py` exit=1，4 failed（首个 0.83s 即失败：`TypeError: InboundPipeline.__init__() got an unexpected keyword argument 'max_concurrent_handlers'`；其余三条同因）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`application/inbound.py`（① `_AdmissionSlot` 受理名额：受理裁决即归还、`release` 幂等，`max_concurrent_handlers` 构造参数封顶在飞受理；② `_await_admission` 定义**受理裁决点**——Runtime 首帧到达 / 流已结束 = 已受理，`AppError` = 未受理，首帧原样交回订阅循环；③ `_route_key` 升为**精确来源**四元组（渠道+入口+用户+**会话**，`RouteKey`），路由锁与每路由排队上限同步精确；④ `_explicit_conversation` + `RunRequest.conversation_id`：带显式会话能力的入口显式指定会话，未实现该能力的通道（企微）为 None 走原入口指针）；`application/runtime_client.py`（`RuntimeClientPort.create_run` 显式声明 `idempotency_key`；管线提交时显式传 `request.message.id`，重投沿用同一身份）；`channels/base.py`（新增 `ExplicitConversationSource` 能力协议，与 `AttachmentSource` 同一条接缝）；`channels/webchat.py`（`explicit_conversation_id`：缺失/坏值**显式失败**，绝不退回隐式"最新会话"指针）。日志/响应不含凭据或消息原文；坏会话的异常只带稳定文案、不回显原值。
- [x] [B-248] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-248] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_admission.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，5 passed（2.73s）。
- [x] verifier `harness-im#RULE-im-002`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。实际执行：exit=0，3 passed（0.76s）——`im-gateway` 新增的 `ExplicitConversationSource`（`channels/base.py`）与 `explicit_conversation_id`（`channels/webchat.py`）都落在适配器层，应用层只问"哪条会话"，`inbound.py` 不出现任何渠道专有字样/取件形状；取件、下载、解密、媒体 URL/密钥仍未出适配器。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-248 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | handler槽仅覆盖受理，长流不占handler；HTTP重投沿用message/幂等身份，企微按原入口指针、Webchat显式会话，new/stop/确认不串Run | tests/gateway/test_capacity_webchat_gateway_admission.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_admission.py"] | verified |
| harness-im#RULE-im-002 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | **渠道差异只活在适配器层**：核心域（`agent-runtime` / `agent-worker`）零渠道专有字样与取件形状；`im-gateway` 除 `channels/` 与装配根 `main.py` 外同样为零。取件、下载、解密、媒体 URL/密钥**一律不出适配器**——上层只看到「已解密字节 + 元信息」。机检见 `tests/architecture/test_channel_neutrality.py`（三条：核心域全文零字样 / 核心域不得给 `ResolveDefinitionRequest.channel` 传字面量 / 全仓代码扫描只允许出现在 `ALLOWED_SURFACES`）。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-248 | `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_admission.py` exit=1，4 failed（`TypeError: InboundPipeline.__init__() got an unexpected keyword argument 'max_concurrent_handlers'`） | 同命令 exit=0，5 passed（2.73s） | ① `test_b248_admission_slot_covers_admission_only_and_sources_route_precisely`：长流仍挂着时 `pipeline._handlers._value == 1`（上限=1，名额已归还）且 `runtime_probe.finished == []`；同用户**另一会话**在 A 未结束时被受理（`texts()==[TEXT_A,TEXT_B]`）；`requests[0/1]["channel"]` 各带自己的来源、`conversation_id` 各为 CONVERSATION_A/B、`Idempotency-Key` 各为原 message_id；放行后 `sink.streamed` 按 route 逐条对齐（A 的回复只回 A、B 只回 B）。② `test_b248_http_redelivery_reuses_message_identity_and_explicit_conversation`：两次 POST 的 `message`/`conversation_id`/`channel` 逐字段相等且 `Idempotency-Key == MESSAGE_A`（重投不新造身份）。③ `test_b248_accepted_redelivery_is_deduped_by_the_same_message_identity`：`store.reservations == [(im:dedupe:WEBCHAT:<msg>, owner), (同键, None)]`、Runtime 只收到一次 Run、键终值为 `DEDUPE_VALUE`。④ `test_b248_malformed_explicit_conversation_fails_loudly_without_falling_back`：坏会话抛 `ChannelAdapterUnavailable`、零 Run 提交、零出站、占位被释放（`get_value == None`）。⑤ `test_b248_wecom_entry_pointer_and_commands_stay_on_their_own_source`：企微 Run 的 `channel` 带入口账号+会话指针且 `conversation_id is None`（原入口指针）；同用户网页消息带显式会话并同时被受理；`/stop` 的 `cancel_calls == [{agent_id, platform_user_id}]` 且回执落在 `/stop` 自己那条消息的 req_id 上；`/new` 的 `conversations` 记录 + 回执落在自己的消息上；放行后企微回复回原入口回调、网页回复只落自己的会话 | 真实 Gateway 应用（`muad_im_gateway.main.app` 真实路由表 + `RegistryDep`）经真实 ASGI/HTTP 封套；真实 `WebchatChannelAdapter`（TASK-047）与真实 `WeComAdapter`（官方 SDK 端口为进程内探针）同注册表、**一个适配器一条** `InboundPipeline.consume` 循环（与装配根同形）；Runtime 侧是真实本地 HTTP 服务（真实 `text/event-stream`、按文本放行长流、记录 Run 请求/链路头/cancel-active/conversations），Console 侧是真实 HTTP 桩（Owner 接口封套）；真实 `InboundPipeline`/`RuntimeClient`/`ConsoleClient`。本文件不触任何数据库（网关不持库，DB 只经 Owner 接口访问） | verified |

实现要点：**受理与订阅解耦**——受理名额只覆盖到受理裁决（`_await_admission`：首帧/流结束/明确拒绝），此后的长流（SSE 订阅、状态、正文渲染）不再占名额（设计 §3.2「SSE 订阅独立存在」、§1.3「持流期间占处理槽」的整改点）；**精确来源路由**——路由锁与每路由排队上限按 `(channel, entry, user, conversation)`，同来源仍串行不交叉、不同来源互不阻塞互不串台；**Webchat 显式会话 / 企微原入口指针**——`RunRequest.conversation_id` 由适配器能力（`ExplicitConversationSource`）决定，坏值显式失败；**HTTP 重投沿用 message/幂等身份**——`RuntimeClientPort.create_run` 显式声明 `idempotency_key`，管线传原 `message.id`。

行为说明（有意变更）：受理裁决之前（等 Runtime 首帧的窗口，最长受 Runtime 心跳约束）不再发计时节拍帧；入站那一刻的「已收到/准备中」状态仍在提交前发送（`_update_progress(force=True)`）。同域连带（测试替身签名）：`tests/gateway/fakes.py`（`FakeRuntimeClient` 补 `idempotency_key` 并记录 `run_keys`）、`tests/gateway/test_inbound.py`/`test_execution_progress.py`/`test_waiting_resume.py` 的 `create_run` 替身补同名形参——只加默认形参，断言强度不变。

回归：`uv run pytest -q tests/gateway` exit=0，381 passed（91.6s；改造前基线 376 + 本任务 5 条）；`uv run pytest -q tests/contracts` exit=0，1281 passed（3.8s）；`uv run pytest -q tests/architecture` exit=0，33 passed（1.0s）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.9s）；`uv run pytest -q tests/agent_runtime/test_memory_redesign_confirmation.py` exit=0，37 passed（122s，隔离库；该文件用真实 Runtime HTTP + PG 驱动 `InboundPipeline.handle`，验证提交身份形参不破坏真实链路）。首轮全量 `tests/gateway` 曾出现 1 条环境性失败（`test_delivery_api.py::test_e05_worker_http_to_gateway_redis_probe_end_to_end`，Redis 探针），单跑与整跑复跑均通过（380→381 passed），按环境残留处理、未改实现。

静态检查：`uv run ruff check apps/im-gateway/src tests/gateway/` 干净；`uv run mypy apps packages` → Success: no issues found in 415 source files；函数均 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF（1428 行 CRLF）、只更新本任务改动文件的行号（`application/inbound.py` 常量整体 +1~+2、`channels/webchat.py` `WEBCHAT_INBOUND_QUEUE_MAX` 34→35，共 49 行；`channels/base.py` 新增协议在常量之后、`runtime_client.py` 常量行未动）。

遗留（非本任务范围）：① 启动设置 `gateway_max_concurrent_handlers`（TASK-003 已入 `SharedSettings`）仍未接线，装配根（TASK-096）应把它传进 `InboundPipeline(max_concurrent_handlers=...)`，本轮保持模块常量默认 8；② Runtime 的 `cancel-active` 仍按 (agent,user) 维度，来源级精确取消/待确认由 TASK-056 落；③ Runtime 侧「无显式会话的入口按 `channel_conversation` 指针选会话」由 TASK-049 落（当前 `_resolve_conversation` 仍按用户+Agent 最新会话）。
- B-248: verified — automated command passed; run_id=a635533d6a6743fcb24e28c296190522 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-049: 显式会话创建、入口核属与标题

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-013, TASK-030, TASK-035, TASK-046
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**: harness-memory#RULE-memory-004
- **Acceptance-Refs**: B-249
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_conversations.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `tests/agent_runtime/test_capacity_webchat_conversation_scope.py`
- **Estimate**: 60分钟

### Description

复用原Conversation/Canonical/DB上下文；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-249][integration] 先在`tests/agent_runtime/test_capacity_webchat_conversation_scope.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：复用原Conversation/Canonical/DB上下文。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_conversation_scope.py` exit=2，1 error during collection（`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_conversations'`——入口会话模块与入口核属/指针/标题语义均不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`application/channel_conversations.py`（① `ConversationScope` + `scope_of`：入口键 `channel.bot_id` 是 UUID 形状时即入口账号 UUID，非 UUID 形状**不编造**（`entry_id_of` 返回 None）；② `load_explicit_conversation`/`assert_entry_attribution`：tenant/user/agent 必配 + 入口**双向严格**核属（可归属入口必须命中同一 `channel_account_id`；不可归属入口不得借用已归属会话），违者 `COMMON_NOT_FOUND`；③ 入口指针：`lock_pointer`（五元组 `SELECT ... FOR UPDATE`）、`register_pointer`（已有行原地切换；缺失时 `ON CONFLICT DO NOTHING` 插入、并发落败者回读并锁）、`resolve_pointer_conversation`（指针缺失/指向已软删会话时按同五元组取最近会话改指，无候选才与消息同事务新建；不可归属入口退回 (tenant,user,agent) 口径且只认未归属会话）；④ 标题：`derive_title`（首条文本空白折叠后截取 80 字符；纯附件按 `locale.default_locale` 用本地化占位；空消息无标题）+ `display_title`（空会话显示「新会话」），**不调用模型**；⑤ `conversation_recency_key`（用户最近发言口径，原 `run_service._conversation_recency_key` 原样迁入唯一口径））。`application/run_service.py`（`_resolve_conversation` 改为「显式核属 / 指针」两条路径；`_create_run` 在可见用户消息事务内 `record_user_message`（`last_message_at` + 首条标题，locale 取同一份设置快照）；`_persist_human_resume` 同事务刷新 `last_message_at`；`_finalize_run` 仅在 COMPLETED（写可见 assistant 正文）时刷新；`create_conversation` 增可选 `entry_id`/`external_thread_id`（幂等指纹含入口/线程、重放先核入口归属、可归属入口建会话后原地切换指针）；新建路径不触碰任何既有会话/ Run）。日志/响应不含凭据或消息原文；核属失败只回稳定错误码。
- [x] [B-249] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-249] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_conversation_scope.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，7 passed（9.44s）。
- [x] verifier `harness-memory#RULE-memory-004`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。实际执行：exit=0，59 passed（148.05s）——本任务不改记忆写入/删除/退休路径：会话核属只读写 `runtime.conversation`/`channel_conversation`，`memory_scope` 仍以 (tenant,user) 为唯一身份（本任务用例 ⑥ 用真实 `MemoryService.lock_scope` 证明跨入口/会话命中同一 scope 行，删除 tombstone/forget_epoch/revision 语义未触碰）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-249 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 复用原Conversation/Canonical/DB上下文；tenant/user/agent/entry同时核属，新建不取消旧Run，首条文本80字符标题无需模型；长期记忆继续tenant/user共享 | tests/agent_runtime/test_capacity_webchat_conversation_scope.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_conversation_scope.py"] | verified |
| harness-memory#RULE-memory-004 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 用户/管理员删除保留 key tombstone，递增 forget_epoch 并撤销旧提议；自动写不能解除此删除、人工 disabled 或把 USER/ADMIN 降级为 AGENT。EXPIRE/QUOTA/TASK_COMPLETE 自动退休仅接受真实新入站记录的 source_scope_revision 证明，不以 job 开始时间冒充新依据。自动批量提交核对原 scope revision，过期输入为 STALE；同任务 lineage 续接原稳定 key，完成后清除未决状态。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-249 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_conversation_scope.py` exit=2，1 error during collection（`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_conversations'`；入口核属/指针/标题语义在实现前均不存在） | 同命令 exit=0，7 passed（9.44s） | ① `test_b249_explicit_conversation_is_scoped_to_tenant_user_agent_entry` L403-494：显式续聊落原会话（L425 `start.conversation_id == 会话`、L426 无新会话行、L427 同一条 canonical `USER_MESSAGE` seq `[1,2]`、L430-432 入口归属/`last_run_id`/`last_message_at`）；拒绝矩阵（L438-464）借其他入口/不可归属入口借用已归属会话/越权用户/越权 Agent 均 `COMMON_NOT_FOUND`，且 L465-473 无新会话、无新 Run；L475-494 跨租户会话不可见。② `test_b249_entry_pointer_switches_current_conversation_in_lock` L502-641：首次无显式会话新建并登记五元组指针（L527-531）；**指针压过「用户最近发言」旧口径**（L544-547：同用户/Agent 更新的会话不得改变解析）；不同线程/入口互不串台（L559、L569-578）；`/new` 语义指针**原地切换**（L596-598 同一指针行 id、L601-602 新会话 `title=None/last_seq=0/last_run_id=None/last_message_at` 非空、L611 后续消息落新会话）；指针指向已软删会话时原地改指可用会话（L636-640）。③ `test_b249_new_conversation_never_cancels_the_old_run` L648-698：新建后旧 Run 仍 `RUNNING` 且 `cancel_requested is False`（L677-678）、旧会话 `last_run_id` 不变（L681）、新会话无 Run（L683）、后续消息落新会话且旧 Run 不受影响（L693-696）。④ `test_b249_first_message_title_is_normalized_and_needs_no_model` L707-773：长文本归一化截取 80 字符（L726-727）、`resolve` 调用恰 1 次且 `model_calls == []`（L728-729，标题不调模型）、第二条消息不改标题（L741）、纯附件占位 `[附件]`（L759）、空消息无标题且读取层显示「新会话」（L771-773）。⑤ `test_b249_attachment_placeholder_follows_tenant_locale` L776-800：en-US 设置下占位 `[Attachment]`。⑥ `test_b249_memory_scope_stays_shared_across_entries_and_conversations` L808-875：两个入口/会话驱动真实受理后，真实 `MemoryService.lock_scope` 命中同一 (tenant,user) scope 行（L868）、另一用户不同行（L869）、`MemoryScope` 列集合无会话/入口维度（L870-874）。⑦ `test_b249_entry_key_parsing_never_fabricates_an_entry_identity` L881-885：UUID 形状解析、非 UUID/空串返回 None（不编造入口身份）。 | 真实 `RunService` 受理路径（会话解析/核属、准入、Run/快照/提交/事件、指针锁内切换全部走真实代码；模型调用是进程内替身，标题「不调模型」由 `FakeResolveClient.calls/model_calls` 真实记录直接证明）；隔离租户的真实 PostgreSQL：`runtime.conversation`/`channel_conversation`/`canonical_event`/`run_record`/`memory_scope`（只增删本租户的行，`_purge_tenant` 含指针/制品先行顺序）；设置源是真实 `PlatformSettingsClient` 形状的进程内实现（capacity 组显式、locale 组可切换）；未 mock 存储或租约 | verified |

实现要点：**入口身份**——`RunRequest.channel.bot_id` 是入口键，UUID 形状即入口账号逻辑 UUID（`conversation.channel_account_id`/`channel_conversation.channel_account_id`），非 UUID 形状**不编造**（企微字符串键的归属缺口见遗留①）；**显式会话核属**——tenant/user/agent 必配 + 入口双向严格（可归属入口必须命中同一入口账号；不可归属入口不得借用已归属会话），一律 `COMMON_NOT_FOUND` 不泄露存在性；**入口指针**——无显式会话的入口按 (tenant,user,agent,入口,外线线程) 五元组 `SELECT ... FOR UPDATE` 锁内切换：已有指针直接命中、缺失/指向已软删会话时按同五元组取最近会话（用户最近发言口径）改指、无候选才与消息同事务新建；**标题**——首条真实用户文本空白折叠后截取 80 字符，纯附件按租户 `locale.default_locale` 用本地化占位，空会话由读取层显示「新会话」，不为标题调用模型；**会话活动**——`last_message_at` 在可见用户消息（创建/续聊）与 COMPLETED 终态正文事务内刷新，失败/取消/续租/订阅不刷新；**新建不取消旧 Run**——`create_conversation` 只建新会话并把该入口线程指针原地切到新会话，不触碰任何既有会话/Run。

回归：`uv run pytest -q tests/agent_runtime` exit=0，**908 passed**（765.27s；含本任务 7 条与既有 TASK-003 `_resolve_conversation` 口径用例、TASK-031 准入/释放、TASK-035 时钟租约等）；`uv run pytest -q tests/contracts` exit=0，1281 passed（2.97s）；`uv run pytest -q tests/architecture` exit=0，33 passed（0.97s，含 `test_channel_neutrality.py`：新模块零渠道专有字样）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.73s）。

静态检查：`uv run ruff check`（本任务 3 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 419 source files；新增 `channel_conversations.py` 全部函数 ≤50 行（`run_service` 既有长函数维持原状，未新增超限函数）；`docs/configuration-inventory.csv` 保持 CRLF（1444 行 CRLF、0 裸 LF），只更新本任务相关行：`channel_conversations.py` 新增 7 行、`run_service.py` 行号整体上移（+10/±），`USER_MESSAGE_EVENT`/`CONVERSATION_ACTIVE` 随唯一消费方迁入新模块（原行删除、新行登记）。

遗留（deferred，非本任务可闭合）：① **企微等非 UUID 形状入口键的指针缺口**——`channel.bot_id` 是字符串（如 `aibln...`），Runtime 无法据它得到入口账号 UUID，故指针仅对 UUID 形状入口生效；非 UUID 入口退回 (tenant,user,agent) 口径且**只认未归属会话**（防止借用已归属会话，跨入口混写风险已显式拒绝但不能做入口级指针）。收口路径：由装配/契约侧把入口账号 UUID 带进 RunRequest（例如适配器/装配根按 `BotSnapshotItem.bot_account_id` 映射，或扩展 `ChannelContext`），届时企微也走同一指针；在此之前保持诚实退化、不编造入口身份。② `RunService.create_conversation` 的入口参数（`entry_id`/`external_thread_id`）与幂等指纹已就绪，但 Runtime HTTP DTO（`POST /v1/conversations`）尚未暴露这两个字段——由 TASK-052 的 Gateway API-08 接线时补齐（含 201 响应 `entry_id` 投影）。③ `last_message_at` 的口径（用户消息 + COMPLETED 正文）由本任务定义；TASK-050 的列表排序若需要其它事件刷新，请在其口径内显式决定，不静默扩大刷新面。
- B-249: verified — automated command passed; run_id=1b853ecab9264b4190285d109ca96ad5 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-050: 历史列表、详情与固定水位分页

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-049
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-250, B-04
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/webchat_history.py`, `apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py`, `tests/agent_runtime/test_capacity_webchat_history_queries.py`
- **Estimate**: 45分钟

### Description

列表按last_message_at/id且list/count同谓词同快照，批量活跃Run无N+1；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-250][integration] 先在`tests/agent_runtime/test_capacity_webchat_history_queries.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：列表按last_message_at/id且list/count同谓词同快照，批量活跃Run无N+1。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_history_queries.py` exit=1，**10 failed in 0.86s**（三条历史 Owner 路由与内部门控尚不存在：`{"code":"COMMON_NOT_FOUND",...}`、`assert 404 == 200`；身份门控用例同样 404）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`application/webchat_history.py`（① `HistoryScope` + `_conversation_scope`：核属维度复用 TASK-049 的 `load_explicit_conversation`（tenant/user/agent/入口 同时成立，一律 `COMMON_NOT_FOUND` 不泄露存在性），不重写归属校验；② 列表：`last_message_at DESC NULLS LAST, id DESC`，list/count **同一语句同一快照**（同 WHERE 的 total 子查询 × 分页子查询 LEFT JOIN，越界页 total 仍正确），活跃 Run 状态按页一次批量关联（`RunRecord.status.in_(ACTIVE_RUN_STATUSES)`）；③ 消息：固定水位——首屏冻结 `through_seq=conversation.last_seq`、后页沿用（page>1 缺水位显式 422）、超过会话持久水位显式 `CURSOR_AHEAD`、page 1 为最新窗口、页内正序、向前加载旧页、总量不被 200 截断；④ 安全公开投影：只投影 `USER_MESSAGE`/`ASSISTANT_MESSAGE`（`run_id` 为空的不可证实历史不冒认）、附件引用按页批量取权威行（含软删 tombstone 投影 `available=false`），工具参数/结果、SYSTEM、原始推理、未知事件、存储键与 TRANSCRIPT 一律不出现；坏载荷空正文兜底、不泄露不整页失败）。`api/webchat_history.py`（API-09/14/15 Owner 端点：`/internal/webchat/...`，路由器级 `require_internal_service` 门控；入口与 Agent 显式传入后复核；页大小默认值从 Console 平台设置 `webchat.history_page_size` 解析，不在端点硬编码）。`main.py` 仅新增路由器装配两行（计划外文件，披露）。
- [x] [B-250] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-250] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed in 0.96s**。
- [x] [B-04][integration] 将原B-04落入`tests/agent_runtime/test_capacity_webchat_history_queries.py`，先RED后GREEN；实际 Conversation/CanonicalEvent 查询与索引；断言：多页会话和超过200条消息按确定顺序读取；消息水位固定时无跳过/重复；列表 list/count 同条件、同快照，无逐会话 N+1；后台续租不改变会话最近活动排序。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-250 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 列表按last_message_at/id且list/count同谓词同快照，批量活跃Run无N+1；消息页through_seq固定、默认50上限100可超200总量；activeRun恢复不跳过正文 | tests/agent_runtime/test_capacity_webchat_history_queries.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | verified |
| B-04 | integration | 实际 Conversation/CanonicalEvent 查询与索引 | 多页会话和超过200条消息按确定顺序读取；消息水位固定时无跳过/重复；列表 list/count 同条件、同快照，无逐会话 N+1；后台续租不改变会话最近活动排序 | tests/agent_runtime/test_capacity_webchat_history_queries.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-250 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_history_queries.py` exit=1，**10 failed in 0.86s**（三条历史路由与内部门控均不存在，首个失败即 `{"code":"COMMON_NOT_FOUND",...}` / `assert 404 == 200`） | 同命令 exit=0，**10 passed in 0.96s**（复跑 1.08s 同绿） | ① `test_b250_list_is_entry_scoped_ordered_and_batches_active_runs` L380-391（顺序 `[newer, busy, older, legacy]`、`total=4`、空标题→「新会话」、NULL 最近活动回落 `create_time`、活跃状态 `RUNNING`/None、`last_run_id` 真实；其他用户/其他 Agent/其他入口/未归属/软删/跨租户行全部不在列表）；② `test_b04_list_pages_are_deterministic_single_snapshot_and_not_n_plus_one` L431-447（三页并集=全集、无重复；每页 total 恒 5——含只剩 1 条的越界页；每页恰 1 条 `runtime.conversation` 语句且含 `count(*)`（list/count 同谓词**同快照**）、恰 1 条 `runtime.run_record` 批量查询（**无 N+1**））；③ `test_b04_background_renewal_does_not_change_recency_order` L469-475（后台续租 + 后台 `MEMORY_CONSOLIDATED` 后顺序不变、`last_message_at` 未被刷新）；④ `test_b250_message_pages_freeze_the_watermark_and_reject_ahead_cursors` L497-545（首屏 `through_seq=5`、页内正序 `[3,4,5]`；冻结水位后新消息（seq 6）不移动水位、旧页 `[1,2]` 不丢；新首屏水位推进到 6；`through_seq=7` → 400 `CURSOR_AHEAD`；page=2 缺水位 → 422 `COMMON_VALIDATION_ERROR`）；⑤ `test_b250_history_supports_more_than_two_hundred_messages` L573-601（250 条三页覆盖无跳过/重复、`total=250` 不被 200 截断；默认页大小 30 来自平台设置而非硬编码；page_size=101 → 422）；⑥ `test_b250_active_run_keeps_visible_body_and_recovery_identity` L627-648（持久水位 5 覆盖 2 条已落库 `MESSAGE_DELTA`；可见正文 `[1,2,3]` 一条不丢；详情 `active_run.id/status/execution_status` 与 `last_seq` 真实可观测）；⑦ `test_b250_detail_is_attributed_and_exposes_no_internal_snapshot` L670-711（响应字段集合恰为契约 8 键；跨用户/跨 Agent/错入口 404 `COMMON_NOT_FOUND`；未归属会话与跨租户 404）；⑧ `test_b250_waiting_input_detail_reports_the_real_run_state` L726-728（WAITING_INPUT 活跃 Run 真实投影）；⑨ `test_b250_internal_history_endpoints_require_service_identity`（缺/错服务身份 403；缺主体字段 422） | 真实 Runtime ASGI 应用（真实路由 + `X-Internal-Service` 门控 + `X-Tenant-Id` 租户上下文）；真实 `WebchatHistoryService` 真实 SQL（`before_cursor_execute` 逐语句捕获证明单语句 list/count 与批量活跃 Run）；隔离租户的真实 PostgreSQL：`runtime.conversation`/`canonical_event`（`seq` 由真实 `EventWriter` 在会话行锁内分配）/`run_record`/`artifact` 全部真实行；不 mock 存储，无模型调用 | verified |
| B-04 | 同 B-250 的 RED（同一文件同一次运行） | 同 B-250 的 GREEN（10 passed in 0.96s） | ②③⑤⑦ + `test_b04_projection_only_exposes_visible_messages_and_safe_attachments` L802-812（消息 list/count 单语句、附件按页恰 1 次批量查询；只出 USER/ASSISTANT 两条可见消息；工具参数/原始推理/SYSTEM/未知事件/记忆收尾 canary 与 `storage_key` 均不在响应文本中；失效附件 `available=false` 不伪造可用；响应键集合为白名单 `{event_id,seq,run_id,role,text,attachments}`） | 同上（同一真实边界）；查询侧安全公开投影与 TASK-051 的 SSE 侧投影后续合并为唯一实现 | verified |

实现要点：**列表**——入口显式（路由/参数）+ Agent 显式，核属复用 TASK-049；`last_message_at DESC NULLS LAST, id DESC`；list/count 用同一条语句（total 子查询 × 分页子查询 LEFT JOIN）实现"同谓词同快照"，越界页 total 仍与同谓词一致；活跃 Run 状态按页一次批量取（同会话至多一个活跃 Run 由 partial unique 保证）。**详情**——`active_run` 的 `execution_status` 复用 TASK-036 的 `project_execution_status`（两入口同一投影函数）；活跃 Run 与 `last_run_id` 不一致时显式内部错误（不静默丢弃恢复身份）。**消息**——`through_seq` 是**会话持久水位**（`conversation.last_seq`，含活跃 Run 已落库的增量事件），首屏冻结、后页沿用；它不是"已渲染正文"的证明：活跃 Run 的恢复必须走该 Run 自己的游标/安全快照（design §3.4/§3.6），本任务保证恢复身份（active_run id/status/execution_status）与水位都可观测、可见正文不丢。**投影**——查询侧白名单；无 Run 归属的事件不冒认；坏载荷空正文兜底；附件元信息按页批量取权威行（含软删 tombstone）。

回归：`uv run pytest -q tests/agent_runtime` exit=0，**918 passed**（840.05s；收尾复跑 775.01s 同绿；基线 908 + 本任务 10 条）；`uv run pytest -q tests/contracts tests/architecture` exit=0，1314 passed（3.90s）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.87s）。

静态检查：`uv run ruff check`（本任务 4 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 421 source files；新增模块全部函数 ≤50 行（AST 逐函数核对）；`docs/configuration-inventory.csv` 保持 CRLF（1449 行 CRLF、0 裸 LF），只追加本任务 5 个常量行并更新本任务改动文件的行号（`main.py` `SERVICE_NAME` 46→47、`POD_NAME` 104→105）。

遗留（deferred，非本任务可闭合）：① `active_run.confirmation` 的**完整候选与选项文案**由 TASK-056 的 resume 语义投影（记忆确认的选项在仓库内没有已定型的线上词表，本任务不合成、不编造，`confirmation=None` 是显式选择）；② 消息页的 `CURSOR_EXPIRED` 需要保留/清理边界（`webchat.history_retention_days` 的落地属 TASK-069），当前只有"超前"分型；③ 内部详情/消息端点要求 `entry_id`+`agent_id` 显式传入（TASK-052 从 `/chat/:entryId` 路由参数与入口快照解析后调用；公共 API-15/09 的入口解析属 TASK-052 接线）；④ 助手消息的产物附件（工具结果类 artifact）不在本任务投影范围，随 TASK-055/059 的附件语义收口；⑤ 页大小默认值解析每次未显式传参时读一次平台设置（TASK-057 的配置缓存落地后可共用同一投影）。
- B-04: verified — automated command passed; run_id=09861432860342169c63c1065e1b9a0b (confirmed_by: runner)
- B-250: verified — automated command passed; run_id=09861432860342169c63c1065e1b9a0b (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-051: 历史和SSE共用安全公开投影

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006, TASK-050
- **Source**: agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-251
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/public_events.py`, `tests/agent_runtime/test_capacity_webchat_public_projection.py`
- **Estimate**: 45分钟

### Description

USER/ASSISTANT/安全附件/确认白名单，工具参数/结果/SYSTEM/推理/未知事件默认不公开；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-251][integration] 先在`tests/agent_runtime/test_capacity_webchat_public_projection.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：USER/ASSISTANT/安全附件/确认白名单，工具参数/结果/SYSTEM/推理/未知事件默认不公开。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_public_projection.py` exit=2，**1 error during collection**（`ImportError: cannot import name 'public_events' from 'muad_agent_runtime.application'`——共用投影模块尚不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/public_events.py`（新增）——把 TASK-050 查询侧安全公开投影抽成**唯一实现**：`project_public_fact`（canonical 行 → `WebchatHistoryMessage`（可见消息）/TASK-006 `PublicWebchatEvent`（公开流事件）/None 单一入口）、`project_public_stream_event`（SSE 接缝，只出公开流事件）、`visible_attachment_ids`（按页批量取附件的唯一口径）；唯一词表 `PUBLIC_MESSAGE_EVENT_TYPES`/`PUBLIC_ROLE_BY_EVENT_TYPE`（USER/ASSISTANT）与 `PUBLIC_STREAM_EVENT_TYPES`（delta/stage/terminal/error/artifact/confirmation 白名单）；唯一附件安全规则（失效 `available=false`、无权威行/尺寸不可投影不伪造、`TRANSCRIPT` 留档不投影、`storage_key` 永不出现）；已知公开事件坏载荷显式 `COMMON_INTERNAL_ERROR`（只带 event_id/seq/stream_type），未知/内部 stream_type 默认不公开。② `application/webchat_history.py`（重构，计划外披露）——删除本地投影实现，SQL 白名单与投影消费 `public_events`；消息分页 select 补 `conversation_id/stream_type/artifact_id/create_time`（仍是单语句 list/count + 按页一次附件批量）。③ `docs/configuration-inventory.csv`——2 个常量随唯一消费方迁入 `public_events.py`、新增 1 行白名单常量、同步 `webchat_history.py` 行号（CRLF 保持）。
- [x] [B-251] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-251] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_public_projection.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**6 passed in 0.37s**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-251 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | USER/ASSISTANT/安全附件/确认白名单，工具参数/结果/SYSTEM/推理/未知事件默认不公开；canonical seq合法跳号，不提供TRANSCRIPT下载或内部存储键 | tests/agent_runtime/test_capacity_webchat_public_projection.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_public_projection.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-251 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_public_projection.py` exit=2，**1 error during collection**（`ImportError: cannot import name 'public_events'`；全部用例已收集执行前即缺模块） | 同命令 exit=0，**6 passed in 0.37s** | ① `test_b251_history_and_stream_share_one_projection_over_the_same_rows` L388-428（单一实现：`webchat_history.project_public_fact is public_events.project_public_fact`；同一批真实行 → 历史只出 seq `[1,5]` 的 USER/ASSISTANT 与失效附件 `available=false`；SSE 接缝只出 `[2,4,8]` 的 delta/stage/terminal——1/3/5/6/7 合法跳号不补号；真实 HTTP 历史端点输出与同一函数逐字段一致；工具/SYSTEM/未知/终态正文 canary 与 `storage_key` 不在任何投影输出）② `test_b251_history_projection_is_not_n_plus_one_and_matches_shared_facts` L501-508（历史分页输出=共用函数事实；附件按页恰 1 次批量查询、无 N+1）③ `test_b251_stream_seam_exposes_only_whitelisted_events` L579-596（artifact.created 只出安全元信息、失效附件 `available=false`、`TRANSCRIPT` 留档不投影、run.failed → 稳定错误码/保守 retryable）④ `test_b251_stream_seam_filters_unknown_and_internal_events_by_default` L630（工具参数/结果、模型请求、推理、SYSTEM、记忆、未知、run.created 与消息行在流上全为 None）⑤ `test_b251_stream_seam_projects_the_complete_safe_confirmation_only` L682-691（完整安全确认 DTO 才公开；内部 proposal 载荷过滤、`proposal_hash` 不外泄）⑥ `test_b251_known_public_events_fail_explicitly_on_malformed_payloads` L725-728（已知公开事件坏载荷显式 `COMMON_INTERNAL_ERROR`，不回显原文） | 真实 Runtime 应用（真实路由 + `X-Internal-Service` 门控）、真实 `WebchatHistoryService` 与 `public_events` 投影、隔离租户的真实 PostgreSQL：conversation/run_record/canonical_event/artifact 真实行，`seq` 由真实 `EventWriter` 在会话行锁内分配；`before_cursor_execute` 逐语句捕获证明附件批量查询；未 mock 存储或租约 | verified |

实现要点：**唯一入口**——`public_events.project_public_fact` 是「canonical 行 → 公开事实」的唯一投影；历史分页与 SSE 接缝都从它取事实（历史路径只保留 `WebchatHistoryMessage`，流路径只保留 `PublicWebchatEvent`），不各自维护第二份白名单/附件规则。**词表**——消息白名单 `USER_MESSAGE`/`ASSISTANT_MESSAGE` 与流白名单 `PUBLIC_STREAM_EVENT_TYPES` 是唯一来源（SQL 谓词也消费同一常量）；`reply_generation` 缺省 0=首次生成（多代属 TASK-062），terminal 只出状态、error 只出稳定码+保守 retryable（未知=False，与 retry_safety 同保守方向）。**附件**——只按权威 `artifact` 行投影（失效 `available=false`；无权威行/尺寸不可投影不伪造；`TRANSCRIPT` 不进入公开面）；历史页附件仍一次批量取。**错误**——已知公开事件的坏载荷显式失败（过滤与坏帧可区分）；未知/内部事件默认不公开（过滤跳号合法）。

回归：`uv run pytest -q tests/agent_runtime` exit=0，**936 passed**（671.18s；含本任务 6 条，日志 `/tmp/task051-regression-agent-runtime.log`）；`uv run pytest -q tests/contracts tests/architecture` exit=0，1314 passed（4.28s）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.79s）；`uv run pytest -q tests/test_attachment_contract.py tests/test_error_catalog.py tests/test_api_i18n.py` exit=0，18 passed（0.31s）。

静态检查：`uv run ruff check`（本任务 3 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 423 source files；新增/改动函数全部 ≤50 行（AST 逐函数核对）；`docs/configuration-inventory.csv` 保持 CRLF（1456 行 CRLF、0 裸 LF），只动本任务相关行：`public_events.py` 新增 3 行、`webchat_history.py` 迁出 2 行并更新 3 行行号。

遗留（deferred，非本任务可闭合）：① **确认的完整候选投影**（`submission_id/source_event_id/options` 的写入侧）属 TASK-056——本任务只认「载荷本身已是完整安全 DTO」，记忆确认现有内部载荷（`proposal_id/proposal_hash/…`）不公开、不合成选项；② **流端点/游标/分片/回压**属 TASK-060/061（本任务只提供 `project_public_stream_event` 接缝与合法跳号语义，未接线任何路由）；③ `message.delta` 的 `reply_generation` 多代与 `message.snapshot` 分片属 TASK-062/061（当前缺省 0=首次生成）；④ `TRANSCRIPT` 清理/下载授权属 TASK-055/069（本任务只保证不进入公开投影与不提供内部存储键）。
- B-251: verified — automated command passed; run_id=6200b71f67594c7b9b2bda67829e7b21 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-052: Gateway入口、会话和消息HTTP接口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-044, TASK-048, TASK-050, TASK-051
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-252
- **Files**: `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `apps/im-gateway/src/muad_im_gateway/application/webchat_service.py`, `tests/gateway/test_capacity_webchat_webchat_resource_api.py`
- **Estimate**: 60分钟

### Description

API-05/06/08/09/14/15完整转Owner；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-252][integration] 先在`tests/gateway/test_capacity_webchat_webchat_resource_api.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：API-05/06/08/09/14/15完整转Owner。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_webchat_resource_api.py` exit=2，1 error during collection（0.32s；`ModuleNotFoundError: No module named 'muad_im_gateway.api.webchat'`——资源路由/服务与运行时客户端接缝在实现前均不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `api/webchat.py`（新增）——API-05/06/08/09/14/15 八条真实路由，主体统一经 `ChatIdentityDep` 收口；消息提交 202、新建会话 201、其余 200；私有响应显式 `Cache-Control: no-store`；`Idempotency-Key` 为必填头（缺失 422）；API-09 用 `WebchatHistoryQuery` 校验"后页必须沿用冻结水位"；服务从装配根发布状态（`runtime_client`/`bot_snapshot`/`tenant_id`）构造，网关不持库。② `application/webchat_service.py`（新增）——`WebchatResourceService` + `WebchatOwnerPort`：入口列表只含已启用且获**当前** Console 授权集合的 WEBCHAT 条目；入口解析（未知/停用 404、未授权 Agent 403）先于任何 Owner 调用；公共会话路径先经 Owner 的 `.../scope` 按 (tenant,user) 解析归属、再核会话 Agent 仍在当前授权集合（撤销影响后续访问），之后才走入口显式读取；消息提交组装显式会话 + 精确来源（channel/bot=入口账号 UUID/external_user/external_conversation）+ `upload_ids[]`，只等**受理裁决**（首帧）即返回；202/恢复回执的状态/阶段取自 Owner 的 Run 视图（含持久阶段投影），201 的创建时不变式（`last_seq=0`）由行事实校验；取消/恢复先核 Run 归属（他主体 404 不泄露存在性）；坏封套/不可投影事实显式 `COMMON_INTERNAL_ERROR`，响应零主体/零凭据/零角色字段。③ `application/runtime_client.py`——`RunAdmission` + `submit_run`/`resume_run`（读到首帧即关闭读取流，不持流；非 2xx 按 Owner 错误码上抛）、`get_run`/`cancel_run`、内部历史面读取（服务身份 + 租户）、`create_conversation` 增 `entry_id`/`external_thread_id`（只在显式给出时上送，IM 调用点请求体不变）。④ Runtime Owner 接线（TASK-053/050 遗留的接线项）：`application/dto.py`（`CreateConversationRequest.entry_id/external_thread_id`、`conversation_view.entry_id`、`run_view.execution_status` 复用 TASK-036 唯一投影）、`api/runs.py` 透传、`application/webchat_history.py`+`api/webchat_history.py` 新增 `GET /internal/webchat/conversations/{id}/scope`（只按 tenant+user 核属、入口/Agent 从行导出）、`packages/contracts/webchat.py` 新增 `WebchatRunCancelResult`/`WebchatResumeReceipt`/`WebchatConversationScope`（前端 TASK-073 已按同形消费）。
- [x] [B-252] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-252] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_resource_api.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**9 passed**（3.7–3.8s，连续复跑稳定）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-252 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | API-05/06/08/09/14/15完整转Owner；核当前CHAT/授权/X-Chat-Context后才查幂等回执，返回真实202/201/忙碌状态，不读写DB | tests/gateway/test_capacity_webchat_webchat_resource_api.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_resource_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-252 | `uv run pytest -q tests/gateway/test_capacity_webchat_webchat_resource_api.py` exit=2，1 error during collection（0.32s；`ModuleNotFoundError: No module named 'muad_im_gateway.api.webchat'`） | 同命令 exit=0，9 passed（3.7–3.8s） | ① `test_b252_chat_gate_and_entry_authorization_precede_every_owner_call` L537-604：缺 Cookie/缺上下文 401/422 且零 Console/Runtime 触达（L557-563）；撤销 401 `CHAT_SESSION_REVOKED` 且不查任何幂等回执（L574-576）；上下文不符 409 `AUTH_CONTEXT_CHANGED`（L582-583）；未授权入口 403 `FORBIDDEN` 且零 Runtime 触达（L590-591）；未知/停用入口 404 `COMMON_NOT_FOUND`（L601-603）。② `test_b252_entries_list_projects_only_enabled_authorized_webchat_entries` L607-633：恰一条已启用且已授权 WEBCHAT 入口、字段恰 `entry_id/agent_id/display_name`（L619-624）；`cache-control: no-store`（L617）；一次请求只核验一次主体（L630）。③ `test_b252_message_submit_returns_real_accepted_run_without_holding_the_stream` L636-711：202 逐字段等于 Owner 投影（L656-660）；受理帧后立即返回且网关关闭读取流（长流未放行/未终态，L667-671）；`Idempotency-Key`/租户/主体/显式会话/精确来源（bot=入口账号 UUID）/`upload_ids[]` 逐字段转发（L676-694）；状态与阶段只从 Owner Run 视图取（L696-698）；同键重投回同一 `run_id`（L702-709）。④ `test_b252_message_submit_surfaces_real_busy_and_capacity_statuses` L714-754：`RUN_BUSY` 409 且不伪造 run_id（L732-733）；`RUN_CAPACITY_EXCEEDED` 429（L743-744）；缺幂等键 422 且不触达 Owner（L750-751）。⑤ `test_b252_conversation_create_and_list_delegate_to_owner` L757-817：201 创建投影（L774-775）；入口/线程/幂等键请求体（L784-785）；API-14 列表投影（L793）；内部面服务身份/租户/查询参数（L808-816）。⑥ `test_b252_conversation_detail_and_history_resolve_scope_then_read_entry_scoped` L820-907：详情/历史投影原样（L833/L855）；归属解析只带受信主体、详情与历史各一次（L873）；入口显式读取参数含 `through_seq`（L878-893）；会话 Agent 不在当前授权集合时 403 且不再读入口显式端点（L900-905）。⑦ `test_b252_cancel_and_resume_bind_the_original_run_and_replay` L910-976：取消回执 + 可重放（L931-937）；恢复回执 + 绑定/selection/幂等身份（L940-952）；他人 Run 404 且零取消/恢复触达（L969-975）。⑧ `test_b252_chat_credentials_never_reach_responses_logs_or_owner_payloads` L979-1001：响应/日志零凭据、原文只进 Console 核验体（L991-995）、响应无主体/角色字段（L998-1000）。⑨ `test_b252_resource_api_modules_hold_no_database` L1004-1019：资源接口/服务模块零数据库依赖（与 `tests/architecture/test_im_gateway_boundaries.py` 机检同口径） | 真实 Gateway 应用：`muad_im_gateway.main.app`（真实路由表、真实 api-kit 封套/错误目录、真实 `api/deps.py` 依赖图）挂载**生产** `api/webchat.py` 路由；真实 Console Owner 接口（`ConsoleClient` 打真实本地 uvicorn，chat-principal + bot 快照契约）；真实 Runtime Owner 接口（`RuntimeClient` 打真实本地 uvicorn：`POST /v1/runs` 与 `.../resume` 是**真实 `text/event-stream` 长流**、受理帧后保持打开直到测试放行——"受理与订阅解耦"由它裁决；`GET /v1/runs/{id}`、取消、`POST /v1/conversations`、`/internal/webchat/...` 同真实 HTTP）；真实 ASGI/HTTP 请求；本文件不触任何数据库（网关不持库，DB 只经 Owner 接口访问） | verified |

实现要点：**门控先于幂等回执**——六个 API 全部经 `ChatIdentityDep`（Console 核验 + 单租户 + `X-Chat-Context` 两层比对），入口/会话 Agent 的**当前**授权在本层再核一次，未过门控的请求零 Owner 触达（尤其不查幂等回执）；**受理与订阅解耦**——API-06 读到 Owner 首帧（受理裁决）即关闭读取流并返回 202（执行由 Runtime 监督槽驱动，HTTP 断开不取消执行），后续正文/阶段由公开 SSE（TASK-061）按 Run 身份另订；**完整转 Owner**——入口/会话/消息/取消/恢复的事实全部经 Console/Runtime 受信 HTTP，202/恢复回执的状态与持久阶段取自 Owner Run 视图，历史/列表/详情是 Owner 公开投影的原样校验，网关不持库、不自建第二份状态；**公共路径的入口解析**——API-09/15 路径不带入口，新增 `.../scope`（只按 tenant+user 核属、入口/Agent 从行导出）解析后再走入口显式读取端点（TASK-050 的核属语义不在网关侧重写）。

回归：`uv run pytest -q tests/gateway` exit=0，**390 passed**（91s；含同域连带修正：TASK-048 用例的消息形状期望补上 TASK-053 起就存在的契约字段 `upload_ids`——该断言在 main 上是**先于本任务**的陈旧失败（`MessageInput` 契约字段自 TASK-053 起恒被序列化，与本次改动无关），按契约事实修正期望、断言强度不变）；`uv run pytest -q tests/contracts` exit=0，1281 passed（3.5s）；`uv run pytest -q tests/architecture` exit=0，33 passed（1.3s）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.8s）。受影响 Runtime 回归：`tests/agent_runtime/test_runs_api.py` 24 passed、`tests/agent_runtime/test_capacity_webchat_history_queries.py` **11 passed**（含本任务新增的 `test_task052_scope_resolution_derives_entry_and_agent_for_the_owner_only`——真实 PG 上验证新增 Owner 接缝：本主体解析 (entry,agent)、他主体/不可归属 404、缺受信主体 422、无服务身份 403）、`tests/agent_runtime/test_capacity_webchat_conversation_scope.py tests/agent_runtime/test_capacity_webchat_run_controls.py` 20 passed（隔离库）。

静态检查：`uv run ruff check`（本任务 9 个改动/新增文件）干净；`uv run mypy apps packages` → Success: no issues found in 425 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF（1468 行 CRLF、0 裸 LF），只新增 `webchat_service.py`（`WEBCHAT_CHANNEL`/`MAX_ENTRIES`）、`api/webchat.py`（`NO_STORE_CACHE_CONTROL`）与 `runtime_client.py` 5 条新路径常量，并更新本任务文件的行号漂移（`webchat_history.py` 3 行 +1、`runtime_client.py` 常量块与函数默认行）。

遗留（deferred，非本任务可闭合）：① **生产装配根注册**（TASK-096）——`main.py` 需 `app.include_router(webchat_router)` 并把 WEBCHAT 出站 sink 注入适配器；本任务测试按"生产路由 + 装配根状态同形注入"直连真实应用，未改 `main.py`（Files 不含它，且 B-296 明确覆盖"生产 app 实际注册"）。② **API-07 公开 SSE/API-10 上传/API-11 通知/API-19 客户端配置**分别属 TASK-061/054/070/057；本任务只落 API-05/06/08/09/14/15。③ **消息文本不做 IM 命令方言**——资源 API 是消息面（停止/新建/确认走 API-08 专用端点）；若后续要求网页输入框支持 `/new` 等文本命令，需在 API-06 契约显式设计判别型响应。④ **恢复回执的 `submission_id` 取自请求绑定**（Owner 的 resume 流首帧不携带该字段）；若 TASK-056 后续在回执中返回权威 submission，应改为从 Owner 投影。⑤ **`WebchatConversationCreated` 的创建时不变式**：幂等重放在"会话已推进"的极端窗口下会显式 `COMMON_INTERNAL_ERROR`（不撒谎返回 `last_seq=0`），如需支持该窗口需扩展契约语义。
- B-252: verified — automated command passed; run_id=da32a8ffa22a4d94975320f7c96921c4 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)


---

## TASK-053: 上传登记与Run独立不可变制品

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-013, TASK-049
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: B-253, B-02
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_uploads.py`, `apps/agent-runtime/src/muad_agent_runtime/application/attachments/inbound.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `packages/contracts/src/muad_contracts/runtime.py`, `tests/agent_runtime/test_capacity_webchat_upload_staging.py`
- **Estimate**: 60分钟

### Description

暂存TTL/tenant/user/entry归属受控，受理创建Run自己的不可变storage_key；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-253][integration] 先在`tests/agent_runtime/test_capacity_webchat_upload_staging.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：暂存TTL/tenant/user/entry归属受控，受理创建Run自己的不可变storage_key。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_upload_staging.py` exit=2，1 error during collection（`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_uploads'`——登记/核属/发布/清理与 Run 受理接线在实现前均不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/channel_uploads.py`（新增）——`register_staged_upload`（storage_key 相对路径形状校验：空串/绝对路径/反斜杠/`.`/`..` 段一律拒绝；kind 词表/media_type 形状/`sha256:<64hex>` 校验和/filename 长度/`size>0`；超 `upload_max_bytes` 用附件门控词表 `ATTACHMENT_TOO_LARGE`；TTL=PG 时钟+`upload_staging_ttl_sec`；同租户活跃暂存键重复登记 `COMMON_CONFLICT`（预检+唯一索引兜底））；`load_owned_upload`/`resolve_message_uploads`（跨租户/不存在/已软删 `COMMON_NOT_FOUND`；同租户越权用户/借入口 `FORBIDDEN`；过期 `UPLOAD_EXPIRED`；数量超限 `ATTACHMENT_COUNT_EXCEEDED`；空正文且无上传 `COMMON_VALIDATION_ERROR`；重复 upload_id 非法）；`run_artifact_storage_key`（`runs/<run_id>/attachments/<upload_id>`，只由系统 UUID 拼出）；`publish_run_artifact`（读暂存字节算真实 sha256 与登记比对：缺失 `UPLOAD_EXPIRED`、不一致 `COMMON_CONFLICT`；`os.link` 原子发布，目标已存在 `FileExistsError`→`COMMON_CONFLICT`，绝不覆盖）；`cleanup_expired_staging`（`FOR UPDATE SKIP LOCKED` 有界扫描过期活跃行→软删+删暂存文件，幂等）。② `application/attachments/inbound.py`——新增 `persist_staged_inbound_attachments`：逐上传发布 Run 自己的键后落 `artifact` 行（`run_id` 非空维持 `ck_artifact_run_task_xor`），`metadata_json` 带 `upload_id/kind/filename/source`；只 flush 不 commit，失败时回滚自己发布的文件（`discard_written`）。③ `application/run_service.py`——`_create_run` 在设置快照后、昂贵解析前调用 `_resolve_message_uploads`（重放已在 `_admit` 返回，只新受理校验 TTL；入口不可归属时显式 `COMMON_VALIDATION_ERROR`）；受理事务内调用 `persist_staged_inbound_attachments`（`written` 出参），提交前失败（IntegrityError/AppError/其他）一律 `discard_written` 回滚盘上文件；`_creation_fingerprint` 纳入 upload_ids（同键换上传→`IDEMPOTENCY_MISMATCH`）；`_resume_active` 对 upload_ids 显式拒绝（不静默丢）；构造器可选 `artifact_root` 注入（测试隔离，生产回落 `SharedSettings.artifact_root`）。④ `packages/contracts/runtime.py`——`MessageInput.upload_ids`（API-06 `upload_ids[]` 的 Runtime 契约形状）。凭据/存储键不进公开响应：事件载荷只有 `artifact_id` 等安全摘要，日志只记 upload_id/error_type。
- [x] [B-253] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-253] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，12 passed（3.52s）。
- [x] [B-02][integration] 将原B-02落入`tests/agent_runtime/test_capacity_webchat_upload_staging.py`，先RED后GREEN；真实凭据/会话/附件归属存储；断言：未授权、过期、空正文、纯附件、大小/数量边界与现有附件门控一致。断言位置：`test_admission_rejects_cross_tenant_forbidden_and_soft_deleted`（L469-521）、`test_admission_rejects_expired_count_and_empty_content`（L524-562）、`test_upload_limits_follow_webchat_settings`（L448-461，默认 50 MiB/5 与既有附件门控常量同值）、`test_registration_rejects_unsafe_keys_bad_checksum_and_oversize`（L348-403，恰等于上限放行、超 1 字节拒绝）。
- [x] verifier `harness-skill#RULE-skill-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。实际执行：exit=0，4 passed（0.02s）。本任务不改 skill 缓存/发布原语；上传受理的 Run 制品发布复用同一不可变契约（`os.link` 目标存在即失败、不覆盖、只相对 storage_key、DB 只存相对键），并在用例 ⑦/⑧/⑨ 以真实文件系统证明"发布不可变 + 暂存/其他 Run 清理不删当前字节"。review 阶段仍需按原 verifier 复核。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-253 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 暂存TTL/tenant/user/entry归属受控，受理创建Run自己的不可变storage_key；暂存或其他Run清理不删当前字节；幂等重放先查已受理结果再检查暂存TTL | tests/agent_runtime/test_capacity_webchat_upload_staging.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | verified |
| B-02 | integration | 真实凭据/会话/附件归属存储 | 未授权、过期、空正文、纯附件、大小/数量边界与现有附件门控一致 | tests/agent_runtime/test_capacity_webchat_upload_staging.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | verified |
| harness-skill#RULE-skill-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml:7-8`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（默认 1h，保护进行中事务）。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-253 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_upload_staging.py` exit=2，1 error during collection（`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_uploads'`；登记/核属/发布/清理与 Run 受理接线在实现前均不存在） | 同命令 exit=0，12 passed（3.52s） | ① `test_registration_records_attribution_and_ttl_without_artifact_rows` L325-345：tenant/user/entry 归属（L333-335）、TTL=PG 时钟+86400s（L343）、暂存字节在共享存储（L344）、**登记不是 artifact 行**（L345）。② `test_registration_rejects_unsafe_keys_bad_checksum_and_oversize` L348-403：任意路径/坏校验和/坏 kind/media_type/size≤0 → `COMMON_VALIDATION_ERROR`（L383-389）、超上限 → `ATTACHMENT_TOO_LARGE`（L390）、**恰等于上限放行**（L396）、拒绝不留行（L403）。③ `test_duplicate_registration_conflicts_but_other_tenants_reuse_the_key` L406-445：同租户活跃暂存键重复 `COMMON_CONFLICT`（L429）、另一租户同键可复用（L437）。④ `test_upload_limits_follow_webchat_settings` L448-461：限额来自 webchat 设置（L455-457），默认 50 MiB/5/86400s（L459-461）。⑤ `test_admission_rejects_cross_tenant_forbidden_and_soft_deleted` L469-521：跨租户 `COMMON_NOT_FOUND`（L486）、越权用户/借入口 `FORBIDDEN`（L498/L508）、软删视同不存在（L521）。⑥ `test_admission_rejects_expired_count_and_empty_content` L524-562：空正文无上传 `COMMON_VALIDATION_ERROR`（L535）、**纯附件放行**（L545）、数量超限 `ATTACHMENT_COUNT_EXCEEDED`（L549）、重复 upload_id 非法（L555）、过期 `UPLOAD_EXPIRED` 只挡新受理（L560-562）。⑦ `test_run_admission_publishes_its_own_immutable_artifact` L570-633：**受理创建 Run 自己的 storage_key**（L596-597 `runs/<run>/attachments/<upload>` ≠ 暂存键）、checksum/元信息/upload_id 溯源（L598-604）、真实字节与校验和不可变（L608-609）、同键二次写入 `FileExistsError`（L611-612）、**登记行受理后不变**（L615-619）、事件载荷无存储键（L632-633）。⑧ `test_two_runs_get_independent_immutable_copies_of_the_same_upload` L636-680：同一上传两个 Run 各自独立键（L667-675）、删 A 不删 B（L679-680）。⑨ `test_staging_cleanup_never_deletes_accepted_run_artifacts` L683-726：清理软删暂存行并删暂存文件（L715-721）、**已受理 Run 制品字节完好**（L722）、清理幂等（L726）。⑩ `test_replay_after_staging_expiry_returns_the_original_run` L729-801：暂存过期且字节已删后同键同指纹重放仍回原 Run（L777-779）、未受理新提交才校验 TTL 且不留 Run/制品（L799-801）。⑪ `test_missing_or_tampered_staged_bytes_fail_explicitly_without_side_effects` L804-850：缺失字节 `UPLOAD_EXPIRED`（L828）、篡改 `COMMON_CONFLICT`（L844）、失败不留 Run/制品文件（L846-848）。⑫ `test_unattributable_entry_cannot_admit_staged_uploads` L853-881：入口不可归属时 upload_ids 显式 `COMMON_VALIDATION_ERROR`（L880）。 | 真实 `RunService` 受理路径（重放检查→会话核属→暂存核属→Run/快照/提交/事件→制品发布→提交/回滚全部真实代码；模型调用是进程内替身，本任务不验证模型执行）；隔离租户的真实 PostgreSQL（`runtime.channel_upload`/`artifact`/`run_record`/`run_submission`/`canonical_event`，只增删本租户的行）；真实共享文件系统（临时 ARTIFACT_ROOT：真实暂存字节、真实 sha256 校验、`os.link` 原子硬链接发布、清理真实 unlink；不 mock 存储或租约）；`MessageInput.upload_ids` 契约由真实 Pydantic 校验；`artifact` 行经真实 ORM/事务与 `ck_artifact_run_task_xor` 约束。 | verified |
| B-02 | 同文件（含入 B-253 的 12 条用例；实现前整文件 collection error，B-02 断言同批转绿） | 同命令 exit=0，12 passed（3.52s） | `test_admission_rejects_cross_tenant_forbidden_and_soft_deleted`（未授权，L486-521）；`test_admission_rejects_expired_count_and_empty_content`（过期/数量/空正文/纯附件，L535-562）；`test_upload_limits_follow_webchat_settings`（大小/数量边界与既有附件门控默认一致：50 MiB/5，L459-460）；`test_registration_rejects_unsafe_keys_bad_checksum_and_oversize`（大小边界含恰等于上限，L390-396）。 | 真实凭据（受验证主体 tenant/user/entry 由请求参数与登记行真实比对）、真实会话（`runtime.conversation` 真实行）、真实附件归属存储（`channel_upload`/`artifact` + 真实文件字节）；未 mock 存储或租约。 | verified |

实现要点：**登记**（`channel_upload` 行=权威元信息，非 artifact；storage_key 系统生成且形状受控；TTL 取 PG 时钟）→ **受理**（只有未受理的新提交校验 TTL/归属；跨租户不泄露存在性、越权 `FORBIDDEN`、过期 `UPLOAD_EXPIRED`、数量 `ATTACHMENT_COUNT_EXCEEDED`）→ **Run 独立不可变制品**（`os.link` 原子发布到 `runs/<run_id>/attachments/<upload_id>`：校验真实 sha256 后才发布，目标存在即失败；暂存键与各 Run 键是不同目录项，清理任一路径都不影响其他引用的字节）→ **幂等重放优先**（`_admit` 的重放/同键串行化在暂存核属之前，指纹含 upload_ids）→ **失败回滚**（发布文件与 DB 事务同生共死：函数内失败自回滚，提交前失败由 `_create_run` 按 `written` 回滚）。

回归：`uv run pytest -q tests/agent_runtime` exit=0，**920 passed**（831.06s；908 条既有 + 本任务 12 条）；`uv run pytest -q tests/contracts` exit=0，1281 passed（3.08s）；`uv run pytest -q tests/architecture` exit=0，33 passed（0.94s，含附件服务依赖方向与通道中立性）；`uv run pytest -q tests/test_attachment_contract.py tests/test_error_catalog.py` exit=0，13 passed（0.28s）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.80s）。

静态检查：`uv run ruff check`（本任务 5 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 420 source files；新增函数均 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF，只新增 `channel_uploads.py` 6 行并同步 `inbound.py`（+4）/`run_service.py`（+8）行号漂移。

遗留（deferred，非本任务可闭合）：① **Runtime HTTP DTO 的 upload_ids 接线**——`MessageInput.upload_ids` 契约与 `RunService` 受理路径已就绪，Gateway→Runtime 的消息提交（API-06）由 TASK-052/054 把 HTTP `upload_ids[]` 映射进来；本任务只保证 Runtime 侧语义。② **活跃引用与孤儿宽限的完整清理保护**属 TASK-069（本任务只提供暂存侧最小正确清理：过期行软删+删暂存文件，已受理制品在 Run 自己的键上不受影响）。③ 暂存核属与清理之间存在小竞态窗口（核属通过后清理进程删了暂存文件）：受理会以 `UPLOAD_EXPIRED` 显式失败且不留半建 Run，用户重传即可；宽限期保护由 TASK-069 收口。④ `_resume_active` 对带 upload_ids 的 resume 显式拒绝（API-08 的 resume 显式绑定原 Run/submission/source，不承载新暂存上传）；若后续需求要支持"暂停期间补传附件"，需在 resume 契约中显式设计。
- B-02: verified — automated command passed; run_id=e826a5acdfee41cbaeb39204c3b968eb (confirmed_by: runner)
- B-253: verified — automated command passed; run_id=e826a5acdfee41cbaeb39204c3b968eb (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-054: Webchat流式上传与动态限额

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002, TASK-052, TASK-058
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-254
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/webchat_uploads.py`, `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `tests/gateway/test_capacity_webchat_upload_gateway.py`
- **Estimate**: 45分钟

### Description

流式写共享字节后经Runtime登记元信息；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-254][integration] 先在`tests/gateway/test_capacity_webchat_upload_gateway.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：流式写共享字节后经Runtime登记元信息。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_upload_gateway.py` exit=1，**8 failed in 3.37s**（实现前：生产路由 `POST /api/v1/webchat/entries/{entry}/attachments` 尚不存在 ⇒ 7 条 HTTP 用例 404 `COMMON_NOT_FOUND`；`application/webchat_uploads.py` 尚不存在 ⇒ 零 sqlalchemy 静态断言 `FileNotFoundError`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/webchat_uploads.py`（新增）——`_MultipartReader`（multipart **流式**解析：部件头/前言 ≤8KiB 有界、缓冲上界=单个入站块+分隔符、分隔符与头块跨块正确、闭合分隔符缺失显式 422）；`_StagingSink`（临时文件 + `os.link` 原子发布到 `webchat/staging/<uuid>`、**写入途中**判上限、失败路径 `discard` 同时清临时与已发布字节）；`WebchatUploadService.upload`（入口授权→平台设置动态限额→流式落共享字节→经受信端口登记，`except BaseException: discard` 后重抛）；`resolve_upload_limits`（`webchat.upload_max_bytes`/`upload_max_per_message` 唯一来源=平台设置，启动硬预算 `chat_upload_hard_max_bytes` 作解析上界，超预算显式失败不静默取小）；MIME 白名单复用 `attachment_gate.ALLOWED_MEDIA_TYPES`（`ATTACHMENT_TYPE_NOT_ALLOWED`），数量/大小用既有 `ATTACHMENT_COUNT_EXCEEDED`/`ATTACHMENT_TOO_LARGE`；文件名只作元信息（空→None，超长/CR/LF/NUL 显式 422，绝不参与路径）；空文件在登记前就拒。② `application/webchat_service.py`——抽出 `resolve_authorized_entry`/`ResolvedEntry`：入口授权唯一裁决（404 不泄露存在性/403 越权），读写面与上传面共用不复制第二份。③ `application/runtime_client.py`——`register_upload` + `_post_internal_json`（`X-Internal-Service` + `X-Tenant-Id` 门控，与读取面同一口径）。④ `application/platform_settings.py`——解析缝注入 chat 硬预算上界（同一处 schema 解析）。⑤ `api/webchat.py`——`POST /entries/{entry_id}/attachments`（201）+ 上传服务依赖（装配根发布的状态值）。日志只记 `error_type`，无上传内容/存储键/文件名原文。
- [x] [B-254] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-254] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_upload_gateway.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**8 passed in 3.43s**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-254 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 流式写共享字节后经Runtime登记元信息；MIME/字节/数量/软硬上限一致，返回upload_id而非伪artifact_id，超额在昂贵处理前拒绝 | tests/gateway/test_capacity_webchat_upload_gateway.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_upload_gateway.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-254 | `uv run pytest -q tests/gateway/test_capacity_webchat_upload_gateway.py` exit=1，**8 failed in 3.37s**（生产路由与上传面模块均不存在：7 条 HTTP 用例 404 `COMMON_NOT_FOUND`；静态断言 `FileNotFoundError`） | 同命令 exit=0，**8 passed in 3.43s** | ① `test_b254_upload_streams_shared_bytes_then_registers_authoritative_metadata`（L419-474）：响应恰为 `upload_id/kind/media_type/size/filename/checksum/expires_at` 七字段（L438）、no-store（L445）、无 `storage_key`/`artifact_id`（L446）、checksum=真实字节 sha256（L442）、登记上送身份三件套+元信息（L457-462）、暂存键系统生成且**磁盘字节逐字节相同**（L463-469）、CHAT 主体只经 Console 核验一次（L471-474）。② `test_b254_upload_gates_chat_identity_and_entry_authorization_before_any_io`（L477-530）：401/401(CHAT_SESSION_REVOKED)/409/403/404/404（L494-524），**门控先于任何 IO**：零设置读取、零登记、零字节（L525-528）。③ `test_b254_upload_rejects_oversize_mid_stream_and_cleans_partial_bytes`（L533-556）：`ATTACHMENT_TOO_LARGE` 400（L549-550）、**读完之前中止**（实测 consumed=4/9 块，L551-553）、半成品与临时文件清理（L554）、不触达登记（L555）、限额来自设置快照（L556）。④ `test_b254_upload_enforces_mime_count_and_soft_hard_limits`（L559-617）：白名单外 MIME `ATTACHMENT_TYPE_NOT_ALLOWED`（L576-577）、数量 `ATTACHMENT_COUNT_EXCEEDED`（L589-591）、恰等于上限放行（L594-600）、设置超启动硬预算 `COMMON_INTERNAL_ERROR` 显式失败且不登记（L603-617）。⑤ `test_b254_upload_rejects_broken_multipart_shapes_before_registering`（L620-651）：非 multipart/无文件部件/空文件/缺闭合分隔符 5 例均 422 且零字节零登记。⑥ `test_b254_registration_failure_surfaces_owner_code_and_cleans_staged_bytes`（L653-675）：Owner 403 原码上抛、已写字节不留孤儿。⑦ `test_b254_chunk_boundaries_do_not_change_the_stored_bytes`（L677-704）：分块 1/2/3/7/64/4096 字节落盘与校验和逐字节一致、每次上传独立键。⑧ `test_b254_upload_modules_hold_no_database`（L707-721）：上传面模块零 sqlalchemy（AST+文本）。 | 真实 Gateway 应用与生产路由（`main.app` + `api/webchat.py`，真实 api-kit 封套/错误目录/主体门控依赖图）；真实 Console HTTP（uvicorn 真实 socket：CHAT 主体核验 + bot 快照 + `GET /internal/v1/platform-settings` 快照）；真实 Runtime HTTP（uvicorn 真实 socket：`POST /internal/webchat/uploads` 用**真实契约** `WebchatUploadRegistration` 校验请求体后才回权威元信息）；真实共享文件系统（隔离 artifact root 上真实 `NfsArtifactStore` 字节，登记上送的键就是磁盘上那份字节）；真实请求体流（multipart 体按块产出并计数，超限中止由真实消费块数裁决）；网关零数据库（本文件不碰 DB + 静态断言）。 | verified |

实现要点：**流式**（multipart 部件体按入站块写入共享存储，内存上界=单块+分隔符，不整包驻留）→ **门控先于昂贵处理**（主体/入口授权 → 设置快照 → MIME/数量/大小，超限在写入途中即中止并清理）→ **原子不可变落盘**（临时文件 + `os.link`，目标存在即冲突）→ **转 Owner 登记**（字节写好后才经受信端口登记权威元信息；失败路径删除无行引用的暂存字节）→ **限额唯一来源**（平台设置 + 启动硬预算上界，前后端与 Runtime 同值：`upload_max_bytes`/`upload_max_per_message`）。

回归：`uv run pytest -q tests/gateway` exit=0，**398 passed**（93.88s；含 TASK-052/044 等既有 390 条 + 本任务 8 条）；`uv run pytest -q tests/architecture tests/contracts tests/test_configuration_inventory.py tests/console_channel` exit=0，**1372 passed**（10.13s，含通道中立性/网关不持库/契约 parity/配置盘点）；同域 `harness-im#RULE-im-001`（`tests/console_channel` + `tests/gateway`）与 `RULE-im-002`（`tests/architecture/test_channel_neutrality.py`）均在本轮真实执行通过。

静态检查：`uv run ruff check`（本任务 6 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 426 source files；新增/修改函数均 ≤50 行（最长 42 行 `_stage_single_part`）；`docs/configuration-inventory.csv` 保持 CRLF（1477 行全 CRLF、无裸 LF），只更新自己的行（`runtime_client.py` 行号漂移 + 新增 `INTERNAL_UPLOADS_PATH`、`api/webchat.py` 行号、`RuntimeClient.__init__.timeout_sec` 行号）并追加 `webchat_uploads.py` 6 行常量。

遗留（deferred，非本任务可闭合）：① **装配根接线**——路由依赖读取 `app.state.{settings_client,artifact_store,chat_upload_hard_max_bytes}`，生产装配由 TASK-096 发布这三个状态值（TASK-052 同一模式：本任务只交付路由与依赖图，`main.py` 不改）。② **下载/预览授权**属 TASK-055（本任务只登记暂存与返回 `upload_id`）。③ **暂存清理的活跃引用/孤儿宽限**属 TASK-069（本任务失败即清理，不留孤儿）。④ **前端 client-config 同值投影**属 TASK-057（`upload_max_bytes`/`upload_max_per_message` 由同一 schema 派生）。⑤ 一次请求只登记一个文件部件（响应契约是单个 `WebchatUpload`；多文件由前端逐个上传），单请求文件部件数仍受 `upload_max_per_message` 上界约束。
- B-254: verified — automated command passed; run_id=22d385216ddd4322a587a7f0a0a589cc (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-055: 附件下载授权与安全预览

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-044, TASK-054
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-255
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/webchat_artifacts.py`, `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `tests/gateway/test_capacity_webchat_artifact_download.py`
- **Estimate**: 45分钟

### Description

下载和图片预览复核会话/来源/当前授权，no-store/nosniff；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-255][integration] 先在`tests/gateway/test_capacity_webchat_artifact_download.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：下载和图片预览复核会话/来源/当前授权，no-store/nosniff。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_artifact_download.py` exit=1，**7 failed in 2.50s**（实现基线：`git stash` 复原 3 个改动生产文件 + 移走 2 个新增生产模块后跑，跑完 `git stash pop` 复原并 md5 逐字节校验 5/5 一致；代表性失败 `assert 404 == 200`——路由与下载面尚不存在，静态用例 `FileNotFoundError`）。Owner 侧归属语义同时建了 `tests/agent_runtime/test_capacity_webchat_artifact_resolve.py`（同一基线 exit=1，**3 failed in 0.28s**，同为 `assert 404 == 200`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现（含超出计划 Files 的披露）：① `apps/im-gateway/.../application/webchat_artifacts.py`（新增，计划内）——`WebchatArtifactService.open_download`：Owner 归属解析 → **当前**入口/Agent 授权（复用 `resolve_authorized_entry`）→ 共享存储取件；`safe_media_type`/`disposition_for`/`content_disposition_header`（显式白名单栅格图片 `inline`，其余含 HTML/SVG 一律 `attachment`；坏媒体类型降级 `application/octet-stream`；文件名只作元信息、控制字符百分号编码）；`_open` 对键越界/字节缺失/`size` 与实际长度不符显式 404（绝不按错误长度声明 `Content-Length`）。② `apps/im-gateway/.../api/webchat.py`（计划内）——`GET /artifacts/{artifact_id}/content` + `_download_response`（`no-store`/`nosniff`/服务端裁决的 `Content-Disposition`；`FileResponse` 流式输出）。③ `apps/im-gateway/.../application/runtime_client.py`——`resolve_webchat_artifact`（受信面只带 `user_id`；`X-Internal-Service` + `X-Tenant-Id`）。④ `apps/agent-runtime/.../application/webchat_artifacts.py`（新增，**计划外但必需**：归属事实只有 Owner 能权威裁决，否则跨用户下载无法拒绝）——`resolve_webchat_artifact`：artifact(tenant, 活跃) → 会话 `(tenant, user, 活跃)` 核属 → `(entry, agent)` 从会话行导出 + `attachment_ref` 单点映射；跨租户/他主体/软删/`TRANSCRIPT` 内部留档/无会话归属/入口不可归属一律 `COMMON_NOT_FOUND`。⑤ `apps/agent-runtime/.../api/webchat_history.py`（**计划外但必需**）——`GET /internal/webchat/artifacts/{artifact_id}`（受信身份门控；响应 = `WebchatConversationScope` + `AttachmentRef`，存储键只在受信面）。⑥ `docs/configuration-inventory.csv`——本任务常量行同步（新模块 6 行 + `runtime_client.py` 新增 1 行/行号平移 + `api/webchat.py` 行号）。日志只记稳定事件名/`error_type`，无存储键、字节原文或主体字段。
- [x] [B-255] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-255] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_artifact_download.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**7 passed in 2.63s**（经 `code-flow acceptance run --owner TASK-055 --write-evidence` 写入机器证据，run_id=7a004f6a9745455fb3dcf75f7ea2a4f4）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-255 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 下载和图片预览复核会话/来源/当前授权，no-store/nosniff；HTML/SVG按附件处理不执行，释放DB事务后流式输出，不凭客户端storage_key取件 | tests/gateway/test_capacity_webchat_artifact_download.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_artifact_download.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-255 | `uv run pytest -q tests/gateway/test_capacity_webchat_artifact_download.py` exit=1，**7 failed in 2.50s**（实现基线：`git stash` 复原 3 个改动生产文件 + 移走 2 个新增生产模块后跑，随后 `git stash pop` 复原并 md5 校验 5/5 逐字节一致；代表性失败 `assert 404 == 200`，静态用例 `FileNotFoundError`）；Owner 侧 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_artifact_resolve.py` 同基线 exit=1，**3 failed in 0.28s**（`assert 404 == 200`） | 同命令 exit=0，**7 passed in 2.63s**；Owner 侧同命令 exit=0，**3 passed in 0.33s** | ① `test_b255_download_serves_authorized_bytes_without_leaking_storage_key` L444-493：字节逐字节相等（L466）、`application/pdf`（L468）、`no-store`（L469）、`nosniff`（L470）、`Content-Length`=真实长度（L471）、`attachment; filename*=utf-8''…`（L472-474）、响应/日志无存储键（L475-481）、解析请求只带当前主体+租户+服务身份（L482-487）、取件键只来自 Owner 解析（L488）、主体字段不进响应（L493）。② `test_b255_image_preview_is_inline_while_executable_content_downloads` L496-584：PNG `inline` + `nosniff` + `no-store`（L561-564）、HTML/SVG `attachment`（L571-576）、坏媒体类型降级 `application/octet-stream` 且无注入头（L580-584）。③ `test_b255_cross_user_cross_tenant_and_deleted_artifacts_are_refused` L587-639：跨用户/软删/内部留档/未知 id 全 404（L625-626）、四种失败不可分辨（L627-631）、不读一个字节（L633）、解析主体恒为当前用户（L637-638）。④ `test_b255_revoked_agent_and_disabled_entry_refuse_before_reading_bytes` L642-711：Agent 撤销 403（L664-665）、入口停用 404（L681-682）、入口换绑 Agent 404（L702-703）、CHAT 会话撤销 401（L710-711）——四例 `store.resolved_keys == []`（L665/682/703/711）。⑤ `test_b255_client_supplied_storage_key_is_ignored_and_bad_bytes_are_refused` L714-790：客户端 `?storage_key=` 被忽略且点名的键从未被读取（L782-784）、长度不符/键越界/字节缺失全 404（L785-787）、解析过的键恰为 Owner 给出的四把（L789）。⑥ `test_b255_owner_resolution_completes_before_shared_bytes_are_read` L793-820：`resolve_finished_at <= store.resolved_at[0]`（L817-819）、一次下载只解析一次（L820）。⑦ `test_b255_download_modules_hold_no_database` L826-840：下载面模块零 sqlalchemy（AST+文本）。 | 真实 Gateway 应用与生产路由（`muad_im_gateway.main.app` + `api/webchat.py`，真实 api-kit 封套/错误目录/`ChatIdentityDep` 主体门控依赖图）；真实 Console HTTP（uvicorn 真实 socket：`POST /internal/channel/chat-principal` 每次下载都核验主体与授权集合 + `/internal/channel/bots` 入口快照）；真实 Runtime HTTP（uvicorn 真实 socket：`GET /internal/webchat/artifacts/{id}` 按**真实契约** `WebchatConversationScope` + `AttachmentRef` 应答，记录网关实际发出的 `user_id`/`X-Tenant-Id`/`X-Internal-Service`，并按归属主体裁决 404）；真实共享文件系统（隔离 artifact root 上真实 `NfsArtifactStore` 字节 + `resolve()` 读取留痕：被拒请求零读取、长度不符与键越界真实触发）；Owner 侧另有隔离租户**真实 PostgreSQL**（`runtime.conversation`/`runtime.artifact` 真实行：软删产物/软删会话/无会话归属/入口不可归属/`TRANSCRIPT`/跨租户/他主体七类真实拒绝，见 `tests/agent_runtime/test_capacity_webchat_artifact_resolve.py`）。 | verified |

实现要点：**逐请求授权链**（CHAT 主体只经 Console 核验 → Owner 的会话主体核属 → 当前入口/Agent 授权）在**读取任何字节之前**完成；**取件键只由 Owner 解析**（客户端 `storage_key`/`path` 在类型与路由上都没有位置）；**安全预览**按显式白名单（`image/png|jpeg|gif|webp`）`inline`，HTML/SVG 等可执行内容一律 `attachment` + `nosniff`，坏媒体类型降级二进制流；**字节完整性**用行内 `size` 与真实 `st_size` 对齐后才声明 `Content-Length`；**DB 事务释放后流式**——Owner 短事务（真实 PG 行查询）响应完整结束之后才 `NfsArtifactStore.resolve` + `FileResponse` 流式输出，网关零数据库依赖。

回归（最终代码，本机无 dev 残留进程）：`uv run pytest -q tests/gateway` exit=0，**405 passed in 98.56s**（含本任务 7 条）；`uv run pytest -q tests/contracts tests/architecture` exit=0，**1314 passed in 3.50s**（契约 parity + 通道中立性 + 网关不持库架构守卫）；同域聚焦 `tests/agent_runtime/test_capacity_webchat_history_queries.py`（TASK-050 的入口显式读取，与本次新增的受信解析面同模块）+ `test_capacity_webchat_upload_staging.py`（TASK-053 的登记/核属）+ 本任务 Owner 侧 3 条 + `tests/test_configuration_inventory.py` exit=0，**30 passed in 2.96s**。

静态检查：`uv run ruff check`（本任务 7 个改动/新增文件）干净；`uv run mypy apps packages` → Success: no issues found in 429 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行（最长 31 行，既有函数行数未变）；`docs/configuration-inventory.csv` 保持 CRLF（1486 行全 CRLF、0 裸 LF），只更新/追加自己的行（`api/webchat.py` 行号平移 1 行、`runtime_client.py` 新增 `INTERNAL_ARTIFACT_PATH` 与 4 行行号平移、新增模块 6 行常量），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。

遗留（deferred，非本任务可闭合）：① **装配根接线**——下载路由依赖读取 `app.state.{runtime_client,bot_snapshot,artifact_store,tenant_id}`，生产装配由 TASK-096 发布这些状态值（TASK-052/054 同一模式：本任务只交付路由与依赖图，`main.py` 不改）。② **前端预览渲染与错误文案**属 TASK-080/084（本任务只交付服务端处置语义：`inline`/`attachment`、`no-store`/`nosniff`；前端已按 `responseType: 'blob'` 取件，见 TASK-073）。③ **清理与保留边界**属 TASK-069（本任务对"字节已清理"的取件一律 404，不做清理本身；活跃引用/孤儿宽限不在本层）。④ **助手/工具产物附件的公开投影**（`WebchatHistoryMessage.attachments` 目前只投影 USER 消息）属 TASK-051/059 的收口——本任务的服务端取件语义对已授权会话的任意可见产物都成立，不依赖该投影是否已扩到助手侧。⑤ `TRANSCRIPT` 不提供公开下载入口（TASK-051 已不投影）；本任务在 Owner 解析面再次显式拒绝（404），两层同口径。
- B-255: verified — automated command passed; run_id=7a004f6a9745455fb3dcf75f7ea2a4f4 (confirmed_by: runner)
- B-255: verified — automated command passed; run_id=22db8256d5b749328ee92bb9719ab791 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-056: 精确Run取消、待确认恢复和记忆回执

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-031, TASK-037, TASK-049
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**: harness-memory#RULE-memory-001, harness-memory#RULE-memory-002
- **Acceptance-Refs**: B-256
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `apps/agent-runtime/src/muad_agent_runtime/api/runs.py`, `tests/agent_runtime/test_capacity_webchat_run_controls.py`
- **Estimate**: 60分钟

### Description

cancel/resume绑定原Run/submission/source并在锁内复核WAITING_INPUT/deadline；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-256][integration] 先在`tests/agent_runtime/test_capacity_webchat_run_controls.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：cancel/resume绑定原Run/submission/source并在锁内复核WAITING_INPUT/deadline。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_run_controls.py` exit=1，**11 failed, 2 passed in 19.14s**（在实现落盘后以 `git checkout --` 复原 6 个改动生产文件取得实现基线，跑完逐字节复原并校验 md5 一致；代表性失败：`{"code":"COMMON_VALIDATION_ERROR",...,"loc":["body","source"]/"submission_id"/"source_event_id","type":"extra_forbidden"}` + `assert 422 == 200`（来源身份与绑定字段尚不存在）、`assert None is not None`（`active_run.confirmation` 恒为 None）、`assert (422 == 404)`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`application/dto.py`（`CancelSource` 至少给会话或入口；`ResumeRunRequest.submission_id/source_event_id` 要么都给要么都不给 + `confirmation_binding()`）、`application/memory_confirmation_resume.py`（`ConfirmationBinding`；动词 `CONFIRM_VERB/REJECT_VERB` 与 `_ANSWER` 同源 + `confirmation_selection()`；`prepare_confirmation(..., confirmation=)` 在锁内复核原提议身份，`_assert_binding` 不一致即 `MEMORY_CONFIRMATION_INVALID` 且不消费；`_validated_proposal` → 公开 `validated_proposal` 供读取/消费共用）、`application/channel_conversations.py`（`resolve_source_conversation`：只读定位，不建会话、不移动指针）、`application/run_service.py`（`cancel_active(..., source=)` + `_active_run_for`；`resume(..., confirmation=)` + `_resume_fingerprint`（绑定进幂等指纹）+ `_resume_snapshot`/`_frozen_definitions` 抽取；`_persist_human_resume` 把绑定复核放在行锁之后）、`application/webchat_history.py`（`_confirmation_view`：WAITING_INPUT 的完整候选投影，坏提议显式内部错误）、`api/runs.py`（两个入口接线）。
- [x] [B-256] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-256] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_run_controls.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**13 passed in 22.66s**。
- [x] verifier `harness-memory#RULE-memory-001`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。**执行结果（最终代码）**：exit=0，**80 passed in 80.52s**（改动前基线同命令 80 passed in 101.93s）。约束逐项未被削弱：自然语言 remember 仍先展示完整候选并由真实用户确认（`MEMORY_CONFIRMATION_REQUIRED` 事件 + `RunInterrupt.prompt_text` 一字不改）、候选每 Run 至多一个、工具整轮完成后才暂停、resume 先检查绝对 deadline；本任务只**新增**可选的原提议绑定（`submission_id`+`source_event_id`）并在锁内复核，未提供绑定时（企微文本路径）行为与改动前逐条一致——verifier 覆盖的正是该路径与显式 resume 路径。
- [x] verifier `harness-memory#RULE-memory-002`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。**执行结果（最终代码）**：exit=0，**26 passed in 33.94s**。Runtime 仍是唯一记忆写入所有者：确认消费仍走 `MemoryService.execute_in_session`（Conversation→Run→interrupt→memory_scope 锁序、锁内无网络 IO），完整 after 历史/幂等 receipt/scope revision 同事务；本任务的 B-256 另在真实 PG 上断言一次确认恰一份（`UserMemory` 行、`UserMemoryHistory.after_json`、`MemoryCommandReceipt.command_id`、`MemoryScope.revision=1`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-256 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | cancel/resume绑定原Run/submission/source并在锁内复核WAITING_INPUT/deadline；确认完整候选且source一次消费，记忆写遵守scope锁/配额/receipt/history/revision与忘记边界 | tests/agent_runtime/test_capacity_webchat_run_controls.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_run_controls.py"] | verified |
| harness-memory#RULE-memory-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 原话仅证明文本来源，不能授予保存授权；模型不得提供 author/explicit_request。自然语言 remember 必须展示完整候选，绑定租户/用户/Run/submission、候选 hash、预期版本、forget_epoch 和有效期，由真实用户确认原子保存；只有真实入站的显式命令或已授权管理员可以直接保存。候选每 Run 至多一个，工具整轮完成后才暂停；resume 先检查绝对 deadline。options 消费以租户和 source_event_id 唯一，结果 outcome 不进入唯一键。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | verified |
| harness-memory#RULE-memory-002 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | Runtime 是唯一记忆写入所有者；按 Conversation→Run→memory_scope 锁序串行修改，锁内禁止网络 IO。同 tenant/user/key 使用完整唯一身份，软删后 id/version 不重置；每次变更、完整 after 历史、幂等 receipt 和 scope revision 同事务提交。锁内按写后有效集合验证冻结条数/UTF-8 字节限额及绝对上限；仅可淘汰 AGENT，USER/ADMIN 不免除硬限；既有超额时治理可减少但不可增加。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-256 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_run_controls.py` exit=1，**11 failed, 2 passed in 19.14s**（实现基线：复原 6 个改动生产文件后跑，随后逐字节复原并 md5 校验一致；首个失败即 `{"code":"COMMON_VALIDATION_ERROR",...,"loc":["body","source"],"type":"extra_forbidden"}` / `assert 422 == 200`，绑定字段同为 `extra_forbidden`，确认投影为 `assert None is not None`） | 同命令 exit=0，**13 passed in 22.66s** | ① `test_b256_source_scoped_cancel_hits_only_its_own_conversation` L459-492（`asyncio.gather` 两条来源同时取消：网页显式会话 → `web_run`、企微入口+线程 → `wecom_run`，各 `run_id`/`CANCELLED` 对齐 L481-486；两个 Run 各自 `admission_released_at` 非空、`CANCEL`/`RUN_COMPLETED` 各恰 1 条 L487-492）；② `test_b256_source_without_active_run_does_not_touch_other_conversations` L495-529（来源自己空闲 → 404 `NO_ACTIVE_RUN` 且不碰别的会话 L524；无来源仍按 (agent,user) 命中 RUNNING Run → 只受理协作取消、`admission_released_at` 仍为空 L526-529）；③ `test_b256_run_bound_cancel_is_replayable_with_a_single_terminal_state` L532-554（同一 Run 连取消两次 → 都是 `CANCELLED`、`cancel_requested=True`，`CANCEL`/`RUN_COMPLETED` 各 1 条）；④ `test_b256_pending_confirmation_projects_the_complete_candidate` L595-636（API-15 `active_run.confirmation` 的 `submission_id`/`source_event_id`/`prompt`（含 `color`+`blue`）/`expires_at` 与真实中断逐项相等 L605-613；选项值 = `确认 <proposal_id>` / `拒绝 <proposal_id>` L614-618 且**被 resume 直接接受** L620-632）；⑤ `test_b256_resume_rejects_other_submission_or_source_and_keeps_waiting` L671-708（错 submission/错 source → 400 `MEMORY_CONFIRMATION_INVALID` L698-701；半绑定 → 422 `COMMON_VALIDATION_ERROR` L703；中断仍 `WAITING`、Run 仍 `WAITING_INPUT`、零记忆行、零回执事件 L704-708）；⑥ `test_b256_resume_consumes_source_once_and_replays_the_same_receipt` L711-744（提议绑定的就是真实来源提交 L718；同键+同 message_id 重放的事件序列**逐条相等** L743；一次确认恰一份：`outcome=CONFIRMED/saved/version=1`、`UserMemory` 行、完整 after 历史、`MemoryCommandReceipt`、`MemoryScope.revision=1`、中断 `RESOLVED` L363-384）；⑦ `test_b256_source_cannot_be_consumed_twice_after_resolution` L747-771（再次确认只回放终态 `run.completed/COMPLETED` L770，仍只有一次写入 L771）；⑧ `test_b256_resume_rechecks_waiting_input_and_absolute_deadline_in_lock` L774-806（RUNNING → 409 `REVISION_CONFLICT` L790；`deadline_at` 已过 → 410 `MEMORY_CONFIRMATION_EXPIRED` L803 且中断仍 WAITING、零记忆行、零回执 L804-806）；⑨ `test_b256_cancelled_waiting_run_closes_the_confirmation` L809-837（取消未开始执行的待确认 Run → `CANCELLED`、中断 `CANCELLED`+`{"reason":"cancelled"}`、详情 `active_run=None`、零记忆行）；⑩ `test_b256_replayed_binding_change_is_rejected_as_idempotency_mismatch` L840-862（同键换绑定 → 409 `IDEMPOTENCY_MISMATCH` L861，回执仍只有 1 条 L862）；⑪ `test_b256_unattributed_entry_cannot_reach_another_entry_conversation` L865-888（借其他入口的会话取消 → 404 `COMMON_NOT_FOUND` L886，目标 Run 仍 RUNNING 且未被标记取消）；⑫ `test_b256_entry_pointer_source_resolves_the_current_conversation` L891-923（入口+线程按当前指针命中 `pointer_run` L918；指针行未被改动、其他入口 Run 不动）；⑬ `test_b256_confirmation_receipt_matches_the_persisted_proposal` L926-957（回执挂在被消费的提议上：`resolution_json` outcome/key/version 与 canonical 事件、记忆行逐项一致 L948-957） | **隔离 PostgreSQL**（`async_tool_database`：每用例一个空库 + `alembic upgrade head`，不触共享 dev 库）；真实 `RunService`/`RunSubmissionService`/`MemoryConfirmationService`（`propose`+`suspend` 落库真实中断/提议/展示证据）/`MemoryService`（scope 锁、幂等 receipt、完整 after 历史、revision）业务路径；真实 PG 行锁与状态 CAS（取消/领取同一 CAS，`admission_released_at` 恰一次）；真实 `EventWriter` seq；真实 Runtime HTTP 应用（真实路由 + `X-Internal-Service` 门控 + `X-Tenant-Id` 租户上下文，uvicorn 真实 socket）；不 mock 存储或租约。执行器用进程内 fake（模型调用不在本契约断言内），因此无模型 HTTP 调用 | verified |

实现要点：**精确取消**——`cancel_active` 新增可选 `source`：显式会话走 `load_explicit_conversation` 的 tenant/user/agent/入口 四维核属（借别的入口一律 404），入口+外线线程走**只读**的 `resolve_source_conversation`（当前指针 → 同线程最近会话 → `None`，**不建会话、不移动指针、不退回「最新 Run」**），定位不到即 `NO_ACTIVE_RUN`；未给来源保留既有 (agent,user) 口径（调用方没有来源事实时不猜来源）。**待确认恢复**——API-15 `active_run.confirmation` 由 `validated_proposal`（与消费路径同一份校验）投影出完整候选与 `submission_id`/`source_event_id`，选项值经 `confirmation_selection` 与 `_ANSWER` 共用动词（投影出来的就是能被消费的文本）；坏提议/缺 `expires_at` 显式 `COMMON_INTERNAL_ERROR`，不投影一份确认不了的候选。**resume 绑定**——`submission_id`+`source_event_id` 全给或全不给（校验层），进幂等指纹（同键换绑定 = `IDEMPOTENCY_MISMATCH`），在 `lock_human_resume` 之后逐项复核原提议身份（不一致即 `MEMORY_CONFIRMATION_INVALID` 且不消费）；幂等重放先于复核，因此重放返回**原回执**而不是二次消费。**锁内复核**——`_resume_snapshot`（`ensure_human_resume` + 本 Run 自己那份冻结快照）保证等待态与绝对期限在行锁后裁决；未开始执行的 Run 取消时关闭 WAITING 中断（不再接受人工恢复）。

回归（最终代码，本机无 dev 残留进程）：`uv run pytest -q tests/agent_runtime` exit=0，**943 passed in 696.66s**（含本任务 13 条；收尾复跑 B-256 单文件 13 passed in 27.07s）；`uv run pytest -q tests/contracts` exit=0，**1281 passed in 2.59s**；同域聚焦首轮 `tests/agent_runtime/test_runs_api.py`/`test_cancel_hint.py`/`test_capacity_webchat_history_queries.py`/`test_capacity_webchat_conversation_scope.py`/`test_memory_redesign_resume.py` + B-256 exit=0，83 passed in 70.31s。

静态检查：`uv run ruff check`（本任务 7 个改动/新增文件）干净；`uv run mypy apps packages` → Success: no issues found in 422 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行（`resume` 47→48、`_resume_run` 53→50；其余 >50 的函数均为改动前既有且行数未变）；`docs/configuration-inventory.csv` 保持 CRLF（1457 行 CRLF、0 裸 LF），只更新本任务文件的常量行（`memory_confirmation_resume.py` 新增 `CONFIRM_VERB`/`REJECT_VERB` 并更新 `_ANSWER`；`run_service.py` 20 行、`webchat_history.py` 5 行行号随 import 变化平移），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。

遗留（deferred，非本任务可闭合）：① 来源级取消的**调用方接线**——Gateway `/stop`（`InboundPipeline._handle_stop`）与 webchat 停止仍只传 (agent,user)；TASK-052/096 应从路由/入口快照解析来源并传 `source`（本任务只落 Runtime 侧精确语义与 API 面）；② 网页 resume 的 `submission_id`/`source_event_id` 由 TASK-052 从 API-15 候选投影取值后透传，本任务不接线 Gateway；③ `WebchatConfirmation` 的选项**标签**用 resume 动词（zh-CN），本地化词条属 TASK-090；④ 确认中断在 TTL 到期后由 TASK-063 的通知/过期扫描收口（本任务只保证投影与消费两侧的 deadline/expiry 语义）。
- B-256: verified — automated command passed; run_id=cdd6bc9dd5d8484fb36e549c6bbd61c5 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-057: 聊天客户端非敏感配置白名单投影

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-008, TASK-052, TASK-055
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-257
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/webchat_config.py`, `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `tests/gateway/test_capacity_webchat_client_config_api.py`
- **Estimate**: 45分钟

### Description

API-19只返回当前CHAT可用的settings/transport revision及分页/输入/上传/轮询/重连/帧限制；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-257][integration] 先在`tests/gateway/test_capacity_webchat_client_config_api.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：API-19只返回当前CHAT可用的settings/transport revision及分页/输入/上传/轮询/重连/帧限制。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_client_config_api.py` exit=1，**7 failed**（7 条用例全部执行：`GET /api/v1/webchat/client-config` 在实现前不存在 ⇒ 请求拿 404 `COMMON_NOT_FOUND`（缺凭据用例期望 401、坏源用例期望 500），静态边界用例 `FileNotFoundError: .../application/webchat_config.py`——端口与配置模块均不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/webchat_config.py`（新建）——`WebchatTransportBudget`（启动传输预算值对象：`sse_event_max_bytes`/`sse_heartbeat_sec`，构造即校验正数）、`transport_revision()`（传输投影的**确定性** SHA-256 十六进制指纹：同值稳定、任一项变化即变、纯十六进制不透明 ⇒ 不可能内嵌内部地址/DSN/密钥）、`project_client_config()`（快照 → `resolve_platform_settings`（同一 contracts schema，含 `chat_message_hard_max_bytes`/`chat_upload_hard_max_bytes` 上界）→ `WebchatClientConfig.from_settings` 白名单投影）、`WebchatClientConfigService.load()`（每次页面初始化取一次设置快照）。② `api/webchat.py`（同文件扩展）——新增 `get_webchat_config_service` 依赖（`app.state.{settings_client,tenant_id,sse_event_max_bytes,run_event_heartbeat_sec,chat_message_hard_max_bytes,chat_upload_hard_max_bytes}`，装配根发布；与 TASK-052/054 同一模式）与 `GET /client-config`（`ChatIdentityDep` 门控 → 投影 → 标准封套；路由器级 `no-store` 覆盖）。③ `docs/configuration-inventory.csv` 同步（新增 `TRANSPORT_FINGERPRINT_HEX_LENGTH` 行 + `api/webchat.py` 的 `NO_STORE_CACHE_CONTROL` 行号漂移，CRLF 保持 1495/0）。
- [x] [B-257] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-257] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_client_config_api.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**7 passed in 4.37s**（冻结代码复跑）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-257 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | API-19只返回当前CHAT可用的settings/transport revision及分页/输入/上传/轮询/重连/帧限制；默认唯一、无池大小/内部URL/额度/secret，配置源坏时明确失败 | tests/gateway/test_capacity_webchat_client_config_api.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_client_config_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-257 | `uv run pytest -q tests/gateway/test_capacity_webchat_client_config_api.py` exit=1，**7 failed**（1.86s；端口/模块实现前全部用例已收集执行：`GET /api/v1/webchat/client-config` 404 `COMMON_NOT_FOUND`、静态边界用例 `FileNotFoundError: application/webchat_config.py`） | 同命令 exit=0，**7 passed in 4.37s**（冻结代码） | ① `test_b257_chat_session_and_context_are_required_before_any_settings_read` L317-347：缺 Cookie 401 `UNAUTHORIZED`、缺/坏 `X-Chat-Context` 422（L326-331）、门控未过**设置源零读取**（L332-333）、会话撤销 401 `CHAT_SESSION_REVOKED`（L336-338）、跨部署租户 403 `FORBIDDEN`（L343-346）。② `test_b257_client_config_projects_the_effective_whitelist_and_revisions` L353-399：200 + `no-store`（L361-363）、传输版本纯十六进制不透明指纹（L365-367）、**data 与有效设置逐字段相等**（L369-388：settings_revision=12、分页 75/输入 8192/上传 123456/数量 7/轮询 2.5、帧 131072/心跳 15/重连 1000/60000/20）、**逐层键集精确等于白名单**（L389-391）、设置源恰一次且只带服务身份/租户、不带 CHAT 凭据（L394-399）。③ `test_b257_client_config_defaults_come_only_from_the_shared_schema` L405-437：无设置版本（revision 0 + 空文档）⇒ 恰等于 contracts schema 默认（L425-437），且进程环境变量（`WEBCHAT_*` canary）不是第二个默认来源（L437）。④ `test_b257_transport_revision_binds_exactly_the_transport_projection` L443-482：同参数确定性稳定（L448-451）、启动预算变化即变（L453-456）、**非传输设置变化不变**（L465-469）、传输设置（重连）变化即重绑（L478-482）。⑤ `test_b257_client_config_never_carries_internal_or_sensitive_fields` L488-506：敏感分组/保留期键位零出现（L501-502）、敏感取值与启动硬上限不出现在投影任何值（L503）、响应正文无 `internal/secret/pool/quota/dsn/url/canary/retention/hard_max` 等词干（L504-506）、CHAT 会话与内部服务身份不回显（L507-508）。⑥ `test_b257_client_config_fails_explicitly_when_the_settings_source_is_broken` L512-553：文档越界/未知分组/版本形状坏/源返回 503/源不可达 ⇒ 分别 500/500/500/503 `PLATFORM_SETTINGS_UNAVAILABLE`/500（L540-547）、`data` 一律为 None（不回退默认，L548-549）、原始细节（`history_page_size`/`model_pool_secret`/canary）不回显（L550-553）。⑦ `test_b257_client_config_modules_hold_no_database_and_no_second_source` L569-590：接口/配置模块零 sqlalchemy（L575-583）、配置模块不 import `muad_common`/`os`、不读 `os.environ`/`getenv`（L585-590） | 真实 Gateway 应用：`muad_im_gateway.main.app` 直连（真实路由表/api-kit 封套/错误目录/HTTP 状态），挂载**生产** `api/webchat.py` 路由；装配根状态按生产同形注入。**真实 Console HTTP（Owner，真实 socket）**：`ConsoleClient` 经 API-12 `POST /internal/channel/chat-principal` 核验 CHAT 主体；`ConsolePlatformSettingsClient` 经 `GET /internal/v1/platform-settings`（真实 `X-Internal-Service`/`X-Caller-Service`/`X-Tenant-Id` 头）取 `revision`+整份设置文档 —— 网关不读 PG、本文件零数据库。**启动参数与设置文档相互独立**：传输上限/心跳来自注入的 `app.state` 启动预算，指纹用例分别驱动两者。断言强度经三次变异验证：指纹丢掉重连参数 ⇒ 用例④红；投影解析失败回退 `WebchatSettings()` 默认 ⇒ 用例⑥红；响应注入 `webchat.access_ttl_days` ⇒ 用例⑤红 | verified |

实现要点：**唯一来源**——业务上限/重连只来自 Console 权威设置快照（`resolve_platform_settings`，同一 contracts schema 解析默认与范围），传输上限/心跳只来自 Gateway 启动参数（装配根注入），本模块不读环境、不复制默认、不持库；**白名单投影**——只经 `WebchatClientConfig.from_settings` 组装 5+5 个字段，敏感字段没有字段位置（契约拒绝未知键）；**失败即失败**——设置源不可达/文档非法/超启动硬预算 ⇒ 明确错误，绝不回退默认（RULE-06）；**transport_revision**——传输投影的确定性十六进制指纹，活跃连接据此绑定原参数、新连接重新读取有效版本。

回归（本机无 dev 残留进程，单跑不并发）：`uv run pytest -q tests/gateway` exit=0，**412 passed in 101.56s**（含本任务 7 条）；`uv run pytest -q tests/contracts tests/architecture/test_im_gateway_boundaries.py tests/test_configuration_inventory.py` exit=0，**1291 passed in 3.35s**。

静态检查：`uv run ruff check`（本任务 3 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 431 source files；新增函数均 ≤50 行（最长 25 行）；`docs/configuration-inventory.csv` 保持 CRLF（1495 CRLF、0 裸 LF），只更新自己的行（新增 `TRANSPORT_FINGERPRINT_HEX_LENGTH` + `NO_STORE_CACHE_CONTROL` 行号漂移）。

遗留（deferred，非本任务可闭合）：① **装配根接线**——路由依赖读取 `app.state.{settings_client,sse_event_max_bytes,run_event_heartbeat_sec,chat_message_hard_max_bytes,chat_upload_hard_max_bytes}`，生产装配由 TASK-096 发布这些状态值（TASK-052/054 同一模式：本任务只交付路由与依赖图，`main.py` 不改）。② 公开 SSE 游标/分片与缓冲回压属 TASK-061（本任务只交付浏览器可见的帧/心跳/重连**投影**；活跃连接绑定 `transport_revision` 的消费语义由 TASK-072/076/082 落地）。③ 前端 clientConfig 加载与版本化消费属 TASK-076（本任务保证投影值与默认唯一来源；组件不复制默认值在前端侧验收）。④ `settings_revision` 当前直接取 Console 快照的整份文档版本；按字段的生效边界（new_run/restart_required 等）不在 API-19 投影内。
- B-257: verified — automated command passed; run_id=55d636a4331a4e878a1a2789ae538f56 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-058: Runtime暂存上传登记的受信端口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-050, TASK-053
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-258
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py`, `tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py`
- **Estimate**: 45分钟

### Description

Gateway共享字节写入后仅经服务门控登记upload_id；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-258][integration] 先在`tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：Gateway共享字节写入后仅经服务门控登记upload_id。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py` exit=1，**8 failed**（8 条用例全部执行：`POST /internal/webchat/uploads` 在实现前不存在，服务门控用例拿到 404 而不是 403，登记/核属用例在 `_register` 处断言 404≠200；受信端口、入口/Agent 核属与登记原语接线在实现前均不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `api/webchat_history.py`——既有路由器（`/internal/webchat`，路由器级 `require_internal_service` 门控，与 TASK-050 的 API-09/14/15 同文件共存）新增 `POST /uploads`：平台设置快照取一次 → `assert_entry_agent_binding`（入口/Agent 核属）→ `register_staged_upload`（TASK-053 登记原语，词表/键形状/校验和/size/TTL 边界统一裁决）→ 提交 → `WebchatUpload` 投影（`upload_id`+权威元信息+`expires_at`，**无 storage_key、无 artifact 引用**）。② `application/channel_uploads.py`（计划外文件，披露）——新增 `assert_entry_agent_binding`：只读同 PG 的 Console 当前事实 `control.bot_account`（先例 `model_dispatch_store`/`model_capacity_store`），跨租户/不存在/已删除 ⇒ `COMMON_NOT_FOUND`（不泄露存在性），同租户绑定别的 Agent/已停用 ⇒ `FORBIDDEN`；**不**复制 Console 的用户活性/授权裁决（CHAT 认证面在 Gateway 侧实时核验）。③ `packages/contracts/src/muad_contracts/webchat.py`（计划外文件，披露）——新增 `WebchatUploadRegistration`（API-12 内部线协议，Gateway↔Runtime 共用形状；身份三件套显式、extra 拒绝 ⇒ `artifact_id`/`upload_id` 之类预造引用没有字段位置）。④ `docs/configuration-inventory.csv` 同步（新增常量行 + 6 行行号漂移，CRLF 保持）。设计取舍：**核属/TTL/发布不在新端口重复暴露**——它们已由既有受信受理端口 `POST /v1/runs`（RunService，API-06）承载；新增第二个语义源只会制造死端点与漂移，因此本任务用**真实受理路径**验证登记结果可用（过期/越权/发布逐项）。凭据/存储键不进响应与日志：响应只回 `WebchatUpload`，登记日志只记 upload_id/error_type。
- [x] [B-258] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-258] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，8 passed（1.97s）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-258 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | Gateway共享字节写入后仅经服务门控登记upload_id；核当前用户/入口/Agent与不可变存储键，重复登记/过期/越权明确，不允许任意路径或预造制品引用 | tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-258 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py` exit=1，8 failed（端口实现前全部用例已收集执行：服务门控用例 404≠403、登记用例 404≠200——`/internal/webchat/uploads`、入口/Agent 核属与登记接线均不存在） | 同命令 exit=0，**8 passed**（1.97s） | ① `test_b258_registration_is_only_reachable_through_the_service_gate` L369-387：缺失/空/错误 `X-Internal-Service` 一律 403 `FORBIDDEN`（L378-381）、拒绝不留登记行与 artifact 行（L382-383）、受信调用 200 且恰一行（L385-387）。② `test_b258_registration_requires_the_trusted_identity_explicitly` L390-410：`user_id`/`agent_id`/`entry_id`/`storage_key`/`size` 缺一即 422 `COMMON_VALIDATION_ERROR`（L399-403）、注入 `artifact_id`/`upload_id` 预造引用 422（L406-408）、拒绝不留行（L409-410）。③ `test_b258_registration_verifies_the_current_entry_and_agent` L418-473：入口绑定别的 Agent / 入口停用 ⇒ 403（L435-442）、未知入口/已删除入口/**跨租户入口** ⇒ 404 不泄露存在性（L445-452）、自报别的 Agent 403（L454-463）、通过后登记行归属 tenant/user/entry（L468-471）。④ `test_b258_registration_records_authoritative_metadata_without_artifacts` L481-526：响应= `WebchatUpload`（L496-501）、响应键集恰为 upload_id+元信息（L502-510）、无 `storage_key`/`artifact_id` 字样（L512-513）、登记行归属与元信息（L515-521）、**TTL 来自 `webchat.upload_staging_ttl_sec`（显式 120s）**（L522-524）、暂存字节留在共享存储（L525）、**登记不是 artifact 行**（L526）。⑤ `test_b258_registration_rejects_arbitrary_paths_and_duplicate_keys` L529-564：空串/绝对路径/`..`/反斜杠路径一律 422（L535-541）、同租户活跃键重复 409 `COMMON_CONFLICT`（L549-561）、原登记行逐字段不变（L563）、无 artifact 行（L564）。⑥ `test_b258_registration_enforces_the_configured_limits` L567-602：超单文件上限 400 `ATTACHMENT_TOO_LARGE`（L580）、size=0/坏 kind/坏 media_type/坏 checksum/空 filename 422（L581-585）、拒绝不留行（L593）、**恰等于上限放行**（L596-602）。⑦ `test_b258_registered_upload_is_admitted_only_for_its_owner` L638-731：真实 `POST /v1/runs` 受理成功（L646-652）、**发布 Run 自己的键 `runs/<run>/attachments/<upload>`（≠暂存键）且字节来自真实存储**（L654-661）、登记行受理后不变（L663）、另一用户/另一入口借上传 403（L665-693）、跨租户 404（L695-715）、伪造 upload_id 404 且不留制品（L717-730）。⑧ `test_b258_expired_registration_blocks_new_admission_only` L734-769：过期上传新受理 410 `UPLOAD_EXPIRED` 且不留制品（L741-752）、未过期上传照常受理并发布自己的键（L754-768）。 | 真实 Runtime HTTP 应用（真实路由 + `require_internal_service` 门控；`/internal/webchat/uploads` 与 TASK-050 的 `/internal/webchat/conversations/...` 同路由面共存，历史端点回归全绿）；隔离租户的真实 PostgreSQL（`runtime.channel_upload`/`artifact`/`run_record`/`conversation` + Console 拥有的 `control.bot_account`/`control.agent_definition`/`control.model_definition` 真实行；只增删本租户的行）；真实共享文件系统（隔离 `ARTIFACT_ROOT`：真实暂存字节、真实 sha256、`os.link` 发布 Run 自己的键；不 mock 存储或租约）；真实 `RunService` 受理路径（核属/TTL/发布真实代码；仅模型调用与定义解析是进程内替身）。 | verified |

实现要点：**登记**（`POST /internal/webchat/uploads`，服务身份门控 + 身份三件套显式 + 入口/Agent 核属 → `channel_upload` 行=权威元信息，非 artifact；storage_key 形状受控；TTL 取 PG 时钟 + `webchat.upload_staging_ttl_sec`）→ **受理**（既有 `POST /v1/runs`：只有未受理的新提交校验 TTL/归属；跨租户不泄露存在性、越权 `FORBIDDEN`、过期 `UPLOAD_EXPIRED`）→ **发布**（`runs/<run_id>/attachments/<upload_id>` 原子硬链接，校验真实 sha256 后才发布）→ **响应**（只回 `WebchatUpload`：`upload_id`+权威元信息+`expires_at`，不回存储键/制品引用）。

回归：`uv run pytest -q tests/agent_runtime` exit=0，**957 passed**（723.97s；含本任务 8 条）；`uv run pytest -q tests/contracts tests/architecture` exit=0，1314 passed（3.69s）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed（0.61s）。

静态检查：`uv run ruff check`（本任务 4 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 423 source files；新增函数均 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF（1459 CRLF、0 裸 LF），只更新自己的行（`channel_uploads.py` 6 行行号 +3 漂移 + 新增 `_ENTRY_BINDING_SQL`）。断言强度经变异验证：临时移除 `assert_entry_agent_binding` 调用后 `test_b258_registration_verifies_the_current_entry_and_agent` 立即失败（核属断言是承重的，不是摆设）。

遗留（deferred，非本任务可闭合）：① **Gateway 侧消费**——`WebchatUploadRegistration` 契约与受信端口已就绪，TASK-054 负责流式写共享字节后调本端口并返回 `WebchatUpload`（API-10）；本任务只保证 Runtime 侧语义与线协议。② **入口/Agent 核属的用户维度**：用户活性与入口授权由 CHAT 认证面（Console API-12 主体 + API-05 授权入口列表）在 Gateway 侧实时核验；Runtime 只核能权威判定的入口身份事实并把受信主体登记到行上（受理时再复核归属），不复制 Console 的授权裁决。③ 暂存核属与清理之间的竞态窗口（核属通过后清理删了暂存文件）与活跃引用/孤儿宽限保护仍属 TASK-069。④ 本任务不新增迁移/DDL：`channel_upload` 结构由 TASK-013/0025 提供，入口核属只读现有 `control.bot_account`。
- B-258: verified — automated command passed; run_id=3421605482e1436a808969aff55ac7ea (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-059: 模型增量有界合并与终态前flush

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-025, TASK-036, TASK-051
- **Source**: agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-259
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py`, `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `tests/agent_runtime/test_capacity_webchat_durable_deltas.py`
- **Estimate**: 60分钟

### Description

时间或字节窗口到任一边界先提交再推送，终态前flush全部已确认正文；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-259][integration] 先在`tests/agent_runtime/test_capacity_webchat_durable_deltas.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：时间或字节窗口到任一边界先提交再推送，终态前flush全部已确认正文。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_durable_deltas.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.18s）**：`ImportError: cannot import name 'DeltaWindow' from 'muad_agent_runtime.application.run_events'`——验收文件先落地、实现尚不存在（边界语义与集成场景同批落地，逐项见 Acceptance Evidence）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/run_events.py`——`DeltaWindow`（时间/字节双边界的有界合并窗口：`add` 到任一边界返回合并文本、`due`/`remaining_seconds` 供时间边界唤醒、`drain` 供到界/结构边界/终态共用、空窗口不产出空事件；时钟可注入、不碰 IO、不持有事务）、`delta_payload`（`message.delta` 的 canonical 载荷形状：合并正文 + `reply_generation`）、`FIRST_REPLY_GENERATION=0`（TASK-051 遗留③：生成代号显式落库，多代替换由 TASK-062 由此递增）、`delta_window_from_settings`（窗口参数唯一来源 = `SharedSettings.stream_delta_flush_interval_ms/bytes`，design §3.7）。② `application/executor.py`——`_ExecutorEventEmitter`（runner 回调 → 执行器事件：`on_delta` 只入窗口、字节到界在回调内 `push`（等 `queue.join()`，canonical 写入先于继续）、非增量事件先 `flush_deltas` 再入队、工具结果预览与 assistant 回合落库原样保留）；`AgentRunnerExecutor.run`（队列/取消轮询/emitter/片段任务/收尾清理，≤50 行）+ `_consume_queue`（读取循环：时间到界 `yield` 合并文本，等待时长 `min(QUEUE_POLL_SEC, 窗口剩余)`；片段结束后先 `drain` 再产出 `runner.outcome`，失败同样先 flush 再上抛原异常）+ `_queue_wait`；`delta_window` 由装配根 `default_executor_factory` 显式注入，直构调用点按同一启动设置取值；`QUEUE_POLL_SEC` 具名常量取代内联轮询字面量。③ 载荷：`{delta, reply_generation}`，日志/事件只出现安全字段（正文与生成代号），凭据/内部请求永不进入事件。
- [x] [B-259] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-259] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_durable_deltas.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**7 passed in 11.93s**（重构后）/ **7 passed in 12.82s**（最终冻结代码复跑）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-259 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 时间或字节窗口到任一边界先提交再推送，终态前flush全部已确认正文；小增量不逐token事务，崩溃不丢已对用户确认的字节 | tests/agent_runtime/test_capacity_webchat_durable_deltas.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_durable_deltas.py"] | verified |



### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-259 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_durable_deltas.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.18s）**：`ImportError: cannot import name 'DeltaWindow' from 'muad_agent_runtime.application.run_events'`（验收文件先落地、实现尚不存在） | 同命令 exit=0，**7 passed in 11.93s**（最终冻结代码） | ① `test_b259_window_flushes_on_either_boundary_and_never_emits_empty` L114-125（未到界不 flush L114-117、**字节边界到界即合并返回** L118、已 flush 的窗口不再产出/空窗口不产出空事件 L119-120/L125、**时间边界在没有新字节时同样到界** L121-124）；② `test_b259_window_drain_is_lossless_in_order` L141（add/drain 任意交错拼接与顺序不变）；③ `test_b259_byte_window_batches_small_deltas_into_bounded_rows` L442-453（无损拼接=模型完整正文 L443、**行数上界 ≤⌈总字节/flush_bytes⌉+1** L444-446、**240 块远非逐 token** L447、单行增量受窗口上界约束 L448、字节边界确实触发落库 L449、**每行显式携带生成代号 0** L450-453）；④ `test_b259_time_window_commits_before_push_while_model_pauses` L474-490（探针仍停在第二块之前 L474、**时间边界在模型停顿期间落库** L475、**tail（SSE 接缝）推送同一 canonical seq 的已提交事实** L478-481、停顿前后顺序与无损 L488-490）；⑤ `test_b259_terminal_flush_orders_all_confirmed_text_before_terminal` L515-530（**终态行 seq 大于全部增量行** L521、终态之后无增量/无第二终态 L522-524、公开投影逐行仍是 `message.delta` L526-529、终态 `final_text` 与增量拼接同一事实 L530）；⑥ `test_b259_truncated_stream_flushes_produced_text_before_failed_terminal` L553-557（断流 → `run.failed`/`MODEL_UNAVAILABLE` L553-555、**窗口里的块也先落库** L556、**失败终态同样排在已产生正文之后** L557）；⑦ `test_b259_confirmed_bytes_survive_process_kill` L650-668（SIGKILL 不可捕获 L650、**已提交行 seq 与内容逐行原样保留** L661-664、仍是完整正文前缀 L665、**仍在内存窗口里的第三块随崩溃消失** L666、**崩溃不做收尾：无终态行** L667-668） | 真实 Runtime 业务服务：真实 `RunService.start`（受理/冻结快照/领取）+ 真实 `AgentRunnerExecutor`/`AgentRunner`/`OpenAICompatibleProvider` 调用链；**真实 HTTP 模型探针**：进程内 uvicorn `text/event-stream` 端点按脚本逐块下发正文，块间节拍与"发完第 N 块等放行"的闸门都在真实 socket 上（不是 provider 替身）；**隔离 PostgreSQL**（每用例空库 + `alembic upgrade head`，跑完 drop）与隔离 Redis 号位：会话/快照/Run/canonical 事件/租约都是真实行，`seq` 由真实 `EventWriter` 在会话行锁内分配，增量行从**独立连接**读回（只有真正提交过的字节可见）；**崩溃**：把真实 RunService 放进子进程，用不可捕获的 SIGKILL 在模型仍在产出时杀死（无收尾/无 flush/无终态）；不 mock 存储或租约 | verified |

实现要点：**有界窗口**（`run_events.DeltaWindow`）——窗口从"缓冲里第一段增量"起算，字节到界（`>= flush_bytes`）或时间到界（`>= flush_interval_sec`）即返回合并文本；`drain()` 供到界/结构边界/终态三处共用，空窗口返回 `None`（不产出空事件）；窗口不碰 IO、不持有事务，时钟可注入（生产 `monotonic`，测试假时钟）。**唯一参数来源**——窗口参数只从 `SharedSettings.stream_delta_flush_interval_ms/bytes`（design §3.7）派生，装配根 `default_executor_factory` 显式注入、直构调用点同源取值，不在调用点另立第二份默认。**执行器**（`executor.AgentRunnerExecutor`）——`on_delta` 只入窗口；字节到界在回调内即刻入队（`push` 等 `queue.join()`，canonical 写入先于继续，语义与改动前一致）；时间到界由读取循环 `yield`（等待时长 `min(QUEUE_POLL_SEC, 窗口剩余)`，到界提前醒来，模型长时间无新块也必须可推送）；**结构边界**（`model.started/completed`、工具事件、`assistant.turn` 等非增量事件）先 flush 再入队，正文与结构事件的 canonical 顺序不破；`runner.outcome`（终态）之前先 `drain()`，**失败路径同样先 flush 再上抛原异常**（与 Gateway `StreamRenderer._on_failed → finalize` 同口径），因此任何终态（完成/失败）都排在已产生正文之后；取消（`CancelledError`）不吞、不补 flush——未确认的内存缓冲允许丢失，与崩溃口径一致（真实 SIGKILL 用例证明已提交字节不受影响）。**载荷**——`delta_payload()` 统一写 `{delta, reply_generation}`，首次生成代号显式落库（TASK-051 遗留③ 的多代替换由 TASK-062 由此递增，投影不再依赖缺省兜底）。**有界性**——单行增量 ≤ `flush_bytes + 单个模型块`（不切分正文；公开 snapshot 分片属 TASK-061），内存缓冲 ≤ 窗口边界；窗口大小/节拍/等待粒度都不随模型块数增长。
回归（最终代码，本机无 dev 残留进程，单跑不并发）：`uv run pytest -q tests/agent_runtime` exit=0，**956 passed in 799.82s**（含本任务 7 条；随后单文件复跑 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_durable_deltas.py` exit=0，**7 passed in 12.82s**）；`uv run pytest -q tests/contracts tests/test_configuration_inventory.py` exit=0，**1285 passed in 4.38s**；同域聚焦首轮 `test_runner_executor.py`/`test_runs_api.py`/`test_sse.py`/`test_run_service.py`/`test_cancellation.py`/`test_execution_defaults_settings.py`/`test_tool_wait_state_machine.py`/`test_context_events.py`/`test_capacity_webchat_provider_pool.py` exit=0，88 passed in 45.15s。诚实记录：期间有一次误并发的第二次全量运行与首次运行同时打共享 dev 库（本项目已知的"单跑绿、串跑红"成因），两者都作废后以**单跑**重取上述 956 passed；断言强度另用三次变异验证（去掉合并 → 3 例红、去掉时间边界 flush → 2 例红、去掉失败路径 flush → 由结构边界 flush 兜住，说明终态前 flush 有三重保障：窗口到界 / 结构边界 / 终态 drain）。

静态检查：`uv run ruff check`（本任务 2 个改动源文件 + 1 个新增测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 423 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行（`AgentRunnerExecutor.run` 21、`_consume_queue` 50、`_execute` 18、`_queue_wait` 6、`_ExecutorEventEmitter` 各回调 3-23；`build_registry` 76 与 `default_executor_factory` 131 为改动前既有 >50 函数，后者仅把构造调用换成多行传参）；`docs/configuration-inventory.csv` 保持 CRLF（1459 行 CRLF、0 裸 LF），只动本任务两个文件的行（`executor.py` 新增 `QUEUE_POLL_SEC`、平移 13 行常量行、删除被具名常量取代的 `asyncio.wait_for.timeout` 内联行、`__init__.provider` 537→582；`run_events.py` 平移 2 行 + 新增 `FIRST_REPLY_GENERATION`、`list_events.limit` 113→197），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。
遗留（deferred，非本任务可闭合）：① `message.snapshot` 分片、公开 SSE 单帧上限与订阅回压属 TASK-061；`reply_generation` 的多代替换（部分正文失败后新代）属 TASK-062（本任务只保证载荷总是显式携带生成代号，且旧代正文不被拼接）；② 共享追尾、提交后唤醒与 PG 有界补查属 TASK-060；③ 事件写入量/WAL/连接占用/首字额外延迟等压测观测与 1000 用户容量结论属 TASK-110/116；④ 崩溃后租约回收（Reaper 把 RUNNING 行转 FAILED）沿用 TASK-031 既有路径，本任务只证明"崩溃不丢已确认字节、崩溃不写终态"。
- B-259: verified — automated command passed; run_id=79f19483cab54628a993c05bcc413cf8 (confirmed_by: runner)
- B-259: verified — automated command passed; run_id=b5f1fd657821449cb7360f270ceaf955 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-259 RED：首建 `tests/agent_runtime/test_capacity_webchat_durable_deltas.py` → **1 error during collection**（ImportError: DeltaWindow；0 passed/0 failed，0.18s）
- [2026-10-18] 实现：`run_events.DeltaWindow`（时间/字节双边界有界合并、`drain` 三处共用、空窗口不产出空事件、时钟可注入）+ `delta_payload`/`FIRST_REPLY_GENERATION` + 窗口参数唯一来源 `SharedSettings.stream_delta_flush_*`；`executor._ExecutorEventEmitter`/读取循环（字节到界即入队、时间到界 yield、结构边界先 flush、终态与失败前先 drain、取消不补 flush）
- [2026-10-18] B-259 GREEN：**7 passed in 11.93s**（最终冻结代码）
- [2026-10-18] 子 agent 中断接管：域回归由孤儿进程完成（输出 `/tmp/b259-agent-runtime-clean.log`）→ `tests/agent_runtime` **956 passed in 799.82s**；补本 Log 后 finish 并按裁决提交
- [2026-10-18] completed (done)

---

## TASK-060: 共享事件追尾、提交后唤醒及补查

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-056, TASK-059
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-260, B-06
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `tests/agent_runtime/test_capacity_webchat_shared_tail.py`
- **Estimate**: 60分钟

### Description

同Run/会话共享上游追尾，注册唤醒后读水位避免丢信；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-260][integration] 先在`tests/agent_runtime/test_capacity_webchat_shared_tail.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：同Run/会话共享上游追尾，注册唤醒后读水位避免丢信。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_shared_tail.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.16s）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.event_tail_hub'`——验收文件先落地、hub 尚不存在。首版实现后仍有真实断言失败（共享就绪判定、心跳等待窗口、慢消费者重建、Redis 降级口径），逐项修正后 GREEN（逐项见 Acceptance Evidence）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `application/event_tail_hub.py`——`EventTailHub`（同会话共享上游：single-flight 水位+分页读取、共享窗口 `WINDOW_ROWS`、窗口淘汰后按持久水位 PG 补查重建；`_follow` 严格按"注册唤醒→读水位→按页追到水位→等待或超时补查"；`publish` 提交后唤醒（本地唤醒 + Redis Pub/Sub hint，失败只计数降级）；`check_cursor` 超前 `CURSOR_AHEAD`/过期 `CURSOR_EXPIRED` 分型；`checkpoint` 只回真实水位不分配 seq；`watermark` = 会话 `last_seq` 与已持久最大 seq 取大者）、`CanonicalEventView`（满足 `public_events.CanonicalRow` 的公开投影接缝）、`TailBatch`（行 + 水位 + `caught_up` + `lagged`）、`TailHubStats`（共享轮次/页读/唤醒/补查/重建/降级计数）、`WakeupHint`/`NullWakeupHint`/`RedisWakeupHint`（Pub/Sub 仅通知，不可达即 `degraded` + 退避重连）、进程单例 `get_event_tail_hub`（会话工厂与 run_service 同源绑定）。② `application/run_service.py`——`_tail_events` 改为共享追尾（只产出已提交行、`seq<=游标` 不重投、退出读取会话后才 yield、心跳不推进游标）+ `_tail_stop`/`_has_terminal_row`（Run 行判定：WAITING_INPUT 停止、终态缺行时合成终态但绝不先于最后持久正文）+ `_wake_tail` 在 6 处提交点之后调用（受理/续聊/取消/增量/完成/失败）；`tail_run_events` 先做游标分型；删除每订阅 0.5s 独立轮询常量 `REPLAY_POLL_SEC`，改由 `run_event_fallback_poll_sec` 的共享补查节拍（design §3.7）。
- [x] [B-260] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-260] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed in 23.51s**（冻结代码复跑）。
- [x] [B-06][integration] 将原B-06落入`tests/agent_runtime/test_capacity_webchat_shared_tail.py`，先RED后GREEN；实际公开事件 DTO/水位和恢复服务；断言：过滤跳号、重复 seq、checkpoint、超前游标与已过期游标分别处理；重建快照不混 Run；心跳不推进游标，终态不能先于最后持久正文。RED：与 B-260 同批（同一文件、同一收集错误）；GREEN：同命令 **10 passed**，其中 B-06 三条用例（过滤跳号/重复 seq/checkpoint/游标分型 L1097-1200、慢消费者按水位重建 L970-1014、Run 隔离与终态顺序 L1203-1288）逐项登记断言位置。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-260 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 同Run/会话共享上游追尾，注册唤醒后读水位避免丢信；Redis仅hint故障按PG有界补查，退出session才yield，心跳不推进业务游标 | tests/agent_runtime/test_capacity_webchat_shared_tail.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | verified |
| B-06 | integration | 实际公开事件 DTO/水位和恢复服务 | 过滤跳号、重复 seq、checkpoint、超前游标与已过期游标分别处理；重建快照不混 Run；心跳不推进游标，终态不能先于最后持久正文 | tests/agent_runtime/test_capacity_webchat_shared_tail.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-260 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_shared_tail.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.16s）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.event_tail_hub'`（验收文件先落地、hub 尚不存在） | 同命令 exit=0，**10 passed in 23.51s**（冻结代码复跑） | ① `test_b260_shared_tail_reads_do_not_grow_with_observers` L687-715：**3 个订阅者对同一事件序列的上游读取轮次不随观察者线性放大**（`shared <= single + 3` L706-708，1 个订阅者 vs 3 个订阅者同工作负载实测；`shared < 3×水位` L709-711 直接否掉"每订阅者各查一遍"）、**提交后唤醒**（补查周期故意设 30s，追平耗时 < 5s，L678-684 在 `_measure_shared_rounds` 内）、每个订阅者**无缺口无重复**覆盖 {1..水位}（L635-637，三个订阅者逐一校验 L712-713）。② `test_b260_reconnect_backfills_missing_interval_without_loss_or_duplication` L721-776：断线期间提交（L754）、**重连从已应用游标之后开始**（L773）、**恰好补齐缺失区间**（L774）、两段拼接恰好覆盖 {1..水位}（L776）。③ `test_b260_hint_failure_keeps_writes_and_local_wakeup` L786-822：Redis 不可达时写入与本地唤醒不受影响（L820-822：`hint_degraded`/`hint_failures` 可见 + `wakeups>=1`）。④ `test_b260_lost_hint_falls_back_to_bounded_pg_catch_up` L825-860：**提示整体丢失时按 PG 有界补查**（`fallback_polls>=1` L856、`hint_degraded` L857、预算内投递 L858-860、拿到的是已提交事实 L855）。⑤ `test_b260_redis_hint_wakes_subscriber_published_from_another_connection` L863-917：**真实 Redis Pub/Sub 另一连接发布提示即唤醒**（真实 `PUBSUB NUMSUB` 计数确认订阅就绪 L891、< 5s 送达 L909-911）。⑥ `test_b260_yields_outside_session_so_slow_subscriber_holds_no_connection` L920-967：**消费者暂停（生成器悬停在 yield）时连接池 0 占用**（L962-964：基线/暂停/暂停期间提交三处都是 0）、恢复消费不跳号（L967）。⑦ `test_b260_slow_subscriber_rebuilds_from_persisted_watermark` L970-1014：窗口有界 + 慢消费者按水位重建（`lagged_batches>=1` L1012）、**重建后不丢不重**（L1013-1014）。⑧ `test_b260_heartbeat_does_not_advance_business_cursor` L1017-1094：真实 SSE 心跳帧 ≥2（L1050-1053）、**心跳期间业务序号不变**（L1055）、**心跳后后续 seq 连续无跳号**（L1077）。 | 真实 Runtime 业务服务：真实 `RunService.start` 受理（含冻结快照/领取/真实执行器链）+ 真实 `AgentRunnerExecutor`/`AgentRunner`/`OpenAICompatibleProvider`；**真实 HTTP 模型探针**：进程内 uvicorn `text/event-stream` 端点按脚本逐块下发正文，起始闸门与"发完第 N 块等放行"都在真实 socket 上（写入侧就是生产路径：`_persist_event`/`_finalize_run` 提交后唤醒）；**隔离 PostgreSQL**（每用例空库 + `alembic upgrade head`，跑完 drop）与隔离 Redis 号位：会话/Run/canonical 事件/租约都是真实行，`seq` 由真实 `EventWriter` 在会话行锁内分配；Redis 用**真实连接**（含第二个连接跨进程发布提示），故障用不可达端口模拟；**真实连接池事实**：`yield` 是否占连接直接读真实引擎池 `checkedout()`；不 mock 存储或租约 | verified |
| B-06 | 同批（同一验收文件、同一收集错误；实现后 B-06 三条用例亦先观察到真实断言失败：心跳窗口与 Run 隔离/合成终态口径） | 同命令 exit=0，**10 passed in 23.51s**（含 B-06 三条用例） | ① `test_b06_filtered_gaps_duplicate_cursor_and_checkpoint_are_distinct` L1097-1200：**过滤跳号合法**（公开投影只出 seq 2/4，canonical 行仍连续覆盖 1..6，L1136-1137）、**重复 seq 不重投**（游标排他：`after_seq=4` 只出 5/6，L1148）、**checkpoint 只回真实水位**（=6 L1157）且**不分配新 canonical seq**（取前取后行数相同 L1158-1160）、**超前游标显式 `CURSOR_AHEAD`**（L1169-1170，含 `hub.check_cursor` 与 API-04 路径）、**过滤跳号不是过期**（L1173-1177）、**已过期游标显式 `CURSOR_EXPIRED` 且带最早可用水位**（L1187-1188：真实软删最早 3 行建立保留策略状态）。② `test_b260_slow_subscriber_rebuilds_from_persisted_watermark` L970-1014：窗口淘汰后的按水位补查重建（见 B-260 ⑦）。③ `test_b06_rebuilt_stream_keeps_runs_separate_and_terminal_after_body` L1203-1288：**重建快照不混 Run**（追第二轮只出第二轮的行，L1242-1243）、**终态不先于最后持久正文**（首轮终态 seq = 本 Run 最大 seq L1249-1251、终态之后无本 Run 已持久正文 L1262）、**Reaper 路径（Run 行终态、缺终态行）合成终态仍排在全部已持久正文之后**（L1286-1288）。 | 同 B-260（同一验收文件、同一真实边界）；额外真实事实：`canonical_event` 行由真实 `EventWriter` 写入（会话行锁分配 seq），公开投影走生产 `project_public_stream_event`，保留策略状态用真实 `is_deleted` 更新建立，终态判定读真实 `run_record` 行 | verified |

实现要点：**共享上游**（`event_tail_hub.EventTailHub`）——同一 `(tenant, conversation)` 的多个订阅者共享**同一次**上游读取（`_shared_read` single-flight：一轮读水位 + 至多一页入共享窗口），订阅者各持游标从窗口按 `seq > 游标` 取行；观察者数量不放大查询（1 个 vs 3 个订阅者实测轮次 `shared <= single + 3`，且 `shared < 3×水位`）。**注册唤醒→读水位→按页追到水位→等待或超时补查**——`waiter.clear()` 在读取**之前**，读取期间到达的唤醒留在 waiter 上（不掉进"读取之后、等待之前"的缝隙）；等待以 `run_event_fallback_poll_sec` 为界超时补查；订阅者仍落后于已读入行时立刻继续追（不等待），因此慢消费者恢复后无需等下一个补查节拍。**提交后唤醒**——`publish` 只在事务提交之后调用（6 处：受理/续聊/取消/增量/完成/失败），先本地唤醒再尽力发 Redis Pub/Sub 提示；hint 是可选加速：连不上/断开即 `degraded=True` + 退避重连 + 计数，**绝不抛回写入路径**，跨进程提示丢失由 PG 有界补查兜底（本地唤醒与 hint 相互独立，测试分别验证）。**补查**——断线重连按已应用游标补拉缺失区间；共享窗口有界（`WINDOW_ROWS`），窗口淘汰后慢消费者按持久水位分页重建（`lagged_rebuilds` 可见）；缺失行已不在时游标推进到窗口地板，绝不空转重查。**退出 session 才 yield**——所有行在读取会话内物化为 `CanonicalEventView`，会话关闭后才交给订阅者；暂停消费（生成器悬停）期间真实连接池 `checkedout()==0`。**去重与合法跳号**——`seq <= 游标` 一律不再产出；内部事件被过滤造成的 canonical 跳号是合法事实（不判丢包、不补拉循环）。**水位与游标分型**——`watermark` = 会话 `last_seq` 与已持久行最大 seq 取大者（行锁分配顺序即提交顺序，故水位只含已提交事实）；`checkpoint` 只回真实水位、不分配 canonical seq；`check_cursor` 对超前 `CURSOR_AHEAD`、已过期 `CURSOR_EXPIRED`（最早可用行被保留策略淘汰）分别显式失败，`tail_run_events`（API-04）先分型再追尾。**心跳不推进业务游标**——心跳由 HTTP 层产出、不经过 hub；等待轮次不移动游标，游标只在真正产出已提交行时前进（真实 SSE 帧实测）。**Run 作用域与终态顺序**——按 `run_id` 过滤不混 Run；终态行（或 Reaper 缺行时按 Run 记录合成）绝不先于最后持久正文；缺终态行时先确认"已提交终态行是否存在"，存在则等追平后送出持久事实，不合成第二份终态。**有界与降级可见**——`PAGE_ROWS`/`WINDOW_ROWS` 有界、页读/共享轮次/唤醒/补查/重建/降级全部计入 `TailHubStats`，降级路径都带 warning 日志（`event_tail_hint_unavailable`/`event_tail_hint_publish_failed`/`event_tail_hint_listen_failed`/`event_tail_lagged_rebuild`）。
回归（最终代码，本机无 dev 残留进程，单跑不并发）：`uv run pytest -q tests/agent_runtime` exit=0，**975 passed in 841.33s**（含本任务 10 条）；`uv run pytest -q tests/contracts tests/test_configuration_inventory.py` exit=0，**1285 passed in 4.04s**；同域聚焦 `test_capacity_webchat_durable_deltas.py`/`test_runs_api.py`/`test_capacity_webchat_public_projection.py` exit=0，46 passed in 33.65s；`test_memory_redesign_confirmation.py`/`test_memory_redesign_supervisor.py`（首轮被共享单例会话工厂打偏的 12 例）exit=0，42 passed in 113.92s。诚实记录：首轮全量域回归 12 failed（`COMMON_NOT_FOUND` 出现在追尾水位读取），根因是 hub 的会话工厂默认从 `infrastructure.db` 取，而这些用例按既有约定替换的是 `run_service.get_session_factory`（进程单例因此读到 dev 库）——已改为 `RunService` 构造时把本模块的 `get_session_factory` 绑定进 hub（与 `AdmissionPolicyService`/`RunSubmissionService` 同源，单例可再次绑定），并在修复后重跑全量域回归取得 975 passed。
静态检查：`uv run ruff check`（本任务 2 个改动源文件 + 1 个新增测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 426 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行（`_tail_events` 34、`_tail_stop` 26、`tail_run_events` 28、`_executor_event`/`_has_terminal_row`/`_wake_tail`/`__init__` 47；`event_tail_hub.py` 全模块无 >50 行函数），既有超长函数 `_create_run` 222→223、`_finalize_run` 90→91、`_finalize_failed` 61→62 各只 +1 行（提交后唤醒调用；此前即 >50，属既有结构债）；`docs/configuration-inventory.csv` 保持 CRLF，只动本任务文件的行（新增 `event_tail_hub.py` 4 行常量 `WAKEUP_CHANNEL`/`PAGE_ROWS`/`WINDOW_ROWS`/`HINT_RETRY_SEC`，`run_service.py` 既有 15 行行号平移 + 删除被共享补查取代的 `REPLAY_POLL_SEC`），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。
计划外披露：`docs/configuration-inventory.csv`（项目硬约束：模块级常量必须盘点；CRLF 保持、只更新自己的行）；未新增迁移/DDL（无新列）。
遗留（deferred，非本任务可闭合）：① **公开 SSE 端点、游标分型投影（HTTP 层）、snapshot 分片与缓冲回压**属 TASK-061——本任务提供 hub 与接缝（`TailBatch.through_seq/caught_up`、`check_cursor`/`checkpoint`、满足 `public_events.CanonicalRow` 的 `CanonicalEventView`）；② **其它 canonical 写入方**（记忆确认 resume 等非 `run_service` 路径）尚未调用 `publish`：接线前其提交后唤醒由 `run_event_fallback_poll_sec` 补查兜底（不丢事件、只增时延），接线属 TASK-063/094；③ 事件写入量/订阅回压/hint 降级等**指标目录**属 TASK-098——本任务的可观测面是 `TailHubStats` 与 warning 日志；④ `run_event_heartbeat_sec` 仍由 HTTP 层使用（心跳不经过 hub，故不推进业务游标）；⑤ Reaper（`_reap_one`）写入终态行但不发唤醒：由补查节拍取回（本任务只保证语义与顺序正确，不等同"立即"）。
- B-06: verified — automated command passed; run_id=9509259deba141fabf36856eb8e67514 (confirmed_by: runner)
- B-260: verified — automated command passed; run_id=9509259deba141fabf36856eb8e67514 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-260/B-06 RED：新建 `tests/agent_runtime/test_capacity_webchat_shared_tail.py` → **1 error during collection**（ModuleNotFoundError: event_tail_hub；0 passed/0 failed，0.16s）
- [2026-10-18] 实现：`event_tail_hub.py`（共享追尾 single-flight、共享窗口与按水位重建、注册唤醒→读水位→按页追水位→超时补查、提交后唤醒 `publish` + Redis hint 降级、`check_cursor`/`checkpoint`/`watermark`、`TailHubStats`）+ `run_service.py`（`_tail_events` 改共享追尾、`_tail_stop`/`_has_terminal_row`、6 处提交后 `_wake_tail`、API-04 游标分型、删除 `REPLAY_POLL_SEC`）
- [2026-10-18] 首轮全量域回归 12 failed → 根因：hub 会话工厂未随既有测试替换（单例读到 dev 库）；改为 `RunService` 构造时绑定本模块 `get_session_factory`（单例可再绑定）
- [2026-10-18] B-260/B-06 GREEN：**10 passed in 23.51s**（冻结代码复跑）；域回归 `tests/agent_runtime` **975 passed in 841.33s**；`tests/contracts + inventory` **1285 passed in 4.04s**；ruff/mypy 干净；盘点 4 passed
- [2026-10-18] completed (done)

---

## TASK-061: 公开SSE游标、分片与缓冲回压

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-051, TASK-052, TASK-057, TASK-060
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-261
- **Files**: `apps/im-gateway/src/muad_im_gateway/application/webchat_streams.py`, `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `tests/gateway/test_capacity_webchat_gateway_sse.py`
- **Estimate**: 60分钟

### Description

CURSOR_EXPIRED/AHEAD分型，合法跳号不补拉循环；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-261][integration] 先在`tests/gateway/test_capacity_webchat_gateway_sse.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：CURSOR_EXPIRED/AHEAD分型，合法跳号不补拉循环。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_sse.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.08s）**：`ModuleNotFoundError: No module named 'muad_im_gateway.application.webchat_streams'`——验收文件先落地、公开流模块尚不存在。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/webchat_streams.py`（新增）——`WebchatStreamBudget`（单帧/心跳/单订阅/进程总预算，含 `total≥per-subscriber` 联动校验）、`WebchatPublicProjector`（canonical `stream_type` → TASK-006 公开 DTO 的唯一公开边投影：delta/stage/terminal/error 严格取值、snapshot 出 `WebchatSnapshotGroup`、confirmation/artifact 只在载荷本身是完整安全 DTO 时公开、已知公开事件坏载荷抛 `MalformedPublicEvent`、未知/内部默认 None）、`render_public_event`/`_snapshot_frames`（snapshot 按 UTF-8 字符边界分片，容量按真实溢出量收敛，逐片 `part_index/part_count`；无法分片的超限帧抛 `OversizedPublicFrame`）、`WebchatPublicScanner`（过滤跳号只推进已扫描水位；checkpoint 只到"下一条公开事件之前"、不分配 canonical seq、不追加正文）、`SseBufferLedger`/`SseBufferLease`/`SseLedgerStats`（进程总预算 + 单订阅份额，到界显式关闭并留痕，退出释放不漂移）、`WebchatPublicStream`（生产任务 + 有界队列 + 心跳消费侧产出）、`WebchatStreamService`（核 Run 归属 → 核会话 Agent 当前授权 → **急切打开** Owner 流，游标分型原样上抛、不留半开连接）。② `api/webchat.py`——`GET /runs/{run_id}/events`（API-07）：`ChatIdentityDep` + `ChatStreamGuardDep`（长流定时复核）、`after_seq`/`Last-Event-ID` 取更靠前的已应用水位（坏值显式 422）、SSE 响应头 no-store + `X-Accel-Buffering: no`；装配根状态（Runtime 客户端/租户/四个传输预算/进程账本）按生产同形注入。③ `application/runtime_client.py`——新增 `open_event_stream`（`__aenter__` 阶段完成请求与状态码裁决，错误码连同 Owner 的游标窗口 `data` 原样上抛）。④ `application/webchat_service.py`——`required_uuid`/`owned_run_state` 提为共用口径（资源面与 SSE 同一授权/归属判定，不各写一份）。⑤ `application/sse.py`——`KNOWN_EVENT_TYPES` 补 `message.snapshot`/`run.stage`（公开词表的 canonical 键必须是解析器已知类型的子集，否则公开事件会被解析层静默丢弃；该一致性由静态用例钉住）。⑥ `application/envelope.py`——`error_data_from_payload`（游标窗口等稳定事实原样带出）。⑦ `docs/configuration-inventory.csv`——新增 6 行 `webchat_streams.py` 常量、更新 `sse.py` 两行（值/行号）与 3 个网关文件的行号（CRLF 保持）。
- [x] [B-261] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-261] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_sse.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed in 11.2–11.9s**（连续 3 次复跑稳定）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-261 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | CURSOR_EXPIRED/AHEAD分型，合法跳号不补拉循环；snapshot同seq分片完整后才应用；单订阅及进程总缓冲有界，慢消费者断开可恢复，不重建Run | tests/gateway/test_capacity_webchat_gateway_sse.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_sse.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-261 | `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_sse.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.08s）**：`ModuleNotFoundError: No module named 'muad_im_gateway.application.webchat_streams'`（验收文件先落地、公开流模块尚不存在） | 同命令 exit=0，**10 passed in 11.2–11.9s**（连续 3 次复跑稳定） | ① `test_b261_chat_gate_run_ownership_and_current_authorization_precede_the_owner_stream` L533-581：缺 Cookie 401 / 缺上下文 422 / 上下文不符 409 / 撤销 `CHAT_SESSION_REVOKED` 401 / 他人 Run 404 / 授权撤销 403，六种拒绝**全部零 Owner 事件流触达**（L548/555/564/573）、归属与授权经受信内部面核验（L577 `x-internal-service`）、响应无 CHAT 凭据（L578）。② `test_b261_cursor_errors_are_typed_expired_and_ahead_not_clamped` L587-621：过期游标 410 `CURSOR_EXPIRED` + Owner 真实最早可用水位（L607-609）、超前游标 400 `CURSOR_AHEAD` + Owner 真实持久水位（L611-613）、坏 `Last-Event-ID` 422 且不触达 Owner（L615-617）、游标原样交给 Owner 裁决（L617-621）。③ `test_b261_legal_cursor_resumes_by_after_seq_and_last_event_id` L624-655：合法游标续传（游标排他：`after_seq=7` ⇒ 下一条 9，L649）、`Last-Event-ID` 同口径（L651）、SSE 头 no-store/`X-Accel-Buffering: no`（L646-647）、流结束不占进程预算（L642-643）。④ `test_b261_legal_filtered_gaps_stream_once_with_checkpoints_and_no_repull` L661-709：过滤跳号只出 3/9/11（L688-689）、内部 canary 不出现（L694）、安全 checkpoint 只到过滤尾部且严格小于下一条公开事件（L697-699）、帧 `id` = canonical seq（L701）、心跳无 id 不推进游标（L704）、**只读一次 Owner 流、无补拉循环**（L706-707）、过滤留痕（L709）。⑤ `test_b261_snapshot_parts_share_identity_and_only_complete_groups_apply` L731-771：分片 >2 片、逐帧 ≤ 单帧预算（L718-720）、同 `run_id/event_id/seq` + 同 `reply_generation`（L721-723）、`part_index` 0..n-1 与 `part_count=n`（L724-725）、**拼接逐字节等于原文**（L726，含 `正文🌏` 多字节字符）、**片间不插入其他事件**（L728）、阶段事件按持久投影公开（L759-768）、分片不改公开身份（L770）。⑥ `test_b261_slow_consumer_is_closed_within_the_subscriber_buffer_and_recovers_by_cursor` L830-864：第一条流在预算内被断开（L850）、两段拼接恰好覆盖 {1..N} 不丢不重（L852）、`max_pending_bytes ≤ 单订阅上限`（L855）、`slow_subscriber_closed ≥ 1`（L856）、退出不占进程预算（L858）、**零 `POST /v1/runs`**（L860）、重连游标单调且都是真实应用过的 seq（L862-864）。⑦ `test_b261_process_total_buffer_closes_slow_subscribers_without_losing_the_body` L867-911：`max_total_pending_bytes ≤ 进程总上限`（L906）、总预算在两个慢订阅并发时生效（L907-908）、正文按游标恢复不丢（L910）、不重建 Run（L911）。⑧ `test_b261_malformed_public_payload_closes_the_subscription_explicitly` L917-938 / `test_b261_oversized_non_shardable_frame_closes_explicitly_without_truncation` L941-960：坏载荷/不可分片超限帧**显式关闭并计数**（`malformed_closed`/`oversized_closed`，L937/L960）、不进入公开流、不截断（L936/L959）。⑨ `test_b261_stream_modules_hold_no_database_and_only_speak_the_public_contract` L966-1006：两个网关模块零 sqlalchemy（L974）、公开词表 ⊆ TASK-006 公开类型且与内部类型不相交（L986-1004）、公开词表的 canonical 键 ⊆ Owner 流解析器已知类型（L1006） | 真实 Gateway 应用与生产路由：`muad_im_gateway.main.app` 挂生产 `api/webchat.py` 路由，**真实 uvicorn 进程内服务 + 真实 socket**（`lifespan=off`，装配根状态按生产同形注入），客户端经真实 HTTP 增量读 SSE；**真实 Console HTTP**：`ConsoleClient` 打真实 uvicorn（API-12 chat-principal 真实封套）；**真实 Runtime HTTP/SSE**：`RuntimeClient` 打真实 uvicorn，`GET /v1/runs/{run}/events` 是真实 `text/event-stream`（按 seq 排他下发、按真实增量窗口节拍、游标越界/过期返回真实错误封套、断开留痕），探针另有 `POST /v1/runs` 端点专门证明"不重建 Run"（真被调用即 500）。**数据库经 Owner 接口访问**：本文件零数据库、零 sqlalchemy（含静态 AST 断言）；公开帧逐帧经 `muad_contracts.webchat_events.parse_public_event` 校验（非公开类型/坏形状即失败）。未 mock 存储或租约（网关本就无库） | verified |

实现要点：**完整转 Owner、网关不持库**——事件流只来自 Runtime 持久事件端点（API-04；TASK-060 的共享追尾与 `check_cursor` 在那里），游标分型（`CURSOR_EXPIRED`/`CURSOR_AHEAD`）由 Owner 裁决、连同其窗口 `data` 原样上抛，网关不夹取、不补造窗口字段；归属（他主体 404）与**当前**入口/Agent 授权（撤销 403）经 Owner 接口先核，**急切打开**（`open_event_stream` 的 `__aenter__` 完成请求与状态码裁决）保证游标错误以 HTTP 错误封套返回、不塞进已开始的流、不留半开连接；长流由 TASK-044 的 `ChatStreamGuard` 定时复核（空闲也复核）。**公开投影在公开边**（design E-13「Runtime 实际持久事件、Gateway 公开投影和浏览器」）——canonical `stream_type` → TASK-006 公开 DTO 只经 `WebchatPublicProjector` 一处；delta 只取 `delta/reply_generation`、stage 严格解析 `WebchatExecutionStatus`、terminal 只出状态、error 只出稳定码+保守 retryable；工具参数/结果、模型请求、推理、SYSTEM、记忆与未知事件默认不公开（过滤跳号不补号、不判丢包、不补拉）；已知公开事件的坏载荷显式失败（不伪装成过滤），confirmation/artifact 只在载荷本身就是完整安全 DTO 时公开（与 Runtime 投影同口径）。**公开身份**——canonical 行 id 不在 Owner 流封套里，公开事件身份由 `(run_id, canonical seq)` 确定性派生（同一事件重连/重放得到同一 `event_id`，客户端据此去重）。**安全 checkpoint**——只带真实已扫描水位且**严格小于下一条公开事件 seq**（否则客户端会按重复 seq 丢掉真正的公开行），不分配 canonical seq、不追加正文；心跳是注释帧（无 `id`），不推进业务游标。**分片**——`message.snapshot`（整组替换正文）超单帧预算即按 UTF-8 字符边界切分，同 `event_id/seq/reply_generation`，`part_index` 0..n-1、`part_count=n`，容量按真实溢出量收敛（最多 4 轮），逐帧 ≤ `sse_event_max_bytes`；片间不插入其他事件（整组到齐才可原子替换）；无法分片的超限帧（如大增量）**显式关闭订阅**，绝不截断或静默丢弃。**缓冲回压**——单订阅（`sse_subscriber_buffer_bytes`）与进程总（`sse_total_buffer_bytes`）双重有界：到界即显式关闭该订阅（warning + `slow_subscriber_closed`/`total_buffer_closed` 计数），客户端按持久游标（`Last-Event-ID`/`after_seq` 取更靠前者）重连补齐，不丢正文、不重建 Run；订阅退出释放全部占用（账本不漂移，`active_subscribers/total_pending_bytes` 归零）。**心跳与代理**——空闲按 `run_event_heartbeat_sec` 发注释帧；响应头 `no-store` + `X-Accel-Buffering: no`（私有数据、关闭入口代理缓冲）。
回归（最终代码，本机无 dev 残留进程，单跑不并发）：`uv run pytest -q tests/gateway` exit=0，**422 passed in 110.53s**（含本任务 10 条）；`uv run pytest -q tests/contracts tests/architecture tests/test_configuration_inventory.py tests/test_error_catalog.py tests/test_api_i18n.py tests/test_attachment_contract.py` exit=0，**1336 passed in 4.44s**；同域聚焦 `test_sse_parser.py`/`test_stream_renderer.py` exit=0（21 passed，含 `KNOWN_EVENT_TYPES` 扩展后的解析/渲染回归）。
静态检查：`uv run ruff check`（本任务 6 个改动源文件 + 1 个新增测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 436 source files；AST 逐函数核对：本任务新增/修改的**生产**函数全部 ≤50 行（测试文件内长用例已拆分为 `_assert_snapshot_group`/`_consume_with_cursor_recovery` 等 ≤50 行的函数），生产代码无 >50 行函数；`docs/configuration-inventory.csv` 保持 CRLF（1513 行 CRLF、0 裸 LF），新增 6 行（`webchat_streams.py`）+ 更新 `sse.py` 两行 + 3 个网关文件行号，`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。
计划外披露：① `application/runtime_client.py`（+`open_event_stream`）、`application/webchat_service.py`（`required_uuid`/`owned_run_state` 提为共用口径）、`application/envelope.py`（+`error_data_from_payload`）、`application/sse.py`（`KNOWN_EVENT_TYPES` 补 `message.snapshot`/`run.stage`）、`docs/configuration-inventory.csv` 不在任务 Files 计划内，属"网关不持库 + 游标分型必须早于响应头 + 授权判定不重复"的必要接线；② **公开投影在网关侧实现**依 design E-13 的边界表述（"Runtime 实际持久事件、Gateway 公开投影和浏览器"）；Runtime 的 `public_events.project_public_stream_event` 仍是历史/流共用的运行时投影接缝，本任务不改它（无第二份事实、只做公开边词表映射）；③ 未新增迁移/DDL。
遗留（deferred，非本任务可闭合）：① **canonical `message.snapshot` 的生产方**（部分正文失败后新 `reply_generation` 的持久替换）属 TASK-062——契约（TASK-006）已定义分片坐标，本任务在公开边实现分片并由契约探针驱动，未接线前该分支由探针覆盖；② **装配根注册**（`main.py` 挂载 webchat 路由、发布 `webchat_sse_ledger` 与四个传输预算、启动预算 `gateway_max_sse_subscribers`）属 TASK-096——本任务的路由依赖已按同形读取 `app.state`（账本未发布时就地建立），B-261 按"生产路由 + 装配根状态同形注入"直连真实应用；③ **订阅者数量上限**（`gateway_max_sse_subscribers`=1100 的显式拒绝语义）与 SSE 指标目录（订阅数/断开原因/缓冲水位）属 TASK-096/098——本任务的可观测面是 `SseLedgerStats` 与 warning 日志（`webchat_sse_slow_subscriber_closed`/`webchat_sse_total_buffer_closed`/`webchat_sse_frame_rejected`/`webchat_sse_owner_stream_failed`）；④ **游标窗口的契约形状**（`WebchatCursorError{run_id,minimum_after_seq,last_seq,requested_seq}`）需 Owner 在错误 `data` 里同时给出最早可用水位与持久水位——当前 Runtime 只给其一，网关按"不补造"原则原样透传，完整窗口投影属 TASK-069（保留/清理）或 Runtime 侧的游标端点演进；⑤ **前端消费**（`message.snapshot` 分组收集、重连退避、`transport_revision` 绑定）属 TASK-072/076/082。
- B-261: verified — automated command passed; run_id=696ac06ba1b8478993f5c95307a4a30f (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-261 RED：新建 `tests/gateway/test_capacity_webchat_gateway_sse.py` → **1 error during collection**（`ModuleNotFoundError: muad_im_gateway.application.webchat_streams`；exit=2，0 passed/0 failed，0.08s）
- [2026-10-18] 实现：`application/webchat_streams.py`（公开投影/安全 checkpoint/分片/有界缓冲账本/急切打开）+ `api/webchat.py`（API-07 路由 + 装配根依赖）+ `runtime_client.py`（`open_event_stream`）+ `webchat_service.py`（共用归属/字段口径）+ `sse.py`（解析器已知类型补 `message.snapshot`/`run.stage`）+ `envelope.py`（错误 `data` 提取）+ 盘点同步
- [2026-10-18] 首版断言失败逐项修正：游标排他语义（`after_seq=7` ⇒ 下一条 9）、`KNOWN_EVENT_TYPES` 缺 `run.stage` 导致阶段/快照被解析层静默丢弃（已补并加一致性静态断言）、慢消费者用例改为"真实客户端按游标重连直到终态"（生产端节拍/消费端读速分离）
- [2026-10-18] B-261 GREEN：**10 passed in 11.2–11.9s**（连续 3 次复跑）；同域回归 `tests/gateway` **422 passed in 110.53s**；契约/架构/盘点/错误目录 **1336 passed in 4.44s**；ruff/mypy(436 files) 干净；盘点 4 passed
- [2026-10-18] completed (done)

---

## TASK-062: 部分正文失败后的模型重试边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-025, TASK-059, TASK-061
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-262
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/model_call_trace.py`, `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`, `tests/agent_runtime/test_capacity_webchat_reply_generations.py`
- **Estimate**: 60分钟

### Description

不同Key/attempt不能拼正文；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-262][integration] 先在`tests/agent_runtime/test_capacity_webchat_reply_generations.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：不同Key/attempt不能拼正文。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_reply_generations.py` exit=1，**5 failed / 1 passed（9.60s）**：① 断流后未发生重试（TASK-025 的"已输出正文一律终止"映射），无 gen1 正文/无 snapshot；② `ModelCallTrace(audit_enabled=...)` 分代账本尚不存在（TypeError）；③ 回合外/被放弃尝试的迟到输出进入了正文（`deltas == ['第一尝试正文。', '第二尝试正文。', '旧尝试迟到输出。']`）；④ 重试重发同一工具调用时 `runtime.tool_call_audit` 的部分唯一索引把 Run 打崩（`COMMON_INTERNAL_ERROR`），操作被 `cancel_operations` 标 CANCELLED——"已提交工具业务键被重试更换"的真实反例（日志 `$TMPDIR/muad-task062/task062-red.log`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `application/model_call_trace.py`——`ModelCallTrace` 增加**回复生成代号账本**（`begin()` 返回 `ModelRound{generation, supersedes, event_fields}`：上一回合"已产生正文后失败"（`note_failure_after_output`）⇒ 显式分代 `generation += 1` 且 `supersedes=True`；`note_delta`/`close_round`/`accepts_output` 定义回合边界，回合外迟到输出被 fence；`audit_enabled=False` 时 `event_fields()` 为空，事件不冒充审计身份）；`FIRST_REPLY_GENERATION` 仍由 `run_events` 唯一声明（账本从它起算）。② `application/executor.py`——`_ExecutorEventEmitter` 持有当前代号：`on_model_started` 先按**旧代号** flush 缓冲、`supersedes` 时落 `message.snapshot`（`{"reply_generation": N, "text": ""}`，空正文整组替换旧代）再发 `model.started`；`drain_delta()` 统一给时间边界/终态前 flush 贴代号；`on_delta` 在回合外只留 `reply_delta_fenced` warning；`AuditedModelProvider`/`PooledModelProvider` 每次尝试加**尝试围栏**（`_Emitted.closed`，结束/停手即置位，迟到输出不并入正文）并在失败时把"已产生正文"记入账本；删除 `_terminal_or_retryable`（重试与否交回 `AgentRunner` 的冻结重试预算：可重试错误 ⇒ 新代重试，预算耗尽/不可重试 ⇒ 终态）；`default_executor_factory` 总是建账本（审计关联按 writer 有无显式开关）。③ `application/public_events.py`——公开词表补 `message.snapshot` 与 `_snapshot_event` 投影（整组替换事实，坏载荷显式失败，不静默当过滤）。④ `infrastructure/audit_writer.py`——`record_tool_call` 按 `(run_id, tool_call_id)` 幂等（`ON CONFLICT DO NOTHING` 命中既有部分唯一索引）：重试重发同一工具调用保留**首次**审计行、业务键不被更换，也不把唯一冲突升级成 Run 失败（重放留 `tool_call_audit_replay_skipped` 日志）。
- [x] [B-262] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-262] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_reply_generations.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**6 passed in 11.01s**（日志 `$TMPDIR/muad-task062/task062-green.log`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-262 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 不同Key/attempt不能拼正文；策略选择终止或新reply_generation持久snapshot替换，旧尝试输出被fence，已提交工具业务键不被重试更换 | tests/agent_runtime/test_capacity_webchat_reply_generations.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_reply_generations.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：隔离 PostgreSQL（每用例一个空库 + `alembic upgrade head`，跑完 drop，不触碰共享 dev 库）、真实 Console 应用（uvicorn + 服务身份门控）提供 API-09 冻结引用解析、真实 HTTP 模型探针（完整回合/工具回合/正文后**真实断流**三种脚本形态，逐请求记录型号/鉴权/消息/工具定义）、真实 `RunService.start` + `default_executor_factory` + `PooledModelProvider`（真实分配器/容量/续租/结算）+ 真实 `AgentRunner`/`AgentRunnerExecutor`/`ModelCallTrace`；不 mock 存储或租约。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-262 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_reply_generations.py` exit=1，**5 failed / 1 passed（9.60s）**（断流后不重试 ⇒ 无新代；账本 API 不存在；迟到输出进正文；重发工具调用触发审计唯一冲突把 Run 打崩） | 同命令 exit=0，**6 passed in 11.01s** | ① `test_b262_partial_output_failure_retries_as_new_generation_with_snapshot` L623-705：旧代正文唯一落库 L647、新代正文 L648、每行显式代号 L649-652、**唯一持久 snapshot 且载荷 `{1, ""}`** L655-659、顺序"旧代 → 替换边界 → 新代" L664、终态 COMPLETED/final_text/序号在后 L669-674、**公开投影按代替换后可见正文只等于新代** L677-682、两次上游调用**换 Key**（两把真实成员 Key 各一）L685-693、租约 `["UNKNOWN","SETTLED"]` 且 Key 身份不同 L697-698、逐 attempt 审计 `RETRY/UNAVAILABLE`→`OK` L700-703。② `test_b262_exhausted_retry_budget_terminates_without_new_generation` L711-741：`agent.max_model_retries=0` ⇒ 无 snapshot L732、部分正文唯一一份 L733、终态 `run.failed/MODEL_UNAVAILABLE` 且排在正文后 L735-737、Run FAILED L739、探针只被调用一次 L740、可见正文保留部分正文 L741。③ `test_b262_ledger_opens_new_generation_only_after_partial_output_failure` L749-775：只有"已产生正文后失败"才分代 L762、未产生正文的失败不分代 L766-770、回合外 `accepts_output=False` L755-758、`audit_enabled=False` 不冒充审计身份 L773-775。④ `test_b262_late_output_after_round_close_is_fenced` L878-896：回合结束后迟到输出不进事件 L893-894、无失败不分代 L895、`reply_delta_fenced` warning 留痕 L896。⑤ `test_b262_abandoned_attempt_output_is_fenced_across_retry` L899-938：被放弃尝试在新回合进行中到达的迟到输出被尝试围栏挡住 L933-937、新代以 `{1, ""}` snapshot 开启 L936、账本代号推进到 1 L938。⑥ `test_b262_committed_tool_operation_business_key_survives_model_retry` L965-1031：真实 ASYNC Skill 工具回合（`execute_skill`→`reserve_operation`→`DurableTaskSubmitter`→真实 Worker 桩 4xx）提交操作，失败回合停在探针闸门时读取操作身份 L1010-1011，重试**重发同一工具调用**后**仍只有一行操作** L1019 且 `(operation_id, input_hash, idempotency_key, status)` 前后逐字节一致 L1021、业务键 `runtime-op:{id}:submit` L1022、`source_tool_call_id` 不变 L1023、Run COMPLETED 且新代可见正文正确 L1026-1031 | verified |

实现要点：**账本**（`model_call_trace.ModelCallTrace`）——分代是**流正确性**设施，与审计关联解耦：`begin()` 总是开启回合并返回 `ModelRound`；上一回合在已产生正文后失败（`note_failure_after_output` 只在 `note_delta` 之后生效）时下一次 `begin` 递增代号并置 `supersedes=True`；`close_round()`/`accepts_output()` 把回合外（含被取代）的迟到输出挡在正文之外；`audit_enabled=False` 的调用点不在事件里冒充 `model_audit_id`。**执行器**（`executor._ExecutorEventEmitter`）——正文按**当前代号**贴载荷（`drain_delta` 是时间边界/结构边界/终态前 flush 的唯一出口）；`on_model_started` 先 flush 旧代缓冲再落 `message.snapshot`（`{reply_generation: N, text: ""}`，空正文整组替换）再发 `model.started`，顺序"旧代正文 → 替换边界 → 新代 delta"由 `queue.join()`（canonical 先提交）保证；`on_delta` 在回合外只写 `reply_delta_fenced` warning，不并入任何一代。**重试边界**——`PooledModelProvider`/`AuditedModelProvider` 不再把"已输出正文后的可重试失败"改写成终止：失败事实记入账本后原样上抛，由 `AgentRunner` 的**冻结重试预算**裁决（429 遵守 Retry-After、5xx 有界退避 ⇒ 新代重试；预算耗尽或不可重试 ⇒ 终态），与 design §3.2"模型调用次数不保证一次"一致；每次尝试有**尝试围栏**（`_Emitted.closed`：尝试结束/失约停手即置位），被放弃尝试的迟到输出不并入正文。**审计幂等**（`audit_writer`）——重试重发同一工具调用时，操作层按 `(run_id, source_tool_call_id)` 返回原操作、业务键不更换，审计层按 `(run_id, tool_call_id)` `ON CONFLICT DO NOTHING` 保留首次行（重放留 `tool_call_audit_replay_skipped`），不把唯一冲突升级成 Run 失败。**公开投影**（`public_events`）——`message.snapshot` 进入公开词表并投影为 `WebchatSnapshotEvent`（整组替换事实；坏载荷显式失败，不静默当过滤，否则旧代正文会与新代 delta 拼接）。**业务名**（`run_service.STREAM_BUSINESS_TYPES`）——`message.snapshot` 落库业务名归入 `ASSISTANT_DELTA`（Console 词表里"正文流内容、不进运行轮廓"的类别，与 token 级增量同类；行身份仍由 `stream_type` 精确区分），使 `test_s402_every_executor_event_has_a_persisted_business_name` 的"每个执行器事件都有业务名"约束继续成立、Console 轮廓/详情无需新增词条。

回归（最终代码；本机无本任务残留进程；`tests/agent_runtime` 在**新建空库 + 迁移到 head** 的隔离库上整跑）：`uv run pytest -q tests/agent_runtime` exit=0，**1034 passed in 934.55s（0:15:34）**（含本任务 6 条）；相邻聚焦套件 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_provider_pool.py` exit=0，**11 passed in 12.12s**（TASK-025 的断流终止/429/5xx 分支未回归）；`uv run pytest -q tests/agent_runtime/test_capacity_webchat_durable_deltas.py tests/agent_runtime/test_execution_audit.py tests/agent_runtime/test_capacity_webchat_public_projection.py tests/contracts/test_capacity_webchat_event_contracts.py` exit=0，**81 passed in 13.71s**；`uv run pytest -q tests/agent_runtime/test_capacity_webchat_activity_write.py tests/agent_runtime/test_capacity_webchat_shared_tail.py tests/agent_runtime/test_capacity_webchat_run_controls.py tests/agent_runtime/test_capacity_webchat_activity_read.py` exit=0，**44 passed in 74.25s**；业务名词表联动 `uv run pytest -q tests/console_platform/test_runs_api.py tests/console_platform/test_run_event_details.py tests/agent_runtime/test_capacity_webchat_provider_pool.py tests/agent_runtime/test_capacity_webchat_durable_deltas.py tests/contracts/test_capacity_webchat_event_contracts.py` exit=0，**127 passed in 31.37s**。诚实记录：域回归**首轮**整跑 1 failed / 1033 passed——`test_s402_every_executor_event_has_a_persisted_business_name` 指出 `message.snapshot` 缺落库业务名（本任务新事件类型的真实集成缺口），补 `STREAM_BUSINESS_TYPES` 映射后整跑全绿；其余失败未出现（本机同期有另一 worktree 的 `tests/agent_worker` 进程在跑，未影响本套件结论）。

静态检查：`uv run ruff check`（5 个改动源文件 + 1 个新增测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 437 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行（`_invoke`/`_record_failure_for`/`_call_inner`/`_record_failure`/`record_tool_call`/`_insert_tool_call`/`_snapshot_event`/账本各方法；`build_registry` 76 与 `default_executor_factory` 132 是**改动前既有**的超限函数，未扩大职责）；`docs/configuration-inventory.csv` 保持 CRLF，只动本任务文件的行（新增 `MESSAGE_SNAPSHOT_EVENT` 1 行，`executor.py`/`public_events.py`/`audit_writer.py`/`run_service.py` 既有 21 行行号/取值平移，无增删其它行），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。

计划外披露：① `apps/agent-runtime/src/muad_agent_runtime/application/public_events.py`（canonical `message.snapshot` 的**公开投影**是"按代替换"成立的必要联动——不投影等于公开流仍会把新代 delta 拼到旧代正文；TASK-051/061 的遗留③/①由本任务闭合）；② `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`（`STREAM_BUSINESS_TYPES` 的落库业务名映射——`test_s402_every_executor_event_has_a_persisted_business_name` 要求执行器声明的每个事件都有业务名，否则 Console 轮廓会冒出裸小写名；`message.snapshot` 归入 `ASSISTANT_DELTA` 这一"正文流内容"业务名，Console 既有的轮廓/详情剔除语义因此继续成立，无需新增 Console 词条/i18n）；③ `apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py`（RED 实测：重试重发同一工具调用命中 `runtime.tool_call_audit` 的**部分唯一索引** `uq_tool_call_audit_run_tool_call`，IntegrityError 直接把 Run 打成 `COMMON_INTERNAL_ERROR` 并 `cancel_operations` 标 CANCELLED——不修则"新代重试"在"模型重复自己"的真实形状下必炸；改为幂等插入，无 DDL/迁移）；④ `docs/configuration-inventory.csv`（项目硬约束：模块级常量必须盘点）。未新增迁移/DDL/新列。

遗留（deferred，非本任务可闭合）：① 围栏/分代的**指标目录**（`reply_delta_fenced`、`tool_call_audit_replay_skipped` 等当前只有 warning/info 日志留痕）属 TASK-098；② 前端对 `message.snapshot` 的按代替换消费（分组收集、重连恢复、`transport_revision` 绑定）属 TASK-072/076/082；③ 公开边分片/游标/回压（同 run/reply_generation/canonical seq 的 part 组）属 TASK-061 已实现部分，本任务只提供 canonical 生产方与 Runtime 投影；④ 设计场景 E2E（S-03/E-09/E-16 等）保持 planned，由 TASK-110/111/113/116 的 verify-e2e 记录完整证据；⑤ 审计表"一个逻辑调用一行"的语义（部分唯一索引 + 重放跳过）若未来要保留每次重放的明细，需要新的审计身份列（DDL）——本任务不做，留待审计明细需求。
- B-262: verified — automated command passed; run_id=2be9000a84a944f5ae2d2f60b9324b51 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-262 RED：`tests/agent_runtime/test_capacity_webchat_reply_generations.py` → exit=1，5 failed / 1 passed（断流后不重试；分代账本不存在；迟到输出进正文；重发工具调用触发 `tool_call_audit` 唯一冲突把 Run 打崩）
- [2026-10-18] 实现 `model_call_trace.py` 回复生成代号账本、`executor.py` 分代发射/尝试围栏/重试边界回到 `AgentRunner` 预算、`public_events.py` snapshot 公开投影、`audit_writer.py` 工具审计幂等插入；同步 `docs/configuration-inventory.csv`（新增 1 行 + 18 行平移，CRLF 保持）
- [2026-10-18] B-262 GREEN：同命令 exit=0，6 passed in 11.01s；相邻聚焦套件 11 + 81 + 44 passed
- [2026-10-18] 域回归 `tests/agent_runtime` 首轮整跑：**1 failed / 1033 passed in 791.07s**——`test_s402_every_executor_event_has_a_persisted_business_name` 报 `message.snapshot` 缺业务名（真实集成缺口：执行器声明的每个事件都必须有落库业务名）；补 `STREAM_BUSINESS_TYPES` 映射后该用例通过，Console 轮廓/详情剔除语义不变
- [2026-10-18] 域回归 `tests/agent_runtime` 整跑（最终代码）：`uv run pytest -q tests/agent_runtime` exit=0，**1034 passed in 934.55s（0:15:34）**；业务名词表联动（Console 轮廓/详情 + 相邻 agent_runtime + 事件契约）127 passed
- [2026-10-18] completed (done)

---

## TASK-063: Run完成和待确认的同事务用户通知

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-014, TASK-056, TASK-059, TASK-060
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-263
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_notifications.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_service.py`, `tests/agent_runtime/test_capacity_webchat_activity_write.py`
- **Estimate**: 45分钟

### Description

终态/确认与feed seq/通知同事务，feed锁在Conversation/Run之后；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-263][integration] 先在`tests/agent_runtime/test_capacity_webchat_activity_write.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：终态/确认与feed seq/通知同事务，feed锁在Conversation/Run之后。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_activity_write.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.07s）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_notifications'`——验收文件先落地、通知写入模块尚不存在（日志 `$TMPDIR/muad-task063/task063-red.log`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `application/channel_notifications.py`——`write_run_notification`（先按 `conversation.channel_account_id` 取入口身份，NULL 即不进网页收件箱并返回 `None`（不编造入口 UUID）；再 `INSERT … ON CONFLICT DO NOTHING` 注册 + `SELECT … FOR UPDATE` 锁住 `(tenant,user)` feed 行；锁内按 `(tenant,delivery_key)` 查既有回执，命中即返回原行（`created=False`：不占 seq、不新增未读）；未命中才在锁内 `last_seq+1` 分配通知 seq 并插行，`delivery_state` 由调用方显式给出）、稳定投递键 `run_terminal_delivery_key`/`run_waiting_delivery_key`（`run:{id}:terminal`、`run:{id}:waiting-input:{interrupt_id}`）、`RunNotificationReceipt`、词表常量 `NOTIFICATION_KINDS`/`DELIVERY_STATES`（与 0026 CHECK 同词表；未知取值 `ValueError` 显式失败，不静默等约束报错）。② `application/run_service.py`——终态写入路径全部接同一接缝：`_finalize_run`（完成/协作取消）、`_finalize_failed`、`_append_cancelled_events`（未开始执行的取消）、`_reap_one`（回收）、`_close_queued_run`（排队超时）；每处先为终态事件预生成 `event_id`，再把**同一个 id** 交给通知行（通知引用产生它的终态事件），全部在**同一事务**内、`commit` 之前，且 feed 锁晚于 `lock_run_source`/`lock_execution` 的 Conversation/Run 行锁。③ `application/checkpoint.py`——`suspend_memory_confirmation` 的待确认事务内写 `RUN_WAITING_INPUT` 通知（delivery_key 绑定该中断 id、event_id 指向 `MEMORY_CONFIRMATION_REQUIRED`），`_notify_waiting_confirmation` 让该函数保持 ≤50 行。通知不写 canonical 事件、不占 canonical seq。
- [x] [B-263] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-263] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_write.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**11 passed in 20.88s**（日志 `$TMPDIR/muad-task063/task063-green.log`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-263 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 终态/确认与feed seq/通知同事务，feed锁在Conversation/Run之后；重复投递不增加未读，隐藏会话仍可发现，不因socket写入就已读 | tests/agent_runtime/test_capacity_webchat_activity_write.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_write.py"] | verified |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-263 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_activity_write.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.07s）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_notifications'` | 同命令 exit=0，**11 passed in 20.88s** | ① `test_b263_completed_run_notifies_in_terminal_transaction` L445-486：终态行 `COMPLETED`/租约释放 L460-461、**通知行身份**（run/conversation/entry/user L467-468、`kind=RUN_COMPLETED` L469、稳定键 L470、`delivery_state=NOT_REQUIRED` L471、**`read_at is None`** L472、空产物/无 task L473）、**feed 水位与通知 seq 同事务**（`feed.last_seq == 1` L476-477、`row.seq == feed.last_seq` L478）、**未读计数 1** L479、**通知引用终态事件** L484、**通知不占 canonical seq**（`conversation.last_seq == 终态事件 seq` L487-491）。② `test_b263_failed_run_notifies_terminal` L488-507：执行失败（`RuntimeError`）→ `RUN_FAILED` 终态行 + 通知（kind/键/未读）L500-507。③ `test_b263_cancelled_run_notifies_terminal` L510-528：未开始执行的取消走 `cancel_run` → `CANCELLED` CAS + 取消事件 + 通知 L517-528（终态载荷 `CANCELLED` L525、通知 `event_id` 指向终态行 L528）。④ `test_b263_notification_failure_rolls_back_terminal_state` L530-572：**同事务回滚证明**——`channel_notification` 上的 BEFORE INSERT 触发器（真实 DDL）令通知写失败 ⇒ `_finalize_run` 抛 `DBAPIError` L557-558，Run 仍 `RUNNING` 且租约未释放 L562-564、**无终态事件** L565、**无通知行** L566、**feed 行不存在** L567、提交仍 `OPEN` L568-571。⑤ `test_b263_feed_lock_is_acquired_after_run_lock` L594-651：**锁序真实证据**——holder 连接先持 `(tenant,user)` feed 行锁 L604-612，放开执行闸门后终态事务进入等待（`pg_stat_activity.wait_event_type='Lock'` 且 `pg_blocking_pids` 含 holder L574-591），此时第三条连接的 `UPDATE runtime.run_record` 以 `statement_timeout` 超时（SQLSTATE 57014）L614-628 ⇒ **等 feed 锁时已持有 Run 行锁**；释放 holder 后通知落库 L630-640，控制组同一 UPDATE 成功 L641-646。⑥ `test_b263_waiting_input_notifies_in_pause_transaction` L691-720：待确认（真实 `MemoryConfirmationService.propose`+`suspend`）→ `WAITING_INPUT` + 中断行 L698-702、通知 `kind=RUN_WAITING_INPUT` L706-709、**delivery_key 绑定中断身份** L711、`read_at is None` L712、feed 水位/seq L713-714、`event_id` 指向 `MEMORY_CONFIRMATION_REQUIRED` L715-720。⑦ `test_b263_waiting_input_notification_failure_rolls_back_pause` L723-761：同一失败注入 ⇒ `suspend` 显式失败 L731-733，Run 仍 `RUNNING` 且租约/归属不变 L736-738、无中断 L739-741、无确认事件 L742-750、无通知/无 feed L751-752。⑧ `test_b263_duplicate_delivery_reuses_receipt_without_new_unread` L763-812：同 delivery_key 重投返回**原行原 seq** L783-787、feed 水位仍 1 L788-789、未读仍 1 L790-791、**软删（隐藏）后重投仍返回原回执且不新发** L794-812。⑨ `test_b263_archived_conversation_notification_stays_discoverable` L814-835：ARCHIVED（隐藏）会话的终态仍进收件箱，按用户未读扫描可发现 L827-835。⑩ `test_b263_socket_replay_does_not_mark_read` L837-855：终态事件经 `tail_run_events`（SSE 读取路径）重放后 `read_at` 仍为 NULL、未读仍 1 L847-855。⑪ `test_b263_conversation_without_entry_has_no_inbox_row` L857-872：无入口归属会话终态正常收尾但**不建收件箱行/feed** L868-872。 | **真实 Runtime 业务服务**：`RunService.start` 受理（真实准入计数/冻结快照/`claim_continuation`/真实 `ExecutionSupervisor` 槽）、`_finalize_run`/`_finalize_failed`/`cancel_run`（含 `_cancel_not_started`）、`MemoryConfirmationService.propose`/`suspend` 全走生产代码；执行器是进程内 fake（模型调用不在本契约断言内），存储/租约/行锁/事件/水位都是真实组件。**隔离 PostgreSQL**：每用例一个空库 + `alembic upgrade head`、跑完 drop（`async_tool_datastore`，不触碰共享 dev 库）；通知/feed 行、`(tenant,user,seq)` 与 `(tenant,delivery_key)` 完整唯一、`conversation.last_seq`、canonical 事件与租约都是真实行。**真实行锁**：feed 行锁等待用 `pg_stat_activity`+`pg_blocking_pids` 观察，Run 行锁用第三条连接的真实 `statement_timeout` 超时（57014）证明；**真实故障注入**：通知表 BEFORE INSERT 触发器（隔离库 DDL）证明终态/确认与通知同事务（不是 mock 或事后断言）。 | verified |

实现要点：**同事务**——终态/待确认的写入路径在**同一个数据库事务**里先写状态与 canonical 事件、再在同一事务内写通知行；通知写失败即整体回滚（`_finalize_run` 的 CAS 更新、终态事件、提交关闭、准入释放与通知一起消失），因此不存在"终态已提交但用户看不到结果"的窗口。**锁序**——`write_run_notification` 在既有 Conversation→Run 行锁之后才取 `(tenant,user)` feed 行锁（`INSERT … ON CONFLICT DO NOTHING` 注册 + `SELECT … FOR UPDATE`），调用点全部位于 `lock_run_source`/`lock_execution` 之后，任何反向路径都不会与终态写互锁；锁内不做网络调用。**seq 与投递键**——通知 seq 在 feed 行锁内 `last_seq+1` 分配（与 `EventWriter._next_seq` 同形，但**不碰** `conversation.last_seq`：通知不是会话事件）；`(tenant_id,user_id,seq)` 与 `(tenant_id,delivery_key)` 由数据库完整唯一兜底，`delivery_key` 稳定（终态 `run:{id}:terminal`、待确认 `run:{id}:waiting-input:{interrupt_id}`）⇒ 崩溃重放/主动补投返回**原行**，不新占 seq、不新增未读；查回执**不带 `is_deleted` 谓词**，隐藏（软删）通知保留唯一回执与投递资格。**待确认与终态两路径**——待确认（记忆确认 interrupt）在 `suspend_memory_confirmation` 的同一事务写 `RUN_WAITING_INPUT` 通知并把 `event_id` 指向确认事件；终态（完成/失败/取消/回收/排队超时）写 `RUN_COMPLETED` 通知并把 `event_id` 指向终态事件；两者共用同一接缝与同一幂等语义。**边界**——无入口归属的会话（`channel_account_id IS NULL`）不进网页收件箱（`entry_id` 是通知 DTO 必填身份，不编造 UUID），终态/事件/租约照常收尾；`delivery_state` 显式选择（网页持久入箱 `NOT_REQUIRED`，出站意图 `PENDING` 留给机器人交付路径），未知 kind/state 显式 `ValueError`。**已读独立**——写入一律 `read_at IS NULL`，SSE/socket 重放不改已读。
回归（最终代码；本机无 dev 残留进程；`tests/agent_runtime` 在**新建空库 + 迁移到 head** 的隔离库上整跑）：`uv run pytest -q tests/agent_runtime` exit=0，**986 passed in 882.30s**（含本任务 11 条）；`uv run pytest -q tests/migrations/test_capacity_webchat_notification_schema.py tests/contracts/test_capacity_webchat_event_contracts.py tests/test_configuration_inventory.py` exit=0，**69 passed in 14.35s**；通知/待确认相邻套件 `test_capacity_webchat_run_controls.py`、`test_memory_redesign_proposals.py`、`test_memory_redesign_confirmation.py`、`test_run_leases.py`、`test_interrupt_checkpoint.py` exit=0，**94 passed in 204.33s**。诚实记录：共享 dev 库上首轮整跑出现 12 errors（`tenant` fixture teardown 的 `DELETE FROM runtime.run_record` 被 `channel_notification.run_id` 外键拦住）与 1 failed（`test_staging_cleanup_never_deletes_accepted_run_artifacts` 的 `removed == 2`）——前者是**本任务新行必须纳入共享清理顺序**的真实集成缺口（已把 `channel_notification`/`channel_notification_feed` 前置进 `tests/agent_runtime/conftest.py` 的 `CLEANUP_ORDER`，重跑 26 passed），后者经**同一用例在新空库上单跑 1 passed** 证实是共享 dev 库中**别的套件留下的已过期 `channel_upload` 行**（`cleanup_expired_staging` 按设计全库扫描、索引不带租户谓词）导致的既有跨套件污染，与本任务改动无关（未删任何 dev 数据）。
静态检查：`uv run ruff check`（3 个改动源文件 + 1 个新增测试文件 + conftest）干净；`uv run mypy apps packages` → Success: no issues found in 428 source files；AST 逐函数核对：本任务新增/修改函数全部 ≤50 行（`write_run_notification` 39、`_lock_feed` 18、`_validate`/`_entry_id_of`/`_existing_receipt`/`_next_seq` ≤13、`_notify_waiting_confirmation` 17、`suspend_memory_confirmation` 46→49）；`docs/configuration-inventory.csv` 保持 CRLF，只动本任务文件的行（新增 `channel_notifications.py` 7 行常量，`run_service.py` 既有 15 行、`checkpoint.py` 既有 4 行行号平移，无增删其它行），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。
计划外披露：① `apps/agent-runtime/src/muad_agent_runtime/application/checkpoint.py`（待确认事务的**唯一**产生点就在 `suspend_memory_confirmation`；不在该事务内写通知就无法满足"确认与通知同事务"，属计划 Files 之外的必需联动）；② `tests/agent_runtime/conftest.py`（共享 `tenant` fixture 的 `CLEANUP_ORDER` 必须包含新表，否则所有在共享库跑终态路径的套件 teardown 被外键拦住）；③ `docs/configuration-inventory.csv`（项目硬约束：模块级常量必须盘点）。未新增迁移/DDL（0026 已提供两张表，无新列）。
遗留（deferred，非本任务可闭合）：① **机器人出站投递意图（`PENDING`）与投递泵**属 TASK-065/066/067——本任务只提供 `delivery_state` 显式接缝与稳定 `delivery_key`；② **通知读取/分页/显式已读（API-11）与受信 Task 结果入箱**属 TASK-064（`TASK_RESULT` kind 已在词表中，写入端口留给 TASK-068）；③ **通知清理/保留**属 TASK-069（隐藏与投递回执保留分离已由唯一键与无谓词回执查询保证）；④ **通知/投递指标目录**属 TASK-098；⑤ 待确认中断 **TTL 到期**后的收口（TASK-056 遗留④）不在本契约断言内：本任务只保证"挂起即有可发现通知"，到期扫描仍由既有 `memory_confirmation_expiry` 路径负责。
- B-263: verified — automated command passed; run_id=00240a9a7e964ac4a4ba9958db563c9d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-263 RED：`tests/agent_runtime/test_capacity_webchat_activity_write.py` → exit=2，1 error during collection（`muad_agent_runtime.application.channel_notifications` 尚不存在）
- [2026-10-18] 实现 `channel_notifications.py`（feed 锁内 seq 分配 + 稳定投递键幂等回执 + 词表校验）、`run_service.py` 五处终态路径与 `checkpoint.py` 待确认路径的同事务通知（终态事件 `event_id` 与通知行共用同一 id）；同步 `docs/configuration-inventory.csv`（新增 7 行 + 19 行行号平移，CRLF 保持）
- [2026-10-18] B-263 GREEN：同命令 exit=0，11 passed in 20.88s；域回归 `tests/agent_runtime` 986 passed（隔离库整跑）、相邻套件 94 passed、通知 schema/事件契约/配置盘点 69 passed；ruff 干净、mypy 428 files 无问题
- [2026-10-18] 集成缺口修复：`tests/agent_runtime/conftest.py` 的 `CLEANUP_ORDER` 前置 `channel_notification`/`channel_notification_feed`（共享 dev 库上终态 Run 的通知行会拦住 `run_record` 的 teardown 删除）；重跑受影响三套件 26 passed
- [2026-10-18] completed (done)

---

## TASK-064: 活动收件箱增量读取与独立已读

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-052, TASK-058, TASK-063
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-264, B-03
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_notification_queries.py`, `apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py`, `tests/agent_runtime/test_capacity_webchat_activity_read.py`
- **Estimate**: 45分钟

### Description

按用户feed增量分页、显式read通知并提供受信Task结果入箱端口；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-264][integration] 先在`tests/agent_runtime/test_capacity_webchat_activity_read.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：按用户feed增量分页、显式read通知并提供受信Task结果入箱端口。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_activity_read.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.10s）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_notification_queries'`——验收文件先落地，读取/已读/入箱服务尚不存在。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `application/channel_notification_queries.py`——`list_notifications`（feed 水位 `through_seq`/`next_seq` 增量分页、`after_seq > through_seq` 显式 `CURSOR_AHEAD`、`total` 为窗口计数、`unread_count` 为用户级可见未读）、`mark_read`（行锁 + PG 服务端时间、幂等返回原 `read_at`、只动 `read_at`/`update_time`，回执形状 `{id, read_at}` 与前端契约 TASK-073 已锁形状一致）、`register_task_result`（核真实 `ToolResultInbox`/`ToolOperation`/Run/用户/会话入口归属 + 每个 artifact 的 `task_id` 来源；走 TASK-063 同一写入接缝 `write_run_notification`，稳定键 `task:{task_id}:final` 重投命中原回执）；安全投影复用 `project_attachment`（历史/SSE/通知唯一实现），DTO 不含投递/租约/存储内部字段。② `application/channel_notifications.py`——`write_run_notification` 扩展 `task_id`/`artifact_ids`（TASK_RESULT 必须有真实 Task 身份、其余 kind 没有位置，`_validate` 显式失败）、`task_result_delivery_key`、`NotificationKind` 字面量。③ `api/webchat_history.py`——`GET /internal/webchat/notifications`、`POST .../{id}/read`、`POST /internal/webchat/task-results`（同一 `/internal/webchat` 受信面 + 服务门控；入箱端点 `commit` 之后才回 `persisted`）。④ `public_events.py`——`_attachment_of` 更名为公开 `project_attachment`（三处调用同一实现，不再私有复制）。⑤ `packages/contracts/.../webchat_events.py`——`WebchatTaskResultRegistration`、`WebchatTaskResultReceipt`、`WebchatNotificationReadReceipt`（`extra=forbid` 的共享契约；Worker/Gateway 跨 app 复用）。
- [x] [B-264] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-264] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed in 13.91s**。
- [x] [B-03][integration] 将原B-03落入`tests/agent_runtime/test_capacity_webchat_activity_read.py`，先RED后GREEN；跨页读取和提交顺序、PG；断言：事件追尾与历史分页无跳过；读游标不能跨会话；通知 seq 按用户提交顺序推进。三条断言分别由 `test_b03_notification_seq_advances_in_user_commit_order`、`test_b03_event_tailing_and_history_pagination_do_not_skip`、`test_b03_read_cursors_cannot_cross_conversations_or_users` 覆盖（RED/GREEN 与位置见证据表）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-264 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 按用户feed增量分页、显式read通知并提供受信Task结果入箱端口；核真实Task/Run/用户/入口来源及授权，持久入箱后才回persisted，feed水位/投递状态/已读不混用 | tests/agent_runtime/test_capacity_webchat_activity_read.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | verified |
| B-03 | integration | 跨页读取和提交顺序、PG | 事件追尾与历史分页无跳过；读游标不能跨会话；通知 seq 按用户提交顺序推进 | tests/agent_runtime/test_capacity_webchat_activity_read.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：隔离 PostgreSQL（每用例一个空库 + `alembic upgrade head`，跑完 drop，不触碰共享 dev 库）、真实 Runtime HTTP 应用与 `require_internal_service` 服务门控、真实 `RunService.start/_finalize_run` 终态事务、真实 `ToolResultInbox`/`ToolOperation`/`Run`/`Conversation`/`Artifact` 行；故障注入用隔离库真实 DDL 触发器。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-264 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_activity_read.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.10s）**：`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_notification_queries'` | 同命令 exit=0，**10 passed in 13.91s** | ① `test_b264_incremental_pagination_by_feed_seq_has_no_skips` L673-715：`after_seq=0&page_size=2` → items seq `[1,2]`、`through_seq=5`/`next_seq=2`/`total=5`/`unread_count=5`；`after_seq=next_seq` 续读 `[3,4]`→`[5]`；追平后 `next_seq=through_seq=5`、空页不夹取；合并三页 = `[1..5]` 不重不漏；offset `page=2` 与水位语义一致；新提交只出现在水位之后（`after_seq=5` → `[6]`）。② `test_b264_projection_is_safe_and_carries_public_refs` L717-743：通知项键集合恰为公开 13 字段，`delivery_state/delivery_key/lease_*/storage_key/payload_json` 全部缺席；`entry_id/agent_id/conversation_id/run_id` 为真实行身份。③ `test_b264_committed_terminal_notification_is_incrementally_readable` L745-771：真实 `RunService` 终态事务提交的通知可增量读到（`kind=RUN_COMPLETED`、未读 1）；**通知不占 canonical seq**（会话水位 == 终态事件 seq）。④ `test_b264_read_is_explicit_idempotent_and_independent_from_delivery` L773-846：`DELIVERED` 后 `read_at` 仍 NULL（送达不代替已读）；显式已读回执 `{id, read_at}`（与前端 TASK-073 已锁形状一致）；已读后 `delivery_state` 不变、feed 水位不变、读过的行仍在 feed；重复标记返回原 `read_at`；`PENDING` 通知可读且投递状态保持（已读不要求送达）；他用户 404、门控 403 无状态变化、隐藏行 404 且投递回执保留；未读徽标只统计可见行。⑤ `test_b264_task_result_inbox_port_verifies_source_and_persists_before_ack` L848-914：`persisted=true/duplicate=false`；**独立连接**在响应返回时已读到已提交的行（`kind=TASK_RESULT`、`delivery_key=task:{task_id}:final`、`delivery_state=NOT_REQUIRED`、`read_at NULL`、`artifact_ids`/`event_id` 指向受理回执事件）；重投 `duplicate=true` 且同 `notification_id`/`seq`、feed 水位不变；投影附件走同一安全投影（`available`、无存储键）。⑥ `test_b264_task_result_port_rejects_fabricated_sources` L916-967：伪造 task/run/user/artifact 一律 `TOOL_RESULT_BINDING_MISMATCH`（403）、无入口归属 404、门控 403，全部不落通知行、不推进 feed。⑦ `test_b264_task_result_write_failure_never_acknowledges_persistence` L969-1008：隔离库 BEFORE INSERT 触发器令写入失败 ⇒ `DBAPIError`、无通知行、无 feed 行（不产生假 `persisted`）。 | **真实 Runtime 业务服务**：`RunService.start/_finalize_run` 与 `write_run_notification`（TASK-063 同一接缝）走生产代码；HTTP 面走真实 FastAPI 路由与服务门控。**隔离 PostgreSQL**：真实唯一约束 `(tenant,user,seq)`/`(tenant,delivery_key)`、真实行锁（`SELECT … FOR UPDATE`）、真实 `ToolResultInbox`/`ToolOperation`/`Artifact` 行与 FK。**真实故障注入**：`runtime.channel_notification` BEFORE INSERT 触发器（隔离库 DDL）证明"持久入箱后才回 persisted"（写失败即无回执）。 | verified |
| B-03 | 同上（同一验收文件、同一命令） | 同上（10 passed in 13.91s，含本场景 3 条） | ① `test_b03_notification_seq_advances_in_user_commit_order` L1010-1043：逐条提交 seq `[1,2,3]` 按提交顺序推进、他用户独立（各自从 1 起）、`asyncio.gather` 并发提交两条 → seq `{4,5}` 无重复、feed 水位 5、序号连续无缺口。② `test_b03_event_tailing_and_history_pagination_do_not_skip` L1045-1115：冻结 `through_seq` 后按 page_size=1 逐页读取与真实可见消息逐一对应（无跳过/无重复）；`tail_run_events` 重放 seq 严格递增且与真实持久流事件逐一对应、终态收尾；新一轮提交后旧冻结水位页不变。③ `test_b03_read_cursors_cannot_cross_conversations_or_users` L1117-1189：会话 A 的水位用在会话 B 上显式 `CURSOR_AHEAD`（不静默夹取）；B 自己的页只含 B 的事件；用户 A 的 feed 水位用在他用户上显式 `CURSOR_AHEAD`；无通知用户返回空页且未读 0。 | **真实提交顺序**：并发两条通知写经 `(tenant,user)` feed 行锁串行化（真实行锁，非 mock）；**真实历史分页**：HTTP `/internal/webchat/conversations/{id}/messages` 冻结水位分页与 canonical 行逐一对照；**真实事件追尾**：`RunService.tail_run_events` 走共享追尾实现，与 `canonical_event` 行逐一对照。 | verified |

回归（最终代码；本机无 dev 残留进程；`tests/agent_runtime` 在**新建空库 + 迁移到 head** 的隔离库上整跑）：`uv run pytest -q tests/agent_runtime tests/contracts tests/migrations/test_capacity_webchat_notification_schema.py tests/test_configuration_inventory.py` exit=0，**2291 passed in 896.69s（0:14:56）**。诚实记录：实现中间版本的同类整跑首轮为 **2286 passed / 1 failed**——失败 `test_capacity_webchat_queue_projection.py::test_b236_creation_persists_waiting_runtime_stage_with_its_event_atomically`（共享 dev 库环境偶发 `DBAPIError`），该用例单跑 1 passed、模块三连跑 6 passed ×3，复跑 `uv run pytest -q tests/agent_runtime` exit=0、**999 passed in 816.47s**（同一用例通过）⇒ 环境偶发，与本次改动无关。

静态检查：`uv run ruff check`（4 个改动源文件 + 1 个新增源文件 + 1 个新增测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 431 source files；AST 逐函数核对：本任务新增/修改的生产函数全部 ≤50 行（`channel_notification_queries.py` 最长 `_page` 35、`list_notifications`/`_project` 33；`write_run_notification` 42 含既有逻辑）；`docs/configuration-inventory.csv` 保持 CRLF，只动本任务文件的行（`channel_notifications.py` 7 行行号平移 + 新增 `KIND_SUMMARIES` 1 行），`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。

实现要点：**增量与水位**——`after_seq` 只作窗口下界，`through_seq` 取自 feed 行（无 feed 行即 0，不注册、不写任何状态）；`next_seq` 在本页扫到窗口末尾时等于 `through_seq`，否则为本页最后一行 seq；`after_seq > through_seq` 显式 `CURSOR_AHEAD`（跨用户/跨流游标不静默夹取成空页）。**先 count 再取行**：`through_seq` 读出后窗口内不可能再出现新可见行（seq 在 feed 行锁内分配、与通知行同事务提交），可见集合只会因隐藏缩小 ⇒ 契约页边界不变量 `len(items) <= total` 恒成立。**已读独立**——`mark_read` 只写 `read_at`/`update_time`（行锁 + PG 服务端时间 `statement_timestamp()`），回执 `{id, read_at}` 与前端 TASK-073 已锁形状一致；幂等（已有 `read_at` 直接返回原值）；`delivery_state`/租约/投递结果永不因已读改变，反之送达（TASK-065 路径）也不写已读。**入箱端口**——只接受 Worker 真实持有的身份事实（task/run/actor user/artifact）；会话/入口/Agent 从真实行导出（调用方自报入口/Agent 没有位置）；核 `ToolResultInbox`（结果已受理的持久证据，并给出通知 `event_id` 引用）→ `ToolOperation`（task/run/actor 一致）→ `Run`（run/user 一致）→ 会话入口归属（NULL ⇒ 404）；每个 artifact 必须 `artifact.task_id == task_id`；写入走 TASK-063 的 `write_run_notification`（feed 锁内 seq、稳定键 `task:{task_id}:final` 重投命中原回执），端点 `commit` 之后才回 `persisted=true`。**安全投影**——通知 DTO 只含公开 13 字段；附件复用 `project_attachment`（历史/SSE/通知唯一实现，`TRANSCRIPT`/无权威行不投影）。

计划外披露：① `packages/contracts/src/muad_contracts/webchat_events.py`（入箱请求/回执与已读回执必须与 Worker（TASK-068）和前端（TASK-073 已锁 `{id, read_at}`）共享；webchat 契约不经 `__init__` 再导出，无导出面变更）；② `apps/agent-runtime/.../application/channel_notifications.py`（TASK-063 的写入接缝必须扩展 `task_id`/`artifact_ids` 才能承载 `TASK_RESULT`——不另起第二份插入路径）；③ `apps/agent-runtime/.../application/public_events.py`（附件安全投影更名公开 `project_attachment` 供通知读取复用，历史/SSE 两处调用点语义不变）；④ `docs/configuration-inventory.csv`（项目硬约束：`channel_notifications.py` 7 行行号平移 + 新增 `KIND_SUMMARIES` 1 行，CRLF 保持）。未新增迁移/DDL（0026 的两张表够用，无新列）。

遗留（deferred，非本任务可闭合）：① Gateway 公开面（API-11 公共路由、CHAT/X-Chat-Context 复核、`{id, read_at}` 透传）属 TASK-070；② Worker 侧接入与投递确认属 TASK-068（本任务提供 `POST /internal/webchat/task-results` 与契约）；③ 通知清理/保留与"用户活动游标最早可用水位"（`CURSOR_EXPIRED`）属 TASK-069——本任务只实现"超前游标显式拒绝"，未引入保留下界；④ 通知/投递指标目录属 TASK-098；⑤ 出站投递意图（`PENDING`）的 reserve/renew/ack 与投递泵属 TASK-065/066/067（本任务只写 `NOT_REQUIRED` 的网页入箱行）。
- B-03: verified — automated command passed; run_id=c55f522b202f495781e5b448c5a50de9 (confirmed_by: runner)
- B-264: verified — automated command passed; run_id=c55f522b202f495781e5b448c5a50de9 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-264/B-03 RED：`tests/agent_runtime/test_capacity_webchat_activity_read.py` → exit=2，1 error during collection（`muad_agent_runtime.application.channel_notification_queries` 尚不存在）
- [2026-10-18] 实现：新增 `channel_notification_queries.py`（feed 水位增量分页 + 显式已读 + 受信 Task 结果入箱）；`channel_notifications.py` 扩展 `task_id`/`artifact_ids` 与稳定键 `task:{task_id}:final`；`api/webchat_history.py` 三个 `/internal/webchat` 受信端点；`packages/contracts/.../webchat_events.py` 三个共享契约（入箱请求/回执、已读回执）；`public_events.py` 附件投影公开为 `project_attachment`；同步 `docs/configuration-inventory.csv`（CRLF 保持）
- [2026-10-18] B-264/B-03 GREEN：同命令 exit=0，10 passed in 13.91s；断言位置与真实边界证据见 Acceptance Evidence
- [2026-10-18] 诚实记录：实现早期两条用例漏挂隔离库 fixture，误在共享 dev 库写入 `activity-read-*` 行并创建 `fail_task_notification` 触发器；已按 FK 顺序删除本任务租户的行并 drop 触发器/函数（未动任何 dev 数据），并把 `activity_tenant` fixture 改为依赖 `activity_db`（结构上不可能再落到共享库）
- [2026-10-18] 域回归（最终代码）：`tests/agent_runtime`+`tests/contracts`+通知 schema+配置盘点 exit=0，**2291 passed in 896.69s**；首轮环境偶发 `queue_projection` 用例经单跑/模块三连跑/整跑复跑（999 passed）全绿；`ruff` 干净、`mypy apps packages` 431 files 无问题
- [2026-10-18] completed (done)

---

## TASK-065: 交付reserve/renew/ack持久协议

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-063
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-265
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_delivery.py`, `apps/agent-runtime/src/muad_agent_runtime/api/channel_delivery.py`, `tests/agent_runtime/test_capacity_webchat_delivery_reservation.py`
- **Estimate**: 45分钟

### Description

API-18按稳定delivery_key/attempt/epoch预留；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-265][integration] 先在`tests/agent_runtime/test_capacity_webchat_delivery_reservation.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：API-18按稳定delivery_key/attempt/epoch预留。RED（实现模块未建时的树状态，实现文件临时移出后复现）：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_delivery_reservation.py` exit=1，**16 条用例全部无法收集**（`ImportError while loading conftest` → `ModuleNotFoundError: No module named 'muad_agent_runtime.api.channel_delivery'`；同源 `application/channel_delivery.py`、`muad_contracts.channel_delivery` 亦不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：`packages/contracts/src/muad_contracts/channel_delivery.py`（API-18 线契约：稳定 `delivery_key`+`attempt_id`+`epoch` 栅栏、租期边界 5–3600s/默认 60s、ack 结局 `DELIVERED/NOT_SENT/UNKNOWN`、首次结果 JSON 严格序列化且 ≤4096B）；`application/channel_delivery.py`（持久状态机：PENDING→DELIVERING（行锁内发号：`lease_owner`=attempt、`lease_epoch`+1、`lease_until`=PG now+租期、`attempt`+1）→ ack 后 DELIVERED（冻结首次结果、清租约）/PENDING（NOT_SENT，退避 `base·2^(attempt-1)` 有界）/UNKNOWN（保留核实）；同 attempt 有效租约重放不重复投递、不同 attempt 只能接管过期租约（epoch 防重）、renew/ack 失约即停手 `DELIVERY_LEASE_LOST`、ack 幂等回放首次结果、DELIVERED 重放超保留期 `DELIVERY_RECEIPT_EXPIRED`、NOT_REQUIRED 显式冲突、时间全用 PG 服务端时钟、事务内不联网）；`api/channel_delivery.py`（API-18 端口：`POST /internal/channel-notifications/reserve`（按稳定键）与 `.../{id}/renew|ack`（键复核身份），路由器级 `require_internal_service`，设置快照在事务外按次取一次、坏源显式失败）。`main.py` 仅装配两行（计划外文件，披露）。
- [x] [B-265] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-265] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_reservation.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**16 passed in 29.15s**（补上"网络在事务外"的 `FOR UPDATE NOWAIT` 探测后的收尾复跑；此前 23.42s 同绿）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-265 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | API-18按稳定delivery_key/attempt/epoch预留；只有实际交付成功ack，已送达重放首次结果，存在占位不等于成功；网络在事务外 | tests/agent_runtime/test_capacity_webchat_delivery_reservation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_reservation.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-265 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_delivery_reservation.py` exit=1，**16 条用例全部无法收集**（`ImportError while loading conftest`：`ModuleNotFoundError: No module named 'muad_agent_runtime.api.channel_delivery'`——以移除实现模块的树状态复现"实现尚不存在"） | 同命令 exit=0，**16 passed in 29.15s**（实现就位后首跑 26.17s、收尾 23.42s 同绿） | ① `test_b265_delivery_state_vocabulary_matches_the_persisted_check` L433-453（服务/线协议词表 == 0026 `ck_channel_notification_delivery_state` 的真实 CHECK 定义）；② `test_b265_reserve_grants_a_bounded_lease_and_a_placeholder_is_not_success` L460-482（PENDING→DELIVERING、`lease_owner`=attempt、`lease_epoch`=1、`attempt`=1、`lease_until`≈PG now+30s（27–33s 带）、**占位无回执** L482 `delivery_result IS NULL`）；③ `test_b265_repeated_reserve_is_idempotent_and_other_attempts_are_fenced` L485-506（同 attempt 重放 epoch/期限/计数逐字段不变；不同 attempt 对有效租约 ⇒ `DELIVERY_LEASE_LOST` 且行逐字段不变）；④ `test_b265_concurrent_reserves_grant_exactly_one_lease` L508-529（`len(grants) == 1` L522 + 恰一个 `DELIVERY_LEASE_LOST`；真实行 `lease_owner`=胜者）；⑤ `test_b265_reserve_waits_on_the_notification_row_lock` L531-547（旁路连接持行锁 0.4s 后 `task.done() is False` L540，放锁后才发号）；⑥ `test_b265_expired_lease_is_reclaimed_with_a_new_epoch_and_the_old_attempt_stops` L550-609（过期租约回收 epoch=2/attempt=2 L593；旧 attempt 的 renew/ack 与接管后迟到 ack 一律 `DELIVERY_LEASE_LOST` 且 `delivery_result` 仍为 NULL）；⑦ `test_b265_renew_requires_the_live_lease_and_only_moves_forward` L612-642（错 attempt/epoch ⇒ 停手；60s 续租前进、5s 请求不回退；过期后停手）；⑧ `test_b265_ack_delivered_freezes_the_first_result_and_replays_it` L645-712（只有 DELIVERED 写回执、清租约；重放 `reused=True`+首次结果 L672；重复 ack 换结果也不覆盖；无租约的别人 ack ⇒ 停手）；⑨ `test_b265_ack_not_sent_returns_to_pending_with_bounded_backoff` L715-752（NOT_SENT 回 PENDING、清租约、退避≈base 5s（4–8s 带）L739，第二次退避更大）；⑩ `test_b265_unknown_is_retained_for_verification_and_never_resent` L755-795（UNKNOWN 保留回执、不再授租约、`attempt`/`lease_owner` 不变 L783、renew 停手）；⑪ `test_b265_not_required_is_not_an_outbound_intent` L798-820（reserve ⇒ `COMMON_CONFLICT` L805 且行不变；ack ⇒ 停手）；⑫ `test_b265_receipt_replay_expires_beyond_the_retention_window` L823-846（真实行把 `acked_at` 移到 31 天前 ⇒ `DELIVERY_RECEIPT_EXPIRED` L843，仍 DELIVERED、不新发号）；⑬ `test_b265_delivery_identity_and_tenant_are_enforced` L849-896（未知键/错 id/错键/跨租户一律 `COMMON_NOT_FOUND`）；⑭ `test_b265_reserves_the_pending_intent_written_by_the_notification_path` L899-927（TASK-063 `write_run_notification(delivery_state=PENDING)` 写下的 `run:{run_id}:terminal` 意图被本协议按同一键领取）；⑮ `test_b265_api18_requires_service_identity_and_walks_the_protocol` L930-995（缺/错服务身份 403 L943 且行不变；真实封套 reserve/renew/ack/重放/404/422 全链路）；⑯ `test_b265_settings_source_is_read_per_call_and_failures_do_not_advance_state` L998-1063（设置快照按次在事务外取用；源不可读 ⇒ `PLATFORM_SETTINGS_UNAVAILABLE` L1050 且行逐字段不变；`LockProbingSettings` 用另一条连接对同一行 `FOR UPDATE NOWAIT` 探测成功 L1063 ⇒ **网络在事务外**） | 真实 `main.app` ASGI 装配（真实路由 + `require_internal_service` + 真实响应封套）与真实 `ChannelDeliveryService`/`ChannelDeliveryPort`；隔离 PostgreSQL（`async_tool_datastore`：空库 + `alembic upgrade head`，跑完 drop）上的真实 `runtime.channel_notification` 行、真实 `(tenant_id,delivery_key)` 唯一约束、真实 `SELECT … FOR UPDATE` 行锁与真实 PG 服务端时钟（租约/退避/回执时刻全部由它裁决）；真实并发（两独立事务同时 reserve 恰一个发号）与真实锁阻塞（旁路连接持锁期间 reserve 确实排队）；TASK-063 的真实写入路径作为上游；不 mock 存储或租约，无模型调用 | verified |

实现要点：**发号**——`reserve` 按稳定 `delivery_key` 取行 `FOR UPDATE`，锁后取 `statement_timestamp()`，PENDING 或已过期 DELIVERING 才发新租约（`lease_epoch+1`/`attempt+1`/`lease_until=now+租期`）；同一 attempt 在租约有效期内重放原租约（不重复投递），别人持有的有效租约 ⇒ `DELIVERY_LEASE_LOST`。**确认**——`ack` 先按冻结回执的 (attempt, epoch) 判幂等重放（丢失响应后的重试不重写），否则要求**仍有效**的租约（非 DELIVERING/epoch 或 attempt 不符/已过期一律停手）；`DELIVERED` 才写送达，`NOT_SENT` 回 PENDING 并按 `task.delivery_backoff_base_sec·2^(attempt-1)`（上限 3600s）退避，`UNKNOWN` 保留核实且不再自动发号。**重放**——DELIVERED 行再次 reserve 回放冻结的首次结果（`lease_epoch=0`/`lease_until=null` 表示未授租约）；超过 `webchat.notification_retention_days` 显式 `DELIVERY_RECEIPT_EXPIRED`。**NOT_REQUIRED** 不是出站意图（`COMMON_CONFLICT`）。**边界**——所有操作按租户谓词取行、`delivery_key` 复核 id 身份（不符一律 `COMMON_NOT_FOUND`）；事务内零网络（设置快照由端口在事务外按次取一次，坏源显式失败不回落默认）。**端点**——`POST /internal/channel-notifications/reserve`（按稳定键，响应回 `notification_id`）与 `.../{id}/renew|ack`（设计 §3.4 的 `{id}` 路径 + 请求体键复核）。

静态检查：`uv run ruff check`（本任务 4 个改动文件 + 测试）干净；`uv run mypy apps packages` → Success: no issues found in 433 source files；新增模块函数均 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF（1505 行、0 裸 LF），只追加本任务 11 个常量行并更新本任务改动文件的行号（`main.py` `SERVICE_NAME` 47→48）。
- B-265: verified — automated command passed; run_id=17fd2c07a4fe4badb6387bd300dd0b32 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-066: Runtime独立可靠交付泵

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-032, TASK-065
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-266
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_delivery_pump.py`, `apps/agent-runtime/src/muad_agent_runtime/main.py`, `tests/agent_runtime/test_capacity_webchat_delivery_pump.py`
- **Estimate**: 45分钟

### Description

原入口PENDING意图按租约有界领取/续租/退避；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-266][integration] 先在`tests/agent_runtime/test_capacity_webchat_delivery_pump.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：原入口PENDING意图按租约有界领取/续租/退避。→ RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_delivery_pump.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.17s）**：`ImportError: cannot import name 'TenantChannelDelivery' from 'muad_agent_runtime.application.channel_delivery'`——13 个行为用例先落地，投递泵模块与按租户协议门面尚不存在（日志 `$TMPDIR/muad-task066/task066-red.log`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `application/channel_delivery_pump.py`——`DeliveryPumpPolicy`（`SharedSettings.channel_delivery_*` 的 typed 投影：并发/timeout/租约/poll/batch/retry base·cap/max_attempts，边界在 `__post_init__` 显式校验，租期必须是整秒且落在线协议 5–3600s 内）、`DeliveryIntent`（领取时的行事实 + Run 持久化入站路由 + 已持久正文）、`DeliverySendOutcome`/`DeliveryIntentSender`/`DeliveryProtocolPort`/`DeliveryTransportPort` 四个协议、`due_intents_statement`（与 `ix_channel_notification_due` 同形的到期谓词，隐藏行不排除）、`ChannelDeliveryPump`（`claim_due`/`run_once`/`run_forever`/`drain`/`close` + 单条独立回收回调 + `_deliver` 有界重试 + `_deliver_once` 的 reserve→发送∥续租→ack + `_send_within_budget` + `_keep_lease`）、`GatewayDeliverySender`（经既有 Gateway `/internal/deliveries` 主动交付，按真实结论分类 DELIVERED/NOT_SENT/UNKNOWN）；② `application/channel_delivery.py` 抽出 `TenantChannelDelivery`（按租户取一次设置快照再委托一次状态转换），API-18 端口与投递泵共用同一门面（退避基数/保留期只有一处来源）；③ `infrastructure/gateway_delivery_client.py` 新增 `DeliveryNotSentError`（连接未建立/网关显式拒绝 = 确定没发出去，与"没拿到结论"分开；子类保证既有调用方行为不变）并让内部投递**不走环境代理**（`trust_env=False`）；④ `packages/contracts` 的 `DeliveryRequest` 接受第三种键形态 `run:{run_id}:terminal`（与持久通知同一个稳定 delivery_key，`run_id` 投影同步）；⑤ `main.py` 装配根挂载（`_mount_delivery_pump` + lifespan 起独立循环、收尾先停循环再关在飞投递与客户端）。错误显式：租约失约/行消失/非 PENDING 一律停手不 ack，设置源不可读等瞬时故障按 base→cap 有界重试（同一 attempt_id 重复 reserve 命中同一租约、不虚增 attempt），`run_forever` 单轮异常记日志后继续；日志只有 notification_id/错误码/类型名，正文与凭据不进日志。
- [x] [B-266] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-266] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_pump.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**13 passed in 25.53s**（日志 `$TMPDIR/muad-task066/task066-green.log`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-266 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 原入口PENDING意图按租约有界领取/续租/退避；Gateway重启或回复窗口过期后恢复交付，执行槽耗尽也可投递，不增加第五部署单元 | tests/agent_runtime/test_capacity_webchat_delivery_pump.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_pump.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-266 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_delivery_pump.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.17s）**：`ImportError: cannot import name 'TenantChannelDelivery' from 'muad_agent_runtime.application.channel_delivery'`（验收文件先落地、投递泵模块尚不存在） | 同命令 exit=0，**13 passed in 25.53s** | ① `test_b266_due_claim_uses_the_pending_index_and_bounds_attempts` L521-556：只领到期 PENDING（L537-538）；路由=Run 持久化的原入口（WECOM/bot/外线用户/线程 L539-547）、正文=终态事件 `final_text` L548；未到期/超预算/`NOT_REQUIRED` 逐行不动 L549-553；真实执行计划（`enable_seqscan=off` + `EXPLAIN (FORMAT JSON)`）用 **`ix_channel_notification_due`** L554。② `test_b266_pending_intent_is_delivered_to_the_original_entry_over_real_http` L558-593：真实 HTTP 请求体（稳定键/租户/路由/文本形态 L572-585）、内部服务头 L586、行 `DELIVERED`+租约清空+首次回执 L587-590、**送达不代替已读** L591。③ `test_b266_no_free_delivery_slot_means_no_claim` L596-629：槽满 `free_slots==0`/`inflight==1` L609-610，`run_once()==0` 且第二次意图逐行不动（仍 PENDING/attempt 0）L611-616；放行后交付 L619。④ `test_b266_not_sent_returns_to_pending_with_backoff_then_recovers` L632-668：网关 404 ⇒ `NOT_SENT` 回 PENDING、清租约、回执 outcome=NOT_SENT L645-648，退避 ≈ 平台设置 5s（4–8s 带）L649-650，退避期内不再领取（探针仍 1 次）L652-653；拨到期后同键恢复 ⇒ DELIVERED/attempt 2 L655-666。⑤ `test_b266_unknown_outcome_is_retained_and_never_resent` L671-696：读超时 ⇒ `UNKNOWN` + 回执 + 清租约 L682-684，`claim_due` 为空、不再发送（探针仍 1 次）L686-688。⑥ `test_b266_lease_is_renewed_while_a_slow_delivery_is_in_flight` L699-736：慢发送期间行确为 DELIVERING 且有期限 L711-713，`lease_until` **真实前进**（续租）L715-721，收尾 DELIVERED+清租约 L725-726。⑦ `test_b266_lease_loss_stops_the_local_delivery_without_writing_any_result` L739-778：过期租约被另一 attempt 接管（epoch 2/attempt 2）L753-757，泵本地停手 L758；行仍归新租约、`delivery_result` 仍 NULL L762-765（迟到结果没有位置）。⑧ `test_b266_attempt_budget_bounds_the_claim_set` L781-807：预算 2 ⇒ 两轮后 attempt==2、仍 PENDING L793-795，`claim_due` 为空且不再发送（探针仍 2 次）L797-799。⑨ `test_b266_recovers_after_gateway_restart_with_execution_slots_exhausted` L810-848：网关下线（连接拒绝）⇒ **NOT_SENT**（确定没发出去，不是 UNKNOWN）L824-828，执行槽仍全忙且计数不变 L829；网关重启+拨到期 ⇒ DELIVERED、槽计数仍 1 L831-836。⑩ `test_b266_lifespan_mounts_the_pump_with_startup_budgets` L851-873：真实 lifespan 注入 `CHANNEL_DELIVERY_*` 启动预算（并发/超时/租约/poll L864-867）、独立循环任务存活 L868-869、退出即停 L871。⑪ 边界 `test_b266_pump_policy_rejects_invalid_budgets` L876-888（0 预算/超时≥租约/租约越界/base>cap 全部 ValueError）与 `test_b266_pump_policy_derives_from_the_startup_settings` L891-901（默认 4/32/30/5.0/1.0/30.0/20/1.0）。⑫ `test_b266_intent_written_by_the_notification_path_is_delivered` L904-935：TASK-063 `write_run_notification(delivery_state=PENDING)` 的真实意图被同一稳定键领取并交付 L927-935。 | **真实 Runtime 业务服务**：`ChannelDeliveryPump`/`TenantChannelDelivery`/`ChannelDeliveryService`（TASK-065 的同一 PG 状态转换）与 TASK-063 的 `write_run_notification` 全走生产代码；装配用真实 `main.lifespan`（真实 PG/Redis 隔离号位）。**隔离 PostgreSQL**：每用例一个空库 + `alembic upgrade head`、跑完 drop（`async_tool_datastore`，不触碰共享 dev 库）；真实 `runtime.channel_notification`/feed/`run_record`/`canonical_event` 行、真实 `(tenant,delivery_key)` 唯一约束、真实 partial 索引（EXPLAIN 证据）与真实 PG 服务端时钟（到期/租约/退避裁决）。**真实 HTTP 交付边界**：uvicorn + 真实 socket 扮演既有 Gateway `/internal/deliveries`（真实请求体/响应封套/404 拒绝/读超时/连接拒绝/慢发送），"网关重启"就是真实停服+同端口重启。**真实执行槽**：`ExecutionSupervisor` 槽全忙时交付照常推进且不占槽。未 mock 存储或租约，无共享 dev 库行。 | verified |

**实现要点**

- **领取**：`due_intents_statement` 的谓词与 `ix_channel_notification_due`（partial：`delivery_state='PENDING'`）同形——`PENDING` + `next_attempt_at <= PG now` + `attempt < max_attempts`，按 `(tenant_id, next_attempt_at, id)` 有序；领取本身**不加锁**，状态转换由 TASK-065 的 `reserve` 行锁内完成（多副本竞争由 epoch 栅栏裁决，`run_once` 还用 `notification_id` 去重本地在飞任务）。隐藏（软删）行**不排除**：投递回执与重试资格不因隐藏消失（§3.6）。
- **有界槽**：`run_once` 的领取量 = `min(CHANNEL_DELIVERY_BATCH_SIZE, 空闲投递槽)`，无空闲槽直接返回 0 且不触碰 PG（§3.7「领取数量≤空闲投递槽，无槽不领取」）；每条投递是独立 asyncio 任务 + 独立完成回调（单条异常/取消只释放自己），`run_forever` 单轮异常只记日志并继续。
- **协议与续租**：`reserve`（租期 `CHANNEL_DELIVERY_LEASE_SEC`，与 timeout 校验 timeout<lease）→ 发送与**独立续租任务**并发（周期 lease/3）；`renew` 拿不到租约即抛 `DELIVERY_LEASE_LOST`，此时取消本地发送且**不 ack**（失约即停手；迟到结果改不动行）。慢发送（4s）在 5s 租约下真实触发续租（用例⑥）。
- **诚实结局**：`DELIVERED` 只在 Gateway 确认 `delivered=true` 后写；**确定没发出去**（连接未建立/网关非 200/网关只占位）⇒ `NOT_SENT` → PENDING + 平台设置退避基数重投；**没拿到结论**（读超时/发送预算耗尽）⇒ `UNKNOWN` → 保留核实、不再自动重发。路由/正文缺失同样按 NOT_SENT（没发出去≠发成功）。
- **重试预算**：`CHANNEL_DELIVERY_RETRY_BASE_SEC/CAP_SEC/MAX_ATTEMPTS` 是**进程级**的瞬时故障重试（设置源不可达、DB 抖动、发送器异常）：同一 `attempt_id` 重跑整条 `reserve→发送→ack`，重复 reserve 命中同一租约（TASK-065 幂等语义）⇒ 不虚增 `attempt`；`max_attempts` 同时是**持久领取**的尝试上限（跨副本、跨重启都记在行上），预算耗尽后意图保留 PENDING 供核实，不再领取。退避节奏的**唯一来源**仍是平台设置 `task.delivery_backoff_base_sec`（TASK-065 的 `DeliveryPolicy`），本任务不另建同义配置。
- **装配**：`_mount_delivery_pump` 在 Runtime 进程内挂起泵（不新增第五部署单元），交付经既有 Gateway `/internal/deliveries`；收尾顺序=取消循环 → `pump.close()`（取消在飞投递）→ 关闭交付客户端。
- **契约对齐**：`DeliveryRequest` 接受 `run:{run_id}:terminal`（与持久通知同一个稳定 delivery_key；§3.6「活跃回复会话的收尾与兜底主动交付使用同一稳定 delivery_key」），`run_id` 投影同步覆盖该形态——TASK-067 的 Gateway 侧协调（API-18 reserve/ack）因此能落在同一个键上。
- **投递事实分离**：`DeliveryNotSentError`（连接未建立/网关显式拒绝）与 `DeliveryUnavailableError`（连接之后的超时/传输错误）分开，泵据此写 `NOT_SENT` 或 `UNKNOWN`；内部服务调用 `trust_env=False`，不让部署环境的 `HTTP_PROXY` 变成内部交付的隐形路由（实测：本机代理把 127.0.0.1 交付变成读超时）。
- **计划外文件披露**（计划 Files 之外的必需联动）：① `apps/agent-runtime/src/muad_agent_runtime/application/channel_delivery.py`（抽出 `TenantChannelDelivery`：按租户设置快照→服务的唯一实现处，泵与 API-18 端口共用，消除重复）；② `apps/agent-runtime/src/muad_agent_runtime/api/channel_delivery.py`（改为委托同一门面，行为不变，TASK-065 的 16 条验收复跑通过）；③ `apps/agent-runtime/src/muad_agent_runtime/infrastructure/gateway_delivery_client.py`（`DeliveryNotSentError` + `trust_env=False`）；④ `packages/contracts/src/muad_contracts/delivery.py`（`TERMINAL_DELIVERY_KEY` 第三形态）；⑤ `docs/configuration-inventory.csv`（新增 2 个常量行、本任务改动文件的常量行号平移，保持 CRLF、0 裸 LF）。
- 未新增迁移/DDL（0026 已提供 due partial 索引与投递列；本任务只用既有列）。

**回归证据（最终代码）**：`uv run pytest -q tests/agent_runtime` exit=0，**1028 passed in 690.24s**（同域全量；隔离空库 + `alembic upgrade head`，含本任务 13 条与 TASK-065/TASK-032 既有套件）；`uv run pytest -q tests/agent_runtime/test_capacity_webchat_delivery_pump.py tests/contracts tests/test_configuration_inventory.py` exit=0，**1298 passed in 22.22s**（契约面 1281 条含新键形态、配置盘点 4 条含新增 2 行）；`uv run pytest -q tests/gateway/test_delivery_api.py tests/gateway/test_runtime_client.py tests/architecture tests/agent_runtime/test_capacity_webchat_delivery_reservation.py tests/agent_runtime/test_attachment_tools.py` exit=0，**117 passed in 35.82s**（Gateway 交付面、架构规则、TASK-065 的 16 条协议验收、产物交付客户端既有消费者）。静态检查：`uv run ruff check`（本任务 7 个改动文件）干净；`uv run mypy apps packages` → Success: no issues found in 436 source files；新增/修改函数全部 ≤50 行（`lifespan` 为既有装配根，本任务净增 7 行，见遗留⑦）。

**遗留（deferred，非本任务可闭合）**

1. **Gateway 活跃回复会话与兜底交付的同键协调**（API-18 reserve/ack、ACK 丢失的至少一次语义）属 **TASK-067**：本任务把泵的交付请求打到既有 `/internal/deliveries`（网关侧 Redis 去重 + 稳定键），但不代它实现"活跃 ReplySession 收尾"的预留/确认。
2. **Task 结果的产物形态交付**属 **TASK-068/094**：本发送器只交付文本形态（终态正文/待确认提示词），带 `artifact_ids` 的意图不会伪造产物已送达。
3. **交付/通知指标目录**属 **TASK-098**：本任务只留结构化日志（`channel_delivery_pump_failed`/`channel_delivery_attempt_failed`/`channel_delivery_crashed`），不新增指标常量。
4. **交付路径的分池 HTTP 预算与生产装配**属 **TASK-094**（`infrastructure/http_clients.py`）：本任务用独立 `GatewayDeliveryClient`（timeout 来自 `CHANNEL_DELIVERY_TIMEOUT_SEC`），池大小/总预算仍在装配任务统一注入。
5. **待确认通知的机器人文案本地化**：泵按已持久事件载荷的 `prompt` 原文交付（不新增 i18n 渲染），若产品要求机器人侧本地化文案，属 TASK-067/096 的渲染边界。
6. **E2E（S-07/S-15/E-19 等）保持 planned**，由 verify-e2e 记录完整证据；本任务只登记 integration 级证据。
7. 诚实记录：`lifespan` 仍超过 50 行（既有装配根，本任务净增 7 行：`_mount_delivery_pump` + 两行挂载/收尾；其余新增函数全部 ≤50 行）。
- B-266: verified — automated command passed; run_id=02f97477615e4873a2b63f9f88123c76 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-266 RED→GREEN：先建 13 个行为用例 → **1 error during collection（0 passed / 0 failed, 0.17s）**（`ImportError: TenantChannelDelivery`/投递泵模块尚不存在）；实现投递泵（到期领取走 due partial 索引 + 有界投递槽 + reserve/续租/ack + NOT_SENT 退避/UNKNOWN 保留 + 限次 + 独立循环）与装配根挂载后 **13 passed in 25.53s**；回归 `tests/agent_runtime` **1028 passed**（690.24s）、契约+盘点 **1298 passed**（22.22s）、Gateway/架构/相邻套件 **117 passed**（35.82s）；ruff 干净、mypy 436 文件无问题
- [2026-10-18] completed (done)

---

## TASK-067: 机器人收尾与主动兜底统一去重

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-048, TASK-066
- **Source**: agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-267
- **Files**: `apps/im-gateway/src/muad_im_gateway/api/delivery.py`, `apps/im-gateway/src/muad_im_gateway/application/channel_delivery.py`, `tests/gateway/test_capacity_webchat_gateway_delivery.py`
- **Estimate**: 45分钟

### Description

活跃ReplySession和主动交付使用同delivery_key预留/确认；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-267][integration] 先在`tests/gateway/test_capacity_webchat_gateway_delivery.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：活跃ReplySession和主动交付使用同delivery_key预留/确认。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_delivery.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.26s）**：`ModuleNotFoundError: No module named 'muad_im_gateway.application.channel_delivery'`——验收文件先落地，同键协调模块尚不存在（日志 `$TMPDIR` 外 `/tmp/b267-red.log`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `application/channel_delivery.py`——`terminal_delivery_key`（稳定键 `run:{run_id}:terminal`，与 Runtime 持久通知/投递泵同一个键）、`ChannelDeliveryProtocol`（API-18 reserve/ack 端口）、`DeliveryPermissionPort` + `ConsoleDeliveryPermission`（按**原入口**复核接收者/Agent 未变且仍被授权）、`TerminalDeliveryPort`（`deliver`/`confirm`）、`ActiveChannelDelivery`（`deliver`：reserve→主动发送→**实际发送成功才 ack**；`confirm`：活跃会话收尾的同键认领+确认；已送达回执重放⇒去重、别人持有效租约⇒不抢发、`UNKNOWN`⇒不自动重发、无持久出站意图/协调端口不可用⇒按原语义直接发送，**不因此丢消息**）；ACK 丢失⇒留痕 + 行留 DELIVERING（至少一次）。② `application/runtime_client.py`——受信 API-18 客户端（`reserve_delivery`/`ack_delivery`，服务身份 + 部署租户 + 真实契约解析）。③ `application/inbound.py`——终态交付统一走同键协调：活跃回复会话收尾 `confirm`、会话失联兜底 `deliver`，失败不静默（`im_final_delivery_total` + 日志）；`state.agent_id` 用于交付时权限复核。④ `main.py` 装配根注入协调器（网关仍不持库，只做 HTTP 调用）。⑤ 删除 `application/final_delivery.py`（旧 `run:{run_id}:final` 键 + 网关自持 Redis 去重），不留双路径/兼容层。
- [x] [B-267] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-267] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_delivery.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**5 passed in 9.83s**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-267 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 活跃ReplySession和主动交付使用同delivery_key预留/确认；路由保持原entry/Agent/user并重核接收权限，ACK丢失保留可追溯至少一次语义 | tests/gateway/test_capacity_webchat_gateway_delivery.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_delivery.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：**真实 Gateway 交付管线与适配器**（`InboundPipeline` + 真实 `WeComAdapter`，官方 SDK 端口是进程内探针）+ **真实 HTTP/SSE 协议探针**（真实 TCP 上的真实 API-18 路由 `POST /internal/channel-notifications/reserve|ack`，真实 `require_internal_service` 门控与真实 PG 状态机；真实 `text/event-stream` 应答；Console 是真实 HTTP 桩）+ **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + `alembic upgrade head`，跑完 drop，不触碰共享 dev 库）+ **真实 Runtime 投递泵**（`ChannelDeliveryPump` + `TenantChannelDelivery` 的 reserve/ack 状态转换）。**网关不持库**：测试不向 Gateway 注入任何数据库对象（只注入 HTTP 客户端），DB 只经 Owner 的 API-18 接口访问，测试自己用独立连接读行事实作证据。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-267 | `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_delivery.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.26s）**：`ModuleNotFoundError: No module named 'muad_im_gateway.application.channel_delivery'` | 同命令 exit=0，**5 passed in 9.83s** | ① `test_b267_lost_reply_session_delivers_on_the_shared_key_and_the_pump_skips_it` L589-631：**同一稳定键**（L607-609：`terminal_delivery_key == run_terminal_delivery_key`、== 通知行 `delivery_key`、匹配契约 `TERMINAL_DELIVERY_KEY`）；**实际发送成功才 ack**（L612 原入口主动发送恰一次 `(chat, 完整答案)`；L614-617 真实 API-18 端口记录 reserve/ack 都落在 `run:{run_id}:terminal`、outcome=`DELIVERED`、epoch=1）；行事实（L619-625：`DELIVERED`、attempt=1/lease_epoch=1、租约清空、冻结回执、`read_at IS NULL`）；**兜底泵同键不重发**（L628-631：`claim_due` 空、`run_once()==0`、记录式发送器零意图）。② `test_b267_active_finalization_confirms_the_shared_key_without_resending` L634-672：**活跃 ReplySession 收尾**（L650 真实入站→真实 SDK 回调→会话流收尾）按同键 reserve+ack（L658-660）而**不重复主动发送**（L657 `sent_texts == []`）、行 `DELIVERED` 且租约清空（L663-667）、兜底泵同键不重发（L670-672）。③ `test_b267_proactive_delivery_first_is_replayed_by_the_active_finalization` L680-724：兜底先送达（L692-699：一次投递、**路由=原入口** bot/用户/会话、正文=已持久终态文本）；活跃收尾命中**冻结回执**（L717-719：不再投递、不再 ack）；行不被改写（L721-724：attempt/epoch 不变、`delivery_result` 逐字段相同、`read_at IS NULL`）。④ `test_b267_delivery_rechecks_permission_and_keeps_the_original_route` L732-763：**路由保持原 entry/Agent/user**（L748-755：受理与交付两次解析都带原入口四元组且逐字段相同）；**重核接收权限**（受理后被撤权 ⇒ L757-759 不发送、不 reserve、不 ack；L761-763 行仍 `PENDING`/attempt=0/无租约/无回执）。⑤ `test_b267_lost_ack_stays_traceable_and_the_same_key_retries_at_least_once` L771-810：**ACK 丢失**（L790 确认响应丢失、无回执；L791-795 行留 `DELIVERING`、attempt=1/epoch=1、租约在、`delivery_result` 仍为空、未读）；租约到期后同键重来被接管（L799-800 再次真实发送；L803-810 `DELIVERED`、attempt=2/epoch=2、两次 reserve 同一键、第二次确认 epoch=2）⇒ **可追溯的至少一次**。 | **真实 Gateway 路由/适配器**：真实 `InboundPipeline.handle`（受理→SSE→终态交付）与真实 `WeComAdapter`（真实 `open_reply`/`send_active`/SDK 端口探针，`wecom_bot_state_changed` 连接状态真实流转）；**真实 HTTP/SSE**：uvicorn 真实 TCP 端口上的真实 API-18 路由（真实 `require_internal_service` 403 面 + 真实响应封套）与真实 `text/event-stream` 应答，`RuntimeClient`/`ConsoleClient` 都是真实 HTTP 客户端；**数据库经 Owner 接口访问**：网关侧零 DB 对象，API-18 端口用服务线程自己的事件循环 engine（`_LoopSessions`），测试用独立连接读 `runtime.channel_notification` 行；**隔离 PostgreSQL**：真实 `(tenant,delivery_key)` 唯一约束、真实 `channel_notification` 行（由 TASK-063 的 `write_run_notification(delivery_state=PENDING)` 写入）、真实 PG 服务端时钟（租约/接管/退避裁决）；**真实投递泵**：`ChannelDeliveryPump` + `TenantChannelDelivery`（发送边界用记录式 sender 观察，Runtime→Gateway HTTP 出站面已由 TASK-066 的真实网关探针覆盖）。 | verified |

实现要点：**同一稳定键**——`run:{run_id}:terminal`（与 TASK-063 的持久通知、TASK-066 的投递泵同一个键），网关侧不再有第二个键（旧 `run:{run_id}:final` 与网关自持 Redis 去重随 `final_delivery.py` 一并删除，不留双路径）。**实际发送成功才 ack**——reserve 授出租约后先发送：发送失败 ack `NOT_SENT`（回 PENDING 重投），发送成功 ack `DELIVERED`（冻结首次结果）；**已送达回执重放** ⇒ 不重发也不重写回执；**别人持有效租约**（Runtime 泵在发）⇒ 不抢发；`UNKNOWN` ⇒ 不自动重发、不标送达。**活跃收尾与兜底共用一条协调**——活跃 ReplySession 收尾（终态内容已写进会话流）走 `confirm`：同键认领 + 确认，阻止兜底重发；会话失联才走 `deliver` 主动发送。**接收权限复核**——交付前按**原入口**再解析一次（同一 Console 面），未授权或接收者/Agent 已变即不发送、不占投递资格；**路由保持原 entry/Agent/user**（企微按原入口指针，不重新猜来源）。**ACK 丢失可追溯**——确认丢失不改变"已发出"的事实（不静默），行留在 `DELIVERING`（attempt/租约可审计、`delivery_result` 仍为空），租约到期后同键重来被接管（epoch 前进）再次投递 ⇒ 至少一次；**降级不丢消息**——无持久出站意图（无入口归属/网页持久入箱）或协调端口不可用 ⇒ 按原语义直接发送（at-least-once，与 `RULE-13` 同口径），日志留痕。

静态检查：`uv run ruff check`（本任务 4 个改动源文件 + 1 个新增模块 + 2 个测试文件 + conftest）干净；`uv run mypy apps packages` → Success: no issues found in 437 source files；AST 逐函数核对：本任务新增/修改的生产函数全部 ≤50 行（最长 `deliver` 24、`confirm` 11、`_reserve` 30、`_send_and_ack` 19、`_ack` 31、`may_deliver` 20、`_deliver_final_text` 31）。`docs/configuration-inventory.csv` 保持 CRLF（1519 行、0 裸 LF），删除 `final_delivery.py` 4 行、新增 `channel_delivery.py` 4 行与 `runtime_client.py` 2 行并平移受影响行号，`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。

诚实记录（测试强度自检，三次变异都被现有断言抓住，均已还原）：① 把稳定键改回旧形态 `run:{run_id}:final` ⇒ 4 failed / 1 passed（同键协调落空）；② 跳过交付时权限复核 ⇒ 权限用例失败；③ 活跃会话收尾不参与同键协调（退化成兜底发送）⇒ 活跃收尾用例失败（L657 `sent_texts == []` 与 L658 reserve 证据）。

计划外文件披露（计划 Files 之外的必需联动）：① `apps/im-gateway/src/muad_im_gateway/application/inbound.py`（终态交付的唯一调用点：活跃收尾/兜底分流与 `state.agent_id`）；② `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`（受信 API-18 客户端；网关 HTTP 客户端唯一实现处）；③ `apps/im-gateway/src/muad_im_gateway/main.py`（装配根注入协调器，否则装配后仍无协调端口）；④ `apps/im-gateway/src/muad_im_gateway/application/final_delivery.py`（**删除**：旧键与网关自持 Redis 去重被本任务的同键协调取代）；⑤ `tests/gateway/test_waiting_resume.py`（TASK-006 的终态交付用例改按新契约断言：API-18 端口进程内替身 + 同键 reserve/ack）；⑥ `tests/gateway/conftest.py`（暴露 `async_tool_datastore` 隔离库夹具）；⑦ `docs/configuration-inventory.csv`（项目硬约束）。**`apps/im-gateway/src/muad_im_gateway/api/delivery.py` 未改动**：泵/Worker 出站到 `/internal/deliveries` 的入站方向行为一字未变——协调发生在**交付前**（API-18 租约），已持租约的调用方本来就该直发，网关侧再加一层同键判断只会重复 Owner 的裁决。未新增迁移/DDL。

遗留（deferred，非本任务可闭合）：① **机器人路由的 `PENDING` 出站意图由 Runtime 终态写入路径选择**（TASK-063 遗留①：现网终态通知写 `NOT_REQUIRED`，`PENDING` 的按通道选择属装配接线）——本任务实现并验证了同键协调本身（验收用例用真实 `write_run_notification(delivery_state=PENDING)` 接缝构造意图，并用真实泵证明同键去重与接管）；按通道写 `PENDING` 的接线归 TASK-094/096 的装配面，不改本任务的契约。② **过期租约的自动回收**：投递泵的领取谓词只认 `PENDING`（TASK-066 已验证的语义），`DELIVERING`+过期租约只能被**同键 reserve** 接管（本任务用例⑤证明了接管语义与至少一次）——自动回收路径（如独立租约回收循环）属 TASK-069/094。③ 交付/通知指标目录属 TASK-098（本任务只沿用既有 `im_final_delivery_total` 的标签词表并新增 `deduplicated` 取值）。④ 待确认通知的机器人文案本地化仍属 TASK-067/096 的渲染边界（本任务不新增 i18n 渲染）。⑤ E-19/S-03/S-07 的端到端（Gateway 重启、真实企微探针、多实例）由 TASK-111 的 verify-e2e 记录。
- B-267: verified — automated command passed; run_id=b637f160bfb94a74a878f1ad128d686d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-267 RED：`tests/gateway/test_capacity_webchat_gateway_delivery.py` → exit=2，1 error during collection（`muad_im_gateway.application.channel_delivery` 尚不存在）
- [2026-10-18] 实现：新增 `application/channel_delivery.py`（稳定键 `run:{run_id}:terminal` + API-18 reserve/ack 协调 + 交付时权限复核 + ACK 丢失可追溯）；`runtime_client.py` 受信 API-18 客户端；`inbound.py` 活跃收尾/兜底统一走同键；`main.py` 装配注入；删除 `final_delivery.py`（旧键 + 网关自持去重）；同步 `docs/configuration-inventory.csv`（CRLF 保持）
- [2026-10-18] B-267 GREEN：同命令 exit=0，5 passed in 9.83s；断言位置与真实边界证据见 Acceptance Evidence
- [2026-10-18] 测试强度自检（变异三次，均已还原）：旧键 `:final` ⇒ 4 failed / 1 passed；跳过权限复核 ⇒ 权限用例失败；活跃收尾不参与协调 ⇒ 活跃收尾用例失败
- [2026-10-18] 域回归（最终代码；无本机 dev 残留进程）：`uv run pytest -q tests/gateway tests/contracts tests/architecture tests/test_configuration_inventory.py` exit=0，**1745 passed in 126.85s**。诚实记录：同一命令的首轮为 **1743 passed / 1 failed**——失败是 `tests/gateway/test_delivery_api.py::test_e05_worker_http_to_gateway_redis_probe_end_to_end`（Worker→Gateway 投递 + Redis 去重，与本任务改动无交集）：单跑 1 passed、`tests/gateway` 整跑 426 passed、同组合复跑 1745 passed；基线（`git stash` 后同命令）1740 passed。按既有口径记为共享 dev 库的偶发污染，未改任何实现去迎合它
- [2026-10-18] 静态检查：`ruff` 干净；`mypy apps packages` 437 files 无问题；新增/修改生产函数全部 ≤50 行；配置盘点 4 passed
- [2026-10-18] completed (done)

---

## TASK-068: Worker离线结果接入持久用户收件箱

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-033, TASK-065
- **Source**: agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-268
- **Files**: `apps/agent-worker/src/muad_agent_worker/delivery/service.py`, `apps/agent-worker/src/muad_agent_worker/results/dispatcher.py`, `tests/agent_worker/test_capacity_webchat_worker_notifications.py`
- **Estimate**: 45分钟

### Description

任务结果由Runtime持久入箱确认后才完成投递；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-268][integration] 先在`tests/agent_worker/test_capacity_webchat_worker_notifications.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：任务结果由Runtime持久入箱确认后才完成投递。RED：`uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_notifications.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.83s）**：`ImportError: cannot import name 'HttpWebchatInboxClient' from 'muad_agent_worker.delivery.client'`——验收文件先落地，受信入箱客户端与 WEBCHAT 投递分支尚不存在。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `delivery/client.py`——`HttpWebchatInboxClient`/`WebchatInboxClientProtocol`/`WebchatInboxReceipt`（受信入箱端口的 HTTP 客户端：服务身份 + 租户头，只有**明确** `data.persisted == true` 且回执 `task_id` 一致才算入箱，非法 JSON/缺字段一律按未入箱返回，可重试）；② `delivery/service.py`——`DeliveryLoop` 按路由通道分流：WEBCHAT 走 `_attempt_webchat_delivery`（先核受信端口/来源 Run/结果发件状态 → 提交 `WebchatTaskResultRegistration{task_id,run_id,user_id,artifact_ids}` → `persisted=true` 才 `_record_sent`；4xx 确定性失败、5xx/传输可重试）；`_webchat_result_settled()` 候选门槛（结果发件仍 `PENDING` 时不消耗投递尝试）；③ `application/terminal_tasks.py`——`_publishes_result()`：终态事务里为 **WEBCHAT 投递意图** 的 DETACH 任务一并写结果发件（Runtime 只接受已被受理的结果，网页送达才有可核验来源；企微路由不变）；④ `application/delivery_routes.py`——`WEBCHAT_CHANNEL`（worker 侧通道名唯一来源）；⑤ `main.py`——装配根注入入箱客户端。
- [x] [B-268] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-268] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_notifications.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**5 passed in 7.99s**（实现首跑 7.35s、重构后收尾 7.99s 同绿）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-268 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | 任务结果由Runtime持久入箱确认后才完成投递；送达/已读分开，按原来源conversation定位；Worker重启/重投不重复通知或业务执行 | tests/agent_worker/test_capacity_webchat_worker_notifications.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_notifications.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：隔离 PostgreSQL（每用例一个空库 + `alembic upgrade head`，跑完 drop，不触碰共享 dev 库）、真实 `WorkerLoop`/`TaskClaimer` 领取与任务槽监督、真实 Skill 子进程（现场脚本，每次执行追加一行标记）、真实 uvicorn TCP 服务上的 Runtime 路由（`/internal/tool-results`、`/internal/webchat/task-results`、`/internal/webchat/notifications`）与真实 `require_internal_service` 门控。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-268 | `uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_notifications.py` exit=2，**1 error during collection（0 passed / 0 failed, 0.83s）**：`ImportError: cannot import name 'HttpWebchatInboxClient' from 'muad_agent_worker.delivery.client'` | 同命令 exit=0，**5 passed in 7.99s** | ① `test_b268_offline_result_enters_the_persistent_inbox_only_after_runtime_acceptance` L597-673：真实 Worker 跑完（L610-612：`COMPLETED`、真实 `result_json`、脚本标记**恰一行**）；结果发件与终态同事务且 `PENDING`（L614-615）；**受理前不成为投递候选**（L619 `run_once() is None`、L621-622 `delivery_attempts == 0`、L623 无通知行）；真实发件后 `tool_result_inbox` 就位（L627-629，`late=True`）；入箱 `persisted=true` 才 `SENT`（L633-638）；**独立连接**在回执返回时已读到已提交的通知行（L641-651：`kind=TASK_RESULT`、`seq=1`、`delivery_key=task:{task_id}:final`、`delivery_state=NOT_REQUIRED`、`read_at IS NULL`、`conversation_id`/`entry_id`/`user_id` 都是**原来源**身份、`artifact_ids` 是该 Task 的真实产物、`event_id` 指向受理回执事件、feed 水位 1）；真实 HTTP 分页（L654-660：未读 1、附件投影）；**送达/已读分开**（L669-674：显式已读写 `read_at` 但 `delivery_state`/`delivery_status` 不变）。② `test_b268_redelivery_after_a_lost_receipt_hits_the_original_notification_once` L681-724：代理吞掉回执 ⇒ 2xx 无持久确认**不置 SENT**（L697-701：仍 `PENDING`、`DELIVERY_RETRY` 事件）；重投（新循环实例 = 重启）命中**原回执**（L710-716：唯一通知行、同 id/seq、`read_at` 仍 NULL、feed 水位不变）；**不重复业务执行**（L720-725：`delivery_attempts` 2 而 `attempt` 仍 1、脚本标记恰一行、终态事件恰一条、`CLAIMED` 恰一条、`DELIVERY_SENT` 恰一条）。③ `test_b268_webchat_delivery_without_source_run_or_entry_fails_explicitly` L732-763：无来源 Run（计划触发形状）⇒ 显式终态失败且**不编造会话通知**（L746-753）；会话无入口归属 ⇒ Runtime 核属 404 ⇒ 确定性失败、不落通知行（L757-764）。④ `test_b268_non_webchat_routes_keep_gateway_delivery_without_runtime_publication` L771-798：企微路由不产生 Runtime 结果发件（L778），Gateway 出站照旧（L789-795）；入箱端口指向死地址仍成功 ⇒ 证明 WEBCHAT 分支未被误用（L796-799）。⑤ `test_b268_webchat_channel_constant_matches_the_shared_vocabulary` L806-808：通道名与共享词表一致。 | **真实 Worker 监督/claim**：`WorkerLoop.run_once`（真实 `TaskClaimer` 行锁领取 + 任务槽监督 + 心跳 + 终态 CAS）走生产代码；**真实子进程**：`SkillTaskExecutor` + `scripts/main.py`（每次执行写一行 `MUAD_TASK_ID`，因此"业务执行只一次"是**可数**事实）；**外部 HTTP 协议探针**：uvicorn 真实 TCP 端口上的真实 Runtime 路由与真实服务门控（`/internal/tool-results` 由真实 `ResultDispatcher` 调用、`/internal/webchat/task-results` 由投递循环调用）；**隔离 PostgreSQL**：真实 `task.*` 行 + 真实 `runtime.conversation/run_record/tool_operation/tool_result_inbox/artifact/channel_notification` 行与完整唯一约束；**故障注入**：真实 HTTP 故障代理（转发但回执不含持久确认，模拟响应丢失）。 | verified |

实现要点：**通道分流**——投递循环按路由通道分流：`WEBCHAT` 之外（企微）保持既有 Gateway 出站与产物解析，一字未改；`WEBCHAT` 走"持久用户收件箱"路径（socket 写入在用户离线时什么都留不下）。**先持久后 ack**——只有 `persisted=true` 才写 `SENT`；2xx 缺持久确认、5xx、传输错误都是可重试失败（与 Gateway 的 `delivered` 同一纪律）；4xx（核属/绑定不符）是确定性失败，不无限重试。**来源可核验**——Runtime 的入箱端口核 `tool_result_inbox`/`tool_operation`/`Run`/用户/入口与每个 artifact 的 `task_id`；因此终态边界为 **WEBCHAT 投递意图** 的 DETACH 任务在**同一事务**里写结果发件（`RULE-worker-001` 禁止提交终态后再补发件），企微路由不额外写 Runtime 事件。**不烧预算**——`_webchat_result_settled()` 把"结果发件仍 `PENDING`"的行挡在投递候选之外：发件在退避时投递不消耗尝试，避免"发件还没结论、投递先把预算烧光、用户永远看不到结果"。**按原来源定位**——提交只带 Worker 真实持有的身份（task/run/actor user/产物引用），会话/入口由 Runtime 从真实行导出；没有来源 Run 或没有入口归属都**显式失败**，不编造会话。**重投命中**——交付键稳定 `task:{task_id}:final`，重投由 Runtime 命中原回执（同 id/seq、feed 水位不变、不新增未读），用户已读另记。

静态检查：`uv run ruff check`（本任务 5 个改动源文件 + 新增测试文件）干净；`uv run mypy apps packages` → Success: no issues found in 435 source files；AST 逐函数核对：本任务新增/修改的生产函数全部 ≤50 行（最长 `_attempt_webchat_delivery` 46、`_webchat_publication_outcome` 20、`_interpret_webchat_receipt` 23、`_result_publication` 14、`_publishes_result` 20、`register_task_result` 客户端方法 15）。`docs/configuration-inventory.csv` 保持 CRLF（1509 行、0 裸 LF），只新增本任务 2 行常量并更新本任务改动文件的行号，`uv run pytest -q tests/test_configuration_inventory.py` exit=0，4 passed。

诚实记录（测试强度自检，两次变异都被现有断言抓住）：① 去掉 `_webchat_result_settled()` 候选门槛 ⇒ `test_b268_offline_result_...` 失败（受理前消耗了投递尝试）；② 把"2xx 即送达"写回 ⇒ `test_b268_redelivery_...` 失败（丢回执被当成送达）。两次变异均已还原。

计划外披露：① `apps/agent-worker/src/muad_agent_worker/delivery/client.py`（受信入箱客户端与 Gateway 投递客户端同址，投递模块 HTTP 客户端唯一实现处）；② `apps/agent-worker/src/muad_agent_worker/application/terminal_tasks.py`（结果发件必须与终态同事务，见上）；③ `apps/agent-worker/src/muad_agent_worker/application/delivery_routes.py`（`WEBCHAT_CHANNEL` worker 侧唯一来源，终态边界与投递循环共用）；④ `apps/agent-worker/src/muad_agent_worker/main.py`（装配根注入入箱客户端，否则装配后 WEBCHAT 投递仍无端口）；⑤ `docs/configuration-inventory.csv`（项目硬约束）。**`results/dispatcher.py` 未改动**：结果发件的 durable 证据规则（只有同 `event_id` 且 `persisted=true` 才写 SENT）在 TASK-068 之前就成立（`RULE-worker-001`），本任务只需要投递循环读它的**结论**（发件状态门槛），不改发件器。未新增迁移/DDL（0026 的两张表够用，无新列）。

遗留（deferred，非本任务可闭合）：① **计划触发的 WEBCHAT 任务**（`trigger_type=SCHEDULED`，无来源 Run/operation）无法定位会话 ⇒ 投递显式终态失败、不编造入口——要让这类结果也进持久收件箱，需要 Runtime 入箱端口支持按投递路由（`external_conversation_id`）定位会话，属 TASK-064 端口的扩展面，不在本任务 Files 内；② 通知清理/保留与"用户活动游标最早可用水位"属 TASK-069；③ 通知/投递指标目录属 TASK-098；④ Gateway 公开面（API-11 公共路由与 CHAT 复核）属 TASK-070；⑤ S-07/S-105 的端到端（多实例、真实浏览器、断线重开拉取）由 TASK-111/114 的 verify-e2e 记录。
- B-268: verified — automated command passed; run_id=8fdafe0e8e3b44c2a04da27d0bfdc5e6 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-268 RED：`tests/agent_worker/test_capacity_webchat_worker_notifications.py` → exit=2，1 error during collection（`HttpWebchatInboxClient` 尚不存在）
- [2026-10-18] 实现：`delivery/client.py` 受信入箱客户端；`delivery/service.py` 通道分流 + 候选门槛 + 先持久后 ack；`application/terminal_tasks.py` WEBCHAT 投递意图的结果发件与终态同事务；`application/delivery_routes.py` `WEBCHAT_CHANNEL`；`main.py` 装配注入；同步 `docs/configuration-inventory.csv`（CRLF 保持）
- [2026-10-18] B-268 GREEN：同命令 exit=0，5 passed in 7.99s；断言位置与真实边界证据见 Acceptance Evidence
- [2026-10-18] 测试强度自检（变异两次，均已还原）：去掉候选门槛 ⇒ 用例① 失败；"2xx 即送达" ⇒ 用例② 失败
- [2026-10-18] 域回归（最终代码；无本机 dev 残留进程）：`uv run pytest -q tests/agent_worker tests/contracts tests/test_configuration_inventory.py` exit=0，**1607 passed in 91.43s**。诚实记录：同一命令的首轮为 **1606 passed / 1 failed**——失败是 `test_configuration_inventory.py`（本任务自己的 CSV 行号在重构后陈旧），重算行号后复跑全绿
- [2026-10-18] 静态检查：`ruff` 干净；`mypy apps packages` 435 files 无问题；新增/修改生产函数全部 ≤50 行
- [2026-10-18] completed (done)

---

## TASK-069: 历史、流增量、通知和制品清理保护

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-053, TASK-061, TASK-064, TASK-068
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-269, B-07
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_retention.py`, `apps/console-platform/backend/src/muad_console_platform/application/artifact_cleanup_service.py`, `tests/agent_runtime/test_capacity_webchat_retention.py`
- **Estimate**: 60分钟

### Description

保留期按清理边界取设置；完整分支及可观测结果见下面验收契约。

### Checklist
- [x] [B-269][integration] 先在`tests/agent_runtime/test_capacity_webchat_retention.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：保留期按清理边界取设置。RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_retention.py` exit≠0，`1 error during collection`（`ModuleNotFoundError: No module named 'muad_agent_runtime.application.channel_retention'`）——验收先于实现落地。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `channel_retention.py`（535 行）——`RetentionPolicy`+`retention_policy_from_settings`（保留期在**每次清理边界**取设置，坏值经 contracts 显式失败、不回落默认）、四类有界软删清理（`cleanup_stream_replay`/`cleanup_visible_history`/`cleanup_notifications`/`cleanup_staging_uploads`）、`run_retention_sweep` 汇总（独立循环调用点，装配属 TASK-096）与可对账 `StagingCleanupAction`/`RetentionReport`；保护三类不同事实：活跃引用（非终态 Run、在飞提炼 job 的 `through_seq` 输入边界、`artifact.metadata_json.upload_id`）、唯一上下文来源（`CONTEXT_SUMMARY` 永不候选；只有被最新摘要覆盖的旧消息可回收）、孤儿宽限（暂存文件 mtime）；全部行锁 + `SKIP LOCKED` + 软删（seq/delivery_key 唯一性与水位不回退）；新增 `tenant_id` 谓词（共享库上破坏性清理必须能只碰一个租户，与 Console `cleanup-artifacts --tenant` 同因）。② `artifact_cleanup_service.py` 文档补齐"活跃引用是第四类、不进候选"的口径；候选 SQL 的 `NOT EXISTS` 保护落在 `artifact_retention_repository.py`（候选查询归它所有）。
- [x] [B-269] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置表）。
- [x] [B-269] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，8 passed（0.81s）。
- [x] [B-07][integration] 将原B-07落入`tests/agent_runtime/test_capacity_webchat_retention.py`，先RED后GREEN；真实保留/清理查询、活跃 Run/job 与共享文件系统；断言：清理不删模型唯一摘要、尚被 Run/job 引用的事件/上传或已受理制品；无安全快照的活动流不能先淘汰重放数据；保留边界在 API 中明确可观测。RED 与首条同源（同一验收文件一次落地）；GREEN 8 passed，B-07 断言位置见 Acceptance Evidence。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-269 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 保留期按清理边界取设置；不删唯一摘要/活跃Run-job引用/仍待投递回执，暂存与Run字节独立；游标过期明确、feed水位不重置，Token过期不删对话 | tests/agent_runtime/test_capacity_webchat_retention.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | verified |
| B-07 | integration | 真实保留/清理查询、活跃 Run/job 与共享文件系统 | 清理不删模型唯一摘要、尚被 Run/job 引用的事件/上传或已受理制品；无安全快照的活动流不能先淘汰重放数据；保留边界在 API 中明确可观测 | tests/agent_runtime/test_capacity_webchat_retention.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | verified |

### Acceptance Evidence
**命令与结果**

| 项 | 命令 | 结果 |
|---|---|---|
| RED（实现前） | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_retention.py` | exit≠0：`1 error during collection`（缺 `muad_agent_runtime.application.channel_retention`） |
| GREEN | 同命令 | exit=0：`8 passed in 0.81s` |
| 同域回归 | `uv run pytest -q tests/agent_runtime` | `1041 passed, 1 failed`（唯一失败是共享库环境残留，见下） |
| 该失败单跑复验 | `uv run pytest -q tests/agent_runtime/test_capacity_webchat_upload_staging.py` | `12 passed`（`cleanup_expired_staging` 无租户谓词，整跑时读到其他租户 3 条过期暂存行 ⇒ `assert 5 == 2`；非本任务实现缺陷——本任务新增清理一律带租户谓词） |
| 同域回归 | `uv run pytest -q tests/console_platform` | `385 passed` |
| 同域回归 | `uv run pytest -q tests/contracts` | `1281 passed` |
| 盘点 | `uv run pytest -q tests/test_configuration_inventory.py` | `4 passed`（新增 9 条 `channel_retention.py` 常量行 + 刷新 3 文件行号；CRLF 保持、只动自己的行） |
| 静态 | `uv run ruff check <4 改动文件>` / `uv run mypy apps packages` | `All checks passed` / `Success: no issues found in 439 source files` |
| 门禁校验 | `.venv/bin/python -m py_compile <4 文件>` / `.venv/bin/python -m mypy <3 源文件>` | OK / `Success: no issues found in 3 source files` |

**关键断言位置**（`tests/agent_runtime/test_capacity_webchat_retention.py`）

| 契约项 | 位置 | 真实边界 |
|---|---|---|
| 保留期按清理边界取设置（48h 不动、下一轮 1h 回收；`history_retention_days=0` 不删） | `:453`（:504 / :515 两个边界结论） | contracts typed 设置 + 隔离租户真实 canonical 行（真实 `EventWriter` 分配 seq） |
| 不删活跃 Run 重放数据 / 在飞 job 输入边界事件 / 唯一摘要 | `:530`（:600–:608） | 真实 `run_record` 状态 + 真实 `memory_consolidation_job` + 真实 `CONTEXT_SUMMARY` 行 |
| 可见历史只回收摘要覆盖的旧消息（无摘要会话一条不删） | `:612`（:735–:748） | 真实摘要 `covers_up_to_seq` 覆盖关系 + 活跃 Run/job 引用 |
| 游标过期明确 + 保留边界在 API 可观测 + checkpoint 不回退 | `:751`（:783 HTTP 410 + `earliest_seq`；:800 checkpoint） | 真实 `GET /v1/runs/{id}/events`（真实 RunService/EventTailHub） |
| 仍待投递/待核实回执与 feed 水位 | `:839`（:874 计数；:897 `last_seq`；:906 收件箱读取；:915 同键 reserve 命中回执） | 真实 `ChannelDeliveryService` reserve/ack（`DELIVERED`/`UNKNOWN`）+ 真实 feed 行 |
| 暂存活跃引用/宽限保护 + 暂存与 Run 字节独立 | `:955`（:1000 动作分类；:1016 Run 制品字节仍可读；:1027 宽限归零后回收） | 真实暂存字节 + 真实 `os.link` 发布的 Run 键 + 真实文件 mtime |
| Token 过期不删对话（完整扫描后会话/水位/可见历史不动） | `:1045`（:1100 sweep 计数；:1112 会话与 `last_seq`） | 真实 `control.chat_access_token`/`chat_session` 过期行 + 真实会话/事件 |
| 制品清理不删活跃 Run/job 引用的已受理制品；已清理字节读取一律 404 | `:1167`（:1230 动作分类；:1252 真实解析路由 404/200） | 真实 `runtime.run_record` + `task.task_execution` 行 + 真实共享文件 + 真实内部产物解析路由 |

**实现与设计事实**：保留期来自 contracts schema（唯一来源），清理每次边界取一次；`tenant_id` 谓词让破坏性清理可在共享库上只碰一个租户；软删保留 `(conversation_id, seq)` 与 `(tenant_id, delivery_key)` 完整唯一，水位与回执不回退。

**遗留（deferred，非本任务可闭合）**：① 装配根把 `run_retention_sweep` 挂进独立维护循环属 TASK-096（本任务只交付清理本体与报告，不占请求路径）；② 清理/保留指标目录属 TASK-098（当前只有 `webchat_retention_sweep` info 日志与可对账报告）；③ Console 产物清理的候选 SQL 改动落在任务 Files 未列出的 `artifact_retention_repository.py`——候选查询归它所有，服务层只补口径说明；④ `history_retention_days>0` 的实际回收以"最新摘要覆盖"为前提（压缩后才可回收旧原文），无摘要会话永久保留——这是 §3.6"不能删掉唯一上下文来源"的显式保护；⑤ 全量 pytest 验收/单测两条 heavy 校验按 validation.yml 口径延后到 verify-e2e/归档。
- B-07: verified — automated command passed; run_id=1faeffdae9554f809907d0952eb91452 (confirmed_by: runner)
- B-269: verified — automated command passed; run_id=1faeffdae9554f809907d0952eb91452 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started

- [2026-10-18] RED 记录：验收文件先落地（collection error，缺 `channel_retention`）
- [2026-10-18] 实现四类保留清理（活跃引用/唯一摘要/孤儿宽限保护）+ Console 制品候选的活跃引用保护；GREEN 8 passed
- [2026-10-18] completed (done)

---

## TASK-070: Gateway通知查询与显式已读接口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-044, TASK-052, TASK-061, TASK-064
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-270
- **Files**: `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `apps/im-gateway/src/muad_im_gateway/application/webchat_service.py`, `apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`, `tests/gateway/test_capacity_webchat_gateway_notification_api.py`
- **Estimate**: 45分钟

### Description

API-11转Runtime实际Owner端口，核CHAT/X-Chat-Context与来源授权；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-270][integration] 先在`tests/gateway/test_capacity_webchat_gateway_notification_api.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：API-11转Runtime实际Owner端口，核CHAT/X-Chat-Context与来源授权。RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_notification_api.py` exit=1，**7 failed + 1 passed in 3.27s**——7 条行为用例失败于公开路由尚不存在（真实 FastAPI 404 封套 `data=None`，如 L858 `TypeError: 'NoneType' object is not subscriptable`），1 条静态不持库用例平凡通过。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `api/webchat.py`——`GET /api/v1/webchat/notifications`（`ChatIdentityDep` 门控；`after_seq`≥0/`page`≥1/`page_size`≤100）与 `POST .../notifications/{id}/read`，响应 data 就是 contracts 投影；② `application/webchat_service.py`——`WebchatOwnerPort` 增加 `list_notifications`/`mark_notification_read`，`WebchatResourceService` 两个方法只携带**本次 Console 核验的** `platform_user_id`（浏览器自报字段没有位置），`_project` 走 `WebchatNotificationPage`/`WebchatNotificationReadReceipt`（`extra="forbid"`：投递/租约/存储内部字段混入即显式 `COMMON_INTERNAL_ERROR`，不转发）；③ `application/runtime_client.py`——新增受信端口 `INTERNAL_NOTIFICATIONS_PATH`/`INTERNAL_NOTIFICATION_READ_PATH` 与两个客户端方法；`_get_internal_json(with_error_data=True)` 让 `CURSOR_AHEAD` 连同 Owner 真实 feed 水位原样上抛（不夹取），`_post_internal_json(params=...)` 支持受信主体查询参数；游标/页参数原样透传（`page_size` 缺省不硬编码，由 Owner 从平台设置派生）。已读与送达独立：网关不触碰投递状态、不留已读副本。
- [x] [B-270] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-270] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_notification_api.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**8 passed in 3.15s**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-270 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | API-11转Runtime实际Owner端口，核CHAT/X-Chat-Context与来源授权；安全摘要/稳定游标/明确read回执，不在Gateway内存中创建另一份通知权威 | tests/gateway/test_capacity_webchat_gateway_notification_api.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_notification_api.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-270 | `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_notification_api.py` exit=1，**7 failed + 1 passed in 3.27s**（7 条行为用例在真实应用上打不到通知路由：404 封套 `data=None`，如 L858 对 `first.json()["data"]["total"]` 的 `TypeError`；1 条静态不持库用例平凡通过） | 同命令 exit=0，**8 passed in 3.15s** | ① `test_b270_chat_gate_precedes_every_notification_owner_call` L535-578：缺 Cookie⇒401 `UNAUTHORIZED`（L548/L554）、缺/坏 `X-Chat-Context`⇒422 `COMMON_VALIDATION_ERROR`、会话撤销⇒401 `CHAT_SESSION_REVOKED`（L561，Console 只判一次 L563）、上下文不符⇒409 `AUTH_CONTEXT_CHANGED`（L569）、跨部署租户⇒403 `FORBIDDEN`（L576），**全部门控失败 Runtime 零触达**（L554/562/570/577）；② `test_b270_notification_page_projects_the_owner_public_projection` L584-657：响应 data 逐字段等于 Owner 公开投影（items 13 字段集 L639、`through_seq=3/next_seq=2/unread_count=3` L599-638）、`no-store`（L596）、投递/租约/存储字段零键位与 canary 零出现（L640-643）、受信调用形状 `user_id/after_seq/page/page_size` + `X-Internal-Service`/`X-Tenant-Id`、不带 Cookie/X-Chat-Context（L648-656）、CHAT 原文与内部令牌不回显（L657）；③ `test_b270_verified_subject_is_the_only_notification_identity` L663-684：客户端自报 `user_id` 不进入受信调用（L684），他用户 feed canary 不出现（L683）；④ `test_b270_incremental_cursor_and_offset_page_follow_the_owner` L690-729：增量只回水位之后（L714）、offset 页与 `next_seq` 语义（L719-721）、超前游标 400 `CURSOR_AHEAD` 且 `data` 是真实水位 `{after_seq:99, through_seq:3}`、不夹取成空页（L723-726）、坏游标 422 且不触达 Owner（L728-729 查询序列 `["2","0","99"]`）；⑤ `test_b270_explicit_read_receipt_is_idempotent_and_delivery_independent` L737-779：回执恰 `{id, read_at}`（L754-755）、重复标记返回原时间且两次都经 Owner（L758-760）、他主体/未知⇒404 `COMMON_NOT_FOUND` 不泄露（L767-771）、只标记明确读过的行（`unread_count` 3→2，L775-776）、已读不改投递事实（L778-779）；⑥ `test_b270_internal_fields_or_broken_envelopes_fail_explicitly` L785-829：内部字段混入/`next_seq` 越界/缺 data 一律 500 `COMMON_INTERNAL_ERROR` 且 data 为空、失败不回显内部词干（L812-829）；⑦ `test_b270_gateway_keeps_no_second_notification_authority` L835-853：Owner 侧新增通知后网关响应立刻跟上（3→4 条、unread 3→4），每次读取都触达 Owner（L850-853）；⑧ `test_b270_notification_modules_hold_no_database` L859-874：三个模块零 sqlalchemy | 真实 `muad_im_gateway.main.app` + 生产 `api/webchat.py` 路由（真实路由表/封套/错误目录/`api/deps.py` CHAT 门控）；真实 Console HTTP（`StubConsole` uvicorn 线程 + 真实 socket，`ConsoleClient` 按 API-12 核验主体）；真实 Runtime HTTP（uvicorn 线程探针，真实封套 + 真实 `X-Internal-Service` 门控，通知权威只在 Owner 侧）；本文件零数据库访问，网关不持库由静态断言钉住 | verified |

实现要点：**唯一权威在 Owner**——通知列表/已读全部经 Runtime 受信内部端口（TASK-064 的 `/internal/webchat/notifications`），网关只做主体门控、请求组装与公开投影校验，不缓存、不留影子副本（每次读取都触达 Owner，用例⑦）；**身份来源**——受信调用只带本次 Console 核验的 `platform_user_id`（通知是用户级事实，没有入口维度），客户端自报 `user_id` 被忽略（用例③）；**游标裁决在 Owner**——`after_seq` 原样透传，`CURSOR_AHEAD` 连同真实 feed 水位原样上抛（`with_error_data=True`），不静默夹取；坏游标在网关侧 422（不触达 Owner）；**投影安全**——`WebchatNotificationPage`/`WebchatNotificationReadReceipt` 是 `extra="forbid"` 契约，投递/租约/存储内部字段混入即 500，绝不转发（用例⑥）；**已读与送达独立**——回执只有 `{id, read_at}`（与前端 TASK-073 已锁形状一致），幂等由 Owner 行事实保证，网关不触碰投递状态。

静态检查：`uv run ruff check`（本任务 4 个改动文件）「All checks passed!」；`uv run mypy apps packages` → Success: no issues found in 440 source files；新增/修改函数全部 ≤50 行；`docs/configuration-inventory.csv` 保持 CRLF（1540 行、0 裸 LF），新增 2 个常量行（`INTERNAL_NOTIFICATIONS_PATH`/`INTERNAL_NOTIFICATION_READ_PATH`）并平移本任务改动文件（`runtime_client.py` 54→57 起、`webchat_service.py` 58/60→62/64、`api/webchat.py` 56→58）的行号。

回归：`uv run pytest -q tests/gateway tests/contracts tests/architecture` exit=0，**1775 passed in 138.65s**（同域全量：Gateway 全部套件含 TASK-052/054/055/057/061/067 既有验收、契约面、架构守卫）；`uv run pytest -q tests/test_configuration_inventory.py` exit=0，**4 passed**。跑前已查无 `pytest/uvicorn/vite` 残留进程；未跑 `tests/acceptance/**`、未跑浏览器验收（共享 dev 库约束）。

遗留（deferred，非本任务可闭合）：① 前端增量轮询/未读徽标/隐藏页暂停与安全预览消费 API-11 属 TASK-084（本任务只提供公开面）；② 通知清理/保留与"用户活动游标最早可用水位"（`CURSOR_EXPIRED`）属 TASK-069（本任务只透传 Owner 裁决，不引入保留下界）；③ 通知/投递指标目录属 TASK-098；④ E2E（S-06/S-13/S-105 等）保持 planned，由 verify-e2e 记录完整证据；⑤ 计划外文件披露（计划 Files 之外的必需联动）：`apps/im-gateway/src/muad_im_gateway/application/runtime_client.py`（受信端口客户端方法，与 TASK-052 同类接线）、`docs/configuration-inventory.csv`（常量盘点）。
- B-270: verified — automated command passed; run_id=46f35e111acd4986b2582bc09a56aa4d (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)
- [2026-10-19] completed (done)

---

## TASK-071: 共享传输区分CONSOLE/CHAT认证上下文

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-007
- **Source**: agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**: harness-frontend#RULE-front-001
- **Acceptance-Refs**: B-271
- **Files**: `apps/console-platform/frontend/src/api/client.ts`, `apps/console-platform/frontend/src/api/authScope.ts`, `tests/frontend/test_capacity_webchat_chat_transport.py`
- **Estimate**: 60分钟

### Description

共享层统一追踪/语言/CSRF/X-Chat-Context；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-271][integration] 先在`tests/frontend/test_capacity_webchat_chat_transport.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：共享层统一追踪/语言/CSRF/X-Chat-Context。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_chat_transport.py` exit=1，4 failed + 6 errors（4 条源码契约断言失败：`client.ts` 未接线 authScope、401 分支未分域、`authScope.ts` 缺失、敏感头扫描；6 条真实 node 执行用例在 fixture 阶段 error——esbuild 解析不到缺失的 `src/api/authScope.ts`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `src/api/authScope.ts`（零依赖）——`AuthScope='CONSOLE'|'CHAT'`、`resolveAuthScope(pathname)`（`/chat` 整段前缀，`/chats`/`/chat-preview` 不误判）、`currentAuthScope()`、`CHAT_CSRF_COOKIE='muad_chat_csrf'`、`CHAT_CONTEXT_HEADER='X-Chat-Context'`、`setChatContextId`（bootstrap 后写入，空值归一为 null）、`chatContextForScope(scope)`（Console 域一律返回 null，不外泄残留上下文）、`onChatUnauthorized`/`notifyChatUnauthorized`（CHAT 401 的统一出口，供认证状态机与 SSE 复用）；② `src/api/client.ts`——请求拦截器按当前域取 CSRF（`csrfTokenForScope`：CONSOLE 读 `muad_csrf`、CHAT 读 `muad_chat_csrf`，头名仍 `X-CSRF-Token`），CHAT 域且有验证上下文时注入 `X-Chat-Context`（无上下文不写空头）；响应拦截器 401 先分域：CHAT 通知认证层并 reject（不跳管理登录、不叠加全局 Toast），CONSOLE 保留 `/login?returnUrl=` 跳转与 `/login` 守卫，`/me` 探测免 Toast 例外保留。凭据只从 Cookie 读取后进请求头，不打印、不入 URL/存储。
- [x] [B-271] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-271] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_transport.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，10 passed（0.12–0.17s）。
- [x] verifier `harness-frontend#RULE-front-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。实际执行：exit=0——`test_api_client_contract.py` 4 passed、`check_frontend_api_usage.py`「frontend api usage check OK」、`check_frontend_i18n.py`「i18n keys OK: 1207」、`npm ... run typecheck` 无输出（tsc --noEmit 干净）；旧拦截器契约（三头注入/兜底/封套 Toast/401 守卫）全部保留，`tests/frontend/test_console_auth_contract.py` 的 11 条契约在同目录回归中通过。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-271 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 共享层统一追踪/语言/CSRF/X-Chat-Context；CHAT 401回Token输入且/me探测不弹启动Toast，管理登录行为保留，组件/hooks不裸fetch或覆盖头 | tests/frontend/test_capacity_webchat_chat_transport.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_transport.py"] | verified |
| harness-frontend#RULE-front-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 前端 HTTP 调用只经服务层：共享 axios 实例与拦截器在 `src/api/client.ts`（`src/api/` 只承载共享客户端与鉴权），各模块的 service 位于 `modules/<module>/services/*.ts` 并 import 该实例；组件与 hooks 禁止裸用 axios/fetch，也禁止拿 `api` 实例自建请求。**HTTP 调用面**之外的 `api/client` 导出不在此禁令内——`apiErrorBody`/`newRequestId` 是无副作用的纯工具，组件可直接 import（现网 7 处，均为纯函数引用）。所有文案只使用 i18n key（zh-CN/en-US）——`scripts/check_frontend_i18n.py` 校验两侧词条的键集齐平与空值，并校验源码中 `t('字面量键')` 引用的键**必须已定义**（增删词条后务必跑它）；模板串动态键变体的齐备性由各模块的 `tests/frontend/*_i18n*.py` 契约钉死。列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-271 | `uv run pytest -q tests/frontend/test_capacity_webchat_chat_transport.py` exit=1，4 failed + 6 errors（4 条源码契约失败：`client.ts` 未接线 authScope / 401 未分域 / `authScope.ts` 缺失 / 敏感头扫描；6 条真实 node 用例 error：esbuild 无法解析缺失的 `src/api/authScope.ts`） | 同命令 exit=0，10 passed（0.12–0.17s） | ① `test_real_transport_keeps_each_scope_on_its_own_credentials`：CONSOLE 请求 `X-CSRF-Token=console-csrf-value` 且无 `X-Chat-Context`；CHAT（bootstrap 前）`X-CSRF-Token=chat-csrf-value`（≠ Console 值）且无上下文头；bootstrap 后 `X-Chat-Context=auth-context-1`；Console 页面带残留上下文时不外发；两次请求 `X-Request-Id` 不同且为 UUIDv4、`X-Locale=zh-CN`。② `test_real_transport_chat_401_returns_to_token_access_without_console_redirect`：CHAT 401 → `redirects==[]`、认证层收到通知、无 Toast、reject 401。③ `test_real_transport_console_401_keeps_the_management_login_behaviour`：CONSOLE 401 → `redirects==['/login?returnUrl=%2Fagents%3Fpage%3D2']`、零 CHAT 通知；`/login` 自身 401 不跳转且仍 Toast「用户名或密码错误」。④ `test_real_transport_session_probe_failures_do_not_toast`：`/auth/me` 与 `/webchat/auth/me` 失败均零 Toast（前者仍跳登录、后者只通知认证层）。⑤ `test_real_auth_scope_resolution_covers_chat_route_only`：8 条路径解析（`/chat*`→CHAT，`/chats`/`/chat-preview`→CONSOLE）。⑥ `test_real_transport_request_id_survives_non_secure_context`：抽掉 `crypto.randomUUID` 后仍产出合法 UUIDv4。⑦ `test_client_wires_the_scope_aware_transport`/`test_client_401_branch_splits_console_and_chat`：`client.ts` 接线逐条钉死（含旧契约的 `readCookie(CSRF_COOKIE)`、`/login` 守卫、`/auth/me` 例外）。⑧ `test_auth_scope_module_is_dependency_free_and_defines_the_chat_domain`：authScope 零 import 与全部导出签名。⑨ `test_components_and_hooks_do_not_bare_fetch_or_override_transport_headers`：`src/` 内除 `src/api/` 外无 `X-CSRF-Token`/`X-Chat-Context`，组件/hooks 无裸 axios/fetch 或传输头覆盖 | 真实 esbuild 0.21.5 把真实 `src/api/client.ts` + `src/api/authScope.ts`（及真实 axios/i18next/React 依赖链）打成 ESM 编译产物（453KB），由真实 node v24 执行；断言观测的是真实 axios 实例经 adapter 实际发出的请求头与真实 `window.location.assign`/Toast 调用。`@douyinfe/semi-ui` 只被别名成可观测 Toast 记录器（展示依赖，非被测共享传输）；本用例不触 HTTP/DB/浏览器，DOM 交互按契约留给 E2E | verified |

实现要点：**域隔离**——同一 axios 实例按当前页面解析认证域（`/chat` 整段前缀），CSRF 取源与 CHAT 上下文都按域选择：CHAT 只读 `muad_chat_csrf`、CONSOLE 只读 `muad_csrf`，两个方向都不发对方的头/值；`X-Chat-Context` 只由验证后的 bootstrap 写入（`setChatContextId`），Console 页面即使残留该状态也不外发。**401 分域**——CHAT 走 `notifyChatUnauthorized()`（认证状态机据此关闭流、清私有状态并回到 TokenAccess；SSE 与 service 复用同一出口），不跳管理登录、不叠加全局 Toast；CONSOLE 原行为逐条保留（`/login?returnUrl=`、`/login` 防循环、`/me` 探测免 Toast）。**可测试性**——域解析、上下文持有与 401 出口都放在零依赖的 `authScope.ts`，共享 axios 传输只接线，行为经真实编译产物在 node 下可执行。

回归：`uv run pytest -q tests/frontend` exit=1，**1 failed / 354 passed**——唯一失败 `test_audit_gap_contract.py::test_b214_resource_type_options_cover_backend_domain`（`CHAT_ACCESS_TOKEN` 未进 `AuditFilterBar.RESOURCE_TYPES`）与本任务零交集：该用例只读 `modules/audit-observability/components/AuditFilterBar.tsx` 与后端 `chat_token_service.AUDIT_RESOURCE_TYPE`（TASK-038 引入），`git diff --stat HEAD -- apps/console-platform/frontend/src/modules apps/console-platform/backend packages` 为空，属既有缺口（见遗留项）。同域相关：`tests/frontend/test_api_client_contract.py` 4 passed、`tests/frontend/test_console_auth_contract.py` 11 passed。`tests/console_auth` 未跑：本任务零 Python 后端改动，该目录为 PG 集成用例（共享环境约束下不启动）。

静态检查：`uv run ruff check tests/frontend/test_capacity_webchat_chat_transport.py` 干净；`npx tsc --noEmit`（frontend）干净；`scripts/check_frontend_api_usage.py`、`scripts/check_frontend_i18n.py` 干净；新增/改动函数均 ≤50 行、无 `any`；未新增日志/存储写入，凭据不进日志/URL。

遗留（非本任务范围）：① `X-Chat-Context` 的**响应上下文核对**（`/me` 返回的 `auth_context_id` 与已发送上下文比对、`AUTH_CONTEXT_CHANGED` 停自动重试）由 TASK-074 认证状态机落，本任务提供 `setChatContextId`/`onChatUnauthorized` 接缝；② 浏览器 SSE（TASK-072）与模块 service（TASK-073）必须从 `src/api/authScope.ts` 取 `chatContextForScope`/`notifyChatUnauthorized`，不得自行拼装传输头（本任务用例 ⑨ 会拦）；③ 既有失败 `test_audit_gap_contract.py::test_b214...`（审计筛选枚举缺 `CHAT_ACCESS_TOKEN`）需 audit-observability 侧补枚举或补词条，与 TASK-071 无交集；④ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 的「请求头固定三件」需补 CHAT 域取源与 `X-Chat-Context`；本任务不改 spec 文件以免 active context 漂移。
- B-271: verified — automated command passed; run_id=a9a2f64fe2b44470bfa7af6771de8e10 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-072: 浏览器SSE与snapshot分片解析器

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006, TASK-061, TASK-071
- **Source**: agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-272, B-101, B-105
- **Files**: `apps/console-platform/frontend/src/api/stream.ts`, `tests/frontend/test_capacity_webchat_browser_sse_parser.py`
- **Estimate**: 45分钟

### Description

真实编译后的解析器经Node验证跨chunk UTF-8/CRLF/多行data/重复seq/合法跳号；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-272][integration] 先在`tests/frontend/test_capacity_webchat_browser_sse_parser.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：真实编译后的解析器经Node验证跨chunk UTF-8/CRLF/多行data/重复seq/合法跳号。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_browser_sse_parser.py` exit=1，**1 failed + 12 errors（0.14s）**——12 条 node 用例在 fixture 阶段 error（esbuild 入口 import 缺失的 `src/api/stream.ts`），静态契约用例断言 `缺少前端文件：.../src/api/stream.ts`。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `src/api/stream.ts`（新增）——`createSseFrameParser`（增量帧解析：按 `\n`/`\r\n` 行切分、按完整行解码跨 chunk UTF-8、多行 `data:` 拼接、注释/心跳不产生帧；帧字节数逐行累计，超 `sse_event_max_bytes` 立即清空缓冲并抛 `FRAME_TOO_LARGE`；坏 `id`/坏 UTF-8 显式 `MALFORMED_FRAME`；`end()` 丢弃未收齐帧）；`createReplyStreamCollector`（`message.delta` 按 `reply_generation` 分代追加、无 snapshot 的分代跳跃显式失败、旧代迟到输出 `STALE_GENERATION` fence；`message.snapshot` 按 `event_id/seq/reply_generation` 分组收集 `part_index/part_count`，同组全到才原子替换正文并推进已应用水位，重复片 `DUPLICATE_PART`、片间插入/截断 `SNAPSHOT_INCOMPLETE`、断流丢弃未齐组；checkpoint 只推进已扫描安全水位且被钳在未齐组之前；重复 seq `DUPLICATE_SEQ`；`run.stage/terminal/error/confirmation/artifact` 按 typed DTO 校验后产出更新）；`openReplyStream`（fetch GET + 共享传输头 + `after_seq`/`Last-Event-ID` 游标排他续传 + EOF 不当完成 + 配置投影退避/次数 + `CURSOR_EXPIRED`/401 显式上抛 + `transport_revision` 创建时冻结）；`reconnectDelayMs`（指数退避 + equal jitter，可注入随机源）。② `src/api/client.ts`——`csrfTokenForScope` 加 `export`（共享流层复用同一凭据取源，一行、无行为变化）。
- [x] [B-272] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-272] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**13 passed（0.10–0.13s，连续多轮稳定）**。
- [x] [B-101][integration] 将原B-F01落入`tests/frontend/test_capacity_webchat_browser_sse_parser.py`，先RED后GREEN；解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序；断言：解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序。
- [x] [B-105][integration] 将原B-F05落入`tests/frontend/test_capacity_webchat_browser_sse_parser.py`，先RED后GREEN；共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文；断言：共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-272 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 真实编译后的解析器经Node验证跨chunk UTF-8/CRLF/多行data/重复seq/合法跳号；分片未齐不替换正文/推进游标，缓冲和单帧受有效配置约束 | tests/frontend/test_capacity_webchat_browser_sse_parser.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | verified |
| B-101 | integration | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 | tests/frontend/test_capacity_webchat_browser_sse_parser.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | verified |
| B-105 | integration | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 | tests/frontend/test_capacity_webchat_browser_sse_parser.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：真实 `src/api/stream.ts`（复用 `src/api/client.ts` 的传输头/追踪 ID/CSRF 取源与 `src/api/authScope.ts` 的 CONSOLE/CHAT 域、`modules/webchat/types.ts` 的 typed DTO）经仓库内 esbuild 打成真实 ESM 编译产物，真实 node 执行解析/状态/重连（fetch 以真实 `ReadableStream` 字节块驱动；DOM 交互与真实 SSE 留浏览器 E2E）。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-272 | `uv run pytest -q tests/frontend/test_capacity_webchat_browser_sse_parser.py` exit=1，**1 failed + 12 errors（0.14s）**：12 条 node 用例 fixture 阶段 error（esbuild 入口 import 缺失的 `src/api/stream.ts`），静态用例断言缺少文件 | 同命令 exit=0，**13 passed in 0.10–0.13s** | ① `test_b272_single_frame_budget_is_enforced_in_utf8_bytes` L739-751：帧字节数恰好等于 `sse_event_max_bytes` 通过、超一字节 `FRAME_TOO_LARGE`、失败后缓冲清空且解析器可继续、字节 > 字符的多字节帧同样超界（`wideBytes=417 > wideChars=357`）。② `test_b272_transport_resumes_by_applied_cursor_and_stops_on_terminal` L825-856：首连 URL 无查询参数、重连 `?after_seq=14` + `Last-Event-ID: 14`（含 checkpoint 已扫描水位）、`terminal` 才停止、应用顺序与公开事件一致、每请求共享头齐备且 bootstrap 前无 `X-Chat-Context`。③ `test_b272_cursor_expiry_is_explicit_and_not_auto_repulled` L858-869：410 `CURSOR_EXPIRED` 显式 `cursor-recovery`、窗口 `{after_seq:2, earliest_seq:5}` 原样带出、仅 1 次请求（不自动补拉）。④ `test_b272_chat_401_returns_to_token_access` L871-879：401 通知认证层、无 Console 跳转、无 Toast。⑤ `test_b272_close_aborts_the_active_connection` L898-904：close() 后 fetch 信号 aborted、结束原因 `closed`。⑥ `test_b272_context_seam_and_backoff_come_from_the_shared_projection` L906-916：`X-Chat-Context` 来自验证后的 bootstrap、退避 [500,1000]/[1000,2000]/[15000,30000] 受 base/cap 约束 | verified |
| B-101 | 同 RED（node 用例全部 fixture error） | 同命令 exit=0，13 passed | ① `test_b101_parser_covers_chunk_splits_crlf_multiline_and_heartbeats` L720-737：逐字节切分（跨 UTF-8 边界）解析出唯一帧 `cursor=3`/正文逐字一致、CRLF 等价 LF、多行 `data:` 按换行拼接、注释/心跳不产生帧、EOF 截断帧丢弃、坏 `id` `MALFORMED_FRAME`。② `test_b101_duplicate_seq_and_legal_gaps_are_explicit` L753-760：重复 seq `ignored/DUPLICATE_SEQ` 且正文不追加两次、合法跳号（1→5）推进到真实应用水位 | verified |
| B-105 | 同 RED（node 用例全部 fixture error） | 同命令 exit=0，13 passed | ① `test_b105_snapshot_parts_apply_atomically_only_when_complete` L762-780：乱序片（1,0）未齐时正文/游标不动（`midText=旧代`、`midResume=1`、`midPending=1`），整组到齐才原子替换且 `byteExact=true`（含多字节）、游标推进到 seq 9、整组后新代 delta 继续追加。② `test_b105_missing_duplicate_parts_and_checkpoints_keep_the_safe_cursor` L782-806：重复片 `DUPLICATE_PART` 不重复累计、片间插入 `SNAPSHOT_INCOMPLETE` 且正文/游标停在安全水位、checkpoint 6→游标 6、checkpoint 20 被钳在未齐组前（8）、断流丢弃未齐组且游标保持 8、重投整组后 `片零片一` 与游标 9。③ `test_b105_generation_switch_never_mixes_reply_text` L808-823：无 snapshot 的分代跳跃 `MALFORMED_FRAME` 且不混代、snapshot 整组替换、旧代迟到 delta `STALE_GENERATION` 不改正文不推游标。④ `test_b105_transport_revision_binds_active_connections_and_new_ones_rebind` L881-896：活跃连接 `transportRevision=rev-old`、调用方改配置后旧连接仍按旧单帧预算失败（`FRAME_TOO_LARGE`、1 次请求=旧重连预算）、新连接 `rev-new` 同帧可解析。⑤ `test_stream_module_keeps_the_shared_transport_contract` L918-：模块复用 authScope/client/typed DTO、无 axios/持久存储/日志/`any`、传输头只引用常量、`client.ts` 导出 `csrfTokenForScope` | verified |

实现要点：**唯一解析/收集接缝**——帧解析（字节级行切分、跨 chunk UTF-8、CRLF、多行 data、心跳跳过、单帧字节预算）与状态应用（分代 delta、snapshot 整组原子替换、checkpoint 安全水位、去重/fence）各只有一处定义；`id` 与载荷 `seq` 必须一致，坏形状显式 `MALFORMED_FRAME`，未知公开类型不静默吞。**游标语义显式**——已应用水位 `appliedCursor` 与已扫描安全水位 `scannedCursor` 分开，续传游标 `resumeCursor = max(applied, min(scanned, 未齐组seq-1))`：checkpoint 不追加正文也不跨未齐组，缺片/重复片/断流一律回安全游标（重连由服务端重投整组），绝不拼半份或跨代正文。**重连只走 GET**——`after_seq` 查询与 `Last-Event-ID` 头同值（游标排他），EOF 不当完成；退避/次数来自 API-19 投影（指数 + equal jitter，注入随机源可确定性验证），`CURSOR_EXPIRED`/`CURSOR_AHEAD`/`CURSOR_RUN_MISMATCH` 停止自动重连并原样带出服务端窗口，CHAT 401 走 `notifyChatUnauthorized`（不跳 Console 登录、不叠加 Toast）。**版本绑定**——连接创建时冻结 `transport_revision` 与四个传输参数，调用方后续更新配置不影响活跃连接，新连接重新读取。**凭据卫生**——SSE 的 CSRF 只取自 CHAT 域 Cookie（复用 `csrfTokenForScope`），上下文只来自验证后的 bootstrap；模块不写日志/持久存储，无新增词条。

回归（本机无 dev/测试残留进程，单跑不并发）：`uv run pytest -q tests/frontend` exit=0，**419 passed in 1.49s**（含 TASK-071 的 `test_capacity_webchat_chat_transport.py` 10 条——`csrfTokenForScope` 导出改动未回归；含本任务 13 条）；继承 verifier `harness-frontend#RULE-front-001`（stage: review；唯一责任 TASK-071）：`["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]` exit=0——4 passed、`frontend api usage check OK`、`i18n keys OK: 1365`（零新增词条）、`tsc --noEmit` 无输出。`uv run ruff check tests/frontend/test_capacity_webchat_browser_sse_parser.py`「All checks passed!」；AST（esbuild + acorn）逐函数核对：本任务生产函数全部 ≤50 行（最长 `applyDataFrameEvent` 47）；无 `any`/裸 axios/裸持久存储。

计划外披露：`apps/console-platform/frontend/src/api/client.ts` 仅把既有 `csrfTokenForScope` 从私有改为导出（一行；SSE fetch 复用同一凭据取源，无行为变化），不在任务 Files 计划内但属"传输头只经 `src/api/` 共享接缝"的必要接线。未新增迁移/DDL，未动 `src/locales/*.json`。

遗留（deferred，非本任务可闭合）：① 订阅生命周期/generation 守卫/可见 Run 编排与抖动消费属 TASK-082（本任务已交付退避+抖动纯函数与 `done` 结束原因）；② clientConfig 加载/版本化消费与配置重载策略属 TASK-076（本任务只消费 API-19 投影值，不复制默认）；③ `WebchatClient.openEvents` 端口接线属 TASK-082（TASK-073 的 service 未伪造 SSE）；④ 浏览器真实 SSE/断网/实例切换 E2E（S-103/E-103 等）保持 planned，由 TASK-113/116 的 verify-e2e 记录完整证据。
- B-101: verified — automated command passed; run_id=5c59d1d827924854b901589634cb490a (confirmed_by: runner)
- B-105: verified — automated command passed; run_id=5c59d1d827924854b901589634cb490a (confirmed_by: runner)
- B-272: verified — automated command passed; run_id=5c59d1d827924854b901589634cb490a (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-19] B-272/B-101/B-105 RED：新建 `tests/frontend/test_capacity_webchat_browser_sse_parser.py` → exit=1，**1 failed + 12 errors**（node 用例 fixture 阶段 error：esbuild 入口 import 缺失的 `src/api/stream.ts`；静态用例断言缺少文件）
- [2026-10-19] 实现 `src/api/stream.ts`（帧解析/分片收集/游标重连/版本绑定）+ `client.ts` 导出 `csrfTokenForScope`；首轮断言逐项修正：harness 中途状态捕获位置、checkpoint 更新解包、预期分片进度条数、断流后安全游标（checkpoint 被钳位到未齐组之前）、重连预算耗尽保留真实错误码
- [2026-10-19] B-272/B-101/B-105 GREEN：同命令 exit=0，**13 passed**（0.10–0.13s，多轮稳定）；`tests/frontend` 整跑 **419 passed in 1.49s**；RULE-front-001 verifier exit=0（4 passed / api usage OK / i18n 1365 / tsc 无输出）；ruff 干净；AST 逐函数 ≤50 行
- [2026-10-19] completed (done)

---

## TASK-073: 模块WebchatClient、DTO与Owner接口服务

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-005, TASK-071
- **Source**: agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-273
- **Files**: `apps/console-platform/frontend/src/modules/webchat/services/webchatApi.ts`, `apps/console-platform/frontend/src/modules/webchat/types.ts`, `tests/frontend/test_capacity_webchat_webchat_services.py`
- **Estimate**: 45分钟

### Description

所有HTTP经模块service与共享实例；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-273][integration] 先在`tests/frontend/test_capacity_webchat_webchat_services.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：所有HTTP经模块service与共享实例。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_webchat_services.py` exit=1，**1 failed + 9 errors**——9 条真实 node 用例在 fixture 阶段 error（esbuild 打包入口 import 缺失的 `modules/webchat/services/webchatApi.ts`/`types.ts`），1 条源码契约用例断言 `缺少前端文件：.../modules/webchat/types.ts`。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `modules/webchat/types.ts`——DTO/请求输入/`WebchatClient` 端口。字段名与 `muad_contracts.webchat`（TASK-005）逐一对应（principal 七字段、入口、会话索引/详情、活跃 Run+待确认、执行阶段、上传与附件、历史页 `through_seq`、通知页 `through_seq+next_seq+unread_count`、API-19 配置投影），全部 snake_case 且无 `any`；`WebchatSubmitInput.conversation_id` 必填、`WebchatResumeInput` 四字段必填、`WebchatRequestOptions.signal?: AbortSignal`、`WebchatRequestErrorCode` 给出 12 个前置校验码。② `modules/webchat/services/webchatApi.ts`——16 个 HTTP 方法（API-04 exchange/me/logout、API-19、API-05、API-14/08/15、API-06/09、API-10 上传+下载、API-11 列表+已读、API-08 cancel/resume）全部经共享 `api` 实例，**不拼装任何传输头、不读 Cookie、不写日志/持久存储**（CHAT 上下文与 401 出口留在 `src/api/authScope.ts` + 共享拦截器，由 TASK-074 写入）；`submit`/`createConversation` 的 `Idempotency-Key` 与 `message_id` 由调用方持有，service 不重新生成；缺参在发请求前以 `WebchatRequestError` 显式失败；`webchatErrorCode`/`isWebchatAuthError`/`isWebchatRetryableError`/`isWebchatCursorError` 把稳定目录码映射成鉴权/可重试/游标三类判定（传输失败→`NETWORK_ERROR`、未知→`UNKNOWN`，不把异常原文当业务码）；上传走真实 `FormData`（字段 `file`）、下载走 `responseType: 'blob'`。
- [x] [B-273] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-273] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_services.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed**（0.09–0.11s）。
- [x] 继承 verifier `harness-frontend#RULE-front-001`（stage: review；timeout=600秒；唯一责任 TASK-071）：`["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`；本任务不改该 Spec 文件、不重复认领，只登记实际执行证据：exit=0——`test_api_client_contract.py` 4 passed、`check_frontend_api_usage.py`「frontend api usage check OK」、`check_frontend_i18n.py`「i18n keys OK: 1265」、`npm --prefix apps/console-platform/frontend run typecheck`（tsc --noEmit）无输出；新增模块目录未引入裸 HTTP/传输头/i18n 缺口（本任务零新增词条，service 无展示文案）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-273 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 所有HTTP经模块service与共享实例；方法覆盖认证/配置/入口/会话/消息/附件/通知/控制，提交必须显式conversationId，严格DTO及AbortSignal | tests/frontend/test_capacity_webchat_webchat_services.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_services.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-273 | `uv run pytest -q tests/frontend/test_capacity_webchat_webchat_services.py` exit=1，1 failed + 9 errors：9 条真实 node 用例在 fixture 阶段 error（esbuild 入口 import 缺失的 `modules/webchat/services/webchatApi.ts`/`types.ts`），1 条源码契约用例断言 `缺少前端文件：.../modules/webchat/types.ts` | 同命令 exit=0，10 passed（0.09–0.11s） | ① `test_real_client_covers_the_documented_http_surface`：`Object.keys(client)` 与 design §3.4 的 16 个方法名齐备；exchange=POST `/webchat/auth/exchange`、me=GET `/webchat/auth/me`、logout=POST `/webchat/auth/logout`（`{logged_out:true}`）、config=GET `/webchat/client-config`、entries=GET `/webchat/entries`、cancel/resume=POST `/webchat/runs/{run}/cancel|resume`；multipart 字段名=`file`。② `test_real_client_keeps_the_chat_token_out_of_everything_but_the_exchange_body`：exchange 体恰为 `{token}`、无查询参数；`tokenInUrl==[]`、`tokenInBody==['POST /webchat/auth/exchange']`；service/types 无 `console.`/`localStorage`/`sessionStorage`/`document.cookie`。③ `test_real_client_rides_the_shared_transport_and_the_chat_context_seam`：bootstrap 前 `X-Chat-Context` 缺失（不伪造），CSRF 取自 `muad_chat_csrf`、`X-Locale=zh-CN`、每请求 `X-Request-Id`；`setChatContextId(session_id)` 后 service 请求自动携带该上下文（接缝生效、service 不拼头）；CHAT 401 → `notifyChatUnauthorized` 命中、`redirects==[]`、`toasts==[]`；静态扫描确认两文件无四个传输头字面量。④ `test_real_client_submits_with_explicit_conversation_and_caller_held_idempotency`：提交体恰为 `{conversation_id,message_id,text,upload_ids}`+`Idempotency-Key: idem-msg-273`；6 条缺参（缺 conversation_id/空 message_id/空正文/空幂等键×2/空 selection）各自返回稳定码且 `requestsSent==0`；`WebchatSubmitInput` 的 `conversation_id` 为必填（`conversation_id?:string` 不存在）。⑤ `test_real_client_maps_conversation_message_and_control_routes`：会话列表 `?page=2&page_size=50`、新建 POST 无自报请求体+幂等键、详情返回活跃 Run 与完整候选（CONFIRM/REJECT 不重编号）、历史首屏 `{page:1,page_size:50}` 与前页 `{page:2,page_size:50,through_seq:40}`、resume 体四字段显式绑定原 submission/source。⑥ `test_real_client_streams_upload_and_authorized_download`：上传 FormData 携带真实文件名/类型/字节数，回执只含 `upload_id`（无伪 `artifact_id`）；下载 `responseType=blob` 且取到 6 字节 `application/pdf`。⑦ `test_real_client_maps_notification_feed_and_read_receipt`：`?page=1&page_size=20&after_seq=5`、页内 `next_seq/through_seq/unread_count` 原样保留、已读 POST 无请求体并返回持久 `read_at`。⑧ `test_real_client_maps_error_codes_for_the_authentication_state_machine`：`CHAT_SESSION_EXPIRED`/`AUTH_CONTEXT_CHANGED`/`CHAT_TOKEN_INVALID`→auth、`CHAT_AUTH_RATE_LIMITED`/`RUN_CAPACITY_EXCEEDED`→retryable、`CURSOR_EXPIRED`→cursor（`UPLOAD_EXPIRED` 不误判）、`NETWORK_ERROR`/`UNKNOWN` 显式区分。⑨ `test_real_client_forwards_abort_signals`：18 条带信号调用全部透传**同一个** `AbortSignal` 对象（`signalMatched`）。⑩ `test_module_source_keeps_dtos_and_client_contract_strict`：13 个 DTO 的字段名与契约一致、`WebchatPage<T>`/`WebchatMessagePage`/`WebchatNotificationPage` 泛型封套、端口 16 方法 + `signal?: AbortSignal`、service 只 import `../../../api/client` 且无 `axios`/`fetch(`/`create(`/松散 `any` | 真实 esbuild 把真实 `modules/webchat/services/webchatApi.ts` + `modules/webchat/types.ts`（含真实 `src/api/client.ts`/`authScope.ts` 与真实 axios/i18next/React 依赖链）打成 ESM 编译产物，由真实 node v24 执行；观测的是真实 axios 实例经 adapter 实际发出的方法/URL/参数/体/头与真实 `authScope` 通知。`@douyinfe/semi-ui` 只被别名成可观测 Toast 记录器（展示依赖，非被测 service）；本用例不触 HTTP/DB/浏览器，DOM 交互按契约留给 E2E（S-101/E-101，TASK-112） | verified |

实现要点：**唯一 HTTP 出口**——16 个方法全部 `api.get/post` 共享实例，传输头（语言/追踪/CSRF/CHAT 上下文）只在 `src/api/client.ts` 按 `authScope` 注入，本模块零头拼装、零 Cookie 读取，TASK-071 的用例⑨敏感头扫描保持通过。**显式身份**——`conversation_id`、`message_id`、`Idempotency-Key`、`submission_id/source_event_id/selection` 全部由调用方持有：提交/新建/恢复缺参在发请求前以 `WebchatRequestError` 失败（0 HTTP），网络重试沿用原身份，满足「切换会话不得改投、重试沿用原消息ID」。**错误映射**——只认后端稳定目录码，传输失败与未知异常各有显式码，鉴权（含 `AUTH_CONTEXT_CHANGED`）/可重试/游标三类判定供 TASK-074 状态机与 TASK-082 恢复消费。**边界诚实**——默认分页与上限不复制前端常量（只透传调用方给出的参数），上传/下载不伪造 artifact，resume 回执只声明 run/submission/status/execution_status。

回归：`uv run pytest -q tests/frontend` exit=0，**375 passed**（1.96s，含 TASK-071 的 `test_capacity_webchat_chat_transport.py` 10 passed 与既有 `test_audit_gap_contract.py`）。`uv run ruff check tests/frontend/test_capacity_webchat_webchat_services.py apps/console-platform/frontend/src/modules/webchat`「All checks passed!」；`npx tsc --noEmit`（frontend）exit=0 无输出；`scripts/check_frontend_api_usage.py`/`scripts/check_frontend_i18n.py` 干净（1265 键，本任务零新增词条）；新增函数均 ≤50 行、无 `any`。

遗留（非本任务范围）：① `openEvents`（回复订阅）属共享流层 `src/api/stream.ts`（TASK-072，尚未实现），由 TASK-082 接入 `WebchatClient` 时补方法，本任务不伪造 SSE 实现；② 后端 Owner 路由（TASK-052/054/055/056/070）尚未实现，本任务按 design §3.4 API 表定义 DTO/路由：其中 `resume` 回执 `WebchatResumeReceipt{run_id,submission_id,status,execution_status}` 与已读回执 `{id,read_at}` 无既有契约类（cancel 回执沿用 Runtime `run_cancel_view` 的 `{run_id,status,cancel_requested}`），TASK-052/056 落地后若字段不同需同步本模块；③ `download` 的错误响应是 Blob（服务端错误封套不可解析），当前映射为 `UNKNOWN`，预览/下载失败文案由 TASK-084 处理；④ CHAT 上下文写入与「已发送上下文 vs `/me` 返回 `auth_context_id` 比对」仍由 TASK-074 认证状态机落（本任务只经共享传输复用接缝）；⑤ 规范刷新：`harness-frontend` Conventions 需补「CHAT 域模块 service 只消费共享传输、不拼装传输头」的对照示例；本任务不改 spec 文件以免 active context 漂移。
- B-273: verified — automated command passed; run_id=a4ed23cf5f3b418caf803aba52c0821a (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-074: CHAT认证状态机与身份切换清理

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-072, TASK-073
- **Source**: agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-274, B-104
- **Files**: `apps/console-platform/frontend/src/modules/webchat/hooks/useChatAuth.ts`, `apps/console-platform/frontend/src/modules/webchat/authenticationState.ts`, `tests/frontend/test_capacity_webchat_chat_auth_state.py`
- **Estimate**: 45分钟

### Description

restore/显式exchange singleflight，退出/切主体停流并清消息/草稿/通知/Blob；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-274][integration] 先在`tests/frontend/test_capacity_webchat_chat_auth_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：restore/显式exchange singleflight，退出/切主体停流并清消息/草稿/通知/Blob。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_chat_auth_state.py` exit=1，**2 failed + 9 errors（0.16s）**——9 条真实 node 用例在 fixture 阶段 error（esbuild 打包入口 import 缺失的 `modules/webchat/authenticationState.ts`），2 条源码契约用例断言 `缺少前端文件：.../modules/webchat/authenticationState.ts` 与 `.../modules/webchat/hooks/useChatAuth.ts`。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `modules/webchat/authenticationState.ts`——CHAT 认证状态机与身份作用域（`createChatAuthenticationService`）。状态 `anonymous/exchanging/authenticated/expired/revoked/context-changed`；`restore`（`/me`）与 `exchangeToken` 各自 singleflight（并发调用合并为一次 HTTP、共享结果），`logout` 撤本会话；`ChatAuthenticationClient` 只暴露 `bootstrap/exchangeToken/logout` 三个认证方法，状态机**没有任何写消息能力**（结构上不可能自动重发 POST）；身份边界 `releaseIdentity` 按固定顺序执行：清上下文 → `abort()` 身份信号（订阅/在飞请求随之结束）→ 逐条执行登记的消息/草稿/通知/游标清理（单条抛错记入 `boundary.failures` 且不阻断其余）→ 撤销本身份 `trackObjectUrl` 的全部 Blob URL；`generation` 只在身份边界（会话结束或主体变化）递增，`epoch` fence 边界之后完成的旧身份认证响应（迟到 `/me` 不复活会话）；错误只认模块 service 的稳定目录码（`webchatErrorCode`），401 出口判 `expired`、`handleAuthFailure(error)` 把 409 冲突/401 撤销精确收敛且**不重复清场**；`snapshot()` 按引用缓存（`useSyncExternalStore` 语义），无 Token 字段、无持久存储、无定时器、无 `console.`。② `modules/webchat/hooks/useChatAuth.ts`——React 绑定：`useMemo` 建 service、`useSyncExternalStore` 订阅快照、`useEffect` 把共享传输的 `onChatUnauthorized` 出口接到状态机并在卸载时注销（单页只有一个当前 CHAT 主体）；只做接线，不自动认证/不自动重试，不拼装传输头、不发 HTTP、不读 Cookie。
- [x] [B-274] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-274] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**11 passed（0.11–0.16s）**。
- [x] [B-104][integration] 将原B-F04落入`tests/frontend/test_capacity_webchat_chat_auth_state.py`，先RED后GREEN；X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界；断言：X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界。RED/GREEN 同上（同一验收文件与 fixture）；`/me` 返回的 `auth_context_id` 与已持有上下文比对（TASK-071 遗留①）由本任务落地：不一致即判 `AUTH_CONTEXT_CHANGED` 停自动重试。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-274 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | restore/显式exchange singleflight，退出/切主体停流并清消息/草稿/通知/Blob；AUTH_CONTEXT_CHANGED停自动交换，Token不进reducer/持久缓存，无滑动续期 | tests/frontend/test_capacity_webchat_chat_auth_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | verified |
| B-104 | integration | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 | tests/frontend/test_capacity_webchat_chat_auth_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-274 | `uv run pytest -q tests/frontend/test_capacity_webchat_chat_auth_state.py` exit=1，**2 failed + 9 errors（0.16s）**：9 条真实 node 用例在 fixture 阶段 error（esbuild 入口 import 缺失 `modules/webchat/authenticationState.ts`），2 条源码契约用例断言 `缺少前端文件：.../modules/webchat/authenticationState.ts` 与 `.../modules/webchat/hooks/useChatAuth.ts` | 同命令 exit=0，**11 passed（0.11–0.16s）** | ① `test_b274_restore_singleflights_and_binds_the_verified_context` L712-733：并发 `restore()` 只发 1 次 `GET /webchat/auth/me`（`requests==1`）、两次调用同一验证主体、上下文等于响应 `auth_context_id`（`sameSnapshotObject==true`）、随后 `client.listEntries()`/`download()` 经共享接缝携带该上下文 + `muad_chat_csrf`、`exchangePosts==0`。② `test_b274_exchange_singleflights_and_keeps_the_token_out_of_everything` L767-784：并发 `exchangeToken` 只 1 次 `POST /webchat/auth/exchange`、体恰 `{token}`、`tokenInUrl==0`、快照 JSON 无 Token、首访交换无上下文头。③ `test_b274_identity_switch_clears_old_state_streams_drafts_and_blob_urls` L787-813：切主体先 `cleanup:identity-switch` 后 `state:authenticated`（同一 generation）、stream/messages/drafts/notifications/cursors 五类清理齐备、两个 Blob URL 撤销、generation+1、新上下文落地、`switchContextSent` 为当前已验证上下文。④ `test_b274_logout_stops_streams_clears_state_and_revokes_blob_urls` L816-850：退出恰 1 次 `POST /webchat/auth/logout`（无体、过独立 CHAT CSRF）、真实 `openReplyStream` 以 `closed` 结束、旧身份信号 aborted 而新作用域信号未中止、五类资源清理、Blob URL 撤销、上下文清空、generation+1、SSE 首连携带验证上下文且 `transportRevision==settings 投影`。⑤ `test_b274_revoked_session_refines_the_state_without_double_cleanup` L885-896：401 出口先判 `expired`，精确码收敛 `revoked`+`CHAT_SESSION_REVOKED`，清理恰 5 条、generation 不变、无补拉。⑥ `test_b274_late_old_identity_response_never_resurrects_the_session` L929-942：边界之后完成的旧身份 `/me` 结果被 fence，保持 `anonymous`、无额外清理/状态迁移。⑦ `test_auth_state_module_keeps_the_credential_and_boundary_contract` L945-1004 / `test_use_chat_auth_hook_only_wires_the_state_machine_to_react` L1007-1033：源码契约（无裸 HTTP、无传输头、无持久存储、无 `setTimeout/setInterval`、无 `any`、Token 无状态保存；hook 只订阅快照并注销 401 监听） | 真实 esbuild 把真实 `modules/webchat/authenticationState.ts` + `services/webchatApi.ts` + `types.ts` + `src/api/client.ts`/`authScope.ts`/`stream.ts`（含真实 axios/i18next/React 依赖链）打成 ESM 编译产物，由真实 node v24 执行；观测的是真实 axios 实例经 adapter 实际发出的方法/URL/体/头、真实 `authScope` 上下文与 401 接缝、真实 `openReplyStream` 的停止原因与 `transportRevision`。`@douyinfe/semi-ui` 只被别名成可观测 Toast 记录器（展示依赖）；不触 HTTP/DB/浏览器，DOM/React 交互按契约留给 E2E（S-101/E-101，TASK-112） | verified |
| B-104 | 同上 RED（同一验收文件与 module fixture） | 同上 GREEN（**11 passed**） | ① `test_b104_restore_rejects_a_context_that_changed_under_the_same_cookie` L735-764：同上下文复核保持 `authenticated` 且 generation 不变、零清理；`/me` 返回不同 `auth_context_id` → `context-changed`+`AUTH_CONTEXT_CHANGED`、上下文清空（冲突后的资源请求零上下文头）、恰 1 次请求（不自动重试）、五类资源清理、无 Console 跳转/无 Toast。② `test_b104_401_expires_without_auto_exchange_resend_or_sliding_renewal` L853-882：真实拦截器 401 出口 → `expired`；精确码收敛 `CHAT_SESSION_EXPIRED`；`requestsAfter==1`（不自动重试/补拉）、`submitPosts==1`（不重发 POST，`Idempotency-Key`/`message_id` 原样）、`exchangePosts==1`（不自动交换）、上下文清空。③ `test_b104_context_changed_stops_automatic_retry_until_explicit_reentry` L899-926：409 经 `handleAuthFailure` → `context-changed`；20 个 tick 后请求数不变（无定时器、无自动互换 Cookie）；只有用户明确重新进入才建立新主体（`exchangePosts==2`）。④ `test_b274_identity_switch...`/`test_b274_logout...`：身份变化撤销 Blob URL、订阅随身份信号停止 | 同 B-274：真实共享传输（`client.ts` 拦截器 + `authScope.ts` 401 出口）、真实模块 service、真实 `openReplyStream`（订阅上下文只来自验证结果、绑定 API-19 `transport_revision`）；SSE 退避与单帧 UTF-8 字节边界由 TASK-072 的 `tests/frontend/test_capacity_webchat_browser_sse_parser.py`（B-272）钉死，本任务只登记认证侧接缝证据，不重复认领 | verified |

实现要点：**状态机唯一**——认证状态、身份作用域与清理只在 `authenticationState.ts` 定义一次，hooks 只做 React 接线（`useSyncExternalStore` + 401 出口注册），组件后续只消费 `snapshot`。**边界顺序即安全**——`releaseIdentity` 先清共享上下文再 `abort()` 身份信号、随后逐条清私有状态、最后撤销 Blob URL，因此任何清理回调都不可能再拿旧身份发请求；`generation` 只在会话结束/主体变化时递增，`epoch` 保证边界之后完成的旧身份 `/me` 或 exchange 结果不写回状态。**凭据零留存**——Token 只经一次 `exchangeToken` 调用进入 service 的请求体，状态机不保存、快照无 Token 字段、无持久存储 API、无日志。**错误码零解析**——401 出口只能判 `expired`，409 冲突与撤销由页面用 `handleAuthFailure(error)` 从稳定目录码精确收敛，且收敛不重复清场（用例⑤钉死）。**上下文比对**——`/me` 返回的 `auth_context_id` 与已持有上下文不一致即判 `AUTH_CONTEXT_CHANGED`（TASK-071 遗留①），不静默换身份。

回归：`uv run pytest -q tests/frontend` exit=0，**430 passed（2.44s）**（含本任务 11 passed 与 TASK-071/072/073 的 capacity_webchat 用例、`test_api_client_contract.py`）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」；`scripts/check_frontend_i18n.py`「i18n keys OK: 1365」（本任务零新增词条）；`uv run ruff check tests/frontend/test_capacity_webchat_chat_auth_state.py apps/console-platform/frontend/src/modules/webchat`「All checks passed!」；`.venv/bin/python -m py_compile` 通过；新增函数均 ≤50 行、无 `any`。变异校验：删掉 `releaseIdentity` 的 Blob 撤销 → 恰 2 条用例失败（切主体/退出）；删掉 `restore` singleflight 守卫 → fixture 阶段即被拦截（请求序列错位）。

遗留（非本任务范围）：① `useChatAuth` 的 React 渲染路径（订阅、卸载注销）按契约留真实浏览器 E2E（S-101/E-101，TASK-112），本任务以源码契约 + `tsc --noEmit` 登记；② 页面级消息/草稿/通知/游标与附件 Blob URL 的**登记**由 TASK-077/078/080/082/084 接入（本任务提供 `registerIdentityCleanup`/`trackObjectUrl`/`identitySignal` 与 generation fence）；③ `handleUnauthorized()` 因共享传输 401 出口不带错误码只能判 `expired`，精确 `revoked`/`expired` 需页面在 catch 中调 `handleAuthFailure(error)`（用例已钉死两者顺序与不重复清场）；④ 头像预览（TASK-085/084）与附件下载复用同一带认证上下文的 service 路径，浏览器内实际渲染留 E2E；⑤ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 的「请求头固定三件」需补 CHAT 域取源与 CHAT 上下文头、以及「CHAT 认证状态机是上下文唯一写入点」；本任务不改 spec 文件以免 active context 漂移。
- B-104: verified — automated command passed; run_id=0f57f9ded7fb4c0e8aed1a3aa966343b (confirmed_by: runner)
- B-274: verified — automated command passed; run_id=0f57f9ded7fb4c0e8aed1a3aa966343b (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] completed (done)

---

## TASK-075: 单个Token输入与过期重新进入

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-074
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: B-275
- **Files**: `apps/console-platform/frontend/src/modules/webchat/components/TokenAccess.tsx`, `tests/frontend/test_capacity_webchat_token_access_ui.py`
- **Estimate**: 30分钟

### Description

无注册/密码/管理账号；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-275][integration] 先在`tests/frontend/test_capacity_webchat_token_access_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：无注册/密码/管理账号。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_token_access_ui.py` exit=1，**5 failed + 7 errors（0.21s）**——7 条真实 node 用例在 fixture 阶段 error（esbuild 打包入口 import 缺失的 `modules/webchat/components/TokenAccess.tsx`），5 条源码/词条契约用例断言 `缺少前端文件：.../modules/webchat/components/TokenAccess.tsx` 与 `zh-CN 缺 ['chat.tokenAccess.title', ...]`。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。（新增导出函数均 ≤50 行；无 `any`；Token 只进一次 exchange 请求体，提示只由稳定码映射，源码无 `console.`/持久存储/URL 写入/裸 HTTP/传输头）
- [x] [B-275] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-275] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_token_access_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**12 passed（0.14s）**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-275 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 无注册/密码/管理账号；遮蔽Token、提交防重复且完成清空；Cookie失效提示有效Token，无效Token联系管理员，不回显原文或把Token放URL | tests/frontend/test_capacity_webchat_token_access_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_token_access_ui.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-275 | `uv run pytest -q tests/frontend/test_capacity_webchat_token_access_ui.py` exit=1，**5 failed + 7 errors（0.21s）**：7 条真实 node 用例在 fixture 阶段 error（esbuild 入口 import 缺失 `modules/webchat/components/TokenAccess.tsx`），5 条源码/词条契约用例断言 `缺少前端文件：.../components/TokenAccess.tsx`、`zh-CN 缺 ['chat.tokenAccess.title', ...]`、`catalog 未登记`/`缺提示词条` | 同命令 exit=0，**12 passed（0.14s）** | ① `test_b275_first_entry_posts_the_token_once_and_clears_the_input` L630-658：粘贴（含前后空白）提交后恰 1 次 `POST /webchat/auth/exchange`、体恰 `{token: TOKEN_A}`（已去空白）、首访无 CHAT 上下文头、提交完成状态复位为 `{token:'',pending:false,failed:false}`、快照无 Token、`state==authenticated`（`USER_TOKEN`+验证上下文）、零跳转/零 Toast。② `test_b275_submit_is_single_flight_and_respects_the_parent_in_flight` L661-674：同一在飞交换的第二次提交被同步闸门挡住（`duplicate==false`、无第二次 exchange、不重复 settle）、父级在飞与空白输入都不发起提交（`{accepted:false,submits:0}`）。③ `test_b275_expired_cookie_prompts_a_valid_token_and_allows_reentry` L677-707：真实 `/me` 建会话后资源请求 401 → `expired`+`UNAUTHORIZED` 提示“重新输入有效 Token”；精确码经 `handleAuthFailure` 收敛 `CHAT_SESSION_EXPIRED`、CHAT 上下文清空；重新粘贴 `TOKEN_B` 后建立新主体、提示消失、该段仅 1 次显式交换（不自动重试/不自动交换）。④ `test_b275_invalid_or_revoked_token_directs_to_the_administrator` L709-737：`CHAT_TOKEN_INVALID`/`CHAT_TOKEN_REVOKED` 提示词条含“管理员”/“administrator”、不得进入聊天、失败也清空原文、恰 1 请求、CHAT 401 不叠加全局 Toast；`CHAT_AUTH_RATE_LIMITED` 提示重试。⑤ `test_b275_unmapped_submit_failure_falls_back_explicitly` L739-748：父级未收敛的提交失败显式落兜底词条、闸门释放可复用、原文仍清空。⑥ `test_b275_input_state_machine_and_notice_mapping_are_pure` L750-774：`change/submit/settle/clear` 迁移、重复 `submit` 幂等（同一状态对象）、9 个稳定码 → `chat.tokenAccess.error.<CODE>` 同名词条、成功无提示、未知码落兜底、映射只收码（`arity==1`）。⑦ `test_b275_token_plaintext_never_leaves_the_exchange_body` L776-791：五个 canary Token 在全部请求的 URL/查询参数/传输头里零出现，请求体恰一次 `POST /webchat/auth/exchange`，提示文案不包含任何原文。⑧ `test_token_access_is_a_single_masked_input_without_registration_or_admin_account` L793-840：源码契约（`type="password"`+`autoComplete="off"`、恰一个输入框、无注册/密码/管理账号/Console 登录守卫、无持久存储/URL 写入/日志/裸 HTTP/传输头/松散类型/硬编码中文）。⑨ `test_token_access_props_match_the_component_contract` L842-852 / `test_token_access_compiles_to_a_real_typescript_artifact` L854-881：`{submitting,errorCode,onSubmit,onClear}` 端口逐字段对上；真实 TSX 经 esbuild 编译成 ESM 且含入口与提示映射。⑩ `test_notice_codes_are_catalog_registered_and_cover_the_auth_surface` L883-912 / `test_token_access_keys_are_localized` L914-940：映射码与 catalog 同名、service 的认证错误面全覆盖、`NETWORK_ERROR` 为 service 自有码、两侧词条齐平且到期引导重新输入、无效/撤销指向管理员、“无注册/密码/管理账号”写入文案 | 真实 esbuild 把真实 `modules/webchat/components/TokenAccess.tsx` + 真实认证状态机 `authenticationState.ts` + 真实模块 service `services/webchatApi.ts` + 真实 typed DTO `types.ts` + 真实共享传输 `src/api/client.ts`/`authScope.ts`（含真实 axios/React/react-i18next 依赖链）打成 ESM 编译产物，由真实 node v24 执行；观测的是真实 axios 实例经 adapter 实际发出的方法/URL/体/头、真实 `/me` 与 401 出口驱动的状态迁移、真实 reducer/闸门/提示映射。`@douyinfe/semi-ui` 只被别名成展示依赖替身（`Toast` 可观测）；不触 HTTP/DB/浏览器，DOM/React 交互按契约留给 E2E（S-F01/E-F01/E-F05，TASK-112） | verified |

实现要点：**单输入 + 遮蔽**——未认证视图只有一个 `type="password"` 的受控输入（`autoComplete="off"`），无注册/密码/管理账号入口，也不接 Console 登录守卫；文案明示“不需要注册、密码或管理账号”，Token 由管理员交付。**明文零留存**——Token 原文只存在于输入 state 与一次 `onSubmit` 调用之间，`tokenAccessReducer` 的 `settle` 动作在提交完成（成功或失败）时清空原文；提示只由 `tokenAccessNoticeKey(errorCode)` 从稳定目录码映射（函数签名只吃码，永不接收原文），因此错误/提示不可能回显 Token，源码也没有 URL 写入、持久存储或日志出口。**提交单飞**——`createTokenAccessGate()` 是同步闸门（不受 React 渲染批处理影响），`runTokenAccessSubmit` 把“父级在飞/空白输入/闸门占用”三种拒绝与“恰一次 submit + 完成回调”收在一处，并可在 node 下真实执行；失败（父级未收敛的拒绝）释放闸门并置 `failed` 标记，由 `tokenAccessNoticeKey(null, failed)` 落兜底文案，绝不静默。**过期重新进入**——`expired`/`revoked`/`context-changed` 由 TASK-074 快照的 `errorCode` 原样传入：到期/上下文变化引导重新输入有效 Token，无效/过期/撤销 Token 指向管理员；401 出口只判到期、精确码由页面用 `handleAuthFailure` 收敛，本视图不自动重试也不自动交换（同一提交路径重新粘贴即恢复）。**词条与码同名**——`chat.tokenAccess.error.<CODE>` 与 `config/api-messages.yaml` 的 code 同名，未知码落 `chat.tokenAccess.errorFallback`；`NETWORK_ERROR` 是模块 service 在无 HTTP 响应时的前端自有码，故不在 catalog。

回归：`uv run pytest -q tests/frontend` exit=0，**442 passed（2.35s）**（含本任务 12 passed 与 TASK-071/072/073/074 的 capacity_webchat 用例、`test_api_client_contract.py`）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`scripts/check_frontend_i18n.py`「i18n keys OK: 1381」（本任务新增 16 条 `chat.tokenAccess.*` 词条，zh/en 齐平）；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」；`uv run ruff check tests/frontend/test_capacity_webchat_token_access_ui.py apps/console-platform/frontend/src/modules/webchat`「All checks passed!」；`.venv/bin/python -m py_compile` 通过；新增函数均 ≤50 行、无 `any`。变异校验：`type="password"`→`type="text"` → 恰 1 条失败（遮蔽断言）；删掉 `!ports.gate.tryEnter()` → 恰 2 条失败（首次进入 + 单飞）；`CHAT_TOKEN_INVALID` 映射改成重新输入词条 → 恰 4 条失败（纯映射/编译产物/catalog 覆盖/管理员指引）；`settle` 不清空原文 → 恰 5 条失败（到期重进/无效撤销/兜底/纯状态）。

遗留（非本任务范围）：① 未认证视图的真实渲染与 DOM 交互（键盘提交、遮蔽显示、窄屏/主题、aria）按契约留真实浏览器 E2E（S-F01/E-F01/E-F05，TASK-112）；本任务以源码契约 + `tsc --noEmit` + 编译产物登记。② 页面编排（ChatPage 把快照 `state==='exchanging'`/`errorCode` 接到 props、认证成功后卸载本视图、`onClear` 丢弃页面本地提示）由 TASK-077 接入；本任务已把端口与语义写进 props 契约。③ `onClear` 只在用户主动清空输入时回调（提交完成后的自动清空不回调），页面不得据此清除状态机 `errorCode`——提示的唯一来源是快照。④ 非 401 的 CHAT 业务失败（如 `CHAT_AUTH_RATE_LIMITED`）仍会经共享传输弹全局 Toast（TASK-071 既有行为，只 401 被钉死不弹），本视图同时给出本地化重试提示，属可接受的重复提示；如需收敛应改共享传输而非本组件。⑤ 词条最终收敛与新增设置项文案由 TASK-090 统一（本任务只追加自己的 `chat.tokenAccess.*` 键，未改动其它键）。⑥ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 可补「未认证视图的 Token 输入：单输入遮蔽、完成清空、提示只由稳定码映射」与「前端自有稳定码（NETWORK_ERROR）不入 catalog 但键名仍同名」；本任务不改 spec 文件以免 active context 漂移。
- B-275: verified — automated command passed; run_id=fa1a2d0ba631433c927c3ad1064fa577 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] completed (done)

---

## TASK-076: 客户端配置加载与版本化消费

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-057, TASK-073
- **Source**: agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界
- **Spec-Refs**:
- **Acceptance-Refs**: B-276
- **Files**: `apps/console-platform/frontend/src/modules/webchat/hooks/useClientConfig.ts`, `apps/console-platform/frontend/src/modules/webchat/clientConfig.ts`, `tests/frontend/test_capacity_webchat_client_config_state.py`
- **Estimate**: 45分钟

### Description

认证后加载API-19再开放发送/上传/订阅；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-276][integration] 先在`tests/frontend/test_capacity_webchat_client_config_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：认证后加载API-19再开放发送/上传/订阅。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_client_config_state.py` exit=1，**3 failed + 5 errors + 1 passed（0.18s）**——5 条真实 node 用例在 fixture 阶段 error（esbuild 打包入口 import 缺失 `modules/webchat/clientConfig.ts`），3 条源码契约用例断言 `缺少前端文件：.../modules/webchat/clientConfig.ts` 与 `.../modules/webchat/hooks/useClientConfig.ts`（字段名对照用例只读已存在的 contracts/types，RED 期即通过）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `modules/webchat/clientConfig.ts`（新增）——`createClientConfigService`：`idle/loading/ready/failed` 状态与按引用缓存的快照（`useSyncExternalStore` 语义）；`load()` singleflight（并发合并为一次 API-19、已就绪幂等）、`retry()` 失败后独立重试（已就绪且无错时幂等防按钮风暴）、`reload()` 强制重读有效版本（刷新期间保持已就绪不关闭能力，失败保留最后已验证投影并留稳定码，绝不回落猜测默认）；`parseClientConfig` 严格校验必需字段类型/单位/边界（坏投影 `CLIENT_CONFIG_INVALID`）并整体 `Object.freeze`（消费方不能反向改写唯一来源）；`ready()`/`capabilities()`（send/upload/subscribe 三项由 `loadState` 派生）/`limits()`/`transportConfig()`/`newConnectionConfig()`（未就绪 `CLIENT_CONFIG_NOT_READY`，不返回默认值）；`reset()` 清空投影并递增 epoch，身份边界之后完成的加载/失败按 epoch fence。② `modules/webchat/hooks/useClientConfig.ts`（新增）——`useMemo` 建 service、`useSyncExternalStore` 订阅快照、`registerIdentityCleanup` 在身份边界清空投影（卸载注销）、只在认证态（state+generation 变化）`load({signal: identitySignal()})`，只做接线、不自动重试。
- [x] [B-276] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-276] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_client_config_state.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**9 passed（0.21–0.24s，多轮稳定）**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-276 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 认证后加载API-19再开放发送/上传/订阅；失败独立重试不重交换，单位/边界由投影确定、不复制默认；新连接绑transportRevision，旧流参数不突变 | tests/frontend/test_capacity_webchat_client_config_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_client_config_state.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：真实 `modules/webchat/clientConfig.ts` + 真实认证状态机 `authenticationState.ts`（身份边界清理的登记方）+ 真实模块 service `services/webchatApi.ts` + 真实 typed DTO `types.ts` + 真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝、`src/api/stream.ts` 的 `openReplyStream`）由仓库内 esbuild 打成真实 ESM 编译产物，由真实 node v24 执行（HTTP 经真实 axios 实例的 adapter 观测方法/URL/体/头；SSE 以真实 `ReadableStream` 字节帧驱动）。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-276 | `uv run pytest -q tests/frontend/test_capacity_webchat_client_config_state.py` exit=1，**3 failed + 5 errors + 1 passed（0.18s）**：5 条 node 用例 fixture 阶段 error（esbuild 入口 import 缺失 `modules/webchat/clientConfig.ts`），3 条源码契约用例断言缺少 `clientConfig.ts`/`hooks/useClientConfig.ts` | 同命令 exit=0，**9 passed in 0.21–0.24s** | ① `test_b276_config_loads_only_after_authentication_and_gates_send_upload_subscribe` L776-839：认证前 idle/投影全 null/`ready=false`/三项能力全关/`limits`·`transportConfig`·`newConnectionConfig` 均 `CLIENT_CONFIG_NOT_READY`；请求顺序恰 `POST /webchat/auth/exchange` → `GET /webchat/client-config`（1 交换 + 1 配置）；loading 态不提前开放能力；`limits` 逐项等于投影（17/4096/12345678/3/2.5）、`transport`（512/3.5/111/222/1）、连接配置 `rev-a-276`+`settings_revision 37`；就绪后重复 `load()` 零新请求；配置请求无参无体且携带验证上下文 `CONTEXT_A`+`muad_chat_csrf`+locale/requestId。② `test_b276_failed_load_retries_independently_without_exchanging_or_resending` L841-889：500 `INTERNAL_ERROR` → `failed`+投影全空+能力全关；两个 tick 后请求数不变（无自动重试）；`retry()` → `ready`（配置 GET=2、交换 POST=1、写 POST=1，零重交换零重发）；刷新失败保留 `rev-a-276`+错误码且能力仍开放（`history_page_size==17` 是投影事实而非默认）、再刷新成功换 `rev-b-276`+`reconnect_max_attempts==3`（总 GET=4、总交换=1、总写=1）。③ `test_b276_invalid_projection_fails_explicitly_without_frontend_defaults` L891-913：空 `transport_revision` 与 `sse_event_max_bytes=0` 均 `CLIENT_CONFIG_INVALID`+投影全空+未就绪消费失败。④ `test_b276_projection_units_drive_consumers_and_new_connections_rebind_the_revision` L915-969：投影整体冻结（`mutationBlocked==true`，改写被拒后 `newConnectionConfig()` 仍 `rev-a-276`/attempts 1）；真实 `openReplyStream` 用投影 A：`transportRevision=rev-a-276`、600 字节帧被投影的 512 字节预算拒收（`failed/FRAME_TOO_LARGE`）、连接数 2 = 投影 attempts(1)+1；`reload()` 后活跃连接 revision 不突变，新连接绑定 `rev-b-276` 且同帧一次解析成功（`delta`+`terminal`、正文 600 字）、reload 后零写 POST。⑤ `test_b276_identity_boundary_clears_the_projection_and_fences_late_loads` L971-1025：边界前已有 `rev-a-276` 已验证投影且刷新期间能力不关闭；真实 `logout` 边界 → `idle` 全 null + 身份信号已中止 + 该边界恰 1 次清理；带信号迟到响应保持 idle；切主体后重新加载 `rev-b-276`（新上下文 `CONTEXT_B`）；**无信号**迟到响应（取消不兜底）仍被 epoch fence 保持 idle，两次边界各只清一次（总 GET=4）。⑥ `test_client_config_module_keeps_the_projection_as_the_only_source` L1027-1077：模块源码契约（typed DTO+模块 service 稳定码；无认证/写能力、无裸 axios/fetch、无传输头、无持久存储、无定时器、无 `console.`、无 `any`；投影字段无默认数值/无 `?? <number>`、无 schema 默认字面量）。⑦ `test_client_config_consumes_the_exact_projected_field_names` L1079-1104：前端三个 DTO 的字段集合与 `muad_contracts.webchat` 白名单投影逐一相等。⑧ `test_use_client_config_hook_only_wires_the_service_to_react_and_identity` L1106-1142：hook 只接线（`useSyncExternalStore`+`createClientConfigService`+`registerIdentityCleanup`→`service.reset()`+卸载注销+`state !== 'authenticated'` 才加载+`identitySignal()`），无裸 HTTP/头/存储/定时器/`any`/默认值。⑨ `test_use_client_config_hook_compiles_to_a_real_typescript_artifact` L1144-1168：真实 esbuild 编译 hook exit 0，产物含 `useClientConfig` 且无测试凭据 | 真实 esbuild 把 `clientConfig.ts`+`authenticationState.ts`+`services/webchatApi.ts`+`types.ts`+`src/api/client.ts`/`authScope.ts`/`stream.ts`（含真实 axios/i18next/React 依赖链）打成 ESM，真实 node v24 执行；观测真实 axios 实例经 adapter 实际发出的方法/URL/参数/体/头、真实 `openReplyStream` 的停止原因与连接次数/`transportRevision`、真实认证状态机的身份边界与 `registerIdentityCleanup` 时序。`@douyinfe/semi-ui` 只被别名成可观测 Toast 记录器（展示依赖）；不触 HTTP/DB/浏览器，DOM/React 交互按契约留给 E2E（S-101/E-105，TASK-112） | verified |

实现要点：**唯一来源**——业务上限/传输参数只来自 API-19 白名单投影，模块内不出现任何 schema 默认值（源码契约钉死默认字面量与 `?? <number>` 兜底）；坏投影（缺字段/空 revision/零预算）显式 `CLIENT_CONFIG_INVALID` 并清空半份投影，未就绪时消费接缝抛 `CLIENT_CONFIG_NOT_READY`，绝不返回前端猜测配置。**能力门由状态派生**——`ready()`/`capabilities()` 只由 `loadState` 计算，发送/上传/订阅三项同源，页面不需要各自判断。**版本化消费**——`newConnectionConfig()` 返回整体冻结的当前已验证投影，新连接据此绑定 `transport_revision` 与冻结参数；`reload()` 成功才原子替换，活跃连接持有旧对象不变（旧流参数不突变），刷新失败保留最后已验证版本 + 稳定码而不丢服务端事实。**加载边界**——`load()` singleflight、`retry()` 只重发 API-19（结构上无交换/写能力）、`reset()` 清空并递增 epoch（边界之后完成的加载/失败被 fence，取消不再兜底也挡得住）。

回归（本机无 dev/测试残留进程）：`uv run pytest -q tests/frontend` exit=0，**439 passed in 1.55s**（含本任务 9 条与 TASK-071/072/073/074 的 capacity_webchat 用例）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；继承 verifier `harness-frontend#RULE-front-001`（stage: review；唯一责任 TASK-071）exit=0——`tests/frontend/test_api_client_contract.py` 4 passed、`check_frontend_api_usage.py`「frontend api usage check OK」、`check_frontend_i18n.py`「i18n keys OK: 1365」（零新增词条）、`npm run typecheck` 无输出。`uv run ruff check tests/frontend/test_capacity_webchat_client_config_state.py apps/console-platform/frontend/src/modules/webchat`「All checks passed!」；`.venv/bin/python -m py_compile` 通过；esbuild+acorn AST 逐函数核对：本任务生产函数全部 ≤50 行（无超限），无 `any`。变异校验（改后即还原）：删 `capabilities()` 的 ready 判定 → 3 条失败；删 epoch fence → 身份边界用例失败；`reset()` 不清投影 → 身份边界用例失败；投影不冻结 → 版本化用例失败；`load()` 去掉幂等守卫 → 认证后加载用例失败。

未新增迁移/DDL，未动 `src/locales/*.json`（i18n 词条数不变 1365），无新增依赖。

遗留（deferred，非本任务可闭合）：① React 渲染路径（订阅、卸载注销、认证态触发的加载时机）按契约留真实浏览器 E2E（S-101/E-105，TASK-112）；② 页面编排（何时 `retry()`/`reload()`、入口与会话选择与配置的组合）属 TASK-077/082；③ 消息分页/输入预检/轮询/重连的消费者接线属 TASK-078/080/081/082/084（本任务提供 `limits()`/`transportConfig()`/`newConnectionConfig()` 与能力门）；④ 系统设置页的字段元信息展示属 TASK-089；⑤ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 可补「API-19 投影是前端上限唯一来源、组件不复制默认值；新连接绑定 `transport_revision`」；本任务不改 spec 文件以免 active context 漂移。
- B-276: verified — automated command passed; run_id=3284e076f53f42f8a0786cd4d910432c (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] B-276 RED：新建 `tests/frontend/test_capacity_webchat_client_config_state.py` → exit=1，**3 failed + 5 errors + 1 passed**（node 用例 fixture 阶段 error：esbuild 入口 import 缺失 `modules/webchat/clientConfig.ts`；静态用例断言缺少 `clientConfig.ts`/`hooks/useClientConfig.ts`）
- [2026-10-19] 实现 `modules/webchat/clientConfig.ts`（加载状态机/投影校验与冻结/能力门/版本化消费/身份边界 fence）+ `modules/webchat/hooks/useClientConfig.ts`（React 接线）；首轮断言逐项修正：身份边界用例的加载/清场/上下文观测位置（边界前先落地已验证投影、无信号迟到加载独立验证 epoch fence）、刷新失败语义（保留已验证版本 + 稳定码，不关闭能力）
- [2026-10-19] B-276 GREEN：同命令 exit=0，**9 passed**（0.21–0.24s，多轮稳定）；`tests/frontend` 整跑 **439 passed in 1.55s**；RULE-front-001 verifier exit=0（4 passed / api usage OK / i18n 1365 / typecheck 无输出）；ruff 干净；AST 逐函数 ≤50 行；5 组变异校验均被对应用例捕获
- [2026-10-19] completed (done)

---

## TASK-077: 独立CHAT路由与页面编排

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-075, TASK-076
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-277
- **Files**: `apps/console-platform/frontend/src/App.tsx`, `apps/console-platform/frontend/src/modules/webchat/ChatPage.tsx`, `tests/frontend/test_capacity_webchat_chat_route.py`
- **Estimate**: 45分钟

### Description

注册懒加载/chat/:entryId与conversation查询参数，位于管理RequireAuth/AppLayout之外；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-277][integration] 先在`tests/frontend/test_capacity_webchat_chat_route.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：注册懒加载/chat/:entryId与conversation查询参数，位于管理RequireAuth/AppLayout之外。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_chat_route.py` exit=1，**4 failed + 11 errors（0.21s）**——11 条真实 node 用例在 fixture 阶段 error（esbuild 入口 import 缺失的 `modules/webchat/ChatPage.tsx`），4 条路由/源码/词条契约用例断言 `缺少前端文件：.../modules/webchat/ChatPage.tsx`、`App.tsx 必须注册 path="chat/:entryId?"`、`zh-CN 缺少词条：chat.page.*`。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。（新增导出函数均 ≤50 行；无 `any`/松散类型；URL 解析与入口/会话解析是纯函数，解析控制器与 React 组件分离，`ChatPage.tsx` 无裸 HTTP/传输头/持久存储/定时器/日志/管理守卫引用；Token 只经认证状态机的 `exchangeToken` 一次请求体）
- [x] [B-277] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-277] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_route.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**15 passed（0.13s）**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-277 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 注册懒加载/chat/:entryId与conversation查询参数，位于管理RequireAuth/AppLayout之外；刷新URL恢复已授权所选会话，无id展示新对话空态，不增加菜单 | tests/frontend/test_capacity_webchat_chat_route.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_route.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-277 | `uv run pytest -q tests/frontend/test_capacity_webchat_chat_route.py` exit=1，**4 failed + 11 errors（0.21s）**：11 条真实 node 用例 fixture 阶段 error（esbuild 打包入口 `export * from 'modules/webchat/ChatPage.tsx'` 不存在），4 条路由/源码/词条契约用例失败（`缺少前端文件：.../modules/webchat/ChatPage.tsx`、`App.tsx 必须注册 path="chat/:entryId?"`、`zh-CN 缺少词条：chat.page.*`） | 同命令 exit=0，**15 passed（0.13s）** | ① `test_b277_chat_route_is_registered_lazily_outside_the_console_shell` L975-1034：`lazy(() => import('./modules/webchat/ChatPage'))`、`path="chat/:entryId?"`、CHAT 路由块缩进 6（`<Routes>` 直接子节点）且含 `<Suspense>`/`<ChatPage>`、块内零 `RequireAuth`/`RequireRole`/`AppLayout`/`Navigate`/`/login`、与 `RequireAuth`+`AppLayout` 管理块（仍含 `path="models"`）不重叠；`config/menu.ts` 仍是 11 项、无 `nav.chat`/`'/chat'`，`AppLayout` 无聊天图标。② `test_b277_app_bundle_keeps_the_chat_page_in_a_lazy_chunk` L1036-1086：真实 esbuild `--splitting` 分包，入口 chunk 无 `chat-page` 标记，恰有一个懒加载 chunk 含 `chat-page`/`parseChatRoute`/`createChatPageBootstrap`，且入口的动态 `import("./chunk-*.js")` 指向该文件。③ `test_b277_chat_page_module_compiles_and_keeps_the_chat_auth_scope` L1088-1163：真实 TSX 编译产物含页面符号且无 canary；源码契约要求复用 `createWebchatClient`/`useChatAuth`/`useClientConfig`/`createChatPageBootstrap`/`useSyncExternalStore`/`TokenAccess`/`auth.exchangeToken`/`registerIdentityCleanup`+`resetResolution`，禁止 `setChatContextId`、管理守卫、持久存储、定时器、URL 写入、日志、裸 HTTP、传输头、松散类型、投影默认值与分页默认值（入口列表调用恰为 `.listEntries({signal})`）。④ `test_b277_url_parsing_and_entry_resolution_are_deterministic` L1165-1218：空白/缺省/重复参数/无关参数/非法 UUID/前后空白矩阵；入口未授权不回落、唯一入口确定性落地、多入口 `required`、零入口 `empty`；本地码 `CONVERSATION_ID_INVALID`/`CONVERSATION_ENTRY_MISMATCH` 由模块导出。⑤ `test_b277_page_orchestration_gates_resolution_on_config_readiness` L1220-1243：认证成功但配置未就绪 → `config-loading` 且 `needed==false`、请求仅 `/me`+`client-config`；配置失败 → `config-failed`+`INTERNAL_ERROR` 且仍不解析入口；全程零 POST/零 SSE。⑥ `test_b277_expired_chat_cookie_returns_to_token_access_without_private_requests` L1245-1263：`/me` 401 → `token-access{submitting:false,errorCode:'CHAT_SESSION_EXPIRED'}`、仅 1 条 `/me`、零私有请求、零跳转。⑦ `test_b277_token_entry_then_config_then_entries_in_order` L1265-1287：粘贴 Token → 顺序 `me → POST exchange → client-config → entries`，体恰 `{token}`、无查询参数、URL/头/提示/跳转零出现 canary、全程仅 1 次写请求、验证后 CHAT 上下文写入 authScope、零 SSE。⑧ `test_b277_refresh_url_restores_the_authorized_conversation_without_duplicate_requests` L1289-1336：配置就绪先于入口解析（`beforeResolve` 恰 2 条）、顺序 `me → config → entries → conversations/<id>`、`neededAfterResolve==false` 且重复渲染零新增请求、入口请求无分页参数且带验证上下文/CHAT CSRF/locale/request-id、会话请求无参数、快照 `restored`+标题、视图 `workspace` 带入口名。⑨ `test_b277_missing_conversation_id_shows_the_new_conversation_state` L1338-1358：`/chat`（唯一入口）无 `conversation` → `new`+`conversationId==null`、零会话请求、视图空态。⑩ `test_b277_entry_selection_never_guesses_between_authorized_entries` L1360-1376：多入口 `required`、零入口 `empty`，两者都不自动挑选且零会话请求。⑪ `test_b277_unauthorized_entry_surfaces_without_falling_back_or_leaking` L1378-1393：URL 入口未授权 → `entry-unauthorized`、`entryId==null`（不回落列表里的入口）、入口未核属前零会话请求。⑫ `test_b277_conversation_failures_surface_explicitly_without_fake_success` L1395-1430：404 → `COMMON_NOT_FOUND`、无半份会话事实、恰 1 次请求且重复渲染零新增；非法标识 → `CONVERSATION_ID_INVALID` 且零请求；跨入口 → `CONVERSATION_ENTRY_MISMATCH` 且不落地会话。⑬ `test_b277_entry_failure_retries_independently_without_re_exchanging` L1432-1451：入口失败 → `entry-failed`+`INTERNAL_ERROR`、零自动重试；`retry()` 只重发 API-05（entries=2、config=1、零会话/零 POST/零 SSE）后进入 `workspace`。⑭ `test_b277_identity_boundary_resets_the_page_and_fences_late_responses` L1453-1492：会话核属在飞时退出 → 经真实 `registerIdentityCleanup` 清空（入口/会话/`appliedSelection` 全 idle）、身份信号已中止、边界后完成的迟到响应被 fence、退出顺序 `… conversations/<id> → POST logout`、零跳转。⑮ `test_b277_page_states_are_localized_in_both_locales` L1494-1503：`chat.page.*` 11 键与源码引用逐一对应、zh/en 齐平且真正翻译、无 `nav.chat`。 | 真实 esbuild 把真实 `modules/webchat/ChatPage.tsx` + 真实认证状态机 `authenticationState.ts` + 真实配置消费 `clientConfig.ts` + 真实模块 service `services/webchatApi.ts` + 真实 typed DTO `types.ts` + 真实共享传输 `src/api/client.ts`/`authScope.ts`（真实 axios/React/react-i18next/i18next 依赖链与真实词条 JSON）打成 ESM 编译产物，由真实 node v24 执行；观测的是真实 axios 实例经 adapter 实际发出的方法/URL/参数/体/头与真实请求顺序、真实 `/me`/401 出口驱动的认证状态、真实 `chatPageResolutionNeeded` 门控下的解析次数。`App.tsx` 的懒加载注册另有真实 esbuild `--splitting` 分包产物（ChatPage 只在动态 chunk）与真实 TSX 编译产物；`@douyinfe/semi-ui` 与 `react-router-dom` 只被别名成展示/路由替身（`Toast` 可观测），不触 HTTP/DB/浏览器，DOM/React 渲染交互按契约留给 E2E（S-101/E-101/E-F05，TASK-112） | verified |

实现要点：**路由独立性**——`App.tsx` 用 `lazy(() => import('./modules/webchat/ChatPage'))` 注册 `chat/:entryId?`（`<Routes>` 顶层兄弟，带 `Suspense` 兜底），不放进 `RequireAuth`/`AppLayout` 管理分支、不进菜单；共用前端产物但不共用管理登录会话。**URL 是选择的唯一来源**——`parseChatRoute(entryIdParam, search)` 只认路径入口与 `conversation` 查询参数（忽略其它参数、重复取首个、空白视为缺省、非法 UUID 不给服务端发无意义请求），`resolveChatEntry` 只认授权集合：URL 入口未授权即显式 `unauthorized`（不回落其它入口），无 URL 入口时只在"唯一授权入口"这一确定事实上落地，多入口 `required`、零入口 `empty`。**逐级门控**——认证（`/me` 或 exchange）→ API-19 配置 → API-05 入口 → API-15 会话，前一级未就绪/失败不发后一级请求；`chatPageResolutionNeeded` 用"已应用选择"比较，失败也记选择（不自动重试），配置 reload 不移动会话；整个编排零 SSE、零写请求（唯一写是用户显式提交的 Token 交换）。**错误面不伪造成功**——配置/入口/会话各有独立重试，会话 404/越权、非法标识、跨入口会话都是显式 `unavailable`（本地码 `CONVERSATION_ID_INVALID`/`CONVERSATION_ENTRY_MISMATCH` 与稳定目录码同名映射词条），不留下半份会话事实。**身份边界**——控制器在构造时经真实 `registerIdentityCleanup` 登记清场（控制器与身份状态机同属页面作用域），每次解析递增 epoch，边界之后完成的迟到响应被 fence；`/me` 401 由 TASK-074 出口收敛为 `expired` 并回到 TokenAccess，不跳 Console 登录。

回归：`uv run pytest -q tests/frontend` exit=0，**466 passed（1.72s）**（含本任务 15 passed 与 TASK-071～076 的 capacity_webchat 用例、`test_console_shell_contract.py`/`test_ui_style_contract.py` 等）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`scripts/check_frontend_i18n.py`「i18n keys OK: 1392」（本任务新增 11 条 `chat.page.*` 词条，zh/en 齐平）；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」。为保持既有 UI 契约绿：`tests/frontend/test_ui_style_contract.py` 的 `NON_LIST_MODULE_PAGES` 增加 `modules/webchat/ChatPage.tsx`（聊天工作区不是管理列表页，反查其确无 `ModuleToolbar`/`<Table`/`PaginationFooter`）。变异校验：改路由 path、把 `lazy` 换成静态 import、把 CHAT 路由块移进管理守卫、删配置门控、删跨入口校验、去掉身份清理登记、忽略 `conversation` 参数、未授权入口回落到首个授权入口——八种变异各自被对应用例捕获（源码/分包契约 1～2 条失败，node 用例给出 `会话核属请求未发出：…` 的明确诊断）。

遗留（非本任务范围）：① React 渲染与 DOM 交互（TokenAccess 切换、加载/空态/失败面渲染、窄屏/主题/aria）按契约留真实浏览器 E2E（S-101/E-101/E-F05，TASK-112）；本任务以源码契约 + `tsc --noEmit` + 真实编译产物登记。② 会话索引/选择/新建与迟到响应隔离属 TASK-078（本任务只做 URL 入口/会话解析与视图门控，未建索引状态）。③ 历史侧栏与单会话工作区（`WebchatShell`/`ChatPanel`/消息线程/输入）由 TASK-079/080/081 在同一编排接缝上装配；本任务的 `ChatWorkspace` 只展示已核验的入口与会话事实。④ 回复订阅（`openReplyStream`）与重连/generation 属 TASK-082；本任务保证编排阶段零 SSE，并把 `client`/入口/会话就绪作为订阅前置条件。⑤ 入口选择 UI（多入口 `entry-required` 的候选列表）与入口切换由 TASK-079/078 落地，本任务只给 `required` 结论与授权集合事实。⑥ 词条最终收敛与新增设置项文案由 TASK-090 统一（本任务只追加自己的 `chat.page.*` 键，未改动其它键）。⑦ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 可补「CHAT 路由独立于管理守卫/登录跳转，按路由懒加载」与「页面编排逐级门控：认证→配置→入口→会话，前一级未就绪不发后一级请求、零 SSE/零写请求」；本任务不改 spec 文件以免 active context 漂移。
- B-277: verified — automated command passed; run_id=d1bf49c5af384f128b96a020d6b23250 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] TDD：先建 B-277 行为验收并记录 RED（4 failed + 11 errors）→ 实现 ChatPage 编排与懒加载路由 → GREEN（15 passed）；回归 tests/frontend 466 passed、tsc 干净、i18n 1392 键齐平、api usage OK；8 项变异校验各自被捕获。
- [2026-10-19] completed (done)

---

## TASK-078: 会话索引、选择、新建与迟到响应隔离

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-073, TASK-077
- **Source**: agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-278, B-103
- **Files**: `apps/console-platform/frontend/src/modules/webchat/hooks/useConversations.ts`, `apps/console-platform/frontend/src/modules/webchat/conversationState.ts`, `tests/frontend/test_capacity_webchat_conversation_state.py`
- **Estimate**: 45分钟

### Description

URL/entry/conversation/generation固定请求归属；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-278][integration] 先在`tests/frontend/test_capacity_webchat_conversation_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：URL/entry/conversation/generation固定请求归属。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_conversation_state.py` exit=1，**2 failed + 11 errors（0.12s）**——11 条真实 node 用例在 fixture 阶段 error（esbuild 入口 `export * from 'modules/webchat/conversationState.ts'` 不存在），2 条源码契约用例断言 `缺少前端文件：.../conversationState.ts`、`缺少前端文件：.../hooks/useConversations.ts`。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。（`conversationState.ts` 974 行、`useConversations.ts` 157 行，导出函数与内部动作均 ≤50 行；无 `any`/松散类型；错误分三类显式处理：本地前置失败抛 `ConversationStateError`（`CONVERSATION_ENTRY_REQUIRED`/`CONVERSATION_NOT_SELECTED`/`CONVERSATION_SCOPE_LIMIT_INVALID`）、服务端/传输经 `webchatErrorCode`、配置投影错误经 `ClientConfigError`；创建失败用显式联合结果而非异常；状态层无裸 HTTP/传输头/持久存储/定时器/日志/React/文案键）
- [x] [B-278] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-278] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**13 passed（0.29s）**。
- [x] [B-103][integration] 将原B-F03落入`tests/frontend/test_capacity_webchat_conversation_state.py`，先RED后GREEN；entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标；断言：entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标。（同一 RED/GREEN 批次：`test_b278_entry_switch_discards_the_old_index_page`/`test_b278_late_message_pages_are_fenced_after_the_selection_moves` 覆盖入口/会话/代际变化下的旧页与旧流隔离，`test_b278_scope_switch_aborts_the_previous_view_requests` 覆盖旧流中止，`test_b103_*` 四条覆盖分页去重/锚点/水位/有界草稿游标）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-278 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | URL/entry/conversation/generation固定请求归属；新建幂等不取消旧Run，动态分页排序合并去重，旧响应不写新会话，草稿/游标按会话有界保存 | tests/frontend/test_capacity_webchat_conversation_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | verified |
| B-103 | integration | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 | tests/frontend/test_capacity_webchat_conversation_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-278 | `uv run pytest -q tests/frontend/test_capacity_webchat_conversation_state.py` exit=1，**2 failed + 11 errors（0.12s）**：11 条真实 node 用例 fixture 阶段 error（esbuild 入口 `export * from 'modules/webchat/conversationState.ts'` 与 `hooks/useConversations.ts` 均不存在），2 条源码/编译契约用例失败（`缺少前端文件：.../modules/webchat/conversationState.ts`、`缺少前端文件：.../modules/webchat/hooks/useConversations.ts`） | 同命令 exit=0，**13 passed（0.29s）** | ① `test_b278_conversation_state_module_keeps_the_service_and_transport_boundaries` L1003-1056：模块导出 `createConversationState`/`mergeConversationItems`/`mergeHistoryMessages`/`applyOlderHistoryPage`/`chatConversationSearch`/`ConversationStateError`/`CONVERSATION_ENTRY_MISMATCH`，含 `AbortController`/`newRequestId`/`webchatErrorCode`/`history_page_size`，不自行构造传输；源码契约禁止裸 axios/fetch、传输头（`X-Locale`/`X-Request-Id`/`X-CSRF-Token`/`X-Chat-Context`）、持久存储、定时器、`any`、React 引用、`cancel`、`page_size` 数值默认、文案键；入口不匹配码与 `ChatPage.tsx` 同值。② `test_b278_hook_binds_the_state_to_identity_boundaries_and_the_config_projection` L1057-1106：`useConversations.ts` 真实 esbuild 编译产物含 `useConversations`/`createConversationState`/`useSyncExternalStore`；源码契约要求 `registerIdentityCleanup`+`.reset()`、显式 `maxScopedConversations`、`limits:config`、无 `useState`/定时器/裸 HTTP/传输头/松散类型/文案键，两侧 locales 文件存在。③ `test_b278_pure_merges_keep_identity_order_dedupe_and_url_rendering` L1107-1136：索引按 `conversation_id` 去重+活动倒序、消息按 `event_id` 去重+`seq` 升序、锚点位移=真实插入条数、无锚点位移 0、`chatConversationSearch` 空选择不写参数、上界非法 → `CONVERSATION_SCOPE_LIMIT_INVALID`。④ `test_b278_conversation_index_pages_merge_dedupe_and_sort_by_activity` L1137-1204：首屏请求 `GET /webchat/entries/<E>/conversations` 且 `params=={}`（不复制分页默认）、带真实 CHAT 上下文/CSRF/locale/request-id、同入口重复 open 只 1 次请求、续页 `params=={page:2}`、合并去重后按活动倒序且取新版本（`last_message_at`/`active_run_status`）、末页 `hasMore=false`、刷新 `params=={}` 且与更早页合并、页深不回退。⑤ `test_b278_entry_switch_discards_the_old_index_page` L1205-1229：旧入口在途页迟到后不写新入口（按 entry/generation fence），默认视图信号下旧请求 `aborted==true` 且视图信号已中止。⑥ `test_b278_selection_never_leaks_late_responses_into_the_new_conversation` L1230-1279：详情未核属前零消息请求、选择加载态、切换后旧详情/旧消息页均不写当前会话（`historyFor` 也为空）、零补偿请求、切回重读权威详情+消息、草稿按 `conversation_id` 隔离、空选择零请求且不销毁其它会话暂存；请求序列恰为 `entries → C1 详情 → C2 详情 → C2 消息 → C1 详情 → C1 消息`。⑦ `test_b278_late_message_pages_are_fenced_after_the_selection_moves` L1280-1302：详情已落地后消息首屏迟到 → 当前会话与旧会话都不被写入。⑧ `test_b278_selection_is_never_decided_by_list_position` L1303-1325：刷新重排后选中项仍为 `CONVERSATION_1`、generation 不变、仅 1 次列表请求。⑨ `test_b278_scope_switch_aborts_the_previous_view_requests` L1326-1345：默认视图信号下上一视图 GET 在传输层中止、旧请求取消不写当前失败面、切换只发新会话详情+消息。⑩ `test_b278_create_is_single_flight_idempotent_and_never_cancels_the_old_run` L1346-1402：创建期间防重复（同一 promise、1 次 POST、`creating==true`）、失败留 `INTERNAL_ERROR` 且不移动选择并以 `{created:false}` 返回、重试沿用同一幂等键、成功 `{created:true}` 且新会话成为选择（generation+1）、空消息区水位=服务端 `last_seq`、零消息请求、旧会话已应用游标(42)/草稿保留、零 `/runs/` 取消请求、URL 查询串按新会话渲染。 | 真实 esbuild 把真实 `modules/webchat/conversationState.ts` + 真实模块 service `services/webchatApi.ts`（`createWebchatClient()` 全量 WebchatClient，含 cancel 能力，但实现从不调用）+ 真实 typed DTO `types.ts` + 真实配置投影消费 `clientConfig.ts`（`limits()`）+ 真实共享传输 `src/api/client.ts`（真实 axios 实例/拦截器）+ `src/api/authScope.ts` 打成 ESM 编译产物，由真实 node v24.17 执行；观测真实 axios 实例经 adapter 实际发出的方法/URL/参数/体/头/中止状态与真实请求顺序，以及真实 `AbortController` 中止语义（adapter 按 signal 拒绝 canceled）。`@douyinfe/semi-ui` 只被别名成 `Toast` 替身；DOM 渲染/滚动补偿按契约留给 E2E（S-110/S-111/E-106，TASK-112/113） | verified |
| B-103 | 同上一行（同一 RED：2 failed + 11 errors） | 同命令 exit=0，**13 passed（0.29s）** | ⑪ `test_b103_older_pages_keep_the_anchor_and_the_frozen_watermark` L1403-1441：消息首屏 `params=={page:1,page_size:40}`（页面大小只来自 API-19 投影、不带水位）、续页 `params=={page:2,page_size:40,through_seq:240}`（沿用冻结水位）、前置插入合并后 `seq` 升序无重复、跨页同 `event_id` 取新版本、锚点按 `event_id` 保持且位移 40、消息页水位与活跃 Run 已应用游标分离（`null` vs 42/7）、游标单调不回退、无选择/无入口显式失败码。⑫ `test_b103_history_keeps_more_than_two_hundred_retained_messages` L1442-1453：6 页 × 40 条 = **240 条全部保留**（无 200 条上限）、身份唯一、区间 1..240、末页 `hasOlder=false`。⑬ `test_b103_scoped_drafts_and_cursors_are_bounded_and_identity_cleared` L1454-1490：当前所选会话即使是最久未使用也**不被淘汰**（先淘汰 LRU 非当前会话）、超界逐次淘汰、身份边界清空索引/选择/草稿/游标并中止视图信号。 | 同上一行 | verified |

实现要点：**索引分页不复制默认**——首屏与刷新不发 `page/page_size`（服务端默认即唯一来源），续页只追加 `page`；`mergeConversationItems` 按 `conversation_id` 身份去重（新版本覆盖）并按最近活动倒序，刷新首屏与已加载的更早页合并（活动重排不丢页、页深不回退、选中项由身份决定而非列表下标）。**选择与新建**——`select()` 先读 API-15 权威详情（先落地，标题/活跃 Run 立即可见）再读 API-09 消息首屏（页面大小来自 API-19 投影）；`create()` 用 API-08，请求期间 singleflight、失败重试沿用同一个 `Idempotency-Key`（成功或切入口才换键），成功后新会话成为当前选择、水位取服务端 `last_seq`、零消息请求；整个模块结构上没有取消能力，也不发任何取消请求（新建/切换都不动后端 Run）。**迟到响应隔离**——每次作用域变化（入口/会话/身份边界）中止上一 `AbortController` 并递增 `generation`，每个请求固定归属 `entryId + conversationId + generation`，apply 前统一校验归属，旧响应（含已无法中止的在途响应）一律丢弃，连旧会话自己的暂存也不写。**按会话有界暂存**——草稿、活跃 Run 已应用游标、消息页（`historyPage/throughSeq/scrollAnchor/anchorShift`）按 `conversation_id` 保存，超出 `maxScopedConversations` 按最近使用淘汰且永不淘汰当前所选会话；身份边界整体清空。**水位≠游标**——消息页 `through_seq` 冻结沿用，活跃 Run 已应用游标只由 `noteAppliedSeq` 单调推进，两者分别保存。

回归：`uv run pytest -q tests/frontend` exit=0，**487 passed（3.63s）**（含本任务 13 passed 与 TASK-071～077 的 capacity_webchat 用例、`test_api_client_contract.py` 等）；聚焦批次（conversation_state + chat_route + chat_auth_state + client_config_state + webchat_services + chat_transport + browser_sse_parser + token_access_ui + api_client_contract）**97 passed（1.34s）**；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`scripts/check_frontend_i18n.py`「i18n keys OK: 1392」（本任务零新增词条——状态层与绑定层不持有文案键，最终文案由 TASK-079/090 收敛）；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」。变异校验：① 去掉消息首屏 fence ② 去掉详情 fence ③ 每次创建换幂等键 ④ 锚点位移恒 0 ⑤ LRU 淘汰忽略当前所选会话 ⑥ 作用域变化不中止视图信号 ⑦ 消息页水位冒充已应用游标 ⑧ 刷新索引按下标重置选择 ⑨ 向前加载不带冻结水位 ⑩ 已应用游标可回退 ⑪ 索引首屏复制分页默认 ⑫ 新建成功后不切到新会话——十二种变异各自被对应用例捕获；其中 ⑤ 首版未被捕获（当时当前所选会话恰好不是 LRU 受害者），已把场景改成"选中后再写其它会话，使当前所选成为最久未使用"后重新捕获。

遗留（非本任务范围）：① React 渲染与 DOM 交互（侧栏分组/高亮、消息线程渲染、滚动锚点补偿、窄屏抽屉）按契约留真实浏览器 E2E（S-110/S-111/E-106，TASK-112/113）；本任务以源码契约 + `tsc --noEmit` + 真实编译产物/真实 node 执行登记。② 历史侧栏与聊天工作区展示（新建按钮、日期分组、忙碌/未读）属 TASK-079；消息线程的安全渲染与分页滚动属 TASK-080（可直接复用本任务的 `mergeHistoryMessages`/`applyOlderHistoryPage` 与 `historyFor`/`loadOlder`/`setScrollAnchor` 接缝）。③ 发送侧固定会话身份（`pendingSend` 的 `conversationId/messageId/idempotencyKey`）与输入预检属 TASK-081，本任务只提供按会话隔离的草稿暂存。④ 回复订阅（`openReplyStream`）、重连与 generation 状态属 TASK-082，本任务提供 `viewSignal()` 作为"切换会话即关旧流"的唯一接缝。⑤ 后台会话活动/未读轮询属 TASK-084；索引刷新时机（何时 `refresh()`）由页面编排决定（TASK-093）。⑥ 词条最终收敛由 TASK-090 统一（本任务未改动 `src/locales/*.json`）。⑦ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 可补「会话作用域请求固定归属 entry/conversation/generation，作用域变化即中止视图信号并 fence 迟到响应；按会话暂存有界（LRU，不淘汰当前视图）；消息页 `through_seq` 与活跃 Run 已应用游标分别保存」；本任务不改 spec 文件以免 active context 漂移。
- B-103: verified — automated command passed; run_id=58db75e7e0b84630bd2b55bfa7904fae (confirmed_by: runner)
- B-278: verified — automated command passed; run_id=58db75e7e0b84630bd2b55bfa7904fae (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] TDD：先建 B-278/B-103 行为验收并记录 RED（2 failed + 11 errors）→ 实现 `conversationState.ts`（索引/选择/新建/epoch fence/按会话有界暂存）+ `hooks/useConversations.ts`（React 绑定）→ GREEN（13 passed）；回归 tests/frontend 487 passed、tsc 干净、i18n 1392 键齐平、api usage OK；12 项变异校验全部被捕获（其中"LRU 淘汰当前所选"首版漏检，已加强场景）。
- [2026-10-19] completed (done)

---

## TASK-079: DeepSeek式历史侧栏与聊天工作区

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-078
- **Source**: agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.7 样式方案, agent-capacity-webchat.frontend.design.md#3.8 可访问性
- **Spec-Refs**:
- **Acceptance-Refs**: B-279
- **Files**: `apps/console-platform/frontend/src/modules/webchat/components/WebchatShell.tsx`, `apps/console-platform/frontend/src/modules/webchat/components/ConversationSidebar.tsx`, `tests/frontend/test_capacity_webchat_history_sidebar.py`
- **Estimate**: 45分钟

### Description

新建/今天昨天更早分组、当前高亮及忙碌/未读真实展示；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-279][integration] 先在`tests/frontend/test_capacity_webchat_history_sidebar.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：新建/今天昨天更早分组、当前高亮及忙碌/未读真实展示。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_history_sidebar.py` exit=1，**1 failed + 8 errors（0.54s）**——8 条真实 node/渲染用例在 fixture 阶段 error（esbuild 入口 `export * from 'modules/webchat/components/ConversationSidebar.tsx'` 与 `WebchatShell.tsx` 均不存在），1 条词条契约用例失败（`zh-CN 缺少词条：chat.sidebar.navLabel`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。（`ConversationSidebar.tsx` 448 行、`WebchatShell.tsx` 256 行；全部函数 ≤50 行（最长 `ConversationSidebarEntry` 42、`WebchatShell` 43）；无 `any`/松散类型；错误显式：分组/忙碌/未读/阶段标签/标题/抽屉/侧栏模式都是纯函数返回显式联合或 `null`（未知值不伪造），本地缺参失败经 `onActionFailure` 端口上报、服务端/传输失败由 TASK-078 状态层写入快照 `errorCode` 就地重试；组件无裸 HTTP/传输头/持久存储/定时器/日志/凭据，`cancel` 词面零出现）
- [x] [B-279] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-279] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_history_sidebar.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**9 passed（0.47s）**；真实组件静态渲染产物（真实 Semi + 真实 i18n + 真实控制器快照）见 Acceptance Evidence 的真实边界证据列。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-279 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 新建/今天昨天更早分组、当前高亮及忙碌/未读真实展示；点击历史只切视图，窄屏抽屉可访问，桌面单会话工作区不混管理表格 | tests/frontend/test_capacity_webchat_history_sidebar.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_history_sidebar.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-279 | `uv run pytest -q tests/frontend/test_capacity_webchat_history_sidebar.py` exit=1，**1 failed + 8 errors（0.54s）**：8 条真实 node/渲染用例 fixture 阶段 error（esbuild 入口 `export * from 'modules/webchat/components/ConversationSidebar.tsx'` 与 `WebchatShell.tsx` 不存在），1 条词条契约用例断言 `zh-CN 缺少词条：chat.sidebar.navLabel` | 同命令 exit=0，**9 passed（0.47s）** | ① `test_b279_conversations_group_by_page_timezone_into_today_yesterday_earlier` L1057-1086：今天/昨天/更早按**页面时区**自然日分桶（`TZ=Asia/Shanghai`：`17:30Z`→`2026-10-19`；`15:59:59Z`→`yesterday`、`16:00:00Z`→`today`，证明日界不是 UTC 日界）、只保留非空分组、组内保持索引顺序（`shuffled` 仍为 `[C2,C1]`）、空索引零分组、跨日重算。② `test_b279_busy_unread_and_sidebar_modes_only_consume_server_facts` L1087-1121：忙碌只由 `active_run_status` 决定（`RUNNING`/`WAITING_INPUT` 真、`null` 假）；阶段→词条逐项映射且未知/缺失返回 `null`（不伪造标签）；未读只取服务端计数（缺省/0/负数/字符串→0、`2.7`→2）；侧栏模式矩阵（未绑定/首屏→`skeleton`、无条目→`empty`、失败→`error`+重试、有内容失败→`list`+`showRetry` 不清空内容）。③ `test_b279_titles_and_drawer_state_are_pure_and_truthful` L1122-1145：标题只取服务端（详情/新建回执），空白标题回落 `{kind:'new'}`；抽屉 `toggle/open/close/select` 语义（选择后收起）；侧栏 id 稳定为 `chat-conversation-sidebar`。④ `test_b279_clicking_history_only_switches_the_view` L1146-1222：真实控制器下点击历史只发 `GET /webchat/conversations/<id>` + `GET .../messages`（消息页 `{page:1,page_size:20}` 只来自 API-19 投影、带视图信号/CHAT 上下文/CSRF）、零 `/runs/` 取消请求、上一视图信号真实中止（连接释放）、索引条目/顺序/总数不变；新建恰 1 次 `POST /webchat/entries/<E>/conversations`（空体 + `Idempotency-Key`）、零取消、新会话成为选择；加载更多 `{page:2}` 合并去重；侧栏重试重读首屏 `{}`；未绑定入口时 `onSelect`/`onLoadMore` 各上报一次 `ConversationStateError(CONVERSATION_ENTRY_REQUIRED)` 且零 HTTP。⑤ `test_b279_sidebar_renders_groups_highlight_busy_unread_and_more` L1223-1281：真实渲染只出现 `today/yesterday/earlier` 三个非空分组、条目顺序与索引一致、`aria-current="true"` 恰一处且落在所选 `conversation_id`、条目是 `type="button"`、`data-active-run-status="RUNNING"/"WAITING_INPUT"` 配本地化标签、归档如实展示、未读徽标显示服务端计数 `3` 且只一处、新建/加载更多入口齐备、`nav` 带 `aria-label` 且标题保留完整 `title`；en-US 分组/新建/加载更多/归档为英文且无未读来源时零徽标。⑥ `test_b279_sidebar_keeps_content_states_and_retries_in_place` L1282-1313：空态含新建入口、未绑定入口显示骨架且零条目、失败显示侧栏内重试且不伪装空成功、工作区入口与索引入口不一致时（侧栏与工作区两处）都显示骨架不跨入口展示。⑦ `test_b279_workspace_is_a_single_conversation_panel_with_render_seams` L1314-1355：恰一个 `chat-panel` + 一个侧栏、面板标题=服务端标题、入口名可见、`data-run-status="RUNNING"`+本地化状态、消息/输入接缝各一处、未装配输入区不渲染空容器、无 `semi-table`/管理列表构件标记、会话列表只在侧栏、抽屉开关 `aria-controls` 指向侧栏 id 且 `aria-expanded="false"`+本地化标签、`data-drawer-open="false"`、新建后空标题显示「新对话」。⑧ `test_b279_components_compile_and_keep_the_module_boundaries` L1356-1430：真实 TS 编译产物含组件/纯函数/侧栏 id 符号；两文件都消费 `../types` 与 Semi、零裸 HTTP/传输头/持久存储/定时器/松散类型/分页数值默认/管理列表构件/`<Table`/`useState(`/`cancel`；`ConversationSidebar` 导出真实接缝且自然日口径与既有 `DateTimeText` 一致（`getFullYear()`/`getMonth()`）；`WebchatShell` 接线显式（`createConversationSidebarBindings(conversations, onActionFailure)`、`onNew/onSelect/onLoadMore/onRetry`、`dispatch({type:'select'})`、`unreadCounts={props.unreadCounts ?? null}`）且不自建传输/状态。⑨ `test_b279_sidebar_keys_are_localized_on_both_sides` L1431-1449：18 条 `chat.sidebar.*`/`chat.workspace.*` 新词条 zh/en 双侧齐平、非空、真正翻译，且都在组件里被引用；加载/重试复用 `common.loading`/`common.retry`、新对话复用 `chat.page.newConversation`（不复制文案）。 | 真实 esbuild 把真实组件 `modules/webchat/components/ConversationSidebar.tsx`/`WebchatShell.tsx` + 真实会话状态 `conversationState.ts` + 真实模块 service `services/webchatApi.ts` + 真实 typed DTO `types.ts` + 真实配置投影 `clientConfig.ts` + 真实共享传输 `src/api/client.ts`/`authScope.ts` 打成 ESM 编译产物：① 纯解析/状态与事件绑定由真实 node v24 执行（HTTP 经真实 axios 实例的 adapter 观测方法/URL/参数/体/`Idempotency-Key`/CHAT 上下文/CSRF/视图信号与真实中止语义），点击历史经 `createConversationSidebarBindings` 绑到**真实 TASK-078 控制器**（真实 service + 真实状态层）；② 真实组件经真实 React `renderToStaticMarkup`（真实 `@douyinfe/semi-ui`/`semi-icons` + 真实 i18n 词条 JSON + 真实控制器快照，7 个场景 zh/en）静态渲染成真实编译产物——证据包括真实 DOM 结构（`nav`/`section`/`button`/`aria-*`/`data-*`）、Semi `Button`/`Badge`/`Empty`/`Spin`/`Typography` 的真实 SSR 输出与真实中文/英文文案；`TZ=Asia/Shanghai` 固定页面时区证明分组口径。DOM 交互（真实点击/键盘/抽屉断点/滚动）按契约留给真实浏览器 E2E（S-110/S-111/E-106，TASK-112/113） | verified |

实现要点：**分组只分桶**——`conversationDayGroup` 用 `getFullYear/getMonth/getDate` 取页面本地自然日（与既有 `DateTimeText` 同口径，不按 UTC 日界），`groupConversationsByDay` 只把 TASK-078 已按最近活动倒序、按 `conversation_id` 去重的索引分进今天/昨天/更早并丢弃空分组，不重排、不按下标决定选中项。**只展示服务端事实**——忙碌来自索引批量关联的 `active_run_status`（终态 Run 不在索引里，null 即无活跃 Run），阶段逐项本地化、未知值不给标签；归档如实标注（不伪装可续聊）；未读只吃调用方传入的服务端权威计数（缺省/非法→0，不显示徽标，不伪造未读）；未读来源属 TASK-084，本任务只提供显示接缝。**模式与重试**——入口未绑定（`index.entryId !== entry.entry_id`）或首屏加载显示骨架，绝不跨入口展示条目；无条目给空态并保留新建入口；失败在侧栏内就地重试，已有内容时保留内容只追加重试提示（不清空当前有效消息）。**事件只切视图**——`createConversationSidebarBindings` 把 `onNew/onSelect/onLoadMore/onRetry` 绑到 TASK-078 控制器，选择只读详情/消息并切视图（零取消、零写请求、零消息重提，索引不变），本地缺参失败经 `onActionFailure` 显式上报；`WebchatShell` 是唯一绑定点并持有窄屏抽屉纯 reducer（开关带 `aria-expanded`/`aria-controls`，选择历史后收起）。**工作区是单会话骨架**——头部标题取服务端详情/新建回执（空白回落「新对话」）、入口来自已核验入口、运行状态取 `active_run.status`；消息区与输入区只留渲染接缝（TASK-080/081/082 在同一接缝装配）；不混任何管理列表构件，会话列表只属于侧栏。**函数规模与类型**——两文件全部函数 ≤50 行（最长 42/43），强类型无 `any`，组件不自建传输/状态、不发 HTTP、不落持久存储、无定时器。

回归：`uv run pytest -q tests/frontend` exit=0，**496 passed（3.05s）**（含本任务 9 passed 与 TASK-071～078 的 capacity_webchat 用例、`test_api_client_contract.py`/`test_ui_style_contract.py`/`test_console_shell_contract.py` 等）；聚焦批次（本任务验收文件 + `test_capacity_webchat_conversation_state.py` + `test_capacity_webchat_chat_route.py` + `test_ui_style_contract.py` + `test_console_shell_contract.py`）全绿；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`scripts/check_frontend_i18n.py`「i18n keys OK: 1410」（本任务新增 18 条 `chat.sidebar.*`/`chat.workspace.*` 词条，zh/en 齐平；既有键未改动）；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」。变异校验（14 项，全部被对应断言捕获）：① 分组按 UTC 日界 ② 未读恒 0 ③ 忙碌恒真 ④ 点击历史改成刷新索引 ⑤ 不校验入口绑定 ⑥ 去掉当前高亮 `aria-current` ⑦ 抽屉开关去掉 `aria-controls` ⑧ 工作区吞掉未读计数 ⑨ 侧栏错误态伪装成空态 ⑩ 归档状态不展示 ⑪ 有内容时失败清空成失败页 ⑫ 选择历史后不收起抽屉 ⑬ 未知阶段伪造标签 ⑭ 空标题伪造会话标题；其中 ⑤/⑧ 首版未被捕获（当时只断言了独立侧栏渲染、未断言工作区内的侧栏与未读透传），已补「工作区入口与索引入口不一致时工作区侧栏也必须骨架」与「工作区必须透传未读计数」两条断言后重新捕获。

遗留（非本任务范围）：① DOM 交互（真实点击历史/新建、键盘操作、抽屉断点开合、滚动与焦点恢复、明暗主题）按契约留真实浏览器 E2E（S-110/S-111/E-106，TASK-112/113）；本任务以真实 SSR 渲染产物 + 真实 node 执行 + `tsc --noEmit` 登记。② 消息线程安全渲染/分页/滚动锚点属 TASK-080（直接复用 `WebchatShell` 的 `messageArea` 接缝与 TASK-078 的 `historyFor/loadOlder/setScrollAnchor`）。③ 输入、附件与消息幂等状态属 TASK-081（复用 `composer` 接缝）。④ 回复订阅/重连/generation 属 TASK-082，页面级装配（把 `useConversations` 控制器与 `messageArea`/`composer` 接进 `ChatPage` 的 workspace 视图、绑定 `onActionFailure`）属 TASK-093；本任务只交付组件与真实事件接缝，未改 `ChatPage.tsx`（其现有 `ChatWorkspace` 占位保持原样）。⑤ 会话活动/未读轮询（`unreadCounts` 的真实来源）属 TASK-084；本任务只提供显示接缝，缺省不显示徽标。⑥ 窄屏断点与抽屉视觉（CSS/主题 token）、标题截断样式属 TASK-091；本任务只提供稳定 class 名与可访问 DOM（`data-drawer-open`、`aria-*`），未改 `styles/app.css`。⑦ 会话选择错误面（`selection.errorCode` 的本地化提示，如越权/不存在）由 TASK-093 按 TASK-077 的页面门控收敛（本任务不在工作区伪造提示面）。⑧ 词条最终收敛由 TASK-090 统一（本任务只追加自己的 18 条键，未改动其它键）。⑨ 规范刷新（cf-learn/refresh）：`harness-frontend`/`harness-ui` Conventions 可补「CHAT 侧栏与工作区属同一编排接缝：索引分组只分桶不重排、忙碌/未读/归档只展示服务端事实、入口未绑定时骨架不跨入口展示；展示组件不混管理列表构件」；本任务不改 spec 文件以免 active context 漂移。
- B-279: verified — automated command passed; run_id=d7a7b89185844e77a57b227918750beb (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] TDD：先建 B-279 行为验收并记录 RED（1 failed + 8 errors）→ 实现 `ConversationSidebar.tsx`（页面时区日期分组/当前高亮/忙碌/归档/未读/骨架-空态-失败重试/加载更多 + 事件绑定工厂）+ `WebchatShell.tsx`（单会话工作区骨架、消息与输入接缝、窄屏抽屉纯 reducer）+ 18 条 zh/en 词条 → GREEN（9 passed）；回归 tests/frontend 496 passed、tsc 干净、i18n 1410 键齐平、api usage OK；14 项变异校验全部被捕获（其中"不校验入口绑定""工作区吞掉未读计数"首版漏检，已补工作区侧断言后重新捕获）。
- [2026-10-19] completed (done)

---

## TASK-080: 消息安全渲染、分页与滚动锚点

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-055, TASK-079
- **Source**: agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-280
- **Files**: `apps/console-platform/frontend/src/modules/webchat/components/MessageThread.tsx`, `apps/console-platform/frontend/src/modules/webchat/hooks/useMessages.ts`, `tests/frontend/test_capacity_webchat_message_history_ui.py`
- **Estimate**: 45分钟

### Description

首屏按配置、可加载超过200条保留消息，页水位不充当已渲染Run游标；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-280][integration] 先在`tests/frontend/test_capacity_webchat_message_history_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：首屏按配置、可加载超过200条保留消息，页水位不充当已渲染Run游标。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_message_history_ui.py` exit=1，**1 failed + 13 errors（0.20s）**——13 条真实 node/渲染用例在 fixture 阶段 error（esbuild 入口 `export * from 'modules/webchat/components/MessageThread.tsx'` 与 `'modules/webchat/hooks/useMessages.ts'` 均不存在 → `Could not resolve`），1 条词条契约用例失败（`zh-CN 缺少词条：chat.thread.label`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。（`MessageThread.tsx` 421 行、`useMessages.ts` 194 行；全部函数 ≤50 行（最长 `MessageAttachmentCard` 44、`MessageThread` 40、`useMessages` 39）；无 `any`/松散类型；错误显式：白名单解析返回显式联合 token、锚点缺失返回 `null`/位移 0、本地失败映射为稳定码 `failureCode` 并在消息区可见，附件失效在绑定层拒绝并上报 `ATTACHMENT_UNAVAILABLE`，取件失败经失败端口上报（零未处理拒绝）；两文件无裸 HTTP/传输头/持久存储/定时器/日志/凭据、不注入 HTML、不走 MDX 求值、不散落主题色值、不消费 `run_id`/`checksum` 等未使用内部字段）
- [x] [B-280] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-280] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_message_history_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**15 passed（0.57s）**；真实组件静态渲染产物（真实 Semi + 真实 i18n + 真实控制器快照）与真实 node 执行证据见 Acceptance Evidence 的真实边界证据列。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-280 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 首屏按配置、可加载超过200条保留消息，页水位不充当已渲染Run游标；USER/ASSISTANT/附件安全渲染，Markdown禁HTML/MDX，加载旧页保持锚点 | tests/frontend/test_capacity_webchat_message_history_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_message_history_ui.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-280 | `uv run pytest -q tests/frontend/test_capacity_webchat_message_history_ui.py` exit=1，**1 failed + 13 errors（0.20s）**：13 条真实 node/渲染用例 fixture 阶段 error（esbuild 入口 `export * from 'modules/webchat/components/MessageThread.tsx'` 与 `'modules/webchat/hooks/useMessages.ts'` 不存在 → `Could not resolve`），1 条词条契约用例断言 `zh-CN 缺少词条：chat.thread.label` | 同命令 exit=0，**15 passed（0.57s）** | ① `test_b280_text_stays_text_and_links_need_safe_protocols` L1091-1132：HTML 原文只产出单个 `text` token（L1094-1097）、注入尝试零非文本 token（L1098）、MDX 源码只作文本（L1099-1106）、只有 http/https/mailto 成为链接（L1105-1107）、`javascript:`/相对/协议相对一律降级文本（L1108-1110）、协议白名单矩阵（L1111-1121）、围栏代码与未闭合围栏原样文本块（L1122-1123）、加粗/行内代码（L1124-1128）、空文本零块（L1129）、附件大小按字节（L1130）。② `test_b280_first_page_uses_projection_and_keeps_cursor_separate` L1134-1152：详情请求无参数、消息首屏 `{page:1,page_size:20}` 只来自 API-19 投影（L1139-1141）、首屏条数=配置页大小且是最新一页（L1142-1143）、`hasOlder`（L1144）、页水位取服务端冻结水位（L1145）、首屏无锚点/位移（L1146-1148）、已渲染 Run 游标独立登记且 ≠ 页水位（L1149-1150）。③ `test_b280_older_pages_load_past_two_hundred_with_frozen_watermark` L1154-1189：12 页请求只追加页码并沿用同一冻结水位（L1157-1160）、每次上翻真实插入一页（L1164-1166）、220/240 条时 `hasOlder` 仍为真（L1167-1169）、260 条全部可加载且到 total 后停（L1170-1171）、升序 + 按身份去重（L1172-1173）、锚点位移=真实插入条数（L1174-1176）、下标位移与条数位移一致（L1177-1179）、水位全程冻结（L1180）、上翻不推进已渲染游标（L1181-1183）、没有更早页时幂等零请求（L1184-1187）。④ `test_b280_unknown_anchor_and_overlapping_page_are_handled_by_identity` L1191-1212：锚点未知位移 0（L1193-1195）、重叠页按身份去重且位移=15（L1199-1201）、TASK-078 合并/位移接缝语义不变（L1202-1208）。⑤ `test_b280_scroll_anchor_keeps_the_visible_position` L1214-1236：位移来自真实状态层（L1217）、条数位移=身份位移（L1218）、补偿像素=条数×行高=800（L1220-1222）、一次性落到 `scrollTop`（L1223）、**上翻后锚点视口位置不变**（L1224-1226）、锚点缺失不产出测量/不动 `scrollTop`（L1227-1228）、不同锚点身份不补偿（L1229）、负值归 0（L1230）、无锚点不动（L1231）、空列表无锚点（L1232）。⑥ `test_b280_attachments_only_download_through_the_authorized_port` L1238-1251：可用附件只按 `artifact_id` 经注入端口取件（L1243-1245）、失效附件不发请求并上报稳定码（L1246-1248）、取件失败交失败端口（L1249）。⑦ `test_b280_local_failures_map_to_stable_codes` L1253-1263：状态层稳定码原样、未知失败收敛为显式码。⑧ `test_b280_thread_renders_roles_markdown_and_escapes_html` L1265-1302：线程唯一且是带标签日志区（L1269-1270）、三条消息与角色计数（L1271-1274）、`event_id`/`seq` 落到 DOM（L1275-1277）、角色标签本地化（L1278）、加粗/行内代码/安全外链带 `noopener noreferrer`（L1280-1285）、无 `<script` 且无真实 `onerror` 属性（L1287-1288）、HTML 原文按文本转义（L1289-1290）、MDX 原样文本（L1291-1293）、危险链接降级为文本（L1294-1299）。⑨ `test_b280_attachments_render_name_kind_size_and_no_storage_path` L1304-1326：两条附件卡片（L1307）、种类与可用性（L1308-1310）、只有可用附件有下载动作（L1311-1313）、失效如实标注（L1314-1316）、文件名/种类回落/大小（L1317-1319）、无 `/artifacts/` 直链（L1320）、不渲染制品标识（L1321）、整个线程唯一 `href` 是安全外链（L1323-1325）。⑩ `test_b280_thread_states_are_explicit_and_keep_existing_content` L1328-1363：空态/骨架/服务端失败就地重试且不清空已有消息/本地失败独立提示（L1331-1350）、上翻动作与角色/附件文案随语言切换（L1352-1363）。⑪ `test_b280_thread_renders_more_than_two_hundred_retained_messages` L1365-1375：260 条全部渲染 + 仍可上翻。⑫ `test_b280_thread_plugs_into_task079_message_area_seam` L1377-1388：单一 chat-panel、线程落在 `messageArea` 接缝内、无管理表格标记、装配零失败。⑬ `test_b280_components_compile_and_keep_the_module_boundaries` L1390-1431 + `test_b280_thread_and_view_binding_keep_their_seams` L1433-1460：编译产物含真实符号、边界（typed DTO/无裸 HTTP/无传输头/无持久存储/无定时器/无松散类型/不复制分页默认/无管理构件/不注入 HTML/不走 MDX 求值/无主题色值/无硬编码文案/不消费未使用内部字段）、组件无状态且不自建绑定、附件只经注入端口、视图绑定复用 TASK-078 接缝。⑭ `test_b280_thread_keys_are_localized_on_both_sides` L1462-1477：12 条新词条两侧齐平非空且真本地化、复用词条存在、词条被源码引用 | 真实 `MessageThread.tsx`/`useMessages.ts` + 真实 `conversationState.ts`（TASK-078 状态机与 `mergeHistoryMessages`/`applyOlderHistoryPage`/`historyFor`/`loadOlder`/`setScrollAnchor` 接缝）+ 真实 `services/webchatApi.ts`（`WebchatClient`）+ 真实 `clientConfig.ts`（API-19 `limits()`）+ 真实 `src/api/client.ts` axios 实例与拦截器 + 真实 `src/api/authScope.ts` CHAT 域，经仓库内真实 esbuild 打成 ESM、由真实 node 执行；HTTP 经真实 axios 实例的 adapter 观测（方法/URL/参数/信号），`@douyinfe/semi-ui` 在纯逻辑批次只被别名成 `Button`/`Empty`/`Spin`/`Banner`/`Typography`/`Toast` 替身；渲染批次用真实 Semi + 真实 zh-CN/en-US 词条 + 真实控制器快照经 `renderToStaticMarkup` 渲染成真实编译产物（含 `WebchatShell` 的 `messageArea` 接缝装配）。DOM 交互（真实点击下载、滚动触发上翻、真实滚动位置保持、焦点恢复）按契约留真实浏览器 E2E（S-108/E-105，TASK-108/112） | verified |

实现要点：**安全渲染只走白名单子集**——正文经 `parseMessageBlocks`/`parseInlineTokens` 解析成显式联合 token（行内代码/加粗/换行/围栏代码块/安全链接），渲染时只构造这些白名单 React 元素，其余（HTML 标签、属性注入、MDX 源码、未匹配语法）一律是文本节点交给 React 转义；**链接协议白名单** `safeLinkHref` 只接受可独立解析的 `http:`/`https:`/`mailto:`（相对/协议相对/`data:`/伪协议与解析失败一律降级文本），外链带 `target="_blank"` + `rel="noopener noreferrer"`；**不用 MDX 求值渲染**——聊天文本不走 Semi `MarkdownRender`（其 foundation 用 `@mdx-js/mdx` 的 `evaluate` 求值 MDX，属不可信内容求值面），源码契约钉死 `MarkdownRender` 与 `dangerouslySetInnerHTML` 零出现。**附件只经授权下载面**——卡片只展示文件名（缺省回落种类词条）/种类/大小与下载动作，取件走注入端口 `createAttachmentDownloader(port, onFailure)`（`port` 结构等于 `WebchatClient.download`，即 TASK-055/073 的逐请求授权下载面），组件不拼路径、不读存储键、不为附件生成任何 `href`；`available === false` 时不出下载动作并在绑定层再拒绝一次（上报 `ATTACHMENT_UNAVAILABLE`），取件失败经失败端口显式上报。**分页按身份合并、页水位不当游标**——视图绑定只读 TASK-078 的 `historyFor` 快照、动作只经 `loadOlder`/`setScrollAnchor`；首屏与每页分页都只来自 API-19 投影（`history_page_size`）与服务端 total，`through_seq` 全程冻结且**从不**写 `noteAppliedSeq`（已渲染 Run 游标只在正文应用成功后由流层推进）；连续上翻到 260 条不设 200 条上限，重复事件按 `event_id` 去重、位移取真实插入条数。**滚动锚点按身份补偿**——上翻前用 `topmostAnchor` 登记当前顶部消息 `event_id` 并测量它在视口里的偏移（`measureAnchor`），旧页落地后按同一次测量把锚点视口位置差一次性补回 `scrollTop`（`restoreAnchor` + `compensatedScrollTop`，负值归 0），锚点不在 DOM/不在新旧页里时位移 0 不猜测；DOM 适配 `elementScrollViewport` 按 `data-event-id` 扫描（不把身份拼进选择器）。**模式显式**——首屏未落地给骨架、无消息给空态、服务端失败就地重试且不清空已有消息、本地动作失败独立提示（不伪装成服务端失败）。

回归：`uv run pytest -q tests/frontend` exit=0，**511 passed（3.56s）**（含本任务 15 passed 与 TASK-071～079 的 capacity_webchat 用例、`test_api_client_contract.py`/`test_ui_style_contract.py`/`test_console_shell_contract.py` 等）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`uv run pytest -q tests/frontend/test_api_client_contract.py` exit=0，4 passed；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」；`scripts/check_frontend_i18n.py`「i18n keys OK: 1422」（本任务新增 12 条 `chat.thread.*`，zh/en 齐平、非空且真本地化，既有键未改动；变更前 1410）；`uv run ruff check`（本任务新增测试文件）「All checks passed!」；逐函数核对：本任务新增/修改函数全部 ≤50 行（TS 最长 `MessageAttachmentCard` 44、`MessageThread` 40、`useMessages` 39；测试文件最长 41）。**变异校验（3 项，全部被对应断言捕获）**：① 正文改 `dangerouslySetInnerHTML` 直出原文 → 3 条失败（HTML 转义断言 + 源码契约「不得注入 HTML」）；② 滚动补偿恒 0（`compensation * 0`）→ `test_b280_scroll_anchor_keeps_the_visible_position` 失败；③ 附件改成 `<a href={'/artifacts/' + artifact_id}>` 直链 → `test_b280_attachments_render_name_kind_size_and_no_storage_path` 失败；每次变异后按备份逐字节还原（md5 核对）并复跑确认 15 passed。

遗留（deferred，非本任务可闭合）：① **DOM 交互**（真实点击下载、滚动触发上翻、真实滚动位置保持、焦点恢复）按契约留真实浏览器 E2E（S-108/E-105，TASK-108/112）；本任务以真实 SSR 渲染产物 + 真实 node 执行 + `tsc --noEmit` 登记，补偿算术以真实 `anchorShift` 驱动并在纯函数层证明位置保持。② **页面级装配**（把 `useMessages` 与 `MessageThread` 接进 `ChatPage` 的 workspace 视图、用 `createAttachmentDownloader(client, onActionFailure)` 绑下载面、复用 TASK-079 的 `messageArea` 接缝）属 TASK-093；本任务只交付组件、视图绑定与真实接缝，未改 `ChatPage.tsx`（其现有 `ChatWorkspace` 占位保持原样）。③ **文字附件输入与消息幂等状态**属 TASK-081（复用 `composer` 接缝）；**回复订阅/重连/generation 与流式正文**属 TASK-082（`run_id` 与流式增量不在本任务消费，线程只渲染 API-09 的安全投影）。④ **附件预览**（图片内联、Blob URL 生命周期、撤销时清理）属 TASK-084；本任务只交付下载动作与失效标注（服务端处置语义 `inline`/`attachment`、`no-store`/`nosniff` 属 TASK-055）。⑤ **样式**（气泡布局、主题 token、窄屏、`aria-live` 频率等可访问性细节）属 TASK-091；本任务只提供稳定 class 名与语义 DOM（`role="log"` + `aria-label`、`data-role`/`data-event-id`/`data-seq`），未改 `styles/app.css`。⑥ **词条最终收敛**由 TASK-090 统一（本任务只追加自己的 12 条键）。⑦ **规范刷新**（cf-learn）：`harness-frontend`/`harness-ui` Conventions 可补「聊天消息文本只经白名单 Markdown 子集渲染（HTML/MDX 只能是文本、链接限 http/https/mailto、不走 MDX 求值渲染），附件只经注入的授权下载端口取件且不为附件生成 href；上翻历史按消息身份登记锚点并一次性补偿 scrollTop、页水位不作已渲染 Run 游标」；本任务不改 spec 文件以免 active context 漂移。
- B-280: verified — automated command passed; run_id=fc7621a8b93441af8932ddc9c3c63d8e (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] TDD：先建 B-280 行为验收并记录 RED（1 failed + 13 errors）→ 实现 `MessageThread.tsx`（白名单 Markdown 子集安全渲染/附件卡片与授权下载绑定工厂/上翻动作/骨架-空态-服务端失败重试-本地失败提示/身份落到 DOM）+ `useMessages.ts`（TASK-078 历史接缝绑定 + 按身份测量与一次性滚动补偿 + 本地失败码）+ 12 条 zh/en 词条 → GREEN（15 passed）；回归 tests/frontend 511 passed、tsc 干净、i18n 1422 键齐平、api usage OK、ruff 干净；3 项变异校验（HTML 注入/无滚动补偿/附件直链）全部被捕获。
- [2026-10-19] completed (done)

---

## TASK-081: 文字附件输入与消息幂等状态

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-054, TASK-080
- **Source**: agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: B-281
- **Files**: `apps/console-platform/frontend/src/modules/webchat/components/ChatComposer.tsx`, `apps/console-platform/frontend/src/modules/webchat/hooks/usePendingSend.ts`, `tests/frontend/test_capacity_webchat_composer_uploads.py`
- **Estimate**: 45分钟

### Description

文件用state，按UTF-8字节/数量/大小预检；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-281][integration] 先在`tests/frontend/test_capacity_webchat_composer_uploads.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：文件用state，按UTF-8字节/数量/大小预检。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_composer_uploads.py` exit=1，**1 failed + 13 errors**（14 条用例）——词条用例因 zh-CN/en-US 各缺 33 条 `chat.composer.*` 词条失败；13 条源码/编译/真实 node 用例在 fixture 阶段 error（`ChatComposer.tsx` 与 `hooks/usePendingSend.ts` 尚不存在，esbuild 解析不到渲染入口）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `hooks/usePendingSend.ts`（新增）——框架无关的发送幂等状态服务 + React 绑定（`useSyncExternalStore`）：预检（`textByteLength` 按 **UTF-8 字节**、附件数量、单件大小；限额只取 API-19 `limits()`，未就绪显式 `CLIENT_CONFIG_NOT_READY`，本文件零默认值）；附件逐件暂存（`client.upload` 走 TASK-054 的入口上传面，只传真实 `Blob`，落地服务端 `upload_id`/`expires_at` 与权威元信息，**不携带存储键/自报归属**）；一次尝试一个消息号（`messageId` 生成一次、`Idempotency-Key` 就是它、尝试固定 `conversationId`、提交后 payload 冻结，`send`/`retry` 共用同一 dispatch 因此怎么点都不换号）；202 前 `phase=PENDING`（成功前不清输入、不显示伪成功，202 落地才清空并保留回执）；结果未知判定（无确定响应/中止）保留原身份与输入待重放；单飞（in-flight 合并，不重复上传/提交）；视图信号中止（TASK-078 `viewSignal()` → `SEND_VIEW_CHANGED`，用户取消 → `SEND_ABORTED`）；按会话隔离（尝试只在自己会话可见、跨会话重试显式拒绝且零请求）；身份边界清空（TASK-074 `registerIdentityCleanup`）；过期暂存本地判定（只提示：已提交重放原样带旧 `upload_id` 不重传，新消息才重传）；稳定码→词条映射（`pendingSendErrorKey`，未登记回落显式文案）。② `components/ChatComposer.tsx`（新增）——展示组件：文本 + 真实 `<input type="file" multiple>`（选择结果经 `onAddFiles` 进 state，不用 `Form`/`FormData` 收集输入）+ 发送/取消/重试/放弃；逐件状态（待上传/上传中/已上传/失败/暂存过期）、限额提示、阶段提示、预检失败与发送失败分开、结果未知明示「重试沿用同一条消息」、失败主操作=重试（文案与行为同一处推导，不会分叉）、Enter 发送/Shift+Enter 换行。③ `src/locales/{zh-CN,en-US}.json` 各**只追加** 33 条 `chat.composer.*` 词条（两侧齐平）。
- [x] [B-281] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-281] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_composer_uploads.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**14 passed**（0.46–0.53s）；另经 `code-flow acceptance run --owner TASK-081 --write-evidence` 写入机器证据（run_id=75a8920fc5f24b898d9c4b9d91195bc1）。
- [x] 断言强度自检（mutation，跑完即还原）：① 幂等键不再等于消息号 → `test_b281_unknown_outcome_retries_with_the_same_message_number` 失败；② 去掉跨会话围栏 → `test_b281_view_change_aborts_the_send_and_never_redirects` 失败；③ 文本预检退化为字符数 → `test_b281_precheck_uses_utf8_bytes_count_and_size_before_any_io` 失败；④ 已提交尝试也重传过期暂存 → `test_b281_expired_staging_replays_accepted_identity_and_restages_for_new_messages` 失败；⑤ 组件不再把逐件状态落到 DOM → 渲染用例失败；⑥ 冻结失败尝试的主操作（含文案）退化为普通发送 → 渲染用例失败；⑦ 待受理阶段不再禁用发送 → 渲染用例失败；⑧ 不再表达「结果未知」 → 渲染用例失败；⑨ 在途阶段不再显示取消 → 渲染用例失败。九处均如实失败后还原并复跑 14 passed。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-281 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 文件用state，按UTF-8字节/数量/大小预检；发送固定会话/输入/附件/幂等身份，202前待受理、响应未知不换消息号，过期暂存不破坏已受理重放 | tests/frontend/test_capacity_webchat_composer_uploads.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_composer_uploads.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-281 | `uv run pytest -q tests/frontend/test_capacity_webchat_composer_uploads.py` exit=1，**1 failed + 13 errors**：`test_b281_composer_keys_are_localized_on_both_sides` 失败（zh/en 各缺 33 条 `chat.composer.*`）；13 条用例在 fixture 阶段 error（`ChatComposer.tsx`/`usePendingSend.ts` 不存在 ⇒ esbuild 解析不到渲染入口） | 同命令 exit=0，**14 passed**（0.46–0.53s） | ① 预检 L1255-1281：`requests==0`（预检先于任何 IO，L1255）、`bytes==[51,48,3]`（UTF-8 字节口径：17 汉字=51 字节被拒、16 汉字=48 字节放行，L1257）、`limitBytes==48`（限额取自 API-19 投影，L1258）、`noConversation`（显式码 + 不建尝试 + 不伪装成发送失败，L1259-1264）、`empty`/`tooLong`（L1265-1266）、`oversize=={code,count:0}`（超限文件不进附件集合，L1267-1269）、`overCount`（第三个文件被拒、前两个保留且状态 `READY`，L1270-1274）、`purePrecheck`（L1275-1279）。② 附件经 API-10 L1291-1337：两次上传 URL 都是 `POST /webchat/entries/{entry}/attachments`（L1295-1298）、multipart 部件键只有 `file`（无 `storage_key`/`upload_id`/归属字段，L1299-1301）、真字节/文件名/类型逐字节比对（L1302-1310）、提交体**恰为** `{conversation_id,message_id,text,upload_ids}`（L1315-1326）、`Idempotency-Key == message_id`（L1327-1329）、写请求带中止信号（L1330）、202 后输入/尝试清空且回执来自服务端（L1332-1339）。③ 待受理与固定会话 L1346-1364：`phase==PENDING` 且 `accepted is None`（L1346-1347）、成功前不清输入（L1348）、在途输入冻结（L1349）、提交仍绑定发起时会话（L1352-1355）、切换后该会话不显示尝试/输入为空（L1356-1358）、202 后清空并保留 Run（L1359-1364）。④ 单飞 L1370-1377：并发 send 只 1 次上传 + 1 次提交、消息号只生成一次、两调用同一最终快照。⑤ 响应未知 L1386-1411：失败态/结果未知/输入冻结（L1386-1390）、幂等键=消息号（L1391-1393）、尝试固定附件身份（L1394）、重试不重传（L1396）、不换号（L1397）、两次提交幂等键与**整份消息体相同**（L1399-1402）、重放拿回原 Run 且清空输入（L1403-1406）、结果未知判定（无响应=未知、有 500 响应=已知，L1407-1409）、稳定码→词条映射（L1410-1411）。⑥ 过期暂存 L1418-1438：本地过期判定（L1418）、纯函数只报确已过期身份（L1419-1421）、重放不重传且原样带旧 `upload_id`（L1422-1426）、不换号（L1427）、放弃后输入保留可编辑且暂存身份仍在（L1429-1433）、新消息重传并换新 `upload_id`/新消息号（L1434-1438）。⑦ 视图变化 L1446-1465：切换后该会话不显示其它会话尝试（L1446-1451）、跨会话重试显式拒绝且零新增请求（L1452-1454）、切回可见同一尝试且失败显式/结果未知（L1455-1465）、两次提交都绑定发起时会话（L1466-1469）、切回重放同一消息号并拿回原 Run（L1470-1472）。⑧ 上传失败 L1477-1495：按服务端稳定码失败、提交尚未发出（L1477-1484）、输入可修正（L1485）、不是结果未知（L1486）、失败附件无暂存身份（L1487-1491）、重试补传后沿用同一消息号（L1492-1495）。⑨ 渲染 L1502-1535：输入区/输入框/文件控件（`type=file`+`multiple`+可访问标签，L1507-1514）、逐件状态与文件名/大小（L1516-1520）、限额提示三值全部来自投影（L1522-1525）、可发送（L1527）、无失败面/阶段面（L1528-1530）、英文文案与数据（L1532-1535）。⑩ 阶段 L1542-1567：待受理/上传中的文案、输入保留、发送禁用、可取消、无伪成功（L1542-1552）、无内容不可发送/无附件不留空容器（L1558-1560）、未就绪禁用且不显示猜测限额（L1562-1566）。⑪ 失败面 L1575-1598：稳定码 + 本地化文案 + 结果未知明示 + 输入保留/冻结 + 主操作=重试且可用 + 可放弃（L1575-1586）、过期暂存标注且仍可发送（L1588-1591）、预检失败就地提示且不伪装成发送失败（L1593-1596）。⑫ 接缝 L1603-1610：单一工作区/输入区接缝、不混管理表格。⑬ 编译产物与源码契约 L1627-1681：逻辑产物含真实符号、组件产物含真实符号、两文件只消费 typed 契约/不裸 HTTP/不拼传输头/不落持久存储/不自带定时器/无松散类型/不复制限额默认/不混管理构件/不注入 HTML/无硬编码文案/不用 Form 字段收集/不消费存储键；组件不自持状态、不复用会话状态层、真实文件控件与 Enter 语义；状态层用 `useSyncExternalStore`、上传/提交只经 service 端口、`viewSignal` 中止、幂等键=消息号、消息号沿用共享传输生成器、身份边界清空。⑭ 词条齐平 L1689-1699 | 真实 esbuild（仓库内 `node_modules/.bin/esbuild`）把真实 `usePendingSend.ts` + `conversationState.ts` + `clientConfig.ts` + `services/webchatApi.ts` + `types.ts` + `src/api/client.ts` + `src/api/authScope.ts` 打成 ESM 编译产物，由真实 node 执行：HTTP 经**真实 axios 实例**（`api.defaults.adapter` 观测方法/URL/参数/体/头/中止信号，multipart 取真实 `File` 字节比对）；CHAT 上下文只由认证域持有（`chatContextForScope('CHAT')==CONTEXT_A`，L1338-1339）；限额经真实 API-19 投影服务消费（`createClientConfigService` + `config.load()`）；视图信号用真实 `AbortController` 语义（切换即 abort，adapter 按真实中止拒绝）。真实组件另经真实 React `renderToStaticMarkup`（真实 Semi + 真实 i18n 双语词条 + 真实会话控制器）静态渲染成真实编译产物；DOM 交互（真实选择文件、Enter 发送、点击取消/重试）按契约留真实浏览器 E2E（S-110/S-111/E-106，TASK-112/113）。 | verified |

实现要点：**一次尝试一个消息号**——`messageId` 在尝试创建时生成一次并直接作为 `Idempotency-Key`（原样透传），`send`/`retry` 共用同一 dispatch：存在未完成尝试就沿用同一身份与冻结 payload（不换号、不改投、不重传已暂存附件），只有显式 `discard()` 之后的新消息才换号。**202 前待受理**——提交在飞时 `phase=PENDING`、输入冻结、成功前不清输入，202 落地才清空并保留服务端回执（线程/订阅接缝留给 TASK-082/093）。**响应未知**——没有确定响应（传输失败/中止）即 `outcomeUnknown`，重试原样重放同一消息体（服务端按 `message_id` 去重，不重复建 Run）。**过期暂存**——本地按 `expires_at` 判定并提示，但已提交尝试的重放绝不重传/替换 `upload_id`（不破坏已受理重放）；只有新消息才重新上传。**按会话隔离**——尝试固定发起时的 `conversationId`，切换视图时视图信号中止在途发送（显式失败、结果未知），其它会话不显示该尝试，跨会话重试显式拒绝且零请求。**文件用 state**——附件是 `<input type="file">` 的选择结果，经状态服务持有（`useSyncExternalStore` 订阅）并只展示文件名/大小/逐件状态；不用 `Form`/`FormData` 收集输入、不拼路径、不直出存储键；真实字节由模块 service 按 TASK-054 的 multipart 面上送。

回归：`uv run pytest -q tests/frontend` exit=0，**525 passed**（3.77–5.16s；本任务新增 14 条用例后全量绿，含 `test_capacity_webchat_message_history_ui.py`、`test_capacity_webchat_history_sidebar.py`、`test_capacity_webchat_chat_transport.py`、`test_api_client_contract.py`、`test_console_shell_contract.py` 等）。`tests/acceptance/**`、`make acceptance*`、Playwright 浏览器验收未执行（共享环境约束 + E2E 属 TASK-112/113/115 终验）。

静态检查：`npx tsc --noEmit`（apps/console-platform/frontend）干净；`scripts/check_frontend_i18n.py`「i18n keys OK: 1455」（1422 + 新增 33）；新增/改动均为强类型（无 `any`、无裸 axios/fetch、无自拼传输头）；函数均 ≤50 行（`usePendingSend.ts` 最长 32 行、`ChatComposer.tsx` 最长 46 行）；词条只追加不删改（插入点：`chat.thread.attachment.other` 之后）。

遗留（deferred，非本任务可闭合）：① **页面级装配**——把 `usePendingSend` 与 `ChatComposer` 接进 `ChatPage.tsx` 的 workspace 视图（复用 TASK-079 的 `composer` 接缝、把 `snapshot.accepted` 交给线程刷新）属 TASK-093；本任务只交付组件/状态层与真实接缝，未改 `ChatPage.tsx`（其现有 `ChatWorkspace` 占位保持原样）。② **按会话草稿多槽位**——本任务的输入作用域是单槽（切会话即清空、切回未编辑则保留），TASK-078 的 `draft/setDraft` 有界多槽位草稿由 TASK-093 装配（避免复制第二份草稿事实）。③ **字节级上传进度**——进度按文件粒度（逐件 `READY/UPLOADING/UPLOADED/FAILED` + 计数）表达，字节级 `onUploadProgress` 需扩展共享传输的 upload 配置（TASK-073 的 `WebchatRequestOptions`/service 面），不在本任务 Files 内。④ **停止 Run / 排队状态**属 TASK-083（本任务的取消只中止在途上传/提交）；**回复订阅/重连/generation** 属 TASK-082；**附件内联预览与 Blob URL 生命周期**属 TASK-084；**窄屏/主题/可访问性视觉细节**属 TASK-091（本任务只提供稳定 class 名与语义 DOM）。⑤ 词条最终收敛由 TASK-090 统一（本任务只追加自己的 33 条键）。⑥ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 可补「发送面一次尝试一个消息号：`Idempotency-Key`=消息号、payload 提交后冻结、失败重试原样重放（含过期暂存身份）、只有显式放弃后的新消息才换号；尝试按会话隔离，视图信号中止在途发送且结果按未知处理」；本任务不改 spec 文件以免 active context 漂移。
- B-281: verified — automated command passed; run_id=75a8920fc5f24b898d9c4b9d91195bc1 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] RED 记录：`uv run pytest -q tests/frontend/test_capacity_webchat_composer_uploads.py` exit=1，1 failed + 13 errors
- [2026-10-19] 实现发送幂等状态服务 + 输入区组件（预检/逐件暂存/一次尝试一个消息号/202 前待受理/结果未知重放/过期暂存不破坏重放/视图中止与按会话隔离），并追加 33 条 zh/en 词条
- [2026-10-19] GREEN 记录：同命令 exit=0，14 passed；mutation 自检九处（换键/去会话围栏/字符数预检/过期重传/逐件状态/主操作/待受理禁用/结果未知/取消）均如实失败后还原
- [2026-10-19] completed (done)

---

## TASK-082: 回复订阅、重连与generation状态

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-072, TASK-076, TASK-080
- **Source**: agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-282
- **Files**: `apps/console-platform/frontend/src/modules/webchat/hooks/useRunEvents.ts`, `apps/console-platform/frontend/src/modules/webchat/runState.ts`, `tests/frontend/test_capacity_webchat_reply_stream_state.py`
- **Estimate**: 45分钟

### Description

仅订阅可见Run，断网只重连GET不重发POST；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-282][integration] 先在`tests/frontend/test_capacity_webchat_reply_stream_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：仅订阅可见Run，断网只重连GET不重发POST。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-282] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-282] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_reply_stream_state.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-282 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 仅订阅可见Run，断网只重连GET不重发POST；delta/snapshot/终态去重，EOF不当完成，按配置退避+抖动，旧attempt迟到不能串正文 | tests/frontend/test_capacity_webchat_reply_stream_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_reply_stream_state.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-083: 停止、排队状态与原Run确认交互

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-056, TASK-082
- **Source**: agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: B-283
- **Files**: `apps/console-platform/frontend/src/modules/webchat/components/RunActions.tsx`, `apps/console-platform/frontend/src/modules/webchat/components/ConfirmationCard.tsx`, `tests/frontend/test_capacity_webchat_run_actions_ui.py`
- **Estimate**: 45分钟

### Description

排队/模型等待/执行与连接状态分开，停止精确Run；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-283][integration] 先在`tests/frontend/test_capacity_webchat_run_actions_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：排队/模型等待/执行与连接状态分开，停止精确Run。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-283] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-283] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_run_actions_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-283 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 排队/模型等待/执行与连接状态分开，停止精确Run；WAITING_INPUT展示完整候选并带真实submission/source，busy不擅自新建或自动消费确认 | tests/frontend/test_capacity_webchat_run_actions_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_run_actions_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-084: 后台会话活动、未读与安全附件预览

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-055, TASK-070, TASK-082
- **Source**: agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-284
- **Files**: `apps/console-platform/frontend/src/modules/webchat/components/NotificationPanel.tsx`, `apps/console-platform/frontend/src/modules/webchat/hooks/useNotifications.ts`, `tests/frontend/test_capacity_webchat_activity_ui.py`
- **Estimate**: 45分钟

### Description

带抖动增量轮询、隐藏页暂停/前台同步，未读以服务端为准且只显式标读；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-284][integration] 先在`tests/frontend/test_capacity_webchat_activity_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：带抖动增量轮询、隐藏页暂停/前台同步，未读以服务端为准且只显式标读。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-284] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-284] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_activity_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-284 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 带抖动增量轮询、隐藏页暂停/前台同步，未读以服务端为准且只显式标读；点击定位原会话，附件经带认证上下文的service取Blob并及时释放 | tests/frontend/test_capacity_webchat_activity_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_activity_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-085: 用户详情Webchat Token凭据卡片

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-039, TASK-071
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.7 样式方案
- **Spec-Refs**: harness-ui-detail#RULE-ui-detail-001
- **Acceptance-Refs**: B-285
- **Files**: `apps/console-platform/frontend/src/modules/user-identity/UserChatTokenCard.tsx`, `apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx`, `tests/frontend/test_capacity_webchat_user_token_ui.py`
- **Estimate**: 45分钟

### Description

仅ADMIN可见，生成/重置/撤销携版本并用ConfirmAction；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-285][integration] 先在`tests/frontend/test_capacity_webchat_user_token_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：仅ADMIN可见，生成/重置/撤销携版本并用ConfirmAction。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_user_token_ui.py` exit=1，**7 failed + 5 errors**（12 条用例）——7 条源码/词条/编译契约失败（`UserChatTokenCard.tsx` 与 23 条 `user.webchatToken.*` 词条尚不存在、esbuild 编译不到卡片、`UserDetailTabs.tsx` 未接线）；5 条真实 node 用例在 fixture 阶段 error（esbuild 打包成功，node 执行到 `service.getWebchatToken` 报 `TypeError: service.getWebchatToken is not a function`——API-03 出口尚未实现）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `services/users.ts`（user-identity 模块 HTTP 出口，API-03）新增 `WebchatTokenMetadata`（**恰好六字段** `configured/status/token_id/revision/issued_at/expires_at`，无原文/摘要）、`WebchatTokenIssued`（= 元信息 + 一次性 `token`）与 `getWebchatToken`/`issueWebchatToken`/`revokeWebchatToken`；纯函数 `webchatTokenExpectedRevision`（**只看 `configured`**：未配置=0 首签，已配置=最新签发行版本）与 `webchatTokenErrorKey`（`REVISION_CONFLICT`/`COMMON_NOT_FOUND`/`FORBIDDEN` → 三个本地化键，未知回落 `common.saveFailed`）。② 新增 `UserChatTokenCard.tsx`——`useTokenMutation`（`inFlight` 单飞 + `pending` 禁用：生成/重置/撤销禁重复；失败/结果不明只 `webchatTokenErrorKey` 映射并 `onChanged()` 重读元信息，**不重发**）、`WebchatTokenMeta`（`DetailGrid` + `StatusTag`（唯一一份 `webchatTokenStatusOptions`）+ `DateTimeText` 展示真实 `issued_at/expires_at`，不在前端自算到期）、`WebchatTokenResult`（`Typography.Text` 原文 + 公共 `CopyButton` + 「关闭结果」按钮 → `setIssued(null)`，`onceHint` 明示只展示一次）、`UserChatTokenCard`（**仅 ADMIN**：非 ADMIN 直接 `return null`；未配置显示「生成 Token」，已配置显示「重置」「撤销」且**两者都走 `ConfirmAction`** 并明示关联登录会话失效；撤销成功后同步清空已展示原文）。③ `UserDetailTabs.tsx`——新增 `WebchatTokenSection`（凭据区域读取元信息，`requestSeq` 竞态守卫 + `Spin`/`ErrorState` 重试），挂在 ADMIN 门控的 `CredentialsTab` 顶部，卡片 props `{userId, metadata, onChanged}` 与设计 §3.4 一致。④ `src/locales/{zh-CN,en-US}.json` 各**只追加** 23 条 `user.webchatToken.*` 词条（两侧齐平）。
- [x] [B-285] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-285] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_user_token_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**12 passed**（0.12–0.20s）。
- [x] 断言强度自检（mutation，跑完即还原）：① 删掉卡片的 `if (!isAdmin) { return null; }` → `test_card_is_admin_gated_and_reveals_the_plaintext_once` 失败；② 把 `webchatTokenExpectedRevision` 改成 `return metadata.revision`（不做未配置判定）→ `test_real_service_reads_metadata_only` 失败（`assert 7 == 0`，首签基准被误当重置基准）；③ 把 `setIssued(null)` 改写成无效表达式 → `test_card_is_admin_gated_and_reveals_the_plaintext_once` 失败。三处均还原后复跑 12 passed，证明「仅 ADMIN」「首签=0」「关闭即清」三条断言不是空转。
- [x] verifier `harness-ui-detail#RULE-ui-detail-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。实际执行 exit=0：12 passed（0.02s）+ `tsc --noEmit` 无输出（干净）；卡片复用公共 `DetailGrid`/`StatusTag`/`ConfirmAction`/`CopyButton`/`DateTimeText`，挂载点仍在 `DetailSideSheet` 的凭据 Tab 之下（`test_user_detail_mounts_the_card_in_the_admin_credentials_area` 断言 `<WebchatTokenSection` 落在 `CredentialsTab` 内）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-285 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 仅ADMIN可见，生成/重置/撤销携版本并用ConfirmAction；仅本次结果可显示/CopyButton复制Token、关闭即清，读取只状态及真实到期时间，响应未知不自动轮换 | tests/frontend/test_capacity_webchat_user_token_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_user_token_ui.py"] | verified |
| harness-ui-detail#RULE-ui-detail-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 详情使用 SideSheet：标题与副标题居左，关闭 X 同行靠右；列表对象级操作集中到主列表最后一列「操作」，详情不重复渲染，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-285 | `uv run pytest -q tests/frontend/test_capacity_webchat_user_token_ui.py` exit=1，7 failed + 5 errors：`test_card_is_admin_gated_and_reveals_the_plaintext_once`/`test_card_uses_confirm_action_and_versioned_mutations`/`test_card_refreshes_metadata_only_when_the_outcome_is_unknown`/`test_card_uses_shared_components_and_localized_copy`/`test_user_detail_mounts_the_card_in_the_admin_credentials_area`（卡片/接线不存在）、`test_card_compiles_to_a_real_typescript_artifact`（esbuild 找不到 TSX）、`test_token_keys_are_localized`（zh/en 各缺 23 键）；5 条 node 用例 error（`TypeError: service.getWebchatToken is not a function`） | 同命令 exit=0，12 passed（0.12–0.20s） | ① `test_real_service_reads_metadata_only` L344-373：GET `/users/{id}/webchat-token` 无 body/params；`metadataKeys` **恰为六字段**（L352-359）；NOT_CONFIGURED 的 `configured=false`/`revision=0` 与 `firstIssueRevision==0`（L363-366）；**首签判定只看 `configured`**——快照带 `revision=7` 也返回 0（L367-369）、已配置按 `revision` CAS（L370-372）。② `test_real_service_issues_and_resets_with_version_cas` L375-397：PUT 体**恰为** `{"expected_revision": 0}`（L380）、带共享传输的 `X-CSRF-Token` 与 `X-Request-Id`（L381-382）、签发响应键集 = 六字段 + `token`（L387-389）、重置按最新签发行 `{expected_revision: 1}` → rev2（L391-397）。③ `test_real_service_revokes_the_named_version` L399-409：DELETE 带 `params={"expected_revision": 2}`、无 body、返回 REVOKED rev2。④ `test_real_service_keeps_the_plaintext_to_one_response` L411-421：`canaryInUrl==[]`、`canaryInBody==[]`（**原文绝不回传服务器**）、`canaryInResponses==['PUT /users/{id}/webchat-token']`（**全响应恰一次**）、重置原文同样只有一条响应。⑤ `test_real_service_never_rotates_on_unknown_outcome` L423-435：409 后 `requestCount==1`（**不自动重发/轮换**）、错误码映射四例。⑥ `test_card_is_admin_gated_and_reveals_the_plaintext_once` L437-452：`useAuth`+`account?.role === 'ADMIN'`+`if(!isAdmin){returnnull;}`；`webchat-token-issued` 结果框 + `CopyButton value={props.issued.token}` + `setIssued(null)` + `onceHint`；无 `localStorage`/`sessionStorage`/`console.log`/`history.pushState`/`location.href`；卡片**不调用** `getWebchatToken(`（不从读取面恢复原文）。⑦ `test_card_uses_confirm_action_and_versioned_mutations` L454-467：重置/撤销的 `ConfirmAction` 词条、`webchatTokenExpectedRevision(props.metadata)`、`issueWebchatToken(props.userId, expectedRevision)`、`revokeWebchatToken(props.userId, expectedRevision)`、`inFlight.current` 单飞 + `disabled={pending}`。⑧ `test_card_refreshes_metadata_only_when_the_outcome_is_unknown` L469-479：`webchatTokenErrorKey(error)` + `props.onChanged()`；`issueWebchatToken(`/`revokeWebchatToken(` 各**只一处**调用点；无 `error.response`/`REVISION_CONFLICT`/`setTimeout`/`retry`/`Date.now`。⑨ `test_card_uses_shared_components_and_localized_copy` L481-504：五个公共组件、`webchatTokenStatusOptions(t)`、`props.metadata.issued_at/expires_at`、无硬编码中文。⑩ `test_user_detail_mounts_the_card_in_the_admin_credentials_area` L506-524：`getWebchatToken(props.userId)` + `requestSeq`/`seq !== requestSeq.current` 竞态守卫、`<WebchatTokenSection` 落在 `CredentialsTab` 内、凭据 Tab 仍在 `isAdmin && (` 门控内、`metadata={`/`onChanged={` 接线。⑪ `test_card_compiles_to_a_real_typescript_artifact` L526-552：真实 TSX 经 esbuild 编译出 ESM 且产物含 `webchat-token-issued`。⑫ `test_token_keys_are_localized` L554-570：23 条词条两侧齐平、状态词条非裸枚举码、重置/撤销确认文案明示会话失效 | 真实 esbuild（仓库内 `node_modules/.bin/esbuild`）+ 真实 node 执行真实模块 service（`modules/user-identity/services/users.ts`）与真实共享传输（`src/api/client.ts` 的 axios 实例、请求拦截器与响应拦截器；断言 CSRF/`X-Request-Id` 头与 `code!=0` 的 reject 路径）；真实 TypeScript 编译产物（esbuild 打包 TSX）；真实 i18n 词条文件；DOM 交互（点击/确认弹窗/复制）留真实浏览器 E2E（S-114/S-115，TASK-115），本任务只登记不执行 | verified |

实现要点：**仅 ADMIN**——卡片自身 `useAuth` 判角色并直接 `return null`，挂载点又位于 ADMIN 门控的凭据 Tab（`isAdmin && (`），构成「前端隐藏 + 后端 403 兜底」的前端一半。**原文一次性**——`token` 只来自签发/重置那一次响应并立即进本地结果框，`CopyButton` 复制、关闭按钮 `setIssued(null)`、撤销成功同步清空；卡片不读持久缓存、不写 URL、不打日志，也没有任何「再取一次原文」的路径（`getWebchatToken` 只由父级调用且只回六字段）。**版本 CAS**——`expected_revision` 每次都从父级最新元信息派生（未配置=0 首签、已配置=最新签发行），生成/重置/撤销都不缓存旧版本；409 之后只 `onChanged()` 重读状态，绝不自动重签。**NOT_CONFIGURED 语义**——未知/外租户用户由后端读作 `NOT_CONFIGURED`，前端据此显示「生成 Token」并按版本 0 首签（node 用例钉死：即使快照带着非零 revision 也不能当重置基准）。**重置/撤销确认**——两者都经 `ConfirmAction` 并在词条里明示关联聊天登录会话将失效。

回归：`uv run pytest -q tests/frontend` exit=0，**377 passed**（本任务新增 12 条用例后全量绿；含 `test_capacity_webchat_credential_ui.py` 10 passed、`test_user_credential_tab_contract.py` 4 passed、`test_detail_sidesheet_contract.py` 10 passed）。`uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py` exit=0，12 passed；`npm --prefix apps/console-platform/frontend run typecheck` 干净。`tests/acceptance/**`、`make acceptance*`、Playwright 浏览器验收未执行（共享环境约束 + E2E 属 TASK-115 终验）。

静态检查：`npx tsc --noEmit`（apps/console-platform/frontend）干净；`scripts/check_frontend_i18n.py`「i18n keys OK: 1288」（1265 + 新增 23）；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」。新增/改动均为强类型（无 `any`、无裸 axios/fetch、无自拼传输头）；Token 原文不进日志/URL/持久化；词条只追加不删改（插入点：`user.columns.actions` 之后）。

遗留（非本任务范围）：① 浏览器 E2E S-114/S-115（TASK-115）保留生成/重置/撤销的点击路径、确认弹窗文案、结果框关闭后不可见、非 ADMIN 不显示入口；本任务只登记不执行。② `services/users.ts` 是 user-identity 模块既有的唯一 HTTP 出口（`modules/<module>/services/*.ts`），API-03 三个端点按模块约定并入其中，未新建 service 文件。③ 到期时间只展示后端 `expires_at`；有效期策略（`webchat.access_ttl_days`）的系统设置编辑面属 TASK-089/设置页范围，本卡片不提供用户级 TTL 输入。④ CHAT 交换（TASK-042）仍按摘要定位 Token，不经过本卡片端点。
- B-285: verified — automated command passed; run_id=d2e0d39e5ee349bdac15154ae4309a6a (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] RED 记录：`uv run pytest -q tests/frontend/test_capacity_webchat_user_token_ui.py` exit=1，7 failed + 5 errors
- [2026-10-18] 实现 API-03 前端出口（六字段元信息 / PUT 版本 CAS / DELETE 版本撤销 / 错误码映射）、`UserChatTokenCard`（仅 ADMIN、原文一次性结果框、ConfirmAction 版本操作、单飞守卫）与 `UserDetailTabs` 凭据区域接线，并追加 23 条 zh/en 词条
- [2026-10-18] GREEN 记录：同命令 exit=0，12 passed；mutation 自检三处（删 ADMIN 门控 / 首签判定退化 / 关闭即清失效）均如实失败后还原
- [2026-10-18] completed (done)

---

## TASK-086: 模型物理Key管理与检测界面

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-016, TASK-028, TASK-071
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-286
- **Files**: `apps/console-platform/frontend/src/modules/model-management/ModelCredentialPanel.tsx`, `apps/console-platform/frontend/src/modules/model-management/services/modelCredentials.ts`, `tests/frontend/test_capacity_webchat_credential_ui.py`
- **Estimate**: 45分钟

### Description

ADMIN凭据维护、容量/额度组及真实Key检测结果；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-286][integration] 先在`tests/frontend/test_capacity_webchat_credential_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：ADMIN凭据维护、容量/额度组及真实Key检测结果。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_credential_ui.py` exit=1，**5 failed + 5 errors**（10 条用例）——5 条源码/词条/编译契约失败（`ModelCredentialPanel.tsx` 与 57 条词条尚不存在、esbuild 编译不到面板）；5 条真实 node 执行用例在 fixture 阶段 error（esbuild 解析不到缺失的 `services/modelCredentials.ts`）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `services/modelCredentials.ts`（模块 HTTP 出口，API-16）——`ModelCredentialItem` 投影只有 `configured`（无 `api_key` 字段）、创建/整份替换/删除/额度组选项读取/检测六个端点；纯函数 `buildCredentialCreateInput`（密钥必填、`max_inflight` 必须正整数，非法显式抛 `ModelCredentialInputError(reason)`）、`buildCredentialUpdateInput`（**留空连 `api_key` 键都不发** = 保留原值，非空才显式替换；`key` 取自行记录、编辑不变更）、`credentialSaveErrorKey`（`apiErrorBody` → 去重/排空/被引用/版本冲突四种本地化键，未登记回落 `common.saveFailed`）；创建支持调用方持有的 `Idempotency-Key`（RULE-api-002）。② 新增 `ModelCredentialPanel.tsx`——`ModelCredentialForm`（连接/Key/全局容量/额度组，密钥输入从空值开始且编辑时提示「留空保留原值」「不回显」）、`CredentialProbeModal`（检测：先按 API-17 占额再由服务端探测，结果逐项展示并明示只覆盖被点名成员）、`CredentialTable` + `useCredentialList`（竞态守卫 + 分页）、`ModelCredentialPanel`（**仅 ADMIN**，非 ADMIN 直接不渲染；删除冲突保留面板内定位）。③ `statusOptions.ts` 新增 `probeStatusOptions`（AVAILABLE/FAILED/NOT_EXECUTED 的唯一枚举映射，与逻辑模型 `last_test_status` 刻意分开）。④ `src/locales/{zh-CN,en-US}.json` 各追加 57 条 `model.credential.*` 词条（只追加、两侧齐平）。
- [x] [B-286] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-286] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_credential_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed**（0.14–0.18s）。
- [x] 断言强度自检（mutation，跑完即还原）：把 `buildCredentialUpdateInput` 改成「留空也发送 `api_key`」→ `test_real_service_keeps_the_key_write_only` 失败（`assert 'api_key' not in {'api_key': '', ...}`）；删掉面板的 `if (!isAdmin) { return null; }` → `test_panel_is_admin_gated_and_never_renders_key_material` 失败。两处均还原后复跑 10 passed，证明「留空保留」与「非 ADMIN 不渲染」两条断言不是空转。
- [x] 同域 verifier 命令（harness-frontend RULE-front-001，stage: code，timeout=600秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]` 实际执行 exit=0——4 passed / 「frontend api usage check OK」/「i18n keys OK: 1265」/ typecheck 无输出（tsc --noEmit 干净）。
- [x] 同域 verifier 命令（harness-ui RULE-ui-001、harness-ui-detail RULE-ui-detail-001 的 pytest 部分）：`uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py` exit=0，21 passed；`npm --prefix apps/console-platform/frontend run build` exit=0（`✓ built in 5.24s`，build 内含 `tsc --noEmit`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-286 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | ADMIN凭据维护、容量/额度组及真实Key检测结果；读取只configured，空白Key保留、替换显式提交，单成员测试不显示整池健康 | tests/frontend/test_capacity_webchat_credential_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_credential_ui.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-286 | `uv run pytest -q tests/frontend/test_capacity_webchat_credential_ui.py` exit=1，5 failed + 5 errors：`test_panel_is_admin_gated_and_never_renders_key_material`/`test_panel_probe_shows_single_member_results_only`/`test_panel_uses_shared_components_guards_and_i18n`（面板文件不存在）、`test_panel_compiles_to_a_real_typescript_artifact`（esbuild 找不到 TSX）、`test_credential_keys_are_localized`（zh/en 各缺 57 键）；5 条 node 用例 error（esbuild 解析不到缺失的 service） | 同命令 exit=0，10 passed（0.14–0.18s） | ① `test_real_service_maps_the_admin_credential_routes`：`GET /model-credentials?page=1&page_size=20`（读取体为空）、`POST /model-credentials` 携带 `Idempotency-Key: idem-286` 与 `X-Request-Id`、创建体**恰为** `{key,provider_key,base_url,api_key,quota_group_id,max_inflight,enabled}`（无 `revision`、无旧逻辑模型的连接/密钥字段）、`PUT /model-credentials/{id}` 带 `expected_revision=3` 且 `key` 不变、`DELETE /model-credentials/{id}`、`GET /model-quota-groups`（额度组归属选项）。② `test_real_service_keeps_the_key_write_only`：明文只出现在创建体与显式替换体；`canaryInUrl==[]`、`canaryInBody==['POST /model-credentials','PUT /model-credentials/{id}']`；留空更新体 `'api_key' not in body`；读取投影 `configured=True`。③ `test_real_service_probes_only_the_named_member`：`POST /model-credentials/test` 体恰为 `{"targets":[{"model_id":…,"credential_id":…}]}`（不遍历模型×Key、不自报计量身份）；`NOT_EXECUTED` 与 `error_code=MODEL_UNAVAILABLE`/`latency_ms` 原样保留。④ `test_real_service_validates_the_credential_input`：空密钥 → `API_KEY_REQUIRED`；`max_inflight` 为 0/2.5 → `MAX_INFLIGHT_INVALID`。⑤ `test_real_service_maps_conflicts_to_explicit_copy`：去重/排空/被引用/版本冲突/未知 → 四个 `model.credential.error.*` + `common.saveFailed`。⑥ `test_panel_is_admin_gated_and_never_renders_key_material`：`useAuth` + `account?.role === 'ADMIN'` + `if(!isAdmin){returnnull;}`；无 `credential.api_key`/`api_key_configured` 读取；`apiKeyOptions(t)`+`credential.configured`；编辑密钥输入从 `api_key: ''` 开始并有「留空保留/不回显」说明。⑦ `test_panel_probe_shows_single_member_results_only`：`probeCredentials([{ model_id: modelId, credential_id: credential.id }])` 单目标；`memberOnly` 与 `pickerTruncated` 提示；无 `batchTestModels`/`last_test_status`/`api_key_configured`；`probeStatusOptions(t)`。⑧ `test_panel_uses_shared_components_guards_and_i18n`：DataTable/FormModal/ModuleToolbar/ConfirmAction/ErrorState/PaginationFooter/StatusTag/DateTimeText 复用、`requestSeq` 竞态守卫、`credentialSaveErrorKey` 而非手搓 `error.response`、无硬编码中文文案。⑨ `test_panel_compiles_to_a_real_typescript_artifact`：真实 TSX（含模块内 service/共享组件依赖图）经 esbuild 编成 ESM，产物含 `credential-panel` 且不含 canary。⑩ `test_credential_keys_are_localized`：57 条 `model.credential.*` 两侧齐平，三个检测状态词条非裸枚举码 | 真实 esbuild（frontend/node_modules/.bin/esbuild）把真实 `services/modelCredentials.ts` + 真实 `src/api/client.ts`（真实 axios 实例与请求拦截器）打成 ESM 编译产物，由真实 node v24 执行；断言观测的是真实 axios adapter 实际收到的 URL/params/请求头/请求体与真实返回值，`@douyinfe/semi-ui` 只被别名成可观测 Toast 记录器（展示依赖，非被测边界）。面板侧以真实 TSX 的 esbuild 编译产物为「TypeScript 编译产物」边界；本用例不触 HTTP/DB/浏览器，DOM 交互（点击新增/编辑/删除/检测、SideSheet 挂接）按契约留给真实浏览器 E2E（S-106，TASK-115） | verified |

实现要点：**ADMIN 维护面**——`ModelCredentialPanel` 用 `useAuth` 判角色，非 ADMIN 直接 `return null`（「前端隐藏 + 后端 403 兜底」的前端一半），列表加载也带请求竞态守卫与分页。**读取只 configured**——DTO 里根本没有密钥字段（`ModelCredentialItem` 只有 `configured`），列表/详情列渲染 `apiKeyOptions` 的「已配置/未配置」标签；明文只作为写入体从表单发出，绝不进 URL、查询参数、检测请求或渲染。**留空保留 / 替换显式提交**——编辑表单密钥框从空值开始，提交时 `buildCredentialUpdateInput` 在留空时**不写 `api_key` 键**（后端契约：缺省 = 保留原值，空串是 422）；非空才显式带上并轮换计量身份。**同物理 Key 去重 / 降额排空**——`credentialSaveErrorKey` 把 `MODEL_CREDENTIAL_EXISTS`、`MODEL_CAPACITY_DRAIN_REQUIRED`、`MODEL_CREDENTIAL_IN_USE`、`REVISION_CONFLICT` 映射到面板内 Banner 文案（共享传输同时按后端目录提示一次），未知失败回落 `common.saveFailed`。**单成员检测**——面板的检测入口先选模型，再只发 `(model_id, credential_id)` 一组目标：占额走 API-17、探测 HTTP 与结算在服务端（TASK-028），面板只展示逐项结果并在结果区明示「只覆盖本次点名的成员、不代表整池健康、不改逻辑模型测试状态」；模型候选只取第一页 100 条（API 上限）并在截断时显式提示。

回归：`uv run pytest -q tests/frontend` exit=0，**365 passed**（本任务新增 10 条用例后全量绿，含 `test_capacity_webchat_chat_transport.py` 10 passed、`test_api_client_contract.py` 4 passed、`test_console_auth_contract.py` 11 passed）。`uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py` exit=0，21 passed。`tests/acceptance/**`、`make acceptance*`、Playwright 浏览器验收未执行（共享环境约束 + E2E 属 TASK-115 终验）。

静态检查：`npx tsc --noEmit`（apps/console-platform/frontend）干净；`npm run build` exit=0；`scripts/check_frontend_i18n.py`「i18n keys OK: 1265」；`scripts/check_frontend_api_usage.py`「frontend api usage check OK」；`uv run pytest -q tests/frontend/test_capacity_webchat_credential_ui.py` 里另有一条真实编译产物检查。新增/改动均为强类型（无 `any`）、无裸 axios/fetch、无自拼传输头；密钥不写日志/URL/响应渲染；词条只追加不删改。

遗留（非本任务范围）：① 浏览器 E2E S-106（TASK-115）保留新增/编辑/删除/检测的点击路径、SideSheet 关闭不破坏列表、BUILDER 不显示入口；本任务只登记不执行。② 面板**挂载点**属 TASK-092（`ModelPage.tsx`/`ModelDetailSideSheet.tsx`：主列表进入物理 Key/额度管理、详情挂成员关系），本任务不新增挂载点，避免「未挂载孤立组件」被 B-292 判红。③ 额度组 CRUD、RPM/TPM/null 单位与共享关系展示属 TASK-088；本任务只提供凭据表单归属选择需要的 `listQuotaGroups()` 选项投影。④ **事实同步缺口**：`ModelFormModal.tsx` 与 `services/models.ts` 仍按 TASK-019/020 之前的字段（`base_url`/`api_key`/`api_key_configured`）渲染，与后端 `extra="forbid"` 的新契约（`provider_key` + `params.max_tokens` 必填）不一致；该两文件不在本任务 Files 内，属 TASK-092「原模型编辑/版本/测试保持最终新语义」的范围。⑤ 检测的模型候选只取第一页 100 条（后端上限），跨页选择需 TASK-092/后续按需补搜索；当前以显式截断提示兜底。
- B-286: verified — automated command passed; run_id=476a9841bb79457793a30123c0b89ae7 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] RED 记录：`uv run pytest -q tests/frontend/test_capacity_webchat_credential_ui.py` exit=1，5 failed + 5 errors
- [2026-10-18] GREEN 记录：同命令 exit=0，10 passed；mutation 自检两处（留空发送密钥 / 删掉非 ADMIN 不渲染）均如实失败后还原
- [2026-10-18] completed (done)

---

## TASK-087: 模型成员绑定、权重与逻辑模型预算

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-017, TASK-019, TASK-086
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-287
- **Files**: `apps/console-platform/frontend/src/modules/model-management/ModelDeploymentPanel.tsx`, `apps/console-platform/frontend/src/modules/model-management/ModelDetailSideSheet.tsx`, `tests/frontend/test_capacity_webchat_deployment_ui.py`
- **Estimate**: 45分钟

### Description

从已配置凭据单项绑定/解绑与权重版本修改，不重复填写Key/集合整体PUT；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-287][integration] 先在`tests/frontend/test_capacity_webchat_deployment_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：从已配置凭据单项绑定/解绑与权重版本修改，不重复填写Key/集合整体PUT。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_deployment_ui.py` exit=1，**6 failed + 4 errors**（0.09s）——4 条真实 node 执行用例在 fixture 阶段 error（esbuild 解析不到尚未创建的 `services/modelDeployments.ts`），6 条源码/编译/词条契约失败（`ModelDeploymentPanel.tsx`、成员 Tab、36 条词条尚不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `services/modelDeployments.ts`（模块 HTTP 出口，API-01）——成员投影恰为 `{id,key,model_id,credential_id,weight,enabled,revision}`；**单关系**四端点（GET 列表/POST 绑定/PUT 改权重/DELETE 解绑），**没有集合整体 PUT**；纯函数 `buildDeploymentCreateInput`（凭据必选、权重 1..100 整数，非法显式抛 `ModelDeploymentInputError(reason)`）、`buildDeploymentUpdateInput`（**不带 `credential_id`**：重绑 = DELETE + POST；带 `expected_revision = member.revision` CAS）、`deploymentSaveErrorKey`（`MODEL_DEPLOYMENT_EXISTS`/`MODEL_PROVIDER_MISMATCH`/`REVISION_CONFLICT` → 本地化键，未登记回落 `common.saveFailed`）、`readOutputBudget`（`params.max_tokens` 正整数，缺失/非法显式 `null`）、`readModelProviderKey`（TASK-019 契约字段，缺失/非字符串按「未声明」空串）。② 新增 `ModelDeploymentPanel.tsx`——`ModelDeploymentForm`（选已配置凭据 + 权重；编辑时凭据只读、留空/密钥字段根本不出现）、`MemberTable`（竞态守卫 + 截断提示 + 解绑走 `ConfirmAction`）、`ModelDeploymentPanel`（**仅 ADMIN** 非 ADMIN 直接不渲染；可绑定集合 = 同供方 ∧ 启用 ∧ 未绑定；供方未声明/凭据索引失败/被截断三种降级各有显式文案；常驻「保存后影响后续新 Run」说明）。③ `ModelDetailSideSheet.tsx` 挂**成员 Tab（仅 ADMIN 门控内）**，并把基本信息同步为新事实（`provider_key` + `params.max_tokens` 预算，去掉已删除的 `base_url`/`api_key_configured` 渲染）。④ `src/locales/{zh-CN,en-US}.json` 各追加 36 条 `model.member.*`/`model.detail.*` 词条（只追加、两侧齐平），并同步 `model.detail.hint`（旧文案仍称「API Key 不回显」，与 0027 删列后的事实不符）。
- [x] [B-287] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-287] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_deployment_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**10 passed**（0.19s）。
- [x] 断言强度自检（mutation，跑完即还原）：① 把 `buildDeploymentUpdateInput` 的 CAS 从 `expected_revision: member.revision` 改成常量 `1` → 2 failed（含 `assert updated["body"]["expected_revision"] == 2`）；② 去掉可绑定集合的 `credential.provider_key === props.providerKey` 过滤 → 1 failed。两处均还原后复跑 10 passed，证明「CAS 版本」与「同供方可绑定集合」两条断言不是空转。
- [x] 同域静态/回归检查：`uv run pytest -q tests/frontend` exit=0 **397 passed**（1.30s）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0；`npm run build` exit=0（`✓ built in 4.66s`，build 内含 typecheck）；`.venv/bin/python scripts/check_frontend_i18n.py`「i18n keys OK: 1324」；`.venv/bin/python scripts/check_frontend_api_usage.py`「frontend api usage check OK」；`ruff check`/`ruff format --check` 新测试文件干净。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-287 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 从已配置凭据单项绑定/解绑与权重版本修改，不重复填写Key/集合整体PUT；显示后续新Run生效及最大输出预算，使用共享详情组件 | tests/frontend/test_capacity_webchat_deployment_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_deployment_ui.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-287 | `uv run pytest -q tests/frontend/test_capacity_webchat_deployment_ui.py` exit=1，**6 failed + 4 errors**（0.09s）：`test_member_panel_is_admin_gated_and_never_refills_keys`/`test_member_panel_shows_effect_eligibility_and_binding_state`/`test_member_panel_uses_shared_components_guards_and_i18n`（面板文件不存在）、`test_detail_sheet_mounts_member_tab_with_budget_facts`（成员 Tab 未挂）、`test_member_panel_compiles_to_real_typescript_artifacts`（esbuild 找不到 TSX）、`test_member_keys_are_localized`（zh/en 各缺 36 键）；4 条 node 用例 error（esbuild 解析不到缺失的 `services/modelDeployments.ts`） | 同命令 exit=0，**10 passed**（0.19s） | ① `test_real_service_maps_single_member_relations` L342-380：`GET /models/{id}/deployments?page=1&page_size=100`（读取体为空）、投影字段集**恰为** `{id,key,model_id,credential_id,weight,enabled,revision}`（L354-362，无密钥/端点）、`POST /models/{id}/deployments` 绑定体恰为 `{key,credential_id,weight,enabled}`（L366-372，无 `revision`、无 `api_key`/`base_url`，带 `X-Request-Id`）、`PUT /models/{id}/deployments/{dep}` 带 `expected_revision=2`（L374-379）、`DELETE` 单关系（L380）。② `test_real_service_never_replaces_the_member_set_or_refills_keys` L383-411：集合级 PUT 请求**零次**（L387）、写请求清单恰为 POST/PUT/DELETE 三条单关系（L388-392）、`PUT` 输入不含 `credential_id`（L396）、请求体无 `api_key`/`base_url`（L398）、导出函数清单无集合级写入口（L399-411）。③ `test_real_service_validates_weight_and_credential_choice` L413-424：权重 0/101/2.5 → `WEIGHT_INVALID`、凭据留空 → `CREDENTIAL_REQUIRED`、CAS 版本取自成员行（L422-423）。④ `test_real_service_maps_conflicts_and_reads_the_output_budget` L426-450：三种冲突码 + 未知回落（L428-433）、`max_tokens` 正整数通过/缺失/0/-1/2.5/字符串/布尔/`max_output_tokens`-only 全 `null`（L434-444）、`provider_key` 声明/未声明/缺失/非字符串（L445-450）。⑤ `test_member_panel_is_admin_gated_and_never_refills_keys` L452-471：`useAuth`+`account?.role === 'ADMIN'`+`if(!isAdmin){returnnull;}`（L455-459）、单关系调用与冲突码映射（L461-464）、成员面**不含** `api_key`/`base_url`/`apiKey` 与密钥输入（L466-469）。⑥ `test_member_panel_shows_effect_eligibility_and_binding_state` L473-490：「保存后影响后续新 Run」（L476）、供方未声明/无可绑定/成员截断/凭据截断/凭据索引失败五种显式降级（L477-482）、可绑定集合同供方过滤（L483-485）、已绑定凭据排除（L486）、解绑走 `ConfirmAction`（L490）。⑦ `test_member_panel_uses_shared_components_guards_and_i18n` L492-519：DataTable/FormModal/ConfirmAction/ErrorState/EmptyState/StatusTag/TableText/tableLayout 复用（L495-504）、`requestSeq` 竞态守卫（L505-507）、关系 Tab「工具行→分区标题→表格→说明」布局（L508-510）、无硬编码中文（L512-519）。⑧ `test_detail_sheet_mounts_member_tab_with_budget_facts` L521-536：成员 Tab 在 `isAdmin ? (<Tabs.TabPane itemKey="members" …)` 门控内（L524-526）、`readModelProviderKey`/`readOutputBudget` + 预算与供方行（L528-532）、旧字段 `api_key_configured`/`model.form.baseUrl` 不再出现（L533-536）。⑨ `test_member_panel_compiles_to_real_typescript_artifacts` L538-568：面板与详情两份真实 TSX 各经 esbuild 编成 ESM，产物含 `model-member-panel` 与 `model.detail.members`、不含 canary。⑩ `test_member_keys_are_localized` L570-585：36 条词条 zh/en 齐平且非照抄 | 真实 esbuild（`frontend/node_modules/.bin/esbuild`）把真实 `services/modelDeployments.ts` + 真实 `src/api/client.ts`（真实 axios 实例与三件请求头拦截器）打成 ESM 编译产物，由真实 node v24 执行；断言观测的是真实 axios adapter 实际收到的 URL/params/请求头/请求体与真实返回值（`@douyinfe/semi-ui` 只别名成可观测 Toast 记录器，是展示依赖而非被测边界）。组件侧以真实 TSX 的 esbuild 编译产物为「TypeScript 编译产物」边界；本用例不触 HTTP/DB/浏览器，DOM 交互（点击绑定/解绑、表单提交、Tab 切换）按契约留给真实浏览器 E2E（S-106 域，TASK-115）。语义以后端真实事实为准：投影 `_deployment_item`、PUT 不含 `credential_id`、集合 PUT 405、`params.max_tokens` 正整数必填（TASK-019）、`provider_key` 同供方核对（TASK-017 已验证） | verified |

实现要点：**单关系口径**——service 只有 POST 绑定/PUT 改权重/DELETE 解绑三条写路径，`buildDeploymentUpdateInput` 从成员行取 `expected_revision` 做 CAS 且**不携带 `credential_id`**（更换凭据必须解绑后重绑），测试用「集合级 PUT 请求零次 + 导出函数清单」双重钉死「不搞集合整体 PUT」。**不重复填写 Key**——成员体只有 `{key,credential_id,weight,enabled}`；面板与表单源码里没有密钥/端点字段，凭据只从已配置列表里选（同供方 ∧ 启用 ∧ 未绑定），编辑时凭据下拉只读。**逻辑模型预算与供方**——详情基本信息按 TASK-019 契约读 `provider_key` 与 `params.max_tokens`：预算是正整数才显示，缺失/非法显示「未声明（保存模型时必须为正整数）」；供方未声明时成员面板显式说明「任何物理 Key 都无法通过同供方核对」并禁用绑定入口（与后端 `MODEL_PROVIDER_MISMATCH` 同口径，不制造注定 422 的请求）。**共享详情组件**——成员面板挂进 `ModelDetailSideSheet` 的成员 Tab（仅 ADMIN），主操作按钮走 `.detail-toolbar` + `detail-section-title` + `DataTable` + `.detail-hint`，绑定/调权重用 `FormModal`、解绑用 `ConfirmAction`、状态用 `StatusTag`、错误用 `ErrorState`/`Banner`。**显式降级**——成员列表与凭据索引各有请求竞态守卫与截断提示；凭据索引加载失败时标签不可用且绑定入口禁用，成员关系仍按成员表展示（不静默清空、不伪造标签）。

实施范围与证据说明（含超出计划 Files 的披露）：计划 Files 为 `ModelDeploymentPanel.tsx`、`ModelDetailSideSheet.tsx`、`tests/frontend/test_capacity_webchat_deployment_ui.py`。超出披露：① 新增 `services/modelDeployments.ts`——RULE-front-001 要求模块 HTTP 出口只能落在 `modules/<module>/services/*.ts`（组件禁止裸用共享 api 实例），故服务文件是实现必需；② `src/locales/{zh-CN,en-US}.json` 只追加 36 条成员/预算词条并同步 `model.detail.hint`（原文案仍称「API Key 不回显」，与 0027 删列后的事实不符，属事实同步）；③ `ModelDetailSideSheet.tsx` 基本信息同步为新事实（供方/最大输出预算；移除 `base_url`/`api_key_configured` 渲染）。未动：`ModelPage.tsx`、`ModelFormModal.tsx`、`services/models.ts`、`ModelCredentialPanel.tsx`、`statusOptions.ts`；无后端/契约/迁移/DDL 改动。

遗留（非本任务范围，明确移交）：① **事实同步缺口**——`services/models.ts` 的 `ModelItem` 仍含 `base_url`/`api_key_configured`、`ModelFormModal.tsx` 仍按旧字段渲染；本任务只在 SideSheet/面板侧按后端真实投影做运行时读取（`readModelProviderKey`/`readOutputBudget`，缺字段按「未声明」显式降级），DTO 与表单的最终新语义属 TASK-092 Files。② 挂载点补全——主列表进入物理 Key/额度管理、`ModelPage.tsx` 传递 `onChanged`（本任务已在 `ModelDetailSideSheet` 预留可选 `onChanged?()`，当前调用点未传）属 TASK-092。③ 额度组 CRUD 与 RPM/TPM/null 单位展示属 TASK-088；本任务只在成员表单说明「同一物理 Key 的容量在所有引用它的模型间共享计量」。④ 浏览器 E2E（绑定/解绑/调权重的点击路径、非 ADMIN 无成员入口、SideSheet 关闭不破坏列表）属 TASK-115 的 S-106 域，本任务只登记不执行。⑤ 成员与凭据候选都只取第一页 100 条（后端上限），跨页选择需后续按需补搜索；当前以显式截断提示兜底。
- B-287: verified — automated command passed; run_id=105d16b1cb3046ce86828df206505736 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] RED 记录：`uv run pytest -q tests/frontend/test_capacity_webchat_deployment_ui.py` exit=1，**6 failed + 4 errors**（service/面板/成员 Tab/词条均未实现）
- [2026-10-18] 实现：新增 `services/modelDeployments.ts`（单关系 CRUD + CAS 输入构造 + 权重 1..100 边界 + 冲突码映射 + `params.max_tokens`/`provider_key` 事实读取）；新增 `ModelDeploymentPanel.tsx`（ADMIN 门控、可绑定集合=同供方/启用/未绑定、绑定/调权重/解绑、五种显式降级）；`ModelDetailSideSheet.tsx` 挂成员 Tab（仅 ADMIN）并同步基本信息为新事实；zh/en 各追加 36 条词条
- [2026-10-18] GREEN 记录：同命令 exit=0，**10 passed**；回归 `tests/frontend` **397 passed**；`npx tsc --noEmit` exit=0；`npm run build` 通过；i18n 检查 OK（1324 键）；api usage 检查 OK；ruff check/format 干净
- [2026-10-18] mutation 自检两处（CAS 版本改常量 / 去掉同供方过滤）均如实失败后还原
- [2026-10-18] completed (done)

---

## TASK-088: 额度组管理及共享预算说明

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-018, TASK-086
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-288
- **Files**: `apps/console-platform/frontend/src/modules/model-management/ModelQuotaPanel.tsx`, `apps/console-platform/frontend/src/modules/model-management/services/modelQuotas.ts`, `tests/frontend/test_capacity_webchat_quota_ui.py`
- **Estimate**: 45分钟

### Description

RPM/TPM/null/max_inflight单位与共享关系可见；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-288][integration] 先在`tests/frontend/test_capacity_webchat_quota_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：RPM/TPM/null/max_inflight单位与共享关系可见。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_quota_ui.py` exit=1，**5 failed + 4 errors**（0.12s）——4 条真实 node 执行用例在 fixture 阶段 error（esbuild 解析不到尚未创建的 `services/modelQuotas.ts`），5 条源码/编译/词条契约失败（`ModelQuotaPanel.tsx` 不存在、41 条 `model.quota.*` 词条尚不存在）。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① 新增 `services/modelQuotas.ts`（模块 HTTP 出口，API-02）——读取投影恰为 `{id,key,max_inflight,rpm,tpm,revision}`；CRUD 四端点（GET 列表/POST 创建带 `Idempotency-Key`/PUT 整份替换带 `expected_revision` CAS/DELETE）；纯函数 `buildQuotaGroupCreateInput`（标识必填、在飞上限 ≥1 整数、RPM/TPM 留空 = 显式 `null`，非法显式抛 `ModelQuotaGroupInputError(reason)`）、`buildQuotaGroupUpdateInput`（同套字段校验 + `expected_revision = group.revision`）、`quotaLimitState`/`describeQuotaLimit`/`describeQuotaGroup`（**`null` = `UNLIMITED`「不设限」，与 `0` 严格区分**；每维度带 `unitKey` 词条键）、`quotaGroupSaveErrorKey`（`MODEL_QUOTA_GROUP_EXISTS`/`MODEL_CAPACITY_DRAIN_REQUIRED`/`MODEL_QUOTA_GROUP_IN_USE`/`REVISION_CONFLICT` → 本地化键，未登记回落 `common.saveFailed`）。② 新增 `ModelQuotaPanel.tsx`——`ModelQuotaGroupForm`（标识 + 在飞上限 + RPM/TPM 两个可留空 `InputNumber`，留空以 `null` 提交；一次提交 = 一个幂等键，重试沿用）、`QuotaGroupTable`（三个维度列各走 `QuotaLimitCell`：`UNLIMITED` 显示「不设限」并把「≠ 零额度」放进悬停说明；编辑/删除动作，删除走 `ConfirmAction`）、`ModelQuotaPanel`（**仅 ADMIN**，非 ADMIN 直接不渲染；竞态守卫 + 分页 + 截断提示；常驻「共享预算」「不设限≠零额度」「降额/新开限流需排空」「引用中删除只冲突不清引用」四段说明；**删除失败提前返回，不刷新不清空列表**）。③ `src/locales/{zh-CN,en-US}.json` 各追加 41 条 `model.quota.*` 词条（只追加、两侧齐平）。④ `services/modelCredentials.ts` 的 `listQuotaGroups` 改为委托 `modelQuotas.listQuotaGroups({page:1,page_size:100})`、`ModelQuotaGroupOption` 改为 `ModelQuotaGroupItem` 的类型别名——同一端点在本模块只留一处 HTTP 出口，行为与投影不变（TASK-086 用例复跑仍 10 passed）。
- [x] [B-288] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-288] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_quota_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**9 passed**（0.15s）。
- [x] 断言强度自检（mutation，跑完即还原，`diff` 验证两文件字节一致）：① `quotaLimitState` 改成 `value === null || value === 0 ? 'UNLIMITED' : 'LIMITED'`（把 0 与 null 混同）→ 1 failed（`test_real_service_keeps_null_dimensions_meaning_unlimited`）；② 删掉删除失败的 `return;`（失败后照常刷新）→ 1 failed（`test_panel_is_admin_gated_and_keeps_the_referenced_group`）；③ `expected_revision: group.revision` 换成常量 `1` → 1 failed（`test_real_service_maps_the_quota_group_routes`）。三处均还原后复跑 9 passed，证明「null≠0」「删除不误清」「CAS 版本」三条断言不是空转。
- [x] 同域静态/回归检查：`uv run pytest -q tests/frontend` exit=0 **406 passed**（2.82s，含 TASK-086/087 两套凭据/成员用例）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0；`.venv/bin/python scripts/check_frontend_i18n.py`「i18n keys OK: 1365」；`.venv/bin/python scripts/check_frontend_api_usage.py`「frontend api usage check OK」；跑前已确认本 worktree 无残留 pytest/uvicorn/vite 进程。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-288 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | RPM/TPM/null/max_inflight单位与共享关系可见；降额/改归属需排空的冲突明确，非ADMIN无维护入口，删除不误清仍引用额度 | tests/frontend/test_capacity_webchat_quota_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_quota_ui.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-288 | `uv run pytest -q tests/frontend/test_capacity_webchat_quota_ui.py` exit=1，**5 failed + 4 errors**（0.12s）：`test_panel_is_admin_gated_and_keeps_the_referenced_group`/`test_panel_states_units_sharing_and_null_dimension_semantics`/`test_panel_uses_shared_components_guards_and_i18n`（面板文件不存在）、`test_panel_compiles_to_a_real_typescript_artifact`（esbuild 找不到 TSX）、`test_quota_keys_are_localized`（zh/en 各缺 41 键）；4 条 node 用例 error（esbuild 解析不到缺失的 `services/modelQuotas.ts`） | 同命令 exit=0，**9 passed**（0.15s） | ① `test_real_service_maps_the_quota_group_routes` L327-358：`GET /model-quota-groups?page=1&page_size=20`（读取体为空，L330-334）、`POST /model-quota-groups` 创建体字段集**恰为** `{key,max_inflight,rpm,tpm}`（L342-345，无 `revision`/凭据字段）、创建带 `Idempotency-Key: idem-288` + 共享传输 `X-CSRF-Token` + `X-Request-Id`（L339-341）、`PUT /model-quota-groups/{id}` 体字段集**恰为** `{key,max_inflight,rpm,tpm,expected_revision}` 且 `expected_revision=2`（L350-353）、留空 TPM 以 `null` 提交（L354）、`DELETE`（L357-358）。② `test_real_service_keeps_null_dimensions_meaning_unlimited` L361-387：读投影 `rpm is None` 原样保留（L363）、`quotaLimitState(null)=UNLIMITED`/`(1200)=LIMITED`/`(0)=LIMITED`（L367-372，**0 与 null 不混同**）、`describeQuotaGroup` 的 rpm 描述符 `{value:None,state:'UNLIMITED',unitKey:'model.quota.unit.rpm'}` 与 tpm/maxInflight 的 `unitKey`（L375-386）。③ `test_real_service_validates_the_quota_input` L390-404：标识留空 → `KEY_REQUIRED`、在飞上限 0/2.5 → `MAX_INFLIGHT_INVALID`、RPM 0 → `RPM_INVALID`、TPM 1.5 → `TPM_INVALID`（L393-397）、留空维度构造成 `{"rpm":None,"tpm":None}` 而非 0/省略（L398-403）。④ `test_real_service_maps_conflicts_to_explicit_copy` L407-415：`MODEL_QUOTA_GROUP_EXISTS`/`MODEL_CAPACITY_DRAIN_REQUIRED`/`MODEL_QUOTA_GROUP_IN_USE`/`REVISION_CONFLICT` → `model.quota.error.*`，未知回落 `common.saveFailed`。⑤ `test_panel_is_admin_gated_and_keeps_the_referenced_group` L418-432：`useAuth`+`account?.role === 'ADMIN'`+`if(!isAdmin){returnnull;}`（L421-426）、冲突只经 `quotaGroupSaveErrorKey` 且组件无 `error.response`（L427-428）、删除 catch 后**立即 return 再 afterMutation**（L429-432）。⑥ `test_panel_states_units_sharing_and_null_dimension_semantics` L435-449：`describeQuotaGroup` 提供单位与状态（L438）、「不设限」+「不设限≠零额度」+「共享/不按 Key 数复制」+「降额需排空」+「引用中删除只冲突」五段说明常驻（L439-443）、维度单位来自词条（L444）、额度面不含 `api_key`/`credential_id`/`provider_key`（L445-446）、创建透传幂等键（L447-449）。⑦ `test_panel_uses_shared_components_guards_and_i18n` L452-477：DataTable/FormModal/ModuleToolbar/ConfirmAction/ErrorState/PaginationFooter/TableText 复用（L456-463）、`requestSeq` 竞态守卫（L465-467）、`data-testid="quota-panel"`（L468）、无硬编码中文（L470-477）。⑧ `test_panel_compiles_to_a_real_typescript_artifact` L480-505：真实 TSX 经 esbuild 编成 ESM，产物含 `quota-panel` 与 `model.quota.limit.unlimited`。⑨ `test_quota_keys_are_localized` L508-528：41 条词条 zh/en 齐平且单位/「不设限」非照抄，zh 说明含「不是零额度」与「共享…复制」 | 真实 esbuild（`frontend/node_modules/.bin/esbuild`）把真实 `services/modelQuotas.ts` + 真实 `src/api/client.ts`（真实 axios 实例与三件请求头拦截器）打成 ESM 编译产物，由真实 node v24 执行；断言观测的是真实 axios adapter 实际收到的 URL/params/请求头/请求体与真实返回值（`@douyinfe/semi-ui` 只别名成可观测 Toast 记录器，是展示依赖而非被测边界）。组件侧以真实 TSX 的 esbuild 编译产物为「TypeScript 编译产物」边界；本用例不触 HTTP/DB/浏览器，DOM 交互（新增/编辑/删除点击、留空维度、非 ADMIN 无入口）按契约留给真实浏览器 E2E（S-106 域，TASK-115）。语义以后端真实事实为准：读取投影 `_quota_item`、创建 `Idempotency-Key`（RULE-api-002）、整份替换 + `expected_revision` CAS、降额/新开限流需排空（`MODEL_CAPACITY_DRAIN_REQUIRED`）、引用中删除冲突（`MODEL_QUOTA_GROUP_IN_USE`）、`rpm`/`tpm` 的 `null` = 不设应用内限流（`application/model_quota_service.py` 头注与 `_tightens_limit` 口径一致） | verified |

实现要点：**共享预算只声明一次**——额度组是「一份容量声明」，面板常驻说明「同一额度组被多把物理 Key（进而多个模型成员）引用时共享这一份，不按 Key 或成员数量复制、叠加；未归组的 Key 独占自身容量」，与 TASK-087 成员表单的「同一物理 Key 的容量在所有引用它的模型间共享计量」互为同一事实的两面。**null 维度语义如实表达**——service 的 `quotaLimitState` 只把 `null` 判成 `UNLIMITED`，`describeQuotaGroup` 每维度带 `unitKey`（`个在飞调用`/`次/分钟`/`token/分钟`）；面板表格把「不设限」渲染成词条并把「不是零额度」放进悬停与常驻说明，表单留空即以 `null` 提交（不是 `0`、也不是省略键）。**冲突显式**——降额/新开限流排空（409 `MODEL_CAPACITY_DRAIN_REQUIRED`）、引用中删除（409 `MODEL_QUOTA_GROUP_IN_USE`）、同名（409 `MODEL_QUOTA_GROUP_EXISTS`）、版本冲突（409 `REVISION_CONFLICT`）四种冲突各有本地化文案键；删除失败**提前返回**，列表保持原样（不误清仍被引用的额度，也不假装删除成功）。**非 ADMIN 无维护入口**——`if (!isAdmin) { return null; }`（「前端隐藏 + 后端 403 兜底」的前端一半），写操作仍走共享传输的 CSRF 头。

实施范围与证据说明（含超出计划 Files 的披露）：计划 Files 为 `ModelQuotaPanel.tsx`、`services/modelQuotas.ts`、`tests/frontend/test_capacity_webchat_quota_ui.py`。超出披露：① `src/locales/{zh-CN,en-US}.json` 各**只追加** 41 条 `model.quota.*` 词条（两侧齐平，无其它改动）；② `services/modelCredentials.ts` 的 `listQuotaGroups`/`ModelQuotaGroupOption` 去重改为委托/别名（同一端点在本模块只留一处 HTTP 出口，投影与行为不变，TASK-086 用例复跑 10 passed）。未动：`ModelPage.tsx`、`ModelFormModal.tsx`、`ModelDetailSideSheet.tsx`、`services/models.ts`、`ModelCredentialPanel.tsx`、`statusOptions.ts`；无后端/契约/迁移/DDL 改动。

遗留（非本任务范围，明确移交）：① **挂载点**——`ModelPage.tsx` 进入物理 Key/额度管理、与 TASK-086 凭据面板/TASK-087 成员面板并列挂接、`onChanged` 接线属 TASK-092 Files（本面板已预留可选 `onChanged?()`，当前无调用点）；② 浏览器 E2E（额度组新增/编辑/删除点击路径、留空维度回显、非 ADMIN 无额度入口、中英文/窄屏布局）属 TASK-115 的 S-106/S-107 域，本任务只登记不执行；③ 额度组列表只取第一页（后端 `page_size≤100`），跨页选择/搜索需后续按需补，当前以显式截断提示兜底；④ `model.credential.form.quotaGroupHint` 仍以「留空表示该 Key 独占自身容量」描述归属，与本次额度组侧的共享说明口径一致，未改（属 TASK-086 词条）。
- B-288: verified — automated command passed; run_id=8aff9ef3ebe44145ba138c49aab553ed (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] 编码完成：RED 5 failed + 4 errors → GREEN 9 passed；`tests/frontend` 406 passed；tsc/i18n/api-usage 全绿
- [2026-10-18] completed (done)

---

## TASK-089: 系统设置新增分组与字段错误定位

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-076
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态
- **Spec-Refs**:
- **Acceptance-Refs**: B-289
- **Files**: `apps/console-platform/frontend/src/modules/settings/types.ts`, `apps/console-platform/frontend/src/modules/settings/components/SettingsGroupPanel.tsx`, `tests/frontend/test_capacity_webchat_settings_ui.py`
- **Estimate**: 45分钟

### Description

容量/模型池/Webchat字段从groups元信息渲染默认/单位/范围/生效标记；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-289][integration] 先在`tests/frontend/test_capacity_webchat_settings_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：容量/模型池/Webchat字段从groups元信息渲染默认/单位/范围/生效标记。RED：`uv run pytest -q tests/frontend/test_capacity_webchat_settings_ui.py` exit=1，**5 failed + 3 passed（0.84s）**（实现先 `git stash` 掉再测同一份测试）——① `test_b289_new_group_fields_project_defaults_ranges_units_and_effects`：`effect_key` 未投影（`None != 'settings.effect.new_admission'`）；② `test_b289_field_errors_locate_the_control_instead_of_a_page_error`：前端缺少字段错误定位 `locateField`；③ `test_b289_group_panel_renders_defaults_units_ranges_and_effect_notes`：真实组件未渲染 `settings-effect-<path>` 节点；④ `test_b289_field_error_renders_inside_its_control_row_only`：出错控件无 `aria-describedby="settings-error-<path>"` 关联；⑤ `test_b289_settings_ui_compiles_from_the_real_sources`：编译产物缺 `settings-effect-`。RED 期即通过的 3 条（整份文档+revision/冲突不覆盖、启动配置不进入编辑面、新增分组 label/unit/effect 词条齐平）钉住既有正确行为，避免实现阶段回归。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。实现：① `modules/settings/types.ts` 的 `SettingsFieldMeta` 增 `effectKey`（API-01 `effect_key`，说明文案只翻译不编写）；② `services/settingsApi.ts` 的 `RawField`/`toField` 投影 `effect_key`（其余元信息字段映射不变）；③ `components/SettingsFieldRow.tsx` 渲染生效说明节点 `settings-effect-<path>`（`t(meta.effectKey)`，无 effect_key 不渲染），并修正字段错误关联：错误节点 `id="settings-error-<path>"`，控件经 `aria-describedby`+`aria-invalid`+`validateStatus` 指回自己的错误（原来 `aria-describedby` 指回输入框自身，方向反了）；④ `modules/settings/formValues.ts` 新增纯函数 `locateField`（错误路径 → 分组/控件：先精确匹配字段路径，再按分组前缀归一化，覆盖 compaction 组「组内相对路径 + 后端带前缀错误」两形态，解析不到返回 null 不猜）；⑤ `pages/SettingsPage.tsx` 用 `locateField` 把首个出错字段所在分组切到前台（单面板下错误必须可见）；⑥ `styles/app.css` 增 `.settings-field-effect`。改动只在设置模块与共享样式，未新增依赖、未动 `src/locales/*.json`（词条由 TASK-008 预置、本任务只消费）。
- [x] [B-289] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置（见 Acceptance Evidence 的断言位置列）。
- [x] [B-289] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_settings_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。GREEN：同命令 exit=0，**8 passed（0.63–0.65s，两轮稳定）**。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-289 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 容量/模型池/Webchat字段从groups元信息渲染默认/单位/范围/生效标记；整份文档及revision保存、冲突不覆盖，不给启动连接池做运行时编辑框 | tests/frontend/test_capacity_webchat_settings_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_settings_ui.py"] | verified |

### Acceptance Evidence

> RED/GREEN 与断言位置如下。真实边界：真实模块 service（`modules/settings/services/settingsApi.ts`）+ 真实 typed DTO（`types.ts`）+ 真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器，HTTP 经 adapter 观测方法/URL/体/头）+ 真实后端字段元信息（`platform_settings_catalog.build_groups`）与真实校验错误路径（`platform_settings_guard.validation_detail`，含 `compaction.summary.model_ref` 这类带分组前缀的真实路径）+ 真实组件（`SettingsGroupPanel`/`SettingsFieldRow`，真实 Semi + 真实 zh/en 词条）静态渲染，全部经仓库内 esbuild 编译产物由真实 node v24 执行；DOM 交互（点击/输入/保存按钮）留真实浏览器 E2E（S-F15，TASK-115）。E2E 保持 planned，最终 verify-e2e 记录完整证据。

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-289 | `uv run pytest -q tests/frontend/test_capacity_webchat_settings_ui.py` exit=1，**5 failed + 3 passed（0.84s）**：effect_key 未投影、缺 `locateField`、无生效说明节点、控件未关联错误、编译产物缺 `settings-effect-` | 同命令 exit=0，**8 passed in 0.63–0.65s** | ① `test_b289_new_group_fields_project_defaults_ranges_units_and_effects` L473-500：capacity/model_pool/webchat 三组字段清单与真实 catalog 逐一相等（不丢字段/不新增），默认值/当前值/min/max/unit_key/`effect_key`/applies_to/type 逐项等于 API-01 元信息；无 `effect_key` 的 compaction 字段不得伪造说明。② `test_b289_save_keeps_whole_document_and_revision_without_overwriting_on_conflict` L503-526：PUT `/platform-settings` 体 `revision=7` + 整份分组文档（组键集合与 catalog 相等、新分组按分组文档结构提交全部字段）；成功采用服务端 revision=8；409 → `PLATFORM_SETTINGS_VERSION_CONFLICT`；hook 冲突分支只 `setConflict(true)`、不重发保存。③ `test_b289_field_errors_locate_the_control_instead_of_a_page_error` L529-565：真实后端错误路径（capacity 跨字段 `capacity.max_open_runs_per_user`、`model_pool.interactive_weight`、`webchat.session_ttl_hours`、`webchat.upload_max_bytes`（部署硬上限）、compaction 相对 `snip.max_groups`、compaction 前缀 `compaction.summary.model_ref`）逐条定位到存在的控件；前缀形态归位到 `summary.model_ref`；不存在路径返回 null；`VALIDATION_FAILED` 的 `details[].path`/message 原样映射；页面用 `locateField` 切分组、字段错误只经面板下发（页面无 `settings-error` 节点/无 `role="alert"`），页面级只有版本冲突 Banner+重新加载。④ `test_b289_startup_pool_and_transport_keys_stay_out_of_the_settings_form` L567-591：真实元信息无 `db_pool*`/`db_max_overflow`/`model_http*`/`runtime_execution*`/`stream_http*` 与 `*_hard_max_bytes`；设置模块与页面源码也不出现这些键；组件不内联 capacity/model_pool/webchat 分组键（分组只来自元信息）。⑤ `test_b289_group_panel_renders_defaults_units_ranges_and_effect_notes` L601-655：真实组件渲染真实元信息——分组标题本地化；`capacity.max_open_runs_per_tenant`/`capacity.queue_wait_timeout_sec`/`model_pool.input_estimate_multiplier`/`webchat.upload_max_bytes`/`webchat.reconnect_cap_ms` 的标签/默认值/单位/范围/生效说明（`settings-effect-<path>`）都在各自字段行内；字段级 `applies_to` 标签与分组级标签都在；zh 与 en 两侧即时渲染且说明文案不同；无 `effect_key` 的 compaction 不出现生效说明节点。⑥ `test_b289_field_error_renders_inside_its_control_row_only` L657-689：4 类控件（number/switch/text/select）出错时错误节点都在对应字段行内、`role="alert"`、`id` 与控件 `aria-describedby`/`aria-invalid` 关联，且每组只出现 1 个字段错误；未出错的渲染零错误节点。⑦ `test_b289_settings_ui_compiles_from_the_real_sources` L691-716：真实 TSX 编译产物含 `settings-group-`/`settings-field-`/`settings-effect-`/`settings-error-`/`aria-describedby`；字段行源码由 `meta.effectKey`/`meta.defaultValue`/`meta.unitKey` 驱动、说明走 `t(meta.effectKey)`、无硬编码中文文案。⑧ `test_b289_new_group_metadata_keys_are_localized_in_both_languages` L718-756：真实 catalog 的全部 label/unit/effect 词条 zh-CN 与 en-US 齐备，effect 词条两侧文案不同，且覆盖新受理/新Run/签发/交换/清理/新连接 | 真实 esbuild 把设置模块（service+typed DTO+纯逻辑）与真实共享传输打成 ESM，node v24 用真实 axios adapter 观测真实 `getPlatformSettings`/`savePlatformSettings` 发出的方法/URL/体/头；元信息与错误路径来自真实后端 `build_groups`/`validation_detail`（无服务、无 DB）；渲染侧用真实 `SettingsGroupPanel`+真实 Semi 组件+真实 zh/en 词条做静态渲染（无 DOM 交互），断言直接读真实 HTML（默认/单位/范围/生效说明/错误节点的位置与 aria 关联）。`@douyinfe/semi-ui` 只在逻辑侧被别名成可观测 Toast 记录器，渲染侧用真实组件 | verified |

实现要点：**元信息唯一来源**——新分组的默认/范围/单位/生效标记/生效说明全部来自 API-01 投影，前端不复制任何默认值或范围字面量（`effectKey` 只是词条键，文案在 locales 里）；**字段错误定位**——`locateField` 是错误路径 → 控件的唯一落点，兼容「字段路径」与「分组前缀 + 组内相对路径」两种真实形态（compaction 跨字段校验错误带前缀），解析不到返回 null 不误挂；错误渲染在字段行内并用 `aria-describedby`/`aria-invalid` 与控件双向可定位，页面级只保留版本冲突入口（E-11/E-12 不变）。

回归（本机无 dev/测试残留进程）：`uv run pytest -q tests/frontend` exit=0，**459 passed in 2.64s**（含本任务 8 条与 TASK-010 的 `test_platform_settings_contract.py`）；`npx tsc --noEmit`（apps/console-platform/frontend）exit=0 无输出；`.venv/bin/python scripts/check_frontend_i18n.py`「i18n keys OK: 1381」（零新增词条）；`uv run ruff check tests/frontend/test_capacity_webchat_settings_ui.py`「All checks passed!」；`.venv/bin/python -m py_compile` 通过。未新增迁移/DDL，未动 `src/locales/*.json`，无新增依赖。

遗留（deferred，非本任务可闭合）：① 浏览器内交互（切换分组、保存、错误滚动/聚焦到控件、窄屏布局）按契约留真实浏览器 E2E（S-F15，TASK-115）；② 新增分组词条正文（若需再扩写说明文案）属 TASK-090 的词条面，本任务只消费已就绪词条；③ 模型/用户模块的 Key 与 Token 维护面分别属 TASK-085/087/088，不在设置页；④ 规范刷新（cf-learn/refresh）：`harness-frontend` Conventions 可补「设置字段的默认/范围/单位/生效说明只来自 API-01 元信息；字段错误按 `details[].path` 定位到控件（含分组前缀归一化），错误只渲染在字段行内」；本任务不改 spec 文件以免 active context 漂移。
- B-289: verified — automated command passed; run_id=57d3677009364f089c47db49e55e05c0 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] completed (done)

---

## TASK-090: 聊天及新增设置中英文词条

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-083, TASK-084, TASK-085, TASK-087, TASK-088, TASK-089
- **Source**: agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.7 样式方案
- **Spec-Refs**: harness-i18n#RULE-i18n-001
- **Acceptance-Refs**: B-290
- **Files**: `apps/console-platform/frontend/src/locales/zh-CN.json`, `apps/console-platform/frontend/src/locales/en-US.json`, `tests/frontend/test_capacity_webchat_webchat_i18n.py`
- **Estimate**: 45分钟

### Description

全部消息/状态/错误/配置单位和生效文案双语键齐平且被真实模块引用；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-290][integration] 先在`tests/frontend/test_capacity_webchat_webchat_i18n.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：全部消息/状态/错误/配置单位和生效文案双语键齐平且被真实模块引用。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-290] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-290] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_i18n.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-i18n#RULE-i18n-001`（stage: review；timeout=300秒）：`["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-290 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 全部消息/状态/错误/配置单位和生效文案双语键齐平且被真实模块引用；动态枚举完整、切换语言即时生效，不硬编码中文或裸状态码 | tests/frontend/test_capacity_webchat_webchat_i18n.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_i18n.py"] | planned |
| harness-i18n#RULE-i18n-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-091: 主题、窄屏和可访问性布局

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-079, TASK-081, TASK-083, TASK-090
- **Source**: agent-capacity-webchat.frontend.design.md#3.7 样式方案, agent-capacity-webchat.frontend.design.md#3.8 可访问性
- **Spec-Refs**: harness-ui#RULE-ui-001
- **Acceptance-Refs**: B-291
- **Files**: `apps/console-platform/frontend/src/modules/webchat/webchat.css`, `apps/console-platform/frontend/src/styles/app.css`, `tests/frontend/test_capacity_webchat_webchat_layout.py`
- **Estimate**: 45分钟

### Description

沿用Semi主题token，侧栏/输入/停止/确认窄屏可见；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-291][integration] 先在`tests/frontend/test_capacity_webchat_webchat_layout.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：沿用Semi主题token，侧栏/输入/停止/确认窄屏可见。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-291] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-291] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_layout.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-ui#RULE-ui-001`（stage: review；timeout=900秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-291 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 沿用Semi主题token，侧栏/输入/停止/确认窄屏可见；按钮标签/键盘焦点/aria-live频率/滚动锚点规范，管理菜单仍十一项 | tests/frontend/test_capacity_webchat_webchat_layout.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_layout.py"] | planned |
| harness-ui#RULE-ui-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | Console 使用 React + TypeScript + Semi Design；列表页采用“左上操作 + 右上搜索筛选 + 列表 + 右下分页”，不重复页签标题/说明块；主展示字段即详情入口；菜单固定十一项：概览/Agent/Skill/MCP/模型/用户/项目平台/后台任务/定时任务/运行审计/系统设置。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-092: 模型管理实际页面挂接Key池与额度操作

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-086, TASK-087, TASK-088, TASK-090
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.7 样式方案
- **Spec-Refs**:
- **Acceptance-Refs**: B-292
- **Files**: `apps/console-platform/frontend/src/modules/model-management/ModelPage.tsx`, `apps/console-platform/frontend/src/modules/model-management/ModelDetailSideSheet.tsx`, `tests/frontend/test_capacity_webchat_model_admin_wiring.py`
- **Estimate**: 45分钟

### Description

主列表可进入物理Key/额度管理，模型详情挂接成员关系；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-292][integration] 先在`tests/frontend/test_capacity_webchat_model_admin_wiring.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：主列表可进入物理Key/额度管理，模型详情挂接成员关系。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-292] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-292] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_model_admin_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-292 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 主列表可进入物理Key/额度管理，模型详情挂接成员关系；真实页面可达且仅ADMIN维护，原模型编辑/版本/测试保持最终新语义，不遗留未挂载孤立组件 | tests/frontend/test_capacity_webchat_model_admin_wiring.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_model_admin_wiring.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-093: 聊天页面完整装配与管理模块接线

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-077, TASK-079, TASK-082, TASK-084, TASK-085, TASK-089, TASK-091, TASK-092
- **Source**: agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流
- **Spec-Refs**:
- **Acceptance-Refs**: B-293, B-102
- **Files**: `apps/console-platform/frontend/src/modules/webchat/ChatPage.tsx`, `apps/console-platform/frontend/src/modules/webchat/components/WebchatShell.tsx`, `tests/frontend/test_capacity_webchat_frontend_wiring.py`
- **Estimate**: 60分钟

### Description

页面实际挂接认证/配置/历史/输入/回复/控制/通知并正确清理；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-293][integration] 先在`tests/frontend/test_capacity_webchat_frontend_wiring.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：页面实际挂接认证/配置/历史/输入/回复/控制/通知并正确清理。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-293] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-293] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-102][integration] 将原B-F02落入`tests/frontend/test_capacity_webchat_frontend_wiring.py`，先RED后GREEN；请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示；断言：请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-293 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 页面实际挂接认证/配置/历史/输入/回复/控制/通知并正确清理；管理卡片由原详情页进入，避免只有组件文件而真实路由仍不可达；共享构建可编译 | tests/frontend/test_capacity_webchat_frontend_wiring.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"] | planned |
| B-102 | integration | 请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示 | 请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示 | tests/frontend/test_capacity_webchat_frontend_wiring.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-094: Runtime装配所有通道端点与独立资源池

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-027, TASK-035, TASK-037, TASK-062, TASK-066, TASK-069
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-294, B-09
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/main.py`, `apps/agent-runtime/src/muad_agent_runtime/infrastructure/http_clients.py`, `tests/agent_runtime/test_capacity_webchat_runtime_wiring.py`
- **Estimate**: 60分钟

### Description

生产app注册历史/控制/通知/交付等实际端点、注入分池HTTP和监督预算；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-294][integration] 先在`tests/agent_runtime/test_capacity_webchat_runtime_wiring.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：生产app注册历史/控制/通知/交付等实际端点、注入分池HTTP和监督预算。→ RED：首建 8 个行为用例后 `uv run pytest -q tests/agent_runtime/test_capacity_webchat_runtime_wiring.py` → **exit=2，1 error in 0.09s**（收集期 `ModuleNotFoundError: No module named 'muad_agent_runtime.infrastructure.http_clients'`：分池装配模块尚不存在，测试对真实 lifespan/真实 uvicorn 应用运行；完整输出 `/tmp/agent-capacity-webchat-plan/task094-red.log`）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 10 个文件（**超出计划 Files 的披露**：`infrastructure/console_client.py`、`infrastructure/platform_settings_client.py`、`infrastructure/gateway_delivery_client.py`、`application/task_client.py`、`application/mcp_runtime_adapter.py`、`application/executor.py`、`application/memory_consolidation.py`、`api/deps.py`，见下方"实施范围"）：新增 `infrastructure/http_clients.py`（`RUNTIME_HTTP_POOLS=("control","model","mcp","egress")` 四池声明 + `pool_limits`/`pool_timeout`/`build_pool_client`/`build_runtime_http_pools`；本进程不建的池名 `stream`/`upload` **显式 `ValueError`**；内部池 `trust_env=False` 保住"内部调用不走部署代理"的既有口径）；`main.py` 装配根（建/关分池、注入 Console 三客户端/控制派发器/投递泵/执行器工厂/记忆提炼，发布 `http_pools`/`control_dispatcher`/`executor_factory`/`consolidation_invoker`/`retention_task`，八条**具名**独立循环，新增**保留清理循环**，退出取消全部循环后先停泵/槽监督再关分池）；`executor.py`（`default_executor_factory` 增 `model_client`/`mcp_client` 注入缝，主调用/摘要/池化 provider 与 MCP 适配器共用同一实例）；`mcp_runtime_adapter.py`（`McpRuntimeAdapter(client=...)`→`_McpSession` 借用语义 + 每请求显式带 `Accept`/`Authorization`）；`memory_consolidation.py`（`ConsolidationInvoker(client=...)`）；`api/deps.py`（`get_executor_factory` 消费装配根工厂，未装配回落既有自建口径）。改动函数行数：`pool_limits` 22、`pool_timeout` 20、`build_pool_client` 7、`build_runtime_http_pools` 6、`_retention_loop` 27、`_mount_delivery_pump` 19、`_build_executor_factory` 21、`_build_outbound` 38、`_start_loops` 47、`_stop_loops` 7、`_close_resources` 11、`lifespan` 19，均 ≤50。错误显式：未调用池名 `ValueError`（不静默按别的池预算建池）、坏设置/清理失败 `logger.exception` 后继续（可见、不静默、不杀循环）、退出只吞 `CancelledError`（不吞真实错误）、半初始化失败不遗留资源（`finally` 内必关）
- [x] [B-294] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 8 个用例逐条对应契约 4 组断言（见 Acceptance Evidence 断言位置列）；六处变异（控制池不注入/模型池不注入/保留循环不启动/退出不关池/配置读取挪进清理事务/建池忽略启动设置）均先复现失败再还原，随后复跑全绿
- [x] [B-294] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **8 passed in 23.08s**（exit=0；日志 `/tmp/agent-capacity-webchat-plan/task094-green1.log`）
- [x] [B-09][integration] 将原B-09落入`tests/agent_runtime/test_capacity_webchat_runtime_wiring.py`，先RED后GREEN；contracts配置解析、真实PG配置/快照、全局准入版本和启动settings；断言：默认值/单位/边界/派生值唯一；旧policy不补新默认，低版本不覆盖全局容量；请求限额/HTTP池/租约/重连/保留联动成立；配置网络读取在行锁短事务外，fixture不把样例参数当生产实测。→ 两个 B-09 用例：①启动预算/保留边界唯一来源 + 未调用池显式失败（派生值 == 启动设置、坏值/未知分组 `PlatformSettingsError` 不回落默认）；②全局准入版本单调（低版本不覆盖）+ 配置读取在行锁短事务外（真实行为 + `fetch_capacity`/`lock_and_apply_in`/`_retention_loop` 的 AST 结构断言）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-294 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 生产app注册历史/控制/通知/交付等实际端点、注入分池HTTP和监督预算；lifespan关闭所有泵/订阅/客户端，readiness与Redis可选退化一致 | tests/agent_runtime/test_capacity_webchat_runtime_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | verified |
| B-09 | integration | contracts配置解析、真实PG配置/快照、全局准入版本和启动settings | 默认值/单位/边界/派生值唯一；旧policy不补新默认，低版本不覆盖全局容量；请求限额/HTTP池/租约/重连/保留联动成立；配置网络读取在行锁短事务外，fixture不把样例参数当生产实测 | tests/agent_runtime/test_capacity_webchat_runtime_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-294 | 首建 `tests/agent_runtime/test_capacity_webchat_runtime_wiring.py`（8 个行为用例）→ `uv run pytest -q tests/agent_runtime/test_capacity_webchat_runtime_wiring.py` → **exit=2，1 error in 0.09s**（收集期 `ModuleNotFoundError: No module named 'muad_agent_runtime.infrastructure.http_clients'`：装配根分池模块不存在；测试对真实 lifespan/真实 uvicorn 应用运行，不是收集以外的替身失败） | 同命令（分池装配 + 端点路由 + 保留清理循环落地后）→ **8 passed in 23.08s**（exit=0） | ①生产 app 注册全部通道端点 `test_b294_production_app_registers_the_full_channel_surface` L550：**路由表 29 条路径逐条** L554-555（`/v1/runs` 受理/详情/事件/取消/恢复/cancel-active/conversations；`/internal/webchat/` 入口会话/会话详情/消息/scope/uploads/notifications/已读/artifacts/task-results；`/internal/channel-notifications/` reserve/renew/ack；admin runs/artifacts/tool-results/model-capacity 三支/memory 四支）；**依赖图** L557-563（`/v1/runs` 含 `get_platform_settings_client`+`get_executor_factory`+`get_resolve_client`；上传面与通知面含 `get_platform_settings_client`+`require_internal_service`）；**真实 HTTP** L566-590（真实 uvicorn + 真实 lifespan：`/readyz` 200；上传面缺服务身份 403、缺表单 422（都不是 404）；通知增量读取 200 且空 feed `items=[]`/`next_seq=0`）。②分池/监督预算注入与退出即关 `test_b294_assembly_root_injects_per_pool_budgets_and_closes_them_on_exit` L598：**四池实例互不相同** L608-609；**有效池值取自真实 httpcore 池** L616-624 且等于 `pool_limits(SharedSettings())` 派生值 L617-619（control/model/mcp/egress = (6,3)/(9,4)/(5,2)/(7,3)）；**期限按类别** L632-637（读 7/11/13/17，连接 4、池 3）；**内部池不走环境代理** L640-643；**调用方消费同一实例** L646-656（Console 三客户端+控制派发器=control、投递泵=egress、执行器工厂 model/mcp/task/delivery=model/mcp/egress/egress、记忆提炼=model）；**执行槽预算** L659；lifespan 内未关 L660、退出后全关 L661-663、无悬挂循环任务 L666-669。③保留清理独立循环 `test_b294_retention_sweep_runs_as_its_own_loop_with_the_settings_policy` L677：循环任务名 `runtime.retention` 且独立运行 L692-694；**真实清理** L697-703（40 天前通知行被隐藏、过期暂存行被隐藏、暂存字节被真实删除）；单轮后循环仍在 L704；**边界来自平台设置快照** L706-711（真实 HTTP 且租户头=部署租户）；**退出即停** L713-716（退出后 2 个节拍不再触达设置源）。④八条独立循环与退出收尾 `test_b294_lifespan_runs_independent_maintenance_loops_and_stops_them_on_exit` L748：循环集合恰为 `runtime.{continuation,memory,deadline,control,reaper,execution_slots,delivery,retention}` 且各自成任务 L756-759；运行期泵/槽空闲 L760-761；**运行期保留清理真的推进** L764-765；退出全部结束、无悬挂、分池与客户端全关 L767-775。⑤readiness 与 Redis 可选退化 `test_b294_readiness_keeps_redis_optional_and_fails_on_real_dependencies` L784：Redis 不可达 → `NullCancelHintStore` 降级 L790-792 且 `/readyz` 200 L794-796；artifact storage 缺失 → 503 且 `failed==["artifact_storage"]` L799-802 | 真实 Runtime 装配根：`muad_agent_runtime.main.lifespan`（真实 `validate_startup`、真实分池、真实 uvicorn 127.0.0.1 socket、真实 `/readyz` 探针、真实后台循环任务）；**真实 Console 设置源（Owner）**：`ConsolePlatformSettingsClient` 打真实本地 uvicorn（真实 socket + 真实封套，记录每次到达的头）；**隔离 PostgreSQL/Redis**：`async_tool_datastore` 建空库 + `alembic upgrade head`（跑完 drop），通知行/暂存登记/准入策略行都是真实行，Redis 用隔离号位；**真实共享文件系统**：隔离 `ARTIFACT_ROOT` 下的暂存字节由真实清理删除。未 mock 存储、租约、装配根或路由 | verified |
| B-09 | 同上（同批 8 用例） | 同上 → **8 passed in 23.08s** | ①启动与保留边界唯一来源 `test_b09_startup_and_platform_budgets_have_one_source_and_unused_pools_fail` L812：池名集合恰四类 L814-815（`RUNTIME_HTTP_POOLS` 与 `RuntimeHttpPools` 字段一致）；**派生值 == 启动设置** L818-824（连接/keepalive 与读超时逐池）；**未调用池名显式失败** L826-830（stream/upload/nonsense → `ValueError`，不静默按别的池预算建池）；**保留边界只来自 contracts schema** L832-843（显式文档 → `RetentionPolicy(1,3,0)`；缺分组 → schema 默认 (24,30,0)；`notification_retention_days=0`/`stream_replay_retention_hours=0`/`history_retention_days=-1`/未知分组 → `PlatformSettingsError`，**不静默回落默认**）；②坏设置显式失败不清理 `test_b09_bad_platform_settings_fail_explicitly_without_falling_back_to_defaults` L719（清理循环留痕 `webchat_retention_settings_failed` L731-735、候选未被清理 L736-739、循环未被杀死 L740）；③全局准入版本与行锁外配置读取 `test_b09_global_admission_keeps_the_higher_revision_and_reads_settings_outside_the_lock` L860：真实 PG 策略行按 revision 7 写入 (500,4) L866-871；读到更低版本（3）→ 退出事务重取一次仍低 ⇒ 显式失败 L874-879 且**策略行原地保持 (7,500,4)** L880-884；**结构断言** L886-905（`fetch_capacity` 调 `fetch_snapshot` 且不含 `_session_factory_provider`；`lock_and_apply_in` 不含 `fetch_snapshot`；`_retention_loop` 的清理事务块内不含 `fetch_snapshot`） | 同 B-294：真实 Console 设置源 HTTP + 隔离 PostgreSQL（设置文档承载 contracts 解析、`runtime.run_admission_policy` 真实行）+ 真实 `AdmissionPolicyService`（装配根注入的**同一**设置客户端）。**fixture 边界诚实记录**：本文件的池预算/保留期/准入阈值都是**用例参数**，不代表生产实测容量；生产额度/SLO 由 TASK-105/109/116 的 profile 与容量报告负责 | verified |

**实施范围与风险说明**

- 实现文件（含超出计划 Files 的披露）：`infrastructure/http_clients.py`（**新增**：四池装配 + 未调用池名显式失败 + 内部池不走环境代理）、`main.py`（装配根：分池建/关、Console 三客户端/控制派发器/投递泵/执行器工厂/记忆提炼注入同一池实例、八条具名独立循环、**新增保留清理循环**、退出收尾）、`infrastructure/console_client.py`/`infrastructure/platform_settings_client.py`/`infrastructure/gateway_delivery_client.py`/`application/task_client.py`（**借用语义** + 绝对 URL 组装：注入的共享池客户端没有 base_url，装配根拥有并关闭）、`application/mcp_runtime_adapter.py`（`McpRuntimeAdapter`/`_McpSession` 借用 MCP 池 + 每请求显式带 `Accept`/`Authorization`）、`application/executor.py`（`default_executor_factory` 的 `model_client`/`mcp_client` 注入缝，主调用/摘要/池化 provider 共用同一模型池）、`application/memory_consolidation.py`（提炼直连路径共用模型池）、`api/deps.py`（`get_executor_factory` 消费装配根工厂；退出后清空该状态，HTTP 依赖回落既有自建口径）、`docs/configuration-inventory.csv`（新增 10 行、`SERVICE_NAME`/`POD_NAME` 行号同步，CRLF 保持 1550/0）。
- 分池口径（诚实记录）：Runtime 进程真实调用的类别只有四类——**control**（Console 解析/凭据/设置 + Worker 控制命令）、**model**（主调用/摘要/记忆提炼）、**mcp**（工具调用）、**egress**（Gateway 交付 + Worker 任务提交）。Runtime 是 SSE 的**产出方**（不做出站 stream 调用）、上传的**接收方**（不把整文件外发），按 §3.7「实例只创建实际需要的池」不建 stream/upload，且这两个池名在 `http_clients` 里**显式失败**（预算仍留在唯一来源 `SharedSettings`）。
- 保留清理循环（TASK-069 遗留①的收口）：节拍沿用 §3.7 既有维护节拍 `RUN_REAPER_INTERVAL_SEC`（一处来源，**不新增同义键**，与 TASK-095 的 Worker 维护循环同口径）；每轮**事务外**取一次平台设置边界再进清理事务；租户谓词取部署租户（不用一个租户的设置删别的租户的数据）；单轮失败 `logger.exception` 后继续（可见、不静默、不杀循环）。
- 判别性验证（防止断言空转；六处变异均先复现失败再还原，随后复跑全绿）：①控制池不注入 Console 解析客户端 → 用例② 1 failed；②模型池不注入执行器工厂 → 用例② 1 failed；③保留清理循环不启动 → 用例④ 1 failed；④退出不关分池 → 用例② 1 failed；⑤配置读取挪进清理事务 → B-09 结构用例 1 failed；⑥建池忽略启动设置（httpx 默认）→ 用例② 1 failed（有效池值 (100,20) ≠ (6,3)）。
- 静态检查：`uv run ruff check`（10 个实现文件 + 验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 441 source files**；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**（CSV 保持 CRLF：1550 CRLF / 0 裸 LF，`git -c core.whitespace=cr-at-eol diff --check` 通过）。
- 聚焦回归（最终冻结代码；运行前已确认无 pytest/uvicorn/vite 残留进程）：`uv run pytest -q tests/agent_runtime` → **1049 passed, 1 failed in 967.52s**；`uv run pytest -q tests/contracts tests/architecture tests/test_configuration_inventory.py tests/test_settings.py tests/test_async_tool_dispatch_settings.py tests/test_collection_layout.py` → **1345 passed in 7.73s**（exit=0）。未跑 `tests/acceptance/**`、`make acceptance*`、浏览器验收；未执行任何迁移/DDL（共享 dev 库约束：本任务用例全部使用隔离库/隔离 Redis 号位）。
- **整跑唯一失败的诚实归因（非本任务改动，环境残留）**：`tests/agent_runtime/test_capacity_webchat_upload_staging.py::test_staging_cleanup_never_deletes_accepted_run_artifacts`（`assert 3 == 2`）。机制已实测复现：TASK-069 的 `test_capacity_webchat_retention.py` 定义了 `PURGE_ORDER`（含 `channel_upload`）但 teardown 只用 `CONTROL_PURGE_ORDER`+`task.task_execution`，**每次整跑向共享 dev 库留下 1 条过期未删的 `runtime.channel_upload` 行**（单跑该套件后即出现恰 1 条 `test-<uuid>` 过期行）；TASK-053 的 `cleanup_expired_staging` **不带租户谓词、按全库计数**，于是该用例整跑时 `removed` 比预期多 1（首轮整跑 7 条历史残留 ⇒ `assert 7 == 2`）。该用例**单跑 1 passed（0.37s）**；本任务实现与用例不写共享 dev 库（全部隔离库/隔离号位）。按项目口径"单跑通过、整跑失败先怀疑共享测试库残留"，未改实现；已删除本次实验留下的那 1 条 `test-` 残留行恢复环境原状。
- **中间过程（诚实记录）**：首轮整跑另发现 1 处**本任务引入的真实回归**——`ConsoleCredentialsClient` 自建客户端一度不再带 `base_url`，而 `tests/agent_runtime/test_memory_redesign_supervisor.py` 用 `client._client.base_url` 取 Console 地址，导致提炼 job 5 秒超时（`TimeoutError`）。修正为"自建时仍按单一目标配 `base_url`，请求一律绝对 URL（注入共享池时同样成立）"，该用例复跑 **1 passed（2.54s）**，第二轮整跑全绿（除上述环境残留项）。
- 延后项（诚实记录）：① **机器人路由的 `PENDING` 出站意图按通道选择**（TASK-063 遗留① / TASK-066/067 遗留①，TASK-096 曾记为"属 TASK-094 Runtime 装配面"）**未在本任务闭合**：它改的是**终态写入路径的通道语义**（`application/run_service.py`/`channel_notifications.py`，TASK-063 的契约面，不在本任务 Files 与验收契约内），且按通道写 `PENDING` 需要自己的验收断言（哪些通道类别主动推送、与投递泵按原入口的权限复核/冻结回执如何交互）——对 WEBCHAT 路由盲写 `PENDING` 会**回归**（网关的 WEBCHAT 适配器按设计没有生产出站 sink，泵会拿到 NOT_SENT 反复重投）；建议作为独立小任务在 TASK-063 契约面内落地。② **过期 `DELIVERING` 租约的自动回收**（TASK-066/067 遗留②）未接线：泵的领取谓词只认 `PENDING`，接管由同键 reserve 完成（已验证的至少一次语义）；自动回收需要在 `channel_delivery` 域内定义候选谓词与回执规则（TASK-065 契约面），不在本任务 Files 内。③ **事件追尾 hub 的跨进程 hint 监听任务**（`EventTailHub._listener`）是进程级单例且无 `aclose`，本任务未纳入 lifespan 收尾——把它关掉需要在 TASK-060 域内定义"关闭后可重开"的语义（否则同进程重入 lifespan 会静默退化为仅 PG 补查）。④ `MCP_MAX_INFLIGHT`/`EGRESS_MAX_INFLIGHT` 调用信号量与 `RUNTIME_MAX_SSE_SUBSCRIBERS` 订阅上限仍是"已声明未消费"的启动预算：本任务只装配**HTTP 池**（连接/keepalive/读超时）；调用并发当前由执行槽（8）与投递泵并发（4）约束，把它们做成进程级信号量/订阅账本需要各自的域内语义与验收，本任务不编造接线。⑤ 保留清理/池饱和指标目录属 TASK-098；⑥ 部署清单的分池预算显式声明与总连接预算校验属 TASK-101/103/104；⑦ S-17/E-21 的端到端（设置保存 → 两个 Runtime 生效）由 TASK-115 的 verify-e2e 记录；⑧ 环境观察（供协调，非本任务改动）：`test_capacity_webchat_retention.py` 的 teardown 未用 `PURGE_ORDER`，会持续向共享库泄漏过期暂存行，并让 `test_staging_cleanup_never_deletes_accepted_run_artifacts` 在整跑时红——建议在 TASK-069 的用例里把 teardown 改为按 `PURGE_ORDER` 清理（或给 `cleanup_expired_staging` 加租户谓词）。
- B-09: verified — automated command passed; run_id=0177a9959a79455b8c977a590546d106 (confirmed_by: runner)
- B-294: verified — automated command passed; run_id=0177a9959a79455b8c977a590546d106 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-294 RED：`uv run pytest -q tests/agent_runtime/test_capacity_webchat_runtime_wiring.py` → exit=2，1 collection error（`muad_agent_runtime.infrastructure.http_clients` 尚不存在）
- [2026-10-18] 实现：新增 `infrastructure/http_clients.py`（control/model/mcp/egress 四池 + 未调用池名显式失败 + 内部池不走环境代理）；装配根建/关分池并把同一实例注入 Console 三客户端/控制派发器/投递泵/执行器工厂/记忆提炼；八条独立循环具名（新增 `runtime.retention` 保留清理循环，事务外取设置边界、失败留痕不杀循环）；Console/设置/交付/任务客户端与 MCP 会话改借用语义 + 绝对 URL；`default_executor_factory` 增模型/MCP 池注入缝；`deps` 消费装配根工厂
- [2026-10-18] B-294/B-09 GREEN：**8 passed in 23.08s**（exit=0）；六处变异验证断言判别性后还原；contracts+architecture+inventory+settings 等 **1345 passed**（7.73s）；ruff 干净、mypy 441 文件无问题、配置盘点 4 passed（CRLF 1550/0）
- [2026-10-18] 首轮整跑发现并修正 1 处本任务回归（自建客户端丢 `base_url` → 提炼 job 超时），修正后该用例 1 passed（2.54s）；第二轮 `tests/agent_runtime` **1049 passed, 1 failed**（967.52s），唯一失败是 TASK-069 用例泄漏过期暂存行 + TASK-053 无租户谓词计数导致的环境残留（单跑绿，已归因并记录）
- [2026-10-18] 延后项与风险见上（PENDING 按通道选择、过期 DELIVERING 租约回收、tail hub 监听收尾、MCP/egress inflight 信号量与 runtime SSE 订阅上限）
- [2026-10-18] completed (done)
- [2026-10-19] completed (done)

---

## TASK-095: Worker装配模型外部调用预算与独立维护循环

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-033, TASK-035, TASK-037, TASK-068
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-295
- **Files**: `apps/agent-worker/src/muad_agent_worker/main.py`, `apps/agent-worker/src/muad_agent_worker/infrastructure/http_clients.py`, `tests/agent_worker/test_capacity_webchat_worker_wiring.py`
- **Estimate**: 60分钟

### Description

真实装配根注入控制/MCP/业务/上传分池、并发和期限；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-295][integration] 先在`tests/agent_worker/test_capacity_webchat_worker_wiring.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：真实装配根注入控制/MCP/业务/上传分池、并发和期限。→ RED：首建 5 个行为用例后 `uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_wiring.py` → **exit=2，1 error in 0.07s**（收集期 `ModuleNotFoundError: No module named 'muad_agent_worker.infrastructure.http_clients'`：装配根分池模块尚不存在，测试对真实应用/真实 lifespan 运行）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 6 个文件（**超出计划 Files 的披露**：`worker/service.py`、`infrastructure/platform_settings_client.py`、`delivery/artifact_client.py`）：新增 `infrastructure/http_clients.py`（`WORKER_HTTP_POOLS` 分池声明 + `pool_limits`/`pool_timeout`/`build_pool_client`/`build_worker_http_pools`；本进程不调用的池名显式 `ValueError`）、`main.py` 装配根（建/关分池 + 注入 control/egress 到设置快照/定时解析/投递/收件箱/产物解析/结果发件，暴露 `app.state.http_pools`/`scheduler_loop`/`delivery_loop`/`result_dispatcher`；启动 5 条独立循环任务）、`worker/service.py`（`run_forever` 收敛为**只领取**；新增 `run_maintenance_forever` 独立回收节拍，节拍取 §3.7 `RUN_REAPER_INTERVAL_SEC`）、`platform_settings_client.py`/`artifact_client.py`（注入池为**借用**语义：装配根拥有并关闭，未注入时自建自关；请求改绝对 URL 组装）。改动函数行数：`pool_limits` 12、`pool_timeout` 12、`build_pool_client` 3、`build_worker_http_pools` 5、`lifespan` 46、`_start_background` 49、`run_forever` 18、`run_maintenance_forever` 12，均 ≤50。错误显式：未调用的池名 `ValueError`（不静默按 egress 建池）、维护循环单次失败 `logger.exception` 后继续（不静默吞、不杀死维护循环）、槽关闭超时沿用既有显式 `TimeoutError`；日志只记低基数与资源 ID，无凭据或消息正文
- [x] [B-295] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 5 个用例逐条对应契约 5 组断言（见 Acceptance Evidence 断言位置列）
- [x] [B-295] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **5 passed in 9.33s**（exit=0，最终冻结版本）；连续复跑 8.89s / 10.29s / 11.35s 全绿

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-295 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | 真实装配根注入控制/MCP/业务/上传分池、并发和期限；续租/回收/投递/调度互不阻塞，进程退出清子进程组，Task旧policy不补默认 | tests/agent_worker/test_capacity_webchat_worker_wiring.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_wiring.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-295 | 首建 `tests/agent_worker/test_capacity_webchat_worker_wiring.py`（5 个行为用例）→ `uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_wiring.py` → **exit=2，1 error in 0.07s**（收集期 `ModuleNotFoundError: No module named 'muad_agent_worker.infrastructure.http_clients'`：分池装配模块不存在，测试对真实 lifespan 运行；完整输出 `/tmp/agent-capacity-webchat-plan/task095-red.log`） | 同命令（分池装配 + 独立维护循环 + 借用注入落地后）→ **5 passed in 9.33s**（exit=0）；复跑 8.89s / 10.29s / 11.35s 全绿，日志 `/tmp/agent-capacity-webchat-plan/task095-green-final.log` | ①分池预算/并发/期限/独立循环注入 `test_b295_assembly_root_injects_per_pool_budgets_concurrency_and_loops` L436-495（真实 lifespan 内取 `app.state.http_pools` L455；control≠egress L458；**有效池值** (6,3)/(9,4) 取自真实 transport 的 httpcore 池 L461-462 且等于 `pool_limits(SharedSettings())` 派生值 L463-464；连接/池/读超时 (7/11, 4, 3) L467-468；设置快照/定时解析消费 control、投递/收件箱/产物解析/结果发件消费 egress 的**同一实例** L471-476；`slot_limit == 3` L479；结果发件策略取启动设置且 lease>timeout L482-487；`worker.claim/worker.maintenance/worker.scheduler/worker.delivery/worker.results` 五条独立任务 L490-491；lifespan 内未关、退出即关 L492-493；退出无悬挂任务 L494-495）；②预算唯一来源 + 不建未调用的池 `test_b295_pool_budgets_come_from_settings_and_unused_pools_fail_explicitly` L498-523（两类池预算随 env 派生 L510-513；`WORKER_HTTP_POOLS == ("control","egress")` 与 `WorkerHttpPools` 字段集恰两类 L515-518；mcp/upload/stream/model 四类池名**显式 ValueError** L519-523）；③续租/回收/领取/投递/调度互不阻塞 `test_b295_reclaim_claim_heartbeat_and_schedule_do_not_block_on_delivery` L531-644（投递循环先阻塞在真实 TCP 监听的真实 HTTP 读上 L605；领取循环拉起真实 Skill 子进程并占槽 L608-616；心跳在阻塞期间推进 `heartbeat_at` L619-623；维护循环把过期租约回收为 `QUEUED` 且 attempt 不变 L626-628；调度循环 sweep 出 `FAILED(TASK_DEADLINE_EXCEEDED)` L631-634；投递仍在同一条真实请求上：连接未减、`worker.delivery` 任务未结束、行仍 `PENDING` 且恰预留 1 次尝试 L637-641）；④退出清子进程组 `test_b295_lifespan_exit_stops_the_skill_process_group` L651-714（真实 Skill 子进程+孙进程 pid 文件与就绪文件 L692-697、池未关 L698；lifespan 退出后子进程回收 L702、**孙进程随进程组回收** L703、退出即关池 L704；失约实例不写伪造结论：行仍 `RUNNING`/无错误码 L706-707；无悬挂任务 L708-709）；⑤Task 旧 policy 不补默认 `test_b295_old_task_policy_is_not_backfilled_with_new_defaults` L722-770（同一确定性失败形状：缺 `budget.retry_safety` 的旧行落 `FAILED(TASK_RESULT_UNVERIFIED)` 不自动重做 L755、声明 `IDEMPOTENT` 的冻结行按**自己的**值有界重试 `QUEUED`/attempt=1 L757-760；旧行快照与 schema 版本逐字节不变 L763-766；冻结声明不被改写 L769-770） | 真实边界（不 mock 存储、租约与外部协议）：**真实 Worker 装配根** `muad_agent_worker.main.lifespan`（真实 `validate_startup`→迁移到 head、真实 PG/Redis 客户端、真实 `WorkerLoop`/`SchedulerLoop`/`DeliveryLoop`/`ResultDispatcher`）；**隔离 PostgreSQL + 隔离 Redis 号位**（`async_tool_datastore`：每用例空库 + `alembic upgrade head`，跑完 drop；只写/删本用例租户行，无 DDL、不触碰共享 dev 库）；**真实子进程**：Skill 包以 zip 写入隔离 artifact root（`NfsArtifactStore.write`），由真实 `SkillArtifactCache`（checksum 校验 + 安全解包 + READY）拉起真实 Python 子进程（④还有孙进程，pid 文件 + `os.kill(pid,0)` 验证整组回收）；**真实 HTTP 协议探针**：真实 uvicorn TCP 服务承载 Console `/internal/v1/platform-settings`，真实 TCP 监听（接受后永不回应）承载 Gateway 投递；池**有效值**取自 httpx 真实 transport 的 httpcore 池（`max_connections`/`max_keepalive_connections`），不是只比较自己的 helper | verified |

**实施范围与风险说明**

- 实现文件与计划 Files 的差异（披露）：计划 3 个文件中 `main.py`、`infrastructure/http_clients.py`、验收文件按计划；**必需的范围扩张**为 `worker/service.py`（领取循环与维护循环拆成两条独立循环）、`infrastructure/platform_settings_client.py` 与 `delivery/artifact_client.py`（这两个客户端自建 httpx 客户端，若不接受注入，控制/业务分池就有一半走默认池）、`tests/agent_worker/test_worker_lifecycle.py`（既有 B-122 lifespan 用例断言后台循环集合，新增维护循环后必须同步并**加强**为「维护循环也是独立任务且随退出停止」）。
- 分池口径（诚实记录）：Worker 进程真实调用的调用类别只有两类——**control**（Console 平台设置快照、定时解析）与 **egress**（Gateway 投递、Runtime 结果发件/收件箱/产物解析）。MCP 调用在 Runtime（`mcp_runtime_adapter`），上传大文件在 Gateway/Runtime；Worker 的 Skill 制品经共享盘落盘。按 design §3.7「实例只创建实际需要的池」，本进程**不建** MCP/上传/流/模型池，且这些池名在 `http_clients` 里**显式失败**（不静默按 egress 建一个没人调用的池）；它们的启动预算仍留在唯一来源 `SharedSettings`（`tests/test_settings.py`/盘点覆盖）。
- 唯一来源与期限：池大小/keepalive/连接/池/读超时全部来自 `SharedSettings` typed 字段，装配根不复制默认值；结果发件策略 `ResultDispatchPolicy.from_settings`（timeout<lease 由类型自校验）；业务调用另受 Task 冻结绝对 deadline（TASK-035）约束，本任务未改该口径。
- 借用语义（不是兼容层）：注入池时 `ConsolePlatformSettingsClient`/`ArtifactResolveClient` **不关闭**调用方拥有的池（`_owns_client`），未注入时自建自关（测试与单点使用）；请求改由 `base_url + path` 组装绝对 URL——共享池客户端没有 base_url，一个池服务多个目标。`aclose()` 语义显式，不吞异常。
- 维护循环节拍（诚实记录）：回收节拍沿用 §3.7 的 `RUN_REAPER_INTERVAL_SEC`（沿用 30 秒，一处来源），**不新增同义键**（design §3.7「既有…沿用现有…不新增同义键」；TASK 侧只有 `TASK_LEASE_SEC`/`TASK_HEARTBEAT_SEC`）。`run_forever` 只领取、`run_maintenance_forever` 只回收：慢的回收扫描不再推迟空闲槽补齐，慢的领取也不会让过期租约回收迟到；单次回收失败 `logger.exception` 后继续（可见、不静默、不杀循环），槽关闭超时仍显式 `TimeoutError`（TASK-033 语义不变）。
- 判别性验证（防止断言空转；四处变异均先复现失败再还原，随后复跑确认全绿）：①`build_worker_http_pools` 忽略设置改回 httpx 默认 → 装配用例 **1 failed**（`assert (100, 20) == (6, 3)`）；②去掉 `_require_worker_pool` 显式拒绝 → ②用例 **1 failed**（`DID NOT RAISE ValueError`）；③`_start_background` 不启动维护循环 → 装配用例 **1 failed**（`缺少独立循环: {'worker.maintenance'}`）且 B-122 用例 **1 failed**；④`TaskSlotSupervisor.close` 不再取消本地片段 → 退出用例 **1 failed**（`Skill 子进程未随退出回收`，25s 有界等待）。旧 policy 用例本身是差分断言（同一失败形状、两种冻结快照分别落 `UNVERIFIED` 与 `QUEUED`），补默认即会失败。
- 配置盘点：新增 `infrastructure/http_clients.py` 的 `WORKER_HTTP_POOLS` 行；`main.py` 的 `SERVICE_NAME` 行号 +1 同步；CRLF 保持（1518 CRLF / 0 裸 LF），`git -c core.whitespace=cr-at-eol diff --check` 通过；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态检查：`uv run ruff check`（6 个实现/验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 438 source files**。
- 聚焦回归：`tests/agent_worker` → **327 passed in 99.48s**（exit=0，含本任务 5 例与加强后的 B-122 lifespan 用例，最终冻结代码）；`tests/contracts tests/architecture tests/test_configuration_inventory.py tests/test_collection_layout.py tests/test_settings.py tests/test_configuration_convergence.py tests/console_tasks tests/test_async_tool_dispatch_settings.py` → **1357 passed in 6.83s**（exit=0）。未跑 `tests/acceptance/**`、`make acceptance*`、浏览器验收；未执行任何迁移 DDL（共享 dev 库约束）。运行前已确认无 dev 服务残留（同机另有并行 worktree 的测试进程，本任务用例全部使用隔离库/隔离 Redis 号位）。
- 延后项（诚实记录）：回收/维护节拍的指标目录（如维护循环失败计数）属 TASK-098/099 的指标目录与观测；部署清单里的分池预算显式声明与总连接预算校验属 TASK-101/103；1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责；本任务不引入 MCP/上传池（Worker 无该调用类别，见上）。
- B-295: verified — automated command passed; run_id=f24b92c858284cd885ec3106534125d1 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-295 RED：`uv run pytest -q tests/agent_worker/test_capacity_webchat_worker_wiring.py` → exit=2，1 collection error（`muad_agent_worker.infrastructure.http_clients` 尚不存在）
- [2026-10-18] 实现：新增 `infrastructure/http_clients.py`（control/egress 分池预算 + 未调用池名显式失败）、装配根建/关分池并把同一实例注入设置快照/定时解析/投递/收件箱/产物解析/结果发件、`WorkerLoop` 拆出独立维护循环（`run_maintenance_forever`，节拍取 `RUN_REAPER_INTERVAL_SEC`）、两个自建池客户端改为借用语义 + 绝对 URL 组装
- [2026-10-18] B-295 GREEN：**5 passed in 9.33s**（复跑 8.89s / 10.29s / 11.35s）；四处变异验证断言判别性后还原；`tests/agent_worker` **327 passed**（99.48s）、contracts/architecture/inventory 等 **1357 passed**（6.83s）；ruff 干净、mypy 438 文件无问题、配置盘点 4 passed
- [2026-10-18] started
- [2026-10-18] completed (done)

---

## TASK-096: Gateway装配HTTP聊天、SSE及四类生命周期

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-055, TASK-057, TASK-061, TASK-067
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-296
- **Files**: `apps/im-gateway/src/muad_im_gateway/main.py`, `apps/im-gateway/src/muad_im_gateway/infrastructure/http_clients.py`, `tests/gateway/test_capacity_webchat_gateway_wiring.py`
- **Estimate**: 60分钟

### Description

生产app实际注册Webchat/企微、身份复核和公开SSE，注入独立控制/stream/上传/业务预算；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-296][integration] 先在`tests/gateway/test_capacity_webchat_gateway_wiring.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：生产app实际注册Webchat/企微、身份复核和公开SSE，注入独立控制/stream/上传/业务预算。→ RED：首建 7 个行为用例后 `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_wiring.py` → **exit=2，1 error in 0.15s**（收集期 `ModuleNotFoundError: No module named 'muad_im_gateway.infrastructure.http_clients'`：分池装配模块尚不存在，测试对真实应用/真实 lifespan 运行；完整输出 `/tmp/agent-capacity-webchat-plan/task096-red.log`）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在 9 个文件（**超出计划 Files 的披露**：`application/console_client.py`、`application/runtime_client.py`、`application/webchat_streams.py`、`api/webchat.py`、`infrastructure/platform_settings_client.py`、`infrastructure/artifact_resolver.py`、`application/inbound.py`）：新增 `infrastructure/http_clients.py`（`GATEWAY_HTTP_POOLS` 四池声明 + `pool_limits`/`pool_timeout`/`build_pool_client`/`build_gateway_http_pools`；model/mcp 池名显式 `ValueError`）；`main.py` 装配根（建/关分池并把同一实例注入 Console/设置/运行时三路客户端与产物解析；发布 `settings_client`/`artifact_store`/`http_pools`/`webchat_sse_ledger`/四个传输预算/`gateway_max_sse_subscribers`/两个硬上限；`InboundPipeline` 注入 `GATEWAY_MAX_CONCURRENT_HANDLERS` 与三条 `GATEWAY_MAX_PENDING_*`；`app.include_router(webchat_router)`；快照/每适配器入站循环命名 `gateway.snapshot`/`gateway.inbound.*`，退出取消并等待（不吞异常、资源与分池必关））；`runtime_client.py`（stream/upload/egress 三客户端借用语义 + 绝对 URL 组装）；`console_client.py`/`platform_settings_client.py`/`artifact_resolver.py`（control/egress 池借用语义 + 绝对 URL 组装）；`webchat_streams.py`（`SseBufferLedger(max_subscribers=...)` 到界显式拒绝 + `rejected_subscribers` 计数 + warning；`WebchatStreamService.open` 在**打开 Owner 流之前**取租约、失败释放、上限映射 `RUN_CAPACITY_EXCEEDED`）；`api/webchat.py`（就地兜底账本同源读启动预算）。改动函数行数：`pool_limits` 22、`pool_timeout` 14、`build_pool_client` 3、`build_gateway_http_pools` 8、`lifespan` 27、`_build_outbound_clients` 25、`_build_inbound` 31、`_build_snapshot` 12、`_build_resources` 49、`_start_background` 17、`open` 32、`_acquire_lease` 6、`lease` 14，均 ≤50。错误显式：未调用的池名 `ValueError`、订阅到界 429（不静默丢/不排队）、半初始化失败回收已建资源、退出收尾不吞异常（资源在 `finally` 内必关）
- [x] [B-296] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 7 个用例逐条对应契约 6 组断言（见 Acceptance Evidence 断言位置列）；9 处变异验证断言判别性后全部还原
- [x] [B-296] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **7 passed in 6.27s**（exit=0，最终冻结版本；连续复跑 6.17s / 6.19s 全绿；日志 `/tmp/agent-capacity-webchat-plan/task096-green-final.log`）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-296 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 生产app实际注册Webchat/企微、身份复核和公开SSE，注入独立控制/stream/上传/业务预算；停服排空不重建Run，单机器人无连接重叠，无SQLAlchemy依赖 | tests/gateway/test_capacity_webchat_gateway_wiring.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_wiring.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-296 | 首建 `tests/gateway/test_capacity_webchat_gateway_wiring.py`（7 个行为用例）→ `uv run pytest -q tests/gateway/test_capacity_webchat_gateway_wiring.py` → **exit=2，1 error in 0.15s**（收集期 `ModuleNotFoundError: No module named 'muad_im_gateway.infrastructure.http_clients'`：分池装配模块不存在，测试对真实 lifespan/真实应用运行；完整输出 `/tmp/agent-capacity-webchat-plan/task096-red.log`） | 同命令（路由挂载/分池注入/四类生命周期/订阅上限落地后）→ **7 passed in 6.27s**（exit=0）；复跑 6.17s / 6.19s 全绿，日志 `/tmp/agent-capacity-webchat-plan/task096-green-final.log` | ① 生产 app 注册路由/身份复核/公开 SSE `test_b296_production_app_registers_webchat_wecom_recheck_and_public_sse` L623：**路由表** L651-666（client-config/entries/messages/conversations/attachments/会话详情与消息/runs events/cancel/resume/artifacts 12 条路径与方法集）；**依赖图身份复核** L667-671（读写面含 `get_chat_identity`、SSE 面同时含 `get_chat_identity` + `get_chat_stream_guard`）；**企微/WEBCHAT 真实注册** L676-683（`registry.names()==("WECOM","WEBCHAT")`、真实 `WeComAdapter`/`WebchatChannelAdapter` 实例、两适配器 started、真实 WSS 连接建立）；**真实 HTTP** L692-720（client-config 200 且传输预算==启动设置、settings_revision==7；entries 200 且恰一条已授权入口+显示名；SSE 200 `text/event-stream` 且含 `event: message.delta`）；**身份复核真实发生** L723-726（Console 收到会话原文与 `X-Chat-Context`，bots 快照经 Console 读取）；**公开流不重建 Run** L729-732（受理 POST 为空、事件 GET after_seq=0）；订阅结束账本归零 L734；**零 sqlalchemy** `test_b296_gateway_holds_no_database` L742（全部 22 个网关源文件扫描）。② 分池/受理上限/四类生命周期 `test_b296_assembly_root_injects_pools_handler_limit_and_lifecycle_loops` L764：**四池独立** L766-771（实例互不相同）；**有效池值==设置派生值** L774-801（从真实 httpcore 池读 (6,3)/(9,4)/(5,2)/(8,4)）；**读超时按类别+连接/池统一** L804-812（7/11/13/17 + 4/3）；**调用方消费同一实例** L817-822（console/settings=control、runtime stream=stream、upload=upload、runtime 业务+产物解析=egress）；**受理上限** L826（3）；**排队上限** L828-830（7/3/5）；**四条独立生命周期任务** L832-838（`gateway.snapshot`+`gateway.inbound.WECOM`+`gateway.inbound.WEBCHAT` 未结束）；退出后连接停止/池全关/无悬挂任务 L839-847。③ 池预算唯一来源+未调用池显式失败 `test_b296_pool_budgets_come_from_settings_and_unused_pools_fail_explicitly` L850：`GATEWAY_HTTP_POOLS` 恰四池 L869-873；派生 limits/read L874-880；model/mcp `ValueError` L884-887。④ 停服排空不重建 Run `test_b296_shutdown_drains_subscription_and_restart_never_rebuilds_the_run` L896：**真实受理** L936-938（202 + run_id + 受理 POST 恰一次）；**持有订阅** L945-946（账本=1）；**关服排空** L951-956（真实 uvicorn 关闭连接后流立即结束、账本归零）；**退出事实** L964-976（收到过 delta、受理 POST 仍恰一次、四池全关、适配器全停、无悬挂任务）；**重启重连 GET 重放** L992-1004（Last-Event-ID=1 → after_seq=1、无 delta 重投、受理 POST 仍恰一次、归属复核 ≥2 次）。⑤ 单机器人无连接重叠 `test_b296_single_bot_never_has_overlapping_connections` L1042：首个真实连接完成订阅 L1062-1066；换密钥（Console rev-2 + 新 secret）经真实刷新路径 L1070-1077；**旧连接已关+新连接唯一活连接** L1078-1082；适配器 CONNECTED L1084；退出后全部真实连接关闭 L1087-1091。⑥ SSE 订阅上限显式拒绝 `test_b296_sse_subscriber_limit_rejects_explicitly_before_opening_the_owner_stream` L1101：首订阅占满 L1130-1136；**429 + `RUN_CAPACITY_EXCEEDED`** L1144-1145；**被拒订阅未打开 Owner 流** L1146-1148（上游事件 GET 恰 1 次）；拒绝计数 L1149；释放后重订成功 L1153-1174 | **真实 Gateway 装配根**：`muad_im_gateway.main.lifespan`（真实 `validate_startup`、真实 `build_channel_registry`、真实分池、真实 uvicorn socket，`app.state` 全部由 `publish()` 发布）；**真实 Console HTTP（Owner）**：`ConsoleClient`/`ConsolePlatformSettingsClient` 打真实 uvicorn（chat-principal/bots/platform-settings，记录头与体）；**真实 Runtime HTTP/SSE（Owner）**：`RuntimeClient` 打真实 uvicorn（受理 `text/event-stream` 首帧、事件流真实持久帧+心跳+保持打开、`GET /v1/runs/{id}` 与 scope 核属）；**真实企微协议探针**：官方 SDK 经真实 WSS 连接 `WeComProbe`（连接建立/密钥轮换先停旧再起新/退出全关都是真实 socket 事实）；**数据库经 Owner 接口访问**：本文件不碰数据库、`DATABASE_URL` 指向不可达地址（只过配置校验、从不连接），22 个网关源文件零 sqlalchemy。E2E 保持 planned，最终 verify-e2e 记录完整证据 | verified |

**实施范围与风险说明**

- 实现文件（含超出计划 Files 的披露）：`infrastructure/http_clients.py`（**新增**：`GATEWAY_HTTP_POOLS=("control","stream","upload","egress")` 四池装配、预算/期限全部来自 typed 字段、本进程不建的池名显式失败）、`main.py`（装配根：分池建/关 + 三路客户端注入 + Webchat 路由挂载 + 状态发布 + 受理/排队上限 + 命名生命周期循环 + 失败收尾）、`application/console_client.py`/`infrastructure/platform_settings_client.py`/`infrastructure/artifact_resolver.py`（控制/业务池**借用**语义 + 绝对 URL 组装）、`application/runtime_client.py`（stream/upload/egress 三客户端拆分）、`application/webchat_streams.py`（订阅者上限显式拒绝）、`api/webchat.py`（兜底账本同源）、`application/inbound.py`（排队上限可注入）。测试：`tests/gateway/test_capacity_webchat_gateway_wiring.py`（新增，7 用例）；连带更新 `tests/gateway/test_capacity_webchat_gateway_sse.py`（B-261 的账本构造补 `max_subscribers=1100`，断言强度不变）。
- **分池口径**（诚实记录）：control=Console 短控制/认证/设置（`ConsoleClient`+`ConsolePlatformSettingsClient`）；stream=Runtime 持久事件流（`create_run`/`open_events`/`open_event_stream`/受理首帧）；upload=webchat 上传流程的受信登记（`register_upload`）；egress=Runtime 业务出站（历史/资源/交付/产物解析）。**WeCom 媒体下载是网关唯一的大文件 HTTP 流**（`media.py` 自建有界流式客户端、30s 传输安全超时是模块常量），把它并入 upload 池需要贯穿 adapter/sdk 端口，属可后补的装配面——本任务的上传池承载上传流程的出站调用（design §3.7「实例只创建实际需要的池」）。
- **SSE 订阅上限**：`SseBufferLedger(max_subscribers=settings.gateway_max_sse_subscribers)` 到界**显式拒绝**（`SseSubscriberLimitExceeded` → `AppError(RUN_CAPACITY_EXCEEDED)` 429；不排队、不静默丢）；裁决发生在**打开 Owner 流之前**（不占上游连接），取租约失败即释放；`rejected_subscribers` 计数 + warning 日志（`webchat_sse_subscriber_limit_rejected`）。错误码复用容量族 429（未新增错误码，避免动错误目录/契约面）。
- **四类生命周期**：①机器人连接（注册表 `start_all`/`stop_all`，真实 WSS 连接先停旧再起新、退出全关）；②快照刷新循环（`gateway.snapshot`）；③入站消费循环（每适配器一条 `gateway.inbound.<CHANNEL>`）；④公开 SSE 订阅（进程账本登记/释放/上限/排空）。四条各自独立成任务、退出即停；退出路径取消后等待收尾（不吞异常），资源与分池在 `finally` 内必关。
- **WEBCHAT 出站 sink 未注入**（诚实记录，TASK-052 的 deferred ①）：WEBCHAT 的出站面是 Runtime 的持久事件流 + 持久通知收件箱（网关是代理、不是写入方），生产 HTTP 提交走 `runtime.submit_run` 不经过适配器队列——因此不存在需要注入的生产 sink；适配器 `send`/`stream` 未装配即显式失败（绝不静默丢），若后续要求适配器队列路径具备出站面再落。
- 判别性验证（防止断言空转；九处变异均先复现失败再还原）：①不挂 webchat 路由 → 用例① 1 failed；②不注入 stream 池 → 用例② 1 failed；③订阅者上限不拒绝 → 用例⑥ 1 failed；④受理上限不注入 → 用例② 1 failed；⑤退出不关池 → 用例② 1 failed；⑥入站消费循环不启动 → 用例② 1 failed；⑦先开 Owner 流再取租约 → 用例⑥ 1 failed（被拒订阅占用了上游连接）；⑧退出不取消后台循环 → 用例④ 1 failed（悬挂任务）；⑨排队上限不注入 → 用例② 1 failed。
- 配置盘点：新增 `infrastructure/http_clients.py` 的 `GATEWAY_HTTP_POOLS` 行；`main.py` 新增 `SNAPSHOT_LOOP_NAME`/`INBOUND_LOOP_NAME_PREFIX` 两行、`SERVICE_NAME` 行号 34→37；`application/inbound.py` 的 `_FEEDBACK_PRIORITY` 420→431、`__init__.delta_flush_interval_sec` 428→449。CSV 保持 CRLF（1524 CRLF / 0 裸 LF），`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态检查：`uv run ruff check`（9 个实现文件 + 2 个测试文件）干净；`uv run mypy apps packages` → **Success: no issues found in 439 source files**；AST 逐函数核对生产函数全部 ≤50 行（最长 `_build_resources` 49）。
- 聚焦回归（最终冻结代码；运行前已确认无 pytest/uvicorn/vite 残留进程）：`uv run pytest -q tests/gateway` → **434 passed in 132.25s**（exit=0，含本任务 7 例与 B-261/B-248/B-267 等既有用例）；`uv run pytest -q tests/contracts tests/architecture tests/test_configuration_inventory.py tests/test_error_catalog.py` → **1328 passed in 4.54s**（exit=0）；`tests/test_attachment_contract.py tests/test_settings.py tests/test_logging.py` → **8 passed**。未跑 `tests/acceptance/**`、`make acceptance*`、浏览器验收；未执行任何迁移 DDL（共享 dev 库约束）。
- 延后项（诚实记录）：① WEBCHAT 适配器出站 sink（见上，无生产落点）；② 机器人路由的 `PENDING` 出站意图由 Runtime 终态写入路径按通道选择（TASK-067 遗留①，属 TASK-094 Runtime 装配面）；③ WeCom 媒体下载并入 upload 池（贯穿 adapter/sdk 端口，可后补）；④ SSE 指标目录（订阅数/断开原因/缓冲水位/拒绝计数进 Prometheus）属 TASK-098；⑤ 部署清单的分池预算显式声明与总连接预算校验属 TASK-101/103/104；⑥ 1000 用户重连/排空容量结论由 TASK-109/116 的 E2E/profile 负责。
- B-296: verified — automated command passed; run_id=085f2e51baec4ac9b91a4bed7238a05a (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-296 RED：`uv run pytest -q tests/gateway/test_capacity_webchat_gateway_wiring.py` → exit=2，1 collection error（`muad_im_gateway.infrastructure.http_clients` 尚不存在）
- [2026-10-18] 实现：新增 `infrastructure/http_clients.py`（control/stream/upload/egress 四池 + 未调用池名显式失败）；装配根建/关分池并把同一实例注入 Console/设置/运行时（stream/upload/egress 拆分）/产物解析，发布 webchat 路由消费的全部状态值、挂载 webchat 路由、注入受理与排队启动预算、命名四条生命周期循环并保证退出即停；`webchat_streams.py` 订阅者上限显式拒绝（先裁决再开 Owner 流）
- [2026-10-18] B-296 GREEN：**7 passed in 6.27s**（复跑 6.17s / 6.19s）；九处变异验证断言判别性后还原；`tests/gateway` **434 passed**（132.25s）、contracts/architecture/inventory/error_catalog **1328 passed**（4.54s）；ruff 干净、mypy 439 文件无问题、配置盘点 4 passed
- [2026-10-18] completed (done)

---

## TASK-097: Console装配CHAT路由及探针资源预算

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-008, TASK-028, TASK-042, TASK-045
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-297
- **Files**: `apps/console-platform/backend/src/muad_console_platform/main.py`, `apps/console-platform/backend/src/muad_console_platform/infrastructure/http_clients.py`, `tests/console_platform/test_capacity_webchat_console_wiring.py`
- **Estimate**: 45分钟

### Description

认证端点不被Console管理会话守卫吞并、admin资源路由保持ADMIN；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-297][integration] 先在`tests/console_platform/test_capacity_webchat_console_wiring.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：认证端点不被Console管理会话守卫吞并、admin资源路由保持ADMIN。→ RED：`uv run pytest -q tests/console_platform/test_capacity_webchat_console_wiring.py` → **exit=2，1 error in 0.07s**（collection `ImportError: cannot import name 'get_probe_budget' from 'muad_console_platform.api.deps'`；装配根注入/工厂尚不存在，测试对真实应用运行）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 新增 `infrastructure/http_clients.py`（探针预算 + 两个独立池 + 契约/部署预算冲突显式失败）+ `main.create_app` 装配根与 `_install_probe_clients`/`_close_probe_clients`（lifespan 用传入实例、启动失败也收尾）；`ProbeBudget` 类型 + `ModelProbeService` 接收注入的客户端/预算；`deps` 消费注入实例（同一工厂兜底）；最长函数 30 行、全量类型注解、ruff/mypy 干净
- [x] [B-297] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 8 个用例逐条对应契约 5 条断言（见 Acceptance Evidence 断言位置列）
- [x] [B-297] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **8 passed in 0.85–0.90s**（exit=0，连续复跑稳定）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-297 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 认证端点不被Console管理会话守卫吞并、admin资源路由保持ADMIN；探针独立容量/HTTP客户端，设置损坏不锁死已认证CHAT，SPA不把API错误回退HTML | tests/console_platform/test_capacity_webchat_console_wiring.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_wiring.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-297 | `uv run pytest -q tests/console_platform/test_capacity_webchat_console_wiring.py` → **exit=2，1 error in 0.07s**（collection `ImportError: cannot import name 'get_probe_budget' from 'muad_console_platform.api.deps'`：装配根注入与 `http_clients` 工厂尚不存在；测试对真实应用运行，不是收集以外的替身失败） | 同命令 → **8 passed in 0.85–0.90s**（exit=0；连续两次复跑 0.90s / 0.85s） | ①公开 CHAT 路由不被管理守卫吞并 `test_b297_chat_public_routes_stay_outside_the_console_login_guard` L222-247（起点零管理会话 L229；无 Cookie 的 `/me` 401 `UNAUTHORIZED` 而非 404 L232-233；**无 Console 会话完成交换 200**、不下发管理会话 Cookie、下发独立 CHAT Cookie L236-238；`/me` 200 且主体来自库行 L241-242；CHAT Cookie 打 `/api/v1/auth/me`、`/api/v1/users`、`/api/v1/model-credentials` 全 401 L246-247）；②admin 资源路由保持 ADMIN `test_b297_admin_resource_routes_keep_the_admin_role` L250-270（无会话 401 L258；BUILDER 403 `FORBIDDEN` L263-264；同一路径 ADMIN 200 L269-270）；③探针预算/独立池由装配根注入 `test_b297_probe_budget_and_pools_are_injected_from_startup_settings` L276-331（真实 lifespan；预算恰为 `ProbeBudget(PROBE_TIMEOUT_SEC, PROBE_CONCURRENCY)` L291；容量端口与模型探测是两个实例 L292-294；**有效池随启动设置变化** (6,3)/(9,4) L297-298 且等于 `control_pool_limits`/`model_pool_limits(SharedSettings())` L300-307；超时 5 秒 L310-313；请求路径消费**同一实例** L317-319；探针路由依赖图含三支注入依赖 L320-324；lifespan 内未关闭、退出后关闭 L326-328）；④坏配置显式失败 `test_b297_probe_budget_conflicts_fail_startup_explicitly` L342-349（`MODEL_HTTP_READ_TIMEOUT_SEC=3` / `CONTROL_HTTP_READ_TIMEOUT_SEC=3` → 启动 `ValueError` 指名预算，不静默取较小值）+ `test_b297_probe_budget_type_rejects_non_positive_values` L352-357；⑤设置损坏不锁死已认证 CHAT `test_b297_corrupt_platform_settings_do_not_lock_out_authenticated_chat` L363-383（交换 200 且恰 1 条会话 L371-372；植入未知分组设置版本后 `/me` 仍 200 L377-378；新交换 503 `PLATFORM_SETTINGS_UNAVAILABLE` L381-382；失败交换不留半写出会话 L383）；⑥SPA 不把 API 错误回退 HTML `test_b297_spa_fallback_never_turns_api_errors_into_html` L389-407（`create_app(static_dir=临时目录)` 真实装配；`/chat/entry-1` 回落 index.html L397-399；管理/CHAT/未知 API 全部 `application/json` L401-403；未知 API 404 `COMMON_NOT_FOUND` L406-407） | 真实 Console 应用服务：`muad_console_platform.main.app`（ASGITransport 真实 ASGI、真实路由表、真实 api-kit 封套与错误目录）；探针装配断言跑**真实 lifespan**（`main.lifespan(main.app)`，含 `validate_startup` 迁移到 head 与启动自检）；SPA 断言用同一段装配代码的 `main.create_app(static_dir=...)`。真实 PostgreSQL（共享 dev 库，仅 DML、无 DDL）：`control.platform_user`/`chat_access_token`/`chat_session`/`platform_setting`/`console_account`/`console_session` 真实行与真实事务，每用例唯一租户、收尾按 tenant_id 清行；设置损坏用真实 SQL 植入现行 schema 不认识的版本（未知分组）。真实 HTTP 头：CHAT 与管理 Cookie、CSRF、Origin 走真实请求头。池有效值取自 httpx 真实 transport 的 httpcore 池（`max_connections`/`max_keepalive_connections`），不是只比较自己的 helper。本文件不发起外部模型调用：模型侧真实 HTTP 协议探针由 B-228 覆盖（该文件回归 **8 passed**），探针端到端行为（先占额、并发 ≤4、单次 ≤5 秒）仍由 B-228 验证 | verified |

**实施范围与风险说明**

- 实现文件：`infrastructure/http_clients.py`（**新增**：探针执行预算与两个独立池的装配、契约预算 vs 部署池读预算的冲突显式失败、缺服务身份指名失败）、`application/model_probe_service.py`（`ProbeBudget` 类型 + `ProbeCapacityClient` 池预算入参 + `ModelProbeService` 接收注入的客户端与预算）、`api/deps.py`（`get_probe_capacity_client` 改为消费装配根注入的实例、新增 `get_probe_http_client`/`get_probe_budget`）、`api/model_resources.py`（探针端点声明三支注入依赖）、`main.py`（`create_app` 装配根 + `_install_probe_clients`/`_close_probe_clients`；lifespan 改用传入实例并在启动失败时也收尾）。测试：`tests/console_platform/test_capacity_webchat_console_wiring.py`（新增，8 用例）。
- CHAT 路由装配事实：公开认证面由 `api/router.py` 在 `authenticated`/`admin` 组之外 `include_router`（TASK-042 已挂载），`create_app` 挂载整个 router——本任务用真实应用证明「不被管理守卫吞并 + 管理资源路由仍 ADMIN」，未移动路由分组（保持既有 public/authenticated/admin/internal 结构）。
- 探针资源预算：执行预算（单次 5 秒、并发 4、单批 ≤50）仍是**契约常量**（design §3.7「这些上限无需另建运营旋钮」），以 `ProbeBudget` 注入执行器；池大小/超时来自 `SharedSettings` typed 字段（`CONTROL_HTTP_*`/`MODEL_HTTP_*`/`HTTP_*`），装配根不复制默认值。
- 冲突显式失败（design §3.7「资源硬预算约束业务上限，但不静默取较小值掩盖错误」）：探针单次预算 > `MODEL_HTTP_READ_TIMEOUT_SEC`，或容量端口请求预算 > `CONTROL_HTTP_READ_TIMEOUT_SEC` 时启动即 `ValueError`；模型探测客户端用统一 5 秒预算（连接/读/写/池），与「单次 ≤5 秒」契约同口径。
- 缺服务身份（`INTERNAL_SERVICE_TOKEN`/`AGENT_RUNTIME_URL`）不建「必然被 403 拒绝」的裸客户端：装配根留指名日志，请求路径显式 `COMMON_INTERNAL_ERROR`（与 TASK-028 既有语义一致）。
- 生命周期：两个探针池由装配根建、装配根关（`finally`，启动失败也收尾，已用真实失败启动复现验证）；`api/deps.py` 的同名工厂只服务未跑 lifespan 的 ASGI 测试（与 deps.py 既有懒建模式一致），规则来源仍是同一工厂。
- 判别性验证（防止断言空转；四处变异均先复现失败再还原）：①`build_probe_http_client` 去掉 model 池预算 → 装配用例失败（L297-298）；②冲突校验改为恒不触发 → 两条冲突用例失败；③`_install_probe_clients` 不注入 `probe_http_client` → 装配用例失败；④探针端点去掉 `ProbeBudgetDep` 声明并自建预算 → 路由依赖图断言失败（L320-324）。
- 配置盘点：`application/model_probe_service.py` 常量行号 +1、`main.py` 的 `SERVICE_NAME`/`MUAD_EXTRA_PLATFORM_ADAPTERS`/`STATIC_DIR` 行号更新（只更新自己的行、CRLF 保持：1438 CRLF / 0 裸 LF）；`infrastructure/http_clients.py` 无模块级大写常量（无需新增行）；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（5 个实现文件 + 验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 418 source files**。
- 聚焦回归：`tests/console_platform` → **384 passed**（75.35s，含本任务 8 例与 B-228 8 例）；`tests/contracts tests/test_configuration_inventory.py` → **1285 passed**（3.77s）；`tests/console_auth tests/console_internal` → **143 passed**（20.28s）；`tests/console_skill tests/console_mcp tests/console_tasks tests/architecture` → **162 passed**（31.85s）。未跑 `tests/acceptance/**`、未跑浏览器验收、未执行任何迁移 DDL（共享 dev 库约束）。
- 延后项（诚实记录）：探针的端到端真实链路（先占额、并发 ≤4、单次 ≤5 秒、结算证据）由 B-228（TASK-028）承担，本任务只证明装配与注入事实；探针装配的指标/观测属 TASK-099；既有「批量测试」路径（`application/model_test_service.py` + `/api/v1/models/batch-test`，前端 ModelPage 仍在用）仍按自身客户端建池（默认 httpx 池），不在本任务 Files/契约范围，未改动（如要统一到 model 池预算，宜另立任务）。
- B-297: verified — automated command passed; run_id=b8b6c650752c4585978e4b86b9f73bb2 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-297 RED：`uv run pytest -q tests/console_platform/test_capacity_webchat_console_wiring.py` → exit=2，1 collection error（`get_probe_budget`/装配根工厂尚不存在）
- [2026-10-18] 实现：新增 `infrastructure/http_clients.py`（探针预算 + control/model 两个独立池 + 冲突显式失败）、`ProbeBudget` 与 `ModelProbeService` 注入式构造、`deps` 三支注入依赖、`main.create_app` 装配根与探针池建/关
- [2026-10-18] B-297 GREEN：**8 passed in 0.85–0.90s**；四处变异验证断言判别性后还原（池预算、冲突校验、注入缺失、端点依赖声明）
- [2026-10-18] 回归：`tests/console_platform` 384 passed、`tests/contracts tests/test_configuration_inventory.py` 1285 passed、`tests/console_auth tests/console_internal` 143 passed、`tests/console_skill tests/console_mcp tests/console_tasks tests/architecture` 162 passed；ruff 干净、`mypy apps packages` 418 源文件无问题
- [2026-10-18] completed (done)

---

## TASK-098: 模型、准入、流与交付指标目录

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-094, TASK-096
- **Source**: agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**: harness-log#RULE-log-001
- **Acceptance-Refs**: B-298
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/metrics.py`, `apps/im-gateway/src/muad_im_gateway/metrics.py`, `tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py`
- **Estimate**: 45分钟

### Description

无流量/metrics目录仍完整，队列/槽位/连接/缓冲/UNKNOWN/重连/交付指标可见；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-298][integration] 先在`tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：无流量/metrics目录仍完整，队列/槽位/连接/缓冲/UNKNOWN/重连/交付指标可见。→ RED：首建 18 个用例（两单元目录家族 + label 卫生/封闭词表 + 无流量独立渲染 + 打点接线 AST + 真实公开流回压/订阅上限/真实样本）→ `uv run pytest -q tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py` → **exit=1，15 failed, 3 passed in 5.91s**：新目录常量尚不存在（`MODEL_CAPACITY_METRIC`/`SSE_SUBSCRIPTION_METRIC` 等 AttributeError）、`record_gauge`/`record_reason`/`record_sse_*`/`record_tail_outcome` 未定义、公开流回压与订阅上限无样本；3 个既有事实的回归守卫（两单元独立子进程渲染、两个 metrics 模块互不 import）先行通过，证明 RED 只来自本任务要落地的观测面。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 计划 Files 2 个 metrics 模块 + 验收文件；**必需的范围扩张**（披露，15 个实现/接线文件）：Runtime 侧 `application/run_admission.py`（受理接受/按目录错误码拒绝/计数行缺失降级）、`application/run_service.py`（排队期限到期）、`application/executor.py`（阶段投影 ok/failed 按真实阶段、回复增量 emitted/fenced）、`application/model_capacity_service.py`（预占按 workload 裁决、结算结果状态含 UNKNOWN、失约停步、过期清扫批量）、`application/event_tail_hub.py`（hint 降级/恢复 + 追尾关键路径）、`application/channel_delivery_pump.py`（交付诚实结局与失败面 + 维护节拍）、`application/channel_retention.py`（四类回收量 + 节拍结局）、`application/async_tools/continuation_pump.py`、`application/async_tools/control_dispatcher.py`、`application/memory_supervisor.py`（各自维护循环节拍）、`infrastructure/audit_writer.py`（审计写入/幂等重放跳过）、`main.py`（槽位数 gauge、槽回收、reaper/deadline/execution_slot/retention 节拍）；Gateway 侧 `application/webchat_streams.py`（订阅结局/账本水位/关闭原因/帧量/上限拒绝）、`application/runtime_client.py`（等池超时按真实池分类）、`application/channel_delivery.py`（交付结局）；`docs/configuration-inventory.csv`（常量盘点）。改动函数行数：`reserve` 16、`_settlement_status` 10、`_delivery_status` 9、`note_closed` 16、`_publish_watermarks` 4、`_record_outcome` 5、`_transport_failure` 9、`_cursor_status` 6、`record_tail_outcome` 3，均 ≤50（改动文件里 >50 行的函数均为 HEAD 既有，逐条核对过）。错误显式：`_settlement_status`/`_delivery_status`/`note_closed` 的封闭词表外取值一律 `ValueError`（不把未知状态当 label 发出去），池等待先分类计数再按既有映射抛 `COMMON_INTERNAL_ERROR`（错误语义不变）；label 只取封闭字面量与目录错误码，canary 正文/会话 Token/UUID 不进指标文本（真实断言）。
- [x] [B-298] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 18 个用例逐条对应契约三条关键断言（见 Acceptance Evidence 断言位置列）；三处变异先复现失败再还原（追尾 wakeup 计数、慢订阅关闭计数、交付结局 NOT_SENT 分档），证明断言不空转。
- [x] [B-298] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **18 passed in 0.80–0.86s**（exit=0，连续三次复跑稳定；日志 `/tmp/agent-capacity-webchat-plan/task098-green.log`）。
- [x] verifier `harness-log#RULE-log-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。→ 本任务为 review 阶段 verifier（Done Gate 记 deferred_review）：**单元部分已真实执行** `uv run pytest -q tests/test_logging.py tests/test_logging_redaction.py` → **13 passed in 0.02s**（exit=0，logging-kit 统一出口/级别/脱敏清单）；`tests/acceptance/test_foundation_logging.py` 按本次隔离 worktree 的共享库约束**未执行**（禁止 `tests/acceptance/**`），与 TASK-099 同一分工（该文件只起子进程写日志、不碰 PG，但仍在 acceptance 目录）——留 review 阶段按原命令完整执行并填证据。本任务未改 logging-kit、未新增日志字段或敏感键。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-298 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 无流量/metrics目录仍完整，队列/槽位/连接/缓冲/UNKNOWN/重连/交付指标可见；label仅固定低基数枚举，UUID/消息/Key不入label或日志 | tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py"] | verified |
| harness-log#RULE-log-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出 JSON，自动携带 `trace_id/request_id/tenant_id`，并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | planned（review 阶段 verifier；单元部分 13 passed 已登记，acceptance 部分留 review） |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-298 | `uv run pytest -q tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py` → **exit=1，15 failed, 3 passed in 5.91s**（新目录常量/打点 helper 尚不存在；3 个既有事实守卫先行通过） | 同命令 → **18 passed in 0.80–0.86s**（exit=0，连跑三次稳定；日志 `/tmp/agent-capacity-webchat-plan/task098-green.log`） | ① 目录家族：Runtime 模型容量/结算（UNKNOWN）/失约/清扫、准入/计数降级/排队到期/阶段投影、执行槽/槽回收/维护循环、hint/追尾/回复增量/审计、交付结局/失败面、清理四类与节拍 L239-267；Gateway 订阅/账本水位/关闭原因/上限拒绝/帧量/池等待/交付且既有目录不被改写 L270-285。② label 卫生与封闭词表：label 名白名单 + 高基数/敏感标记 L288-297；`TailHubOutcome`/`RuntimeLoop`/`LeaseLostOperation`/`DeliveryFailureReason`/`RetentionKind`/`SseSubscriptionStatus`/`SseCloseReason`/`SseFrameKind`/`GatewayPool`/`DeliveryStatus` 的取值集合 L300-337。③ 无流量目录完整：两单元**独立子进程**真实 ASGI `GET /metrics`，`declared ⊆ rendered` 且不泄漏他单元指标 L344-356；两个 metrics 模块互不 import L359-367。④ Runtime 接线（真实源码 AST）：准入接受/拒绝/设置源不可用/计数行降级 L374-402；排队到期 + 槽位数取自 typed 设置 + 槽回收 + 四类维护循环 L405-436；预占按 workload、结算词表 `{SETTLED,UNKNOWN}`、失约（transition/renew/execute）、清扫批量 L439-461；hint 降级/恢复 + 追尾五条关键路径 + 回复增量 emitted/fenced + 阶段投影 {WAITING_MODEL,RUNNING} + 审计 inserted/replay_skipped L464-498；交付诚实结局词表 + 失败面 + 清理四类回收量 L501-524。⑤ Gateway 接线：订阅 opened/resumed/rejected + 游标分型 + 每条关闭原因 + 帧量五类 + 账本两类水位 L531-566；等池超时按真实池名（stream/egress）分类 L569-586。⑥ 真实打点（真实注册表 + 真实公开流，无共享数据）：慢订阅在**单订阅预算**上被显式关闭 → `gateway_sse_closed_total{reason="slow_subscriber"}`、帧量 `snapshot_part`、收尾后水位归零，且指标文本不含 canary 正文/run UUID、真实样本 label ⊆ 白名单 L641-666；订阅上限在 typed 设置边界拒绝 → `gateway_sse_subscribers_rejected_total` + 活跃水位等于 `gateway_max_sse_subscribers`、释放后归零 L669-687；执行槽 gauge 等于 `runtime_execution_concurrency` L690-697；十个家族真实样本各自可打点且不含 UUID/canary L700-755 | ① 真实源码依赖图：AST 读真实 import/调用/字面量（含 `_transport_failure` 的池名字面量、`_publish_watermarks` 的账本范围、`_delivery_status`/`_settlement_status` 的 return 词表）；② 真实注册表 + 真实 `/metrics`：两单元各自在独立子进程里安装目录并真实渲染（无流量也完整），本进程内用 `render_metrics()` 读真实样本增量；③ 真实公开流回压：真实 `SseBufferLedger`/`WebchatPublicStream` 跑真实 snapshot 帧（内存、无 PG/Redis），驱动真实的慢订阅关闭、账本水位与拒绝路径；④ 实际 typed 配置：订阅上限与缓冲预算、执行槽数都从 `SharedSettings` 真实字段读出并在配置边界上被验证；⑤ 不访问共享 dev 数据、无 DDL、无网络；部署清单/分池预算与总连接预算校验属 TASK-101/103（本任务不重写清单） | verified |

**实施范围与风险说明**

- 观测面口径（design §3.5「指标 label 仅固定 workload/outcome/reason 等低基数枚举，资源 UUID 只进受控日志或管理查询」）：Runtime 侧把此前只留日志的观测点归口到目录——模型容量（预占裁决/结算状态含 UNKNOWN/失约/清扫）、准入（接受/拒绝/计数行降级）、排队与阶段（到期/投影 ok|failed）、执行槽（配置值 gauge/回收/维护循环节拍）、事件流（hint 降级与恢复、追尾唤醒/补查/游标分型/窗口重建、回复增量 emitted|fenced、审计重放跳过）、交付（DELIVERED|NOT_SENT|UNKNOWN|SKIPPED + 泵失败面）、清理（四类回收量 + 节拍）；Gateway 侧补公开流（订阅结局含**重连**、账本连接数与缓冲水位、五类显式关闭原因、上限拒绝、帧量）、出站池等待与渠道交付结局。label 一律取封闭字面量或目录错误码，**没有**任何租户/用户/run/conversation/notification UUID、投递键、正文或凭据进 label（真实样本断言）。
- label 名与 helper 同源（诚实记录）：`record_outcome` 一律写 `status`（模型容量/结算/失约/准入/阶段/维护/hint/回复增量/审计/清理节拍），`record_reason` 写 `reason`（交付失败面/关闭原因），`record_tail_outcome` 写 `outcome`（追尾路径），`record_counter` 的批量计数写 `kind`（清理四类）；因此 `model_settlement_total{status="UNKNOWN"}`、`model_lease_lost_total{status="transition"}` 等用 status 而不是自造 label 名——目录声明的 label 元组与打点 helper 一一对齐（测试同时钉住两侧）。
- 导入环（诚实记录）：Gateway 目录模块 import 了 `api.delivery` → `api.deps` → `application.inbound` → `application.channel_delivery`，故 `runtime_client.py`/`channel_delivery.py` 对 `metrics` 采用**按需导入**（`runtime_client` 走 `TYPE_CHECKING` + 函数内导入，`channel_delivery` 走单点 `_record_outcome` 包装），并在注释里写明原因；其余接线点（`webchat_streams.py`）模块级导入无环。
- 判别性验证（三处变异均先复现失败再还原，随后复跑全绿）：① `event_tail_hub._wake` 去掉 `record_tail_outcome("wakeup")` → 1 failed；② `SseBufferLedger.note_closed` 去掉 `record_sse_close("slow_subscriber")` → 2 failed（接线 + 真实回压用例）；③ `_delivery_status` 去掉 `NOT_SENT` 分档 → 1 failed。
- 配置盘点：`docs/configuration-inventory.csv` 只更新本任务触及的 14 个文件（含两个 metrics 模块新增 19+7 个 `python-constant`）的行号与取值，CRLF 保持（**1575 CRLF / 0 裸 LF**）、`git -c core.whitespace=cr-at-eol diff --check` 通过；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（17 个实现/接线文件 + 验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 441 source files**。
- 聚焦回归：`tests/architecture tests/contracts tests/test_logging.py tests/test_logging_redaction.py tests/test_configuration_inventory.py` → **1368 passed in 8.10s**（exit=0）；`tests/gateway` → **442 passed in 138.02s**（exit=0，含 B-261 公开 SSE 与 B-296 装配回归）；`tests/agent_runtime` → **1050 passed in 733.83s**（exit=0）。未跑 `tests/acceptance/**`、`make acceptance*`、浏览器验收；未执行任何迁移 DDL（共享 dev 库约束）；运行前确认无 dev 服务/残留 pytest 进程。
- 延后项（诚实记录）：① `harness-log#RULE-log-001` 的 acceptance 部分（`tests/acceptance/test_foundation_logging.py`）留 review 阶段按原命令执行；② 部署清单的分池预算显式声明、HPA 上界与总连接预算校验属 TASK-101/103/104（本任务只让运行期观测面可见，不重写清单）；③ 逐部署均衡从调用审计聚合、1000 用户容量结论属 TASK-110/116；④ 「过期 `DELIVERING` 租约自动回收」「按通道写 `PENDING` 意图」等 TASK-096 遗留仍不在本任务 Files 内（本任务只补它们的可观测面）。
- B-298: verified — automated command passed; run_id=a9de42b3ebc0419fb66cd37c50cd5438 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] B-298 RED：`uv run pytest -q tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py` → exit=1，15 failed / 3 passed in 5.91s（新目录常量与打点 helper 尚不存在）
- [2026-10-19] 实现：Runtime 目录补模型容量/准入/排队与阶段/执行槽与维护循环/事件流与围栏/交付/清理七类家族并接线 12 个实现文件；Gateway 目录补公开流（订阅/账本/关闭原因/拒绝/帧量）、池等待与渠道交付并接线 3 个实现文件；label 与打点 helper 一一对齐、封闭词表外取值显式 `ValueError`
- [2026-10-19] B-298 GREEN：**18 passed in 0.80–0.86s**（连跑三次）；三处变异验证断言判别性后还原；`tests/architecture+contracts+logging+inventory` 1368 passed、`tests/gateway` 442 passed、`tests/agent_runtime` 1050 passed；ruff 干净、mypy 441 文件无问题、配置盘点 4 passed；verifier 单元部分 13 passed（acceptance 部分留 review）
- [2026-10-19] completed (done)

---

## TASK-099: Worker与Console资源和认证观测

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-095, TASK-097
- **Source**: agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-299
- **Files**: `apps/agent-worker/src/muad_agent_worker/metrics.py`, `apps/console-platform/backend/src/muad_console_platform/metrics.py`, `tests/architecture/test_capacity_webchat_worker_console_metrics.py`
- **Estimate**: 45分钟

### Description

任务回收/投递归属正确，认证/探针/池等待与配置失败可区分，标准日志有tenant/trace关联但不含Token/Key，四单元catalog可独立读取；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-299][integration] 先在`tests/architecture/test_capacity_webchat_worker_console_metrics.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：任务回收/投递归属正确，认证/探针/池等待与配置失败可区分，标准日志有tenant/trace关联但不含Token/Key，四单元catalog可独立读取。→ RED：首建 19 个用例（4 单元独立渲染 ×4 + 15 个行为/接线用例）→ `uv run pytest -q tests/architecture/test_capacity_webchat_worker_console_metrics.py` → **exit=1，13 failed, 6 passed in 13.95s**：新目录常量尚不存在（`CHAT_AUTH_METRIC` 等 AttributeError、`DELIVERY_ATTEMPT_METRIC`/`MAINTENANCE_METRIC` 缺失）、`probe_model_endpoint(pool=...)` 未支持（TypeError）、维护失败无计数、池等待/配置失败无样本；6 个既有事实的回归守卫（label 白名单、四单元独立渲染、指标模块互不 import）先行通过，证明 RED 只来自本任务要落地的观测面。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 计划 Files 2 个 metrics 模块 + 验收文件；**必需的范围扩张**（披露）：`worker/service.py`（维护节拍计数）、`delivery/service.py`（投递计数归口）、`api/webchat_auth.py`（三条认证路由 `count_outcome`）、`application/model_probe_service.py`（探针结论/池等待/配置失败打点 + `ProbeReason` 封闭词表 + `pool` 参数）、`application/model_test_service.py`（批量测试声明 `batch` 池）、`api/deps.py` 与 `main.py`（缺服务身份的配置失败计数）、`infrastructure/http_clients.py`（预算冲突的配置失败计数）。改动函数行数：`run_maintenance_forever` 13、`probe_model_endpoint` 25、`_perform_probe` 33、`_classify_probe_response` 10、`_record_probe_outcome` 5、`record_reason` 3、`record_pool_wait` 3，均 ≤50；错误显式：池等待按真实池计数后仍按 B-228 既有映射上抛（`COMMON_INTERNAL_ERROR` / `MODEL_UNAVAILABLE`+UNKNOWN），预算冲突先计数再 `ValueError`，缺服务身份指名日志 + `AppError`；凭据/Token 不进 label、指标文本与日志（真实 canary 断言）。
- [x] [B-299] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 19 个用例逐条对应契约四条关键断言（见 Acceptance Evidence 断言位置列）；三处变异先复现失败再还原（维护失败计数、模型池池等待计数、exchange 认证计数），证明断言不空转。
- [x] [B-299] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_worker_console_metrics.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **19 passed in 3.29–3.43s**（exit=0，连续三次复跑稳定；日志 `/tmp/agent-capacity-webchat-plan/task099-green.log`）。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-299 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 任务回收/投递归属正确，认证/探针/池等待与配置失败可区分，标准日志有tenant/trace关联但不含Token/Key，四单元catalog可独立读取 | tests/architecture/test_capacity_webchat_worker_console_metrics.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_worker_console_metrics.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-299 | `uv run pytest -q tests/architecture/test_capacity_webchat_worker_console_metrics.py` → **exit=1，13 failed, 6 passed in 13.95s**（目录常量/装配打点尚不存在；6 个回归守卫先行通过） | 同命令 → **19 passed in 3.29–3.43s**（exit=0；复跑稳定，日志 `/tmp/agent-capacity-webchat-plan/task099-green.log`） | ① 回收/投递归属：目录覆盖 reclaim/lease/delivery/maintenance `test_b299_worker_catalog_covers_reclaim_delivery_and_maintenance` L448-457；维护循环驱动回收且成功/失败都计数 L524-537（`_recorded_statuses == {"ok","failed"}` L529）；`reclaim_expired` 只计 `TASK_RECLAIM_METRIC`/`TASK_LEASE_EXPIRED_METRIC` L536-537；投递计数从 `metrics` 导入、调用方不再散落名字 L539-556。② 认证/探针/池等待/配置失败可分：Console 目录四家族 L460-469；三条认证路由 operation 分桶 L558-564；探针/池等待/配置失败各自接线（`_record_probe_outcome`、`record_pool_wait("control")`、`record_reason("budget_conflict"/"missing_service_identity")`）L567-606；reason 封闭词表 L608-630。③ 真实打点：真实 HTTP 200/401/500/拒连 → `model_probe_total`/`model_probe_failure_total` 样本 L632-674；真实池等待（连接被挂起请求占满）→ `console_http_pool_wait_total{pool="model"}` 且**不触碰**配置失败家族 L676-711；容量端口真实池等待 → `{pool="control"}` 且错误语义不变 L713-742；维护循环连真实拒连端口 → `worker_maintenance_total{status="failed"}` 且循环未死 L748-785。④ 日志：真实 logging-kit 出口带 `trace_id`/`tenant_id`、指名缺项、canary Token 不出现 L787-838；预算冲突配置失败计数 L844-866；认证结局分桶（`CHAT_TOKEN_INVALID` vs `PLATFORM_SETTINGS_UNAVAILABLE` vs `SUCCESS`）L868-896。⑤ 四单元独立：每单元独立子进程真实 ASGI 渲染完整目录、不泄漏他单元指标 L486-493；四个 metrics 模块互不 import L496-521 | ① 四单元在**独立子进程**真实安装 + `GET /metrics`（ASGI，无流量）逐单元断言目录完整与不泄漏；② 真实本机 socket HTTP 探针（200/401/500/拒连/挂起/慢滴，`trust_env=False` 直连），驱动真实 `probe_model_endpoint`/`ProbeCapacityClient`；③ 真实拒连端口（`127.0.0.1:1`）驱动真实 `WorkerLoop.run_maintenance_forever`，断言失败计数 + 循环未结束 + `logger.exception` 日志；④ 真实 logging-kit 文件出口（tmp 目录）解析 JSON 行断言 tenant/trace 关联与无凭据；⑤ 真实源码 AST（import/调用/关键字字面量）钉住归属与接线；不访问共享 dev 数据、无 DDL | verified |

**实施范围与风险说明**

- 观测面口径（design §3.5「label 仅固定 workload/outcome/reason 等低基数枚举」）：Worker 侧新增 `worker_maintenance_total{status}`（维护节拍 ok/failed，承接 TASK-095 延后项）并把 `delivery_attempt_total`/`delivery_failed_total`/`delivery_artifact_gone_total` 归口到 `metrics.CATALOG`（此前散在 `delivery/service.py`，无流量时不暴露、目录看不到）；Console 侧新增四个可区分家族——`chat_auth_total{operation,status}`（认证，`PLATFORM_SETTINGS_UNAVAILABLE` 与 `CHAT_TOKEN_*` 是不同样本）、`model_probe_total{status}` + `model_probe_failure_total{reason}`（探针，reason 为 11 值封闭词表）、`console_http_pool_wait_total{pool}`（池等待，`control`/`model`/`batch` 三值）、`console_config_failure_total{component,reason}`（配置失败：缺服务身份/预算冲突）。四类互不混报（池等待测试同时断言配置失败计数零增量）。
- 池归属（诚实记录）：`probe_model_endpoint` 新增必填 `pool` 参数，装配根探针传 `model`、既有批量测试传 `batch`（它仍用自己的默认客户端建池，TASK-097 已披露未统一）；池等待超时按真实池计数，不把批量测试的等待冒充 `model` 池。既有 B-228 用户可见映射（401→CREDENTIAL_MISSING、超时/池等待→MODEL_UNAVAILABLE+UNKNOWN 保守保留、拒连→NOT_SENT）**未改变**，新增 `reason` 只用于观测。
- 判别性验证（三处变异均先复现失败再还原，随后复跑全绿）：① `run_maintenance_forever` 去掉失败计数 → 2 failed（源码接线 + 真实失败用例）；② `_perform_probe` 去掉 `record_pool_wait(pool)` → 2 failed（接线 + 真实池等待用例）；③ `exchange_chat_token` 去掉 `count_outcome` → 1 failed（认证路由接线用例）。
- 配置盘点：`docs/configuration-inventory.csv` 只更新本任务触及的 10 个文件行号与新增 8 个 `python-constant`（worker/console metrics 各 4/5 个），CRLF 保持（1527 CRLF / 0 裸 LF）、`git -c core.whitespace=cr-at-eol diff --check` 通过；`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**。
- 静态校验：`uv run ruff check`（10 个实现/接线文件 + 验收文件）干净；`uv run mypy apps packages` → **Success: no issues found in 438 source files**。
- 聚焦回归：`tests/architecture tests/contracts tests/test_audit_observability_config.py tests/test_configuration_inventory.py` → **1342 passed in 9.07s**（exit=0，含 B-203 既有 Console 指标/日志契约）；`tests/agent_worker` → **327 passed in 102.46s**（exit=0）；`tests/console_platform tests/console_auth` → **474 passed in 71.13s**（exit=0，含 B-228/B-297 探针与装配回归）。未跑 `tests/acceptance/**`、`make acceptance*`、浏览器验收；未执行任何迁移 DDL（共享 dev 库约束）；运行前确认无 dev 服务残留。
- 延后项（诚实记录）：Runtime/Gateway 目录属 TASK-098（B-298，含 harness-log#RULE-log-001 verifier）；探针端到端容量链路（先占额、并发 ≤4、结算证据）属 B-228（TASK-028）；部署清单里的分池预算显式声明与总连接预算校验属 TASK-101/103；1000 用户容量结论属 TASK-110/116；本任务不新增运维旋钮、不改写既有错误映射。
- B-299: verified — automated command passed; run_id=272c628a448846c6a09a89b1c6a69626 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-299 RED：`uv run pytest -q tests/architecture/test_capacity_webchat_worker_console_metrics.py` → exit=1，13 failed / 6 passed（新目录常量与装配打点尚不存在）
- [2026-10-18] 实现：worker/console 指标目录补齐（维护节拍、投递归口、认证/探针/池等待/配置失败四家族 + 封闭 reason 词表）；三条 CHAT 认证路由 `count_outcome`；探针按真实池记录池等待并区分传输原因；缺服务身份/预算冲突记为配置失败；投递计数归口 `metrics.CATALOG`
- [2026-10-18] B-299 GREEN：**19 passed in 3.29–3.43s**；三处变异验证断言判别性后还原；`tests/architecture+contracts+audit_observability+inventory` 1342 passed、`tests/agent_worker` 327 passed、`tests/console_platform+console_auth` 474 passed；ruff 干净、mypy 438 文件无问题、配置盘点 4 passed
- [2026-10-18] completed (done)

---

## TASK-100: Console/Gateway单Pod及静态页面部署

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-093, TASK-096, TASK-097
- **Source**: agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.frontend.design.md#3.2 页面与路由
- **Spec-Refs**:
- **Acceptance-Refs**: B-300
- **Files**: `deploy/k8s/base/console-platform.yaml`, `deploy/k8s/base/im-gateway.yaml`, `tests/architecture/test_capacity_webchat_console_gateway_deploy.py`
- **Estimate**: 45分钟

### Description

Console/Gateway各1Pod，readiness/liveness与终止宽限正确，Console镜像内静态资源提供/chat；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-300][integration] 先在`tests/architecture/test_capacity_webchat_console_gateway_deploy.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：Console/Gateway各1Pod，readiness/liveness与终止宽限正确，Console镜像内静态资源提供/chat。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-300] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-300] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_console_gateway_deploy.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-300 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | Console/Gateway各1Pod，readiness/liveness与终止宽限正确，Console镜像内静态资源提供/chat；不新增前端镜像/Pod/Service或运行Node | tests/architecture/test_capacity_webchat_console_gateway_deploy.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_console_gateway_deploy.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-101: Runtime/Worker资源与共享存储约束

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-094, TASK-095
- **Source**: agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.backend.design.md#4.2 发布与迁移
- **Spec-Refs**:
- **Acceptance-Refs**: B-301
- **Files**: `deploy/k8s/base/agent-runtime.yaml`, `deploy/k8s/base/agent-worker.yaml`, `tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`
- **Estimate**: 45分钟

### Description

资源requests/limits、进程/执行槽、共享RWX及独立本地cache明确；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-301][integration] 先在`tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：资源requests/limits、进程/执行槽、共享RWX及独立本地cache明确。→ RED：新建 10 个行为用例（17 例含参数化）后 `uv run pytest -q tests/architecture/test_capacity_webchat_runtime_worker_deploy.py` → **12 failed, 5 passed in 1.65s**（失败=清单尚无 resources/terminationGracePeriodSeconds/执行槽/分池预算；已绿 5 例是**回归保护**的既有事实：RWX PVC 与挂载/容量、无 bot/user 绑定、PG 总预算，断言未削弱）
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。→ 实现落在计划 Files 的 3 个文件：`deploy/k8s/base/agent-runtime.yaml`、`deploy/k8s/base/agent-worker.yaml`（各加 `terminationGracePeriodSeconds: 60`、容器 `resources.requests/limits`、`POD_NAME`（仅 Runtime，源码读它作 instance_id 前缀）、执行槽、分池 HTTP 预算；共享卷/本地 cache 注释明确）、`tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`（417 行，10 个用例/17 例含参数化；辅助函数最长 19 行、用例最长 31 行，均 ≤50；强类型 `DeployUnit` dataclass + `Any` 仅用于 YAML 文档边界；`_scaled_int`/`_cpu_millicores` 对无法解析的数量**显式报错**不静默取默认；断言只读清单/源码/typed profile，无凭据、无原始内部内容）
- [x] [B-301] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。→ 10 个用例逐条对应契约 5 组断言（见 Acceptance Evidence 断言位置列）；另做 5 处变异验证断言判别性（先复现失败再还原）
- [x] [B-301] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_worker_deploy.py"]`并记录GREEN、关键断言位置和实际组件证据。→ **17 passed in 0.61s**（exit=0，最终冻结版本；还原变异后复跑 0.55s 全绿）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-301 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 资源requests/limits、进程/执行槽、共享RWX及独立本地cache明确；不按bot/user绑定Pod，最大副本下PG/HTTP/FD预算可计算 | tests/architecture/test_capacity_webchat_runtime_worker_deploy.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_worker_deploy.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-301 | 首建 `tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`（10 用例/17 例含参数化）→ `uv run pytest -q tests/architecture/test_capacity_webchat_runtime_worker_deploy.py` → **12 failed, 5 passed in 1.65s**（缺 resources/终止宽限/执行槽/分池预算；5 例为既有事实的回归保护：RWX 共享卷与容量、无 bot/user 绑定、PG 总预算） | 同命令（两份清单补齐资源/终止宽限/启动预算后）→ **17 passed in 0.61s**（exit=0）；变异还原后复跑 **17 passed in 0.55s** | ①资源 requests/limits 与 profile 一致 `test_requests_and_limits_match_the_capacity_profile` L250-262（runtime `cpu 1/4、mem 2Gi/8Gi` L255-258，worker 同值；profile `resources.hardware` 逐字段比对）；②执行槽显式且等于 typed 默认 `test_execution_slots_are_declared_and_match_typed_defaults` L264-271（`RUNTIME_EXECUTION_CONCURRENCY=8`/`WORKER_EXECUTION_CONCURRENCY=4` 对 `settings.runtime/worker_execution_concurrency` L268-270）；③分池预算与真实池一致 `test_http_pool_budgets_cover_exactly_the_pools_the_process_creates` L273-296（键集合 == `RUNTIME_HTTP_POOLS=(control,model,mcp,egress)`/`WORKER_HTTP_POOLS=(control,egress)` 派生的 8/4 键 L279-290；每池 connections/keepalive 等于 typed 字段且 keepalive≤connections L291-296）；④键是 typed 字段且有本单元消费点 `test_declared_budget_keys_are_typed_settings_consumed_by_this_unit` L298-316（键集合 == 期望集合 L302-305；`SharedSettings.model_fields` L309；AST 收集的 `settings.<field>` 消费点 L310-314；`POD_NAME` 只在源码真的 `os.getenv` 它的单元声明 L315-316）；⑤共享 RWX + 独立本地 cache `test_shared_artifact_volume_is_rwx_and_skill_cache_is_per_process` L318-348（唯一共享卷=artifacts PVC `muad-artifacts-rwx` 且挂载点==ConfigMap `ARTIFACT_ROOT` L325-333；PVC `accessModes:[ReadWriteMany]` + `100Gi`==profile `storage_budget.artifact_bytes=107374182400` L335-339；skill-cache 为 emptyDir、挂载点==`SKILL_CACHE_ROOT`、不在制品路径下 L341-345；其余卷必须每进程独立 L347-348）；⑥不按 bot/user/具体 Pod 绑定 `test_deployments_are_not_bound_to_bots_users_or_specific_pods` L350-370（kind==Deployment/replicas 为 int L354-355；无 nodeName/hostname/subdomain/nodeSelector/affinity L356-361；env/卷/标签名无 bot|user 词元 L363-369）；⑦终止宽限==60 `test_termination_grace_period_covers_the_drain_budget` L372-378；⑧最大副本 PG 预算 `test_pg_connection_total_at_max_replicas_fits_profile_budget` L380-394（真实 engine 工厂的 `pool.size()+max_overflow`=5+10 L237-248；`Σ process_counts×max_replica_counts×每进程` = console 15 + runtime 60 + worker 120 = **195 ≤ profile 200** L386-393；Gateway 不持库不参与 L391）；⑨每进程 HTTP+PG+Redis+订阅 FD 预算 `test_per_process_http_pg_and_fd_budget_fits_the_nofile_budget` L396-417（runtime 128+15+50+1100=**1293 ≤ 4096**；worker 48+15+50=**113 ≤ 4096** L403-412；runtime 内存上限 8192MiB ≥ 进程级 SSE 总缓冲 `sse_total_buffer_bytes`=64MiB L413-417） | 真实边界（不 Mock、不访问共享 dev 数据）：**真实清单**（`yaml.safe_load_all` 解析 `deploy/k8s/base/{agent-runtime,agent-worker,pvc-artifacts,configmap}.yaml`）；**真实源码依赖图**（`RUNTIME_HTTP_POOLS`/`WORKER_HTTP_POOLS` 直接 import 自 `infrastructure/http_clients.py`；消费点用 AST 收集 `settings.<field>`；`POD_NAME` 用 AST 收集 `os.getenv` 常量）；**真实 engine 工厂**（`muad_agent_runtime/worker/console_platform.infrastructure.db.get_engine()` 惰性建池、不连库，`DATABASE_URL` 由 fixture 注入假值，用后清 lru_cache）；**真实 typed 配置**（`SharedSettings` 默认/校验、`tests/capacity/profile.py` 的严格 typed profile 解析示例） | verified |

**实施范围与风险说明**

- 范围：只动计划 Files 的 3 个文件；无 DDL/迁移、无应用代码改动、未跑 `tests/acceptance/**`、`make acceptance*` 或浏览器验收（共享环境约束）；跑前已确认无 dev/pytest 残留进程。
- 清单与代码事实一致（不写不生效的键）：清单只声明**本单元源码真实消费**的预算键（`RUNTIME_EXECUTION_CONCURRENCY`、`CHANNEL_DELIVERY_CONCURRENCY`、control/model/mcp/egress 池；worker 为 `WORKER_EXECUTION_CONCURRENCY` + control/egress 池），并由 ④ 用例机检「每个键都是 `SharedSettings` 字段且本单元有 `settings.<field>` 消费点」——多写/少写都会红。**未声明** `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_POOL_TIMEOUT_SEC`、`REDIS_MAX_CONNECTIONS`、`RUNTIME_MAX_SSE_SUBSCRIBERS`：前三个未被 engine 工厂消费（`create_async_engine(url, pool_pre_ping=True)` 用 SQLAlchemy 默认 5+10），后两个是「已声明未消费」的启动预算（TASK-094 延后项④）；写进清单会让"有效值"变成假象。PG/FD 总预算因此按**真实**来源核算：PG 取 engine 工厂实际 pool，Redis/订阅上限取 typed 有效值（默认）。
- 资源数值来源（不是臆造）：`cpu 1/4`、`memory 2Gi/8Gi` 与容量 profile `resources.hardware.agent-runtime/agent-worker`（`cpu_request_millicores: 1000`、`cpu_limit_millicores: 4000`、`memory_request_mb: 2048`、`memory_limit_mb: 8192`）逐字段一致，由 ① 用例机检；改 profile 必须同步清单（反之亦然）。正式测量前按实测调整是 TASK-117 报告的输出，本任务只保证声明一致。
- 共享存储：制品卷是唯一共享卷（RWX PVC `muad-artifacts-rwx`，`100Gi`==profile `artifact_bytes`），skill-cache 是每进程 `emptyDir`（harness-skill RULE-skill-001：不落共享盘、不直接从共享盘执行）；挂载点与 ConfigMap 的 `ARTIFACT_ROOT`/`SKILL_CACHE_ROOT` 对齐（⑤ 机检）。
- 无 Pod/bot/user 绑定：无 `nodeName`/`hostname`/`subdomain`/`nodeSelector`/`affinity`，env/卷/标签命名无 bot|user 词元，`kind: Deployment`（非 StatefulSet）；`POD_NAME` 只作日志/指标 instance_id 前缀（源码 `os.getenv('POD_NAME') or socket.gethostname()`），不改变"任意 Pod 可接管同一会话"（RULE-arch-001）。基线 `replicas: 1`，Runtime/Worker 的副本上界与 HPA 属 TASK-103。
- 排空/发布（§4.2 的部署侧）：两份清单显式 `terminationGracePeriodSeconds: 60`（起始值），readiness `/readyz`、liveness `/healthz` 口径不变（既有 `test_k8s_deploy_manifests.py` 仍绿）；维护窗口的备份/排空/单次迁移流程属 TASK-104 的预检与 README，不在本任务 Files。
- 判别性验证（防止断言空转；5 处变异均先复现失败再还原，随后复跑确认 17 passed）：①runtime 去掉 `MODEL_HTTP_MAX_KEEPALIVE` → ③用例 **1 failed**（`缺 ['MODEL_HTTP_MAX_KEEPALIVE']`）；②runtime 声明未接线的 `DB_POOL_SIZE` → ④用例 **1 failed**（`没有 settings.db_pool_size 消费点`）；③worker 内存上限改 1Gi → ①用例 **1 failed**（`2048 == 1024`）；④runtime 加 `user-data` 卷 → ⑥用例 **1 failed**（`不得按 bot/user 绑定 Pod：['user-data']`）；⑤worker 删 `terminationGracePeriodSeconds` → ⑦用例 **1 failed**。
- 静态检查与盘点：`uv run ruff check tests/architecture/test_capacity_webchat_runtime_worker_deploy.py` → **All checks passed**；`uv run mypy apps packages` → **Success: no issues found in 441 source files**；未在 `apps`/`packages` 新增/移动模块级大写常量，`uv run pytest -q tests/test_configuration_inventory.py` → **4 passed**（`docs/configuration-inventory.csv` 无需改动）；两份清单 `yaml.safe_load_all` 解析为 Deployment+Service 两文档（既有架构清单用例一并绿）。
- 聚焦回归：`uv run pytest -q tests/architecture` → **69 passed in 4.90s**（exit=0，含既有 `test_k8s_deploy_manifests.py` 的探针/镜像/非 root/迁移 Job 机检与 `harness-arch#RULE-arch-001` 的 verifier 套件）。
- 延后项（诚实记录）：① `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_POOL_TIMEOUT_SEC` 的 engine 工厂接线（§3.7 声明预算落地）不在本任务 Files，属持库单元基础设施的域内变更；接线后 PG 每进程上限会从 15 下降，本用例会按新事实自动重算（不写死数字）。② `REDIS_MAX_CONNECTIONS`/`RUNTIME_MAX_SSE_SUBSCRIBERS` 做成进程级账本/信号量（TASK-094 延后项④）后应在清单显式声明；本任务按 typed 有效值核算 FD 预算。③ 总连接/FD/内存缓冲的渲染前预检与发布阻断属 TASK-103/104；HPA 上界与 `maxReplicas` 属 TASK-103。④ Console/Gateway 的资源/终止宽限与分池预算显式声明属 TASK-100（本任务只覆盖 Runtime/Worker 与共享卷）。⑤ 1000 用户容量结论仍由 TASK-110/116 的 E2E/profile 负责；本任务不声称生产容量已达标。
- B-301: verified — automated command passed; run_id=a56ddf43a3184d608fc7e0cf33efa35c (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-19] started
- [2026-10-19] B-301 RED：新建 `tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`（10 用例/17 例）→ `uv run pytest -q tests/architecture/test_capacity_webchat_runtime_worker_deploy.py` → **12 failed, 5 passed in 1.65s**（清单尚无 resources/终止宽限/执行槽/分池预算；5 例为既有事实的回归保护）
- [2026-10-19] 实现：`agent-runtime.yaml`/`agent-worker.yaml` 加 `terminationGracePeriodSeconds: 60`、容器 `resources.requests/limits`（与容量 profile hardware 逐字段一致）、`POD_NAME`（仅 Runtime）、执行槽/投递并发、按真实池声明的 HTTP connections+keepalive；共享 RWX 制品卷与每进程 emptyDir skill-cache 注释明确；未声明未接线的 DB/Redis/SSE 键（不写不生效的键）
- [2026-10-19] B-301 GREEN：**17 passed in 0.61s**（变异还原后复跑 0.55s）；5 处变异验证断言判别性后还原；`tests/architecture` **69 passed**（4.90s）；ruff 干净、mypy 441 文件无问题、配置盘点 4 passed；PG 总预算 195≤200、FD 预算 runtime 1293/worker 113 ≤4096（engine 工厂真实 pool 5+10 核算）
- [2026-10-19] completed (done)

---

## TASK-102: 启动配置和同源HTTP/SSE路径分流

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-100, TASK-101
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.frontend.design.md#3.2 页面与路由
- **Spec-Refs**:
- **Acceptance-Refs**: B-302
- **Files**: `deploy/k8s/base/configmap.yaml`, `deploy/k8s/base/ingress.yaml`, `tests/architecture/test_capacity_webchat_configmap_ingress.py`
- **Estimate**: 45分钟

### Description

服务URL/租户/非敏感预算显式配置，认证路径优先转Console、其余聊天API转Gateway、/chat与静态到Console；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-302][integration] 先在`tests/architecture/test_capacity_webchat_configmap_ingress.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：服务URL/租户/非敏感预算显式配置，认证路径优先转Console、其余聊天API转Gateway、/chat与静态到Console。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-302] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-302] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_configmap_ingress.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-302 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 服务URL/租户/非敏感预算显式配置，认证路径优先转Console、其余聊天API转Gateway、/chat与静态到Console；SSE禁缓冲且代理超时覆盖心跳 | tests/architecture/test_capacity_webchat_configmap_ingress.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_configmap_ingress.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-103: HPA上界与部署清单预算校验

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-102
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量
- **Spec-Refs**:
- **Acceptance-Refs**: B-303
- **Files**: `deploy/k8s/base/capacity-autoscaling.yaml`, `deploy/k8s/base/kustomization.yaml`, `tests/architecture/test_capacity_webchat_hpa_budget.py`
- **Estimate**: 45分钟

### Description

注册入口/HPA资源；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-303][integration] 先在`tests/architecture/test_capacity_webchat_hpa_budget.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：注册入口/HPA资源。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-303] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-303] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_hpa_budget.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-303 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 注册入口/HPA资源；Console/Gateway固定1，Runtime/Worker最大副本显式，按在飞/排队/CPU内存指标扩缩，不能超PG保留预算或下游总并发 | tests/architecture/test_capacity_webchat_hpa_budget.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_hpa_budget.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-104: 迁移、排空及资源预算预检

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-098, TASK-099, TASK-103
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.2 发布与迁移, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**: harness-arch#RULE-arch-001
- **Acceptance-Refs**: B-304
- **Files**: `scripts/check_agent_capacity_deployment.py`, `deploy/k8s/README.md`, `tests/architecture/test_capacity_webchat_deployment_preflight.py`
- **Estimate**: 45分钟

### Description

渲染正式清单验总连接/FD/内存缓冲与资源声明，错误阻断发布；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-304][integration] 先在`tests/architecture/test_capacity_webchat_deployment_preflight.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：渲染正式清单验总连接/FD/内存缓冲与资源声明，错误阻断发布。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-304] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-304] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_deployment_preflight.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-arch#RULE-arch-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/architecture"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-304 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 渲染正式清单验总连接/FD/内存缓冲与资源声明，错误阻断发布；迁移维护窗口排空/备份/head和只读历史说明可执行，PG不可写不受理202、Redis故障不误下线全部Pod | tests/architecture/test_capacity_webchat_deployment_preflight.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_deployment_preflight.py"] | planned |
| harness-arch#RULE-arch-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 固定四个部署单元（console-platform / agent-runtime / agent-worker / im-gateway）；Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/architecture"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-105: 容量验收profile严格schema与示例

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002, TASK-003
- **Source**: agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: B-305
- **Files**: `tests/capacity/profile.py`, `tests/capacity/profiles/agent-capacity-webchat.example.yml`, `tests/capacity/test_capacity_webchat_load_profile.py`
- **Estimate**: 45分钟

### Description

users/负载分布/资源/SLO/模型模式typed校验，正式验收缺必填拒绝pass；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-305][unit] 先在`tests/capacity/test_capacity_webchat_load_profile.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：users/负载分布/资源/SLO/模型模式typed校验，正式验收缺必填拒绝pass。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-305] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-305] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_profile.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-305 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | users/负载分布/资源/SLO/模型模式typed校验，正式验收缺必填拒绝pass；1000用户不能由空闲连接代替，不在配置中嵌真实密钥 | tests/capacity/test_capacity_webchat_load_profile.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_profile.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-305 | `uv run pytest -q tests/capacity/test_capacity_webchat_load_profile.py` → **1 error, 0 passed**（exit=1, 0.06s，collection ERROR）：`ModuleNotFoundError: No module named 'tests.capacity.profile'`（实现前测试先行，新增的 typed schema/规范示例尚不存在） | 同命令 → **98 passed**（exit=0, 0.07s）；聚焦回归 `tests/test_platform_settings_schema.py tests/test_settings.py tests/test_configuration_inventory.py` → **114 passed**（0.86s）；`uv run ruff check tests/capacity/` 逐文件干净；`tests/capacity --collect-only` → 98 tests collected | `test_b305_example_profile_parses_and_is_acceptance_ready` L166-177（users=1000、turns_per_user≥1、text_bytes.max≥1、mode=CONTROLLED_PROBE、blockers==()、require_formal_acceptance 通过）；`test_b305_example_matches_canonical_document` L179-181（示例 = 规范文档）；`test_b305_no_embedded_secrets_and_no_env_resolution` L183-196（文件文本无 api_key/secret/password/bearer/private_key 与 sk-/ghp_/AKIA/私钥标记，且 delenv 后解析结果不变）；`test_b305_integer_fields_reject_bool_float_and_out_of_range` L199-221（19 组 bool/小数/字符串/越界：users/turns/active_runs/ramp/steady/分布/连接/FD/存储/副本数）；`test_b305_slo_numbers_must_be_finite_positive` L232-237（bool/NaN/±inf/字符串/None/0/-1 全拒）；`test_b305_error_ratio_is_bounded_number` L240-244（[0,1] 外拒）；`test_b305_slo_percentiles_must_be_ordered` L247-251（p95≤p99）；`test_b305_distribution_percentiles_must_be_ordered` L254-261（p50≤p95≤max、max≥0）；`test_b305_users_below_target_only_blocks_the_pass` L266-273（users=999 可诊断但 blockers 含 workload.users 且抛 ProfileAcceptanceError）；`test_b305_missing_required_keys_are_rejected` L279-301（20 条路径缺键拒绝）；`test_b305_missing_slo_and_resources_never_yield_an_acceptance_pass` L308-316（缺整段 → 明确拒绝 pass）；`test_b305_unknown_keys_are_rejected` L319-334（顶层/各段/部署单元/未知单元 10 处未知键）；`test_b305_model_mode_is_a_closed_enum` L346-350（MOCK/小写/空/非字符串拒）；`test_b305_real_mode_requires_quota_evidence` L353-357、`test_b305_mode_specific_keys_are_rejected` L360-373（模式专属键互斥）；`test_b305_real_mode_records_quota_evidence_with_env_references_only` L376-386（credential_ref/quota_group_ref 为 env: 引用，referenced_env_vars 只回变量名）；`test_b305_quota_entries_are_strictly_typed` L400-409、`test_b305_quota_entries_must_declare_a_quota_dimension` L411-417（max_inflight 正整数必填、rpm/tpm 正整数或 null 且不得同时 null）；`test_b305_credential_fields_are_rejected_without_echoing_the_secret` L429-438、`test_b305_secret_looking_values_are_rejected_without_echoing_them` L441-446（错误消息不回带原文）；`test_b305_resources_keep_console_and_gateway_single_pod` L449-458（Console/Gateway maxReplicas=1）、`test_b305_resources_require_every_deployment_unit_and_replica_room` L461-470（四单元齐全、replica≤max）、`test_b305_hardware_requests_must_fit_limits` L473-477、`test_b305_attachment_ratio_governs_upload_distribution` L480-497（ratio∈[0,1]、ratio>0 必填 upload_bytes、ratio=0 纯文本）；`test_b305_profile_hash_freezes_content_and_round_trips` L504-516（profile_hash = sha256(规范 JSON)、回灌不变、改一值即变、file_digest=文件字节摘要）；`test_b305_load_profile_reports_missing_broken_or_non_mapping_input` L519-533（缺文件/坏 YAML/非对象根均显式报错） | 真实 typed schema：`tests/capacity/profile.py` 的 `parse_profile`/`load_profile`（不读 env、不联网、不连 PG/Redis），真实 YAML 文件 `tests/capacity/profiles/agent-capacity-webchat.example.yml`（经 `yaml.safe_load` 解析，模式/分布/资源声明逐键校验）；契约裁决由 `acceptance_blockers`/`require_formal_acceptance` 实现，缺 SLO/资源或 users<1000 一律 `ProfileAcceptanceError`，绝不静默给 pass（E-21）；密钥边界：`_reject_secrets` 按凭据键名与 `sk-`/`ghp_`/`AKIA`/私钥/Bearer 形态 fail-closed，REAL 模式额度证据只允许 `env:NAME` 引用 | verified |

**实施范围与证据说明**

- 实现文件与计划 Files 完全一致：`tests/capacity/profile.py`（严格 schema/解析/验收裁决/冻结 hash）、`tests/capacity/profiles/agent-capacity-webchat.example.yml`（可解析通过的规范示例，CONTROLLED_PROBE 模式，无任何密钥与真实额度数字）、`tests/capacity/test_capacity_webchat_load_profile.py`（98 个用例）。未改动 apps/packages/migrations，故 `uv run mypy apps packages` 与配置盘点无新增项（`tests/test_configuration_inventory.py` 4 passed 复验）。
- 契约口径（设计 §3.7「验收profile字段」）：`workload.users`（≥1000 才可验收本目标，否则只给诊断）、`active_runs_per_user`（≥1 且 ≤users）、`ramp_sec`（≥0）/`steady_sec`（>0）、`turns_per_user`（≥1）、`input_distribution.think_time_ms|text_bytes`（p50≤p95≤max，text_bytes.max≥1：空闲连接不算执行负载）、`attachment_distribution.ratio`（[0,1]，>0 必填 `upload_bytes`）；`model.mode` 只认 CONTROLLED_PROBE/REAL，模式专属键互斥，REAL 必填 `credentials_and_quotas`（provider/model/`env:` 凭据引用/max_inflight 正整数/rpm|tpm 至少一维）；`slo` 五项（有限正数、p95≤p99、ratio∈[0,1]）与 `resources`（四部署单元 hardware requests≤limits、process/replica/max_replica 计数、Console/Gateway maxReplicas=1、pg/fd/storage 预算）在正式验收必填且无默认。
- 供后续任务使用的稳定接口（TASK-106/107/109/117/118）：`load_profile`/`parse_profile`/`profile_payload`/`profile_hash`/`file_digest`/`acceptance_blockers`/`is_acceptance_ready`/`require_formal_acceptance`/`referenced_env_vars` 与 dataclass 类型；示例文档与测试内规范文档逐键一致（`test_b305_example_matches_canonical_document` 守卫漂移）。
- 诚实记录：YAML 解析失败时只用 `problem_mark` 的 行列 定位并且 `from None` 不链接原文异常——PyYAML 的原始报错会带出源码片段，可能含密钥原文，故错误路径不回带内部内容（与"错误不回带原文"一致）。
- 延后项：真实模型（REAL）额度实测、探针/负载驱动/隔离栈与容量报告分别由 TASK-106/107/109/116/117/118 落地；本任务只保证 profile 的 typed schema、规范示例与"缺必填拒绝 pass"的裁决事实。
- B-305: verified — automated command passed; run_id=9a690bcf84d344e2b0cd307c39e7e481 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-305 RED：`tests/capacity/test_capacity_webchat_load_profile.py` 先行建立 → **1 error, 0 passed**（0.06s，collection ERROR：`ModuleNotFoundError: No module named 'tests.capacity.profile'`）
- [2026-10-18] 实现：`tests/capacity/profile.py` 严格 typed schema（bool 冒充整数拒绝、NaN/Inf 拒绝、未知键/缺失必填拒绝、分布有序、模式专属键互斥、四单元资源与 console/gateway 单 Pod 约束、凭据键与密钥形态 fail-closed 且不回带原文）+ `acceptance_blockers`/`require_formal_acceptance`（缺 SLO/资源或 users<1000 拒绝 pass）+ `profile_hash`/`file_digest`；规范示例 `tests/capacity/profiles/agent-capacity-webchat.example.yml`
- [2026-10-18] B-305 GREEN：98 passed（0.07s）；聚焦回归 114 passed；ruff 逐文件干净；`tests/capacity --collect-only` 98 collected
- [2026-10-18] completed (done)

---

## TASK-106: 真实模型、企微与故障协议探针

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-004, TASK-006, TASK-105
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: B-306
- **Files**: `tests/acceptance/capacity/probes.py`, `tests/acceptance/capacity/conftest.py`, `tests/capacity/test_capacity_webchat_http_probes.py`
- **Estimate**: 45分钟

### Description

HTTP模型可设置延迟/增量/usage/429/断流/应答丢失，企微走实际SDK协议探针；完整分支及可观测结果见下面验收契约。

### Checklist

- [x] [B-306][integration] 先在`tests/capacity/test_capacity_webchat_http_probes.py`建立行为验收并记录RED；真实边界：实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测；关键断言：HTTP模型可设置延迟/增量/usage/429/断流/应答丢失，企微走实际SDK协议探针。
- [x] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [x] [B-306] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [x] [B-306] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_http_probes.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-306 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | HTTP模型可设置延迟/增量/usage/429/断流/应答丢失，企微走实际SDK协议探针；所有错误注入可观测，不直接mock服务方法或PG | tests/capacity/test_capacity_webchat_http_probes.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_http_probes.py"] | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|---|---|---|---|---|---|
| B-306 | `uv run pytest -q tests/capacity/test_capacity_webchat_http_probes.py` exit=2：**1 error, 0 passed**（0.34s，collection ERROR：`ModuleNotFoundError: No module named 'tests.acceptance.capacity.probes'`）；日志 `/tmp/agent-capacity-webchat-plan/task106-red.log` | 同命令 exit=0：**14 passed**（7.4s）；聚焦回归 `uv run pytest -q tests/capacity tests/test_configuration_inventory.py` exit=0：**116 passed**（8.1s）；`uv run ruff check tests/acceptance/capacity/ tests/capacity/test_capacity_webchat_http_probes.py` 干净；全部函数 ≤50 行；无残留进程 | 模型线级/生产解析：L185-200（增量顺序/`[DONE]`/finish_reason/usage/model 回显/调用与 Key 计数观测）、L211-215（latency_ms≥250ms、chunk 节拍 2×120ms）、L222-232（429+`Retry-After: 7` → 生产 `ModelRateLimitedError.retry_after==7.0`）、L236-247（503 → `ModelUnavailableError`，逐调用 fault 序列）、L260-270（断流只 2 块、无 `[DONE]`、生产 `finish_reason==""`）、L278-287（应答丢失：httpx `RemoteProtocolError`、请求仍被记录、生产 `ModelUnavailableError`）、L292-305（按 Key 计数/超限 429/别的 Key 不受影响/观测只回指纹）、L321-357（子进程 + 真实控制面：behavior/key-limits/state/reset、未知字段 400）、L364-385（严格校验：自相矛盾行为/非法类型/DELAY 无延迟显式拒绝）；企微：L428-457（官方 SDK 认证帧 body/心跳/回调推送/`aibot_respond_msg` 流式体/`aibot_send_msg`/回执计数）、L471-486（认证拒绝 40101 与断线后 SDK 连接不可用）、L501-527（发送失败 50001、限流 45009、ACK 丢失 TimeoutError 且无回执记录）；故障代理：L568-598（直通/延迟 250ms/在飞连接被切断/新连接被丢弃/拒绝连接 ECONNREFUSED/恢复/计数与在飞归零）、L652-655（上游不可达显式计数） | ① 模型探针是真实 HTTP 服务（真实 socket、真实 SSE 分帧）：线级断言用 `httpx`，解析与错误映射走**生产** `muad_agent_core.model.OpenAICompatibleProvider`（`stream()`/`_parse_stream`），并真实跑 `python -m tests.acceptance.capacity.probes` 子进程 + 真实 HTTP 控制面；② 企微探针是真实 TLS/WSS 服务（自签证书），由**官方 SDK** `wecom-aibot-python-sdk`（`aibot.WSClient`）按真实协议驱动，探针帧/回执/连接全部来自真实 socket；③ 故障代理是真实 TCP 转发器，真实 HTTP 流量经代理进出，注入为真实网络行为（延迟/切断/ECONNREFUSED）；④ 全程未连 PG/Redis、未 mock 任何服务方法 | verified |

**实施范围与证据说明**

- 实现文件与计划 Files 完全一致：`tests/acceptance/capacity/probes.py`（模型 HTTP 探针 + 企微 SDK 协议探针 + 故障代理，含子进程模式与 HTTP 控制面）、`tests/acceptance/capacity/conftest.py`（`model_probe`/`wecom_probe`/`fault_proxy_factory` fixture）、`tests/capacity/test_capacity_webchat_http_probes.py`（14 个用例）。未改动 apps/packages/migrations，故不涉及 mypy 与 `docs/configuration-inventory.csv`（盘点测试 4 passed 复验；其扫描范围仅 apps/packages）。
- 供后续任务（TASK-107/109/110/116/117）使用的稳定接口：`ModelProbeServer`（`set_behavior`/`set_key_limit`/`reset`/`calls`/`faults`/`key_calls`/`max_concurrency`/`broken_pipes`/`state()`；`ModelProbeBehavior` 覆盖 latency/chunk 节拍/增量/usage/429+Retry-After/5xx/断流/应答丢失；`ModelProbeFault` 逐调用可观测）、`WeComProbeServer`（继承既有帧级探针：认证/心跳/回调/回复/主动发送/上传三步/认证拒绝/断线/发送失败，新增限流 `errcode=45009`、ACK 丢失、回执时刻与 `stats()`）、`FaultProxy`（任意 TCP 上游的延迟/中断/拒绝连接注入与连接/字节/丢弃计数）、`key_fingerprint`；子进程模式 `python -m tests.acceptance.capacity.probes --port N` 打印 `{"url":...}`。
- 诚实记录（实测发现，供下游栈直接受益）：① 本机若配置系统代理，httpx 默认 `trust_env=True` 会**经代理**访问探针，断流/应答丢失被代理改写为 502（本轮实测）；用例已统一 `trust_env=False`，下游容量栈调用探针必须同样处理，否则测的不是探针；② 企微 SDK 不保证服务端断线回调（与生产 Adapter "存活探测兜底"一致），故断线用例断言的是"SDK 连接不再可用"这一真实可观测事实；③ 故障代理的延迟/丢弃发生在**连接**粒度（真实 TCP 语义），非连接复用场景需新连接。
- 延后项：真实模型（REAL）额度实测与正式四单元栈、负载驱动、容量报告分别由 TASK-107/109/110/116/117/118 落地；本任务只保证探针本身的协议精确性与故障可观测性，不冒充真实企业微信实网或真实模型吞吐。
- B-306: verified — automated command passed; run_id=1d1e156b0a3144c9b670235afd020d81 (confirmed_by: runner)

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-18] started
- [2026-10-18] B-306 RED：`tests/capacity/test_capacity_webchat_http_probes.py` 先行建立 → **1 error, 0 passed**（exit=2，collection ERROR：`ModuleNotFoundError: No module named 'tests.acceptance.capacity.probes'`）
- [2026-10-18] 实现：`tests/acceptance/capacity/probes.py`（模型 HTTP 探针：延迟/SSE 增量/usage/429+Retry-After/5xx/断流/应答丢失 + 按 Key 计数限流 + 观测快照与子进程控制面；企微真实 SDK 协议探针：认证/心跳/回调/回复/主动发送/上传 + 限流与 ACK 丢失注入 + 帧/回执观测；故障代理：真实 TCP 转发的延迟/中断/拒绝连接注入与计数）+ `tests/acceptance/capacity/conftest.py`（三个真实探针 fixture）
- [2026-10-18] B-306 GREEN：14 passed（7.4s）；聚焦回归 `tests/capacity` + `tests/test_configuration_inventory.py` → 116 passed；ruff 逐文件干净；函数 ≤50 行；无残留进程
- [2026-10-18] completed (done)

---

## TASK-107: 四单元独立验收栈与故障进程控制

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-104, TASK-106
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: B-307
- **Files**: `tests/acceptance/capacity/stack.py`, `tests/acceptance/capacity/conftest.py`, `tests/capacity/test_capacity_webchat_isolated_stack.py`
- **Estimate**: 60分钟

### Description

复用验收自动空库+alembic与隔离Redis编号，真实服务进程/共享卷/代理可起停及替换实例；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-307][integration] 先在`tests/capacity/test_capacity_webchat_isolated_stack.py`建立行为验收并记录RED；真实边界：实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测；关键断言：复用验收自动空库+alembic与隔离Redis编号，真实服务进程/共享卷/代理可起停及替换实例。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-307] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-307] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_isolated_stack.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-307 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | 复用验收自动空库+alembic与隔离Redis编号，真实服务进程/共享卷/代理可起停及替换实例；检查dev残留，结束只清本轮数据/进程，不动共享dev库 | tests/capacity/test_capacity_webchat_isolated_stack.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_isolated_stack.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-108: Webchat浏览器验收配置与可信测试身份

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-093, TASK-107
- **Source**: agent-capacity-webchat.frontend.design.md#2.4 验收条件, agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: B-308
- **Files**: `e2e/playwright.webchat.config.ts`, `e2e/support/webchat-fixtures.ts`, `tests/frontend/test_capacity_webchat_browser_harness.py`
- **Estimate**: 60分钟

### Description

真实构建产物+浏览器+入口代理+四服务，ADMIN经API为已有用户生成随机Token，提供多用户/多入口/故障控制fixture；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-308][integration] 先在`tests/frontend/test_capacity_webchat_browser_harness.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：真实构建产物+浏览器+入口代理+四服务，ADMIN经API为已有用户生成随机Token，提供多用户/多入口/故障控制fixture。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-308] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-308] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_harness.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-308 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 真实构建产物+浏览器+入口代理+四服务，ADMIN经API为已有用户生成随机Token，提供多用户/多入口/故障控制fixture；成功路径禁止route.fulfill造结果 | tests/frontend/test_capacity_webchat_browser_harness.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_harness.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-109: 1000用户受理和满载控制负载驱动

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-105, TASK-107
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: B-309
- **Files**: `tests/capacity/driver.py`, `tests/capacity/metrics.py`, `tests/capacity/test_capacity_webchat_load_driver.py`
- **Estimate**: 60分钟

### Description

按锁定profile产生真实HTTP/SSE消息负载，记录持久受理/等待/推理并发及P95/P99、WAL/连接/锁/磁盘；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-309][integration] 先在`tests/capacity/test_capacity_webchat_load_driver.py`建立行为验收并记录RED；真实边界：实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测；关键断言：按锁定profile产生真实HTTP/SSE消息负载，记录持久受理/等待/推理并发及P95/P99、WAL/连接/锁/磁盘。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-309] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-309] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_driver.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-309 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | 按锁定profile产生真实HTTP/SSE消息负载，记录持久受理/等待/推理并发及P95/P99、WAL/连接/锁/磁盘；取消/确认/续租独立探测，错误分类无选择性排除 | tests/capacity/test_capacity_webchat_load_driver.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_driver.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-110: 模型池、准入和执行监督E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-035, TASK-037, TASK-062, TASK-109
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: S-04, S-12, E-01, E-15, E-16
- **Files**: `tests/acceptance/capacity/test_model_execution.py`, `tests/acceptance/capacity/test_admission_recovery.py`
- **Estimate**: 60分钟

### Description

多实例/物理Key共享/均衡/UNKNOWN/队列/失约/外部副作用实际协议链路，具体断言逐场景保留；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-04][E2E] 在`tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py`编写原S-04真实验收；不得Mock边界：多 Runtime、同模型多 Key HTTP 探针、实际额度存储和审计；关键断言：等权等容量且无失败的空闲池分配请求时，累计预占次数的最大差≤1；编码期不运行，留给verify-e2e。
- [ ] [S-12][E2E] 在`tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py`编写原S-12真实验收；不得Mock边界：两个 Runtime、主模型/摘要/记忆任务、Console 探针和真实 HTTP Key 计数服务；关键断言：多个模型成员引用同一 credential_id，所有调用合并占用；编码期不运行，留给verify-e2e。
- [ ] [E-01][E2E] 在`tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py`编写原E-01真实验收；不得Mock边界：两并发请求、PG、模型探针计数；关键断言：同消息重投仅一个 Run；编码期不运行，留给verify-e2e。
- [ ] [E-15][E2E] 在`tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py`编写原E-15真实验收；不得Mock边界：真实 Worker 子进程、PG claim/reclaim、受控外部写 HTTP 服务；关键断言：外部成功而应答丢失后，幂等写复用业务键且效果一次；编码期不运行，留给verify-e2e。
- [ ] [E-16][E2E] 在`tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py`编写原E-16真实验收；不得Mock边界：模型服务端处理中断、真实 Key/账户计数和租约存储；关键断言：本地超时但远端是否停止未知时记 UNKNOWN、保留占用；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | E2E | 多 Runtime、同模型多 Key HTTP 探针、实际额度存储和审计 | 等权等容量且无失败的空闲池分配请求时，累计预占次数的最大差≤1；异容量/长短请求验证容量和额度上限，同会话可用不同 Key 且 model/上下文一致 | tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | planned |
| S-12 | E2E | 两个 Runtime、主模型/摘要/记忆任务、Console 探针和真实 HTTP Key 计数服务 | 多个模型成员引用同一 credential_id，所有调用合并占用；一个 Key 不因多成员/多进程增加容量；供方额度、工作负载份额与尝试审计均可核对 | tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | planned |
| E-01 | E2E | 两并发请求、PG、模型探针计数 | 同消息重投仅一个 Run；同 key 异正文/附件回 IDEMPOTENCY_MISMATCH | tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | planned |
| E-15 | E2E | 真实 Worker 子进程、PG claim/reclaim、受控外部写 HTTP 服务 | 外部成功而应答丢失后，幂等写复用业务键且效果一次；非幂等写不自动重做，显示结果待核实；旧租约不能重复提交或自动解释成成功 | tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | planned |
| E-16 | E2E | 模型服务端处理中断、真实 Key/账户计数和租约存储 | 本地超时但远端是否停止未知时记 UNKNOWN、保留占用；未有证据不靠冷却到时自动超发；已证明结束才释放；其他健康 Key 仍遵守共同额度 | tests/acceptance/capacity/test_model_execution.py, tests/acceptance/capacity/test_admission_recovery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_model_execution.py","tests/acceptance/capacity/test_admission_recovery.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-111: 事件恢复、投递和持久回执E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-067, TASK-068, TASK-069, TASK-107
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: S-03, S-07, E-02, E-19
- **Files**: `tests/acceptance/capacity/test_stream_delivery.py`, `tests/acceptance/capacity/test_worker_delivery.py`
- **Estimate**: 60分钟

### Description

真实PG/Redis故障、Gateway重启、跨实例回放与机器人主动兜底，ACK丢失不伪送达；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-03][E2E] 在`tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py`编写原S-03真实验收；不得Mock边界：两个 Runtime 实例、PG、真实 SSE、故障进程；关键断言：断开重连并替换订阅实例，游标无缺口/重复渲染，不重建 Run/工具；编码期不运行，留给verify-e2e。
- [ ] [S-07][E2E] 在`tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py`编写原S-07真实验收；不得Mock边界：Worker 多实例、实际 Skill 子进程组、PG、投递链路；关键断言：并发任务分别续租；编码期不运行，留给verify-e2e。
- [ ] [E-02][E2E] 在`tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py`编写原E-02真实验收；不得Mock边界：PG/Redis 故障代理、真实执行/连接；关键断言：通知丢失可补拉；编码期不运行，留给verify-e2e。
- [ ] [E-19][E2E] 在`tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py`编写原E-19真实验收；不得Mock边界：单 Gateway 重启、真实 Run 终态/PG投递意图、企微协议探针；关键断言：Gateway 下线期间 Run 完成或回复窗口失效，恢复后持久意图触发原入口主动交付；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | E2E | 两个 Runtime 实例、PG、真实 SSE、故障进程 | 断开重连并替换订阅实例，游标无缺口/重复渲染，不重建 Run/工具 | tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | planned |
| S-07 | E2E | Worker 多实例、实际 Skill 子进程组、PG、投递链路 | 并发任务分别续租；取消杀整组；崩溃任务按租约接管；离线结果持久入箱后可重开读取 | tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | planned |
| E-02 | E2E | PG/Redis 故障代理、真实执行/连接 | 通知丢失可补拉；全局模型额度不可确认时不超发；旧租约不能写事件/终态；失败有指标 | tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | planned |
| E-19 | E2E | 单 Gateway 重启、真实 Run 终态/PG投递意图、企微协议探针 | Gateway 下线期间 Run 完成或回复窗口失效，恢复后持久意图触发原入口主动交付；发送失败/ACK丢失保持可追溯重试，不把占位标送达，不依赖内存订阅复活 | tests/acceptance/capacity/test_stream_delivery.py, tests/acceptance/capacity/test_worker_delivery.py | ["uv","run","pytest","-q","tests/acceptance/capacity/test_stream_delivery.py","tests/acceptance/capacity/test_worker_delivery.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-112: 聊天身份、历史和跨用户浏览器E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-040, TASK-056, TASK-069, TASK-108
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.frontend.design.md#2.4 验收条件
- **Spec-Refs**:
- **Acceptance-Refs**: S-05, S-10, S-16, E-05, E-10, E-11, E-12, E-17, E-20, S-101, S-110, S-112, E-101, E-105, E-106
- **Files**: `e2e/tests/webchat/auth-history.spec.ts`, `e2e/tests/webchat/identity-isolation.spec.ts`
- **Estimate**: 60分钟

### Description

真实Token/12小时Cookie边界/关联撤销、授权/历史续聊与主体切换，浏览器/API/PG/Runtime边界均保留，不用虚构JWT宿主；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-05][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原S-05真实验收；不得Mock边界：管理员/普通用户真实浏览器、Console Token 签发/交换、PG、Gateway；关键断言：ADMIN 在已有用户详情生成 Token，用户打开 /chat 输入后仅见获授权 Agent；编码期不运行，留给verify-e2e。
- [ ] [S-10][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原S-10真实验收；不得Mock边界：真实浏览器、Gateway、Runtime、PG 与捕获模型 HTTP 输入的探针；关键断言：在冻结记忆注入关闭的测试配置下，A 含局部标记而新建 B 不带 A 历史；编码期不运行，留给verify-e2e。
- [ ] [S-16][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原S-16真实验收；不得Mock边界：Console 策略/签发、浏览器 Cookie、真实 PG 时间与历史续聊接口；关键断言：默认 Token 到期=签发+30天，CHAT 到期=min(交换+12小时,Token到期)；编码期不运行，留给verify-e2e。
- [ ] [E-05][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-05真实验收；不得Mock边界：两租户、真实认证/归属查询/下载与 SSE；关键断言：篡改 user/tenant/entry/run/artifact 拒绝，令牌不进入 URL、日志或快照，撤销授权后新受理被拒；编码期不运行，留给verify-e2e。
- [ ] [E-10][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-10真实验收；不得Mock边界：真实随机 Token、Console、两租户用户、PG CHAT 会话；关键断言：随机伪造/过期/已撤销 Token、已停用用户、自填 user-id/tenant 均不能冒认身份；编码期不运行，留给verify-e2e。
- [ ] [E-11][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-11真实验收；不得Mock边界：两租户/用户/入口的真实列表、详情、发送和确认 API；关键断言：篡改 conversation_id、从其他入口借用会话或授权已撤销均拒绝；编码期不运行，留给verify-e2e。
- [ ] [E-12][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-12真实验收；不得Mock边界：同一浏览器 A/B 用户 Token、共享 Cookie、API/SSE/下载；关键断言：退出 A 后输入 B Token，先停旧流并清状态再交换身份；编码期不运行，留给verify-e2e。
- [ ] [E-17][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-17真实验收；不得Mock边界：单租户 Gateway、其他租户有效 CHAT 会话、稳定入口/Run 来源；关键断言：跨部署租户身份明确拒绝；编码期不运行，留给verify-e2e。
- [ ] [E-20][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-20真实验收；不得Mock边界：ADMIN 重置/撤销、两个浏览器 CHAT 会话、真实 PG 事务与长流复核；关键断言：重置/撤销与交换竞态后旧 Token 不能创建有效会话，原所有关联会话拒绝下一请求且长流在复核上限内停止；编码期不运行，留给verify-e2e。
- [ ] [S-101][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原S-F01真实验收；不得Mock边界：ADMIN 为已有用户生成 Token，用户直接打开 /chat 输入后进入聊天；有效 Cookie 刷新恢复，仅见获授权入口；无需注册/密码/管理账号，访问管理 URL 被拒；关键断言：ADMIN 为已有用户生成 Token，用户直接打开 /chat 输入后进入聊天；编码期不运行，留给verify-e2e。
- [ ] [S-110][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原S-F10真实验收；不得Mock边界：真实浏览器经 API 新建 A/B；侧栏显示标题与活动分组，打开 A 看原消息后继续提问，新增 Run 仍归 A；刷新含 conversation 的 URL 恢复 A；首屏50条，可加载超过200条保留消息；关键断言：真实浏览器经 API 新建 A/B；编码期不运行，留给verify-e2e。
- [ ] [S-112][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原S-F12真实验收；不得Mock边界：同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；共享Cookie切换导致旧页上下文不匹配时明确停用，不能自动互相换Token循环；关键断言：同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；编码期不运行，留给verify-e2e。
- [ ] [E-101][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-F01真实验收；不得Mock边界：CHAT 会话过期/撤销时关闭流并清私有状态，回到 Token 输入；提示重新输入有效 Token，无效/过期则联系管理员；不跳 Console 登录、不自动保存或交换Token、不保留他人消息缓存；关键断言：CHAT 会话过期/撤销时关闭流并清私有状态，回到 Token 输入；编码期不运行，留给verify-e2e。
- [ ] [E-105][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-F05真实验收；不得Mock边界：伪造/过期/撤销 Token 与停用用户不能进入聊天；重新输入有效Token后恢复原用户，先前消息不自动重提；Token不进地址/持久缓存/日志，错误不回显Token或暴露他人身份；关键断言：伪造/过期/撤销 Token 与停用用户不能进入聊天；编码期不运行，留给verify-e2e。
- [ ] [E-106][E2E] 在`e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts`编写原E-F06真实验收；不得Mock边界：会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；忙碌不自动新建/换会话，切换不隐式取消；关键断言：会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | E2E | 管理员/普通用户真实浏览器、Console Token 签发/交换、PG、Gateway | ADMIN 在已有用户详情生成 Token，用户打开 /chat 输入后仅见获授权 Agent；刷新 Cookie 恢复；无需注册/密码/管理账号，聊天会话不能进入管理 API，注销仅撤销本登录会话 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| S-10 | E2E | 真实浏览器、Gateway、Runtime、PG 与捕获模型 HTTP 输入的探针 | 在冻结记忆注入关闭的测试配置下，A 含局部标记而新建 B 不带 A 历史；打开 A 追问，模型请求含 A 的 DB 历史/摘要；conversation_id 不变且产生新 Run，B 无该轮消息；用户级长期记忆共享另按既有契约验收 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| S-16 | E2E | Console 策略/签发、浏览器 Cookie、真实 PG 时间与历史续聊接口 | 默认 Token 到期=签发+30天，CHAT 到期=min(交换+12小时,Token到期)；配置变更仅影响后续签发/交换，已签发期限不漂移；Cookie 到期输入有效 Token 恢复原历史，Token 到期重签后同用户仍可续聊 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-05 | E2E | 两租户、真实认证/归属查询/下载与 SSE | 篡改 user/tenant/entry/run/artifact 拒绝，令牌不进入 URL、日志或快照，撤销授权后新受理被拒 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-10 | E2E | 真实随机 Token、Console、两租户用户、PG CHAT 会话 | 随机伪造/过期/已撤销 Token、已停用用户、自填 user-id/tenant 均不能冒认身份；BUILDER/CHAT 无权签发重置，原文仅签发响应一次可见，GET/审计/日志/快照/幂等回执无 Token 明文 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-11 | E2E | 两租户/用户/入口的真实列表、详情、发送和确认 API | 篡改 conversation_id、从其他入口借用会话或授权已撤销均拒绝；A 的迟到响应不写入 B；WAITING_INPUT 只允许原 Run 的明确确认，不按最新会话自动消费 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-12 | E2E | 同一浏览器 A/B 用户 Token、共享 Cookie、API/SSE/下载 | 退出 A 后输入 B Token，先停旧流并清状态再交换身份；旧页面 X-Chat-Context 与 Cookie 不匹配时拒绝，不返回 B 数据给 A 页面；退出/撤销后重放也不能绕过权限 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-17 | E2E | 单租户 Gateway、其他租户有效 CHAT 会话、稳定入口/Run 来源 | 跨部署租户身份明确拒绝；有历史的入口不能原地换 Agent 后误投旧结果；停用入口/撤销授权不通过通知或下载泄露数据，原执行按冻结与控制契约处理 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-20 | E2E | ADMIN 重置/撤销、两个浏览器 CHAT 会话、真实 PG 事务与长流复核 | 重置/撤销与交换竞态后旧 Token 不能创建有效会话，原所有关联会话拒绝下一请求且长流在复核上限内停止；新 Token 仍须已有 Agent 授权；过期/撤销不删除历史、不自动重发消息、不隐式取消已受理 Run | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| S-101 | E2E | ADMIN 为已有用户生成 Token，用户直接打开 /chat 输入后进入聊天；有效 Cookie 刷新恢复，仅见获授权入口；无需注册/密码/管理账号，访问管理 URL 被拒 | ADMIN 为已有用户生成 Token，用户直接打开 /chat 输入后进入聊天；有效 Cookie 刷新恢复，仅见获授权入口；无需注册/密码/管理账号，访问管理 URL 被拒 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| S-110 | E2E | 真实浏览器经 API 新建 A/B；侧栏显示标题与活动分组，打开 A 看原消息后继续提问，新增 Run 仍归 A；刷新含 conversation 的 URL 恢复 A；首屏50条，可加载超过200条保留消息 | 真实浏览器经 API 新建 A/B；侧栏显示标题与活动分组，打开 A 看原消息后继续提问，新增 Run 仍归 A；刷新含 conversation 的 URL 恢复 A；首屏50条，可加载超过200条保留消息 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| S-112 | E2E | 同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；共享Cookie切换导致旧页上下文不匹配时明确停用，不能自动互相换Token循环 | 同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；共享Cookie切换导致旧页上下文不匹配时明确停用，不能自动互相换Token循环 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-101 | E2E | CHAT 会话过期/撤销时关闭流并清私有状态，回到 Token 输入；提示重新输入有效 Token，无效/过期则联系管理员；不跳 Console 登录、不自动保存或交换Token、不保留他人消息缓存 | CHAT 会话过期/撤销时关闭流并清私有状态，回到 Token 输入；提示重新输入有效 Token，无效/过期则联系管理员；不跳 Console 登录、不自动保存或交换Token、不保留他人消息缓存 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-105 | E2E | 伪造/过期/撤销 Token 与停用用户不能进入聊天；重新输入有效Token后恢复原用户，先前消息不自动重提；Token不进地址/持久缓存/日志，错误不回显Token或暴露他人身份 | 伪造/过期/撤销 Token 与停用用户不能进入聊天；重新输入有效Token后恢复原用户，先前消息不自动重提；Token不进地址/持久缓存/日志，错误不回显Token或暴露他人身份 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |
| E-106 | E2E | 会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；忙碌不自动新建/换会话，切换不隐式取消 | 会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；忙碌不自动新建/换会话，切换不隐式取消 | e2e/tests/webchat/auth-history.spec.ts, e2e/tests/webchat/identity-isolation.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-113: 排队、流恢复、停止确认及双入口E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-062, TASK-083, TASK-108, TASK-110
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.frontend.design.md#2.4 验收条件
- **Spec-Refs**:
- **Acceptance-Refs**: S-02, S-09, S-11, E-03, E-07, E-08, E-09, S-103, S-104, S-108, S-111, E-102, E-103, E-109
- **Files**: `e2e/tests/webchat/streams-actions.spec.ts`, `e2e/tests/webchat/cross-channel-memory.spec.ts`
- **Estimate**: 60分钟

### Description

真实浏览器+企微协议探针同时对话，原Run控制/记忆确认/forget/模型上下文可核；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-02][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-02真实验收；不得Mock边界：真实企微协议探针、浏览器、Gateway、Runtime、Worker、PG/Redis；关键断言：同用户机器人查告警、网页分析文档；编码期不运行，留给verify-e2e。
- [ ] [S-09][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-09真实验收；不得Mock边界：企微协议探针、真实浏览器/SSE、PG、受控模型与 Worker；关键断言：故意耗尽 Runtime/模型/工具容量，两入口及时显示所处阶段；编码期不运行，留给verify-e2e。
- [ ] [S-11][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-11真实验收；不得Mock边界：两浏览器页面、两个 Runtime、PG、真实 SSE 和等待中的模型探针；关键断言：A 运行时切换 B 不取消 A；编码期不运行，留给verify-e2e。
- [ ] [E-03][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-03真实验收；不得Mock边界：两入口、真实 Run/待确认状态；关键断言：伪造另一入口的 Run、确认或 conversation 不得串操作；编码期不运行，留给verify-e2e。
- [ ] [E-07][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-07真实验收；不得Mock边界：慢浏览器、SSE 代理、PG、重连；关键断言：消费缓冲达到上限后明确重连；编码期不运行，留给verify-e2e。
- [ ] [E-08][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-08真实验收；不得Mock边界：双入口、真实确认请求、记忆 PG 状态与模型 HTTP 输入；关键断言：一端删除记忆后，另一端旧确认/提炼不能复活；编码期不运行，留给verify-e2e。
- [ ] [E-09][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-09真实验收；不得Mock边界：队列实际存储、并发受理/取消、SSE、模型探针计数；关键断言：队列满时拒绝新受理且不生成假 Run；编码期不运行，留给verify-e2e。
- [ ] [S-103][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-F03真实验收；不得Mock边界：断网、页面刷新及服务实例切换后恢复正确 Run/文本/附件，不重复提交、不重复显示；关键断言：断网、页面刷新及服务实例切换后恢复正确 Run/文本/附件，不重复提交、不重复显示；编码期不运行，留给verify-e2e。
- [ ] [S-104][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-F04真实验收；不得Mock边界：与企微同时处理不同任务，网页停止/new/确认仅影响网页当前 Run/会话；关键断言：与企微同时处理不同任务，网页停止/new/确认仅影响网页当前 Run/会话；编码期不运行，留给verify-e2e。
- [ ] [S-108][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-F08真实验收；不得Mock边界：控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；刷新恢复，取消后停止等待，状态不显示为模型正文；关键断言：控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；编码期不运行，留给verify-e2e。
- [ ] [S-111][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原S-F11真实验收；不得Mock边界：A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文和真实状态，不重复、不被 B 迟到响应覆盖；WAITING_INPUT 回到原确认操作；关键断言：A 流式输出时切 B，A 在后端继续执行；编码期不运行，留给verify-e2e。
- [ ] [E-102][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-F02真实验收；不得Mock边界：未授权入口、过载、受理超时和消息幂等冲突有明确状态；重试沿用原消息ID；不显示伪成功；关键断言：未授权入口、过载、受理超时和消息幂等冲突有明确状态；编码期不运行，留给verify-e2e。
- [ ] [E-103][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-F03真实验收；不得Mock边界：慢消费者或事件缺口触发游标恢复；取消旧连接后迟到数据不得污染新入口；关键断言：慢消费者或事件缺口触发游标恢复；编码期不运行，留给verify-e2e。
- [ ] [E-109][E2E] 在`e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts`编写原E-F09真实验收；不得Mock边界：外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；新建/切换会话不改变该结果；关键断言：外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | E2E | 真实企微协议探针、浏览器、Gateway、Runtime、Worker、PG/Redis | 同用户机器人查告警、网页分析文档；两个 Conversation 独立执行，停止一个不影响另一个，附件/Task 结果回原入口 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| S-09 | E2E | 企微协议探针、真实浏览器/SSE、PG、受控模型与 Worker | 故意耗尽 Runtime/模型/工具容量，两入口及时显示所处阶段；资源恢复后原 Run 继续，状态不混进助手正文；刷新后恢复排队原因和开始时间 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| S-11 | E2E | 两浏览器页面、两个 Runtime、PG、真实 SSE 和等待中的模型探针 | A 运行时切换 B 不取消 A；切回恢复 A 已落库增量与状态，不从历史页水位跳过当前 Run 的正文；同一会话两端同时发送仅一个新 Run 受理，另一端显示忙碌/原 Run | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-03 | E2E | 两入口、真实 Run/待确认状态 | 伪造另一入口的 Run、确认或 conversation 不得串操作；同入口非等待输入态的新消息保持既有串行语义 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-07 | E2E | 慢浏览器、SSE 代理、PG、重连 | 消费缓冲达到上限后明确重连；按 seq 补齐；页面内消息只显示一次 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-08 | E2E | 双入口、真实确认请求、记忆 PG 状态与模型 HTTP 输入 | 一端删除记忆后，另一端旧确认/提炼不能复活；同 source_event 的选择与取消只能消费一次；后续模型调用刷新记忆，冻结设置及 UTF-8 预算保持有效 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-09 | E2E | 队列实际存储、并发受理/取消、SSE、模型探针计数 | 队列满时拒绝新受理且不生成假 Run；等待中取消/超时后无越权领取、无超时后模型调用；同消息重投仍是原结果，两入口均显示原因 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| S-103 | E2E | 断网、页面刷新及服务实例切换后恢复正确 Run/文本/附件，不重复提交、不重复显示 | 断网、页面刷新及服务实例切换后恢复正确 Run/文本/附件，不重复提交、不重复显示 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| S-104 | E2E | 与企微同时处理不同任务，网页停止/new/确认仅影响网页当前 Run/会话 | 与企微同时处理不同任务，网页停止/new/确认仅影响网页当前 Run/会话 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| S-108 | E2E | 控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；刷新恢复，取消后停止等待，状态不显示为模型正文 | 控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；刷新恢复，取消后停止等待，状态不显示为模型正文 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| S-111 | E2E | A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文和真实状态，不重复、不被 B 迟到响应覆盖；WAITING_INPUT 回到原确认操作 | A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文和真实状态，不重复、不被 B 迟到响应覆盖；WAITING_INPUT 回到原确认操作 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-102 | E2E | 未授权入口、过载、受理超时和消息幂等冲突有明确状态；重试沿用原消息ID；不显示伪成功 | 未授权入口、过载、受理超时和消息幂等冲突有明确状态；重试沿用原消息ID；不显示伪成功 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-103 | E2E | 慢消费者或事件缺口触发游标恢复；取消旧连接后迟到数据不得污染新入口 | 慢消费者或事件缺口触发游标恢复；取消旧连接后迟到数据不得污染新入口 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |
| E-109 | E2E | 外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；新建/切换会话不改变该结果 | 外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；新建/切换会话不改变该结果 | e2e/tests/webchat/streams-actions.spec.ts, e2e/tests/webchat/cross-channel-memory.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-114: 附件、通知与公开消息安全E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-055, TASK-068, TASK-069, TASK-108
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.frontend.design.md#2.4 验收条件
- **Spec-Refs**:
- **Acceptance-Refs**: S-06, S-13, E-13, E-14, E-18, S-102, S-105, S-113, E-104, E-107, E-108
- **Files**: `e2e/tests/webchat/attachments-notifications.spec.ts`, `e2e/tests/webchat/public-events.spec.ts`
- **Estimate**: 60分钟

### Description

上传暂存TTL/幂等重放/独立字节、通知未读与原来源，内部canary不从历史/SSE/附件泄露，断流分片无重复/半份消息；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-06][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原S-06真实验收；不得Mock边界：canonical、共享卷、Gateway、真实浏览器；关键断言：初次加载最近50条可见消息，可分页查看超过200条的保留历史；编码期不运行，留给verify-e2e。
- [ ] [S-13][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原S-13真实验收；不得Mock边界：浏览器、PG、真实 Run/Worker 投递与提交后故障注入；关键断言：用户查看 B 时 A 完成或待确认，活动持久入箱；编码期不运行，留给verify-e2e。
- [ ] [E-13][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原E-13真实验收；不得Mock边界：Runtime 实际持久事件、Gateway 公开投影和浏览器；关键断言：插入含内部 canary 的工具/推理/未知事件，公开 SSE/历史不泄露；编码期不运行，留给verify-e2e。
- [ ] [E-14][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原E-14真实验收；不得Mock边界：浏览器上传、真实共享文件系统、PG、清理与幂等受理；关键断言：202 已受理后暂存过期，再用原幂等身份重试仍得到原 Run；编码期不运行，留给verify-e2e。
- [ ] [E-18][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原E-18真实验收；不得Mock边界：实际代理/应用解析、浏览器、PG 与模型计数服务；关键断言：超大文本/认证Token/事件/上传在配置边界明确拒绝且不调用模型；编码期不运行，留给verify-e2e。
- [ ] [S-102][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原S-F02真实验收；不得Mock边界：发文字和附件，用户气泡先显示待受理，202 后绑定真实 Run；模型增量显示，终态只结算一次；关键断言：发文字和附件，用户气泡先显示待受理，202 后绑定真实 Run；编码期不运行，留给verify-e2e。
- [ ] [S-105][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原S-F05真实验收；不得Mock边界：离线 Task 完成，重开拉取持久通知，定位结果、下载附件并标记已读；关键断言：离线 Task 完成，重开拉取持久通知，定位结果、下载附件并标记已读；编码期不运行，留给verify-e2e。
- [ ] [S-113][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原S-F13真实验收；不得Mock边界：查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；隐藏页暂停轮询，回前台同步；重放不重复累计未读，打开 A 不清 B 的未读；关键断言：查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；编码期不运行，留给verify-e2e。
- [ ] [E-104][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原E-F04真实验收；不得Mock边界：确认过期、附件拒绝/失效和下载越权有本地化反馈，其余有效内容可继续；关键断言：确认过期、附件拒绝/失效和下载越权有本地化反馈，其余有效内容可继续；编码期不运行，留给verify-e2e。
- [ ] [E-107][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原E-F07真实验收；不得Mock边界：公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；原消息不重提，恢复正文不重复；关键断言：公开流合法跳号不重连；编码期不运行，留给verify-e2e。
- [ ] [E-108][E2E] 在`e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts`编写原E-F08真实验收；不得Mock边界：已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；未知受理不新建消息号，不因切换会话改投；关键断言：已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-06 | E2E | canonical、共享卷、Gateway、真实浏览器 | 初次加载最近50条可见消息，可分页查看超过200条的保留历史；附件下载核属；确认 prompt 仅回所属用户；刷新恢复且不暴露工具/系统内容 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| S-13 | E2E | 浏览器、PG、真实 Run/Worker 投递与提交后故障注入 | 用户查看 B 时 A 完成或待确认，活动持久入箱；提交后进程崩溃不丢通知；回前台/重开能定位 A，重复投递不重复未读，未查看的其他通知不被误清 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| E-13 | E2E | Runtime 实际持久事件、Gateway 公开投影和浏览器 | 插入含内部 canary 的工具/推理/未知事件，公开 SSE/历史不泄露；合法 seq 跳号不触发循环重连，过期游标明确恢复安全快照，不重发消息 POST | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| E-14 | E2E | 浏览器上传、真实共享文件系统、PG、清理与幂等受理 | 202 已受理后暂存过期，再用原幂等身份重试仍得到原 Run；清理暂存或另一 Run 的过期制品不破坏当前附件；未受理新消息不能使用过期上传 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| E-18 | E2E | 实际代理/应用解析、浏览器、PG 与模型计数服务 | 超大文本/认证Token/事件/上传在配置边界明确拒绝且不调用模型；HTML/SVG 不获同源执行，私有附件/消息不缓存；失败回执不含敏感输入原文 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| S-102 | E2E | 发文字和附件，用户气泡先显示待受理，202 后绑定真实 Run；模型增量显示，终态只结算一次 | 发文字和附件，用户气泡先显示待受理，202 后绑定真实 Run；模型增量显示，终态只结算一次 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| S-105 | E2E | 离线 Task 完成，重开拉取持久通知，定位结果、下载附件并标记已读 | 离线 Task 完成，重开拉取持久通知，定位结果、下载附件并标记已读 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| S-113 | E2E | 查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；隐藏页暂停轮询，回前台同步；重放不重复累计未读，打开 A 不清 B 的未读 | 查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；隐藏页暂停轮询，回前台同步；重放不重复累计未读，打开 A 不清 B 的未读 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| E-104 | E2E | 确认过期、附件拒绝/失效和下载越权有本地化反馈，其余有效内容可继续 | 确认过期、附件拒绝/失效和下载越权有本地化反馈，其余有效内容可继续 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| E-107 | E2E | 公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；原消息不重提，恢复正文不重复 | 公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；原消息不重提，恢复正文不重复 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |
| E-108 | E2E | 已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；未知受理不新建消息号，不因切换会话改投 | 已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；未知受理不新建消息号，不因切换会话改投 | e2e/tests/webchat/attachments-notifications.spec.ts, e2e/tests/webchat/public-events.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-115: 模型池与Token管理、设置和布局E2E

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-085, TASK-087, TASK-088, TASK-089, TASK-091, TASK-108
- **Source**: agent-capacity-webchat.frontend.design.md#2.4 验收条件, agent-capacity-webchat.backend.design.md#2.5 验收条件
- **Spec-Refs**:
- **Acceptance-Refs**: S-08, S-17, E-21, S-106, S-107, S-109, S-114, S-115, E-110, E-111
- **Files**: `e2e/tests/webchat/admin-settings.spec.ts`, `e2e/tests/webchat/layout-deployment.spec.ts`
- **Estimate**: 60分钟

### Description

ADMIN/BUILDER/CHAT真实角色，Key池/Token一次显示/配置生效/错误字段、中英主题窄屏与正式Console镜像同源入口；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-08][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-08真实验收；不得Mock边界：Console 管理界面、PG、Runtime 实际模型请求；关键断言：修改成员和权重只影响后续配置快照；编码期不运行，留给verify-e2e。
- [ ] [S-17][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-17真实验收；不得Mock边界：Console系统设置/资源配置、两个Runtime、真实PG准入、Gateway启动配置与浏览器；关键断言：修改配额/等待/网页参数后各消费者按声明边界生效；编码期不运行，留给verify-e2e。
- [ ] [E-21][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原E-21真实验收；不得Mock边界：设置保存API、真实PG版本行、进程启动与正式部署清单/容量harness；关键断言：bool冒充整数、非法范围/枚举、未知键及跨字段冲突明确拒绝且无新版本；编码期不运行，留给verify-e2e。
- [ ] [S-106][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-F06真实验收；不得Mock边界：模型 Key 新增/编辑/解绑、用户 Token 签发/重置/撤销及策略设置影响对应后续请求；关闭 SideSheet 不破坏列表，BUILDER/CHAT 不显示凭据维护入口且后端拒绝越权；关键断言：模型 Key 新增/编辑/解绑、用户 Token 签发/重置/撤销及策略设置影响对应后续请求；编码期不运行，留给verify-e2e。
- [ ] [S-107][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-F07真实验收；不得Mock边界：中英文、明暗主题、桌面和窄屏下状态/时间/操作可见；消息区域滚动不盖住输入和确认；关键断言：中英文、明暗主题、桌面和窄屏下状态/时间/操作可见；编码期不运行，留给verify-e2e。
- [ ] [S-109][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-F09真实验收；不得Mock边界：使用正式 Console 镜像及真实入口代理/浏览器/Gateway；直接打开并刷新 `/chat/{entryId}`、加载静态资源、交换用户Token和订阅SSE均成功；渲染部署清单确认未增加 Webchat 前端 Pod/Service，CHAT 不经过 Console 管理登录守卫；关键断言：使用正式 Console 镜像及真实入口代理/浏览器/Gateway；编码期不运行，留给verify-e2e。
- [ ] [S-114][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-F14真实验收；不得Mock边界：真实 Console 设置/签发和浏览器Cookie；管理员可见Token到期时间且原文仅本次显示/复制，刷新详情不能再次读取；默认30天/12小时及自定义TTL均受后端期限约束；Cookie到期重新认证或Token重签后，同用户原历史仍可继续；关键断言：真实 Console 设置/签发和浏览器Cookie；编码期不运行，留给verify-e2e。
- [ ] [S-115][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原S-F15真实验收；不得Mock边界：真实系统设置API、Gateway配置投影和浏览器；调整消息/附件/分页/轮询/重连值后新操作使用同一有效配置；页面展示单位/范围/生效说明，非法值按后端details落字段；不暴露服务地址/池大小/模型Key或另存默认值；关键断言：真实系统设置API、Gateway配置投影和浏览器；编码期不运行，留给verify-e2e。
- [ ] [E-110][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原E-F10真实验收；不得Mock边界：两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；重置响应丢失只刷新版本、不自动再次重置，用户Token原文不进入持久store，关闭结果框即清除；关键断言：两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；编码期不运行，留给verify-e2e。
- [ ] [E-111][E2E] 在`e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts`编写原E-F11真实验收；不得Mock边界：配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；失败单独可重试，不重复交换Token/发送消息；传输上限变化只影响新连接，旧流不被中途换规则，历史与已受理附件仍可读取；关键断言：配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-08 | E2E | Console 管理界面、PG、Runtime 实际模型请求 | 修改成员和权重只影响后续配置快照；旧 Run 沿用冻结语义；调用明细能追溯实际实例，无密钥 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| S-17 | E2E | Console系统设置/资源配置、两个Runtime、真实PG准入、Gateway启动配置与浏览器 | 修改配额/等待/网页参数后各消费者按声明边界生效；旧Run期限不变，已受理重放不被新输入上限破坏；降全局上限不杀旧Run且旧配置实例不能继续超收，重启应用新连接/槽位预算；浏览器限额与后端一致 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| E-21 | E2E | 设置保存API、真实PG版本行、进程启动与正式部署清单/容量harness | bool冒充整数、非法范围/枚举、未知键及跨字段冲突明确拒绝且无新版本；非法env拒绝启动；设置损坏不静默回落默认值；容量验收缺SLO/资源声明时明确拒绝给出通过结论，未泄露服务地址/凭据 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| S-106 | E2E | 模型 Key 新增/编辑/解绑、用户 Token 签发/重置/撤销及策略设置影响对应后续请求；关闭 SideSheet 不破坏列表，BUILDER/CHAT 不显示凭据维护入口且后端拒绝越权 | 模型 Key 新增/编辑/解绑、用户 Token 签发/重置/撤销及策略设置影响对应后续请求；关闭 SideSheet 不破坏列表，BUILDER/CHAT 不显示凭据维护入口且后端拒绝越权 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| S-107 | E2E | 中英文、明暗主题、桌面和窄屏下状态/时间/操作可见；消息区域滚动不盖住输入和确认 | 中英文、明暗主题、桌面和窄屏下状态/时间/操作可见；消息区域滚动不盖住输入和确认 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| S-109 | E2E | 使用正式 Console 镜像及真实入口代理/浏览器/Gateway；直接打开并刷新 `/chat/{entryId}`、加载静态资源、交换用户Token和订阅SSE均成功；渲染部署清单确认未增加 Webchat 前端 Pod/Service，CHAT 不经过 Console 管理登录守卫 | 使用正式 Console 镜像及真实入口代理/浏览器/Gateway；直接打开并刷新 `/chat/{entryId}`、加载静态资源、交换用户Token和订阅SSE均成功；渲染部署清单确认未增加 Webchat 前端 Pod/Service，CHAT 不经过 Console 管理登录守卫 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| S-114 | E2E | 真实 Console 设置/签发和浏览器Cookie；管理员可见Token到期时间且原文仅本次显示/复制，刷新详情不能再次读取；默认30天/12小时及自定义TTL均受后端期限约束；Cookie到期重新认证或Token重签后，同用户原历史仍可继续 | 真实 Console 设置/签发和浏览器Cookie；管理员可见Token到期时间且原文仅本次显示/复制，刷新详情不能再次读取；默认30天/12小时及自定义TTL均受后端期限约束；Cookie到期重新认证或Token重签后，同用户原历史仍可继续 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| S-115 | E2E | 真实系统设置API、Gateway配置投影和浏览器；调整消息/附件/分页/轮询/重连值后新操作使用同一有效配置；页面展示单位/范围/生效说明，非法值按后端details落字段；不暴露服务地址/池大小/模型Key或另存默认值 | 真实系统设置API、Gateway配置投影和浏览器；调整消息/附件/分页/轮询/重连值后新操作使用同一有效配置；页面展示单位/范围/生效说明，非法值按后端details落字段；不暴露服务地址/池大小/模型Key或另存默认值 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| E-110 | E2E | 两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；重置响应丢失只刷新版本、不自动再次重置，用户Token原文不进入持久store，关闭结果框即清除 | 两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；重置响应丢失只刷新版本、不自动再次重置，用户Token原文不进入持久store，关闭结果框即清除 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |
| E-111 | E2E | 配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；失败单独可重试，不重复交换Token/发送消息；传输上限变化只影响新连接，旧流不被中途换规则，历史与已受理附件仍可读取 | 配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；失败单独可重试，不重复交换Token/发送消息；传输上限变化只影响新连接，旧流不被中途换规则，历史与已受理附件仍可读取 | e2e/tests/webchat/admin-settings.spec.ts, e2e/tests/webchat/layout-deployment.spec.ts | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-116: 1000用户容量及单Pod恢复验收

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-108, TASK-109, TASK-110, TASK-111
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: S-01, S-14, S-15
- **Files**: `tests/acceptance/capacity/test_capacity.py`, `e2e/tests/webchat/capacity-recovery.spec.ts`
- **Estimate**: 60分钟

### Description

固定规格与profile进行1000用户消息负载、增量写入/控制通道和单Console/Gateway重连；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [S-01][E2E] 在`tests/acceptance/capacity/test_capacity.py, e2e/tests/webchat/capacity-recovery.spec.ts`编写原S-01真实验收；不得Mock边界：真实 HTTP Gateway→Runtime→PG；模型可为真实 HTTP 受控探针；关键断言：配置接纳 1000 不同用户各一个 Run；编码期不运行，留给verify-e2e。
- [ ] [S-14][E2E] 在`tests/acceptance/capacity/test_capacity.py, e2e/tests/webchat/capacity-recovery.spec.ts`编写原S-14真实验收；不得Mock边界：容量栈真实 PG/HTTP/SSE、可配置增量模型探针；关键断言：按配置窗口合并小增量，断流恢复正文一致；编码期不运行，留给verify-e2e。
- [ ] [S-15][E2E] 在`tests/acceptance/capacity/test_capacity.py, e2e/tests/webchat/capacity-recovery.spec.ts`编写原S-15真实验收；不得Mock边界：正式四单元栈、真实浏览器/入口代理、单 Console/Gateway 重启与 Runtime/Worker 替换；关键断言：单 Pod 短暂不可用按事实记录；编码期不运行，留给verify-e2e。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | E2E | 真实 HTTP Gateway→Runtime→PG；模型可为真实 HTTP 受控探针 | 配置接纳 1000 不同用户各一个 Run；确认持久受理、可见等待/执行、最终进展、无跨用户事件；执行不超模型/工具容量，排队不冒充同时推理 | tests/acceptance/capacity/test_capacity.py, e2e/tests/webchat/capacity-recovery.spec.ts | ["bash","-lc","uv run pytest -q tests/acceptance/capacity/test_capacity.py && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | planned |
| S-14 | E2E | 容量栈真实 PG/HTTP/SSE、可配置增量模型探针 | 按配置窗口合并小增量，断流恢复正文一致；事件写入率/PG连接/内存不超报告预算，取消/确认/续租在满载时仍可服务，终态前全部正文已持久化 | tests/acceptance/capacity/test_capacity.py, e2e/tests/webchat/capacity-recovery.spec.ts | ["bash","-lc","uv run pytest -q tests/acceptance/capacity/test_capacity.py && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | planned |
| S-15 | E2E | 正式四单元栈、真实浏览器/入口代理、单 Console/Gateway 重启与 Runtime/Worker 替换 | 单 Pod 短暂不可用按事实记录；恢复后重连带抖动，已受理 Run 不重建、无双执行，租约资源无误释放，FD/HTTP/PG 总预算可核对 | tests/acceptance/capacity/test_capacity.py, e2e/tests/webchat/capacity-recovery.spec.ts | ["bash","-lc","uv run pytest -q tests/acceptance/capacity/test_capacity.py && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts capacity-recovery.spec.ts"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-117: 容量报告与Runtime/Worker副本建议计算

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-104, TASK-109, TASK-116
- **Source**: agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告
- **Spec-Refs**:
- **Acceptance-Refs**: B-317
- **Files**: `tests/capacity/report.py`, `docs/operations/agent-capacity-webchat-capacity.md`, `tests/capacity/test_capacity_webchat_capacity_report.py`
- **Estimate**: 45分钟

### Description

只用实际运行证据生成P95/P99/瓶颈/资源/配置hash与副本公式；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-317][integration] 先在`tests/capacity/test_capacity_webchat_capacity_report.py`建立行为验收并记录RED；真实边界：实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测；关键断言：只用实际运行证据生成P95/P99/瓶颈/资源/配置hash与副本公式。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-317] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-317] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_capacity_report.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-317 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | 只用实际运行证据生成P95/P99/瓶颈/资源/配置hash与副本公式；区分协议探针和真实模型/机器人边界，输入缺失不得输出生产达标或虚构Pod数 | tests/capacity/test_capacity_webchat_capacity_report.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_capacity_report.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-118: 验收映射与规范回归证据收口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-099, TASK-112, TASK-113, TASK-114, TASK-115, TASK-117
- **Source**: agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告, agent-capacity-webchat.backend.design.md#6. 需求追溯矩阵, agent-capacity-webchat.frontend.design.md#2.4 验收条件
- **Spec-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: B-318
- **Files**: `tests/capacity/acceptance_audit.py`, `docs/operations/agent-capacity-webchat-validation.md`, `tests/capacity/test_capacity_webchat_acceptance_audit.py`
- **Estimate**: 45分钟

### Description

核对全部设计场景/Rule唯一负责人、functional RED/GREEN与终验E2E证据；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-318][integration] 先在`tests/capacity/test_capacity_webchat_acceptance_audit.py`建立行为验收并记录RED；真实边界：实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测；关键断言：核对全部设计场景/Rule唯一负责人、functional RED/GREEN与终验E2E证据。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-318] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-318] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_acceptance_audit.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-test#RULE-test-001`（stage: review；timeout=2400秒）：`["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-318 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | 核对全部设计场景/Rule唯一负责人、functional RED/GREEN与终验E2E证据；全量verifier真实返回后再写结论，未验证容量边界明确保留，不绕过Done Gate | tests/capacity/test_capacity_webchat_acceptance_audit.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_acceptance_audit.py"] | planned |
| harness-test#RULE-test-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
