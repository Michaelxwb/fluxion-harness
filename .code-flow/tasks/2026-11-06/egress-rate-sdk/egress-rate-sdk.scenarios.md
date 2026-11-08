---
id: egress-rate-sdk-scenarios
schema: 1
requirement: egress-rate-sdk
status: draft
scenarios:
- id: S-01
  title: RequestBudget 放行、计次和脱敏
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 Python SDK 对象与可注入单调时钟
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 构造累计、滑动窗口和 inflight 规则并依次获取/关闭 lease
  - 触发 success/error/cancelled/proxy_rate_limited outcome，检查 used/inflight 和错误对象
  expected:
  - 每次 acquire 代表一次真实发送尝试，失败/取消/Proxy 429 不返还次数；正常关闭释放 inflight
  - 错误和 callback 只有 scope/target/limit 元数据，不含 header/body/token/cookie
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-02
  - FEAT-06
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-01
  - RULE-SDK-02
  checks: []
  owner: TASK-002
- id: S-02
  title: PlatformAdapter 认证、校验、业务和重试共用 gate
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - sdk
  boundary: 真实 Runtime/Worker + 本地 HTTP 探针 + PlatformAdapter，不使用 mock transport
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 配置登录、health、业务请求预算，注入一次登录失效触发受控 GET 重试
  - 读取探针实际请求数并对照 SDK callback
  expected:
  - 登录、校验、业务和重试都在实际 send 前经过 gate；超限时对应请求未发出
  - 每次真实 HTTP 尝试只计一次，既有认证/Session 语义仍成立
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-02
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-01
  - RULE-SDK-03
  checks: []
  owner: TASK-003
- id: S-03
  title: MCP gate 与 Proxy 429 透传
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - sdk
  - mcp
  boundary: 真实 Runtime + Streamable HTTP MCP 探针
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 调用 initialize、initialized notification、tools/call，同时设置 Run 预算
  - 探针对 tools/call 返回 429 + Retry-After，检查上层错误和 callback
  expected:
  - 每个实际 MCP HTTP 发送分别计次，本地超限不外发，现有授权保持
  - 上游 HTTP 状态和 Retry-After 通过安全结构化字段保留，不误标本地超限，不暴露原始响应正文/秘密
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-02
  - FEAT-05
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-01
  - RULE-SDK-04
  checks: []
  owner: TASK-004
- id: S-04
  title: Run/Task 快照和 invocation 身份可信
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - sdk
  - worker
  boundary: 真实 Runtime/Worker、Skill 子进程和既有快照/租约边界
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 从 Agent runtime_config 的 execution/skill_defaults/skills 解析有效预算并冻结快照
  - 执行中改变配置，先执行两个脚本再进行 Task retry 和 Run resume
  - 尝试从脚本 input 覆盖可信预算和身份
  expected:
  - 策略始终沿用冻结值；同段脚本共用 execution 计数，各 invocation 有独立子计数
  - retry/resume 建立新段和新本地计数，并记录 segment 身份；不声称跨段累计硬上限
  - 脚本输入不能改变可信预算/身份；已知预算耗尽不自动重试
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
    - 3
  - expected: 3
    steps:
    - 1
    - 2
    - 3
  features:
  - FEAT-03
  - FEAT-04
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-05
  - RULE-SDK-06
  checks: []
  owner: TASK-005
- id: E-01
  title: 本地次数或并发超限在发送前拒绝
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 SDK 对象与 fake send counter，不发送真实网络
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 耗尽 max_requests，再尝试 acquire；另将 inflight 占满后尝试新 acquire
  - 检查异常字段、已用次数和底层 send 调用数
  expected:
  - 返回结构化 LocalRateLimitError，底层发送调用数为零；规则失败不部分扣减
  - 并发释放后可再次获取；拒绝不会增加请求次数
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-04
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-02
  checks: []
  owner: TASK-002
- id: E-02
  title: invocation 耗尽终止脚本但 Run 可继续
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - worker
  - sdk
  - artifact
  boundary: 真实 Skill 子进程组、Runtime/Worker 和 HTTP 探针；沿用 start_new_session/killpg
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 运行捕获 SDK 错误后仍循环的脚本达到 invocation 预算
  - 观察进程组、探针请求数和父 Run/Task 后续汇报
  expected:
  - 第 N 次正常完成；第 N+1 次被拦截，通知父进程回收 invocation 整个进程组及未完成请求，不自动重试
  - 已有外部操作保留，Run 可汇报部分结果，Task 按确定性错误收尾
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-04
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-06
  checks: []
  owner: TASK-005
- id: B-01
  title: 多条本地规则原子判定
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 SDK 对象与可注入时钟
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 一个 controller 同时管理 execution 父预算和 invocation/target 子预算，分别独立耗尽各项
  - 并发 acquire 后检查所有 scope 的计数与占用
  expected:
  - 任一规则不足时 lease 不成立，其他计数/占用保持不变；全部满足才一次提交
  - 相同 scope 的并发调用不超过本地规则
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-02
  checks: []
  owner: TASK-002
- id: B-02
  title: 无预算完全旁路
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 SDK 装配对象，不连接 Redis/PG
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 分别用 budget=None 和全 None policy 发起 Platform/MCP 发送
  - 检查无 limiter await、计数器、Redis/PG 依赖或默认错误
  expected:
  - 请求走原有发送路径，不创建计数结构、不访问外部计数依赖、不增加默认限制
  - Proxy 返回结果仍由原客户端处理
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-05
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-07
  checks: []
  owner: TASK-002
- id: B-03
  title: 流式/取消/异常释放 inflight
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - sdk
  boundary: 真实慢 HTTP/流式探针与 Runtime 发送器
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 保持响应体未关闭时达到 inflight 上限，分别取消、断开和正常关闭 lease
  - 随后发送新请求检查可用槽位
  expected:
  - 流关闭前不释放；取消、异常、正常关闭均释放一次且重复 close 不误释放其他 lease
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-02
  checks: []
  owner: TASK-003
- id: E-03
  title: 进程重启不冒充全局计数恢复
  priority: P0
  level: integration
  exec: auto
  env: local-api
  components:
  - backend
  - sdk
  boundary: 两个独立 Runtime/Worker 进程 + 真实 Proxy HTTP 探针，不使用共享 limiter 存储
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 进程 A 使用本地预算后退出，进程 B 以新执行启动；Proxy 注入全局 429
  - 分别读取本地 callback 和 Proxy 响应
  expected:
  - 新进程不宣称恢复旧进程计数，上游 429 可被上层识别
  - 两个进程各有本地计数；只验证上游响应处理，不宣称验证 Proxy 全局算法
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-05
  design_refs:
  - egress-rate-sdk.design.md#2.4
  - egress-rate-sdk.design.md#3.2
  rules:
  - RULE-SDK-04
  - RULE-SDK-07
  checks: []
  owner: TASK-005
- id: B-04
  title: 配置合并及非法值边界
  priority: P0
  level: contract
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据
  preconditions:
  - 真实配置解析器已实现；必须执行断言，不能用 Plan 骨架替代
  steps:
  - 解析省略、null、Skill 显式值与默认预算及父预算的关系
  - 输入 bool、0、负数、非有限秒数、单边窗口和未知字段
  expected:
  - Skill 按字段覆盖默认，父预算继续约束；空有效规则旁路
  - 非法预算在发送前失败，不能回退为无限制
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-03
  design_refs:
  - egress-rate-sdk.design.md#3.5
  - egress-rate-sdk.design.md#3.7
  rules: []
  checks: []
  owner: TASK-001
- id: B-05
  title: 接口匹配与连续窗口边缘
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 匹配动态路径段、不同 query、重叠规则及 MCP 握手/Tool 请求
  - 用注入的单调时钟测试恰好过期、同时间戳并发和墙钟回拨
  expected:
  - query 不新建桶，所有命中规则共同生效，握手不冒充 Tool
  - 窗口开左闭右，不受墙钟回拨影响，拒绝不部分扣减
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-01
  - FEAT-02
  design_refs:
  - egress-rate-sdk.design.md#3.5
  - egress-rate-sdk.design.md#3.7
  rules: []
  checks: []
  owner: TASK-002
- id: E-04
  title: 观测回调失败不改变发送及收尾
  priority: P0
  level: unit
  exec: auto
  env: none
  components:
  - sdk
  boundary: 真实 Python SDK/配置契约对象，可注入时钟和 observer；不代表产品执行证据
  preconditions:
  - Plan 测试仅为可收集骨架，Start 时先替换为真实断言并取 RED/GREEN 证据；禁止把骨架失败当功能 RED
  steps:
  - 在放行、拒绝、取消和上游限流时注入 observer 异常
  - 检查发送次数、inflight、warning 及失败指标
  expected:
  - observer 失败可见，不重复发送业务请求或将拒绝变放行
  - lease 正确释放，日志不含头/正文/凭据
  expected_refs:
  - expected: 1
    steps:
    - 1
    - 2
  - expected: 2
    steps:
    - 1
    - 2
  features:
  - FEAT-06
  design_refs:
  - egress-rate-sdk.design.md#3.5
  - egress-rate-sdk.design.md#3.7
  rules: []
  checks: []
  owner: TASK-002
- id: S-05
  title: 有效出站预算在 Run/Task 快照中冻结
  priority: P0
  level: contract
  exec: auto
  env: none
  components:
  - backend
  - contracts
  boundary: 真实 Runtime build_snapshot/execution_defaults_of 与 contracts build_task_snapshot；只验证构建读回契约，不代表
    DB/子进程生命周期
  preconditions:
  - 构造真实 ResolvedAgent/ResolvedSkill/WorkspaceRef；不访问真实业务平台
  steps:
  - 用同一配置分别构建 Run policy_json 和 Task execution_snapshot
  - 修改 Agent 原配置，再读回旧快照并构建新快照
  expected:
  - 两类快照携带相同有效预算；Task 预算只在 budget.egress_budget，Run 在 policy_json.egress_budget
  - 旧快照独立于修改后的 Agent 配置，新快照采用新值并改变 hash；读回不重新 resolve 或读取控制面
  expected_refs:
  - expected: 1
    steps:
    - 1
  - expected: 2
    steps:
    - 2
  features:
  - FEAT-03
  design_refs:
  - egress-rate-sdk.design.md#3.5
  rules:
  - RULE-SDK-05
  owner: TASK-001
  checks: []
---

# SDK 验收唯一源

S-04 保持完整真实集成边界，归 TASK-005；S-05 独立验证快照构建契约，归 TASK-001。
