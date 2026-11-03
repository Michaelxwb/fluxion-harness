---
id: redis-cluster-support-scenarios
description: Redis 单机/Cluster 双模式接入验收场景唯一源
stages:
  - design
enforcement: advisory
schema: 1
requirement: redis-cluster-support
scenarios:
  - id: S-01
    title: 单机模式保持现有 Redis 行为与 key 布局
    priority: P0
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, gateway, connector, console, runtime, worker]
    boundary: 真实 console/runtime/worker/gateway/connector 子进程 + 真实 standalone Redis + 真实 PostgreSQL；不 mock Redis 行为
    preconditions:
      - REDIS_MODE=standalone
      - 真实单机 Redis 与验收独立库可用
    steps:
      - 启动 standalone Redis 与五进程真实栈
      - 运行 dedupe、session、inbound stream、connector lease、outbound、worker hint、notification pubsub 相关测试
      - 检查所有服务 /readyz 与 Redis 客户端日志
    expected:
      - 上述 Redis 相关测试全部通过，standalone 行为无回归
      - 所有服务 /readyz 返回 200，日志中没有 MovedError 或 CROSSSLOT
      - inbound、lease、outbound 的 key 名与设计 §3.4 完全一致，standalone 也保留 hash tag
    expected_refs:
      - expected: 1
        steps: [1, 2]
      - expected: 2
        steps: [1, 3]
      - expected: 3
        steps: [3]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2
      - backend design §3.4
      - backend design §3.5
    rules: [RULE-01, RULE-02, RULE-03]
    checks: []

  - id: S-02
    title: Cluster 模式入站 32 个 shard 全部可达且只消费一次
    priority: P0
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, gateway]
    boundary: 真实 Gateway 子进程 + 真实 cluster Redis + 真实 PostgreSQL；32 个 im.inbound:{inbound}:* stream
    preconditions:
      - REDIS_MODE=cluster，集群 16384 槽位全覆盖
      - INBOUND_STREAM_SHARDS=32
      - 每个 shard 至少一条已发布事件
    steps:
      - 向 im.inbound:{inbound}:0..31 各发布一条入站事件
      - 启动 Gateway 入站消费组并等待 32 条事件被处理
      - 重启一个 Gateway 实例并等待 pending 消息被 XAUTOCLAIM 接管
      - 检查 Gateway 日志、/metrics 与 Redis pending 计数
    expected:
      - 32 条事件各处理一次，external_message_id 无重复、无丢失
      - 日志中无 MovedError、CROSSSLOT 或 inbound_stream_group_unavailable
      - 32 个 key 落在同一 slot；重启后 pending 归零，XAUTOCLAIM 接管成功
      - 超过保留窗口的已确认条目被裁剪；未确认的 pending 条目不被裁剪，重启后仍可接管
    expected_refs:
      - expected: 1
        steps: [1, 2]
      - expected: 2
        steps: [2, 4]
      - expected: 3
        steps: [2, 3, 4]
      - expected: 4
        steps: [2, 3, 4]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2
      - backend design §3.4
    rules: [RULE-02, RULE-04, RULE-06, RULE-11]
    checks: []

  - id: S-03
    title: Cluster 模式 Connector 租约 Lua 原子且可安全接管
    priority: P0
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, connector]
    boundary: 两个真实 Connector 子进程 + 真实 cluster Redis + 真实 Console 快照；租约与 epoch 为真实 Redis key
    preconditions:
      - REDIS_MODE=cluster
      - 两个 Connector 实例竞争同一个 bot
      - lease TTL 与 heartbeat 使用测试可接受的小值
    steps:
      - 同时启动两个 Connector，等待租约竞争完成
      - 读取 connector:bot:{tenant:bot}:lease 与 ...:epoch
      - 续租一次后 SIGKILL 当前 owner，等待 TTL 过期与另一实例接管
      - 检查新 owner epoch 与旧 owner 的 fencing 行为
    expected:
      - 任意时刻只有一个 owner；lease 与 epoch 两个 key 同 slot
      - acquire/renew/release 的 Lua 调用无 CROSSSLOT；接管后 epoch 严格递增
      - 旧 owner 的后续写入被 fencing 拒绝，入站事件不重复
    expected_refs:
      - expected: 1
        steps: [1, 2]
      - expected: 2
        steps: [2, 3, 4]
      - expected: 3
        steps: [3, 4]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3
      - backend design §3.4
    rules: [RULE-02, RULE-06, RULE-07]
    checks: []

  - id: S-04
    title: Cluster 模式出站命令与结果跨进程可达
    priority: P1
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, gateway, connector]
    boundary: 真实 Gateway 入队 + 真实 Connector XREAD + 真实 cluster Redis + 真实 PostgreSQL
    preconditions:
      - REDIS_MODE=cluster
      - 同一 tenant 下至少两个 bot stream
    steps:
      - Gateway 向 im.outbound:{tenant}:{bot} XADD 两条命令
      - Connector 用一次 XREAD 读取该 tenant 的 bot streams
      - Connector 写 im:outbound:result:{command_id}，Gateway 轮询读取
      - 检查 cursor 与 result key
      - 向同一 bot 流注入一条早于保留窗口的命令（显式 ID），触发一次生产端裁剪，检查窗口外条目被裁、窗口内条目保留
    expected:
      - 同 tenant 的多个 bot stream 落在同一 slot，XREAD 无 CROSSSLOT
      - 两条命令只执行一次，result 可被 Gateway 读到且内容完整
      - cursor 只由当前 owner 推进，重复读取不重复发送
      - 保留窗口外的出站命令被裁剪，窗口内命令不受影响
    expected_refs:
      - expected: 1
        steps: [1, 2]
      - expected: 2
        steps: [1, 2, 3]
      - expected: 3
        steps: [3, 4]
      - expected: 4
        steps: [5]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.3
      - backend design §3.4
    rules: [RULE-02, RULE-06, RULE-11]
    checks: []

  - id: S-05
    title: Cluster 模式 PlatformSession 失效清空所有 session
    priority: P1
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, console]
    boundary: 真实 Console 子进程 + 真实 cluster Redis；多个 PlatformSession 分布在多个 slot
    preconditions:
      - REDIS_MODE=cluster
      - 同一 tenant/platform 下至少三个 actor session
    steps:
      - 创建三个 platform_session key 并写入 index Set
      - 调用 RedisPlatformSessionInvalidator.clear_platform
      - 检查 session key、index key 与重新认证行为
      - 对同一 actor 连续 acquire 触发滑动续期（TOUCH）与一次登录发布（4 key EVAL），检查 session 与 generation
    expected:
      - 三个 session key 与 index key 全部删除，无 CROSSSLOT
      - 重新认证会重新创建可用的 PlatformSession
      - 其他 tenant/platform 的 session 不受影响
      - 滑动续期与登录发布的 Lua 在集群下无 CROSSSLOT；session、index、lock、generation 同 slot
    expected_refs:
      - expected: 1
        steps: [1, 2, 3]
      - expected: 2
        steps: [2, 3]
      - expected: 3
        steps: [1, 2]
      - expected: 4
        steps: [1, 4]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
      - backend design §3.5
    rules: [RULE-02, RULE-06, RULE-08]
    checks: []

  - id: S-06
    title: Cluster 下通知、唤醒、取消、会话与令牌等 Redis 使用面可用
    priority: P1
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, gateway, console, runtime, worker]
    boundary: 真实五进程 + 真实 cluster Redis；覆盖 pub/sub、单 key、pipeline、scan 与 token 存储
    preconditions:
      - REDIS_MODE=cluster
      - 真实 PostgreSQL 与 Console/Runtime/Worker 可用
    steps:
      - 触发 WebChat 通知发布与订阅
      - 触发 worker wakeup、runtime cancel hint 与 agent runtime event-tail hint
      - 读写 Console session、artifact fetch token、MCP catalog cache 与 gateway dedupe
      - 检查各服务 /readyz、/metrics 与日志
    expected:
      - 通知与提示消息可跨进程送达；取消 hint 可被 Runtime 读到
      - Console session、fetch token、MCP cache、dedupe 读写成功，无 MOVED
      - Redis 不可用时既有降级路径仍可见，不静默吞错
    expected_refs:
      - expected: 1
        steps: [1, 2]
      - expected: 2
        steps: [3, 4]
      - expected: 3
        steps: [4]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.2
      - backend design §3.5
    rules: [RULE-02, RULE-03, RULE-07, RULE-09, RULE-10]
    checks: []

  - id: S-07
    title: 非法 cluster 配置启动失败关闭
    priority: P0
    level: contract
    exec: auto
    env: none
    components: [backend, config]
    boundary: create_redis_client 与 SharedSettings 的纯配置校验；不建立真实连接
    preconditions:
      - 不依赖真实 Redis
    steps:
      - 以 mode=standalone、合法 URL 调用工厂
      - 以 mode=cluster、db=0、单 seed 调用工厂
      - 以 mode=cluster、db=1、缺失 URL、非法 mode 调用工厂
      - 以 mode=cluster、REDIS_CLUSTER_SEEDS 提供两个额外 seed 调用工厂
      - 以 mode=cluster、非法 seed 调用工厂；再以 mode=standalone + seeds 调用工厂
    expected:
      - standalone 返回 Redis 客户端；cluster db=0 返回 RedisCluster 客户端
      - cluster db!=0、缺失 URL 或非法 mode 返回明确配置错误，不进入半可用状态
      - 多 seed 被解析并去重，主 seed 自动合并；启动节点列表包含全部可达 seed
      - 非法 seed 抛 RedisConfigError；standalone 收到 seeds 只记录 warning，仍返回 Redis
    expected_refs:
      - expected: 1
        steps: [1, 2]
      - expected: 2
        steps: [3]
      - expected: 3
        steps: [4]
      - expected: 4
        steps: [5]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.1
      - backend design §3.4
    rules: [RULE-01, RULE-04]
    checks: []

  - id: S-08
    title: key slot 契约与无直连客户端不变量
    priority: P0
    level: unit
    exec: auto
    env: none
    components: [backend, contracts]
    boundary: 纯 Python key builder + redis.crc.key_slot；不启动 Redis
    preconditions:
      - 无
    steps:
      - 计算 32 个 inbound key 的 slot
      - 计算 lease 与 epoch key 的 slot
      - 计算同一 tenant 两个 bot 出站 stream 的 slot
      - 扫描 apps/packages 中 Redis.from_url 的构造点
    expected:
      - 32 个 inbound key 只有 1 个 distinct slot
      - lease 与 epoch 两个 key 是同一 slot；同 tenant 出站 bot stream 是同一 slot
      - 业务代码中除公共工厂外没有 Redis.from_url 直接构造
    expected_refs:
      - expected: 1
        steps: [1]
      - expected: 2
        steps: [2, 3]
      - expected: 3
        steps: [4]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.4
    rules: [RULE-02, RULE-05, RULE-06]
    checks: []

  - id: E-01
    title: 3M3R 节点故障与 reshard 后自动恢复
    priority: P0
    level: integration
    exec: deferred
    env: staging
    owner: 发布负责人
    target_phase: review
    defer_reason: 需要 3M3R staging 与节点重启/reshard 权限；本地单节点 cluster 无法验证 MOVED/ASK 与 failover
    components: [backend, redis, gateway, connector]
    boundary: 真实 3 主 3 从 Redis Cluster + 真实 Gateway/Connector + 真实 PostgreSQL
    preconditions:
      - staging 3M3R 集群，16384 槽位全覆盖
      - 可安全 kill 一个 master 并触发 failover/reshard
    steps:
      - 运行 S-02 入站与 S-03 租约的代表性流量
      - kill 一个 master，等待 replica 提升
      - 对至少一个 slot 执行 reshard
      - 继续投递入站与出站流量，检查客户端重连、MOVED/ASK 处理与消息结果
      - 停止 REDIS_URL 主 seed，仅保留 REDIS_CLUSTER_SEEDS 中另一个 seed，重启应用并检查拓扑发现
    expected:
      - failover 与 reshard 后客户端在既有重试窗口内恢复，无需重启进程
      - 入站消息至少一次、无重复业务副作用；租约仍保持单 owner
      - 日志/指标能看到 MOVED/ASK 处理与恢复，不出现永久 MovedError
      - 主 seed 不可达时，应用通过其他 seed 完成拓扑发现并正常启动
    expected_refs:
      - expected: 1
        steps: [2, 3, 4]
      - expected: 2
        steps: [1, 4]
      - expected: 3
        steps: [2, 3, 4]
      - expected: 4
        steps: [5]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
      - backend design §4.2
    rules: [RULE-04, RULE-06, RULE-07]
    checks: []

  - id: E-02
    title: Cluster 不可用时按既有语义降级
    priority: P1
    level: integration
    exec: auto
    env: local-api
    components: [backend, redis, gateway, console, runtime, worker]
    boundary: 真实五进程 + 真实 cluster Redis；运行中停止集群
    preconditions:
      - REDIS_MODE=cluster
      - Gateway 配置了 IM/webhook 路由，stream_reliant=true
    steps:
      - 启动五进程并完成一次正常请求
      - 停止 cluster 所有节点
      - 检查 Gateway /readyz、入站消费、worker hint、console session 与运行时 hint
      - 恢复集群后再次发起请求
    expected:
      - stream_reliant 的 Gateway /readyz 返回 503，且不伪造入站成功
      - Runtime/Worker 的 PG 主链路继续；wakeup/cancel/notification hint 显式降级并记录指标
      - 集群恢复后服务自动恢复，不需要重启进程
    expected_refs:
      - expected: 1
        steps: [1, 2, 3]
      - expected: 2
        steps: [2, 3]
      - expected: 3
        steps: [2, 4]
    trigger_paths: []
    smoke: false
    design_refs:
      - backend design §3.5
      - backend design §4.2
    rules: [RULE-03, RULE-07, RULE-10]
    checks: []

---
