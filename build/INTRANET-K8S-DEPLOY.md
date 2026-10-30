# 新版内网 K8s 部署配置

适用于 `feat/intranet-build` 分支，5 个服务镜像 + 5 个 per-service Helm chart。

本文是**部署配置的最终版**；镜像构建和千流流水线见 `build/INTRANET-SETUP.md`。

## 0. 与老项目的差异

| 项目 | 老项目 | 新项目 |
|---|---|---|
| 服务 | console + openclaw | console-platform / agent-runtime / agent-worker / im-gateway / im-connector |
| Console 状态 | SQLite `console.db` + 本地日志 | 无状态；PostgreSQL + Redis + 共享 artifacts PVC |
| 共享存储 | `muad-public-data`（skills） | `muad-artifacts-rwx`（artifacts，5 个服务都挂） |
| Console 数据 PVC | `muad-console-data`（open-local-lvm） | 不需要 |
| 配置 | `config.yaml` Secret | 环境变量：ConfigMap + Secret |
| 入口 | NodePort 30080 | 推荐 Ingress；NodePort 可选 |
| Worker 节点 | `workerNodeSelector` | `agent-worker` Deployment 的 `service.nodeSelector` |
| Worker 镜像 | `muad-openclaw` | `muad-agent-runtime` / `muad-agent-worker` |

## 1. 前置资源

### 1.1 Namespace

```bash
export NS=muad
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
```

### 1.2 PostgreSQL / Redis

准备可被 K8s 访问的 PostgreSQL 和 Redis：

```text
PostgreSQL: 创建 muad 数据库和用户
Redis:      可访问即可
```

### 1.3 NFS 共享 PVC

公共 NFS 已经部署好，不需要重装 NFS Server / provisioner。复用现有 StorageClass：

```text
muad-nfs-public-provisoner
```

创建新 PVC（不要直接复用旧 `muad-public-data`，避免旧 skills 数据混入）：

```yaml
# muad-artifacts-rwx.yaml
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
kubectl -n muad get pvc muad-artifacts-rwx -w
```

5 个服务都会挂载：

```text
PVC: muad-artifacts-rwx
MountPath: /mnt/muad-artifacts
```

如果 PVC 名字不同，Helm 设置：

```bash
--set global.artifacts.existingClaim=<PVC名>
```

权限：Pod 以 UID 10001 运行。旧 NFS 使用 `no_root_squash` + `chmod 777` 一般可写；如果报 `Permission denied`，检查 StorageClass 的 `mountPermissions` 或 provisioner 创建的子目录权限。

### 1.4 Secret

```bash
export INTERNAL_TOKEN="$(openssl rand -hex 32)"

kubectl -n "$NS" create secret generic muad-external \
  --from-literal=DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad' \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=INTERNAL_SERVICE_TOKEN="$INTERNAL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NS" create secret generic muad-connector-external \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=INTERNAL_SERVICE_TOKEN="$INTERNAL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

注意：

- `DATABASE_URL` 必须用 `postgresql+asyncpg://`
- 密码含特殊字符时先 URL 编码
- 两个 Secret 的 `INTERNAL_SERVICE_TOKEN` 必须一致
- Connector 不持库，不需要 `DATABASE_URL`

### 1.5 镜像拉取 Secret

如果节点没有预置 registry 凭据：

```bash
kubectl -n "$NS" create secret docker-registry regcred \
  --docker-server=docker.sangfor.com \
  --docker-username="$DOCKER_USER" \
  --docker-password="$DOCKER_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 2. `.env` 到 K8s 的映射

**不要**把本地 `.env` 整个 `--from-env-file` 进 Secret。本地 `.env` 里有：

```text
CONSOLE_PLATFORM_URL=http://127.0.0.1:8000
LOG_DIR=./.data/logs
ARTIFACT_ROOT=./.data/artifacts
```

这些在 K8s 里是错的，会覆盖 chart 已经配好的 Service DNS 和容器路径。

### 2.1 Secret（敏感）

| Secret | Key |
|---|---|
| `muad-external` | `DATABASE_URL`、`REDIS_URL`、`INTERNAL_SERVICE_TOKEN` |
| `muad-connector-external` | `REDIS_URL`、`INTERNAL_SERVICE_TOKEN` |

如果以后增加敏感项，例如 `GATEWAY_WEBHOOK_CHANNELS`（JSON 里含 webhook secret），加到 `muad-external`，再用 `extraEnvFrom` 或 `service.extraEnv` 注入。

### 2.2 ConfigMap（非敏感）

console chart 会创建 `muad-common`：

```text
DEFAULT_TENANT_ID
LOG_DIR
ARTIFACT_ROOT
SKILL_CACHE_ROOT
API_MESSAGES_FILE
CONSOLE_PLATFORM_URL
AGENT_RUNTIME_URL
AGENT_WORKER_URL
IM_GATEWAY_URL
RUN_EVENT_HEARTBEAT_SEC
GATEWAY_MAX_SSE_SUBSCRIBERS
SSE_EVENT_MAX_BYTES
SSE_SUBSCRIBER_BUFFER_BYTES
SSE_TOTAL_BUFFER_BYTES
CHAT_MESSAGE_HARD_MAX_BYTES
CHAT_UPLOAD_HARD_MAX_BYTES
CHAT_AUTH_RECHECK_SEC
STREAM_HTTP_READ_TIMEOUT_SEC
GATEWAY_ROUTED_CHANNELS
```

要覆盖其它非敏感 `.env` 项，加到 console chart 的 `config:`：

```yaml
config:
  RUN_LEASE_SEC: "60"
  TASK_LEASE_SEC: "60"
  DB_POOL_SIZE: "10"
  DB_MAX_OVERFLOW: "0"
  REDIS_MAX_CONNECTIONS: "50"
  WORKER_EXECUTION_CONCURRENCY: "4"
  RUNTIME_EXECUTION_CONCURRENCY: "8"
```

这些会进入 `muad-common`，5 个服务都通过 `envFrom` 自动拿到。

### 2.3 额外 Secret key 怎么让所有服务拿到

方式 A：`global.extraEnvFrom` / `service.extraEnvFrom`（本次 chart 已支持）

```yaml
global:
  extraEnvFrom:
    - secretRef:
        name: muad-external
```

> `global.extraEnvFrom` 会注入到所有服务，包括 connector。connector 不需要
> `DATABASE_URL`，所以给 connector 用 `service.extraEnvFrom` 指向
> `muad-connector-external`，不要用 global 注入 `muad-external`。

方式 B：单个 key 用 `service.extraEnv`：

```yaml
service:
  extraEnv:
    - name: GATEWAY_WEBHOOK_CHANNELS
      valueFrom:
        secretKeyRef:
          name: muad-external
          key: GATEWAY_WEBHOOK_CHANNELS
```

注意：

- `GATEWAY_WEBHOOK_CHANNELS` 含 secret，放 Secret，不要放 ConfigMap
- 本地 `.env` 里的 `SECRET_PROVIDER`、`DEFAULT_LOCALE` 已不是当前 `SharedSettings` 字段（`extra="ignore"`），不要配到 K8s
- 旧项目的 `config.yaml` / `muad-console-config` 不要复用，新项目不读它

## 3. Helm 公共 values

建议用一个公共 values 文件：

```yaml
# values-common.yaml
global:
  imageRegistry: docker.sangfor.com/mss-release
  imageTag: "1.0.0"
  imagePullPolicy: Always
  imagePullSecrets:
    - name: regcred
  existingSecret: muad-external
  connectorSecret: muad-connector-external
  tenantId: default
  artifacts:
    existingClaim: muad-artifacts-rwx
    mountPath: /mnt/muad-artifacts
  # 可选：所有服务统一节点
  nodeSelector: {}
  tolerations: []
  affinity: {}
  # 可选：所有服务统一注入额外 Secret/ConfigMap
  extraEnvFrom: []
```

每个 chart 的 `service` 还支持：

```yaml
service:
  type: ClusterIP
  nodePort: null
  externalTrafficPolicy: ""
  nodeSelector: {}
  tolerations: []
  affinity: {}
  extraEnvFrom: []
```

调度优先级：

```text
service.nodeSelector 覆盖 global.nodeSelector
service.tolerations   覆盖 global.tolerations
service.affinity      覆盖 global.affinity
extraEnvFrom = global.extraEnvFrom + service.extraEnvFrom
```

ServiceAccount：

```yaml
serviceAccount:
  create: false
  name: ""
  annotations: {}
```

- `create=true`：创建一个以 chart 名命名的 SA
- `create=false` + `name`：引用已有 SA
- 都不设：使用 default SA

## 4. 数据库迁移（部署前手动执行）

Helm chart **不再创建迁移 Job**，迁移必须在 `helm upgrade` 之前手动完成。

方式 A：用 console 镜像：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad'
export CONSOLE_IMAGE=docker.sangfor.com/mss-release/muad-console-platform:1.0.0

docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini current'
```

方式 B：在仓库里：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad'
printf '[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n' "$DATABASE_URL" > /tmp/alembic.prod.ini

uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini current
```

迁移失败不要部署新版本。

## 5. 安装 console-platform

```bash
export TAG=1.0.0
export CHART_DIR=/tmp/muad-charts

helm upgrade --install muad-console-platform \
  "$CHART_DIR/muad-console-platform-${TAG}.tgz" \
  -n "$NS" \
  -f values-common.yaml \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.host=muad.example.com \
  --set service.resources.requests.cpu=100m \
  --set service.resources.requests.memory=128Mi \
  --set service.resources.limits.cpu=2 \
  --set service.resources.limits.memory=4Gi
```

如果使用 TLS：

```bash
--set 'ingress.tls[0].secretName=muad-tls' \
--set 'ingress.tls[0].hosts[0]=muad.example.com'
```

如果让 chart 创建 artifacts PVC（不推荐，和手动 PVC 二选一）：

```bash
--set global.artifacts.create=true \
--set global.artifacts.storageClassName=muad-nfs-public-provisoner \
--set global.artifacts.storage=100Gi
```

console chart 创建：

```text
muad-common ConfigMap
muad-artifacts-rwx PVC（可选）
muad-webchat / muad-webchat-sse Ingress
muad-console-platform Deployment/Service
```

## 6. 安装其余 4 个服务

```bash
for chart in muad-agent-runtime muad-agent-worker muad-im-gateway muad-im-connector; do
  helm upgrade --install "$chart" \
    "$CHART_DIR/${chart}-${TAG}.tgz" \
    -n "$NS" \
    -f values-common.yaml
done
```

不接企业微信的话，`muad-im-connector` 可以不装。

资源按服务调整，例如 worker：

```bash
helm upgrade muad-agent-worker "$CHART_DIR/muad-agent-worker-${TAG}.tgz" \
  -n "$NS" -f values-common.yaml \
  --set service.resources.requests.cpu=1 \
  --set service.resources.requests.memory=2Gi \
  --set service.resources.limits.cpu=4 \
  --set service.resources.limits.memory=8Gi
```

## 7. 入口：Ingress vs NodePort

### 推荐：Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  host: muad.example.com
  tls:
    - secretName: muad-tls
      hosts:
        - muad.example.com
```

Ingress 负责：

```text
/                          -> console-platform:8000
/api                       -> console-platform:8000
/chat, /assets             -> console-platform:8000
/api/v1/webchat/auth       -> console-platform:8000
/api/v1/webchat            -> im-gateway:8003
/api/v1/webchat/runs       -> im-gateway:8003（SSE，proxy-buffering off）
```

### NodePort（兼容旧方式）

当前 chart 已支持：

```bash
--set service.type=NodePort \
--set service.nodePort=30080 \
--set service.externalTrafficPolicy=Cluster
```

但要注意：新项目把聊天/SSE 拆到了 gateway，**只给 console 开 30080 不够**。
如果完全不用 Ingress，需要：

```text
console NodePort: 30080 -> console-platform:8000
gateway NodePort: 30083 -> im-gateway:8003
```

并且调用方/前端要按路径把 `/api/v1/webchat*` 转到 gateway。
最省事、最接近新架构的还是 Ingress。

## 8. 固定 worker 节点

老项目的 `workerNodeSelector` 对应新项目的 `agent-worker` Deployment 调度。

### 只固定 agent-worker

```bash
helm upgrade muad-agent-worker "$CHART_DIR/muad-agent-worker-${TAG}.tgz" \
  -n "$NS" -f values-common.yaml \
  --set service.nodeSelector.app=openclaw
```

### 所有服务固定

```yaml
global:
  nodeSelector:
    app: openclaw
```

### 容忍污点

```yaml
service:
  tolerations:
    - key: dedicated
      operator: Equal
      value: muad
      effect: NoSchedule
```

### 节点亲和

```yaml
service:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: app
                operator: In
                values:
                  - openclaw
```

说明：

- 新项目没有“动态创建 worker Pod”的逻辑，`agent-worker` 是普通 Deployment
- `agent-worker` 的 `/var/cache/muad/skills` 是 `emptyDir`，Pod 重建缓存会丢；要持久化应挂 PVC，而不是靠固定节点
- 如果只是为了 NFS，NFS 是共享存储，所有节点都能挂，不需要固定节点

## 9. ServiceAccount

老项目的 `serviceAccount.create=true` 现在对应：

```bash
--set serviceAccount.create=true
```

默认 SA 名是 chart 名，例如：

```text
muad-console-platform
muad-agent-runtime
muad-agent-worker
muad-im-gateway
muad-im-connector
```

如果要指定名字：

```bash
--set serviceAccount.create=true --set serviceAccount.name=muad-workload
```

如果 SA 已经由平台统一创建：

```bash
--set serviceAccount.create=false --set serviceAccount.name=muad-workload
```

新项目一般不需要自定义 SA；旧项目的 SA 主要用于创建 worker Pod 的 RBAC，新项目不再需要。

## 10. 老 Helm 参数映射

| 老参数 | 新参数 | 说明 |
|---|---|---|
| `--set nodeSelector.app=openclaw` | `--set global.nodeSelector.app=openclaw` | 所有服务统一节点 |
| `--set workerNodeSelector.app=openclaw` | `--set service.nodeSelector.app=openclaw`（agent-worker chart） | 新项目没有动态 worker Pod |
| `--set image.repository=docker.sangfor.com/mss-release/muad-console` | `--set global.imageRegistry=docker.sangfor.com/mss-release` | 镜像名由各 chart 的 `service.image` 决定 |
| `--set image.tag=${tag}` | `--set global.imageTag=$TAG` | 建议直接传明确版本 |
| `--set image.pullPolicy=Always` | `--set global.imagePullPolicy=Always` | 已支持 |
| `--set imagePullSecrets[0].name=regcred` | `--set 'global.imagePullSecrets[0].name=regcred'` | 已支持 |
| `--set serviceAccount.create=true` | `--set serviceAccount.create=true` | 已支持 |
| `--set resources.limits.cpu=2` | `--set service.resources.limits.cpu=2` | 每个 chart 单独设 |
| `--set resources.limits.memory=4Gi` | `--set service.resources.limits.memory=4Gi` | 每个 chart 单独设 |
| `--set resources.requests.cpu=100m` | `--set service.resources.requests.cpu=100m` | 每个 chart 单独设 |
| `--set resources.requests.memory=128Mi` | `--set service.resources.requests.memory=128Mi` | 每个 chart 单独设 |
| `--set service.type=NodePort` | `--set service.type=NodePort` | 已支持；注意 gateway 分流 |
| `--set service.nodePort=30080` | `--set service.nodePort=30080` | 已支持 |
| `--set service.externalTrafficPolicy=Cluster` | `--set service.externalTrafficPolicy=Cluster` | 已支持 |
| `--set data.storageClassName=open-local-lvm` | 不需要 | 新 console 无 `console.db` data PVC |
| `--set publicSkills.pvcName=muad-public-data` | `--set global.artifacts.existingClaim=muad-artifacts-rwx` | 建议新建 PVC，不复用旧数据 |
| `--set configFile.enabled=false` | 不需要 | 新项目不读 `config.yaml` |
| `--set image.openclawtag=${image}` | 不需要 | 新项目没有 openclaw 镜像；用 `global.imageTag` |

## 11. 验证

```bash
kubectl -n "$NS" get pods -o wide
kubectl -n "$NS" get svc,ingress
kubectl -n "$NS" get pvc

kubectl -n "$NS" logs deploy/muad-console-platform --tail=100
kubectl -n "$NS" logs deploy/muad-agent-worker --tail=100

kubectl -n "$NS" port-forward svc/muad-console-platform 8000:8000
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

其余服务：

```text
console-platform 8000
agent-runtime    8001
agent-worker     8002
im-gateway       8003
im-connector     8004
```

每个服务都有 `/healthz` 和 `/readyz`。

## 12. 常见问题

| 现象 | 排查 |
|---|---|
| `ImagePullBackOff` | `global.imageRegistry` / `global.imageTag` / `regcred` |
| `CreateContainerConfigError` | `muad-external`、`muad-connector-external` 的 key 是否齐全；`muad-common` 是否存在 |
| Pod `Pending` | `muad-artifacts-rwx` 是否 Bound、是否支持 RWX |
| 应用启动报表不存在 | 部署前没有执行第 4 节的手动迁移 |
| 服务起来但内部调用 401/403 | 两个 Secret 的 `INTERNAL_SERVICE_TOKEN` 不一致 |
| Ingress 502 | Service/Port 是否匹配；gateway 是否启动；Ingress 注解是否被覆盖 |
| NodePort 聊天不通 | 只暴露了 console，`/api/v1/webchat` 没转到 gateway |
| `helm upgrade` 后 nodeSelector 丢失 | 之前用的是 `kubectl patch`；改用 `service.nodeSelector` |

## 13. 升级 / 回滚

升级前：

```bash
# 1. 备份数据库
# 2. 手动执行迁移（第 4 节）
# 3. 用新 TAG 重新跑 5 条镜像 + Helm 流水线
```

升级：

```bash
helm upgrade muad-console-platform ... -f values-common.yaml --set global.imageTag="$NEW_TAG"
helm upgrade muad-agent-runtime ... -f values-common.yaml --set global.imageTag="$NEW_TAG"
helm upgrade muad-agent-worker ... -f values-common.yaml --set global.imageTag="$NEW_TAG"
helm upgrade muad-im-gateway ... -f values-common.yaml --set global.imageTag="$NEW_TAG"
helm upgrade muad-im-connector ... -f values-common.yaml --set global.imageTag="$NEW_TAG"
```

回滚：

```bash
helm rollback muad-console-platform -n "$NS"
helm rollback muad-agent-runtime -n "$NS"
helm rollback muad-agent-worker -n "$NS"
helm rollback muad-im-gateway -n "$NS"
helm rollback muad-im-connector -n "$NS"
```
