# 内网千流构建流水线

这套脚本用于把 `fluxion-harness` 的 5 个 Python 服务构建成内网镜像，并产出 Helm 包。
依赖安装走内网 `pip install -r requirements.txt`，不依赖 uv/公网。

## 1. 组件与基础镜像

| 服务 | 镜像 | 基础镜像 | 说明 |
|---|---|---|---|
| console-platform | `muad-console-platform` | `muad-python-base:3.12` | Python + 前端静态产物 |
| im-gateway | `muad-im-gateway` | `muad-python-base:3.12` | 纯 Python |
| im-connector | `muad-im-connector` | `muad-python-base:3.12` | 纯 Python |
| agent-runtime | `muad-agent-runtime` | `muad-python-node-base:3.12-node24` | JS Skill 需要 Node 24 |
| agent-worker | `muad-agent-worker` | `muad-python-node-base:3.12-node24` | JS Skill 需要 Node 24 |

基础镜像只需构建/推送一次：

```bash
# 本地验证（不打标签到内网仓库）
BASE_REGISTRY=muad PLATFORM=linux/amd64 PUSH=0 ./build/docker-build/base/build.sh

# 内网发布
BASE_REGISTRY=docker.sangfor.com/mss-base \
PLATFORM=linux/amd64 PUSH=1 ./build/docker-build/base/build.sh
```

两个底座：

- `build/docker-build/base/Dockerfile.python`：Python 3.12、pip/setuptools/wheel、CA、tzdata、UID 10001。
- `build/docker-build/base/Dockerfile.python-node`：在上面基础上复制 Node 24，并安装 `libstdc++6`。

> 构建机需要能拉取 `python:3.12-slim-bookworm` / `node:24-bookworm-slim`，或通过
> `PYTHON_IMAGE` / `NODE_IMAGE` 指向内网中转镜像。底座发布一次后，应用镜像只访问内网 pip 源。

> Node 24 只给 `agent-runtime`/`agent-worker`。`console-platform` 的前端构建用 Node 22
> builder，运行镜像不需要 Node。

## 2. requirements.txt

五个服务共用项目根目录的一份 `requirements.txt`，从 `uv.lock` 一次性导出：

```bash
./build/docker-build/export-requirements.sh
```

生成 `requirements.txt`，包含所有服务的第三方依赖。`muad-*` workspace 包在镜像构建时
从本地源码安装：

```dockerfile
RUN pip install --no-deps --no-build-isolation /app/packages/* /app/apps/<service>
```

因此不要手动往 `requirements.txt` 里加 `-e ./packages/...`。所有服务镜像都执行
`pip install -r requirements.txt`，不按服务拆分依赖文件。

## 3. 千流任务编排

千流按“一个服务一条流水线”编排，不在一个脚本里同时构建多个镜像；构建任务只做打包，
不跑 lint、单测或其它前置检查。

### 前端任务（Node）

```text
build/docker-build/task0-console-frontend.sh
  缓存上传：/tmp/muad-console-frontend/
```

缓存内容：

```text
/tmp/muad-console-frontend/
  dist/                         # vite build 产物
  frontend-dist-<version>.tar.gz
  DIST_VERSION
```

### 五个镜像任务（Docker，彼此独立并行）

```text
build/docker-build/task-image-console-platform.sh   # 依赖前端缓存
build/docker-build/task-image-agent-runtime.sh
build/docker-build/task-image-agent-worker.sh
build/docker-build/task-image-im-gateway.sh
build/docker-build/task-image-im-connector.sh
```

- console-platform 任务从 `/tmp/muad-console-frontend/dist` 取前端产物，和后端一起打进
  `muad-console-platform` 镜像。
- 其余四个任务不需要前端缓存，可以独立并行。
- 每个任务只构建/推送**自己的一个镜像**；互不依赖。
- 每个镜像任务可选缓存输出：`/tmp/muad-image-<service>/`（`image.txt` + `IMAGE_TAG`），
  仅供需要把镜像 tag 传给后续任务的场景使用；Helm 任务不依赖它。

### Helm 任务（Docker + Helm）

```text
build/helm-build/task2-helm.sh
  缓存上传：/tmp/muad-helm/
```

Task2 一次打包 5 个 per-service chart，并可选上传 5 个 tgz；不依赖镜像构建结果。
如果流水线需要严格串行，按“前端 → 五个镜像 → Helm”执行。

### 环境变量（镜像任务）

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `DOCKER_USER` | `PUSH=1` 时必填 | - | 内网 registry 账号 |
| `DOCKER_PASS` | `PUSH=1` 时必填 | - | 内网 registry 密码 |
| `CI_COMMIT_BRANCH` | 建议 | - | 分支路由 |
| `CI_COMMIT_TAG` | 可选 | - | `v*` 走 release |
| `PUSH` | 否 | `1` | 本地测试设 0 |
| `PLATFORM` | 否 | `linux/amd64` | 集群架构 |
| `PYTHON_BASE_IMAGE` | 否 | `docker.sangfor.com/mss-base/muad-python-base:3.12` | 底座 |
| `NODE_BASE_IMAGE` | 否 | `docker.sangfor.com/mss-base/muad-python-node-base:3.12-node24` | Node 底座 |
| `PIP_INDEX_URL` | 否 | `http://mirrors.sangfor.org/pypi/simple` | 内网 pip 源 |
| `PIP_TRUSTED_HOST` | 否 | `mirrors.sangfor.org` | http 源 |
| `FRONTEND_CACHE_DIR` | console 任务 | `/tmp/muad-console-frontend` | 前端缓存下载路径 |
| `IMAGE_CACHE_DIR` | 否 | `/tmp/muad-image-<service>` | 单镜像元数据缓存 |

分支路由与老项目一致：

```text
v* / patch / master / release  -> docker.sangfor.com/mss-release
feature / bug                  -> docker.sangfor.com/mss-dev
其它                           -> docker.sangfor.com/mss-dev
```

镜像 tag：

```text
CI_COMMIT_TAG 存在  -> 直接使用 tag
否则                -> <yyMMddHHmm><branch，/ 替换为 _>
```

### 环境变量（前端任务）

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `NPM_REGISTRY` | 否 | `http://npm.uedc.sangfor.com.cn/` | 内网 npm 源 |
| `DIST_VERSION` | 否 | 时间戳 | 缓存版本号 |
| `FRONTEND_CACHE_DIR` | 否 | `/tmp/muad-console-frontend` | 缓存输出路径 |

### 环境变量（Helm 任务）

| 变量 | 必填 | 说明 |
|---|---|---|
| `AUTH_TOKEN` | `UPLOAD=1` 时必填 | sfspm token |
| `UPLOAD` | 否，默认 1 | 本地验证设 0 |
| `HELM_VERSION` | 否 | 默认 `1.0.<DIST_VERSION>` |
| `CI_COMMIT_BRANCH` | 建议 | 决定 `mss-dev-helm` / `mss-release-helm` |

## 4. 本地验证

> 以下 `verify.sh` / `helm lint` 只是本地开发检查，不接入千流构建任务；内网任务只执行构建/打包。

```bash
# shell 语法
bash -n build/docker-build/base/build.sh build/docker-build/*.sh build/helm-build/*.sh

# 基础镜像（需要 docker）
BASE_REGISTRY=muad PLATFORM=linux/amd64 PUSH=0 ./build/docker-build/base/build.sh

# 前端产物（需要 node/npm）
cd apps/console-platform/frontend && npm ci && npm run build
mkdir -p ../../../build/frontend/dist && cp -a dist/. ../../../build/frontend/dist/

# 单服务镜像（示例：gateway）
TAG=local PUSH=0 PYTHON_BASE_IMAGE=muad/muad-python-base:3.12 PIP_INDEX_URL=https://pypi.org/simple PIP_TRUSTED_HOST=pypi.org   ./build/docker-build/build-image.sh im-gateway

# Helm：5 个独立 chart
for chart in muad-console-platform muad-agent-runtime muad-agent-worker muad-im-gateway muad-im-connector; do
  helm lint "build/helm-build/${chart}"
  helm template "muad-${chart}" "build/helm-build/${chart}" --namespace muad >/tmp/"${chart}".yaml
done
./build/helm-build/package-helm.sh
```

## 5. 镜像/Helm 命名

镜像：

```text
docker.sangfor.com/mss-dev/muad-console-platform:<tag>
docker.sangfor.com/mss-dev/muad-agent-runtime:<tag>
docker.sangfor.com/mss-dev/muad-agent-worker:<tag>
docker.sangfor.com/mss-dev/muad-im-gateway:<tag>
docker.sangfor.com/mss-dev/muad-im-connector:<tag>
```

Helm（5 个独立 chart）：

```text
muad-console-platform-<HELM_VERSION>.tgz
muad-agent-runtime-<HELM_VERSION>.tgz
muad-agent-worker-<HELM_VERSION>.tgz
muad-im-gateway-<HELM_VERSION>.tgz
muad-im-connector-<HELM_VERSION>.tgz
```

部署时用 `--set global.imageTag=<tag>` 对齐本次构建的镜像。建议安装顺序：
`muad-console-platform`（创建共享 ConfigMap / 可选 PVC / Ingress / 迁移 Job）→ 其余四个服务。
Secret 和已有的 RWX PVC 是前置条件。

## 6. 与老项目的差异

> `deploy/k8s/base` 仍是 GitHub/本地 kustomize 部署的事实源；`build/helm-build/` 下的
> 5 个 per-service chart 是内网 Helm 交付形态。两边共享同一组服务名/端口/env 预算，
> 修改其中一侧时必须同步另一侧。

老项目 `muad-openclaw/build` 的任务映射：

| 老任务 | 新项目对应 | 说明 |
|---|---|---|
| Task0 session-manager CLI | 无 | 新项目没有该 Node CLI |
| Task1 前端 dist | `task0-console-frontend.sh` | 同样的 npm 构建 + 缓存上传 |
| Task2 Go 后端编译 | 无 | Python 依赖在镜像构建阶段 `pip install` |
| Task3 console 镜像 + Helm | `task-image-console-platform.sh` + `task2-helm.sh` | 镜像与 Helm 分开 |
| Task4 openclaw 应用镜像 | `task-image-agent-*.sh` | 两个服务共用 Node 底座 |

其它差异：

- 老项目是 Go 二进制 + 前端 dist 的拼装；新项目是 Python 服务，镜像内执行 `pip install -r`。
- 老项目只构建 console + openclaw 两个镜像；新项目一次构建 5 个服务。
- 老项目 Helm 只有 console 一个 chart；新项目按 5 个服务拆成 5 个独立 chart，
  一次 Task2 全部打包/上传。
- 老脚本在 task3 中硬编码了内网 registry 凭据默认值；新脚本要求显式注入
  `DOCKER_USER`/`DOCKER_PASS`/`AUTH_TOKEN`，不在仓库保存任何密码。
