# 新版内网 K8s 部署配置

适用分支：`feat/intranet-build`。5 个服务镜像 + 5 个独立 Helm chart。

## 1. 前置资源

```bash
kubectl create namespace muad --dry-run=client -o yaml | kubectl apply -f -
```

### 1.1 共享 PVC

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: muad-artifacts-rwx
  namespace: muad
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: muad-nfs-public-provisoner
  resources:
    requests:
      storage: 100Gi
```

```bash
kubectl apply -f muad-artifacts-rwx.yaml
kubectl -n muad get pvc muad-artifacts-rwx
```

5 个服务都挂载：

```text
PV: muad-artifacts-rwx
MountPath: /mnt/muad-artifacts
```

### 1.2 Secret

```bash
export INTERNAL_TOKEN="$(openssl rand -hex 32)"

kubectl -n muad create secret generic muad-external \
  --from-literal=DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad' \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=INTERNAL_SERVICE_TOKEN="$INTERNAL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n muad create secret generic muad-connector-external \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=INTERNAL_SERVICE_TOKEN="$INTERNAL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

`DATABASE_URL` 用 `postgresql+asyncpg://`；两个 Secret 的 `INTERNAL_SERVICE_TOKEN` 必须一致。

### 1.3 镜像拉取 Secret

```bash
kubectl -n muad create secret docker-registry regcred \
  --docker-server=docker.sangfor.com \
  --docker-username="$DOCKER_USER" \
  --docker-password="$DOCKER_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 2. 五个独立配置

每个服务一份，直接交给发布平台对应的 release。`$TAG` 替换成实际镜像版本。

### 2.1 muad-console-platform

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imageTag="$TAG" \
--set global.imagePullPolicy=Always \
--set 'global.imagePullSecrets[0].name=regcred' \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
--set global.tenantId=default \
--set global.artifacts.existingClaim=muad-artifacts-rwx \
--set global.artifacts.mountPath=/mnt/muad-artifacts \
--set serviceAccount.create=true \
--set service.image=muad-console-platform \
--set service.port=8000 \
--set autoscaling.minReplicas=1 \
--set autoscaling.maxReplicas=1 \
--set service.type=NodePort \
--set service.nodePort=30090 \
--set service.externalTrafficPolicy=Cluster \
--set service.resources.requests.cpu=1 \
--set service.resources.requests.memory=2Gi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=4Gi \
--set console.workers=4 \
--set ingress.enabled=false
```

### 2.2 muad-agent-runtime

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imageTag="$TAG" \
--set global.imagePullPolicy=Always \
--set 'global.imagePullSecrets[0].name=regcred' \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
--set global.tenantId=default \
--set global.artifacts.existingClaim=muad-artifacts-rwx \
--set global.artifacts.mountPath=/mnt/muad-artifacts \
--set serviceAccount.create=true \
--set service.image=muad-agent-runtime \
--set service.port=8001 \
--set autoscaling.minReplicas=8 \
--set autoscaling.maxReplicas=8 \
--set service.type=ClusterIP \
--set service.resources.requests.cpu=1 \
--set service.resources.requests.memory=2Gi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=4Gi
```

### 2.3 muad-agent-worker

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imageTag="$TAG" \
--set global.imagePullPolicy=Always \
--set 'global.imagePullSecrets[0].name=regcred' \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
--set global.tenantId=default \
--set global.artifacts.existingClaim=muad-artifacts-rwx \
--set global.artifacts.mountPath=/mnt/muad-artifacts \
--set serviceAccount.create=true \
--set service.image=muad-agent-worker \
--set service.port=8002 \
--set autoscaling.minReplicas=4 \
--set autoscaling.maxReplicas=4 \
--set service.type=ClusterIP \
--set service.nodeSelector.app=openclaw \
--set service.resources.requests.cpu=500m \
--set service.resources.requests.memory=1Gi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=2Gi
```

### 2.4 muad-im-gateway

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imageTag="$TAG" \
--set global.imagePullPolicy=Always \
--set 'global.imagePullSecrets[0].name=regcred' \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
--set global.tenantId=default \
--set global.artifacts.existingClaim=muad-artifacts-rwx \
--set global.artifacts.mountPath=/mnt/muad-artifacts \
--set serviceAccount.create=true \
--set service.image=muad-im-gateway \
--set service.port=8003 \
--set autoscaling.minReplicas=2 \
--set autoscaling.maxReplicas=2 \
--set service.type=NodePort \
--set service.nodePort=30093 \
--set service.externalTrafficPolicy=Cluster \
--set service.resources.requests.cpu=1 \
--set service.resources.requests.memory=1Gi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=2Gi
```

### 2.5 muad-im-connector

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imageTag="$TAG" \
--set global.imagePullPolicy=Always \
--set 'global.imagePullSecrets[0].name=regcred' \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
--set global.tenantId=default \
--set global.artifacts.existingClaim=muad-artifacts-rwx \
--set global.artifacts.mountPath=/mnt/muad-artifacts \
--set serviceAccount.create=true \
--set service.image=muad-im-connector \
--set service.port=8004 \
--set autoscaling.minReplicas=2 \
--set autoscaling.maxReplicas=2 \
--set service.type=ClusterIP \
--set service.resources.requests.cpu=250m \
--set service.resources.requests.memory=512Mi \
--set service.resources.limits.cpu=1 \
--set service.resources.limits.memory=1Gi
```

### 2.6 压测结论对齐

默认值按压测生产表设置：

| 服务 | 副本 | request / limit | 内存 |
|---|---:|---|---:|
| console-platform | 1（4 workers） | 1 / 2 | 2Gi / 4Gi |
| agent-runtime | 8 | 1 / 2 | 2Gi / 4Gi |
| agent-worker | 4 | 500m / 2 | 1Gi / 2Gi |
| im-gateway | 2 | 1 / 2 | 1Gi / 2Gi |
| im-connector | 2 | 250m / 1 | 512Mi / 1Gi |

副本控制：

```text
autoscaling.enabled=true  -> HPA 使用 minReplicas/maxReplicas
autoscaling.enabled=false -> Deployment replicas = minReplicas，maxReplicas 不生效
```

平台统一缩到 1 副本：

```bash
--set autoscaling.minReplicas=1 --set autoscaling.maxReplicas=1
```

### 内核参数

`nofile` 不能直接在 Pod spec 设置；当前容器默认 soft 20480 / hard 1048576，已高于
`fd_budget` 16384。需要 `net.core.somaxconn` 等参数时：

```bash
--set 'service.sysctls[0].name=net.core.somaxconn' --set 'service.sysctls[0].value=4096'
```

非 safe sysctl 需要节点/kubelet 允许；`nofile` 需要在节点/容器运行时配置。

### 2.7 `.env` 完整映射

先分清三层配置：

| 层 | 内容 | 存放 | 生效方式 |
|---|---|---|---|
| 进程启动配置 | DB/Redis、服务地址、池大小、租约、SSE/CHAT 硬上限 | ConfigMap `muad-common` + Secret | 重启 Pod 生效 |
| 业务设置 | 口令策略、压缩预算、任务默认、记忆策略、保留天数、语言/时区、IM 节拍、MCP、容量 | PostgreSQL `control.platform_setting` | 页面保存后新请求/新 Run 生效 |
| 部署参数 | 镜像、副本、资源、NodePort、PVC、nodeSelector | Helm values | 发布时生效 |

`muad-common` 由 chart 自动创建，**不需要手动 `kubectl create configmap`**。

下面这段是**可选的**全量显式覆盖：不写就走代码默认值；只有需要固定某些进程启动参数时才加。
页面「系统设置」里的业务参数**不要**写进 ConfigMap，写了也不会生效。

5 份独立配置已经覆盖 K8s 关键项。下面是把 `.env.example` 里**所有非敏感项**显式放进
`muad-common` ConfigMap 的完整 `config:` 块。

需要全量可控时，用这段替换 console chart `values.yaml` 里的 `config:` 段；不需要改的
可以删掉，未设置的项使用代码默认值。

```yaml
config:
  ENV: "prod"
  LOG_DIR: /var/log/muad
  LOG_LEVEL: INFO
  API_MESSAGES_FILE: /app/config/api-messages.yaml

  ARTIFACT_ROOT: /mnt/muad-artifacts
  SKILL_CACHE_ROOT: /var/cache/muad/skills
  MIGRATIONS_DIR: /app/migrations/versions

  CONSOLE_PLATFORM_URL: http://muad-console-platform:8000
  AGENT_RUNTIME_URL: http://muad-agent-runtime:8001
  AGENT_WORKER_URL: http://muad-agent-worker:8002
  IM_GATEWAY_URL: http://muad-im-gateway:8003

  CHANNEL_PROBE_URL: ""
  WECOM_WS_URL: ""
  WECOM_WS_CA_FILE: ""

  RUN_LEASE_SEC: "60"
  RUN_HEARTBEAT_SEC: "20"
  RUN_REAPER_INTERVAL_SEC: "30"
  RUN_EVENT_HEARTBEAT_SEC: "15"

  TASK_LEASE_SEC: "60"
  TASK_HEARTBEAT_SEC: "20"
  TASK_CANCEL_CHECK_SEC: "2"
  WORKER_POLL_INTERVAL_SEC: "5"
  BATCH_PLATFORM_LIMIT: "16"

  SCHEDULER_POLL_INTERVAL_SEC: "10"
  SCHEDULER_BATCH_SIZE: "100"
  TASK_DEADLINE_SWEEP_INTERVAL_SEC: "30"
  DELIVERY_POLL_INTERVAL_SEC: "5"
  DELIVERY_BATCH_SIZE: "20"

  ASYNC_TOOL_DISPATCH_TIMEOUT_SEC: "5"
  ASYNC_TOOL_DISPATCH_LEASE_SEC: "30"
  ASYNC_TOOL_DISPATCH_POLL_SEC: "1"
  ASYNC_TOOL_DISPATCH_BATCH_SIZE: "32"
  ASYNC_TOOL_DISPATCH_RETRY_BASE_SEC: "1"
  ASYNC_TOOL_DISPATCH_RETRY_CAP_SEC: "30"
  ASYNC_TOOL_DISPATCH_MAX_ATTEMPTS: "20"

  IM_PROGRESS_UPDATES_PER_SECOND: "10"

  RUNTIME_EXECUTION_CONCURRENCY: "8"
  WORKER_EXECUTION_CONCURRENCY: "4"
  GATEWAY_MAX_CONCURRENT_HANDLERS: "8"
  GATEWAY_MAX_PENDING_HANDLERS: "256"
  GATEWAY_MAX_PENDING_PER_ROUTE: "8"
  GATEWAY_MAX_PENDING_COMMANDS: "32"
  GATEWAY_MAX_SSE_SUBSCRIBERS: "1100"
  RUNTIME_MAX_SSE_SUBSCRIBERS: "1100"
  BOT_SNAPSHOT_POLL_SEC: "30"

  GATEWAY_ROUTED_CHANNELS: WECOM
  DELIVERY_STREAM_WAIT_SEC: "2.0"
  WEBHOOK_TIMESTAMP_TOLERANCE_SEC: "300"

  INBOUND_STREAM_SHARDS: "32"
  INBOUND_STREAM_MAXLEN: "100000"
  INBOUND_STREAM_LOST_AFTER_SEC: "60"

  RUNTIME_ADMISSION_QUEUE_LIMIT: "256"
  MEMORY_CONSOLIDATION_QUEUE_MAX: "10000"

  DB_POOL_SIZE: "10"
  DB_MAX_OVERFLOW: "0"
  DB_POOL_TIMEOUT_SEC: "5"
  REDIS_MAX_CONNECTIONS: "50"

  CONTROL_HTTP_MAX_CONNECTIONS: "32"
  CONTROL_HTTP_MAX_KEEPALIVE: "16"
  STREAM_HTTP_MAX_CONNECTIONS: "1100"
  STREAM_HTTP_MAX_KEEPALIVE: "64"
  MODEL_HTTP_MAX_CONNECTIONS: "64"
  MODEL_HTTP_MAX_KEEPALIVE: "32"
  MCP_HTTP_MAX_CONNECTIONS: "16"
  MCP_HTTP_MAX_KEEPALIVE: "8"
  MCP_MAX_INFLIGHT: "8"
  EGRESS_HTTP_MAX_CONNECTIONS: "16"
  EGRESS_HTTP_MAX_KEEPALIVE: "8"
  EGRESS_MAX_INFLIGHT: "8"
  UPLOAD_HTTP_MAX_CONNECTIONS: "8"
  UPLOAD_HTTP_MAX_KEEPALIVE: "4"
  HTTP_CONNECT_TIMEOUT_SEC: "5"
  HTTP_POOL_TIMEOUT_SEC: "2"
  CONTROL_HTTP_READ_TIMEOUT_SEC: "10"
  STREAM_HTTP_READ_TIMEOUT_SEC: "45"
  MODEL_HTTP_READ_TIMEOUT_SEC: "60"
  UPLOAD_HTTP_READ_TIMEOUT_SEC: "60"
  MCP_HTTP_READ_TIMEOUT_SEC: "30"
  EGRESS_HTTP_READ_TIMEOUT_SEC: "30"

  RUN_EVENT_FALLBACK_POLL_SEC: "5"
  MODEL_CALL_LEASE_SEC: "60"
  MODEL_CALL_HEARTBEAT_SEC: "20"
  STREAM_DELTA_FLUSH_INTERVAL_MS: "50"
  STREAM_DELTA_FLUSH_BYTES: "4096"
  SSE_EVENT_MAX_BYTES: "262144"
  SSE_SUBSCRIBER_BUFFER_BYTES: "1048576"
  SSE_TOTAL_BUFFER_BYTES: "67108864"

  CHAT_AUTH_RECHECK_SEC: "15"
  CHAT_EXCHANGE_MAX_BODY_BYTES: "4096"
  CHAT_EXCHANGE_PER_IP_PER_MINUTE: "6000"
  CHAT_MESSAGE_HARD_MAX_BYTES: "1048576"
  CHAT_UPLOAD_HARD_MAX_BYTES: "52428800"

  CHANNEL_DELIVERY_CONCURRENCY: "4"
  CHANNEL_DELIVERY_TIMEOUT_SEC: "5"
  CHANNEL_DELIVERY_LEASE_SEC: "30"
  CHANNEL_DELIVERY_POLL_SEC: "1"
  CHANNEL_DELIVERY_BATCH_SIZE: "32"
  CHANNEL_DELIVERY_RETRY_BASE_SEC: "1"
  CHANNEL_DELIVERY_RETRY_CAP_SEC: "30"
  CHANNEL_DELIVERY_MAX_ATTEMPTS: "20"
```

`DEFAULT_TENANT_ID` 不在 `config` 里，由每个 release 的 `global.tenantId` 控制（默认 `default`）。
不接企业微信时，把 `GATEWAY_ROUTED_CHANNELS` 设为空字符串。

### Secret 项（不要放 ConfigMap）

| Key | Secret | 说明 |
|---|---|---|
| `DATABASE_URL` | `muad-external` | PostgreSQL DSN，`postgresql+asyncpg://` |
| `REDIS_URL` | `muad-external` | Redis DSN |
| `INTERNAL_SERVICE_TOKEN` | `muad-external` | 内部服务鉴权，随机值 |
| `GATEWAY_WEBHOOK_CHANNELS` | `muad-external` | 仅启用 webhook 通道时使用，JSON 内含 secret |

Connector 使用独立 Secret：

```text
muad-connector-external
  REDIS_URL
  INTERNAL_SERVICE_TOKEN
```

### 不需要配置的项

```text
SECRET_PROVIDER
DEFAULT_LOCALE
```

这两个不是当前 `SharedSettings` 字段，配了也不会生效。

## 3. 安装顺序

```text
1. muad-console-platform
2. muad-agent-runtime
3. muad-agent-worker
4. muad-im-gateway
5. muad-im-connector
```

`console-platform` 必须先装，它创建共享 `muad-common` ConfigMap。其余 4 个可以并行。

## 4. 数据库迁移（部署前手动执行）

每个新版本部署前执行一次：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad'
export CONSOLE_IMAGE=docker.sangfor.com/mss-release/muad-console-platform:$TAG

docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini current'
```

迁移失败不要部署新版本。

## 5. 验证

```bash
kubectl -n muad get pods -o wide
kubectl -n muad get svc,ingress
kubectl -n muad logs deploy/muad-console-platform --tail=100

kubectl -n muad port-forward svc/muad-console-platform 8000:8000
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

端口：

```text
console-platform 8000
agent-runtime    8001
agent-worker     8002
im-gateway       8003
im-connector     8004
```

## 6. 老参数替换

| 老参数 | 新参数 |
|---|---|
| `nodeSelector.app` | `global.nodeSelector.app` |
| `workerNodeSelector.app` | agent-worker 的 `service.nodeSelector.app` |
| `image.repository` | `global.imageRegistry` |
| `image.tag` | `global.imageTag` |
| `image.pullPolicy` | `global.imagePullPolicy` |
| `imagePullSecrets[0].name` | `global.imagePullSecrets[0].name` |
| `serviceAccount.create` | `serviceAccount.create` |
| 副本数 | `autoscaling.minReplicas` / `autoscaling.maxReplicas` |
| `resources.*` | `service.resources.*` |
| `service.type/nodePort/externalTrafficPolicy` | `service.type/nodePort/externalTrafficPolicy` |
| `publicSkills.pvcName` | `global.artifacts.existingClaim` |

`autoscaling.enabled` 默认：runtime/worker 为 true，console/gateway/connector 为 false。
平台只传 `minReplicas` / `maxReplicas` 即可；非 HPA 服务的 `maxReplicas` 不生效。

删除这些老参数：

```text
data.storageClassName
configFile.enabled
image.openclawtag
```

## 7. NodePort 注意

新项目的聊天/SSE 在 gateway：

```text
/api/v1/webchat      -> im-gateway:8003
/api/v1/webchat/runs -> im-gateway:8003
```

NodePort 模式需要 console 30090 + gateway 30093，入口侧按路径分流。
有 Ingress 时建议改成 Ingress：console 和 gateway 都设 `service.type=ClusterIP`，console 设 `ingress.enabled=true`。

## 8. 固定 console 到 openclaw 节点

线上 5 台 worker 都有 `app=openclaw` 标签时：

```bash
kubectl get nodes -l app=openclaw
```

### 只让 console 跑在这 5 台节点池

```bash
--set service.nodeSelector.app=openclaw
```

这是 5 台节点池，调度器会从这 5 台里选一台，不是固定某一台。

### 固定到其中一台

给目标节点加唯一标签：

```bash
kubectl label node <node-name> muad-console=true
```

console release 设置：

```bash
--set service.nodeSelector.muad-console=true
```

如果要同时限制在 openclaw 节点池：

```bash
--set service.nodeSelector.app=openclaw \
--set service.nodeSelector.muad-console=true
```

### 只让 console / worker 在 openclaw 节点

从其它 3 个 release 去掉 `global.nodeSelector.app=openclaw`，只给 console 和 agent-worker 加：

```bash
--set service.nodeSelector.app=openclaw
```

`service.nodeSelector` 会覆盖 `global.nodeSelector`；两者不要同时配成不同节点池。

### 注意

- console 只有 1 副本，固定到单节点就是单点；节点故障 console 不可用。
- 需要 console HA 时至少 2 副本，并用 `service.affinity` 配 podAntiAffinity 分散到不同节点。
- runtime/worker 的多副本不需要逐个固定节点，交给调度器按资源分布即可。
