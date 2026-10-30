# 内网千流构建流水线

这套脚本用于把 `fluxion-harness` 的 5 个 Python 服务构建成内网镜像，并产出 Helm 包。
依赖安装走内网 `pip install -r build/requirements/<service>.txt`，不依赖 uv/公网。

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
BASE_REGISTRY=muad PLATFORM=linux/amd64 PUSH=0 ./build/base/build.sh

# 内网发布
BASE_REGISTRY=docker.sangfor.com/mss-base \
PLATFORM=linux/amd64 PUSH=1 ./build/base/build.sh
```

两个底座：

- `build/base/Dockerfile.python`：Python 3.12、pip/setuptools/wheel、CA、tzdata、UID 10001。
- `build/base/Dockerfile.python-node`：在上面基础上复制 Node 24，并安装 `libstdc++6`。

> 构建机需要能拉取 `python:3.12-slim-bookworm` / `node:24-bookworm-slim`，或通过
> `PYTHON_IMAGE` / `NODE_IMAGE` 指向内网中转镜像。底座发布一次后，应用镜像只访问内网 pip 源。

> Node 24 只给 `agent-runtime`/`agent-worker`。`console-platform` 的前端构建用 Node 22
> builder，运行镜像不需要 Node。

## 2. requirements.txt

按服务从 `uv.lock` 导出，版本与开发/CI 完全一致：

```bash
./build/scripts/export-requirements.sh
```

生成：

```text
build/requirements/console-platform.txt
build/requirements/agent-runtime.txt
build/requirements/agent-worker.txt
build/requirements/im-gateway.txt
build/requirements/im-connector.txt
```

文件只包含第三方依赖。`muad-*` workspace 包在镜像构建时从本地源码安装：

```dockerfile
RUN pip install --no-deps --no-build-isolation /app/packages/* /app/apps/<service>
```

因此不要手动往 requirements.txt 里加 `-e ./packages/...`。

## 3. 千流任务编排

推荐三个 Task（与老项目 `muad-openclaw/build/docker-build` 的职责对应）：

```text
Task0 (Node)   build/qianliu/task0-frontend.sh
  缓存上传：/tmp/muad-console-frontend/

Task1 (Docker) build/qianliu/task1-images.sh
  缓存下载：/tmp/muad-console-frontend/
  缓存上传：/tmp/muad-build/（images.env + DIST_VERSION + IMAGE_TAG）

Task2 (Docker+Helm) build/qianliu/task2-helm.sh
  缓存下载：/tmp/muad-build/（取 DIST_VERSION）
  缓存上传：/tmp/muad-helm/（HELM_PKGS）
```

Task0 与 Task1 有依赖关系；Task2 不依赖镜像构建结果，可和 Task1 并行（若流水线平台
限制串行，按 Task0 → Task1 → Task2 执行即可）。

### Task0 缓存

```text
/tmp/muad-console-frontend/
  dist/                         # vite build 产物
  frontend-dist-<version>.tar.gz
  DIST_VERSION
```

### Task1 环境变量

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

### Task2 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `AUTH_TOKEN` | `UPLOAD=1` 时必填 | sfspm token |
| `UPLOAD` | 否，默认 1 | 本地验证设 0 |
| `HELM_VERSION` | 否 | 默认 `1.0.<DIST_VERSION>` |
| `CI_COMMIT_BRANCH` | 建议 | 决定 `mss-dev-helm` / `mss-release-helm` |

## 4. 本地验证

```bash
# shell 语法
bash -n build/base/build.sh build/scripts/*.sh build/qianliu/*.sh

# 基础镜像（需要 docker）
BASE_REGISTRY=muad PLATFORM=linux/amd64 PUSH=0 ./build/base/build.sh

# 前端产物（需要 node/npm）
cd apps/console-platform/frontend && npm ci && npm run build
mkdir -p ../../../build/frontend/dist && cp -a dist/. ../../../build/frontend/dist/

# Helm
helm lint build/helm/muad-platform
helm template muad build/helm/muad-platform --namespace muad > /tmp/muad-platform.yaml
./build/scripts/package-helm.sh
```

服务镜像本地构建示例（公网 pip 源，仅用于验证 Dockerfile）：

```bash
BASE_REGISTRY=muad PLATFORM=linux/amd64 PUSH=0 ./build/base/build.sh
TAG=local PUSH=0 IMAGE_NAMESPACE=local \
PYTHON_BASE_IMAGE=muad/muad-python-base:3.12 \
NODE_BASE_IMAGE=muad/muad-python-node-base:3.12-node24 \
PIP_INDEX_URL=https://pypi.org/simple PIP_TRUSTED_HOST=pypi.org \
  ./build/scripts/build-images.sh
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

Helm：

```text
muad-platform-<HELM_VERSION>.tgz
```

部署时用 `--set global.imageTag=<tag>` 对齐本次构建的镜像。

## 6. 与老项目的差异

老项目 `muad-openclaw/build` 的任务映射：

| 老任务 | 新项目对应 | 说明 |
|---|---|---|
| Task0 session-manager CLI | 无 | 新项目没有该 Node CLI |
| Task1 前端 dist | `task0-frontend.sh` | 同样的 npm 构建 + 缓存上传 |
| Task2 Go 后端编译 | 无 | Python 依赖在镜像构建阶段 `pip install` |
| Task3 console 镜像 + Helm | `task1-images.sh` + `task2-helm.sh` | 镜像与 Helm 分开 |
| Task4 openclaw 应用镜像 | `task1-images.sh` 的 runtime/worker | 两个服务共用 Node 底座 |

其它差异：

- 老项目是 Go 二进制 + 前端 dist 的拼装；新项目是 Python 服务，镜像内执行 `pip install -r`。
- 老项目只构建 console + openclaw 两个镜像；新项目一次构建 5 个服务。
- 老项目 Helm 只有 console 一个 chart；新项目提供 `muad-platform` 总 chart，可用
  `imConnector.enabled=true` 切到五单元。
- 老脚本在 task3 中硬编码了内网 registry 凭据默认值；新脚本要求显式注入
  `DOCKER_USER`/`DOCKER_PASS`/`AUTH_TOKEN`，不在仓库保存任何密码。
