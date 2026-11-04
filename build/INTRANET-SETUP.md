# 内网构建与部署步骤

## 目的

把 5 个服务构建成内网镜像和 Helm 包，推送到 `docker.sangfor.com`，然后按
`build/INTRANET-K8S-DEPLOY.md` 部署到 K8s。需要单 NodePort 同时提供页面和聊天时，
再构建可选的 nginx 统一入口镜像。

## 产物

| 产物 | 位置 |
|---|---|
| 两个底座镜像 | `docker.sangfor.com/mss-base/python:3.12.13` / `python-node:3.12.13-node24` |
| 底座导出 tar | `/Users/jahan/share/python-3.12.13-amd64-base-images.tar` |
| Python standalone 包 | `/Users/jahan/share/cpython-3.12.13+20260325-x86_64-unknown-linux-gnu-install_only.tar.gz` |
| Node 24 包 | `/Users/jahan/share/node-v24.22.2-linux-x64.tar.gz` |
| 5 个 release 脚本 | `build/docker-build/task-release-*.sh` |
| 前端脚本 | `build/docker-build/task0-console-frontend.sh` |
| 6 个 Helm chart | `build/helm-build/muad-*/`（5 个服务 + `muad-nginx`） |
| nginx 入口构建 | `build/nginx-build/` |
| nginx 入口 chart 任务 | `build/docker-build/task-release-nginx-entry.sh` |
| nginx 入口静态 YAML | `build/nginx-entry.yaml` |

## 1. 底座镜像

### 1.1 导入现成 tar（推荐）

```bash
docker load -i /Users/jahan/share/python-3.12.13-amd64-base-images.tar

docker login docker.sangfor.com -u "$DOCKER_USER" -p "$DOCKER_PASS"
docker push docker.sangfor.com/mss-base/python:3.12.13
docker push docker.sangfor.com/mss-base/python-node:3.12.13-node24
```

验证：

```bash
docker run --rm --platform linux/amd64 \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24 \
  sh -c 'python --version; node --version'
```

预期：

```text
Python 3.12.13
v24.22.2
```

### 1.2 可选：内网重新构建

把两个源码包放到：

```text
build/docker-build/base/vendor/
```

```bash
DOCKER_USER="$DOCKER_USER" \
DOCKER_PASS="$DOCKER_PASS" \
BASE_REGISTRY=docker.sangfor.com/mss-base \
SOURCE_BASE_IMAGE=docker.sangfor.com/mss-base/python:3.11.2 \
PLATFORM=linux/amd64 \
  bash ./build/docker-build/base/build.sh
```

内网 `python:3.11.2` 是 PlatOS 1.3 / glibc 2.28，没有 gcc/make，底座使用
python-build-standalone，不需要编译。

## 2. 千流 5 条流水线

| 流水线 | 节点 | 命令 | 缓存 |
|---|---|---|---|
| console-platform | 前端 | `bash ./build/docker-build/task0-console-frontend.sh` | 上传 `/tmp/muad-console-frontend/` |
| console-platform | 镜像 + Helm | `bash ./build/docker-build/task-release-console-platform.sh` | 下载 `/tmp/muad-console-frontend/`；上传 `/tmp/muad-helm/` |
| agent-runtime | 镜像 + Helm | `bash ./build/docker-build/task-release-agent-runtime.sh` | 上传 `/tmp/muad-helm/` |
| agent-worker | 镜像 + Helm | `bash ./build/docker-build/task-release-agent-worker.sh` | 上传 `/tmp/muad-helm/` |
| im-gateway | 镜像 + Helm | `bash ./build/docker-build/task-release-im-gateway.sh` | 上传 `/tmp/muad-helm/` |
| im-connector | 镜像 + Helm | `bash ./build/docker-build/task-release-im-connector.sh` | 上传 `/tmp/muad-helm/` |

说明：

- 前端节点必须在 console release 节点之前。
- 其余 4 条可以并行。
- Helm 在 release 节点内完成，不需要单独 Helm 流水线。
- `build/helm-build/task2-helm.sh` 仅作为“一次打包全部 5 个 chart”的备用，不要和
  `task-release-*` 同时跑。
- 需要 nginx 统一入口时再加第 6 条 `nginx-entry` 流水线，见 4.1；它不影响这 5 条。

### 2.1 变量 / Secret

5 个 release 节点只需要三个 Secret：

```text
DOCKER_USER
DOCKER_PASS
AUTH_TOKEN
```

其余变量都有默认值，不用配：

```text
TAG                       默认 1.0.0.<构建时间><分支名>（git tag 发布时用 tag）
PLATFORM                  默认 linux/amd64
FRONTEND_CACHE_DIR        默认 /tmp/muad-console-frontend（前端任务和 console 任务一致）
```

`PUSH` 不用配，也不要配：

```text
有 DOCKER_USER / DOCKER_PASS -> 登录并推送
没有凭据                     -> 千流任务入口直接报错（避免“构建成功但没推送”）
```

千流任务（`task-image-*.sh` / `task-release-*.sh`）会强制校验 DOCKER 凭据和 `AUTH_TOKEN`。
本地直接跑 `build-image.sh` / `package-service.sh` 时可以不配，只构建/打包不上传；
需要强制不推送/不上传时才设 `PUSH=0` / `UPLOAD=0`。

默认 tag 用**本次流水线运行的构建时间**：同一次运行的所有服务共用一个 tag，下一次
运行是新 tag；部署按服务读取 `release-<service>.env`，不要重新算。时间戳来自流水线级
变量（`TAG_TIMESTAMP`，或平台提供的 `CI_PIPELINE_CREATED_AT`），不是各任务自己的时钟，
否则并行任务会得到不同 tag。

### 2.2 任务镜像

- 前端节点：Node 镜像，带 `npm`（例如 `mss-fe-node:20.9.0`）。
- release 节点：Docker 镜像，带 `docker`、`helm`、`sfspm`。

## 3. 首次构建

1. 确认两个底座已经推到 `docker.sangfor.com/mss-base`。
2. 跑 console-platform 流水线：前端 → console 镜像 + Helm。
3. 跑其余 4 条流水线。
4. 检查镜像：

```bash
docker manifest inspect docker.sangfor.com/mss-release/muad-console-platform:1.0.0
docker manifest inspect docker.sangfor.com/mss-release/muad-agent-runtime:1.0.0
docker manifest inspect docker.sangfor.com/mss-release/muad-agent-worker:1.0.0
docker manifest inspect docker.sangfor.com/mss-release/muad-im-gateway:1.0.0
docker manifest inspect docker.sangfor.com/mss-release/muad-im-connector:1.0.0
```

5. 检查 chart：

```text
Helm 仓库：mss-release-helm（release）或 mss-dev-helm（feature）
或千流缓存：/tmp/muad-helm/
```

## 4. 构建 nginx 入口镜像（可选）

如果不想维护 console 30090 + gateway 30093 两个 NodePort，也不想上 Ingress，构建一个 nginx 入口：

```bash
BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2 \
IMAGE_REPOSITORY=docker.sangfor.com/mss-release/muad-nginx \
  ./build/nginx-build/build.sh
```

默认从 PlatOS/Kylin RPM 安装 nginx。RPM 失败时用 copy 模式：

```bash
NGINX_BUILD_MODE=copy \
NGINX_SOURCE_IMAGE=<和底座 glibc 兼容的 nginx 镜像> \
BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2 \
IMAGE_REPOSITORY=docker.sangfor.com/mss-release/muad-nginx \
  ./build/nginx-build/build.sh
```

`nginx:1.24-bullseye` 是 glibc 2.31；底座是 glibc 2.28 时不要直接用它。
优先 RPM 模式，或把 `NGINX_SOURCE_IMAGE` 指向现有 `portal-nginx` 的镜像。

详细说明见 `build/nginx-build/README.md`。

### 4.1 千流流水线

单独一条 `nginx-entry` 流水线，和 5 个服务镜像流水线并行：

| 项 | 值 |
|---|---|
| 流水线名 | `nginx-entry` |
| 节点 | 1 个 Docker 节点（和 release 节点同类） |
| 任务镜像 | 镜像 + Helm 用 release 节点（带 `docker`、`helm`、`sfspm`）；只构建镜像时带 `docker` 即可 |
| 命令 | `bash ./build/docker-build/task-release-nginx-entry.sh`（镜像 + Helm） |
| Secret | `DOCKER_USER`、`DOCKER_PASS`、`AUTH_TOKEN` |
| 缓存 | 上传 `/tmp/muad-helm/` |

可选变量（一般不用配）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BASE_IMAGE` | `docker.sangfor.com/mss-base/platos-kylin:1.3.2` | 构建底座 |
| `IMAGE_REPOSITORY` | 按分支路由 | 覆盖目标仓库 |
| `TAG` | `1.0.0.<构建时间><分支名>` | 和 5 个服务同 tag |
| `NGINX_BUILD_MODE` | `rpm` | 装不上时改 `copy` |
| `NGINX_SOURCE_IMAGE` | `nginx:1.24-bullseye` | 仅 copy 模式使用 |
| `PLATFORM` | `linux/amd64` | 目标架构 |

产物：

```text
git tag / patch / master / release -> docker.sangfor.com/mss-release/muad-nginx:<TAG>
feature / bug / 其它               -> docker.sangfor.com/mss-dev/muad-nginx:<TAG>

Helm：mss-release-helm/muad-nginx-<HELM_VERSION>.tgz
      mss-dev-helm/muad-nginx-<HELM_VERSION>.tgz
```

只要镜像、不要 chart 时，用 `task-image-nginx-entry.sh`（不需要 `helm` / `sfspm`）。

`<TAG>` 按服务从 `release-<service>.env` 读取，部署时不需要手动统一
`global.imageTag=<TAG>`。

日志是 6 步，能直接看出卡在哪：

```text
[nginx] [1/6] resolve configuration
[nginx] [2/6] docker login docker.sangfor.com
[nginx] [3/6] ensure base image docker.sangfor.com/mss-base/platos-kylin:1.3.2
[nginx] [4/6] docker build docker.sangfor.com/mss-release/muad-nginx:<TAG>
[nginx] [5/6] docker push docker.sangfor.com/mss-release/muad-nginx:<TAG>
[nginx] [6/6] write build metadata
[nginx] done in 42s: docker.sangfor.com/mss-release/muad-nginx:<TAG>
```

缺 `DOCKER_USER` / `DOCKER_PASS` 时任务入口会直接报错，不会“构建成功但没推送”。

如果第 4 步 RPM 安装失败，日志会停在该步，按第 4 节换成 copy 模式重跑。

### 4.2 部署这条流水线的产物

chart 名和 release 名都用 `muad-nginx`。

部署前先把 console / gateway 改成 ClusterIP：

```text
console-platform: service.type=ClusterIP
im-gateway:       service.type=ClusterIP
```

用 Helm 部署。`--set` 参数块（和 5 个服务格式一致，镜像 tag 由平台从
`release-<service>.env` 注入，不要写 `$TAG`）：

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

命令行安装时，把整块接在：

```text
helm upgrade --install muad-nginx mss-release-helm/muad-nginx --namespace muad
```

需要固定到 openclaw 节点时，在块里加：

```bash
--set global.nodeSelector.app=openclaw
```

参数说明见 `build/INTRANET-K8S-DEPLOY.md` 9.3。

也可以继续用静态 YAML（不经过 Helm）：

```bash
TAG=<流水线产出的 tag>
sed "s#muad-nginx:1.0.0#muad-nginx:${TAG}#" build/nginx-entry.yaml \
  | kubectl apply -f -
```

访问：

```text
http://<master-ip>:30090/
```

nginx 启动时要解析 `muad-console-platform:8000` 和 `muad-im-gateway:8003`，
必须晚于这两个 Service 部署。

这个任务不重建 5 个服务镜像，也不重建底座；改 Pod 资源/副本更不需要重跑它。

## 5. K8s 部署

完整部署配置见：

```text
build/INTRANET-K8S-DEPLOY.md
```

所有 `.env` 配置的完整映射见该文档的 **2.7 节**。

### 5.1 Redis Cluster（内网必配）

内网 Redis 只有 **3 主 3 从 Cluster**（客户端端口 6393/6394），没有单机可切换；部署前确认：

1. **Secret**（`muad-external` 与 `muad-connector-external`）的 `REDIS_URL` 用
   `redis://:<password>@<seed>:6393/0`：Cluster 只支持 db 0，URL 路径必须是 `/0`，
   两个 Secret 的值一致；密码含 `@ : / ? #` 时先做 URL 编码。
2. **ConfigMap `muad-common`** 显式设置：

   ```yaml
   REDIS_MODE: cluster
   REDIS_CLUSTER_SEEDS: "10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393"
   ```

   默认 `standalone` 会因实际拓扑不匹配而启动失败。部署步骤不变：照常
   `kubectl apply -f deploy/k8s/base/configmap.yaml`，再补一次 merge patch 把这两个键改成
   上面的值（命令见 `deploy/k8s/README.md` 的「内网 Redis Cluster」）。patch 可重复执行；
   ConfigMap 更新不会自动重启 Pod，改完需 `kubectl rollout restart` 5 个 Deployment。
3. **网络**放行 6 个通告地址（`10.74.145.45/46/47` × `6393/6394`）：客户端按
   `CLUSTER SLOTS` 连接所有 master，不只是 seed。
4. **上线前跑探针**：

   ```bash
   python scripts/probe_redis_cluster.py --url "$REDIS_URL" \
     --nodes 10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393,10.74.145.45:6394,10.74.145.46:6394,10.74.145.47:6394
   python scripts/probe_redis_cluster_commands.py --url "$REDIS_URL" --seeds "$REDIS_CLUSTER_SEEDS"
   ```

   第一条检查地址可达、版本 ≥6.2、`redis_mode=cluster`、槽位与内存/连接上限；第二条检查
   11 项命令面与 key 契约（同 slot `XREADGROUP`/`XAUTOCLAIM`/双 key `EVAL`、跨节点
   `PUBLISH`、跨 slot 拒绝等）。探针在能访问集群的运维机/跳板机执行；镜像未包含
   `scripts/`，如需 Pod 内执行先 `kubectl cp` 拷入。

完整 Secret/ConfigMap 示例见 `build/INTRANET-K8S-DEPLOY.md` §1.2、§1.3。

顺序：

```text
1. 建 namespace、Secret、muad-common ConfigMap、artifacts PVC
2. 部署前手动执行数据库迁移
3. 安装 5 个服务（无顺序依赖，可以并行）
4. 验证 /healthz、/readyz
```

如果使用 nginx 统一入口：

```text
1. console / gateway 改成 service.type=ClusterIP，去掉 NodePort
2. helm upgrade --install muad-nginx mss-release-helm/muad-nginx（参数见 4.2）
   或 kubectl apply -f build/nginx-entry.yaml
3. 访问 http://<master-ip>:30090/
```

详见 `build/INTRANET-K8S-DEPLOY.md` 第 9 节。

数据库迁移命令：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad_db'
export CONSOLE_IMAGE=docker.sangfor.com/mss-release/muad-console-platform:1.0.0

docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini current'
```

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 底座构建访问不了 GitHub | 把两个源码包放到 `build/docker-build/base/vendor/` |
| 底座 pip 报 `SSLEOFError` | 用内网 HTTP pip 源，或把 `setuptools`/`wheel` wheel 放到 `vendor/` |
| 镜像 push 401 | 检查 `DOCKER_USER` / `DOCKER_PASS` |
| console 报前端缓存缺失 | 检查前端节点上传和 console release 节点下载都是 `/tmp/muad-console-frontend/` |
| release 报 `helm` / `sfspm` 不存在 | 任务镜像缺命令，安装或改用单独的 `task2-helm.sh` |
| 5 个镜像 tag 不一致 | 构建日志里找 `[image-tag] warning`：流水线没提供 `TAG_TIMESTAMP` / `CI_PIPELINE_CREATED_AT`，各任务用了自己的时钟；在流水线里设置一次 `TAG_TIMESTAMP` |
| chart 重复上传 | 用了 `task-release-*` 就不要再跑 `task2-helm.sh` |
| nginx 报 `libprofiler.so.0` | RPM 缺 gperftools 依赖；构建会自动尝试 `gperftools-libs` 等包，仍失败改用 copy 模式 |
| nginx RPM 安装失败 | 改用 `NGINX_BUILD_MODE=copy`，或把 `NGINX_SOURCE_IMAGE` 指向 `portal-nginx` 镜像 |
| nginx 30090 端口冲突 | console / gateway 必须改成 ClusterIP，并去掉它们的 NodePort |
| nginx 起来但聊天 404 | 检查 `build/nginx-entry.yaml` 的 nginx 配置和 gateway Service DNS |
