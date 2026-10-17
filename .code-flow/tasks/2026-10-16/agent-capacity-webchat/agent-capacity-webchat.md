# Tasks: Agent 并发容量与通用 Webchat

- **Source**: .code-flow/tasks/2026-10-16/agent-capacity-webchat/（前后端v0.6设计合并）
- **Created**: 2026-10-16
- **Updated**: 2026-10-17
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
| E-04 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实故障模型 HTTP 服务和调度存储 | TASK-025 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | . | 300 |  |
| E-05 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | E2E | 两租户、真实认证/归属查询/下载与 SSE | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| E-06 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实压缩/记忆提炼接线、持久快照和 model HTTP | TASK-026 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | . | 300 |  |
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
| B-01 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 并发预占/释放、实际租约存储 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | . | 300 |  |
| B-02 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实凭据/会话/附件归属存储 | TASK-053 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | . | 300 |  |
| B-03 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 跨页读取和提交顺序、PG | TASK-064 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | . | 300 |  |
| B-04 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 实际 Conversation/CanonicalEvent 查询与索引 | TASK-050 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | . | 300 |  |
| B-05 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 实际准入计数、Run 事务、取消/领取竞态和 PG 行锁 | TASK-031 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | . | 300 |  |
| B-06 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 实际公开事件 DTO/水位和恢复服务 | TASK-060 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | . | 300 |  |
| B-07 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实保留/清理查询、活跃 Run/job 与共享文件系统 | TASK-069 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | . | 300 |  |
| B-08 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | 真实 PG Token/会话约束、有效期裁决与用户锁 | TASK-040 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | . | 300 |  |
| B-09 | agent-capacity-webchat.backend.design.md#2.5 验收条件 | integration | contracts配置解析、真实PG配置/快照、全局准入版本和启动settings | TASK-094 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | . | 300 |  |
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
| B-101 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 | TASK-072 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | . | 300 |  |
| B-102 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 请求序号和 AbortSignal 屏蔽迟到响应；空列表、空 children 和字段边界完整显示 | TASK-093 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"] | . | 300 |  |
| S-108 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 控制模型/工具容量触发等待，显示“已收到，正在排队/正在等待模型资源/正在执行任务”；刷新恢复，取消后停止等待，状态不显示为模型正文 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-105 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 伪造/过期/撤销 Token 与停用用户不能进入聊天；重新输入有效Token后恢复原用户，先前消息不自动重提；Token不进地址/持久缓存/日志，错误不回显Token或暴露他人身份 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-109 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 使用正式 Console 镜像及真实入口代理/浏览器/Gateway；直接打开并刷新 `/chat/{entryId}`、加载静态资源、交换用户Token和订阅SSE均成功；渲染部署清单确认未增加 Webchat 前端 Pod/Service，CHAT 不经过 Console 管理登录守卫 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| S-110 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 真实浏览器经 API 新建 A/B；侧栏显示标题与活动分组，打开 A 看原消息后继续提问，新增 Run 仍归 A；刷新含 conversation 的 URL 恢复 A；首屏50条，可加载超过200条保留消息 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-111 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文和真实状态，不重复、不被 B 迟到响应覆盖；WAITING_INPUT 回到原确认操作 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| E-106 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 会话越权/其他入口、归档不可续聊、同会话正在运行或授权撤销均有准确反馈；忙碌不自动新建/换会话，切换不隐式取消 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| B-103 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 | TASK-078 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | . | 300 |  |
| S-112 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 同浏览器退出A并输入B的Token，验证旧流/消息/草稿/图片Blob已清，先完成B认证才拉B历史；共享Cookie切换导致旧页上下文不匹配时明确停用，不能自动互相换Token循环 | TASK-112 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts auth-history.spec.ts identity-isolation.spec.ts"] | . | 1800 |  |
| S-113 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 查看 B 时 A 完成或待确认，服务端活动通知可见且能定位 A；隐藏页暂停轮询，回前台同步；重放不重复累计未读，打开 A 不清 B 的未读 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-107 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；原消息不重提，恢复正文不重复 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-108 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 已受理消息响应丢失且上传暂存后来过期，原身份重试仍恢复原 Run 与附件；未知受理不新建消息号，不因切换会话改投 | TASK-114 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts attachments-notifications.spec.ts public-events.spec.ts"] | . | 1800 |  |
| E-109 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 外部任务结果待核实时清楚显示状态并定位原任务，不自动重试写操作或显示伪成功；新建/切换会话不改变该结果 | TASK-113 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts streams-actions.spec.ts cross-channel-memory.spec.ts"] | . | 1800 |  |
| B-104 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 | TASK-074 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | . | 300 |  |
| S-114 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 真实 Console 设置/签发和浏览器Cookie；管理员可见Token到期时间且原文仅本次显示/复制，刷新详情不能再次读取；默认30天/12小时及自定义TTL均受后端期限约束；Cookie到期重新认证或Token重签后，同用户原历史仍可继续 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| E-110 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 两个真实浏览器登录同用户后，ADMIN重置/撤销Token，旧Token和关联Cookie拒绝访问、长流有界关闭；重置响应丢失只刷新版本、不自动再次重置，用户Token原文不进入持久store，关闭结果框即清除 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| S-115 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 真实系统设置API、Gateway配置投影和浏览器；调整消息/附件/分页/轮询/重连值后新操作使用同一有效配置；页面展示单位/范围/生效说明，非法值按后端details落字段；不暴露服务地址/池大小/模型Key或另存默认值 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| E-111 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | E2E | 配置加载失败、设置版本冲突及进程新旧transport_revision的真实HTTP边界；失败单独可重试，不重复交换Token/发送消息；传输上限变化只影响新连接，旧流不被中途换规则，历史与已受理附件仍可读取 | TASK-115 | planned | ["bash","-lc","npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.webchat.config.ts admin-settings.spec.ts layout-deployment.spec.ts"] | . | 1800 |  |
| B-105 | agent-capacity-webchat.frontend.design.md#2.4 验收条件 | integration | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 | TASK-072 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | . | 300 |  |
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
| B-216 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-016 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_api.py"] | . | 300 |  |
| B-217 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-017 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_deployment_relations.py"] | . | 300 |  |
| B-218 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-018 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_quota_groups.py"] | . | 300 |  |
| B-219 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-019 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_logical_model_api.py"] | . | 300 |  |
| B-220 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-020 | planned | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_resolve_credential.py"] | . | 300 |  |
| B-221 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-021 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | . | 300 |  |
| B-222 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-022 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_settle.py"] | . | 300 |  |
| B-223 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-023 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_allocator.py"] | . | 300 |  |
| B-224 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-024 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_snapshot.py"] | . | 300 |  |
| B-225 | agent-capacity-webchat.backend.design.md#3.1 方案选型与决策, agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-025 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | . | 300 |  |
| B-226 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-026 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | . | 300 |  |
| B-227 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-027 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py"] | . | 300 |  |
| B-228 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-028 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_probe.py"] | . | 300 |  |
| B-229 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-029 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_policy.py"] | . | 300 |  |
| B-230 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-030 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_create.py"] | . | 300 |  |
| B-231 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-031 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | . | 300 |  |
| B-232 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-032 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_slots.py"] | . | 300 |  |
| B-233 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-033 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_slots.py"] | . | 300 |  |
| B-234 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-034 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_retry_safety.py"] | . | 300 |  |
| B-235 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-035 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_clock_lease.py"] | . | 300 |  |
| B-236 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-036 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_queue_projection.py"] | . | 300 |  |
| B-237 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-037 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py"] | . | 300 |  |
| B-238 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-038 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_service.py"] | . | 300 |  |
| B-239 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-039 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_admin_api.py"] | . | 300 |  |
| B-240 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-040 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | . | 300 |  |
| B-241 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.1 技术选型 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-041 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_cookie_security.py"] | . | 300 |  |
| B-242 | agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-042 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_auth_api.py"] | . | 300 |  |
| B-243 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-043 | planned | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_chat_internal_principal.py"] | . | 300 |  |
| B-244 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-044 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_chat_auth.py"] | . | 300 |  |
| B-245 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-045 | planned | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_redaction.py"] | . | 300 |  |
| B-246 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-046 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_webchat_entry.py"] | . | 300 |  |
| B-247 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.3 数据设计 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-047 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_adapter.py"] | . | 300 |  |
| B-248 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-048 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_admission.py"] | . | 300 |  |
| B-249 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-049 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_conversation_scope.py"] | . | 300 |  |
| B-250 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-050 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | . | 300 |  |
| B-251 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-051 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_public_projection.py"] | . | 300 |  |
| B-252 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-052 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_resource_api.py"] | . | 300 |  |
| B-253 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-053 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | . | 300 |  |
| B-254 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-054 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_upload_gateway.py"] | . | 300 |  |
| B-255 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-055 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_artifact_download.py"] | . | 300 |  |
| B-256 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-056 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_run_controls.py"] | . | 300 |  |
| B-257 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-057 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_client_config_api.py"] | . | 300 |  |
| B-258 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-058 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py"] | . | 300 |  |
| B-259 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-059 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_durable_deltas.py"] | . | 300 |  |
| B-260 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-060 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | . | 300 |  |
| B-261 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-061 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_sse.py"] | . | 300 |  |
| B-262 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-062 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_reply_generations.py"] | . | 300 |  |
| B-263 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-063 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_write.py"] | . | 300 |  |
| B-264 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-064 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | . | 300 |  |
| B-265 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-065 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_reservation.py"] | . | 300 |  |
| B-266 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-066 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_pump.py"] | . | 300 |  |
| B-267 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-067 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_delivery.py"] | . | 300 |  |
| B-268 | agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-068 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_notifications.py"] | . | 300 |  |
| B-269 | agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-069 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | . | 300 |  |
| B-270 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-070 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_notification_api.py"] | . | 300 |  |
| B-271 | agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.backend.design.md#3.4 接口设计 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-071 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_transport.py"] | . | 300 |  |
| B-272 | agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-072 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | . | 300 |  |
| B-273 | agent-capacity-webchat.frontend.design.md#3.1 技术选型, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-073 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_services.py"] | . | 300 |  |
| B-274 | agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-074 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | . | 300 |  |
| B-275 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-075 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_token_access_ui.py"] | . | 300 |  |
| B-276 | agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-076 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_client_config_state.py"] | . | 300 |  |
| B-277 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-077 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_route.py"] | . | 300 |  |
| B-278 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-078 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | . | 300 |  |
| B-279 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.7 样式方案, agent-capacity-webchat.frontend.design.md#3.8 可访问性 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-079 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_history_sidebar.py"] | . | 300 |  |
| B-280 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-080 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_message_history_ui.py"] | . | 300 |  |
| B-281 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-081 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_composer_uploads.py"] | . | 300 |  |
| B-282 | agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-082 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_reply_stream_state.py"] | . | 300 |  |
| B-283 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-083 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_run_actions_ui.py"] | . | 300 |  |
| B-284 | agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-084 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_activity_ui.py"] | . | 300 |  |
| B-285 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-085 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_user_token_ui.py"] | . | 300 |  |
| B-286 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-086 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_credential_ui.py"] | . | 300 |  |
| B-287 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.4 组件接口契约 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-087 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_deployment_ui.py"] | . | 300 |  |
| B-288 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-088 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_quota_ui.py"] | . | 300 |  |
| B-289 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.6 UI 状态 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-089 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_settings_ui.py"] | . | 300 |  |
| B-290 | agent-capacity-webchat.frontend.design.md#3.5 状态与数据流, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-090 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_i18n.py"] | . | 300 |  |
| B-291 | agent-capacity-webchat.frontend.design.md#3.7 样式方案, agent-capacity-webchat.frontend.design.md#3.8 可访问性 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-091 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_layout.py"] | . | 300 |  |
| B-292 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.7 样式方案 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-092 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_model_admin_wiring.py"] | . | 300 |  |
| B-293 | agent-capacity-webchat.frontend.design.md#3.2 页面与路由, agent-capacity-webchat.frontend.design.md#3.3 组件设计, agent-capacity-webchat.frontend.design.md#3.5 状态与数据流 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | TASK-093 | planned | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_frontend_wiring.py"] | . | 300 |  |
| B-294 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | TASK-094 | planned | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | . | 300 |  |
| B-295 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | TASK-095 | planned | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_wiring.py"] | . | 300 |  |
| B-296 | agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | TASK-096 | planned | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_wiring.py"] | . | 300 |  |
| B-297 | agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | TASK-097 | planned | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_wiring.py"] | . | 300 |  |
| B-298 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-098 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py"] | . | 300 |  |
| B-299 | agent-capacity-webchat.backend.design.md#3.5 质量实现方案, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-099 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_worker_console_metrics.py"] | . | 300 |  |
| B-300 | agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.frontend.design.md#3.2 页面与路由 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-100 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_console_gateway_deploy.py"] | . | 300 |  |
| B-301 | agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.backend.design.md#4.2 发布与迁移 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-101 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_worker_deploy.py"] | . | 300 |  |
| B-302 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量, agent-capacity-webchat.frontend.design.md#3.2 页面与路由 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-102 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_configmap_ingress.py"] | . | 300 |  |
| B-303 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.1 部署与容量 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-103 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_hpa_budget.py"] | . | 300 |  |
| B-304 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.2 发布与迁移, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | TASK-104 | planned | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_deployment_preflight.py"] | . | 300 |  |
| B-305 | agent-capacity-webchat.backend.design.md#3.7 配置契约与生效边界, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | TASK-105 | planned | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_profile.py"] | . | 300 |  |
| B-306 | agent-capacity-webchat.backend.design.md#2.5 验收条件, agent-capacity-webchat.backend.design.md#4.3 分层验收与报告 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | TASK-106 | planned | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_http_probes.py"] | . | 300 |  |
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

- **Status**: draft
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

- [ ] [B-216][integration] 先在`tests/console_platform/test_capacity_webchat_credential_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：凭据创建/更新/删除仅ADMIN+CSRF，读取仅configured。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-216] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-216] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-216 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 凭据创建/更新/删除仅ADMIN+CSRF，读取仅configured；关联删除/降额冲突明确，创建按strict canonical JSON持久幂等回放 | tests/console_platform/test_capacity_webchat_credential_api.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_credential_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-017: 模型成员绑定、解绑及权重修改

- **Status**: draft
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

- [ ] [B-217][integration] 先在`tests/console_platform/test_capacity_webchat_deployment_relations.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：成员只引用credential，不复制Key。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-217] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-217] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_deployment_relations.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-rel#RULE-rel-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-217 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 成员只引用credential，不复制Key；同供方/逻辑模型核属，POST/DELETE单关系修改、权重版本冲突可见，禁止集合整体PUT | tests/console_platform/test_capacity_webchat_deployment_relations.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_deployment_relations.py"] | planned |
| harness-rel#RULE-rel-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 关系类修改使用单关系 POST/DELETE 并由独立事务完成；禁止用全量 PUT 覆盖整个关系集合。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_platform/test_user_side_relations.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-018: 共享账号额度组管理与降额限制

- **Status**: draft
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

- [ ] [B-218][integration] 先在`tests/console_platform/test_capacity_webchat_quota_groups.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：RPM/TPM与max_inflight按真实资源存储，null仅关闭该维度限流。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-218] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-218] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_quota_groups.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-218 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | RPM/TPM与max_inflight按真实资源存储，null仅关闭该维度限流；被引用不可删除，额度归属/降额按排空契约处理 | tests/console_platform/test_capacity_webchat_quota_groups.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_quota_groups.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-019: 逻辑模型配置去除执行密钥并要求真实输出预算

- **Status**: draft
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

- [ ] [B-219][integration] 先在`tests/console_platform/test_capacity_webchat_logical_model_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：Agent继续显式选择model_id、无默认模型。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-219] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-219] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_logical_model_api.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-model#RULE-model-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-219 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | Agent继续显式选择model_id、无默认模型；OPENAI/protocol不暗换，模型最大输出使用真正发送的max_tokens，旧base_url/api_key执行入口删除（末段 revision 物理删列；0021 为过渡保留，删列生命周期由本任务收口） | tests/console_platform/test_capacity_webchat_logical_model_api.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_logical_model_api.py"] | planned |
| harness-model#RULE-model-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | `model_definition` 不设 `is_default`；每个 Agent 必须显式保存 `model_id`，不存在平台默认模型回退；模型调用只允许 OPENAI 兼容协议。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/console_platform/test_models_api.py && uv run pytest -q tests/console_platform/test_agents_api.py -k disabled"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）
- [2026-10-17] 承接计划变更：`model_definition.base_url`/`api_key` 的物理删除从 TASK-009 的 0021 移至本任务的末段删列 revision（用户确认；0021 只建新结构并保留旧列，`provider_key` 过渡期 `''`）

---

## TASK-020: 所选成员凭据的内部实时解析

- **Status**: draft
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

- [ ] [B-220][integration] 先在`tests/console_internal/test_capacity_webchat_resolve_credential.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：服务身份门控，按冻结model/member/credential核属并检查expected_revision。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-220] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-220] 执行`["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_resolve_credential.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-secret#RULE-secret-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-220 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 服务身份门控，按冻结model/member/credential核属并检查expected_revision；返回真实Key身份/版本仅在调用内存使用，MCP可选鉴权语义保留 | tests/console_internal/test_capacity_webchat_resolve_credential.py | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_resolve_credential.py"] | planned |
| harness-secret#RULE-secret-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP `auth_secret`、用户/共享凭据 `credential_json`；**平台侧无自有密钥列**，见 Conventions 的休眠列说明），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_logging_redaction.py","tests/acceptance/test_foundation_ops_audit.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-021: PG物理Key与额度组原子预占

- **Status**: draft
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

- [ ] [B-221][integration] 先在`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：池→quota_group→key→attempt固定锁序。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-221] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-221] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-01][integration] 将原B-01落入`tests/agent_runtime/test_capacity_webchat_capacity_reserve.py`，先RED后GREEN；并发预占/释放、实际租约存储；断言：容量边界、重复释放、异常退出、取消和租约失效均不超发；失约执行停手。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-221 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 池→quota_group→key→attempt固定锁序；重复attempt不重复占位，跨模型/主调用/摘要/提炼/探针合并真实Key用量，锁内不联网 | tests/agent_runtime/test_capacity_webchat_capacity_reserve.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | planned |
| B-01 | integration | 并发预占/释放、实际租约存储 | 容量边界、重复释放、异常退出、取消和租约失效均不超发；失约执行停手 | tests/agent_runtime/test_capacity_webchat_capacity_reserve.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_reserve.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-022: 调用续租、usage结算与UNKNOWN保留

- **Status**: draft
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

- [ ] [B-222][integration] 先在`tests/agent_runtime/test_capacity_webchat_capacity_settle.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：epoch/租约CAS、重复释放和跨分钟结算正确。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-222] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-222] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_settle.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-222 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | epoch/租约CAS、重复释放和跨分钟结算正确；本地超时不释放远端未知占用，有完成证据才结算，无usage保留保守预占 | tests/agent_runtime/test_capacity_webchat_capacity_settle.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_capacity_settle.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-023: 加权均衡、共享epoch与工作负载公平调度

- **Status**: draft
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

- [ ] [B-223][integration] 先在`tests/agent_runtime/test_capacity_webchat_pool_allocator.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：等权空闲池预占次数差≤1。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-223] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-223] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_allocator.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-223 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 等权空闲池预占次数差≤1；按dispatch_count/weight与在飞量稳定轮转，重试计尝试、低额度不超发，后台工作按冻结权重/老化可进展 | tests/agent_runtime/test_capacity_webchat_pool_allocator.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_allocator.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-024: 冻结模型池语义与非密钥执行策略

- **Status**: draft
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

- [ ] [B-224][integration] 先在`tests/agent_runtime/test_capacity_webchat_pool_snapshot.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：冻结model/member/路由/输出/等待及完整记忆设置，排除Key/指纹/计量身份与启动预算。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-224] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-224] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_snapshot.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-memory#RULE-memory-006`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。
- [ ] verifier `harness-snapshot#RULE-snapshot-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-224 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 冻结model/member/路由/输出/等待及完整记忆设置，排除Key/指纹/计量身份与启动预算；旧resume不补新默认、不更换型号或延长原deadline | tests/agent_runtime/test_capacity_webchat_pool_snapshot.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_pool_snapshot.py"] | planned |
| harness-memory#RULE-memory-006 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 新 Run/job 冻结完整 memory 设置、工具历史预算及提炼能力；设置执行期不重读，在线硬额度只提高，降低必须维护窗口排空。迁移需排空所有旧在途 Run/Task/job；每 key 单一稳定基线保留全部 legacy incarnation，不伪造确认来源，旧 AGENT 按原更新时间补 TTL。迁移和 ORM 约束/索引同构；移除旧 ratio/source_type 协议，历史快照原样保留仅作审计，不加兼容写层。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/migrations/test_memory_redesign_migration.py","tests/agent_runtime/test_memory_redesign_snapshot.py"] | planned |
| harness-snapshot#RULE-snapshot-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-025: 真实AuditedModelProvider调用链接入资源池

- **Status**: draft
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

- [ ] [B-225][integration] 先在`tests/agent_runtime/test_capacity_webchat_provider_pool.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：预占提交后解析所选Key，轮换不一致按未发送释放/有界重选。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-225] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-225] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [E-04][integration] 将原E-04落入`tests/agent_runtime/test_capacity_webchat_provider_pool.py`，先RED后GREEN；真实故障模型 HTTP 服务和调度存储；断言：429/5xx/断流在 deadline/重试预算内处理；已输出正文不混拼另一轮回复；不跨型号暗换。
- [ ] verifier `harness-memory#RULE-memory-003`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-225 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 预占提交后解析所选Key，轮换不一致按未发送释放/有界重选；同会话可换Key但上下文/型号一致，429遵守Retry-After与原deadline，逐attempt审计可追溯 | tests/agent_runtime/test_capacity_webchat_provider_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | planned |
| E-04 | integration | 真实故障模型 HTTP 服务和调度存储 | 429/5xx/断流在 deadline/重试预算内处理；已输出正文不混拼另一轮回复；不跨型号暗换 | tests/agent_runtime/test_capacity_webchat_provider_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_provider_pool.py"] | planned |
| harness-memory#RULE-memory-003 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 候选查询读取用户完整有界集合，禁止 latest-N 截断；非空 SEARCH 先匹配，零覆盖返回空，空 query 为 LIST，偏好注入不依赖 query。revision 缓存只存候选行：冷读取行与实际 revision 同快照，暖读取 revision 与当前语句时间，并逐次过滤 TTL、重新选择/渲染；读取失败清除旧缓存和 MemoryComponent。每个模型请求前刷新独立 MemoryComponent，完整 SYSTEM 块及 recall JSON 严守最终 UTF-8 字节与条数预算；0 关闭注入，长条跳过，无首条越限保底，压缩不能吞掉记忆组件。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_cache.py","tests/agent_runtime/test_memory_redesign_context.py","tests/agent_core/test_memory_redesign_selection.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-026: 摘要和记忆提炼共享模型容量

- **Status**: draft
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

- [ ] [B-226][integration] 先在`tests/agent_runtime/test_capacity_webchat_background_pool.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：摘要/提炼都走统一物理资源预算。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-226] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-226] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [E-06][integration] 将原E-06落入`tests/agent_runtime/test_capacity_webchat_background_pool.py`，先RED后GREEN；真实压缩/记忆提炼接线、持久快照和 model HTTP；断言：摘要失败保留原历史；提炼失败整批回滚且主 Run 不变；槽位泄漏和旧记忆缓存不残留。
- [ ] verifier `harness-memory#RULE-memory-005`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-226 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 摘要/提炼都走统一物理资源预算；摘要失败原历史继续、批量提炼失败回滚且主Run不失败，冻结来源/epoch/forget tombstone与可观测退化保留 | tests/agent_runtime/test_capacity_webchat_background_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | planned |
| E-06 | integration | 真实压缩/记忆提炼接线、持久快照和 model HTTP | 摘要失败保留原历史；提炼失败整批回滚且主 Run 不变；槽位泄漏和旧记忆缓存不残留 | tests/agent_runtime/test_capacity_webchat_background_pool.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_background_pool.py"] | planned |
| harness-memory#RULE-memory-005 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 只有 COMPLETED Run 可入队持久 consolidation job；唯一 job、claim epoch/lease 和终态共同 fence 提交权。输入事件 through_seq、摘要自身 seq/覆盖边界、hash、scope revision 与来源 Run 策略首次冻结后重试不刷新；独立 provider 使用冻结主模型、实时凭据并显式关闭。模型调用次数不保证一次，持久效果/历史/完成事件/job 终态必须同事务最多一次；失败整批回滚，warning/metric 可见，主 Run 终态不变。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_input_boundary.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-027: Console探针的受信容量端口

- **Status**: draft
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

- [ ] [B-227][integration] 先在`tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：API-17只接受受信probe身份、正确attempt/epoch与期限。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-227] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-227] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-227 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | API-17只接受受信probe身份、正确attempt/epoch与期限；reserve/renew/settle幂等，探针与Run/job共占实际Key及账号额度 | tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_probe_capacity_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-028: 物理Key检测保持Console侧HTTP实现

- **Status**: draft
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

- [ ] [B-228][integration] 先在`tests/console_platform/test_capacity_webchat_console_probe.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：至多50个model/credential对、并发≤4、单次≤5秒。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-228] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-228] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_probe.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-228 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 至多50个model/credential对、并发≤4、单次≤5秒；先占额再由Console探测，单成员成功不伪称整池健康，不改配置revision/计费审计 | tests/console_platform/test_capacity_webchat_console_probe.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_probe.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-029: 全局准入阈值的单调版本应用

- **Status**: draft
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

- [ ] [B-229][integration] 先在`tests/agent_runtime/test_capacity_webchat_admission_policy.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：source_settings_revision只增不退，policy锁在会话锁之前。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-229] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-229] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_policy.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-229 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | source_settings_revision只增不退，policy锁在会话锁之前；旧实例不能按旧阈值超收，降额不杀现有Run，低版本网络刷新在事务外 | tests/agent_runtime/test_capacity_webchat_admission_policy.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_policy.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-030: Run受理与准入计数同事务

- **Status**: draft
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

- [ ] [B-230][integration] 先在`tests/agent_runtime/test_capacity_webchat_admission_create.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：全部非终态占用户/租户共享份额。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-230] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-230] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_create.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-api#RULE-api-002`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-230 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 全部非终态占用户/租户共享份额；幂等重投不重复计数，队列满不建假Run；持久Run/事件后才返回202，指纹含tenant/endpoint/user/entry/conversation | tests/agent_runtime/test_capacity_webchat_admission_create.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_create.py"] | planned |
| harness-api#RULE-api-002 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次提交；请求指纹 = 规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256，**必须含 `endpoint` 与 `tenant_id` 两个判别键**，另含 actor/资源与关键参数（Runtime 另含 run_id/message_id）；同 key 同指纹重放首次持久化结果（SSE 按 submission 重放已落库事件、不重新执行），同 key 不同指纹返回 `IDEMPOTENCY_MISMATCH`；并发插入由 partial unique 兜底，落败者读取首次提交结果。真实并发控制是**先取 `pg_advisory_xact_lock`**（键由 `(tenant_id, idempotency_key, endpoint)` 派生）把同键请求串行化，partial unique 只作兜底（`channel_service.py:153-159`、`auth_service.py:270-276`、`audit_export_service.py:386-392`、`agent_service.py:159-162`、`mcp_service.py:240-243`、`skill_service.py:278-286`）。**存量待收敛**：console 侧 `agent_service`/`mcp_service`/`skill_service` 三处 `_fingerprint` 仍用 `&#124;` 拼接且不含 endpoint/tenant，须按本口径对齐（合规参考 `channel_service.py:59-74`、`auth_service.py:253-267`、`audit_export_service.py:100-119`、`apps/agent-runtime/src/muad_agent_runtime/application/run_submission.py:30-49`）。 Runtime 记忆命令使用永久回执 `runtime.memory_command_receipt`，完整唯一 `(tenant_id, command_id)` 且禁止软删；Scope 行锁后取同一 `(tenant_id, command_id)` 身份的事务 advisory lock，复查首次回执，完整唯一约束兜底。endpoint/user/actor/source_run/来源事件和规范化输入均进入指纹；同命令号跨 endpoint 或 user 返回 `IDEMPOTENCY_MISMATCH`，不新建第二份回执。回执、事实、完整历史、scope revision 同事务；批量清空只写一个回执。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_skill/test_import_idempotency.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-031: 终态释放、取消与排队期限竞态

- **Status**: draft
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

- [ ] [B-231][integration] 先在`tests/agent_runtime/test_capacity_webchat_admission_release.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：终态与admission_released_at CAS/计数释放同事务。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-231] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-231] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-05][integration] 将原B-05落入`tests/agent_runtime/test_capacity_webchat_admission_release.py`，先RED后GREEN；实际准入计数、Run 事务、取消/领取竞态和 PG 行锁；断言：幂等/回滚/双终态/接管后计数正确且不为负；每用户跨入口受同一上限；初始排队过期与领取只有一个结果，已开始执行不被旧排队期限误杀。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-231 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 终态与admission_released_at CAS/计数释放同事务；取消/超时/领取只有一个结果，不出现负数或超时后模型调用，已开跑不被旧排队期限误杀 | tests/agent_runtime/test_capacity_webchat_admission_release.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | planned |
| B-05 | integration | 实际准入计数、Run 事务、取消/领取竞态和 PG 行锁 | 幂等/回滚/双终态/接管后计数正确且不为负；每用户跨入口受同一上限；初始排队过期与领取只有一个结果，已开始执行不被旧排队期限误杀 | tests/agent_runtime/test_capacity_webchat_admission_release.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_admission_release.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-032: Runtime执行槽与独立监督

- **Status**: draft
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

- [ ] [B-232][integration] 先在`tests/agent_runtime/test_capacity_webchat_runtime_slots.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：本地有槽才PG claim，无界gather替换为有界监督。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-232] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-232] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_slots.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-232 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 本地有槽才PG claim，无界gather替换为有界监督；WAITING_TOOL/INPUT释放片段槽，心跳/回收/控制不排在长任务队尾，断流不取消执行 | tests/agent_runtime/test_capacity_webchat_runtime_slots.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_slots.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-033: Worker有界任务池及独立回收

- **Status**: draft
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

- [ ] [B-233][integration] 先在`tests/agent_worker/test_capacity_webchat_worker_slots.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：并发任务独立续租、无槽不领取。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-233] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-233] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_slots.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-worker#RULE-worker-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-233 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | 并发任务独立续租、无槽不领取；失约停止整组子进程、claim/reclaim不破坏既有父子锁序，投递/回收/调度独立，默认并发配置注入 | tests/agent_worker/test_capacity_webchat_worker_slots.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_slots.py"] | planned |
| harness-worker#RULE-worker-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py:20`、`worker/service.py:491`）。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-034: 外部副作用重试安全与结果待核实

- **Status**: draft
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

- [ ] [B-234][integration] 先在`tests/agent_worker/test_capacity_webchat_retry_safety.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：READ_ONLY/IDEMPOTENT/NON_RETRYABLE由可信配置冻结。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-234] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-234] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_retry_safety.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-234 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | READ_ONLY/IDEMPOTENT/NON_RETRYABLE由可信配置冻结；远端成功应答丢失复用业务键，未知非幂等效果不自动重做，MCP readOnlyHint不能单独授权重试 | tests/agent_worker/test_capacity_webchat_retry_safety.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_retry_safety.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-035: PG时钟、租约及绝对期限校验

- **Status**: draft
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

- [ ] [B-235][integration] 先在`tests/agent_runtime/test_capacity_webchat_clock_lease.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：Run/Task/模型租约与窗口使用PG时间。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-235] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-235] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_clock_lease.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-time#RULE-time-001`（stage: review；timeout=300秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-235 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | Run/Task/模型租约与窗口使用PG时间；本机时钟偏差不延长执行权，heartbeat与lease联动拒错，页面时间仍用IANA/DateTimeText口径 | tests/agent_runtime/test_capacity_webchat_clock_lease.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_clock_lease.py"] | planned |
| harness-time#RULE-time-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 数据库统一 `timestamptz` 存储；Console 展示统一 `YYYY-MM-DD HH:mm:ss`；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-036: 持久排队阶段与两入口状态投影

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006, TASK-025, TASK-032
- **Source**: agent-capacity-webchat.backend.design.md#3.2 架构与执行边界, agent-capacity-webchat.backend.design.md#3.4 接口设计
- **Spec-Refs**:
- **Acceptance-Refs**: B-236
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/execution_status.py`, `apps/agent-runtime/src/muad_agent_runtime/application/run_events.py`, `tests/agent_runtime/test_capacity_webchat_queue_projection.py`
- **Estimate**: 45分钟

### Description

WAITING_RUNTIME/WAITING_MODEL/工具阶段与时间原因先持久化；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-236][integration] 先在`tests/agent_runtime/test_capacity_webchat_queue_projection.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：WAITING_RUNTIME/WAITING_MODEL/工具阶段与时间原因先持久化。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-236] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-236] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_queue_projection.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-236 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | WAITING_RUNTIME/WAITING_MODEL/工具阶段与时间原因先持久化；恢复后状态真实，状态文案不变助手正文/下一轮历史，不显示假预计时间或名次 | tests/agent_runtime/test_capacity_webchat_queue_projection.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_queue_projection.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-037: 工具可信重试元数据冻结到操作策略

- **Status**: draft
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

- [ ] [B-237][integration] 先在`tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：ToolRegistry可信retry_safety随execution policy冻结，未知默认NON_RETRYABLE。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-237] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-237] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-mcp#RULE-mcp-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-237 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | ToolRegistry可信retry_safety随execution policy冻结，未知默认NON_RETRYABLE；恢复不重新tools/list或猜测readOnlyHint，Worker读取的业务幂等键与Run操作一致 | tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retry_policy_freeze.py"] | planned |
| harness-mcp#RULE-mcp-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_mcp/test_mcp_rules.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-038: 用户Token签发、重置与版本撤销

- **Status**: draft
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

- [ ] [B-238][integration] 先在`tests/console_auth/test_capacity_webchat_token_service.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：安全随机至少256位、仅摘要落库。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-238] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-238] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_service.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-238 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 安全随机至少256位、仅摘要落库；用户→Token锁序，PUT expected_revision单胜且原文一次显示；DELETE旧版本重放不撤销新Token，关联会话同事务撤销 | tests/console_auth/test_capacity_webchat_token_service.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_service.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-039: 用户详情Token管理的ADMIN端点

- **Status**: draft
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

- [ ] [B-239][integration] 先在`tests/console_auth/test_capacity_webchat_token_admin_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：API-03 ADMIN+CSRF、租户取当前管理员主体。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-239] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-239] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_admin_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-239 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | API-03 ADMIN+CSRF、租户取当前管理员主体；GET只元信息，首签/重置响应no-store且不入幂等回执，BUILDER/CHAT不能维护 | tests/console_auth/test_capacity_webchat_token_admin_api.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_admin_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-040: Token交换与独立CHAT会话生命周期

- **Status**: draft
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

- [ ] [B-240][integration] 先在`tests/console_auth/test_capacity_webchat_chat_session_service.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：验证来源Token/用户状态后生成独立会话摘要。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-240] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-240] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-08][integration] 将原B-08落入`tests/console_auth/test_capacity_webchat_chat_session_service.py`，先RED后GREEN；真实 PG Token/会话约束、有效期裁决与用户锁；断言：有效期必须正整数且有界；到期瞬间拒绝，Token最后1小时交换只能获≤1小时会话，读请求不滑动续期；同用户并发重置仅一个expected_revision成功，响应丢失重试不再次轮换或回放明文；旧版本撤销重放不影响后续新Token；摘要唯一且每用户至多一个未撤销Token。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-240 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 验证来源Token/用户状态后生成独立会话摘要；expires=min(PG now+12h,Token expires)，不滑动续期；Cookie到期重认同用户保留历史，logout只撤本会话 | tests/console_auth/test_capacity_webchat_chat_session_service.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | planned |
| B-08 | integration | 真实 PG Token/会话约束、有效期裁决与用户锁 | 有效期必须正整数且有界；到期瞬间拒绝，Token最后1小时交换只能获≤1小时会话，读请求不滑动续期；同用户并发重置仅一个expected_revision成功，响应丢失重试不再次轮换或回放明文；旧版本撤销重放不影响后续新Token；摘要唯一且每用户至多一个未撤销Token | tests/console_auth/test_capacity_webchat_chat_session_service.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_session_service.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-041: CHAT独立Cookie、CSRF与Origin校验

- **Status**: draft
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

- [ ] [B-241][integration] 先在`tests/console_auth/test_capacity_webchat_chat_cookie_security.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：CHAT Cookie名称与管理会话隔离，HttpOnly/Secure/SameSite及Max-Age同源。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-241] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-241] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_cookie_security.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-241 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | CHAT Cookie名称与管理会话隔离，HttpOnly/Secure/SameSite及Max-Age同源；交换POST验证Origin，写请求核独立CSRF，不把Console身份当PlatformUser | tests/console_auth/test_capacity_webchat_chat_cookie_security.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_cookie_security.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-042: CHAT exchange/me/logout公开认证路由

- **Status**: draft
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

- [ ] [B-242][integration] 先在`tests/console_auth/test_capacity_webchat_chat_auth_api.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：路由在管理登录守卫之外。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-242] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-242] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_auth_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-242 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 路由在管理登录守卫之外；exchange只接受Token，me返回统一principal/期限/auth_context，logout幂等；无管理角色继承、无JWT/provider配置 | tests/console_auth/test_capacity_webchat_chat_auth_api.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_chat_auth_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-043: 受信聊天身份核验与授权解析

- **Status**: draft
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

- [ ] [B-243][integration] 先在`tests/console_internal/test_capacity_webchat_chat_internal_principal.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：内部门控验证CHAT/来源Token/用户/部署租户，主体从PG记录导出。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-243] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-243] 执行`["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_chat_internal_principal.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-auth#RULE-auth-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-243 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 内部门控验证CHAT/来源Token/用户/部署租户，主体从PG记录导出；User→Agent和Skill/MCP叠加授权不变，伪造头或user-id不能认证 | tests/console_internal/test_capacity_webchat_chat_internal_principal.py | ["uv","run","pytest","-q","tests/console_internal/test_capacity_webchat_chat_internal_principal.py"] | planned |
| harness-auth#RULE-auth-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 授权为三层关系：User→Agent（AgentAccessGrant）、Agent→Skill/MCP（Binding）、SELECTED 资源再叠加 SkillUserGrant/McpUserGrant；Effective Capability 公式必须含 `is_deleted=false` 与资源/Agent `enabled`；绑定无启停开关、授权无到期时间；不建三元授权、不做 MCP Tool 级授权；未授权资源不得进入 Prompt/ToolRegistry/Skill Catalog。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/console_platform/test_user_side_relations.py -k s04 && uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-044: Gateway CHAT主体及长流复核

- **Status**: draft
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

- [ ] [B-244][integration] 先在`tests/gateway/test_capacity_webchat_gateway_chat_auth.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：Gateway只经Console核验，拒绝跨部署tenant。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-244] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-244] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_chat_auth.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-244 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | Gateway只经Console核验，拒绝跨部署tenant；所有读写/SSE/下载比较X-Chat-Context，空闲流也定时复核且到expires即停止，不每token查控制面 | tests/gateway/test_capacity_webchat_gateway_chat_auth.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_chat_auth.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-045: Token签发与交换全链路脱敏

- **Status**: draft
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

- [ ] [B-245][integration] 先在`tests/console_auth/test_capacity_webchat_token_redaction.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：既有token敏感键覆盖签发/交换请求和响应。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-245] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-245] 执行`["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_redaction.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-245 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 既有token敏感键覆盖签发/交换请求和响应；审计丢键/日志替换语义分别验证，canary反查Canonical/审计/快照/代理回执均无原文 | tests/console_auth/test_capacity_webchat_token_redaction.py | ["uv","run","pytest","-q","tests/console_auth/test_capacity_webchat_token_redaction.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-046: WEBCHAT入口控制面与快照类型

- **Status**: draft
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

- [ ] [B-246][integration] 先在`tests/console_platform/test_capacity_webchat_webchat_entry.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：WEBCHAT稳定入口无需企微secret且创建后不原地改Agent。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-246] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-246] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_webchat_entry.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-im#RULE-im-001`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/console_channel","tests/gateway"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-246 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | WEBCHAT稳定入口无需企微secret且创建后不原地改Agent；停用保留历史来源，Agent已有0..N账号能力保留，本轮企微按单机器人基线验收 | tests/console_platform/test_capacity_webchat_webchat_entry.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_webchat_entry.py"] | planned |
| harness-im#RULE-im-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/console_channel","tests/gateway"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-047: Gateway注册WEBCHAT标准通道适配器

- **Status**: draft
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

- [ ] [B-247][integration] 先在`tests/gateway/test_capacity_webchat_webchat_adapter.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：HTTP输入被规范化为统一事件并经唯一iter_events入口消费。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-247] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-247] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_adapter.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-247 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | HTTP输入被规范化为统一事件并经唯一iter_events入口消费；通道特有数据留Gateway，不能触发企微长连接/secret路径，缓存有界 | tests/gateway/test_capacity_webchat_webchat_adapter.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_adapter.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-048: Gateway受理与订阅解耦及精确来源路由

- **Status**: draft
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

- [ ] [B-248][integration] 先在`tests/gateway/test_capacity_webchat_gateway_admission.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：handler槽仅覆盖受理，长流不占handler。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-248] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-248] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_admission.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-im#RULE-im-002`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-248 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | handler槽仅覆盖受理，长流不占handler；HTTP重投沿用message/幂等身份，企微按原入口指针、Webchat显式会话，new/stop/确认不串Run | tests/gateway/test_capacity_webchat_gateway_admission.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_admission.py"] | planned |
| harness-im#RULE-im-002 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | **渠道差异只活在适配器层**：核心域（`agent-runtime` / `agent-worker`）零渠道专有字样与取件形状；`im-gateway` 除 `channels/` 与装配根 `main.py` 外同样为零。取件、下载、解密、媒体 URL/密钥**一律不出适配器**——上层只看到「已解密字节 + 元信息」。机检见 `tests/architecture/test_channel_neutrality.py`（三条：核心域全文零字样 / 核心域不得给 `ResolveDefinitionRequest.channel` 传字面量 / 全仓代码扫描只允许出现在 `ALLOWED_SURFACES`）。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/architecture/test_channel_neutrality.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-049: 显式会话创建、入口核属与标题

- **Status**: draft
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

- [ ] [B-249][integration] 先在`tests/agent_runtime/test_capacity_webchat_conversation_scope.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：复用原Conversation/Canonical/DB上下文。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-249] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-249] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_conversation_scope.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-memory#RULE-memory-004`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-249 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 复用原Conversation/Canonical/DB上下文；tenant/user/agent/entry同时核属，新建不取消旧Run，首条文本80字符标题无需模型；长期记忆继续tenant/user共享 | tests/agent_runtime/test_capacity_webchat_conversation_scope.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_conversation_scope.py"] | planned |
| harness-memory#RULE-memory-004 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 用户/管理员删除保留 key tombstone，递增 forget_epoch 并撤销旧提议；自动写不能解除此删除、人工 disabled 或把 USER/ADMIN 降级为 AGENT。EXPIRE/QUOTA/TASK_COMPLETE 自动退休仅接受真实新入站记录的 source_scope_revision 证明，不以 job 开始时间冒充新依据。自动批量提交核对原 scope revision，过期输入为 STALE；同任务 lineage 续接原稳定 key，完成后清除未决状态。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_job_effects.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-050: 历史列表、详情与固定水位分页

- **Status**: draft
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

- [ ] [B-250][integration] 先在`tests/agent_runtime/test_capacity_webchat_history_queries.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：列表按last_message_at/id且list/count同谓词同快照，批量活跃Run无N+1。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-250] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-250] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-04][integration] 将原B-04落入`tests/agent_runtime/test_capacity_webchat_history_queries.py`，先RED后GREEN；实际 Conversation/CanonicalEvent 查询与索引；断言：多页会话和超过200条消息按确定顺序读取；消息水位固定时无跳过/重复；列表 list/count 同条件、同快照，无逐会话 N+1；后台续租不改变会话最近活动排序。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-250 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 列表按last_message_at/id且list/count同谓词同快照，批量活跃Run无N+1；消息页through_seq固定、默认50上限100可超200总量；activeRun恢复不跳过正文 | tests/agent_runtime/test_capacity_webchat_history_queries.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | planned |
| B-04 | integration | 实际 Conversation/CanonicalEvent 查询与索引 | 多页会话和超过200条消息按确定顺序读取；消息水位固定时无跳过/重复；列表 list/count 同条件、同快照，无逐会话 N+1；后台续租不改变会话最近活动排序 | tests/agent_runtime/test_capacity_webchat_history_queries.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_history_queries.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-051: 历史和SSE共用安全公开投影

- **Status**: draft
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

- [ ] [B-251][integration] 先在`tests/agent_runtime/test_capacity_webchat_public_projection.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：USER/ASSISTANT/安全附件/确认白名单，工具参数/结果/SYSTEM/推理/未知事件默认不公开。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-251] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-251] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_public_projection.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-251 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | USER/ASSISTANT/安全附件/确认白名单，工具参数/结果/SYSTEM/推理/未知事件默认不公开；canonical seq合法跳号，不提供TRANSCRIPT下载或内部存储键 | tests/agent_runtime/test_capacity_webchat_public_projection.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_public_projection.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-052: Gateway入口、会话和消息HTTP接口

- **Status**: draft
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

- [ ] [B-252][integration] 先在`tests/gateway/test_capacity_webchat_webchat_resource_api.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：API-05/06/08/09/14/15完整转Owner。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-252] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-252] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_resource_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-252 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | API-05/06/08/09/14/15完整转Owner；核当前CHAT/授权/X-Chat-Context后才查幂等回执，返回真实202/201/忙碌状态，不读写DB | tests/gateway/test_capacity_webchat_webchat_resource_api.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_webchat_resource_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-053: 上传登记与Run独立不可变制品

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-013, TASK-049
- **Source**: agent-capacity-webchat.backend.design.md#3.3 数据设计, agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**: harness-skill#RULE-skill-001
- **Acceptance-Refs**: B-253, B-02
- **Files**: `apps/agent-runtime/src/muad_agent_runtime/application/channel_uploads.py`, `apps/agent-runtime/src/muad_agent_runtime/application/attachments/inbound.py`, `tests/agent_runtime/test_capacity_webchat_upload_staging.py`
- **Estimate**: 60分钟

### Description

暂存TTL/tenant/user/entry归属受控，受理创建Run自己的不可变storage_key；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-253][integration] 先在`tests/agent_runtime/test_capacity_webchat_upload_staging.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：暂存TTL/tenant/user/entry归属受控，受理创建Run自己的不可变storage_key。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-253] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-253] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-02][integration] 将原B-02落入`tests/agent_runtime/test_capacity_webchat_upload_staging.py`，先RED后GREEN；真实凭据/会话/附件归属存储；断言：未授权、过期、空正文、纯附件、大小/数量边界与现有附件门控一致。
- [ ] verifier `harness-skill#RULE-skill-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-253 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 暂存TTL/tenant/user/entry归属受控，受理创建Run自己的不可变storage_key；暂存或其他Run清理不删当前字节；幂等重放先查已受理结果再检查暂存TTL | tests/agent_runtime/test_capacity_webchat_upload_staging.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | planned |
| B-02 | integration | 真实凭据/会话/附件归属存储 | 未授权、过期、空正文、纯附件、大小/数量边界与现有附件门控一致 | tests/agent_runtime/test_capacity_webchat_upload_staging.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_staging.py"] | planned |
| harness-skill#RULE-skill-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml:7-8`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（默认 1h，保护进行中事务）。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_skill_artifact_cache.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-054: Webchat流式上传与动态限额

- **Status**: draft
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

- [ ] [B-254][integration] 先在`tests/gateway/test_capacity_webchat_upload_gateway.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：流式写共享字节后经Runtime登记元信息。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-254] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-254] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_upload_gateway.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-254 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 流式写共享字节后经Runtime登记元信息；MIME/字节/数量/软硬上限一致，返回upload_id而非伪artifact_id，超额在昂贵处理前拒绝 | tests/gateway/test_capacity_webchat_upload_gateway.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_upload_gateway.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-055: 附件下载授权与安全预览

- **Status**: draft
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

- [ ] [B-255][integration] 先在`tests/gateway/test_capacity_webchat_artifact_download.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：下载和图片预览复核会话/来源/当前授权，no-store/nosniff。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-255] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-255] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_artifact_download.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-255 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 下载和图片预览复核会话/来源/当前授权，no-store/nosniff；HTML/SVG按附件处理不执行，释放DB事务后流式输出，不凭客户端storage_key取件 | tests/gateway/test_capacity_webchat_artifact_download.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_artifact_download.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-056: 精确Run取消、待确认恢复和记忆回执

- **Status**: draft
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

- [ ] [B-256][integration] 先在`tests/agent_runtime/test_capacity_webchat_run_controls.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：cancel/resume绑定原Run/submission/source并在锁内复核WAITING_INPUT/deadline。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-256] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-256] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_run_controls.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-memory#RULE-memory-001`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。
- [ ] verifier `harness-memory#RULE-memory-002`（stage: review；timeout=600秒）：`["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-256 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | cancel/resume绑定原Run/submission/source并在锁内复核WAITING_INPUT/deadline；确认完整候选且source一次消费，记忆写遵守scope锁/配额/receipt/history/revision与忘记边界 | tests/agent_runtime/test_capacity_webchat_run_controls.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_run_controls.py"] | planned |
| harness-memory#RULE-memory-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 原话仅证明文本来源，不能授予保存授权；模型不得提供 author/explicit_request。自然语言 remember 必须展示完整候选，绑定租户/用户/Run/submission、候选 hash、预期版本、forget_epoch 和有效期，由真实用户确认原子保存；只有真实入站的显式命令或已授权管理员可以直接保存。候选每 Run 至多一个，工具整轮完成后才暂停；resume 先检查绝对 deadline。options 消费以租户和 source_event_id 唯一，结果 outcome 不进入唯一键。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_evidence.py","tests/agent_runtime/test_memory_redesign_confirmation.py"] | planned |
| harness-memory#RULE-memory-002 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | Runtime 是唯一记忆写入所有者；按 Conversation→Run→memory_scope 锁序串行修改，锁内禁止网络 IO。同 tenant/user/key 使用完整唯一身份，软删后 id/version 不重置；每次变更、完整 after 历史、幂等 receipt 和 scope revision 同事务提交。锁内按写后有效集合验证冻结条数/UTF-8 字节限额及绝对上限；仅可淘汰 AGENT，USER/ADMIN 不免除硬限；既有超额时治理可减少但不可增加。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_redesign_writes.py","tests/agent_runtime/test_memory_redesign_quotas.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-057: 聊天客户端非敏感配置白名单投影

- **Status**: draft
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

- [ ] [B-257][integration] 先在`tests/gateway/test_capacity_webchat_client_config_api.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：API-19只返回当前CHAT可用的settings/transport revision及分页/输入/上传/轮询/重连/帧限制。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-257] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-257] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_client_config_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-257 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | API-19只返回当前CHAT可用的settings/transport revision及分页/输入/上传/轮询/重连/帧限制；默认唯一、无池大小/内部URL/额度/secret，配置源坏时明确失败 | tests/gateway/test_capacity_webchat_client_config_api.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_client_config_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-058: Runtime暂存上传登记的受信端口

- **Status**: draft
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

- [ ] [B-258][integration] 先在`tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：Gateway共享字节写入后仅经服务门控登记upload_id。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-258] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-258] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-258 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | Gateway共享字节写入后仅经服务门控登记upload_id；核当前用户/入口/Agent与不可变存储键，重复登记/过期/越权明确，不允许任意路径或预造制品引用 | tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_upload_runtime_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-059: 模型增量有界合并与终态前flush

- **Status**: draft
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

- [ ] [B-259][integration] 先在`tests/agent_runtime/test_capacity_webchat_durable_deltas.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：时间或字节窗口到任一边界先提交再推送，终态前flush全部已确认正文。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-259] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-259] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_durable_deltas.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-259 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 时间或字节窗口到任一边界先提交再推送，终态前flush全部已确认正文；小增量不逐token事务，崩溃不丢已对用户确认的字节 | tests/agent_runtime/test_capacity_webchat_durable_deltas.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_durable_deltas.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-060: 共享事件追尾、提交后唤醒及补查

- **Status**: draft
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

- [ ] [B-260][integration] 先在`tests/agent_runtime/test_capacity_webchat_shared_tail.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：同Run/会话共享上游追尾，注册唤醒后读水位避免丢信。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-260] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-260] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-06][integration] 将原B-06落入`tests/agent_runtime/test_capacity_webchat_shared_tail.py`，先RED后GREEN；实际公开事件 DTO/水位和恢复服务；断言：过滤跳号、重复 seq、checkpoint、超前游标与已过期游标分别处理；重建快照不混 Run；心跳不推进游标，终态不能先于最后持久正文。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-260 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 同Run/会话共享上游追尾，注册唤醒后读水位避免丢信；Redis仅hint故障按PG有界补查，退出session才yield，心跳不推进业务游标 | tests/agent_runtime/test_capacity_webchat_shared_tail.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | planned |
| B-06 | integration | 实际公开事件 DTO/水位和恢复服务 | 过滤跳号、重复 seq、checkpoint、超前游标与已过期游标分别处理；重建快照不混 Run；心跳不推进游标，终态不能先于最后持久正文 | tests/agent_runtime/test_capacity_webchat_shared_tail.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_shared_tail.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-061: 公开SSE游标、分片与缓冲回压

- **Status**: draft
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

- [ ] [B-261][integration] 先在`tests/gateway/test_capacity_webchat_gateway_sse.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：CURSOR_EXPIRED/AHEAD分型，合法跳号不补拉循环。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-261] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-261] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_sse.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-261 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | CURSOR_EXPIRED/AHEAD分型，合法跳号不补拉循环；snapshot同seq分片完整后才应用；单订阅及进程总缓冲有界，慢消费者断开可恢复，不重建Run | tests/gateway/test_capacity_webchat_gateway_sse.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_sse.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-062: 部分正文失败后的模型重试边界

- **Status**: draft
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

- [ ] [B-262][integration] 先在`tests/agent_runtime/test_capacity_webchat_reply_generations.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：不同Key/attempt不能拼正文。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-262] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-262] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_reply_generations.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-262 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 不同Key/attempt不能拼正文；策略选择终止或新reply_generation持久snapshot替换，旧尝试输出被fence，已提交工具业务键不被重试更换 | tests/agent_runtime/test_capacity_webchat_reply_generations.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_reply_generations.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-063: Run完成和待确认的同事务用户通知

- **Status**: draft
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

- [ ] [B-263][integration] 先在`tests/agent_runtime/test_capacity_webchat_activity_write.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：终态/确认与feed seq/通知同事务，feed锁在Conversation/Run之后。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-263] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-263] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_write.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-263 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 终态/确认与feed seq/通知同事务，feed锁在Conversation/Run之后；重复投递不增加未读，隐藏会话仍可发现，不因socket写入就已读 | tests/agent_runtime/test_capacity_webchat_activity_write.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_write.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-064: 活动收件箱增量读取与独立已读

- **Status**: draft
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

- [ ] [B-264][integration] 先在`tests/agent_runtime/test_capacity_webchat_activity_read.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：按用户feed增量分页、显式read通知并提供受信Task结果入箱端口。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-264] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-264] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-03][integration] 将原B-03落入`tests/agent_runtime/test_capacity_webchat_activity_read.py`，先RED后GREEN；跨页读取和提交顺序、PG；断言：事件追尾与历史分页无跳过；读游标不能跨会话；通知 seq 按用户提交顺序推进。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-264 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 按用户feed增量分页、显式read通知并提供受信Task结果入箱端口；核真实Task/Run/用户/入口来源及授权，持久入箱后才回persisted，feed水位/投递状态/已读不混用 | tests/agent_runtime/test_capacity_webchat_activity_read.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | planned |
| B-03 | integration | 跨页读取和提交顺序、PG | 事件追尾与历史分页无跳过；读游标不能跨会话；通知 seq 按用户提交顺序推进 | tests/agent_runtime/test_capacity_webchat_activity_read.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_activity_read.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-065: 交付reserve/renew/ack持久协议

- **Status**: draft
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

- [ ] [B-265][integration] 先在`tests/agent_runtime/test_capacity_webchat_delivery_reservation.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：API-18按稳定delivery_key/attempt/epoch预留。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-265] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-265] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_reservation.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-265 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | API-18按稳定delivery_key/attempt/epoch预留；只有实际交付成功ack，已送达重放首次结果，存在占位不等于成功；网络在事务外 | tests/agent_runtime/test_capacity_webchat_delivery_reservation.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_reservation.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-066: Runtime独立可靠交付泵

- **Status**: draft
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

- [ ] [B-266][integration] 先在`tests/agent_runtime/test_capacity_webchat_delivery_pump.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：原入口PENDING意图按租约有界领取/续租/退避。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-266] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-266] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_pump.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-266 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 原入口PENDING意图按租约有界领取/续租/退避；Gateway重启或回复窗口过期后恢复交付，执行槽耗尽也可投递，不增加第五部署单元 | tests/agent_runtime/test_capacity_webchat_delivery_pump.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_delivery_pump.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-067: 机器人收尾与主动兜底统一去重

- **Status**: draft
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

- [ ] [B-267][integration] 先在`tests/gateway/test_capacity_webchat_gateway_delivery.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：活跃ReplySession和主动交付使用同delivery_key预留/确认。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-267] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-267] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_delivery.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-267 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 活跃ReplySession和主动交付使用同delivery_key预留/确认；路由保持原entry/Agent/user并重核接收权限，ACK丢失保留可追溯至少一次语义 | tests/gateway/test_capacity_webchat_gateway_delivery.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_delivery.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-068: Worker离线结果接入持久用户收件箱

- **Status**: draft
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

- [ ] [B-268][integration] 先在`tests/agent_worker/test_capacity_webchat_worker_notifications.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：任务结果由Runtime持久入箱确认后才完成投递。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-268] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-268] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_notifications.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-268 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | 任务结果由Runtime持久入箱确认后才完成投递；送达/已读分开，按原来源conversation定位；Worker重启/重投不重复通知或业务执行 | tests/agent_worker/test_capacity_webchat_worker_notifications.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_notifications.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-069: 历史、流增量、通知和制品清理保护

- **Status**: draft
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

- [ ] [B-269][integration] 先在`tests/agent_runtime/test_capacity_webchat_retention.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：保留期按清理边界取设置。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-269] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-269] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-07][integration] 将原B-07落入`tests/agent_runtime/test_capacity_webchat_retention.py`，先RED后GREEN；真实保留/清理查询、活跃 Run/job 与共享文件系统；断言：清理不删模型唯一摘要、尚被 Run/job 引用的事件/上传或已受理制品；无安全快照的活动流不能先淘汰重放数据；保留边界在 API 中明确可观测。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-269 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 保留期按清理边界取设置；不删唯一摘要/活跃Run-job引用/仍待投递回执，暂存与Run字节独立；游标过期明确、feed水位不重置，Token过期不删对话 | tests/agent_runtime/test_capacity_webchat_retention.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | planned |
| B-07 | integration | 真实保留/清理查询、活跃 Run/job 与共享文件系统 | 清理不删模型唯一摘要、尚被 Run/job 引用的事件/上传或已受理制品；无安全快照的活动流不能先淘汰重放数据；保留边界在 API 中明确可观测 | tests/agent_runtime/test_capacity_webchat_retention.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_retention.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-070: Gateway通知查询与显式已读接口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-044, TASK-052, TASK-061, TASK-064
- **Source**: agent-capacity-webchat.backend.design.md#3.4 接口设计, agent-capacity-webchat.backend.design.md#3.6 综合复核补充契约
- **Spec-Refs**:
- **Acceptance-Refs**: B-270
- **Files**: `apps/im-gateway/src/muad_im_gateway/api/webchat.py`, `apps/im-gateway/src/muad_im_gateway/application/webchat_service.py`, `tests/gateway/test_capacity_webchat_gateway_notification_api.py`
- **Estimate**: 45分钟

### Description

API-11转Runtime实际Owner端口，核CHAT/X-Chat-Context与来源授权；完整分支及可观测结果见下面验收契约。

### Checklist

- [ ] [B-270][integration] 先在`tests/gateway/test_capacity_webchat_gateway_notification_api.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：API-11转Runtime实际Owner端口，核CHAT/X-Chat-Context与来源授权。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-270] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-270] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_notification_api.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-270 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | API-11转Runtime实际Owner端口，核CHAT/X-Chat-Context与来源授权；安全摘要/稳定游标/明确read回执，不在Gateway内存中创建另一份通知权威 | tests/gateway/test_capacity_webchat_gateway_notification_api.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_notification_api.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-071: 共享传输区分CONSOLE/CHAT认证上下文

- **Status**: draft
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

- [ ] [B-271][integration] 先在`tests/frontend/test_capacity_webchat_chat_transport.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：共享层统一追踪/语言/CSRF/X-Chat-Context。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-271] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-271] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_transport.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-frontend#RULE-front-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-271 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 共享层统一追踪/语言/CSRF/X-Chat-Context；CHAT 401回Token输入且/me探测不弹启动Toast，管理登录行为保留，组件/hooks不裸fetch或覆盖头 | tests/frontend/test_capacity_webchat_chat_transport.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_transport.py"] | planned |
| harness-frontend#RULE-front-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 前端 HTTP 调用只经服务层：共享 axios 实例与拦截器在 `src/api/client.ts`（`src/api/` 只承载共享客户端与鉴权），各模块的 service 位于 `modules/<module>/services/*.ts` 并 import 该实例；组件与 hooks 禁止裸用 axios/fetch，也禁止拿 `api` 实例自建请求。**HTTP 调用面**之外的 `api/client` 导出不在此禁令内——`apiErrorBody`/`newRequestId` 是无副作用的纯工具，组件可直接 import（现网 7 处，均为纯函数引用）。所有文案只使用 i18n key（zh-CN/en-US）——`scripts/check_frontend_i18n.py` 校验两侧词条的键集齐平与空值，并校验源码中 `t('字面量键')` 引用的键**必须已定义**（增删词条后务必跑它）；模板串动态键变体的齐备性由各模块的 `tests/frontend/*_i18n*.py` 契约钉死。列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-072: 浏览器SSE与snapshot分片解析器

- **Status**: draft
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

- [ ] [B-272][integration] 先在`tests/frontend/test_capacity_webchat_browser_sse_parser.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：真实编译后的解析器经Node验证跨chunk UTF-8/CRLF/多行data/重复seq/合法跳号。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-272] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-272] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-101][integration] 将原B-F01落入`tests/frontend/test_capacity_webchat_browser_sse_parser.py`，先RED后GREEN；解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序；断言：解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序。
- [ ] [B-105][integration] 将原B-F05落入`tests/frontend/test_capacity_webchat_browser_sse_parser.py`，先RED后GREEN；共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文；断言：共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-272 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 真实编译后的解析器经Node验证跨chunk UTF-8/CRLF/多行data/重复seq/合法跳号；分片未齐不替换正文/推进游标，缓冲和单帧受有效配置约束 | tests/frontend/test_capacity_webchat_browser_sse_parser.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | planned |
| B-101 | integration | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 | 解析器覆盖跨 chunk UTF-8、CRLF、多行 data、心跳、重复seq与delta/snapshot顺序 | tests/frontend/test_capacity_webchat_browser_sse_parser.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | planned |
| B-105 | integration | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 | 共享流解析器、后端typed DTO和配置投影；检验字节单位/边界、配置更新/旧请求失效和snapshot分片；同canonical seq的part全部到齐才原子替换及推进游标，缺片/重复片/超额缓冲按安全游标恢复，不拼出半份正文 | tests/frontend/test_capacity_webchat_browser_sse_parser.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_browser_sse_parser.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-073: 模块WebchatClient、DTO与Owner接口服务

- **Status**: draft
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

- [ ] [B-273][integration] 先在`tests/frontend/test_capacity_webchat_webchat_services.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：所有HTTP经模块service与共享实例。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-273] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-273] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_services.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-273 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 所有HTTP经模块service与共享实例；方法覆盖认证/配置/入口/会话/消息/附件/通知/控制，提交必须显式conversationId，严格DTO及AbortSignal | tests/frontend/test_capacity_webchat_webchat_services.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_webchat_services.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-074: CHAT认证状态机与身份切换清理

- **Status**: draft
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

- [ ] [B-274][integration] 先在`tests/frontend/test_capacity_webchat_chat_auth_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：restore/显式exchange singleflight，退出/切主体停流并清消息/草稿/通知/Blob。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-274] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-274] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-104][integration] 将原B-F04落入`tests/frontend/test_capacity_webchat_chat_auth_state.py`，先RED后GREEN；X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界；断言：X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-274 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | restore/显式exchange singleflight，退出/切主体停流并清消息/草稿/通知/Blob；AUTH_CONTEXT_CHANGED停自动交换，Token不进reducer/持久缓存，无滑动续期 | tests/frontend/test_capacity_webchat_chat_auth_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | planned |
| B-104 | integration | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 | X-Chat-Context来自验证后的bootstrap，401不自动交换/重发POST，Token交换singleflight，AUTH_CONTEXT_CHANGED停自动重试；头像/附件请求经共享传输，身份变化撤销Blob URL，SSE退避和单帧大小有界 | tests/frontend/test_capacity_webchat_chat_auth_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_auth_state.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-075: 单个Token输入与过期重新进入

- **Status**: draft
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

- [ ] [B-275][integration] 先在`tests/frontend/test_capacity_webchat_token_access_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：无注册/密码/管理账号。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-275] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-275] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_token_access_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-275 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 无注册/密码/管理账号；遮蔽Token、提交防重复且完成清空；Cookie失效提示有效Token，无效Token联系管理员，不回显原文或把Token放URL | tests/frontend/test_capacity_webchat_token_access_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_token_access_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-076: 客户端配置加载与版本化消费

- **Status**: draft
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

- [ ] [B-276][integration] 先在`tests/frontend/test_capacity_webchat_client_config_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：认证后加载API-19再开放发送/上传/订阅。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-276] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-276] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_client_config_state.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-276 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 认证后加载API-19再开放发送/上传/订阅；失败独立重试不重交换，单位/边界由投影确定、不复制默认；新连接绑transportRevision，旧流参数不突变 | tests/frontend/test_capacity_webchat_client_config_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_client_config_state.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-077: 独立CHAT路由与页面编排

- **Status**: draft
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

- [ ] [B-277][integration] 先在`tests/frontend/test_capacity_webchat_chat_route.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：注册懒加载/chat/:entryId与conversation查询参数，位于管理RequireAuth/AppLayout之外。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-277] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-277] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_route.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-277 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 注册懒加载/chat/:entryId与conversation查询参数，位于管理RequireAuth/AppLayout之外；刷新URL恢复已授权所选会话，无id展示新对话空态，不增加菜单 | tests/frontend/test_capacity_webchat_chat_route.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_chat_route.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-078: 会话索引、选择、新建与迟到响应隔离

- **Status**: draft
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

- [ ] [B-278][integration] 先在`tests/frontend/test_capacity_webchat_conversation_state.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：URL/entry/conversation/generation固定请求归属。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-278] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-278] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-103][integration] 将原B-F03落入`tests/frontend/test_capacity_webchat_conversation_state.py`，先RED后GREEN；entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标；断言：entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-278 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | URL/entry/conversation/generation固定请求归属；新建幂等不取消旧Run，动态分页排序合并去重，旧响应不写新会话，草稿/游标按会话有界保存 | tests/frontend/test_capacity_webchat_conversation_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | planned |
| B-103 | integration | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 | entry/conversation/generation 变化时旧页、旧流和待发送草稿按会话隔离；列表分页合并去重，向前载入消息保留滚动锚点，不将历史水位误作活跃 Run 已应用游标 | tests/frontend/test_capacity_webchat_conversation_state.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_conversation_state.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-079: DeepSeek式历史侧栏与聊天工作区

- **Status**: draft
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

- [ ] [B-279][integration] 先在`tests/frontend/test_capacity_webchat_history_sidebar.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：新建/今天昨天更早分组、当前高亮及忙碌/未读真实展示。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-279] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-279] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_history_sidebar.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-279 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 新建/今天昨天更早分组、当前高亮及忙碌/未读真实展示；点击历史只切视图，窄屏抽屉可访问，桌面单会话工作区不混管理表格 | tests/frontend/test_capacity_webchat_history_sidebar.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_history_sidebar.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-080: 消息安全渲染、分页与滚动锚点

- **Status**: draft
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

- [ ] [B-280][integration] 先在`tests/frontend/test_capacity_webchat_message_history_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：首屏按配置、可加载超过200条保留消息，页水位不充当已渲染Run游标。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-280] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-280] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_message_history_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-280 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 首屏按配置、可加载超过200条保留消息，页水位不充当已渲染Run游标；USER/ASSISTANT/附件安全渲染，Markdown禁HTML/MDX，加载旧页保持锚点 | tests/frontend/test_capacity_webchat_message_history_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_message_history_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-081: 文字附件输入与消息幂等状态

- **Status**: draft
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

- [ ] [B-281][integration] 先在`tests/frontend/test_capacity_webchat_composer_uploads.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：文件用state，按UTF-8字节/数量/大小预检。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-281] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-281] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_composer_uploads.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-281 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 文件用state，按UTF-8字节/数量/大小预检；发送固定会话/输入/附件/幂等身份，202前待受理、响应未知不换消息号，过期暂存不破坏已受理重放 | tests/frontend/test_capacity_webchat_composer_uploads.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_composer_uploads.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

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

- **Status**: draft
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

- [ ] [B-285][integration] 先在`tests/frontend/test_capacity_webchat_user_token_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：仅ADMIN可见，生成/重置/撤销携版本并用ConfirmAction。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-285] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-285] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_user_token_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-ui-detail#RULE-ui-detail-001`（stage: review；timeout=600秒）：`["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-285 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 仅ADMIN可见，生成/重置/撤销携版本并用ConfirmAction；仅本次结果可显示/CopyButton复制Token、关闭即清，读取只状态及真实到期时间，响应未知不自动轮换 | tests/frontend/test_capacity_webchat_user_token_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_user_token_ui.py"] | planned |
| harness-ui-detail#RULE-ui-detail-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 详情使用 SideSheet：标题与副标题居左，关闭 X 同行靠右；列表对象级操作集中到主列表最后一列「操作」，详情不重复渲染，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。 | 沿用原Spec verifier，不重写成更弱测试 | ["bash","-lc","uv run pytest -q tests/frontend/test_detail_sidesheet_contract.py tests/frontend/test_form_layout_contract.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-086: 模型物理Key管理与检测界面

- **Status**: draft
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

- [ ] [B-286][integration] 先在`tests/frontend/test_capacity_webchat_credential_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：ADMIN凭据维护、容量/额度组及真实Key检测结果。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-286] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-286] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_credential_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-286 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | ADMIN凭据维护、容量/额度组及真实Key检测结果；读取只configured，空白Key保留、替换显式提交，单成员测试不显示整池健康 | tests/frontend/test_capacity_webchat_credential_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_credential_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-087: 模型成员绑定、权重与逻辑模型预算

- **Status**: draft
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

- [ ] [B-287][integration] 先在`tests/frontend/test_capacity_webchat_deployment_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：从已配置凭据单项绑定/解绑与权重版本修改，不重复填写Key/集合整体PUT。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-287] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-287] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_deployment_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-287 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 从已配置凭据单项绑定/解绑与权重版本修改，不重复填写Key/集合整体PUT；显示后续新Run生效及最大输出预算，使用共享详情组件 | tests/frontend/test_capacity_webchat_deployment_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_deployment_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-088: 额度组管理及共享预算说明

- **Status**: draft
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

- [ ] [B-288][integration] 先在`tests/frontend/test_capacity_webchat_quota_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：RPM/TPM/null/max_inflight单位与共享关系可见。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-288] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-288] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_quota_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-288 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | RPM/TPM/null/max_inflight单位与共享关系可见；降额/改归属需排空的冲突明确，非ADMIN无维护入口，删除不误清仍引用额度 | tests/frontend/test_capacity_webchat_quota_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_quota_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-089: 系统设置新增分组与字段错误定位

- **Status**: draft
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

- [ ] [B-289][integration] 先在`tests/frontend/test_capacity_webchat_settings_ui.py`建立行为验收并记录RED；真实边界：真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E；关键断言：容量/模型池/Webchat字段从groups元信息渲染默认/单位/范围/生效标记。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-289] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-289] 执行`["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_settings_ui.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-289 | integration | 真实模块service/typed DTO、共享传输与TypeScript编译产物；纯解析/状态经Node执行，DOM交互留真实浏览器E2E | 容量/模型池/Webchat字段从groups元信息渲染默认/单位/范围/生效标记；整份文档及revision保存、冲突不覆盖，不给启动连接池做运行时编辑框 | tests/frontend/test_capacity_webchat_settings_ui.py | ["uv","run","pytest","-q","tests/frontend/test_capacity_webchat_settings_ui.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

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

- **Status**: draft
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

- [ ] [B-294][integration] 先在`tests/agent_runtime/test_capacity_webchat_runtime_wiring.py`建立行为验收并记录RED；真实边界：真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约；关键断言：生产app注册历史/控制/通知/交付等实际端点、注入分池HTTP和监督预算。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-294] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-294] 执行`["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] [B-09][integration] 将原B-09落入`tests/agent_runtime/test_capacity_webchat_runtime_wiring.py`，先RED后GREEN；contracts配置解析、真实PG配置/快照、全局准入版本和启动settings；断言：默认值/单位/边界/派生值唯一；旧policy不补新默认，低版本不覆盖全局容量；请求限额/HTTP池/租约/重连/保留联动成立；配置网络读取在行锁短事务外，fixture不把样例参数当生产实测。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-294 | integration | 真实Runtime业务服务、隔离PostgreSQL/快照/事件与必要HTTP模型探针；不mock存储或租约 | 生产app注册历史/控制/通知/交付等实际端点、注入分池HTTP和监督预算；lifespan关闭所有泵/订阅/客户端，readiness与Redis可选退化一致 | tests/agent_runtime/test_capacity_webchat_runtime_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | planned |
| B-09 | integration | contracts配置解析、真实PG配置/快照、全局准入版本和启动settings | 默认值/单位/边界/派生值唯一；旧policy不补新默认，低版本不覆盖全局容量；请求限额/HTTP池/租约/重连/保留联动成立；配置网络读取在行锁短事务外，fixture不把样例参数当生产实测 | tests/agent_runtime/test_capacity_webchat_runtime_wiring.py | ["uv","run","pytest","-q","tests/agent_runtime/test_capacity_webchat_runtime_wiring.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-095: Worker装配模型外部调用预算与独立维护循环

- **Status**: draft
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

- [ ] [B-295][integration] 先在`tests/agent_worker/test_capacity_webchat_worker_wiring.py`建立行为验收并记录RED；真实边界：真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针；关键断言：真实装配根注入控制/MCP/业务/上传分池、并发和期限。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-295] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-295] 执行`["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-295 | integration | 真实Worker监督/claim、隔离PostgreSQL、实际子进程或外部HTTP协议探针 | 真实装配根注入控制/MCP/业务/上传分池、并发和期限；续租/回收/投递/调度互不阻塞，进程退出清子进程组，Task旧policy不补默认 | tests/agent_worker/test_capacity_webchat_worker_wiring.py | ["uv","run","pytest","-q","tests/agent_worker/test_capacity_webchat_worker_wiring.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-096: Gateway装配HTTP聊天、SSE及四类生命周期

- **Status**: draft
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

- [ ] [B-296][integration] 先在`tests/gateway/test_capacity_webchat_gateway_wiring.py`建立行为验收并记录RED；真实边界：真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库；关键断言：生产app实际注册Webchat/企微、身份复核和公开SSE，注入独立控制/stream/上传/业务预算。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-296] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-296] 执行`["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-296 | integration | 真实Gateway路由/适配器与HTTP/SSE协议探针；数据库经Owner接口访问，禁止Gateway持库 | 生产app实际注册Webchat/企微、身份复核和公开SSE，注入独立控制/stream/上传/业务预算；停服排空不重建Run，单机器人无连接重叠，无SQLAlchemy依赖 | tests/gateway/test_capacity_webchat_gateway_wiring.py | ["uv","run","pytest","-q","tests/gateway/test_capacity_webchat_gateway_wiring.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-097: Console装配CHAT路由及探针资源预算

- **Status**: draft
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

- [ ] [B-297][integration] 先在`tests/console_platform/test_capacity_webchat_console_wiring.py`建立行为验收并记录RED；真实边界：真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针；关键断言：认证端点不被Console管理会话守卫吞并、admin资源路由保持ADMIN。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-297] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-297] 执行`["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_wiring.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-297 | integration | 真实Console应用服务/HTTP依赖、独立PostgreSQL与事务；外部模型使用真实HTTP协议探针 | 认证端点不被Console管理会话守卫吞并、admin资源路由保持ADMIN；探针独立容量/HTTP客户端，设置损坏不锁死已认证CHAT，SPA不把API错误回退HTML | tests/console_platform/test_capacity_webchat_console_wiring.py | ["uv","run","pytest","-q","tests/console_platform/test_capacity_webchat_console_wiring.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-098: 模型、准入、流与交付指标目录

- **Status**: draft
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

- [ ] [B-298][integration] 先在`tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：无流量/metrics目录仍完整，队列/槽位/连接/缓冲/UNKNOWN/重连/交付指标可见。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-298] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-298] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py"]`并记录GREEN、关键断言位置和实际组件证据。
- [ ] verifier `harness-log#RULE-log-001`（stage: review；timeout=300秒）：`["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"]`；必须验证本条约束并填写真实证据，不因全量延后而免除责任。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-298 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 无流量/metrics目录仍完整，队列/槽位/连接/缓冲/UNKNOWN/重连/交付指标可见；label仅固定低基数枚举，UUID/消息/Key不入label或日志 | tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_gateway_metrics.py"] | planned |
| harness-log#RULE-log-001 | integration / E2E（review保留原verifier） | 对应Spec声明的真实API/PG/运行时/构建或浏览器；不能用替身覆盖完整verifier | 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出 JSON，自动携带 `trace_id/request_id/tenant_id`，并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。 | 沿用原Spec verifier，不重写成更弱测试 | ["uv","run","pytest","-q","tests/test_logging.py","tests/test_logging_redaction.py","tests/acceptance/test_foundation_logging.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-099: Worker与Console资源和认证观测

- **Status**: draft
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

- [ ] [B-299][integration] 先在`tests/architecture/test_capacity_webchat_worker_console_metrics.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：任务回收/投递归属正确，认证/探针/池等待与配置失败可区分，标准日志有tenant/trace关联但不含Token/Key，四单元catalog可独立读取。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-299] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-299] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_worker_console_metrics.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-299 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 任务回收/投递归属正确，认证/探针/池等待与配置失败可区分，标准日志有tenant/trace关联但不含Token/Key，四单元catalog可独立读取 | tests/architecture/test_capacity_webchat_worker_console_metrics.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_worker_console_metrics.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

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

- **Status**: draft
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

- [ ] [B-301][integration] 先在`tests/architecture/test_capacity_webchat_runtime_worker_deploy.py`建立行为验收并记录RED；真实边界：真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据；关键断言：资源requests/limits、进程/执行槽、共享RWX及独立本地cache明确。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-301] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-301] 执行`["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_worker_deploy.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-301 | integration | 真实源码依赖图、渲染后的部署清单及实际typed配置；不访问共享dev数据 | 资源requests/limits、进程/执行槽、共享RWX及独立本地cache明确；不按bot/user绑定Pod，最大副本下PG/HTTP/FD预算可计算 | tests/architecture/test_capacity_webchat_runtime_worker_deploy.py | ["uv","run","pytest","-q","tests/architecture/test_capacity_webchat_runtime_worker_deploy.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

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

- **Status**: draft
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

- [ ] [B-305][unit] 先在`tests/capacity/test_capacity_webchat_load_profile.py`建立行为验收并记录RED；真实边界：真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收；关键断言：users/负载分布/资源/SLO/模型模式typed校验，正式验收缺必填拒绝pass。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-305] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-305] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_profile.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-305 | unit | 真实typed schema/协议函数与完整输入边界；无网络和数据库替身来冒充跨服务验收 | users/负载分布/资源/SLO/模型模式typed校验，正式验收缺必填拒绝pass；1000用户不能由空闲连接代替，不在配置中嵌真实密钥 | tests/capacity/test_capacity_webchat_load_profile.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_load_profile.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

---

## TASK-106: 真实模型、企微与故障协议探针

- **Status**: draft
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

- [ ] [B-306][integration] 先在`tests/capacity/test_capacity_webchat_http_probes.py`建立行为验收并记录RED；真实边界：实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测；关键断言：HTTP模型可设置延迟/增量/usage/429/断流/应答丢失，企微走实际SDK协议探针。
- [ ] 实现Description中的全部分支与错误行为；函数≤50行、强类型、错误显式处理，凭据/原始内部内容不得进入公开响应或日志。
- [ ] [B-306] 按下方契约逐项验证成功、边界及失败分支，登记每个关键断言的位置。
- [ ] [B-306] 执行`["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_http_probes.py"]`并记录GREEN、关键断言位置和实际组件证据。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-306 | integration | 实际容量harness/profile、HTTP协议探针/子进程与隔离栈；按任务范围验证真实边界，不用伪造成功回执代替实测 | HTTP模型可设置延迟/增量/usage/429/断流/应答丢失，企微走实际SDK协议探针；所有错误注入可观测，不直接mock服务方法或PG | tests/capacity/test_capacity_webchat_http_probes.py | ["uv","run","pytest","-q","tests/capacity/test_capacity_webchat_http_probes.py"] | planned |

### Acceptance Evidence

> 尚未编码或运行。本任务开始后记录functional的RED/GREEN、命令/退出码、断言位置和真实边界；E2E保持planned，最终verify-e2e记录完整证据。required verifier由本任务负责，review前不得声称已通过。

### Log

- [2026-10-16] created（review-draft；尚未启动）
- [2026-10-17] 用户确认写入（draft；尚未启动）

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
