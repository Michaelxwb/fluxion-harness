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
--set service.replicas=1 \
--set service.type=NodePort \
--set service.nodePort=30080 \
--set service.externalTrafficPolicy=Cluster \
--set service.resources.requests.cpu=100m \
--set service.resources.requests.memory=128Mi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=4Gi \
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
--set service.replicas=1 \
--set service.type=ClusterIP \
--set service.resources.requests.cpu=1 \
--set service.resources.requests.memory=2Gi \
--set service.resources.limits.cpu=4 \
--set service.resources.limits.memory=8Gi
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
--set service.replicas=1 \
--set service.type=ClusterIP \
--set service.nodeSelector.app=openclaw \
--set service.resources.requests.cpu=1 \
--set service.resources.requests.memory=2Gi \
--set service.resources.limits.cpu=4 \
--set service.resources.limits.memory=8Gi
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
--set service.replicas=2 \
--set service.type=NodePort \
--set service.nodePort=30083 \
--set service.externalTrafficPolicy=Cluster \
--set service.resources.requests.cpu=500m \
--set service.resources.requests.memory=1Gi \
--set service.resources.limits.cpu=2 \
--set service.resources.limits.memory=4Gi
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
--set service.replicas=2 \
--set service.type=ClusterIP \
--set service.resources.requests.cpu=250m \
--set service.resources.requests.memory=512Mi \
--set service.resources.limits.cpu=1 \
--set service.resources.limits.memory=1Gi
```

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
| `resources.*` | `service.resources.*` |
| `service.type/nodePort/externalTrafficPolicy` | `service.type/nodePort/externalTrafficPolicy` |
| `publicSkills.pvcName` | `global.artifacts.existingClaim` |

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

NodePort 模式需要 console 30080 + gateway 30083，入口侧按路径分流。
有 Ingress 时建议改成 Ingress：console 和 gateway 都设 `service.type=ClusterIP`，console 设 `ingress.enabled=true`。
