# 通用智能服务执行框架

当前版本：**v0.3.0**

这是一个面向开源复用的 Agent + ServiceExecution 框架骨架。框架核心与具体业务项目解耦，业务通过 `Integration + Provider/Adapter` 接入。

## 当前架构

```text
Channel
  ↓
channel-gateway
  ↓
agent-runtime
  ├── 普通问答 / 短时只读 Capability
  └── ExecutionService
          ↓
      PostgreSQL
          ↓
        worker
          ├── Agent Step
          ├── Capability Step
          └── System Step
```

控制面：

```text
Console
→ platform-api
→ Agent / Service / Capability / Knowledge / User / Execution 管理
```

生产常驻应用镜像保持四个：

```text
platform-api
agent-runtime
worker
channel-gateway
```

PostgreSQL、Redis、Object Store、Secret Provider、OTel 为外部基础设施。

## v0.3.0 关键基线

### 数据库统一字段

所有 Framework 自建表统一继承：

```text
is_deleted  BOOLEAN NOT NULL DEFAULT FALSE
create_time TIMESTAMPTZ NOT NULL DEFAULT now()
update_time TIMESTAMPTZ NOT NULL DEFAULT now()
```

代码通过 `SoftDeleteTimestampMixin` 强制落实，并有 Architecture Test 检查全部 ORM Table。

普通 Repository 查询默认过滤 `is_deleted=false`；删除默认逻辑删除。

LangGraph Checkpointer 等第三方库自管理表不改 Schema，避免破坏第三方升级兼容。

### 后端统一封装

普通 JSON API 统一：

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req-id",
  "timestamp": "ISO-8601"
}
```

同时统一：

```text
AppError
RequestValidationError
HTTPException
Unknown Exception
request_id
JSON stdout logging
sensitive-field redaction
```

### 前端

Console 唯一通用 UI 组件库：

```text
@douyinfe/semi-ui
```

标准列表布局：

```text
左上：新增/主要操作
右上：过滤/筛选/搜索/刷新
中间：Semi Table
右下：统一 Pagination
最后一列：行操作
```

所有普通资源列表复用 `StandardListPage`。

### Tool / Workspace / Sandbox

```text
filesystem.read
filesystem.write
filesystem.edit
filesystem.glob
filesystem.grep
shell.execute
```

统一作为 Sandbox-backed Capability，不新增 Tool Runtime。

LocalSandbox 仅用于开发/受信环境；生产启用 shell/不可信代码前必须实现真正隔离的 SandboxExecutor。

## 目录

```text
apps/
  platform_api/
  agent_runtime/
  worker/
  channel_gateway/

framework/
  domain/
  contracts/
  agent_core/
  execution/
  capability/
  knowledge/
  auth/
  channel/
  memory/
  workspace/
  observability/
  web/

adapters/
  postgres/
  sandbox/

integrations/
  demo/
  mss/

frontend/
  console/

migrations/
tests/
docs/
.code-flow/
```

## 设计文档

`docs/` 已全部中文化，并按照：

- `design-full.md`
- `design-lite.md`
- `design-frontend.md`

重新完成模块详细设计。

cf-task:align 的本轮产物位于：

```text
.code-flow/tasks/2026-09-10/framework-v0.3.0/
```

## 本地开发

安装 Python：

```bash
python -m pip install -e '.[dev,agent,redis]'
```

执行测试：

```bash
pytest -q
```

数据库迁移：

```bash
alembic upgrade head
```

启动 Platform API：

```bash
uvicorn apps.platform_api.main:app --reload --port 8000
```

Agent Runtime：

```bash
uvicorn apps.agent_runtime.main:app --reload --port 8001
```

Worker：

```bash
python -m apps.worker.main
```

Channel Gateway：

```bash
cd apps/channel_gateway
npm install
npm run dev
```

Console：

```bash
cd frontend/console
npm install
npm run dev
```

## 当前实现边界

该版本是**架构对齐后的 Framework 基线代码**，不是对任意生产场景作“全部业务功能已经实现”的承诺。

当前重点已经落地：

```text
领域/Contract 边界
统一数据库 ORM Schema
Alembic 初始 Schema
Soft Delete/Timestamp Gate
Capability/Sandbox SPI
统一 Response/Logging
Semi Design / StandardListPage
Platform API Agent CRUD 基线
Architecture Tests
```

Worker 的真实生产级 Step State Machine、完整 LangGraph AgentExecutor、具体 Project Integration、生产 Sandbox、WeCom Adapter 等仍应按 `docs/02-模块设计` 中的验收场景继续实现和验证。
