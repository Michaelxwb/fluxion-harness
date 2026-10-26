---
id: high-qps-remediation-scenarios
description: 高 QPS 架构整改验收场景唯一源
stages:
- design
enforcement: advisory
schema: 1
requirement: high-qps-remediation
scenarios:
- id: S-01
  title: 1000 用户真实流式对话在目标拓扑内达到 SLO（capacity_load）
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-012
  target_phase: release
  defer_reason: 1000×8 需目标拓扑与模型额度，只在 staging 执行（E-09）；本地回归守卫为 S-16（闸门容量 smoke）
  env: local-api
  components:
  - backend
  - capacity
  - postgres
  - redis
  boundary: 真实入口代理 → Gateway → Runtime → PostgreSQL/Redis；模型为可控 HTTP 流式探针；通知轮询为浏览器同形请求
  preconditions:
  - 1 个 Console、N 个 Gateway（多副本）、企微场景下 1+ Connector、Runtime/Worker 按目标副本
  - load profile 声明 1000 users、8 turns/600s、20 chunks/s × 10s、通知轮询 15s
  - 模型额度/执行槽总量足以承接目标并发
  steps:
  - 启动四/五单元真实栈与目标 profile
  - 1000 个用户各建立 CHAT 会话并完成 8 轮提交/SSE/通知轮询
  - 校验 attempts == users × turns（8000）且 steady 窗口 600s 真正约束新提交
  - 采集 admission、first-delta、错误率、CPU、PG total/idle-in-tx/commits/s、WAL/s、retention
  expected:
  - admission P95 ≤ 500ms、P99 ≤ 1500ms；first-delta P95 ≤ 1200ms；系统错误率 < 1%
  - attempts == 8000；steady_sec 约束生效；Gateway/Console CPU < 60% limit
  - PG total < 200；稳态 idle-in-tx ≈ 0；commits/s ≤ 4k（Phase 2）/ ≤2k（Phase 3）
  - retention 删除速率 ≥ 写入速率；无 pool timeout、无 SSE 拒绝/缺终帧
  expected_refs:
  - expected: 1
    steps:
    - 4
  - expected: 2
    steps:
    - 3
  - expected: 3
    steps:
    - 4
  - expected: 4
    steps:
    - 4
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §3.5
  - backend design §2.5.3
  rules:
  - RULE-01
  - RULE-02
  - RULE-03
  - RULE-08
  - RULE-11
  checks: []
- id: S-02
  title: 1000 用户空闲通知轮询不再放大 Console
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-002
  target_phase: review
  defer_reason: 1000 会话空闲轮询需容量档环境；功能已由 10 用户自检覆盖，需求级终验复查
  env: local-api
  components:
  - backend
  - frontend
  - console
  - postgres
  boundary: 浏览器同形通知轮询 → Gateway → Console auth / Runtime notifications；真实 PG/Redis
  preconditions:
  - 1000 个已认证 CHAT 会话
  - 前端请求带 page_size；轮询间隔 15s（可配置）
  steps:
  - 保持 1000 个会话在线但不发消息
  - 按 15s ±20% 抖动轮询 /notifications
  - 采集 Console 内部调用、SQL 数、CPU、settings 读取次数
  expected:
  - 通知轮询不再触发 Console platform-settings 读取
  - Console settings 构造数 = 0；chat-principal ≤3 SQL；Console CPU < 60%
  - 无 pool timeout、无认证错误
  expected_refs:
  - expected: 1
    steps:
    - 3
  - expected: 2
    steps:
    - 3
  - expected: 3
    steps:
    - 3
  trigger_paths:
  - apps/console-platform/frontend/src/modules/webchat/hooks/useNotifications.ts
  - apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py
  - apps/console-platform/backend/src/muad_console_platform/**
  - apps/im-gateway/src/muad_im_gateway/**
  smoke: false
  design_refs:
  - backend design §3.5
  - frontend design §3.5
  rules:
  - RULE-03
  - RULE-11
  checks: []
- id: S-03
  title: 小连接池下 100 条 SSE 不随流数线性占用 PG 连接
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - postgres
  - redis
  boundary: 真实 Gateway/Runtime HTTP SSE + 真实 PostgreSQL；runtime DB pool=2
  preconditions:
  - runtime `DB_POOL_SIZE=2`、`DB_MAX_OVERFLOW=0`
  - 100 条并发 /v1/runs/{id}/events
  steps:
  - 创建 100 个 Run（或复用已持久 Run）
  - 同时打开 100 条 SSE 并保持 30s
  - 采样 pg_stat_activity total/idle-in-transaction 与 runtime pool checked out
  expected:
  - 100 条流全部成功打开；无 QueuePool timeout
  - pool=2 时 100 条流 PG total ≤ 20（pool×进程 + 余量），不随流数线性增长
  - 流结束后连接归还
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §3.5
  rules:
  - RULE-01
  - RULE-08
  checks: []
- id: S-04
  title: 批量事件写保持顺序、终态与重放正确
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - postgres
  - redis
  boundary: 真实 Runtime 事件 writer + 真实 PostgreSQL/Redis；20 delta/s × 10s
  preconditions:
  - 100 个并发 Run，每 Run 20 delta/s，首段立即 flush
  - 事件批量窗口 250–500ms/8–16KB
  steps:
  - 运行 100 个流式 Run 并记录 commits/s、statements/commit、seq
  - 在流中断言已提交前缀可重放、无重复 seq
  - SIGKILL 一个 Runtime，验证已提交前缀与终态 CAS
  expected:
  - ≤4 commits/s/Run；≤3 statements/event；无丢/重 seq；terminal 最后
  - 重放文本与终态一致；SIGKILL 后无半行
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.3 / §3.5
  rules:
  - RULE-02
  - RULE-09
  checks: []
- id: S-05
  title: 同一 bot 只有一个 Connector owner（假 WS），故障可接管且不重复发送
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-007
  target_phase: review
  defer_reason: 两个真实 Connector + 假 WS 的 lease 接管用例留需求级 verify-e2e 执行
  env: local-api
  components:
  - backend
  - connector
  - redis
  - postgres
  boundary: 两个真实 Connector 进程 + 假企微 WS 服务端 + 真实 Redis lease；Runtime/Gateway 真实进程
  preconditions:
  - 同一 bot 配置给两个 Connector 候选
  - Redis lease TTL 已知
  steps:
  - 同时启动两个 Connector，观察 WS 连接数与 lease owner
  - 杀掉 owner，等待 lease 过期/接管
  - 在切换窗口发送入站消息与出站回复/产物
  expected:
  - 任意时刻只有一个 active owner；旧 owner 命令被 epoch 拒绝
  - 接管后新消息可处理；未恢复的 reply_ref 走主动发送 + DEGRADED
  - 无重复入站 Run、无消息发到错误回调
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
    - 3
  - expected: 3
    steps:
    - 3
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §3.5
  rules:
  - RULE-04
  - RULE-07
  checks: []
- id: S-06
  title: 多 Gateway 副本下 WebChat 提交/SSE/重连任意 Pod 可用
  priority: P0
  level: E2E
  exec: deferred
  owner: TASK-009
  target_phase: review
  defer_reason: 浏览器多副本断线重连 E2E 在 verify-e2e 统一执行
  env: browser
  components:
  - frontend
  - backend
  - gateway
  - runtime
  boundary: 真实浏览器/HTTP 客户端 → 两个以上 Gateway 副本 → Runtime/PG/Redis；SSE 断线重连到另一副本
  preconditions:
  - Gateway ≥2 副本，无 sticky session
  - 同一 CHAT 会话有活跃 Run
  steps:
  - 在 Pod A 提交消息并打开 SSE
  - 断开后带 after_seq 重连到 Pod B
  - 用同 message_id 重试提交；重启一个 Gateway Pod
  - 再提交下一条消息并观察恢复
  expected:
  - 任意 Pod 可服务；断线重连从游标续上，无缺口/重复
  - 同 message_id 重试只建一个 Run
  - Gateway Pod 重启不影响已持久 Run；重连后继续
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 3
  - expected: 3
    steps:
    - 3
    - 4
  why_e2e: SSE 长连接、负载均衡、断线重连和跨 Pod 游标只有真实浏览器 + 多副本 + Runtime 持久流才能覆盖。
  trigger_paths:
  - apps/im-gateway/**
  - apps/agent-runtime/src/muad_agent_runtime/api/runs.py
  - apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py
  - deploy/k8s/base/im-gateway.yaml
  smoke: true
  design_refs:
  - backend design §3.2 / §3.4
  - frontend design §3.5
  rules:
  - RULE-05
  - RULE-06
  - RULE-08
  checks: []
- id: S-07
  title: 新增 Webhook 通道只加 adapter，不新增微服务
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - gateway
  - runtime
  - redis
  boundary: 真实 Gateway HTTP /channels/{channel}/events + 假 webhook 提供方 + Redis Stream
    + Runtime
  preconditions:
  - 一个 fake webhook channel adapter
  - 签名/token 校验配置
  steps:
  - 发送合法 webhook，验证快速 2xx 与入站事件
  - 发送错误签名，验证拒绝且无入站事件
  - 重复 event_id，验证只建一个 Run
  expected:
  - 不新增 Deployment/镜像；adapter 包 + 路由即可接入
  - 合法事件先持久化再 ACK；非法签名不落库
  - 重复 event_id 幂等
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §3.4
  rules:
  - RULE-06
  - RULE-10
  checks: []
- id: S-08
  title: WebChat 走统一核心 SYNC 受理，不重复 Console 解析
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - gateway
  - console
  - runtime
  - postgres
  boundary: 真实 Gateway → 统一消息核心 → Console/Runtime/PG；已验证 CHAT 主体上下文
  preconditions:
  - 已认证 ChatIdentity
  - 同一 message_id 重试
  steps:
  - 提交消息并记录 Console chat-principal 调用次数
  - 用同一 message_id 重试
  - 校验 Run 数、202 延迟、SSE 可用
  expected:
  - 只建一个 Run；202 P95 ≤ 500ms（与受理 SLO 同口径）
  - 不再为已解析主体重复调用 Console resolve
  - 既有 API 封套/错误码不变
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
    - 3
  - expected: 2
    steps:
    - 1
    - 2
  - expected: 3
    steps:
    - 1
    - 2
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §3.4
  rules:
  - RULE-06
  - RULE-03
  checks: []
- id: S-09
  title: 前端通知请求带 page_size，轮询间隔可配置
  priority: P1
  level: component
  exec: auto
  env: none
  components:
  - frontend
  boundary: 前端 service/hook + mock HTTP；不访问真实后端
  preconditions:
  - client-config 已返回 history_page_size/notification_poll_sec
  steps:
  - 触发一次通知同步
  - 捕获请求 query 与下一次定时器延迟
  expected:
  - 请求包含 page_size，值来自投影
  - 轮询间隔来自配置（默认推荐 15s），带抖动
  - 组件/hook 不裸用 fetch/axios
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    assertions:
    - no_bare_fetch_axios
  assertions:
  - no_bare_fetch_axios
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - frontend design §3.4 / §3.5
  rules:
  - RULE-03
  checks: []
- id: S-10
  title: 聊天首屏 code splitting 与 admin 深链
  priority: P1
  level: component
  exec: auto
  env: none
  components:
  - frontend
  boundary: 构建产物 + 前端路由（mock API）
  preconditions:
  - admin 路由已 lazy
  - 保留深链
  steps:
  - 构建并检查 chat 入口 chunk
  - 打开 /chat 断言不加载 admin chunk
  - 直接打开 admin 深链断言可加载
  expected:
  - 聊天首屏不加载 12 个 admin 页面 chunk
  - admin 深链可用、错误边界正常
  - 菜单 11 项不变
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - frontend design §3.2 / §3.3
  rules:
  - RULE-10
  checks: []
- id: S-11
  title: 统一消息契约 round-trip：InboundEvent/DeliveryCommand/Capabilities/Lease
  priority: P0
  level: contract
  exec: auto
  env: none
  components:
  - contracts
  - im-core
  boundary: 纯契约/序列化；不连服务
  preconditions:
  - 契约已定义
  steps:
  - 构造四类契约并 JSON round-trip
  - 校验 extra=forbid/必填/枚举
  - 校验 shard/route key 稳定性
  expected:
  - 字段/枚举/未知字段拒绝符合契约
  - 同输入 shard 稳定
  - 旧 WebChat/IM 契约不破坏
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.4.2
  rules:
  - RULE-06
  - RULE-10
  checks: []
- id: S-12
  title: Connector/Gateway 边界与预检：独立镜像、无公开路由、无 WeCom SDK、ChannelName 开放
  priority: P0
  level: integration
  exec: auto
  env: none
  components:
  - backend
  - connector
  - deploy
  boundary: 真实包导入/清单渲染/architecture tests；不启动业务栈
  preconditions:
  - connector 包/镜像/清单存在
  steps:
  - 检查 gateway 依赖不含 aibot
  - 检查 connector 不暴露公开 HTTP 路由
  - 检查 bot snapshot 按 channel 过滤
  - 检查 ChannelName/registry 可扩展
  - 运行 5 单元预检
  expected:
  - Gateway 无 WeCom SDK；Connector 无 WebChat 公开路由
  - Gateway 响应不含 WECOM secret
  - 新增 fake channel 不改 ChannelName 字面量
  - 预检在 5 单元/多 worker 下通过
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 3
  - expected: 3
    steps:
    - 4
  - expected: 4
    steps:
    - 5
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §4.1
  rules:
  - RULE-05
  - RULE-07
  - RULE-10
  checks: []
- id: S-13
  title: Runtime 队列指标/HPA、history 移出受理、memory 背压
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - runtime
  - worker
  - postgres
  boundary: 真实 Runtime/Worker/PG + 模拟队列压力
  preconditions:
  - 队列 gauge 已发布
  - history 后台化
  - memory 入队限速
  steps:
  - 制造等待队列，采样 waiting/oldest age
  - 提交消息，断言 202 不等待 history
  - 制造 memory job backlog，观察限速
  expected:
  - HPA 可读队列指标；队列年龄可断言
  - 202 不因 history 扫描变慢
  - memory backlog 有界，不拖慢聊天
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-08
  - RULE-11
  checks: []
- id: S-14
  title: Console admission-materials 去重与通知 fast path
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - console
  - runtime
  boundary: 真实 Console/Runtime/PG；记录 SQL/设置读取
  preconditions:
  - materials 合并解析
  - 通知 fast path
  steps:
  - 一次受理采样 model/credential 查询次数
  - 空闲通知轮询采样 count/page/unread 次数
  - 故障注入 settings 源
  expected:
  - materials 不重复解析 model/credential
  - 空闲轮询不再 count/page/unread 三条全跑
  - settings 故障不阻断已带 page_size 的轮询
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-03
  checks: []
- id: S-15
  title: WebChat 读路径 lean scope、202 不 get_run、dead queue 清理
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - gateway
  - runtime
  - postgres
  boundary: 真实 Gateway/Runtime/PG；SQL 计数
  preconditions:
  - lean scope 已实现
  - 受理帧带 status
  steps:
  - 打开 SSE 采样 scope SQL 数
  - 提交消息采样 202 后 get_run 次数
  - 检查 WebchatChannelAdapter 无 dead queue 或已接入
  expected:
  - scope ≤2 SQL
  - 202 后不再 get_run
  - 无 dead queue 假象
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.4 / §3.5
  rules:
  - RULE-06
  - RULE-11
  checks: []
- id: S-16
  title: 100 用户 capacity smoke（PR 级）
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-001
  target_phase: review
  defer_reason: 100 用户 smoke 需真实四单元栈；编码期只登记，verify-e2e 执行
  env: local-api
  components:
  - backend
  - capacity
  - postgres
  - redis
  boundary: 真实栈 + 100 用户 × 1 轮 × 20 chunks/s + 通知轮询
  preconditions:
  - capacity_smoke profile
  steps:
  - 运行 smoke profile
  - 采样 admission/first-delta/错误/PG/CPU
  expected:
  - admission P95 ≤1000ms、first-delta P95 ≤2500ms
  - 无 5xx/SSE 打开失败
  - PG total ≤120、idle-in-tx 峰值 ≤20
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  - expected: 3
    steps:
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §2.5.4
  rules:
  - RULE-08
  - RULE-11
  checks: []
- id: S-17
  title: 200 用户 bounded verification（nightly）
  priority: P1
  level: integration
  exec: deferred
  owner: TASK-001
  target_phase: review
  defer_reason: 200 用户 bounded verification 属 nightly 档负载
  env: local-api
  components:
  - backend
  - capacity
  boundary: 真实栈 + 200 用户 × 1 轮
  preconditions:
  - make capacity-load
  steps:
  - 运行 200 档
  - 采样 SLO/PG
  expected:
  - 按 profile SLO 判定；不得冒充 1000 结论
  - 无 pool timeout
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §2.5.4
  rules:
  - RULE-08
  - RULE-11
  checks: []
- id: S-18
  title: 400 用户 30–60 分钟 soak
  priority: P1
  level: integration
  exec: deferred
  owner: TASK-001
  target_phase: review
  defer_reason: 400 用户 30–60 分钟 soak 需显式门禁 MUAD_CAPACITY_SOAK=1
  env: local-api
  components:
  - backend
  - capacity
  boundary: 真实栈 + 400 用户 + 通知轮询，30–60 分钟
  preconditions:
  - soak 脚本/profile
  steps:
  - 持续 30–60 分钟
  - 采样内存/连接/WAL/retention
  expected:
  - 无连接/内存泄漏
  - PG/Redis 稳定；retention 追写入
  - 错误率 <1%
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 1
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §2.5.4
  rules:
  - RULE-08
  - RULE-09
  - RULE-11
  checks: []
- id: E-01
  title: 设置源不可用时空闲通知轮询仍可工作
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - gateway
  - runtime
  - console
  boundary: 真实 Gateway/Runtime + Console auth；模拟 platform-settings 不可用
  preconditions:
  - 通知请求已带 page_size
  - Console settings 端点故障
  steps:
  - 关闭/打桩 Console platform-settings
  - 发起通知轮询
  - 模拟 CHAT 凭据失效，断言 401/403/409 既有语义
  expected:
  - 认证正常时通知轮询不再依赖 settings；无 503
  - 若认证失败，返回既有 401/403/409 语义，不吞错
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-03
  checks: []
- id: E-02
  title: 连接池耗尽时 SSE 打开快速失败而不是挂死
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - gateway
  - runtime
  - postgres
  boundary: 真实 Runtime + PostgreSQL；pool=1，打开 10 条 SSE
  preconditions:
  - runtime pool=1/overflow=0/timeout=1s
  - 已有长流占用连接
  steps:
  - 打开超过池容量的 SSE
  - 采样响应时间/错误码/池等待
  expected:
  - 超出容量的请求在 1s 内显式失败（429/503/稳定错误码），不挂 5s
  - 不出现未处理的 QueuePool 异常；指标可见
  - 修后同场景 100 条流不失败
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    assertions:
    - post_fix_hundred_streams
  assertions:
  - post_fix_hundred_streams
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-01
  - RULE-08
  checks: []
- id: E-03
  title: Connector 在入站持久化前崩溃：平台重投后只建一个 Run
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-007
  target_phase: review
  defer_reason: Connector 崩溃 + 平台重投需真实进程与故障注入，留 verify-e2e
  env: local-api
  components:
  - backend
  - connector
  - redis
  boundary: 真实 Connector 进程 + Redis Stream；kill -9 在 WS 收到后、XADD 前
  preconditions:
  - 假 WeCom 服务端可重投/可观测
  - Redis Stream AOF/重投策略已配置
  steps:
  - 注入崩溃点（收到消息后、XADD 前）
  - 重启 Connector/等待平台重投
  expected:
  - 平台重投后消息最终恰好一个 Run（message_id 幂等）
  - 无重复用户可见回复
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-04
  - RULE-08
  checks: []
- id: E-04
  title: reply_ref 过期/epoch 不匹配时降级主动发送，不串消息
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - connector
  - redis
  - fake-wecom
  boundary: 真实 Connector + 假 WS 服务端；旧 reply_ref 过期后发送终态/产物
  preconditions:
  - 旧连接 epoch 已失效
  - run_id→reply_ref 过期/不匹配
  steps:
  - 触发终态/产物投递
  - 观察发送命令与审计结果
  expected:
  - 走主动发送（send_text/send_media），审计 DEGRADED
  - 不把消息挂到本路由最新回调/另一条消息
  - 用户可见内容不丢
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  - expected: 3
    steps:
    - 1
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-04
  - RULE-07
  checks: []
- id: E-05
  title: Redis Stream 不可用时降级为 PG 有界补查且可观测
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - runtime
  - redis
  - postgres
  boundary: 真实 Runtime + Redis 停机 + PG 持久事件
  preconditions:
  - 有已持久 Run 事件
  - Redis 关闭
  steps:
  - 关闭 Redis
  - 打开/保持 SSE，发送新事件
  - 恢复 Redis，断言回切且无重复/缺口
  expected:
  - 退化为 PG 有界轮询，事件不丢/不重
  - 降级指标/日志可见；恢复后自动回切
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 2
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-02
  - RULE-08
  checks: []
- id: E-06
  title: 真机企微探针：同 bot 双 WS 与 req_id 跨重连语义
  priority: P0
  level: manual
  exec: manual
  env: staging
  components:
  - connector
  - wecom
  boundary: 真实企微智能机器人 + 两个 Connector 候选 + 真实网络；不是假 WS
  preconditions:
  - 可用企微测试 bot/secret
  - 两个 Connector 进程
  - 可观察服务端行为
  steps:
  - 同一 bot 同时建立两个 WS，记录踢旧/分发/拒绝
  - 断开后重连，尝试用旧 req_id 继续回复
  - 验证不能继续回复时的主动发送降级
  expected:
  - 明确记录企微单/多连接行为
  - 明确记录 req_id 跨重连寿命
  - 降级行为符合产品预期，不串消息
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.2 / §3.5
  rules:
  - RULE-04
  - RULE-07
  checks: []
- id: E-07
  title: Connector 在入站持久化前崩溃：平台不重投时显式失败
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-007
  target_phase: review
  defer_reason: Connector 崩溃 + 平台不重投需故障注入，留 verify-e2e
  env: local-api
  components:
  - connector
  - redis
  boundary: 真实 Connector 进程 + Redis Stream；关闭平台重投
  preconditions:
  - 假 WS 服务端不重投
  - 崩溃点可注入
  steps:
  - 在收到消息后、XADD 前 kill -9
  - 等待/确认无重投
  - 检查用户可见状态与告警
  expected:
  - 显式 FAILED/告警，不假装成功
  - 文档写明 at-least-once 边界，不承诺 exactly-once
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    assertions:
    - documented_at_least_once
  assertions:
  - documented_at_least_once
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-04
  - RULE-08
  checks: []
- id: E-08
  title: PG failover / Redis 抖动 / Pod kill 混沌
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-012
  target_phase: review
  defer_reason: PG failover/Redis 抖动/Pod kill 混沌需可注入故障的环境
  env: local-api
  components:
  - backend
  - postgres
  - redis
  - gateway
  - runtime
  boundary: 真实进程 + PG/Redis 故障注入
  preconditions:
  - 可注入故障
  - 已持久 Run
  steps:
  - PG failover；Redis 抖动；kill Gateway/Runtime Pod
  - 观察重连/恢复/重复
  expected:
  - 已受理 Run 不丢；SSE 可恢复
  - 无重复终态/投递；降级指标可见
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-02
  - RULE-04
  - RULE-05
  - RULE-08
  checks: []
- id: E-09
  title: 1000 用户 × 8 轮 manual/staging release gate
  priority: P0
  level: manual
  exec: manual
  env: staging
  components:
  - backend
  - capacity
  boundary: staging 目标拓扑；人工执行并记录证据
  preconditions:
  - 目标拓扑/模型额度/观测就绪
  steps:
  - 执行 1000×8 全量 capacity_load
  - 记录 SLO/PG/CPU/retention 证据
  expected:
  - SLO 全过；PG/CPU/retention 预算内
  - 未过不得写生产达标
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §2.5.4
  rules:
  - RULE-08
  - RULE-11
  checks: []
- id: B-01
  title: 1000 个受理请求瞬时到达时有界排队/显式拒绝（capacity_load）
  priority: P0
  level: integration
  exec: deferred
  owner: TASK-005
  target_phase: review
  defer_reason: 1000 瞬时受理 burst 需容量档环境
  env: local-api
  components:
  - backend
  - gateway
  - runtime
  - postgres
  boundary: 真实 Gateway/Runtime/PG；ramp=0，1000 个不同用户同时提交
  preconditions:
  - 目标执行槽/模型额度有限
  - 队列/准入预算已配置
  steps:
  - 同时提交 1000 个 Run
  - 采样排队、429/拒绝、内存、延迟
  - 对已受理 Run 执行查询与取消；恢复后观察排空
  expected:
  - 不 OOM、不无限 create_task；超出预算显式 429/CHANNEL_BUSY
  - 已受理 Run 可查询/取消；不静默丢
  - 队列恢复后按 SLO 排空
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5
  rules:
  - RULE-08
  checks: []
- id: B-02
  title: 20k 事件/s 下 retention 追得上写入
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - postgres
  boundary: 真实 PostgreSQL；批量写入 + retention sweep
  preconditions:
  - 事件写入 20k rows/s 的等价批量（或 1000 Run × 20/s）
  - retention 批量/索引已调整
  steps:
  - 持续写入并运行 retention
  - 采样表大小、删除速率、WAL、索引
  expected:
  - 删除速率 ≥ 写入速率；表大小稳定
  - 无长事务/锁等待；分区/索引按设计生效
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.3 / §3.5
  rules:
  - RULE-09
  - RULE-02
  checks: []
- id: B-03
  title: 多 worker 下指标与 artifact token 不串进程
  priority: P1
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - gateway
  - console
  boundary: 真实 uvicorn 2+ workers；/metrics 与 artifact fetch token
  preconditions:
  - Gateway/Console 使用多 worker
  - ArtifactFetchTokens/metrics 已改造
  steps:
  - 抓取 /metrics，核对计数/SSE 账本聚合
  - 在一个 worker 签发 artifact token，在另一个 worker 下载
  expected:
  - 指标包含所有 worker 样本；active subscriber 总数等于各 worker 之和；pending bytes ≤ 配置总预算
  - artifact token 跨 worker 可用
  - 无进程内状态造成的 404/漏计数
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 1
    - 2
  why_e2e: ''
  trigger_paths: []
  smoke: false
  design_refs:
  - backend design §3.5 / §4.1
  rules:
  - RULE-05
  - RULE-11
  checks: []
- id: B-04
  title: 静态 overlay 保持同源 Cookie/CSRF 与 SSE 路由
  priority: P1
  level: E2E
  exec: deferred
  owner: TASK-011
  target_phase: review
  defer_reason: 静态 overlay 为 opt-in 可选层；未启用 overlay 时无真实边界可测，留 verify-e2e
  env: browser
  components:
  - frontend
  - backend
  - gateway
  boundary: 真实浏览器 → Ingress → console-web/CDN + console-api + gateway；同源 Cookie
  preconditions:
  - 启用可选 console-web overlay
  - 同域路径分流
  steps:
  - 打开 /chat，登录/交换 CHAT 会话
  - 加载静态资源并发起 /api 与 /api/v1/webchat 请求
  - 打开 SSE
  - 在不启用 overlay 的默认 base 上重复登录/API/SSE 关键路径作为对照
  expected:
  - Cookie/CSRF/Origin 正常；无 CORS
  - 静态资源不经过 Python；SSE 仍走 Gateway 且 buffering off
  - 未启用 overlay 时默认 4 单元行为不变
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  - expected: 3
    steps:
    - 3
    - 4
  why_e2e: 浏览器同源策略、Cookie、CSRF、Ingress 路径与 SSE 只有真实浏览器 + 真实代理才能验证。
  trigger_paths:
  - apps/console-platform/frontend/**
  - deploy/k8s/base/ingress.yaml
  - deploy/k8s/overlays/console-web/**
  smoke: false
  design_refs:
  - frontend design §3.2 / §3.5
  rules:
  - RULE-10
  checks: []
---



# 高 QPS 架构整改验收场景

> 本文件是验收场景的唯一源。`high-qps-remediation.backend.design.md` 与
> `high-qps-remediation.frontend.design.md` 只引用场景 ID，不复制执行元数据。
>
> 目标：在开源部署可接受的前提下（默认 4 个部署单元；启用企微时 5 个；中间件仅 PG + Redis），
> 让 1000 个并发对话用户的瓶颈不再落在 Gateway/Console 的同步路径与单 PG 连接/写入上。

