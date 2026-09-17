# MSS 智能服务交付平台 - 新版项目骨架

该骨架按 V1.3 最新设计基线初始化，核心后台服务固定为：

```text
muad-console-platform
muad-agent-runtime
muad-agent-worker
muad-im-gateway
```

外部依赖：

```text
PostgreSQL
Redis
NFS / 企业共享文件存储（Kubernetes RWX PVC）
Secret Provider
OpenTelemetry Backend
```

## 一、框架层固定能力

### 1. 日志模块

所有服务统一依赖：

```text
packages/logging-kit
```

业务服务只需要：

```python
configure_logging('muad-agent-runtime')
```

部署侧只配置根目录：

```bash
LOG_DIR=/var/log/muad
```

落盘结构：

```text
${LOG_DIR}/
├── muad-console-platform/
│   └── 2026-09-17.log
├── muad-agent-runtime/
│   └── 2026-09-17.log
├── muad-agent-worker/
│   └── 2026-09-17.log
└── muad-im-gateway/
    └── 2026-09-17.log
```

日志统一 JSON，自动注入：

```text
timestamp
service
level
logger
trace_id
request_id
message
```

业务模块禁止自行创建 FileHandler/RotatingFileHandler。

### 2. 统一 API 响应

JSON REST API 统一返回：

```json
{
  "code": "0",
  "msg": "成功",
  "data": {},
  "trace_id": "...",
  "request_id": "...",
  "timestamp": "..."
}
```

业务错误只写 code：

```python
raise AppError(ErrorCode.AGENT_NOT_FOUND)
```

禁止在业务代码中直接写用户可见错误信息：

```python
# 禁止
raise HTTPException(404, 'Agent 不存在')
raise AppError('AGENT_NOT_FOUND', 'Agent 不存在')
```

HTTP status + 中英文 msg 统一由：

```text
config/api-messages.yaml
```

映射。

SSE、文件下载等非 JSON 协议按各自 Contract 返回，不强套 JSON Envelope。

### 3. 后端中英文

优先读取：

```text
X-Locale: zh-CN
X-Locale: en-US
```

没有 `X-Locale` 时读取 `Accept-Language`。

新增业务错误只增加配置：

```yaml
ORDER_NOT_FOUND:
  http_status: 404
  messages:
    zh-CN: 订单不存在
    en-US: Order not found
```

代码仍然只写：

```python
raise AppError('ORDER_NOT_FOUND')
```

### 4. 前端中英文

Console 使用 `react-i18next`，统一 locale 文件：

```text
apps/console-platform/frontend/src/locales/
├── zh-CN.json
└── en-US.json
```

页面使用：

```tsx
t('agent.fields.name')
```

切换语言后框架自动：

1. 切换页面文案；
2. 保存 localStorage；
3. API Client 自动发送 `X-Locale`；
4. 后端错误 `msg` 使用相同语言。

新增业务只补 locale key，不重新实现语言切换逻辑。

### 5. Skill Artifact

V1 不额外部署 MinIO/S3：

```text
NFS / 企业文件存储
 -> RWX PVC
 -> /mnt/muad-artifacts
```

Runtime / Worker：

```text
/mnt/muad-artifacts        # 权威 Artifact 源
/var/cache/muad/skills     # emptyDir，本地执行缓存
```

每次执行都经过：

```text
SkillArtifactCache.ensure
 -> memory/local READY hit
 -> hit: 直接执行，不访问 NFS
 -> miss: 从 NFS PVC 复制
 -> checksum
 -> 临时目录解压
 -> atomic rename
 -> READY
 -> SkillExecutor
```

Skill 不直接从 NFS 目录执行。

## 二、目录结构

```text
MSS智能服务交付平台-新版项目骨架/
├── apps/
│   ├── console-platform/
│   │   ├── backend/
│   │   └── frontend/
│   ├── agent-runtime/
│   ├── agent-worker/
│   └── im-gateway/
├── packages/
│   ├── logging-kit/
│   ├── api-kit/
│   ├── contracts/
│   ├── common/
│   ├── artifact-store/
│   ├── agent-core/
│   ├── skill-sdk/
│   └── platform-sdk/
├── config/
├── migrations/
├── deploy/k8s/base/
├── docs/
├── scripts/
└── tests/
```

## 三、本地运行

推荐 Python 3.12+、Node 20+、uv。

```bash
cp .env.example .env
uv sync --all-packages
```

Console Platform：

```bash
uv run uvicorn muad_console_platform.main:app \
  --app-dir apps/console-platform/backend/src \
  --reload --port 8000
```

Runtime：

```bash
uv run uvicorn muad_agent_runtime.main:app \
  --app-dir apps/agent-runtime/src \
  --reload --port 8001
```

Worker Admin API：

```bash
uv run uvicorn muad_agent_worker.main:app \
  --app-dir apps/agent-worker/src \
  --reload --port 8002
```

Gateway：

```bash
uv run uvicorn muad_im_gateway.main:app \
  --app-dir apps/im-gateway/src \
  --reload --port 8003
```

前端：

```bash
cd apps/console-platform/frontend
npm install
npm run dev
```

## 四、检查

```bash
make check
```

包括：

- Python compile；
- 日志按服务/日期落盘；
- API code -> 中英文 msg；
- locale key 中英文一致；
- Skill Artifact local cache；
- 错误信息硬编码静态扫描。
