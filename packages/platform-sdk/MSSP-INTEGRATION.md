# MSSP / MSSW 日常调用与 Redis 登录态

## 运行方式

复用现有 Python HMAC 登录实现，只注册一个 MSS AK/SK Adapter（mss-hmac-session），MSSP/MSSW 平台实例都选择它。Branch Tag 与业务 CSRF 请求头在平台配置 JSON 中指定；用户/账号、平台实例与缓存隔离。模块随 Runtime/Worker 部署，无独立微服务。

平台公共配置仍用 JSON，Base URL 在 resolver_config.base_url；AK/SK 只在用户或共享凭据表中保存。保存配置不等于实际登录成功。认证、权限或业务错误在 Skill 真正执行时返回。

## 两小时滑动过期

Redis 登录态的空闲 TTL 固定为 7200 秒。每次执行声明了平台依赖的 Skill（execute_skill 或 run_skill_script）时，受信执行上下文先解析该用户/共享账号的当前凭据并 acquire 对应 Session：缓存有效则重置 TTL，缺失/失效则登录并缓存。

例如 10:00 执行后空闲过期点为 12:00；11:30 再执行同平台 Skill，过期点移至 13:30。普通聊天、检索/读取 Skill 说明、访问配置页都不续期。后台 Worker 实际开始执行或重试 Skill 时也续期，不是提交任务时提前续期。

SDK 内该平台的业务请求同样经过 acquire，执行期间持续调用会继续续期。未声明/未使用的平台或其他账号不会被当前执行续期。长时间无平台访问的纯计算不会运行定时保活。

这是客户端缓存的空闲时间，不会延长 MSS 服务端 JWT/Cookie 自身的有效期。平台提前失效时仍须重新认证。原 session_ttl_seconds 影响独立 Adapter 初始 Session 对象，Redis 管理器的空闲 TTL 统一使用 7200 秒，不受旧配置的 900 秒限制。

缓存 key 包含租户、平台、用户/共享账号、凭据指纹、Adapter 版本及公共配置摘要。只有派生 Cookie 等进入 Redis，AK/SK 不存入 Session；修改配置或凭据会改用新缓存键。平台级失效通过索引清理和 generation 防止旧登录请求重新写回缓存。

## Skill 调用

在 SKILL.md frontmatter 中声明平台实例的 key（不是 Adapter 的 key）：

```yaml
---
name: query-mss-events
description: 查询 MSS 业务事件
platforms: [mssp]
---
```

脚本保持 JSON stdin/stdout 契约，只需使用共享 SDK 发业务请求：

```python
import json
from muad_skill_sdk import platform_request

result = platform_request("mssp", "GET", "/实际事件查询接口路径", {"company_id": company_id})
print(json.dumps(result, ensure_ascii=False))
```

调用 mssw 时使用对应平台实例 key。路径及参数需按实际业务接口合同填写，上例路径仅为说明。Skill 不负责获取 AK/SK、保存 Cookie、设置用户身份或直接连接 Redis。

Runtime 和 Worker 从受信 Run/Task 绑定 tenant/user/execution，传到公共脚本执行器。脚本通过仅本次执行有效的本机 Unix Socket 请求，身份和平台允许列表固定在父进程中，不能由脚本参数切换。Socket 位于私有临时目录，执行结束即关闭。传给子进程的是 Socket 路径，不是 Cookie 或服务令牌。

后台任务冻结 Skill 的 platforms 列表，不把 AK/SK 或 Session 放入 Task 快照。已排队且没有平台声明的旧任务不会自动获得新平台能力，应重新以声明正确的 Skill 提交。

每次平台调用重新解析凭据与启用状态。403 业务权限失败不自动重放；明确鉴权失败时重新登录一次，仅 GET 允许自动重试，写操作返回 PLATFORM_AUTH_RETRY_REQUIRED，由业务幂等策略决定后续动作。调用审计只记平台/路径/方法/错误码，排除请求头、凭据及响应正文。

## 配置与启动条件

Runtime/Worker 需要可用的 REDIS_URL、CONSOLE_PLATFORM_URL、INTERNAL_SERVICE_TOKEN。内部凭据解析携带 X-Internal-Service，租户与执行用户取自平台上下文。未配置 Redis 或服务令牌时平台 Skill 明确失败，不回退到模型自报身份或共享临时文件。

平台 JSON 常见项：branch_tag、csrf_header、session_endpoint、health_endpoint、csrf_enabled、csrf_endpoint、host_header、referer、timezone、timeout_seconds、ca_bundle。支持内网 HTTP；HTTPS 默认校验证书，自签 CA 用 ca_bundle 指定。MSSW 业务 CSRF 为 x-csrf-token，MSSP 为 X-CSRFToken，均由登录 Cookie 派生，不默认发送 Origin。

## 本地验证边界

自动测试使用独立测试 HTTP 服务、随机测试凭据、实际 Redis 和实际 Skill 子进程。Redis 使用本次测试独有租户/平台 key，不清空共享 Redis、不操作业务任务数据。空闲到期测试缩短 key 剩余 TTL 模拟两小时后的状态，同时检查正式续期值为 7200 秒。

```sh
uv run --extra dev pytest -q tests/sdk/test_mss_sessions.py tests/sdk/test_platform_sliding_session.py
```

覆盖登录/失败、双平台协议、用户隔离、并发冷登录、滑动续期、空闲过期、平台提前撤销、凭据轮换、失效清理与正在登录的竞争、同步 Skill 工具入口、Worker 入口和真实子进程业务请求。

测试服务用于协议验证，不代表真实 MSSP/MSSW 已联调。内部实际接口、CA/Host 和账号权限仍需在目标环境验收。

## 统一 Adapter 的平台配置

界面只有“MSS AK/SK”，不再提供 MSSP/MSSW 两个 Adapter 选项。平台实例仍可以分别命名为 MSSP 和 MSSW，各自填写 Base URL、AK/SK，并隔离缓存。默认公共参数为 MSSP 协议头；MSSW 实例在配置 JSON 中设置：

```json
{
  "branch_tag": "MSSW-ADAPTER",
  "csrf_header": "x-csrf-token"
}
```

MSSP 实例可使用默认值，或显式设置 branch_tag 为 MSSP-ADAPTER、csrf_header 为 X-CSRFToken。其余登录路径、Host 等按环境填写。已有使用旧 Adapter key 的本地平台记录需改为 mss-hmac-session，并带上相应协议头参数；不需要在 SDK 中保留旧 Adapter 别名。
