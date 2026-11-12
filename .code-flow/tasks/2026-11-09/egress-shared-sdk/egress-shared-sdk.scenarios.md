---
id: egress-shared-sdk-scenarios
schema: 1
requirement: egress-shared-sdk
status: draft
scenarios:
- id: S-01
  title: 跨进程共享接口上限
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 同时竞争同接口50次窗口预算
  expected:
  - 两进程合计最多50次获准并到达HTTP探针；所有KEY同slot，无CROSSSLOT
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-02
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-009
- id: S-02
  title: 父子规则原子判定
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实Redis及两个SDK客户端
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 为两个invocation叠加execution和target规则并并发申请
  expected:
  - 任一桶不足时其他桶不扣；成功申请同时占用全部适用桶
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-03
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-009
- id: S-03
  title: 恢复与Task重领延续累计
  priority: P0
  level: integration
  exec: deferred
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实PG、Redis、Runtime和Worker执行器进程
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 用去30/50后终止进程并恢复或重领同一调用
  expected:
  - execution/invocation不变，仅剩20；segment变更不清零；Worker使用冻结execution_timeout_sec
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-03
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-006
  defer_reason: 用户已明确要求重型Linux进程/恢复验收统一放项目CI，本地只做定向验证；未运行不计pass
  target_phase: review
- id: S-04
  title: 旧requests脚本透明接入
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: Linux受控镜像、系统site hook、真实Skill子进程及HTTP
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 按生产-E -s -B参数启动原import requests脚本，调用Session.request与send
  expected:
  - 无需改业务逻辑即受控；Cookie和固定CSRF保持；无hook启动自检明确失败
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-01
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-004
- id: S-05
  title: 认证与MCP逐发送计量
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 依次执行login/validate/业务重试及initialize/notification/tool
  expected:
  - 每个物理发送各计一次；超限不发送；不在运行时新增tools/list
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-01
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-005
- id: E-01
  title: 计数依赖不确定时关闭准入
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实Redis进程、网络故障代理与HTTP探针
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 注入断连、ACL拒绝、提交后响应丢失及错误类型key
  expected:
  - 受控新请求不发送；协议重试同attempt不重复扣；错误标记依赖故障而非累计耗尽
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-06
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-003
- id: E-02
  title: 累计耗尽真正停止脚本
  priority: P0
  level: integration
  exec: deferred
  env: ci
  components:
  - sdk
  - backend
  boundary: Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 脚本消耗N次后捕获第N+1次异常继续循环，另有孙进程及慢请求
  expected:
  - N次可完成，N+1不发送；宿主killpg回收进程组和请求；Task不重试；Run保留已完成结果
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-06
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-006
  defer_reason: 用户已明确要求重型Linux进程/恢复验收统一放项目CI，本地只做定向验证；未运行不计pass
  target_phase: review
- id: E-03
  title: Proxy错误透传
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实HTTP Proxy探针与SDK客户端
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 探针返回429、Retry-After和业务响应体
  expected:
  - 状态头正文保持原语义，标记UPSTREAM；已发送计数不返还；不变成本地拒绝
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-05
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-005
- id: E-04
  title: 监控故障与缺口
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实事件spool文件系统、PG汇总及Redis
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 停止明细写入后恢复，再耗尽有界spool
  expected:
  - 可补写事件按attempt去重；缓冲失效显式记录gap区间和丢失数；业务准入不依赖日志写入成功
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-07
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-007
- id: B-01
  title: 连续窗口边界及时钟回拨
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实Redis Lua及可控隔离测试时钟
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 在连续60秒跨整分边界竞争，另向时钟守卫注入回拨
  expected:
  - 有效区间为(now-window,now]，不存在跨整分放行100次；回拨不提前释放窗口
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-02
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-009
- id: B-02
  title: 跳转重试与目标授权
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实requests/urllib3、HTTP重定向及失败探针
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 执行一次302、两次受控503重试并跨Host跳转
  expected:
  - 服务分别接收2/3次且逐次申请；未经授权跳转拒绝；自定义adapter显式声明支持或报不支持
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-01
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-004
- id: B-03
  title: 流式并发租约
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实流式HTTP、Redis及宿主deadline监督
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 仅取响应头后再申请并发，随后EOF/close/异常/取消/重复释放
  expected:
  - 读完前保持占用；只释放自身token；续租失败关闭本地传输；租约不早于强制deadline安全过期
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-04
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-003
- id: B-04
  title: 公共规则版本一致
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 两个真实SDK进程与Redis权威规则
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - A缓存旧100，发布新50后由A/B共同申请；随后增加窗口长度
  expected:
  - 按当前50统一判定且不清桶；旧缓存先拒后刷新；历史不足进入新窗口阻断期；冻结累计不变
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-02
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-009
- id: B-05
  title: 状态生命周期及丢失
  priority: P0
  level: integration
  exec: auto
  env: ci
  components:
  - sdk
  - backend
  boundary: 真实PG、Redis Cluster主从切换与故障演练
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 恢复旧execution、删除累计key或改变受控epoch并演练failover
  expected:
  - 可恢复执行不按明细TTL清零；缺key或epoch不确定时阻断；不能静默恢复旧执行；若无法检测切换则该保证不得验收
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-06
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-010
- id: B-06
  title: 无规则与非法配置
  priority: P0
  level: contract
  exec: auto
  env: none
  components:
  - sdk
  - backend
  boundary: 真实typed配置及带调用计数的依赖测试替身，不声称真实网络覆盖
  preconditions:
  - Plan阶段仅收集骨架；实施前替换真实断言，骨架失败不得作为功能RED
  steps:
  - 解析空规则、非法阈值、未知字段、不同目标映射及未支持客户端
  expected:
  - 无规则不设上限且不访问计量Redis；非法配置拒绝；未知目标不误归桶；未支持客户端标记未受控
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-01
  design_refs:
  - egress-shared-sdk.design.md#2.5 验收条件
  - egress-shared-sdk.design.md#3.2 架构设计
  rules: []
  checks: []
  owner: TASK-001
- id: B-08
  title: 统一Settings替代专用文件
  priority: P0
  level: contract
  exec: auto
  env: local
  components:
  - sdk
  - backend
  boundary: 真实SharedSettings与SDK装配；存储依赖替身不代表真实网络
  preconditions:
  - 不设置专用文件路径
  steps:
  - 仅用统一Settings构建受控宿主，再校验空规则和非法生命周期
  expected:
  - 启用装配无需JSON；空规则不构造Redis；非法参数明确拒绝
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-01
  design_refs:
  - remove-egress-file.plan.md#1. 范围与验收 B-08
  rules: []
  checks: []
  owner: TASK-012
- id: B-09
  title: Gateway首次快照就绪
  priority: P0
  level: contract
  exec: auto
  env: local
  components:
  - backend
  boundary: 真实BotSnapshotCache和ASGI健康端点，Console故障替身
  preconditions:
  - 首次未应用完整快照
  steps:
  - 分别返回不完整快照和制造快照应用失败，查询readyz
  expected:
  - 两种情况均503；Console连通不等于已应用
  expected_refs:
  - expected: 1
    steps:
    - 1
  features:
  - FEAT-01
  design_refs:
  - ci-gateway-readiness.plan.md#1. B-09范围与证据
  rules: []
  checks: []
  owner: TASK-013
---

# 共享出口管控 SDK 验收场景

本文件是验收场景唯一源。
