# 内网搭建步骤（最终版）

适用分支：`feat/intranet-build`，本文对应提交 `a9d45079` 之后。

> K8s 部署配置的最终版见 `build/INTRANET-K8S-DEPLOY.md`；本文保留构建/千流步骤和基础部署顺序。

## 0. 产物清单

| 产物 | 位置 |
|---|---|
| 两个底座镜像 tar | `/Users/jahan/share/python-3.12.13-amd64-base-images.tar` |
| Python 3.12.13 standalone 包 | `/Users/jahan/share/cpython-3.12.13+20260325-x86_64-unknown-linux-gnu-install_only.tar.gz` |
| Node 24 包 | `/Users/jahan/share/node-v24.22.2-linux-x64.tar.gz` |
| 5 条 release 脚本 | `build/docker-build/task-release-*.sh` |
| 前端脚本 | `build/docker-build/task0-console-frontend.sh` |
| 5 个 Helm chart | `build/helm-build/muad-*/` |

底座镜像：

```text
docker.sangfor.com/mss-base/python:3.12.13
docker.sangfor.com/mss-base/python-node:3.12.13-node24
```

## 1. 底座镜像

### 1.1 导入现成 tar（推荐）

```bash
docker load -i /Users/jahan/share/python-3.12.13-amd64-base-images.tar

docker images | grep mss-base

docker login docker.sangfor.com -u "$DOCKER_USER" -p "$DOCKER_PASS"
docker push docker.sangfor.com/mss-base/python:3.12.13
docker push docker.sangfor.com/mss-base/python-node:3.12.13-node24
```

验证：

```bash
docker run --rm --platform linux/amd64 \
  docker.sangfor.com/mss-base/python:3.12.13 \
  sh -c 'python --version; python -c "import ssl; print(ssl.OPENSSL_VERSION)"'

docker run --rm --platform linux/amd64 \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24 \
  sh -c 'python --version; node --version'
```

预期：

```text
Python 3.12.13
OpenSSL 3.5.5
Python 3.12.13
v24.22.2
```

### 1.2 可选：在内网重新构建

如果不想用导出的 tar，可以在内网构建机重建：

```bash
mkdir -p build/docker-build/base/vendor

cp '/Users/jahan/share/cpython-3.12.13+20260325-x86_64-unknown-linux-gnu-install_only.tar.gz' \
   build/docker-build/base/vendor/
cp /Users/jahan/share/node-v24.22.2-linux-x64.tar.gz \
   build/docker-build/base/vendor/

DOCKER_USER="$DOCKER_USER" \
DOCKER_PASS="$DOCKER_PASS" \
BASE_REGISTRY=docker.sangfor.com/mss-base \
SOURCE_BASE_IMAGE=docker.sangfor.com/mss-base/python:3.11.2 \
PLATFORM=linux/amd64 PUSH=1 \
  bash ./build/docker-build/base/build.sh
```

内网 `python:3.11.2` 是 PlatOS 1.3 / glibc 2.28，没有 gcc/make；底座使用
python-build-standalone，不需要编译工具。

## 2. 千流 5 条流水线

最终形态：

```text
1. console-platform
     node 1: 前端构建
     node 2: console 镜像 + Helm

2. agent-runtime
     node 1: 镜像 + Helm

3. agent-worker
     node 1: 镜像 + Helm

4. im-gateway
     node 1: 镜像 + Helm

5. im-connector
     node 1: 镜像 + Helm
```

### 2.1 流水线命令

| 流水线 | 节点 | 命令 |
|---|---|---|
| console-platform | 前端 | `bash ./build/docker-build/task0-console-frontend.sh` |
| console-platform | 镜像 + Helm | `bash ./build/docker-build/task-release-console-platform.sh` |
| agent-runtime | 镜像 + Helm | `bash ./build/docker-build/task-release-agent-runtime.sh` |
| agent-worker | 镜像 + Helm | `bash ./build/docker-build/task-release-agent-worker.sh` |
| im-gateway | 镜像 + Helm | `bash ./build/docker-build/task-release-im-gateway.sh` |
| im-connector | 镜像 + Helm | `bash ./build/docker-build/task-release-im-connector.sh` |

不要粘贴整段脚本内容，千流任务里写上面的一行命令即可。

### 2.2 缓存

console 前端节点：

```text
上传：/tmp/muad-console-frontend/
```

console release 节点：

```text
下载：/tmp/muad-console-frontend/
```

5 个 release 节点可选上传：

```text
/tmp/muad-helm/
  muad-<service>-<HELM_VERSION>.tgz
  HELM_PKG
  HELM_VERSION
```

### 2.3 环境变量 / Secret

5 个 release 节点和底座任务都需要：

```text
DOCKER_USER
DOCKER_PASS
PUSH=1
PLATFORM=linux/amd64
```

Helm 上传：

```text
UPLOAD=1
AUTH_TOKEN
```

建议 5 条流水线共用：

```text
TAG=1.0.0
```

console release 额外：

```text
FRONTEND_CACHE_DIR=/tmp/muad-console-frontend
```

### 2.4 任务镜像要求

- 前端节点：Node 镜像（老项目的 `mss-fe-node:20.9.0` 可用），带 `npm`。
- release 节点：Docker 任务镜像，需带 `docker`、`helm`、`sfspm`。
- 如果任务镜像没有 `helm`/`sfspm`，要么在任务里先安装，要么改回
  `task-image-*` + `task2-helm.sh` 的拆分模式。

## 3. 首次构建

1. 确认两个底座已经推到 `docker.sangfor.com/mss-base`。
2. 先跑 console-platform 流水线：
   - 前端节点成功，缓存 `/tmp/muad-console-frontend/` 上传。
   - release 节点下载缓存，构建并推送 console 镜像，再打包/上传 console chart。
3. 其余 4 条流水线可以并行。
4. 检查镜像：

```bash
docker manifest inspect docker.sangfor.com/mss-dev/muad-console-platform:1.0.0
docker manifest inspect docker.sangfor.com/mss-dev/muad-agent-runtime:1.0.0
docker manifest inspect docker.sangfor.com/mss-dev/muad-agent-worker:1.0.0
docker manifest inspect docker.sangfor.com/mss-dev/muad-im-gateway:1.0.0
docker manifest inspect docker.sangfor.com/mss-dev/muad-im-connector:1.0.0
```

5. 检查 chart：

- Helm 仓库：`mss-dev-helm`（feature）或 `mss-release-helm`（release）
- 或千流缓存 `/tmp/muad-helm/`

## 4. K8s 部署前准备

### 4.1 Namespace

```bash
export NS=muad
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
```

### 4.2 Secret

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

- `DATABASE_URL` 必须用 `postgresql+asyncpg://`。
- 密码含特殊字符时先 URL 编码。
- 两个 Secret 里的 `INTERNAL_SERVICE_TOKEN` 必须一致。
- Connector 不需要 `DATABASE_URL`。

### 4.3 镜像拉取 Secret

如果节点没有预置 registry 凭据：

```bash
kubectl -n "$NS" create secret docker-registry muad-registry \
  --docker-server=docker.sangfor.com \
  --docker-username="$DOCKER_USER" \
  --docker-password="$DOCKER_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4.4 artifacts PVC

5 个服务都会挂载：

```text
muad-artifacts-rwx -> /mnt/muad-artifacts
```

方式 A：已有 RWX PVC：

```bash
kubectl -n "$NS" get pvc muad-artifacts-rwx
```

方式 B：由 console chart 创建（需要支持 ReadWriteMany 的 StorageClass）：

```bash
--set global.artifacts.create=true \
--set global.artifacts.storageClassName='<rwx-storage-class>' \
--set global.artifacts.storage=100Gi
```

> PVC 不存在或不支持 RWX 时，5 个服务的 Pod 都会 Pending。

### 4.5 PostgreSQL / Redis

确保：

- PostgreSQL 已创建 `muad` 数据库和用户。
- Redis 可访问。
- 执行迁移的机器/镜像能连到 PostgreSQL。

### 4.6 数据库迁移（部署前手动执行）

内网 Helm chart 不再创建迁移 Job，迁移必须在 `helm upgrade` 之前手动完成。

> `deploy/k8s/base/migrate-job.yaml` 是 GitHub/本地 kustomize 路径，仍保留自动迁移
> Job；它不用于内网 Helm 交付。内网以本节的手动迁移为准。

方式 A：用 console 镜像（推荐，镜像里已带 alembic/sqlalchemy/asyncpg）：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad'
export CONSOLE_IMAGE=docker.sangfor.com/mss-dev/muad-console-platform:1.0.0

docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini current'
```

方式 B：在仓库里执行：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad'
printf '[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n' "$DATABASE_URL" > /tmp/alembic.prod.ini

uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini
uv run python migrations/db_migrate.py -c /tmp/alembic.prod.ini current
```

注意：

- 迁移必须在 `helm upgrade` 之前执行；失败就不要部署新版本。
- `db_migrate.py` 默认执行 `alembic upgrade head`。
- 不要提交 `/tmp/alembic.prod.ini`。
- 升级前先备份数据库。

## 5. Helm 安装

### 5.1 准备 chart

方式 A：从内网 Helm 仓库：

```bash
helm repo add muad '<HELM_REPO_URL>'
helm repo update
```

方式 B：用千流缓存里的 tgz：

```bash
mkdir -p /tmp/muad-charts
# 从 /tmp/muad-helm/ 下载 5 个 tgz 到 /tmp/muad-charts/
```

### 5.2 安装 console-platform（必须先装）

```bash
export TAG=1.0.0
export REGISTRY=docker.sangfor.com/mss-dev   # release 用 mss-release
export CHART_DIR=/tmp/muad-charts            # 或使用 helm repo

helm upgrade --install muad-console-platform \
  "$CHART_DIR/muad-console-platform-${TAG}.tgz" \
  -n "$NS" \
  --set global.imageRegistry="$REGISTRY" \
  --set global.imageTag="$TAG" \
  --set 'global.imagePullSecrets[0].name=muad-registry' \
  --set global.existingSecret=muad-external \
  --set global.connectorSecret=muad-connector-external \
  --set global.artifacts.existingClaim=muad-artifacts-rwx \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.host=muad.example.com \
  --set 'ingress.tls[0].secretName=muad-tls' \
  --set 'ingress.tls[0].hosts[0]=muad.example.com'
```

如果不用 TLS，去掉最后两行。

如果由 chart 创建 PVC，追加：

```bash
--set global.artifacts.create=true \
--set global.artifacts.storageClassName='<rwx-storage-class>'
```

console chart 会创建：

```text
muad-common ConfigMap
muad-artifacts-rwx PVC（可选）
muad-webchat / muad-webchat-sse Ingress
muad-console-platform Deployment/Service
```

### 5.3 安装其余 4 个服务

```bash
for chart in muad-agent-runtime muad-agent-worker muad-im-gateway muad-im-connector; do
  helm upgrade --install "$chart" \
    "$CHART_DIR/${chart}-${TAG}.tgz" \
    -n "$NS" \
    --set global.imageRegistry="$REGISTRY" \
    --set global.imageTag="$TAG" \
    --set 'global.imagePullSecrets[0].name=muad-registry' \
    --set global.existingSecret=muad-external \
    --set global.connectorSecret=muad-connector-external \
    --set global.artifacts.existingClaim=muad-artifacts-rwx
done
```

如果暂时不接企业微信，可以不装 `muad-im-connector`。

## 6. 验证

```bash
kubectl -n "$NS" get pods -o wide
kubectl -n "$NS" get svc,ingress

kubectl -n "$NS" logs deploy/muad-console-platform --tail=100

kubectl -n "$NS" port-forward svc/muad-console-platform 8000:8000
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

其余服务端口：

```text
console-platform 8000
agent-runtime    8001
agent-worker     8002
im-gateway       8003
im-connector     8004
```

每个服务都有 `/healthz` 和 `/readyz`。

浏览器访问：

```text
https://muad.example.com/
```

## 7. 升级 / 回滚

升级：

```bash
# 用新的 TAG 重新跑 5 条流水线
helm upgrade muad-console-platform ... --set global.imageTag="$NEW_TAG"
helm upgrade muad-agent-runtime ... --set global.imageTag="$NEW_TAG"
...
```

注意：

- 升级前先手动执行数据库迁移（见 4.6），再 `helm upgrade`。
- 镜像 tag 不要复用；每次发布用新 `TAG`。
- 回滚：

```bash
helm rollback muad-console-platform -n "$NS"
helm rollback muad-agent-runtime -n "$NS"
...
```

## 8. 常见问题

| 现象 | 排查 |
|---|---|
| `ImagePullBackOff` | 镜像 tag/registry 是否正确；`muad-registry` 是否存在；`kubectl describe pod` |
| `CreateContainerConfigError` | `muad-external` / `muad-connector-external` 键是否齐全；`muad-common` ConfigMap 是否存在 |
| Pod `Pending` | `muad-artifacts-rwx` 是否 Bound；StorageClass 是否支持 RWX |
| 应用启动报表不存在/列不存在 | 部署前没有执行 4.6 的数据库迁移 |
| 服务起来但内部调用 401/403 | 两个 Secret 的 `INTERNAL_SERVICE_TOKEN` 是否一致；改完重启 Pod |
| 千流 release 任务报 `helm: command not found` | Docker 任务镜像没有 helm；安装或改用 `task2-helm.sh` 单独流水线 |
| 千流 release 任务报 `sfspm: command not found` | 同上 |
| chart 重复上传 | 用了 `task-release-*` 就不要再跑 `task2-helm.sh` |
| 5 个镜像 tag 不一致 | 5 条流水线共用同一个 `TAG` |
| 底座构建访问不了 GitHub | 把两个源码包放到 `build/docker-build/base/vendor/` |
| 底座构建访问不了 PyPI | 把 `setuptools-*.whl` / `wheel-*.whl` 放到 `vendor/`，或使用内网 HTTP pip 源 |
