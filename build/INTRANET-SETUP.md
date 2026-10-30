# 内网构建与部署步骤

## 目的

把 5 个服务构建成内网镜像和 Helm 包，推送到 `docker.sangfor.com`，然后按
`build/INTRANET-K8S-DEPLOY.md` 部署到 K8s。

## 产物

| 产物 | 位置 |
|---|---|
| 两个底座镜像 | `docker.sangfor.com/mss-base/python:3.12.13` / `python-node:3.12.13-node24` |
| 底座导出 tar | `/Users/jahan/share/python-3.12.13-amd64-base-images.tar` |
| Python standalone 包 | `/Users/jahan/share/cpython-3.12.13+20260325-x86_64-unknown-linux-gnu-install_only.tar.gz` |
| Node 24 包 | `/Users/jahan/share/node-v24.22.2-linux-x64.tar.gz` |
| 5 个 release 脚本 | `build/docker-build/task-release-*.sh` |
| 前端脚本 | `build/docker-build/task0-console-frontend.sh` |
| 5 个 Helm chart | `build/helm-build/muad-*/` |

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
PLATFORM=linux/amd64 PUSH=1 \
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

### 2.1 变量 / Secret

5 个 release 节点：

```text
DOCKER_USER
DOCKER_PASS
PUSH=1
PLATFORM=linux/amd64
TAG=1.0.0
UPLOAD=1
AUTH_TOKEN
```

console release 额外：

```text
FRONTEND_CACHE_DIR=/tmp/muad-console-frontend
```

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

## 4. K8s 部署

完整部署配置见：

```text
build/INTRANET-K8S-DEPLOY.md
```

所有 `.env` 配置的完整映射见该文档的 **2.7 节**。

顺序：

```text
1. 建 namespace、Secret、artifacts PVC、regcred
2. 部署前手动执行数据库迁移
3. 安装 console-platform
4. 安装其余 4 个服务
5. 验证 /healthz、/readyz
```

数据库迁移命令：

```bash
export DATABASE_URL='postgresql+asyncpg://muad:CHANGE_ME@postgres:5432/muad'
export CONSOLE_IMAGE=docker.sangfor.com/mss-release/muad-console-platform:1.0.0

docker run --rm -e DATABASE_URL="$DATABASE_URL" "$CONSOLE_IMAGE" \
  sh -c 'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini
         python migrations/db_migrate.py -c /tmp/alembic.ini current'
```

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| 底座构建访问不了 GitHub | 把两个源码包放到 `build/docker-build/base/vendor/` |
| 底座 pip 报 `SSLEOFError` | 用内网 HTTP pip 源，或把 `setuptools`/`wheel` wheel 放到 `vendor/` |
| 镜像 push 401 | 检查 `DOCKER_USER` / `DOCKER_PASS` |
| console 报前端缓存缺失 | 检查前端节点上传和 console release 节点下载都是 `/tmp/muad-console-frontend/` |
| release 报 `helm` / `sfspm` 不存在 | 任务镜像缺命令，安装或改用单独的 `task2-helm.sh` |
| 5 个镜像 tag 不一致 | 5 条流水线共用同一个 `TAG` |
| chart 重复上传 | 用了 `task-release-*` 就不要再跑 `task2-helm.sh` |
