# Redis key 命名空间切换

Agent 服务写入的 Redis key 与 pub/sub channel 统一以 `muad:` 开头。Session 的索引、锁、generation 与失效匹配同步使用新前缀；Redis Cluster hash tag 保持原布局。

部署时停止旧版生产者与消费者，处理完旧流中的 pending 消息和待投递命令，再同步升级 Console、Runtime、Worker、Gateway 与 Connector。不要新旧版本混跑：租约、去重、取消提示及回复上下文分属两个命名空间。

本次不自动迁移旧 key，也不回读旧前缀。旧 WebChat 会话需要重新登录，平台 Session 会按需重新认证。旧 key 按原 TTL 过期；无 TTL 的流、索引及 epoch 只在确认不再需要后由运维清理，禁止对共享 Redis 执行 FLUSHDB。
