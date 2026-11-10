---
design_template: full
version: 0.1
date: 2026-11-09
status: design-review
implementation_status: partially-implemented
baseline: a089b9e0
supersedes: ../../2026-11-06/egress-rate-sdk/egress-rate-sdk.design.md
---

# 共享出口管控 SDK 模块需求与设计一体化文档

## 1. 文档控制

### 1.1 责任人

需求决策：Max；设计与实施：当前Code Flow任务执行者；验收：各TASK唯一owner。目前仅共享规则和可信身份契约及惰性装配已实现；Redis共享计量、接入和需求级验收仍未完成。具体状态以任务记录为准。

### 1.2 修订历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1 | 2026-11-09 | 继承更新5.7聊天确认，合并a089b9e0后重新设计共享SDK |

来源：聊天01a1be2e-5bdb-7632-b3e8-2e1e2848dd11最后确认及本聊天CSRF澄清。替代旧进程内方案的“跨Pod不共享、恢复清零、计数故障放行”结论；旧目录及测试证据保留，不迁移为新验收。现场已有暂存改动属于旧任务，本计划不改动其暂存状态。

## 2. 需求分析

### 2.1 需求概述

在既有Runtime/Worker内嵌SDK，使用现有Redis实现本组件已接入请求的跨Pod限制；外部APIProxy继续做整个平台保护。不得新增独立限流服务。

### 2.2 痛点与价值

进程内限额随Pod数量放大，旧requests逻辑请求hook遗漏redirect/retry；恢复重新生成invocation会绕过累计预算。目标是在不改旧Skill业务逻辑的前提下，让已声明支持的发送路径共享计量并可核查。用户规模与峰值尚未统计，不编造收益比例。

### 2.3 功能方案

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-01 | 统一接入与配置 | 旧requests透明hook、宿主Adapter/MCP、可信目标和无规则旁路 | P0 | 用户需求及更新5.7确认 |
| FEAT-02 | 共享接口频率 | 统一当前公共护栏、连续窗口、多规则原子判定 | P0 | 用户需求及更新5.7确认 |
| FEAT-03 | 冻结累计预算 | 稳定execution/invocation身份，恢复不清零 | P0 | 用户需求及更新5.7确认 |
| FEAT-04 | 共享并发租约 | 流式请求持有至关闭，有界deadline和fencing | P0 | 用户需求及更新5.7确认 |
| FEAT-05 | Proxy结果透传 | 上游429、Retry-After、业务错误与本地拒绝分离 | P0 | 用户需求及更新5.7确认 |
| FEAT-06 | 故障与执行监督 | 计量fail closed、累计耗尽停止进程组 | P0 | 用户需求及更新5.7确认 |
| FEAT-07 | 监控与缺口 | 摘要7天、汇总90天、有界缓冲和gap可见 | P0 | 用户需求及更新5.7确认 |

### 2.4 范围与边界

本阶段：共享内核、requests接入、Runtime/Worker/Adapter/MCP接线、后端摘要与接口聚合。模型/入站API单独管理；SDK不是防恶意绕过的网络边界。无规则无隐含限额、无计量Redis访问；非法配置不当无限制。

监控Console查询视图与预警界面需求保留，交由后续前端设计；本后端计划不以“无UI”宣称全部产品需求完成。公共规则首期受控配置发布，无Console CRUD。所有首次接入客户端应登记支持范围。

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 规则 | 验证场景 |
|---|---|---|
| RULE-SHARED-01 | 全部适用规则先检查后共同扣减，拒绝不部分扣 | S-01, S-02 |
| RULE-SHARED-02 | 每次真实发送重新申请；已放行未发送保守计数，不返还 | S-05, B-02 |
| RULE-SHARED-03 | 恢复不清累计；计量未知关闭受控准入 | S-03, E-01, B-05 |
| RULE-SHARED-04 | 上游响应与认证语义保持，本地累计拒绝停止脚本 | E-02, E-03, S-04 |

#### 2.5.2 功能验收场景

场景定义见egress-shared-sdk.scenarios.md，验证状态以egress-shared-sdk.md的验收记录为准；当前仅B-06契约场景已验证，其他共享限流场景尚未验证。后端跨进程旅程使用integration，不伪装浏览器E2E。设计落点：S-01/S-02/B-01/B-04对应共享Lua；S-03/B-05对应恢复；S-04/B-02对应hook；S-05/E-03对应宿主；E-01/E-02/B-03对应故障与监督；E-04对应监控；B-06对应typed配置。

#### 2.5.3 非功能指标

| 指标ID | 目标 | 测量方式 | 场景 |
|---|---|---|---|
| NFR-01 | 未配置规则时零计量Redis调用 | 依赖调用计数 | B-06 |
| NFR-02 | 不向脚本、快照、日志泄露Redis凭据和请求正文 | 真实脚本与日志安全断言 | S-04, E-04 |
| NFR-03 | 明细7天、汇总90天；故障可见gap | 隔离PG维护测试 | E-04 |

#### 2.5.4 验证环境假设

| 维度 | 假设 | 验证方式 | 不满足时 |
|---|---|---|---|
| 容量 | 峰值未知；测试阈值50只是fixture | 发布前记录热slot延迟/内存与实际峰值 | 不声明容量验收 |
| 硬件 | Linux CI支持bwrap与进程组；本机macOS不能替代 | Linux隔离runner preflight | env_blocked，owner=运行环境维护者 |
| 依赖 | 独立PG、Redis Cluster及故障代理、真实HTTP | 测试启动清单与health check | env_blocked，禁止共用dev库 |
| profile | Plan只收集；实施先定向；全量按CI缓存runbook | 保存收集/执行/复用来源 | incomplete不自动降级 |

## 3. 技术设计

### 3.1 方案选型

选Python现有SDK+Redis原子脚本：保留既有部署关系，较远程限流服务少一个业务RPC层。拒绝文件锁/内存计数，因为无法跨Pod；拒绝把全部正确性外包APIProxy，因为其不能定义本组件每次执行预算。单原子域同slot是正确性取舍，不能用多slot pipeline冒充事务。

以下是已核对muad-skill-http 0.3.0发布源码与旧包探针的评估；这些结果不证明新实现有效。参考代码未复制进仓库，也未将临时目录作为测试依赖。


### 3.1 可以复用什么

| 模块 | 发布源码的实际行为 | 复用方式 |
|---|---|---|
| `compat.py` | 在开启开关且有 Host 列表时 patch requests.Session.request | 保留透明启动和目标过滤思路，修改实际发送 hook 层级 |
| `_http.py` | requests 薄封装，返回原 Response | 可保留习惯用法；薄封装与 hook 只能有一个计量点 |
| `_normalize.py` | query/fragment 不入路径，数字/UUID/长十六进制段归成 `{id}` | 用于接口发现建议；硬规则使用明确注册的 route ID/模板 |
| `_trace.py` | 摘要、top interface、本地 JSONL、执行间文件锁 | 保留事件字段思路；spool 是受控缓冲，计量内核重写 |
| `_local.py` | Cookie → 本地调试 state | 仅开发辅助；与生产认证和限流解耦 |

该发布包的 setuptools 配置只收集 Python package；“镜像的 .pth 启动 hook”由 README 描述，并不等于 `pip install` 后已自动完成运行镜像接线。必须实际检查镜像与启动参数。

### 3.2 不能原样保留什么

1. `_SharedCounter` 是 `fcntl` 锁文件，无法提供跨 Pod 的统一原子计量。异常时退回本地字典会破坏共享上限；新内核必须 fail closed。
2. `Session.request` 包住整个逻辑请求，会漏记 requests 重定向及 urllib3 底层自动重试；只换存储不修发送边界仍然漏算。
3. 普通 `requests.get` 经 compat 且 Host 命中时，`compat.py` 强制 UUID 覆盖 CSRF；但 `muad_skill_http.get(headers=...)` 经 internal_request 跳过该覆盖，可传固定值。同事的说法适用于后一入口。新限流层保留有效固定值或真实Cookie配对值，不负责生成认证值。
4. `_trace.admit` 在拒绝第 N+1 次前已递增总数；新监控必须拆分 attempted/allowed/blocked，blocked 不应计作实际已发送。
5. `os._exit(86)` 只退出当前进程，不证明父脚本、兄弟进程和在途请求被回收。脚本可捕获异常，父执行器停止信号必须独立可靠。
6. burst 的 10 秒峰值统计不是任意连续 60 秒的拒绝策略；旧版没有分布式并发租约或多规则原子判定。
7. 日志写入异常被吞掉，最多 500 条明细、无可靠请求唯一 ID，不能宣称完整逐请求监控。
8. Host 白名单是 Pod 所有平台的并集，不是本次用户/Skill 获授权平台集合；host:port 也不一定唯一识别一个平台实例。

### 3.3 已做的最小真实探针

环境：未改动的 0.3.0 发布源码，requests 2.34.2、urllib3 2.8.0，本机 ThreadingHTTPServer。只验证旧 SDK 的这三种行为，不代表 Redis 新设计或生产平台认证已验证。

| 探针 | HTTP 服务实际收到 | SDK admit 次数 | 结果 |
|---|---:|---:|---|
| 一次 GET 跟随一次同 Host 重定向 | 2 | 1 | 漏计重定向 |
| 配置 urllib3 对 503 重试两次 | 3 | 1 | 漏计自动重试 |
| 普通requests + compat显式CSRF | 1 | 1 | 被UUID覆盖；封装入口不适用此结论 |

因此可以“在旧包基础上升级”，但改动集中在核心发送与计量职责，不能承诺只改几行。同步 requests/返回 Response 的表面尽量保留，正确性差的旧行为直接替换，不并行运行文件/Redis 两套计数。


### 3.2 架构设计


```text
Pod A / Runtime 或 Worker                         Pod B / Runtime 或 Worker
  Skill requests                                 Skill requests
      ↓ 兼容 transport hook                          ↓ 兼容 transport hook
  invocation 专属 Unix IPC                        invocation 专属 Unix IPC
      ↓                                              ↓
  宿主 Egress SDK ← PlatformAdapter/MCP          宿主 Egress SDK ← PlatformAdapter/MCP
      └─────────────── Redis 共享状态 ────────────────┘
                      检查成功返回 permit
  原 HTTP 客户端发送 ───────── 外部 APIProxy ───────── 业务平台
```

SDK 嵌入既有 Runtime/Worker 进程；Unix IPC 是执行进程内部的本地接线，不新增 Deployment、远程服务或独立常驻代理。Redis 不转发请求。代码分层保持 packages → contracts/shared packages，不能从 package 反向 import app。

Skill 兼容包只处理同步客户端协议、请求元数据和响应关闭；宿主内核掌握 Redis、规则、计数、可信身份及停止事件。MCP 和认证发送在宿主直接调用同一内核。无需把 Redis DSN、密码或任意 Lua 权限下发给脚本。

##### requests 接管

- 在受控镜像的系统 site-packages 安装启动 hook，适配项目实际 `-E -s -B` 启动方式，不依赖 PYTHONPATH 或用户 site。`-S`、自建解释器和独立虚拟环境不承诺覆盖。
- hook 选择 `HTTPAdapter.send(PreparedRequest)` 一类实际 transport 边界，对每个重定向跳转重新授权和计量；`Session.send(prepared)` 也被覆盖。
- 受控 adapter 禁用隐藏的 urllib3 自动重试，确有重试需求时由受控循环每次重新申请 permit。对脚本已定制的 adapter/retry 策略显式报不支持或接入专门适配，不能静默减少其原重试行为而不记录迁移影响。
- `stream=True` 获取响应头后继续持有并发租约，EOF/close/read 异常/取消才完成；上层 helper 和 compat 不重复计数。
- 重定向进入另一 Host/端口/协议时先重新判定授权。已受控请求跳到未获授权目标时拒绝，而非掉出白名单后悄悄直连；独立非业务请求仍按既有出网策略处理。
- 保留 Cookie、Authorization、Host、CSRF、状态、headers、body、SSL verify 和超时语义。trace/request ID 采用项目现有契约，不生成假的 CSRF。

##### 一次请求的协议

1. 宿主基于发起连接绑定的可信 execution/invocation、当前租约与冻结授权，确认这次执行仍可运行。脚本上报的 tenant/run/task ID 不作权威。
2. 解析实际发送 URL，校验 scheme/origin/Host 配置、有效端口及路径编码；由可信注册表映射 physical platform instance 和 route。MCP 用 server identity + rpc_method + 可选 tool。
3. 宿主产生唯一 attempt_id，收集全部适用的公共接口规则与冻结执行规则。
4. Lua 验证运行状态、当前策略 revision、计数 epoch，清理滑动窗口和过期租约；先检查全部桶，再一次提交全部增量。不足则所有桶不扣。
5. 允许时创建有界 permit/lease，然后立即进入底层发送。跨进程取消/连接失败导致 permit 已扣但没发出时，保守计一次，不返还累计和窗口次数；监控将 allowed 与 observed send start 分开。
6. 完成时按 attempt_id + lease token 幂等释放并发、发摘要；失败/超时/Proxy 429 仍消耗发送配额。响应原样交还调用者。

Redis admit 的通信重试沿用相同 attempt_id，只用于查询同次预占是否已提交，不能再次扣费；客户端不允许把已有 permit 复用到第二次物理发送。Redis 无法确认时不发请求。预算协议幂等不代表业务请求 exactly once，外部副作用仍由业务幂等键负责。

#### 稳定身份与规则版本

##### 稳定身份

| 身份 | 用途 | 恢复原则 |
|---|---|---|
| protection_domain | 本 Fluxion 部署的计数与原子性域 | 所有 Pod 使用同一域；不是 Pod 名 |
| target_instance_id | 实际被保护的平台实例 | 不使用用户凭据 ID；同一实例的多个租户配置应显式映射到同 ID |
| route_id | method + 已注册 path 模板 | query 不建桶；版本发布保持 ID 稳定 |
| execution_id | Run 或 Task 业务执行 | PG 冻结并恢复；同 Task attempt 不生成新 execution |
| invocation_id | 一次逻辑脚本调用 | 实际 call/operation 在启动前持久化；子进程共享，重领同一次调用复用 |
| segment_id / lease_epoch | 当前执行实例的执行段/防旧持有者 | 每次激活更新，用于 fencing；不用于重建累计桶 |
| attempt_id | 一次获准底层发送 | 业务重试新 ID；Redis 协议重试用原 ID |

同一 Run 重新明确调用一次脚本是新 invocation，执行总预算继续约束；崩溃恢复同一次调用不得伪造新 invocation。定时任务下一次 fire 是新 Task。不同 Child Task 默认独立累计，需要父任务预算时显式绑定 root scope，不能隐式扩大。

##### 公共保护与冻结预算分开

- 公共平台/接口的频率和并发是共享护栏，在每次 admission 上使用同一权威当前版本；旧 Run 也受它约束。否则旧 Run=100、新 Run=50 虽共用桶，仍按各自阈值判断，无法保证统一的 50。
- Agent/Skill 的执行/脚本累计预算在 Run/Task 创建边界冻结，后续恢复沿用同份快照；公共规则是附加护栏，只能收紧允许集合，不能被 Skill override 覆盖。
- 公共规则首期由运维通过受控的版本化配置发布，宿主向 Redis 发布一致 revision；不新增 Console CRUD 页面。是否扩展现有平台设置 DTO 在实现 Plan 明确，不把规则硬塞进认证 adapter_config。
- stable rule ID 和计数 key 不含 policy revision；修改阈值不重置已用次数。Lua 直接读取有效公共规则；宿主旧 revision 在扣费前被拒并刷新，不能按旧缓存放行。
- 降额只拒后续新请求，不回滚已完成请求；当前 inflight 高于新上限时等其自然收尾。窗口长度增加需要保留足够长历史；若既有数据不完整，先进入至少新窗口长度的保守阻断期，不能假装从零开始。
- 新增规则时无法重建过去未计量的历史：文档和界面明确以生效时刻为起点，并对涉及严格滚动窗口的切换采取保守阻断。

#### Redis 数据与原子性

##### 首期采用一个原子域同 slot

为了同时支持接口跨租户共享及 execution 跨平台总预算，所有可能共同命中的 key 首期用同一 protection_domain hash tag，例如：

```text
fx:egress:{prod-domain}:policy
fx:egress:{prod-domain}:guard
fx:egress:{prod-domain}:target:<instance>:<route>:window
fx:egress:{prod-domain}:execution:<execution_id>:total
fx:egress:{prod-domain}:invocation:<invocation_id>:total
fx:egress:{prod-domain}:lease:<attempt_id>
fx:egress:{prod-domain}:decision:<attempt_id>
```

示例不是固定部署值，环境/产品前缀按配置生成。一次 Lua 涉及的所有 key 显式传 KEYS，校验 hash tag；不靠按平台或租户分 slot 后再宣称跨 slot 原子。首期一个 slot 是有意的容量取舍，不承诺自动横向扩展；上线前以真实峰值压测该热 slot、Lua 时间及内存。用户未提供峰值，不编造吞吐或 SLO 数字。

需要进一步分片时必须按完整原子域划分；跨域 Run 总预算要另行设计协调，不能在本版“顺便支持”。

##### 算法

- 累计：执行/invocation 的受信计数，拒绝不增，已 admit 不退。
- 连续窗口：Sorted Set 存 Redis 服务端 TIME 与 attempt_id，清理 `timestamp <= now-window`，有效区间 `(now-window,now]`；正常工作下任意连续窗口至多允许阈值次数。Redis 时钟漂移仍需运维约束和回拨测试，不能宣称服务端时间天然等同单调时钟。
- 并发：token 化租约及 expiry 集合；每次 admit 清理安全过期项，release 只能释放自身。租约时长覆盖宿主强制端到端 deadline 和清理余量，流式续租只能在仍持合法执行租约且远端连接未终止时进行。
- Lua 先做类型/参数/状态完整校验再修改，避免脚本中途报错留下部分写；Redis Lua 的隔离执行不意味着运行时错误自动回滚。检测到错误可能已写入时按计数不确定处理。
- 计数 key 的生命周期由执行是否允许恢复决定，不由日志 7 天 TTL 决定；Run/Task 仍可恢复时不得删除累计 key。终态后保留覆盖最大重放期和并发清理期，再由既有维护循环清理。

##### 故障保证的边界

正常跨 Pod 重建、Worker reclaim、WAIT/resume：Redis 状态完整时，继续原 cumulative key；不按 segment/attempt 重置。无规则请求完全旁路计量。

连接失败、Lua 超时、ACL 拒绝、状态缺失或损坏：拒绝受控新请求，记录依赖错误；不当成本地次数耗尽、不自动重跑整个 Skill。访问计数与记录日志的失败策略分开：日志故障可以继续请求，计数故障不行。

**Redis 不是业务事实源，但限流状态也不能当作可随意丢弃的缓存。** 应隔离 key 空间、禁用受控计数的淘汰、启用合适持久化，拒绝脚本删除/改写计数。累计状态缺失时查 PG 确认旧 execution 存在，仍不能自动初始化为零。

Redis 官方明确异步复制在故障切换时可能丢失已确认写入，不能承诺“Redis-only 在任意数据丢失下绝不突破历史累计”。本版采用可用性换保护：状态丢失/主节点代际不确定时关闭受影响保护域，停止继续恢复这些旧执行；窗口等待完整窗口过去，并发等待有界请求结束，确认后才开启新执行。不能用不完整审计简单 SUM 后继续。

若产品还要求“Redis 丢数据后旧执行也必须精确恢复累计且继续运行”，必须增加 PG 权威累计账本/同步扣减或选择更强持久一致后端。这是额外正确性要求，需要另行评审成本；不能只靠 SDK、TTL、AOF 或 WAIT 口头保证。上线前 failover/drill 必须证明计量状态代际变化能触发阻断，否则不得验收该故障保证。

**并发上限**约束 Fluxion 本地仍在发送/等待响应的请求；客户端断连不证明平台任务已停止。远端后台任务占用/执行并发仍需要 APIProxy/平台自身治理，不把 lease TTL 当作远端取消确认。

#### 可信身份和终止

兼容包的连接绑定由宿主创建的一次性私有 socket/启动清单建立，socket 只映射到该 invocation；请求不能任意选择 tenant/platform/run/规则。即便环境变量被脚本改写，也不能改变宿主计量作用域。脚本无需读取 Redis 认证信息。

累计拒绝时，宿主先将 invocation 标记 exhausted，拒绝其后续 admission，再通知执行监督；停止进程组并取消该 invocation 的 broker 请求/关闭传输，最后确认 lease 释放。已完成的业务操作保留。不能先等待 server 关闭所有连接，再取消慢 handler 而造成退出相互等待。

窗口/并发拒绝只拒当前调用，带可计算的 retry_after_seconds（并发未知则为空），SDK 不自动 sleep、排队或业务重试。累计拒绝停止本次脚本；Run 可以组织已完成结果，Task 返回确定性预算错误不重试。Redis 故障、平台故障与用户取消分别显示来源。

#### 监控与预警

- request/attempt identity：一条 attempt 的允许、开始、完成用同一 ID，重试用新的 ID 并链接 logical_request_id/retry_of；聚合以事实阶段和 attempt ID 去重，不累计多次事件为多次请求。
- 允许字段：平台/route/规则 ID、method、Run/Task/invocation/segment、开始时间、耗时、允许/拒绝、结果、状态、重试关系、依赖错误和策略版本。正文、完整 query、Cookie/Authorization、响应体不写明细；展示 upstream reason 时只白名单提取安全错误码。
- 限制检查指标与业务统计分离。Redis 窗口 key 随清理消失，不能拿它直接作为 90 天历史；top 10 或 500 条 detail cap 也不能代表全量。
- 明细保留 7 天、汇总 90 天；复用现有审计/维护链路，需新增字段/索引时按 Owner 与 schema parity 评审，不为限流创建独立监控微服务。
- 日志链路故障：有界 spool + 异步重放；超过缓冲产生明确 gap 区间、丢失数和告警，不静默吞错。控制器准入不依赖明细写库成功。
- 接口请求量、允许/拒绝、上游429、错误/超时、P95/均值、重试、inflight、脚本中止和计量依赖健康可按接口查看；业务 ID 不放 Prometheus 高基数 label。
- 阈值预警只告警、不扣额、不改变请求；限制达到才拒绝。界面显示监控覆盖范围、更新时间及缺口。APIProxy 指标仅补充其他系统流量，未接入不影响 Fluxion 自身预警。


##### 发送所有权与 fencing 的边界
Skill requests 保持在脚本进程发送，IPC只交换准入、send-start、完成和心跳元数据，宿主不代理业务正文。对累计耗尽，宿主终止整个受控进程组；hook在进程内关闭响应。这里“取消broker请求”仅指取消IPC handler，不能将其描述为宿主已代理并取消所有HTTP。宿主直接发出的Adapter/MCP请求由宿主取消。

旧Worker持有者在新fence启用后不能再申请许可；已经放行但尚未开始的客户端发送与fence切换不能跨Redis/网络事务做到完全原子。许可有效期需短于执行租约剩余安全期，发送前校验本地取消状态；已计数许可不退。新持有者必须先确认旧执行段终止或等其有界deadline结束，再建立互斥执行段；无法确认时阻断恢复。不能宣称Redis fencing能撤回已经发向平台的请求。

##### Redis时钟与故障代际
窗口使用Redis TIME并保存域级last_seen_time；检测到回拨时关闭准入，等待运维恢复而非提前清空窗口。epoch由受控发布/故障恢复流程变更，SDK不能仅靠Redis内一个可回滚key自动检测全部历史丢失。Cluster角色/主节点身份变化与守卫续期检查属于上线必需接线；存在检测间隙，Redis-only不能承诺异步故障切换零丢失写入。故障演练不能证明的保证保持未验收。

### 3.3 数据设计

execution预算继续由Task snapshot的budget和Run policy_json冻结；execution_timeout_sec保留当前合并主线契约。公共规则由独立运维配置所有者持有，不从Agent/Skill snapshot读取，不属于用户配置覆盖；若扩展规范，应描述这一边界而不改变既有冻结含义。

身份使用现有Run/Task ID与持久化逻辑调用键。新增runtime.egress_execution与runtime.egress_invocation注册表作为明确恢复协议，不在Redis缺key时盲建计数；PG不保存高频逐请求扣减。执行身份注册先提交，再启动脚本，状态转换CAS；PG与Redis非分布式事务，任何初始化不确定都阻断旧执行，首次初始化用显式一次性注册token与幂等确认。

| 表 | 字段/约束 | 索引/用途 |
|---|---|---|
| runtime.egress_execution | id UUID PK；tenant_id UUID；source_kind RUN/TASK；source_id UUID逻辑引用；policy_json jsonb无密钥；policy_hash；state；current_fence bigint；计数epoch；is_deleted=false；create_time/update_time timestamptz | 完整UNIQUE(tenant_id,source_kind,source_id)含tombstone；终态清理索引(state,update_time) |
| runtime.egress_invocation | id UUID PK；tenant_id UUID；execution_id UUID FK；operation_key text；skill_key text；state；init_token_hash；is_deleted=false；create_time/update_time timestamptz | 完整UNIQUE(execution_id,operation_key)；复合FK(tenant_id,execution_id)引用execution的对应唯一键，避免跨租户归属 |

operation_key来源于持久化tool_call_id或Task逻辑调用路径；重领复用，显式新调用另建。原始随机token不入快照或日志。task跨Owner Schema为逻辑引用；SQLAlchemy模型、迁移、baseline与schema parity同步。

后端审计字段在TASK-007沿现有运行审计所有者追加；逐attempt摘要使用(tenant_id,attempt_id)唯一键和时序索引，聚合用(target_instance_id,route_id,bucket_start)键并包含tenant可见域。跨租户物理平台聚合仅向具备全域权限的运维显示，普通租户不透出其他租户明细。具体审计表复用点必须在该TASK start核对当前模型，不能在未读表结构时虚构完成DDL。

### 3.4 接口设计

采用内部函数库接口，无新增远程HTTP限流API。

| 接口ID | 类型化接口 | 返回与错误 | 功能 |
|---|---|---|---|
| API-01 | resolve_policy(raw: object) -> SharedEgressPolicy | 非法字段/阈值直接配置错误；空配置显式disabled | FEAT-01, FEAT-02, FEAT-03 |
| API-02 | admit(identity: TrustedInvocation, request: AttemptDescriptor) -> Permit | permit含attempt_id/token/fence/valid_until；拒绝含scope/rule/kind/retry_after | FEAT-02, FEAT-03, FEAT-04, FEAT-06 |
| API-03 | finish(permit: Permit, outcome: SafeOutcome) -> None | 自身token幂等释放；不可返还累计或窗口；失败显式告警 | FEAT-04, FEAT-05, FEAT-07 |
| API-04 | bind_invocation(execution: ExecutionRef, operation_key: str) -> TrustedInvocation | PG持久注册与fence；不接受脚本自报业务身份 | FEAT-01, FEAT-03, FEAT-06 |
| API-05 | emit(event: SafeAttemptEvent) -> RecordStatus | 返回recorded/buffered/gap，日志故障不阻塞准入 | FEAT-07 |

LocalRateLimitError保留本地来源，TOTAL只停对应invocation（Run总额不足则阻断该Run其余调用）；WINDOW/INFLIGHT只拒当前调用。MeteringUnavailable区分依赖未知，不标为上游错误或可自动重试脚本。IPC消息仅准入/完成元数据，固定schema、长度限制、peer隔离、deadline与关闭协议，严禁正文和凭据。

### 3.5 质量实现方案

高频路径一次admit Lua完成多桶判定，完成另一次幂等release；不每请求写PG身份账本。滑窗清理按过期member，脚本输入与每次处理量受配置上限保护，超过安全工作量时关闭准入而非阻塞Redis。日志异步批量落库；Prometheus不含run/invocation等高基数label。性能数据待真实压测。

无秘密进入脚本或snapshot；统一logging-kit与白名单SafeAttemptEvent。attempted/allowed/send-start/completed/blocked分别记录，不拿allowed冒充实际发送；SDK无法跨网络事务保证精确发送事实，已准入未发送保守占额。重复事件按attempt+phase去重。摘要不采集header/query/body。NFR-01由B-06，NFR-02由S-04/E-04，NFR-03由E-04验证。

## 4. 部署与运维

### 4.1 部署架构

无新Deployment。Runtime/Worker镜像安装hook，沙箱挂载单invocation私有Unix socket。Redis隔离namespace和ACL，禁计数淘汰；所有Pod使用同protection_domain。Cluster同slot限制及故障接线须做真实测试。

### 4.2 发布与回滚

先通过新门禁和Linux/Cluster演练，再部署一致协议版本；禁止新旧计数内核混用同域。切换时停止领取新任务，排空旧执行后建立新域/规则；旧执行不得带空桶恢复。回滚需同样排空或阻断，不能悄悄降为内存限流。无策略路径保持原业务发送。

### 4.3 监控告警

计量依赖不可用、epoch不确定、脚本中止、spool溢出必须告警。摘要7天/汇总90天由既有维护循环清理；保留期不决定累计桶寿命。Console视图与阈值预警交互另立前端设计，当前后端接口不能替代产品视图验收。

### 4.4 数据迁移

TASK-006提供注册表模型/迁移/schema parity；TASK-007补审计字段与汇总迁移。先迁移再启用，不双写旧错误设计。既存无稳定身份的运行先排空，不猜测历史计数。

## 5. 风险与依赖

| 风险ID | 失效模式 | 措施 | 验证场景 |
|---|---|---|---|
| RISK-01 | Redis异步故障切换丢已确认计数 | 显式故障代际守卫；不能检测则不验收该保证，严禁声称Redis-only无损恢复 | E-01, B-05 |
| RISK-02 | 系统site hook未加载/自定义adapter绕开 | 镜像启动自检、支持矩阵与真实发送探针 | S-04, B-02, B-06 |
| RISK-03 | stream/旧执行段泄漏并发 | deadline、进程组监督、幂等lease释放 | E-02, B-03 |
| RISK-04 | 规则降额/加窗清空历史 | 稳定key、当前revision、历史不足保守阻断 | B-04 |
| RISK-05 | 身份或日志清理让恢复获得新额度 | PG持久身份、累计独立生命周期、缺状态阻断 | S-03, B-05 |
| RISK-06 | 全域单slot热点 | 发布前压测；现阶段不承诺吞吐能力 | S-01 |

## 6. 需求追溯矩阵

> 由 `cf_traceability.py` 从 design §2.5 + manifest 生成，不手工维护。
>
> 场景 15 / 绑定 check 15 / aspirational 0 / 未绑定 0 / 缺失 0

| 场景ID | 优先级 | 测试层级 | 执行方式 | 环境 | 输入 | 追溯 check | 执行命令 | 负责任务 | 状态 |
|--------|--------|---------|---------|------|------|-----------|---------|---------|------|
| S-01 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-796b96d5c4c0 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_01"] | TASK-002 | planned |
| S-02 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-3d00dd1c331b | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_02"] | TASK-009 | planned |
| S-03 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-a52e49c30468 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_03"] | TASK-006 | planned |
| S-04 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-9009bcf64f74 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_04"] | TASK-004 | planned |
| S-05 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-6cd04b635f20 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_05"] | TASK-005 | planned |
| E-01 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-bc23640890b2 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01"] | TASK-003 | planned |
| E-02 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-d1c20b8574a7 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_02"] | TASK-006 | planned |
| E-03 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-d3fb02c37f45 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_03"] | TASK-005 | planned |
| E-04 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-40d03deca068 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_04"] | TASK-007 | planned |
| B-01 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-b906d0200e02 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_01"] | TASK-002 | planned |
| B-02 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-717825543759 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_02"] | TASK-004 | planned |
| B-03 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-eca57f9dd900 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03"] | TASK-003 | planned |
| B-04 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-afd9f5788584 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_04"] | TASK-002 | planned |
| B-05 | P0 | integration | auto | ci | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-59e70cf85f88 | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_05"] | TASK-003 | planned |
| B-06 | P0 | contract | auto | none | packages/**, apps/agent-runtime/**, apps/agent-worker/**, migrations/** | CHK-51c11c9f1c84 | ["uv", "run", "pytest", "-q", "tests/contracts/test_shared_egress.py"] | TASK-001 | verified |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态/N/A 理由 |
|---|---|---|---|---|---|
| harness-arch#RULE-arch-001 | required | SDK嵌入既有服务，不增部署单元 | §3.2 架构设计 / RULE-arch-001 | S-01；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-data#RULE-data-001 | required | 同ownerFK、跨owner逻辑UUID、迁移与模型同构 | §3.3 数据设计 / RULE-data-001 | S-03, E-04；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-log#RULE-log-001 | required | 统一logging-kit、白名单摘要与缺口告警 | §3.5 质量实现方案 / RULE-log-001 | E-04；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-mcp#RULE-mcp-001 | required | 冻结catalog，initialize/notification/tool逐次POST | §3.2 架构设计 / RULE-mcp-001 | S-05；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-project-platform#RULE-platform-001 | required | 认证归Adapter，保留有效CSRF与Session | §3.1 方案选型 / RULE-platform-001 | S-05；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-secret#RULE-secret-001 | required | 脚本无Redis凭据，快照与日志无秘密 | §3.5 质量实现方案 / RULE-secret-001 | S-04, E-04；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-skill#RULE-skill-001 | required | READY本地缓存与argv保持，进程组监督 | §3.2 架构设计 / RULE-skill-001 | E-02；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-snapshot#RULE-snapshot-001 | required | 累计与超时冻结；公共护栏独立且不写快照 | §3.3 数据设计 / RULE-snapshot-001 | S-03, B-04；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-test#RULE-test-001 | required | 真实PG/Redis/HTTP/进程边界；独立验收环境 | §2.5 验收条件 / RULE-test-001 | S-01, S-03, E-02；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-time#RULE-time-001 | required | PG timestamptz；Redis时间回拨明确阻断 | §3.3 数据设计 / RULE-time-001 | B-01；对应任务执行真实verifier | applied（设计映射，非实现验证） |
| harness-worker#RULE-worker-001 | required | PG权威任务lease与CAS；Redis只管理限流状态 | §3.3 数据设计 / RULE-worker-001 | S-03, E-02；对应任务执行真实verifier | applied（设计映射，非实现验证） |

## 附录：术语表

execution为Run/Task；invocation为一次逻辑脚本调用；segment为一次持有执行段；attempt为一次发送准入；permit不等于业务exactly-once。
