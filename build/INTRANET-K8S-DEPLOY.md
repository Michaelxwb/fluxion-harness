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
# 内网 Redis 只有 Cluster（3 主 3 从），且只支持 db 0；密码含 @ : / ? # 时先做 URL 编码。
export REDIS_PASSWORD='CHANGE_ME'
export REDIS_SEED='10.74.145.45:6393'

kubectl -n muad create secret generic muad-external \
  --from-literal=DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad_db' \
  --from-literal=REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_SEED}/0" \
  --from-literal=INTERNAL_SERVICE_TOKEN="$INTERNAL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n muad create secret generic muad-connector-external \
  --from-literal=REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_SEED}/0" \
  --from-literal=INTERNAL_SERVICE_TOKEN="$INTERNAL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

`DATABASE_URL` 用 `postgresql+asyncpg://`；两个 Secret 的 `INTERNAL_SERVICE_TOKEN` 与
`REDIS_URL` 必须一致（同一集群、同一凭据）。

`REDIS_URL` 在 Cluster 模式下只承载**凭据 + 一个 seed**；`REDIS_MODE=cluster` 与
`REDIS_CLUSTER_SEEDS` 是非敏感配置，放 `muad-common` ConfigMap（见 §1.3），不要写进 Secret。
Cluster 只有 db 0，URL 路径必须是 `/0`。dev/本地单机仍可用 `REDIS_MODE=standalone` +
`REDIS_URL=redis://redis:6379/0`。

外部托管 PostgreSQL / Redis 时的完整变量：

```text
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<pg-host>:5432/<db>
REDIS_MODE=cluster
REDIS_URL=redis://:<password>@<seed1>:6393/0
REDIS_CLUSTER_SEEDS=<seed1>:6393,<seed2>:6393,<seed3>:6393
```

- `REDIS_CLUSTER_SEEDS` 写全部发现 seed（内网为
  `10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393`）。
- 客户端按 `CLUSTER SLOTS` 的**通告地址**连接所有 master，不只是 seed：内网 3 个 master
  分别监听 `10.74.145.46:6393`（5461–10922）、`10.74.145.46:6394`（0–5460）、
  `10.74.145.47:6393`（10923–16383）。防火墙必须放行 **6 个通告地址**
  （`10.74.145.45/46/47` × `6393/6394`），否则部分 key 无法路由。
- 服务端版本必须 ≥ 6.2（`XAUTOCLAIM` 下限；内网实测 6.2.18）。
- 上线前跑两条只读探针：
  - `python scripts/probe_redis_cluster.py --url "$REDIS_URL" --nodes <6 个通告地址>`：
    地址可达、`redis_mode=cluster`、版本、槽位全覆盖、`maxmemory`/`maxclients`；
  - `python scripts/probe_redis_cluster_commands.py --url "$REDIS_URL" --seeds "$REDIS_CLUSTER_SEEDS"`：
    同 slot `XREADGROUP`/`XAUTOCLAIM`/双 key `EVAL`、跨节点 `PUBLISH`、`SCAN`、跨 slot 拒绝
    与三族 key 的 hash tag 布局（11 项）。
- 两条探针在能访问集群的运维机/跳板机执行；镜像没有 COPY `scripts/`，如需在 Pod 内执行，
  先 `kubectl cp scripts/probe_redis_cluster.py scripts/probe_redis_cluster_commands.py <pod>:/tmp/`。

### 1.3 共享 ConfigMap

`muad-common` 是 5 个服务共用的进程启动配置，不归属任何服务 chart：

```bash
# 部署步骤不变：照常 apply base ConfigMap，再补一次 merge patch 切到内网 Cluster
kubectl apply -f deploy/k8s/base/configmap.yaml
kubectl -n muad patch configmap muad-common --type merge -p \
  '{"data":{"REDIS_MODE":"cluster","REDIS_CLUSTER_SEEDS":"10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393"}}'
kubectl -n muad get configmap muad-common -o jsonpath='{.data.REDIS_MODE}'   # cluster
```

`base/configmap.yaml` 默认 `REDIS_MODE: standalone`（dev/CI/verify-multipod 复用该文件），
内网只需把下面两个键改成：

```yaml
REDIS_MODE: cluster
REDIS_CLUSTER_SEEDS: "10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393"
```

`REDIS_CLUSTER_SEEDS` 只写非敏感 seed，密码只放 Secret。服务仍按本文后面的
`helm upgrade --install` 步骤安装，不要用 `kubectl apply -k deploy/k8s/base` 把原始清单
铺进 Helm 管理的 namespace。

已有 ConfigMap 时 `kubectl apply` 会原地更新，但**不会重启 Pod**；`kubectl patch` 可重复
执行。顺序固定为「先 apply（会把值重置为 standalone）→ 再 patch」，然后重启 5 个
Deployment（`muad-common` 是外部资源，Helm 不会因它变化自动滚动）：

```bash
kubectl -n muad rollout restart deploy/muad-console-platform deploy/muad-agent-runtime \
  deploy/muad-agent-worker deploy/muad-im-gateway
# 启用 Connector 时再加 deploy/muad-connector
kubectl -n muad rollout status deploy/muad-im-gateway --timeout=180s
```

### 1.4 镜像拉取 Secret（不需要）

内网集群的节点可以直接匿名拉取 `docker.sangfor.com` 的镜像，5 个服务和 nginx 都**不用**
配置 `global.imagePullSecrets`，也不需要在 `muad` 里建 docker-registry Secret。chart 的
`global.imagePullSecrets` 默认是 `[]`，模板用 `{{- with }}` 包裹，为空时整个
`imagePullSecrets` 字段不渲染。

只有换成需要认证的私有仓库时才需要：

```bash
kubectl -n muad create secret docker-registry regcred \
  --docker-server=docker.sangfor.com \
  --docker-username="$DOCKER_USER" \
  --docker-password="$DOCKER_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
# 然后在部署参数里加：--set 'global.imagePullSecrets[0].name=regcred'
```

## 2. 五个独立配置

每个服务一份，直接交给发布平台对应的 release。

> **参数块里不写镜像 tag。** 发布平台会从 `release-<service>.env` 自动注入
> `--set image.repository=... --set image.tag=...`，chart 里 `image.tag` 优先于
> `global.imageTag` 和 `service.tag`。手工执行 `helm` 时自己补
> `--set image.tag=<tag>`。
>
> 也不要在参数块里写 `$TAG`：平台生成的脚本是 `set -euo pipefail`，未定义的变量会
> 直接退出（`bash: line 22: TAG: unbound variable`），命令一行都不会执行。

> 如果使用第 9 节的 nginx 统一入口，把 console 和 gateway 的 `service.type` 改成
> `ClusterIP`，并去掉 `service.nodePort` / `service.externalTrafficPolicy`。

### 2.1 muad-console-platform

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imagePullPolicy=Always \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
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
--set global.imagePullPolicy=Always \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
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
--set global.imagePullPolicy=Always \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
--set global.artifacts.existingClaim=muad-artifacts-rwx \
--set global.artifacts.mountPath=/mnt/muad-artifacts \
--set serviceAccount.create=true \
--set service.image=muad-agent-worker \
--set service.port=8002 \
--set autoscaling.minReplicas=4 \
--set autoscaling.maxReplicas=4 \
--set service.type=ClusterIP \
--set service.resources.requests.cpu=500m \
--set service.resources.requests.memory=1Gi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=2Gi
```

### 2.4 muad-im-gateway

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imagePullPolicy=Always \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
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
--set global.imagePullPolicy=Always \
--set global.nodeSelector.app=openclaw \
--set global.existingSecret=muad-external \
--set global.connectorSecret=muad-connector-external \
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

`muad-common` 由第 1.3 步的 `deploy/k8s/base/configmap.yaml` 创建，不归属任何服务 chart；
5 个服务可以任意顺序安装。

下面这段是**可选的**全量显式覆盖：不写就走代码默认值；只有需要固定某些进程启动参数时才加。
页面「系统设置」里的业务参数**不要**写进 ConfigMap，写了也不会生效。

5 份独立配置已经覆盖 K8s 关键项。下面是把 `.env.example` 里**所有非敏感项**显式放进
`muad-common` ConfigMap 的完整 `data` 块。

需要全量可控时，用下面这段替换 `deploy/k8s/base/configmap.yaml` 的 `data:`；
不需要改的可以删掉，未设置的项使用代码默认值。

```yaml
data:
  DEFAULT_TENANT_ID: "default"
  ENV: "prod"
  LOG_DIR: /app/.data/logs
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

  REDIS_MODE: "cluster"
  REDIS_CLUSTER_SEEDS: "10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393"
  REDIS_MAX_CONNECTIONS: "50"

  INBOUND_STREAM_SHARDS: "32"
  INBOUND_STREAM_MAXLEN: "5000"
  INBOUND_STREAM_LOST_AFTER_SEC: "60"
  INBOUND_STREAM_RETENTION_SEC: "600"
  OUTBOUND_STREAM_RETENTION_SEC: "3600"

  RUNTIME_ADMISSION_QUEUE_LIMIT: "256"
  MEMORY_CONSOLIDATION_QUEUE_MAX: "10000"

  DB_POOL_SIZE: "10"
  DB_MAX_OVERFLOW: "0"
  DB_POOL_TIMEOUT_SEC: "5"

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

`DEFAULT_TENANT_ID` 在 `muad-common` 的 `data` 里，默认 `default`。
不接企业微信时，把 `GATEWAY_ROUTED_CHANNELS` 设为空字符串。

### Secret 项（不要放 ConfigMap）

| Key | Secret | 必填 | 说明 |
|---|---|---|---|
| `DATABASE_URL` | `muad-external` | 是 | PostgreSQL DSN，`postgresql+asyncpg://` |
| `REDIS_URL` | `muad-external` | 是 | Redis Cluster 凭据 + 主 seed：`redis://:<pwd>@<seed>:6393/0`（只支持 db 0） |
| `INTERNAL_SERVICE_TOKEN` | `muad-external` | 是 | 内部服务鉴权，随机值 |
| `GATEWAY_WEBHOOK_CHANNELS` | `muad-external` | 否 | 仅启用 webhook 通道时使用，JSON 数组，内含 secret |

Connector 使用独立 Secret：

```text
muad-connector-external
  REDIS_URL                 (必填)
  INTERNAL_SERVICE_TOKEN    (必填，和 muad-external 相同)
```

`REDIS_MODE` / `REDIS_CLUSTER_SEEDS` / `REDIS_MAX_CONNECTIONS` / `INBOUND_STREAM_*` 是
非敏感项，放 `muad-common` ConfigMap；内网必须显式 `REDIS_MODE=cluster`，否则会按
`standalone` 连接并因拓扑不匹配启动失败。

`GATEWAY_WEBHOOK_CHANNELS` 格式：

```json
[{"channel":"wecom","secret":"<回调验签密钥>"}]
```

不接 webhook 通道时不要设置这个 key。

### 不需要配置的项

```text
SECRET_PROVIDER
DEFAULT_LOCALE
```

这两个不是当前 `SharedSettings` 字段，配了也不会生效。

## 3. 安装顺序

前置资源就绪后（namespace、Secret、`muad-common` ConfigMap、PVC），
5 个服务没有顺序依赖，可以任意顺序安装，也可以并行。

```text
muad-console-platform
muad-agent-runtime
muad-agent-worker
muad-im-gateway
muad-im-connector
```

## 4. 数据库迁移（部署前手动执行）

数据库必须先存在，迁移脚本不会建库：

```sql
-- 用 postgres 维护库连接后执行一次
CREATE DATABASE muad_db;
```

连接串指向这个库：

```text
postgresql+asyncpg://<user>:<password>@<pg-host>:5432/muad_db
```

每个新版本部署前执行一次。`upgrade head` 是幂等的：没有新迁移时是 no-op，
不确定有没有新迁移时直接跑也安全：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad_db'
export CONSOLE_IMAGE=docker.sangfor.com/mss-release/muad-console-platform:$TAG

docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini current'
```

迁移失败不要部署新版本。

怎么判断有没有新迁移：

```bash
docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         echo "db:";      python migrations/db_migrate.py -c /tmp/alembic.ini current
         echo "code:";    python migrations/db_migrate.py -c /tmp/alembic.ini heads
         echo "pending:"; python migrations/db_migrate.py -c /tmp/alembic.ini history -r current:head'
```

- `db` 和 `code` 一致：没有待执行迁移
- 不一致：有新迁移，跑 `upgrade head`
- 空库还没有 revision，`history -r current:head` 会报错，直接跑 `upgrade head`

新迁移一般是表结构变更（建表、加列、索引、约束、schema），也可能包含数据回填
（例如 0025 的存量会话迁移），所以准确说是“数据库变更”，不只是“表结构变更”。

只有 `migrations/versions/` 新增文件的版本才需要动库；每次发布前跑一遍 `upgrade head`
只是确认已经到最新。

## 5. 验证

```bash
kubectl -n muad get pods -o wide
kubectl -n muad get svc,ingress
kubectl -n muad logs deploy/muad-console-platform --tail=100

kubectl -n muad port-forward svc/muad-console-platform 8000:8000
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

Redis 拓扑自检（Gateway 的 `/readyz` detail 会带 `redis_mode`）：

```bash
kubectl -n muad port-forward svc/muad-im-gateway 8003:8003
curl -fsS http://127.0.0.1:8003/readyz   # data.redis_mode 应为 "cluster"
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
不想维护两个 NodePort、也不想上 Ingress 时，用第 9 节的 nginx 统一入口：console 和 gateway
都设 `service.type=ClusterIP`，只暴露 nginx 的 30090。

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


## 9. 统一入口 nginx（单 NodePort）

用一个 nginx 镜像把 console 页面和 gateway 聊天/SSE 收到同一个 NodePort：

```text
http://<master-ip>:30090/
```

### 9.1 构建镜像

```bash
BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2 \
IMAGE_REPOSITORY=docker.sangfor.com/mss-release/muad-nginx \
  ./build/nginx-build/build.sh
```

千流：单独一条 `nginx-entry` 流水线，命令
`bash ./build/docker-build/task-image-nginx-entry.sh`，变量同上。

默认从 PlatOS/Kylin RPM 安装 nginx。RPM 装不上时用 copy 模式：

```bash
NGINX_BUILD_MODE=copy \
NGINX_SOURCE_IMAGE=<和底座 glibc 兼容的 nginx 镜像> \
BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2 \
IMAGE_REPOSITORY=docker.sangfor.com/mss-release/muad-nginx \
  ./build/nginx-build/build.sh
```

`nginx:1.24-bullseye` 是 glibc 2.31；底座是 glibc 2.28 时不要直接用它。
优先用 RPM 模式，或把 `NGINX_SOURCE_IMAGE` 指向你们现有 `portal-nginx` 用的镜像。

### 9.2 调整 console / gateway

```text
console-platform: service.type=ClusterIP，去掉 service.nodePort=30090
im-gateway:       service.type=ClusterIP，去掉 service.nodePort=30093
```

`ingress.enabled` 保持 `false`。

### 9.3 nginx 的 --set 参数

```text
chart:   muad-nginx
release: muad-nginx
```

和 2.x 的服务一样，把下面整块交给发布平台的 nginx release：

```bash
--set global.imageRegistry=docker.sangfor.com/mss-release \
--set global.imagePullPolicy=Always \
--set service.image=muad-nginx \
--set service.port=8080 \
--set service.type=NodePort \
--set service.nodePort=30090 \
--set service.externalTrafficPolicy=Cluster \
--set autoscaling.minReplicas=2 \
--set autoscaling.maxReplicas=2 \
--set service.resources.requests.cpu=100m \
--set service.resources.requests.memory=128Mi \
--set service.resources.limits.cpu=500m \
--set service.resources.limits.memory=512Mi
```

参数说明：

| 参数 | 默认 | 说明 |
|---|---|---|
| `global.imageRegistry` | `docker.sangfor.com/mss-dev` | 镜像仓库，生产填 `mss-release` |
| `global.imageTag` | `latest` | 平台注入 `image.tag` 后不生效；手工执行时用它或 `image.tag` |
| `global.imagePullPolicy` | `IfNotPresent` | 生产建议 `Always` |
| `global.imagePullSecrets` | `[]` | 内网匿名拉取，不用配；私有仓库才填 Secret 名（如 `regcred`） |
| `service.image` | `muad-nginx` | 镜像名 |
| `service.tag` | `''` | 单独覆盖 `global.imageTag`，一般不用 |
| `service.port` | `8080` | 容器和 Service 端口，镜像固定 8080 |
| `service.type` | `NodePort` | 统一入口用 `NodePort`；走 Ingress 改 `ClusterIP` |
| `service.nodePort` | `30090` | 对外端口 |
| `service.externalTrafficPolicy` | `''` | 只对 `NodePort` / `LoadBalancer` 生效 |
| `autoscaling.minReplicas` | `2` | 副本数（没有 HPA 时即实际副本数） |
| `autoscaling.maxReplicas` | `2` | 与 5 个服务保持同一组参数 |
| `service.resources.*` | 100m/128Mi、500m/512Mi | requests / limits |
| `probes.path` | `/healthz` | 就绪/存活探针路径 |
| `global.nodeSelector` | `{}` | 不限制节点；要固定时加 `--set global.nodeSelector.app=openclaw` |

镜像 tag 的优先级是 `image.tag` > `service.tag` > `global.imageTag`，填其中一个即可。
发布平台走 `image.tag`，所以参数块里不用写 `global.imageTag`。

命令行安装时，把上面整块 `--set` 接在下面这条命令后面：

```text
helm upgrade --install muad-nginx mss-release-helm/muad-nginx --namespace muad
```

nginx 会使用同 namespace 的 Service DNS：

```text
muad-console-platform:8000
muad-im-gateway:8003
```

不用 Helm 时，仍可直接 apply 静态 YAML：

```bash
TAG=<流水线产出的 tag>
sed "s#muad-nginx:1.0.0#muad-nginx:${TAG}#" build/nginx-entry.yaml \
  | kubectl apply -f -
```

### 9.4 路由

```text
/                       -> console
/api                    -> console
/chat, /assets          -> console
/api/v1/webchat/auth    -> console
/api/v1/webchat/*       -> gateway
/api/v1/webchat/runs/*  -> gateway（SSE，不缓冲）
/healthz                -> 200
```

### 9.5 验证

```bash
kubectl -n muad get pods -l app=muad-nginx
kubectl -n muad get svc muad-nginx

curl -I http://<master-ip>:30090/
curl -i http://<master-ip>:30090/api/v1/webchat/auth
curl -N http://<master-ip>:30090/api/v1/webchat/runs
```

- `/` 返回 200：页面入口通
- `/api/v1/webchat/auth` 返回 401/403：已经到 console
- `/api/v1/webchat/runs` 返回 401/403：已经到 gateway
- 返回 404：nginx 没有把路径分到 gateway

### 9.6 说明

- `portal-nginx` 是普通反向代理，不是 Ingress Controller；本方案不修改 `mss-pro/portal-nginx`。
- nginx 镜像运行 UID 10001，pid/temp 都在 `/tmp/nginx`。
- 这个改动不需要重建 5 个服务镜像，也不需要重建基础镜像。
- 详细构建说明见 `build/nginx-build/README.md`。
