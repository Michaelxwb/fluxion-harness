# Fluxion Harness 部署说明

本目录提供 Fluxion Harness 的部署产物：

```text
deploy/
├── docker/                     Docker 镜像与本地一键编排
│   ├── Dockerfile              多阶段构建（前端 Vite + 后端 Python 3.12）
│   ├── entrypoint.sh           入口脚本：桥接环境变量到 fluxion serve
│   └── docker-compose.yml      本地 PostgreSQL + Fluxion 后端
├── helm/fluxion/               最小可用 Helm Chart（Deployment/Service/Secret/ConfigMap）
└── README.md                   本文件
```

> 本地纯开发（不涉及部署产物）直接使用 `fluxion serve --dev`（SQLite + 前端 dev bundle），
> 见仓库根 `README.md`。

## 环境变量约定

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `FLUXION_DATABASE_URL` | 生产必填 | 数据库 DSN，例如 `postgresql+asyncpg://user:pass@host:5432/fluxion` |
| `FLUXION_MASTER_KEY` | 生产必填 | 32 字节 AES-256-GCM key 的 base64 |
| `FLUXION_ROLE` | 可选 | 进程角色：`api`（默认，Control Plane）/ `runtime`（AgentLoop 独立进程）/ `worker`（DBOS workflow） |
| `FLUXION_ENV` | 可选 | 运行环境标识，默认 `development` |
| `FLUXION_LOG_LEVEL` | 可选 | 日志级别，默认 `INFO` |

> 说明：后端 CLI 的 `fluxion serve` 通过 `--registry-dsn` 接收数据库 DSN（不会自动读取
> `FLUXION_DATABASE_URL`），且代码内 Secret Store 实际读取的变量名是
> `FLUXION_SECRET_MASTER_KEY`。`entrypoint.sh` 会自动完成这两个变量的桥接，部署时你只需
> 提供 `FLUXION_DATABASE_URL` 与 `FLUXION_MASTER_KEY`。

## MASTER_KEY 生成

`FLUXION_MASTER_KEY` 是 AES-256-GCM 所需的 32 字节随机 key，部署时以 base64 提供：

```bash
openssl rand -base64 32
```

示例输出（勿在生产使用示例值）：

```text
x8Q3uVn1yR4tP6aZ9cW2eF5hJ7kL0mN8oQ1sT3uV6wY=
```

该 key 必须保存在外部安全位置（环境变量、K8s Secret、密钥管理服务），严禁写入源码或镜像。

## 一、Docker 本地一键启动

前置：本机已安装 Docker（含 Compose v2），并在**仓库根目录**执行。

```bash
# 1. 生成并导出 MASTER_KEY
export FLUXION_MASTER_KEY="$(openssl rand -base64 32)"

# 2. 构建并启动（postgres + fluxion）
docker compose -f deploy/docker/docker-compose.yml up --build -d

# 3. 查看状态
docker compose -f deploy/docker/docker-compose.yml ps

# 4. 健康检查（生产模式暴露 /healthz 与 /readyz）
curl http://127.0.0.1:8000/healthz
```

停止与清理：

```bash
docker compose -f deploy/docker/docker-compose.yml down          # 停止并删除容器
docker compose -f deploy/docker/docker-compose.yml down -v       # 连同 postgres 数据卷一起删除
```

说明：

- `fluxion` 服务使用 `deploy/docker/Dockerfile` 从仓库根构建；前端先经 node:22 + pnpm
  构建出 `console` / `chat` 的 `dist`，再复制进 python:3.12-slim 运行镜像。
- 生产模式 `fluxion serve`（非 dev）走 PostgreSQL，不依赖前端产物；前端 `dist` 仍按仓库
  相对路径 `frontend/apps/{console,chat}/dist` 放入镜像，保证 `--dev` 模式也可用。

## 二、Helm 安装（Kubernetes）

前置：已配置可访问的 K8s 集群，并已构建、推送镜像到可拉取的仓库。

### 1. 准备镜像

```bash
docker build -f deploy/docker/Dockerfile -t <registry>/fluxion-harness/fluxion:0.1.0 .
docker push <registry>/fluxion-harness/fluxion:0.1.0
```

### 2. 拉取子 chart 依赖（内置 PostgreSQL）

```bash
cd deploy/helm/fluxion
helm dependency update
```

### 3. 安装（内置 PostgreSQL）

```bash
helm install fluxion . \
  --set image.repository=<registry>/fluxion-harness/fluxion \
  --set image.tag=0.1.0 \
  --set secrets.masterKey="$(openssl rand -base64 32)" \
  --set postgresql.auth.password="<生产数据库密码>"
```

### 4. 使用已有 PostgreSQL（不装内置库）

```bash
helm install fluxion . \
  --set image.repository=<registry>/fluxion-harness/fluxion \
  --set secrets.masterKey="$(openssl rand -base64 32)" \
  --set postgresql.enabled=false \
  --set externalDatabase.url="postgresql+asyncpg://user:pass@your-postgres:5432/fluxion"
```

### 5. 校验

```bash
kubectl get deploy,svc,secret,configmap -l app.kubernetes.io/instance=fluxion
kubectl port-forward svc/fluxion 8000:8000
curl http://127.0.0.1:8000/healthz
```

### 关键设计

- **MASTER_KEY 用 Secret**：`FLUXION_MASTER_KEY`（及代码实际读取的
  `FLUXION_SECRET_MASTER_KEY`、含密码的 `FLUXION_DATABASE_URL`）都写入 Secret，Deployment
  通过 `envFrom.secretRef` 注入，不进入 ConfigMap 或 Deployment spec。
- **非敏感配置用 ConfigMap**：`FLUXION_ENV`、`FLUXION_LOG_LEVEL` 通过 `envFrom.configMapRef` 注入。
- **探针**：liveness 指向 `/healthz`，readiness 指向 `/readyz`（后端生产模式已实现）。
- **PostgreSQL 开关**：`postgresql.enabled=true` 时自动拼子 chart DSN；`false` 时用
  `externalDatabase.url`。也可直接 `--set databaseUrl=...` 显式覆盖。
- **推荐用外部 Secret 管理生产密钥**：设置 `--set secrets.existingSecret=<name>`，该 Secret
  需包含 `FLUXION_MASTER_KEY`、`FLUXION_SECRET_MASTER_KEY`、`FLUXION_DATABASE_URL` 三个键，
  可配合 SealedSecrets / ExternalSecrets 等工具落地。
- **三服务运行边界分离（规则 14）**：同一镜像按 `FLUXION_ROLE` 分派为三个独立 Deployment，
  互不影响、独立扩缩：
  - `api`（Control Plane，`replicaCount`）—— Console / Chat / Workspace / Eval / Operations；
  - `runtime`（AgentLoop 执行，`runtime.replicaCount`）—— 无状态，按 Agent 负载横向扩；
  - `worker`（DBOS workflow，`worker.replicaCount`）—— durable 执行，按队列负载扩。

## 三、生产 PostgreSQL 配置

后端连接 PostgreSQL 使用 SQLAlchemy asyncpg 驱动，DSN 形如：

```text
postgresql+asyncpg://<user>:<password>@<host>:5432/<database>
```

要点：

- 生产建议独立部署 PostgreSQL（托管云数据库或独立 StatefulSet），并开启 TLS；可用
  `FLUXION_POSTGRES_SSL` 控制 SSL 模式（默认 `disable`，可设 `require`/`verify-full` 等）。
- 数据库表结构由 `scripts/init_db.py` 初始化（幂等 `metadata.create_all`，PG/SQLite 双库），
  服务进程不建表。首次部署前先执行：
  ```bash
  python3 scripts/init_db.py --dsn "postgresql+asyncpg://<user>:<pass>@<host>:5432/<database>"
  ```
- 密码与 MASTER_KEY 一样，通过 Secret / 外部密钥管理注入，不落仓库、不落镜像。
