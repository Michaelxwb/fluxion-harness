---
{"description": "MCP HTTP 认证、表单简化与本地假 MCP 的人工验收场景", "requirement": "mcp-http-auth"}
---
# MCP HTTP 认证与表单简化 验收场景

> 这是给**人工测试者**使用的验收清单，不是 E2E 代码或测试注册表。
> 场景 ID 与 Task 的 `Scenarios` 字段对应；步骤用真实用户动作描述，期望写可观察结果。

## S-01 Bearer Token 服务注册并发现工具

- **负责**：TASK-003
- **验收边界**：真实 Console + 假 MCP（bearer 模式）
- **前置**：
  - 假 MCP 以 --mcp-auth bearer --mcp-token test-mcp-token 启动
  - 管理员已登录 Console
- **步骤**：
  1. 打开 MCP 页面，点击注册
  2. 填写名称/标识/服务地址 http://<host>:<port>/mcp
  3. 认证方式选择 Bearer Token，填入 test-mcp-token
  4. 保存并发现工具
- **期望**：
  - 注册成功，连接状态为 AVAILABLE
  - 工具列表包含 platform.ping / platform.targets.list / platform.echo / platform.targets.delete / platform.fail
  - 请求头只包含 Authorization: Bearer test-mcp-token（无多余认证头）
- **实际结果**：待执行（通过/失败 + 实际观察）

## S-02 自定义请求头服务注册并发现工具

- **负责**：TASK-003
- **验收边界**：真实 Console + 假 MCP（headers 模式）
- **前置**：
  - 假 MCP 以 --mcp-auth headers --mcp-header X-Api-Key=test-key 等启动
- **步骤**：
  1. 注册 MCP，认证方式选择自定义请求头
  2. 添加 X-App-Id=sdspmcp、X-Platform-Uid=53842
  3. 密钥请求头填 X-Api-Key，密钥值填 test-key
  4. 保存并发现工具
- **期望**：
  - 发现成功，工具数与假 MCP 一致
  - 实际请求包含三个头，X-Api-Key 为原始值（无 Bearer 前缀）
- **实际结果**：待执行（通过/失败 + 实际观察）

## S-03 注册表单简化后的最小填写路径

- **负责**：TASK-003
- **验收边界**：无认证的公开 MCP
- **前置**：
  - 假 MCP 以 --mcp-auth none 启动
- **步骤**：
  1. 点击注册，观察表单
  2. 只填写名称、标识、服务地址，认证方式保持无认证
  3. 展开/收起高级设置
  4. 保存并发现工具
- **期望**：
  - 常显字段只有名称、标识、服务地址、认证方式、用户范围、启用状态
  - 传输协议以静态文案展示，不是输入框
  - 连接超时与 TTL 默认收起，不填写也能成功
- **实际结果**：待执行（通过/失败 + 实际观察）

## S-04 Runtime 执行自定义头 MCP 工具

- **负责**：TASK-002
- **验收边界**：Runtime + 冻结定义 + API-09 密钥
- **前置**：
  - Agent 已绑定自定义头 MCP，工具目录已发现
  - Runtime 可访问假 MCP
- **步骤**：
  1. 触发一次使用 platform.ping 的 Run
  2. 再触发一次使用 platform.fail 的 Run
- **期望**：
  - ping 返回 pong，工具调用审计为成功
  - fail 以工具错误结束，不伪报成功
  - egress_audit/tool_call_audit 中不出现密钥值
- **实际结果**：待执行（通过/失败 + 实际观察）

## E-01 密钥错误返回明确失败且保留旧目录

- **负责**：TASK-001
- **验收边界**：假 MCP bearer 模式 + 错误 token
- **前置**：
  - MCP 已有一次成功 Catalog
- **步骤**：
  1. 把认证方式改为 Bearer，填入错误 token
  2. 保存后点击刷新工具目录
- **期望**：
  - 连接状态 DISCOVERY_FAILED，last_discovery_error 有协议错误摘要
  - tool_catalog_json/hash/revision 保持上一成功值
- **实际结果**：待执行（通过/失败 + 实际观察）

## E-02 非法请求头配置被拒绝

- **负责**：TASK-001
- **验收边界**：CRLF / 保留头 / 密钥头重名
- **前置**：
  - 已打开注册表单
- **步骤**：
  1. 请求头名填 Host；或值填 "a\r\nX-Evil: 1"
  2. 或密钥请求头与静态头同名
  3. 提交
- **期望**：
  - 前端本地拦截并定位字段；绕过前端直接调 API 返回 MCP_CONFIG_INVALID
  - DB 不产生新记录，审计不记录成功
- **实际结果**：待执行（通过/失败 + 实际观察）

## E-03 静态头填入疑似密钥被拒绝

- **负责**：TASK-001
- **验收边界**：敏感头名校验
- **前置**：
  - 无特殊前置
- **步骤**：
  1. 自定义头模式，添加头名 X-Api-Key 并把密钥值填在"值"
  2. 提交
- **期望**：
  - 提示密钥必须填在密钥值/密钥请求头
  - 请求不落库，config_audit_log 不出现该值
- **实际结果**：待执行（通过/失败 + 实际观察）

## E-04 编辑时密钥不回显

- **负责**：TASK-003
- **验收边界**：已配置密钥的 MCP
- **前置**：
  - 已保存一个带 auth_secret 的 MCP
- **步骤**：
  1. 打开编辑弹窗
  2. 不修改密钥直接保存
  3. 再打开编辑，切换为无认证并保存
- **期望**：
  - 密钥框为空且提示"已配置，留空不修改"
  - 直接保存不改变密钥
  - 切换无认证后 auth_secret_configured 变为 false
- **实际结果**：待执行（通过/失败 + 实际观察）

## E-05 发现期间服务不可达

- **负责**：TASK-001
- **验收边界**：连接超时
- **前置**：
  - endpoint 指向未监听端口
- **步骤**：
  1. 保存并发现工具
- **期望**：
  - 注册仍成功，连接状态 UNAVAILABLE 或 DISCOVERY_FAILED
  - 错误分类为 connect/timeout，不抛出未处理异常
- **实际结果**：待执行（通过/失败 + 实际观察）

## B-01 请求头数量与长度边界

- **负责**：TASK-001
- **验收边界**：32 个头上限、单值 4 KiB、总 16 KiB
- **前置**：
  - 无特殊前置
- **步骤**：
  1. 构造 32/33 个请求头
  2. 构造超长头值
- **期望**：
  - 上限内接受，超限返回 MCP_CONFIG_INVALID
- **实际结果**：待执行（通过/失败 + 实际观察）

## B-02 旧 auth_config 数据迁移

- **负责**：TASK-004
- **验收边界**：`{"header": "X-Api-Key"}`
- **前置**：
  - 升级前存在旧结构行
- **步骤**：
  1. 执行迁移
  2. 打开详情与编辑
- **期望**：
  - 迁移为 {"secret_header": "X-Api-Key"}
  - 未知结构归 {}，页面不报错
- **实际结果**：待执行（通过/失败 + 实际观察）

## B-03 粘贴 mcpServers 配置导入

- **负责**：TASK-003
- **验收边界**：Bearer、自定义头、stdio 三种条目
- **前置**：
  - 无特殊前置
- **步骤**：
  1. 粘贴 Bearer 配置并导入
  2. 粘贴自定义头配置并导入
  3. 粘贴 command/args 配置并导入
- **期望**：
  - Bearer 条目自动选择 Bearer 并填 Token
  - 自定义头条目填入请求头，等待用户选择密钥头
  - stdio 条目提示当前不支持，不提交
- **实际结果**：待执行（通过/失败 + 实际观察）

## 场景编写约定

- 一个场景只验证一个用户可理解的目标。
- 步骤按真实操作顺序编号；期望写用户看到什么或数据变成什么。
- 需要机器执行的快速回归放在 Task 的 `### Tests`；E2E 和全量回归留在 CI。
- 场景不生成 manifest、不登记执行结果、不阻断 `task finish` 或 `task archive`。
