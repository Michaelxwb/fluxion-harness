# MSSP / MSSW 日常调用与 Redis 登录态

## 运行方式

复用现有 Python HMAC 登录实现，只注册一个 MSS Adapter（mss-session），MSSP/MSSW 平台实例都选择它。Branch Tag 与业务 CSRF 请求头在平台配置 JSON 中指定；用户/账号、平台实例与缓存隔离。模块随 Runtime/Worker 部署，无独立微服务。

平台公共配置仍用 JSON，Base URL 在 resolver_config.base_url；AK/SK 只在用户或共享凭据表中保存。保存配置不等于实际登录成功。认证、权限或业务错误在 Skill 真正执行时返回。

## 两小时滑动过期

Redis 登录态的空闲 TTL 固定为 7200 秒。每次执行声明了平台依赖的 Skill（execute_skill 或 run_skill_script）时，受信执行上下文先解析该用户/共享账号的当前凭据并 acquire 对应 Session：缓存有效则重置 TTL，缺失/失效则登录并缓存。

例如 10:00 执行后空闲过期点为 12:00；11:30 再执行同平台 Skill，过期点移至 13:30。普通聊天、检索/读取 Skill 说明、访问配置页都不续期。后台 Worker 实际开始执行或重试 Skill 时也续期，不是提交任务时提前续期。

每次调用 `session-manager get-state` 会重新 acquire 并续期。Skill 直接发出的业务请求不会自动触发续期；长时间运行的 Skill 需要在再次获取登录态时续期。未声明的平台或其他账号不会被当前执行续期。

这是客户端缓存的空闲时间，不会延长 MSS 服务端 JWT/Cookie 自身的有效期。平台提前失效时仍须重新认证。原 session_ttl_seconds 影响独立 Adapter 初始 Session 对象，Redis 管理器的空闲 TTL 统一使用 7200 秒，不受旧配置的 900 秒限制。

缓存 key 包含租户、平台、用户/共享账号、凭据指纹、Adapter 版本及公共配置摘要。只有派生 Cookie 等进入 Redis，AK/SK 不存入 Session；修改配置或凭据会改用新缓存键。平台级失效通过索引清理和 generation 防止旧登录请求重新写回缓存。

## Skill 调用

Skill 在 SKILL.md frontmatter 声明平台实例 key，例如 `platforms: [mssp]`；旧包也可在 `muad.skill.json` 声明。导入后声明随 Artifact 保存，已导入的旧 Artifact 需重新上传。

Skill 使用旧命令 `session-manager get-state --skill-name <当前 Skill 名>`，从返回 JSON 的 `sessionStateFile` 读取 `platforms.<key>.cookies` 和 `requestProfile`。名称须与本次执行的 SKILL.md 名称一致，不能用于切换其他 Skill 或用户。命令返回的文件仅限本次执行，Linux 使用内存文件，结束后关闭；执行环境不是不同 Unix 用户之间的安全沙箱，同 UID 进程不应被视为互相隔离。

Runtime/Worker 从可信 Run/Task 构造租户、用户和允许的平台列表；命令使用当前上下文重取用户/共享凭据并经 Redis acquire。登录态文件包含 Cookie，Skill 自行发请求，须自行避免把 Cookie 写入日志、结果或持久文件。业务 HTTP 不再由平台代理，代理侧的请求限制、出网审计与自动重试不适用于 Skill 直接请求；Skill 自行处理鉴权失效和重试。

`requestProfile` 中包含平台 Base URL、可选 Host/Referer、时区和业务公共请求头。MSS 适配器配置 JSON 的 `business_headers` 可填写非密钥的业务固定头；认证材料仍只放用户或共享凭据，不得放入公共头。测试用的 monitor Skill 从登录态 profile 获取这些配置，业务路径保留在 Skill 包中。旧 Skill 如果仍读取自身硬编码地址和请求头，需要逐个检查是否与平台配置一致。

## 配置与启动条件

Runtime/Worker 需要可用的 REDIS_URL、CONSOLE_PLATFORM_URL、INTERNAL_SERVICE_TOKEN。内部凭据解析携带 X-Internal-Service，租户与执行用户取自平台上下文。未配置 Redis 或服务令牌时平台 Skill 明确失败，不回退到模型自报身份或共享临时文件。

平台 JSON 常见项：branch_tag、csrf_header、session_endpoint、health_endpoint、csrf_enabled、csrf_endpoint、host_header、referer、timezone、business_headers、timeout_seconds、ca_bundle。支持内网 HTTP；登录组件的 HTTPS 默认校验证书，自签 CA 用 ca_bundle 指定；Skill 自行请求时的 TLS 校验由 Skill 自身实现。MSSW 业务 CSRF 为 x-csrf-token，MSSP 为 X-CSRFToken，可从 Cookie 派生或由业务公共头配置，不默认发送 Origin。

## 本地验证边界

自动测试使用独立测试 HTTP 服务、随机测试凭据、实际 Redis 和实际 Skill 子进程。Redis 使用本次测试独有租户/平台 key，不清空共享 Redis、不操作业务任务数据。空闲到期测试缩短 key 剩余 TTL 模拟两小时后的状态，同时检查正式续期值为 7200 秒。

```sh
uv run --extra dev pytest -q tests/sdk/test_mss_sessions.py tests/sdk/test_platform_sliding_session.py
```

覆盖登录/失败、双平台协议、用户隔离、并发冷登录、滑动续期、空闲过期、平台提前撤销、凭据轮换、失效清理与正在登录的竞争、同步 Skill 工具入口、Worker 入口和真实子进程业务请求。

测试服务用于协议验证，不代表真实 MSSP/MSSW 已联调。内部实际接口、CA/Host 和账号权限仍需在目标环境验收。

## 统一 Adapter 的平台配置

界面只有“mss-session”，不再提供 MSSP/MSSW 两个 Adapter 选项。平台实例仍可以分别命名为 MSSP 和 MSSW，各自填写 Base URL、AK/SK，并隔离缓存。默认公共参数为 MSSP 协议头；MSSW 实例在配置 JSON 中设置：

```json
{
  "branch_tag": "MSSW-ADAPTER",
  "csrf_header": "x-csrf-token"
}
```

MSSP 实例可使用默认值，或显式设置 branch_tag 为 MSSP-ADAPTER、csrf_header 为 X-CSRFToken。其余登录路径、Host 等按环境填写。已有使用旧 Adapter key 的本地平台记录需改为 mss-session，并带上相应协议头参数；不需要在 SDK 中保留旧 Adapter 别名。

## JSON 字段用途与登录请求

平台 JSON（公共参数，不得填写秘密）：`session_endpoint` 为登录路径，`health_endpoint` 为校验路径，`host_header` 为 Host 头，`login_headers` 为公共登录请求头，`login_body` 为显式公共请求体；Base URL 仍填写在平台访问地址中。

用户/共享凭据 JSON 的顶层只接受 `ak`、`sk`、`headers`、`body`。未知顶层字段明确拒绝；body 内可自由定义字段和嵌套内容。与旧 MSSClaw 同样，AK 放在 Authorization 的 Access 中，SK 只用于本地签名，不自动发送原文。连接配置不能放进用户 body 来改变请求目的地。

默认 `auth_mode: "hmac"`，只填 AK/SK 时发送空请求体：
```json
{"ak":"example-ak","sk":"example-sk"}
```
需要额外请求头或请求体时显式配置：
```json
{"ak":"example-ak","sk":"example-sk","headers":{"X-Account":"example"},"body":{"region":"example","options":{"scopes":["read"]}}}
```

请求体仅合并平台 `login_body` 与凭据 `body`，同名键由凭据覆盖，嵌套对象整体替换；两者均未配置时发送空字符串。配置 `{}` 则显式发送空 JSON 对象。非空请求体摘要参与 HMAC 签名；协议生成的认证头优先于自定义同名头。`auth_mode: "json"` 不生成 HMAC Authorization，可在凭据 headers/body 中按接口协议填写认证内容；登录响应仍须包含业务码与登录 Cookie。

登录参数只用于获取 Session，不自动注入业务请求。Redis 仍负责存取、7200 秒滑动过期、并发登录收敛、账号隔离与失效防复活。Adapter 版本升为 2，使旧版本缓存不再命中；AK/SK 和私密登录 headers/body 不进入 Session 缓存。真实 MSS 环境仍须联调。
