# 内网千流构建流水线

这套脚本用于把 `fluxion-harness` 的 5 个 Python 服务构建成内网镜像，并产出 Helm 包。
依赖安装走内网 `pip install -r requirements.txt`，不依赖 uv/公网。

## 1. 组件与基础镜像

| 服务 | 镜像 | 基础镜像 | 说明 |
|---|---|---|---|
| console-platform | `muad-console-platform` | `docker.sangfor.com/mss-base/python:3.12.13` | Python + 前端静态产物 |
| im-gateway | `muad-im-gateway` | `docker.sangfor.com/mss-base/python:3.12.13` | 纯 Python |
| im-connector | `muad-im-connector` | `docker.sangfor.com/mss-base/python:3.12.13` | 纯 Python |
| agent-runtime | `muad-agent-runtime` | `docker.sangfor.com/mss-base/python-node:3.12.13-node24` | JS Skill 需要 Node 24 |
| agent-worker | `muad-agent-worker` | `docker.sangfor.com/mss-base/python-node:3.12.13-node24` | JS Skill 需要 Node 24 |

两个底座只需构建/推送一次：

```bash
# 内网发布（DOCKER_USER / DOCKER_PASS 由千流 Secret 注入）
BASE_REGISTRY=docker.sangfor.com/mss-base \
SOURCE_BASE_IMAGE=docker.sangfor.com/mss-base/python:3.11.2 \
PLATFORM=linux/amd64 PUSH=1 \
DOCKER_USER="$DOCKER_USER" DOCKER_PASS="$DOCKER_PASS" \
  ./build/docker-build/base/build.sh
```

产物：

```text
docker.sangfor.com/mss-base/python:3.12.13
docker.sangfor.com/mss-base/python-node:3.12.13-node24
```

> 底座镜像是独立的一次性任务，不要放进 5 条服务流水线；服务流水线只拉取上面两个
> 已发布的底座。当前 GitHub/本地环境无法访问 `docker.sangfor.com`（`docker manifest
> inspect` 返回 `EOF`），所以底座的实际构建要在内网构建机执行。

### Python 3.12.13 怎么来的

`Dockerfile.python` 以内部 `docker.sangfor.com/mss-base/python:3.11.2` 为底座，
**在底座内部编译 Python 3.12.13**，输出到 `/usr/local`，再把 3.12 运行时复制回
同一个底座。

不采用“从 `python:3.12.13-slim-bookworm` 复制 `/usr/local`”的原因：

- 内部 `python:3.11.2` 按官方同名 tag 对应 Debian 11（glibc 2.31、OpenSSL 1.1.1）。
- 官方 3.12.13 slim 镜像是 bookworm（glibc 2.36、OpenSSL 3）。
- `python:3.12.13-slim-bullseye` 不存在（3.12.13 发布时 bullseye 已停更）。
- 跨发行版复制会让 `_ssl` / `_hashlib` 动态链接到 `libssl.so.3`，在 bullseye 上直接导入失败。

在同一个底座内编译，glibc、OpenSSL、zlib、sqlite 的 ABI 都与底座一致。

### Node 24 怎么来的

`Dockerfile.python-node` 在 Python 底座上解压官方 Node 24 linux tarball 到 `/usr/local`，
并安装 `libstdc++6` / `libatomic1`。使用 tarball 而不是 `node:24-bookworm-slim` 的原因同上：
bookworm 的 Node 二进制按 glibc 2.36 构建，不能直接放进 Debian 11 底座。

### 离线构建

如果构建机访问不了 `python.org` / `nodejs.org`，把以下文件放到
`build/docker-build/base/vendor/`：

```text
Python-3.12.13.tgz
node-v24.22.2-linux-x64.tar.gz      # amd64
node-v24.22.2-linux-arm64.tar.gz    # 仅 PLATFORM=linux/arm64
```

Dockerfile 会优先使用 `vendor/` 下的文件，找不到才走 `PYTHON_SOURCE_URL` /
`NODE_DIST_URL` 下载。可选的 `pip-*.whl` / `setuptools-*.whl` / `wheel-*.whl`
放进同一目录后，构建工具也走离线安装。

> 如果 `pip install` 报 `SSLEOFError`，优先使用内网 HTTP pip 镜像，或把
> `pip` / `setuptools` / `wheel` 三个 wheel 放进 `vendor/` 走 `--no-index`。

### 底座构建环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `BASE_REGISTRY` | 否 | `docker.sangfor.com/mss-base` | 底座仓库 |
| `SOURCE_BASE_IMAGE` | 否 | `docker.sangfor.com/mss-base/python:3.11.2` | 内部 3.11.2 底座 |
| `PYTHON_BASE_TAG` | 否 | `3.12.13` | Python 底座 tag |
| `NODE_BASE_TAG` | 否 | `3.12.13-node24` | Python+Node 底座 tag |
| `PYTHON_VERSION` | 否 | `3.12.13` | 编译的 Python 版本 |
| `NODE_VERSION` | 否 | `24.22.2` | Node 版本 |
| `PYTHON_SOURCE_URL` | 否 | python.org | Python 源码地址 |
| `NODE_DIST_URL` | 否 | nodejs.org | Node tarball 地址 |
| `PIP_INDEX_URL` | 否 | `http://mirrors.sangfor.org/pypi/simple` | 安装 pip/setuptools/wheel |
| `PIP_TRUSTED_HOST` | 否 | `mirrors.sangfor.org` | http 源 |
| `PLATFORM` | 否 | `linux/amd64` | 目标架构 |
| `PUSH` | 否 | `0` | 发布时设 `1` |
| `DOCKER_USER` | `PUSH=1` 时必填 | - | 内网 registry 账号 |
| `DOCKER_PASS` | `PUSH=1` 时必填 | - | 内网 registry 密码 |

> Node 24 只给 `agent-runtime`/`agent-worker`。`console-platform` 的前端构建用 Node
> builder，运行镜像不需要 Node。

### 底座构建失败时先确认什么

先在内网构建机上看一眼 3.11.2 底座的发行版和编译工具：

```bash
docker run --rm --platform linux/amd64 --entrypoint sh \
  docker.sangfor.com/mss-base/python:3.11.2 -c \
  '. /etc/os-release; echo "$ID $VERSION_ID $VERSION_CODENAME"; \
   ldd --version | head -1; \
   command -v gcc make; \
   ls /usr/include/openssl/ssl.h /usr/include/zlib.h /usr/include/sqlite3.h'
```

- 输出是 `debian 11 bullseye` + `gcc`/`make` + 头文件齐全：当前 Dockerfile 可以直接编译。
- 是 slim 底座、缺 `gcc`/头文件：需要构建机能访问 Debian/内网 apt 源，或在
  `vendor/` 里准备好源码并预装编译工具。
- 不是 Debian：Dockerfile 也支持 `dnf`/`yum` 安装依赖；把上面的输出发出来再确认包名。

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

### 前置：千流 Secret

| Secret | 用于 | 说明 |
|---|---|---|
| `DOCKER_USER` | 底座任务、5 个镜像任务 | 内网 registry 账号 |
| `DOCKER_PASS` | 底座任务、5 个镜像任务 | 内网 registry 密码 |
| `AUTH_TOKEN` | Helm 任务 | sfspm token（也兼容 `sfspm_token`） |

`DOCKER_USER` / `DOCKER_PASS` 只在 `PUSH=1` 时读取；脚本不内置任何默认账号密码，
也不把凭据写进仓库。

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
| `PYTHON_BASE_IMAGE` | 否 | `docker.sangfor.com/mss-base/python:3.12.13` | Python 底座 |
| `NODE_BASE_IMAGE` | 否 | `docker.sangfor.com/mss-base/python-node:3.12.13-node24` | Node 底座 |
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

# 基础镜像（本地用公网 python:3.11.2 代替内网底座）
BASE_REGISTRY=local SOURCE_BASE_IMAGE=python:3.11.2 \
PLATFORM=linux/amd64 PUSH=0 \
PIP_INDEX_URL=http://mirrors.aliyun.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.aliyun.com \
  ./build/docker-build/base/build.sh

# 前端产物（需要 node/npm）
cd apps/console-platform/frontend && npm ci && npm run build
mkdir -p ../../../build/frontend/dist && cp -a dist/. ../../../build/frontend/dist/

# 单服务镜像（示例：gateway）
TAG=local PUSH=0 \
PYTHON_BASE_IMAGE=local/python:3.12.13 \
PIP_INDEX_URL=http://mirrors.aliyun.com/pypi/simple \
PIP_TRUSTED_HOST=mirrors.aliyun.com \
  ./build/docker-build/build-image.sh im-gateway

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

## 6. 与老项目 `muad-openclaw` 的对应关系

参考了老项目的**千流任务拆分方式**，但没有逐字复制脚本内容。老项目是
Go 后端 + Node CLI + console/openclaw 两个镜像，新项目是 5 个 Python/FastAPI 服务，
构建单元和产物都不一样。

| 老任务 | 新项目对应 | 为什么不同 |
|---|---|---|
| `task0-session-manager.sh` | 无 | 新项目没有 session-manager / muad-progress 这类 Node CLI，不需要单独构建 Node dist |
| `task1-frontend.sh` | `task0-console-frontend.sh` | 同样是“前端构建 + `/tmp/muad-console-frontend/` 缓存”，但新脚本用 `npm ci`（有 `package-lock.json`）而不是 `npm install`，并把 `dist/` 展开到缓存，console 镜像任务直接复制 |
| `task2-backend.sh` | 无 | 老项目是 Go，需要 `go-compiler` 任务先编译二进制；新项目是 Python，依赖在镜像构建阶段 `pip install -r requirements.txt`，没有独立编译步骤 |
| `task3-assemble.sh` | `task-image-console-platform.sh` + `task2-helm.sh` | 老 task3 同时做“镜像拼装 + docker save 上传 generic + Helm 打包上传”；新项目把镜像和 Helm 拆成两个任务，镜像按服务拆成 5 条独立流水线 |
| `task4-openclaw.sh` | `task-image-agent-runtime.sh` + `task-image-agent-worker.sh` | 老 task4 构建独立的 openclaw CLI 运行镜像；新项目的 runtime/worker 是 Python 服务，只是运行 JS Skill 时需要 Node，因此共用 `python-node` 底座，不再有单独的 CLI 缓存 |

新项目额外多出：

```text
task-image-im-gateway.sh
task-image-im-connector.sh
```

这两个服务在老项目中没有对应物。

其它差异：

- 老项目只构建 console + openclaw 两个镜像；新项目一次构建 5 个服务。
- 老项目 Helm 只有 console 一个 chart；新项目按 5 个服务拆成 5 个独立 chart，
  一次 Task2 全部打包/上传。
- 老脚本在 `task3-assemble.sh` 中硬编码了内网 registry 的默认账号密码；新脚本要求
  显式注入 `DOCKER_USER`/`DOCKER_PASS`/`AUTH_TOKEN`，仓库里不保存任何密码。
- 老脚本用 `set -e`；新脚本统一 `set -euo pipefail`，并对缺失的前端缓存、镜像元数据
  给出明确错误。
- 老 task3 会把镜像 `docker save` 后上传 generic 仓库；新脚本只推送 registry 镜像，
  不额外保存 tar（如果交付要求需要 tar，可以再加一个可选的 `docker save` 步骤）。

> `deploy/k8s/base` 仍是 GitHub/本地 kustomize 部署的事实源；`build/helm-build/` 下的
> 5 个 per-service chart 是内网 Helm 交付形态。两边共享同一组服务名/端口/env 预算，
> 修改其中一侧时必须同步另一侧。
