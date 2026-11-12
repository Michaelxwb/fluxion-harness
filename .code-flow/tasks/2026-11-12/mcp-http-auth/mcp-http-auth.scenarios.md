---
id: scenarios-mcp-http-auth
description: MCP HTTP 认证、表单简化与本地假 MCP 的人工验收场景
---

# MCP HTTP 认证与表单简化 验收场景

```yaml
schema: 1
requirement: mcp-http-auth
scenarios:
  - id: S-01
    title: Bearer Token 服务注册并发现工具
    level: e2e
    boundary: 真实 Console + 假 MCP（bearer 模式）
    preconditions:
      - 假 MCP 以 --mcp-auth bearer --mcp-token test-mcp-token 启动
      - 管理员已登录 Console
    steps:
      - 打开 MCP 页面，点击注册
      - 填写名称/标识/服务地址 http://<host>:<port>/mcp
      - 认证方式选择 Bearer Token，填入 test-mcp-token
      - 保存并发现工具
    expected:
      - 注册成功，连接状态为 AVAILABLE
      - 工具列表包含 platform.ping / platform.targets.list / platform.echo / platform.targets.delete / platform.fail
      - 请求头只包含 Authorization: Bearer test-mcp-token（无多余认证头）
    exec: manual
    owner: TASK-003

  - id: S-02
    title: 自定义请求头服务注册并发现工具
    level: e2e
    boundary: 真实 Console + 假 MCP（headers 模式）
    preconditions:
      - 假 MCP 以 --mcp-auth headers --mcp-header X-Api-Key=test-key 等启动
    steps:
      - 注册 MCP，认证方式选择自定义请求头
      - 添加 X-App-Id=sdspmcp、X-Platform-Uid=53842
      - 密钥请求头填 X-Api-Key，密钥值填 test-key
      - 保存并发现工具
    expected:
      - 发现成功，工具数与假 MCP 一致
      - 实际请求包含三个头，X-Api-Key 为原始值（无 Bearer 前缀）
    exec: manual
    owner: TASK-003

  - id: S-03
    title: 注册表单简化后的最小填写路径
    level: manual
    boundary: 无认证的公开 MCP
    preconditions:
      - 假 MCP 以 --mcp-auth none 启动
    steps:
      - 点击注册，观察表单
      - 只填写名称、标识、服务地址，认证方式保持无认证
      - 展开/收起高级设置
      - 保存并发现工具
    expected:
      - 常显字段只有名称、标识、服务地址、认证方式、用户范围、启用状态
      - 传输协议以静态文案展示，不是输入框
      - 连接超时与 TTL 默认收起，不填写也能成功
    exec: manual
    owner: TASK-003

  - id: S-04
    title: Runtime 执行自定义头 MCP 工具
    level: integration
    boundary: Runtime + 冻结定义 + API-09 密钥
    preconditions:
      - Agent 已绑定自定义头 MCP，工具目录已发现
      - Runtime 可访问假 MCP
    steps:
      - 触发一次使用 platform.ping 的 Run
      - 再触发一次使用 platform.fail 的 Run
    expected:
      - ping 返回 pong，工具调用审计为成功
      - fail 以工具错误结束，不伪报成功
      - egress_audit/tool_call_audit 中不出现密钥值
    exec: manual
    owner: TASK-002

  - id: E-01
    title: 密钥错误返回明确失败且保留旧目录
    level: integration
    boundary: 假 MCP bearer 模式 + 错误 token
    preconditions:
      - MCP 已有一次成功 Catalog
    steps:
      - 把认证方式改为 Bearer，填入错误 token
      - 保存后点击刷新工具目录
    expected:
      - 连接状态 DISCOVERY_FAILED，last_discovery_error 有协议错误摘要
      - tool_catalog_json/hash/revision 保持上一成功值
    exec: manual
    owner: TASK-001

  - id: E-02
    title: 非法请求头配置被拒绝
    level: integration
    boundary: CRLF / 保留头 / 密钥头重名
    preconditions:
      - 已打开注册表单
    steps:
      - 请求头名填 Host；或值填 "a\r\nX-Evil: 1"
      - 或密钥请求头与静态头同名
      - 提交
    expected:
      - 前端本地拦截并定位字段；绕过前端直接调 API 返回 MCP_CONFIG_INVALID
      - DB 不产生新记录，审计不记录成功
    exec: manual
    owner: TASK-001

  - id: E-03
    title: 静态头填入疑似密钥被拒绝
    level: integration
    boundary: 敏感头名校验
    preconditions: []
    steps:
      - 自定义头模式，添加头名 X-Api-Key 并把密钥值填在"值"
      - 提交
    expected:
      - 提示密钥必须填在密钥值/密钥请求头
      - 请求不落库，config_audit_log 不出现该值
    exec: manual
    owner: TASK-001

  - id: E-04
    title: 编辑时密钥不回显
    level: component
    boundary: 已配置密钥的 MCP
    preconditions:
      - 已保存一个带 auth_secret 的 MCP
    steps:
      - 打开编辑弹窗
      - 不修改密钥直接保存
      - 再打开编辑，切换为无认证并保存
    expected:
      - 密钥框为空且提示"已配置，留空不修改"
      - 直接保存不改变密钥
      - 切换无认证后 auth_secret_configured 变为 false
    exec: manual
    owner: TASK-003

  - id: E-05
    title: 发现期间服务不可达
    level: integration
    boundary: 连接超时
    preconditions:
      - endpoint 指向未监听端口
    steps:
      - 保存并发现工具
    expected:
      - 注册仍成功，连接状态 UNAVAILABLE 或 DISCOVERY_FAILED
      - 错误分类为 connect/timeout，不抛出未处理异常
    exec: manual
    owner: TASK-001

  - id: B-01
    title: 请求头数量与长度边界
    level: unit
    boundary: 32 个头上限、单值 4 KiB、总 16 KiB
    preconditions: []
    steps:
      - 构造 32/33 个请求头
      - 构造超长头值
    expected:
      - 上限内接受，超限返回 MCP_CONFIG_INVALID
    exec: manual
    owner: TASK-001

  - id: B-02
    title: 旧 auth_config 数据迁移
    level: integration
    boundary: {"header": "X-Api-Key"}
    preconditions:
      - 升级前存在旧结构行
    steps:
      - 执行迁移
      - 打开详情与编辑
    expected:
      - 迁移为 {"secret_header": "X-Api-Key"}
      - 未知结构归 {}，页面不报错
    exec: manual
    owner: TASK-004

  - id: B-03
    title: 粘贴 mcpServers 配置导入
    level: component
    boundary: Bearer、自定义头、stdio 三种条目
    preconditions: []
    steps:
      - 粘贴 Bearer 配置并导入
      - 粘贴自定义头配置并导入
      - 粘贴 command/args 配置并导入
    expected:
      - Bearer 条目自动选择 Bearer 并填 Token
      - 自定义头条目填入请求头，等待用户选择密钥头
      - stdio 条目提示当前不支持，不提交
    exec: manual
    owner: TASK-003
```
