# Kubernetes 部署

四个逻辑部署单元（`harness-arch#RULE-arch-001`）：`console-platform` / `agent-runtime` / `agent-worker` / `im-gateway`。
`im-gateway` 可拆成**无状态 web（Gateway）** 与**有状态 connector（Connector）** 两个运行时角色；
Connector 不是第五个业务微服务，只在启用企微时部署（`deploy/k8s/with-connector/`，5 单元）。
Runtime 与 Worker **无状态、可横向扩展**（`docs/01 §4.2`：runtime 2~N、worker 2~N、gateway N、console 1）。

## 目录

| 路径 | 内容 |
|---|---|
| `base/` | 四个 Deployment + Service + ConfigMap(=服务间接线) + RWX PVC + **迁移 Job** |
| `base/secret.example.yaml` | Secret 模板（**不含真实凭据**，键名是契约） |
| `with-connector/` | 启用企微的 **5 单元 overlay**：只多一个 Connector Deployment/Service/PDB（自身 secret、无公开入口）；Gateway 多副本与远程通道开关在 base |
| `with-connector/secret-connector.example.yaml` | Connector 专属 Secret 模板（只有 `REDIS_URL`/`INTERNAL_SERVICE_TOKEN`；**没有** `DATABASE_URL`） |
| `verify-multipod/` | 多 Pod 冒烟用 overlay（runtime/worker 撑到 2 副本）+ 怎么跑 |

## 部署顺序

```bash
# 0) 发布前预检（失败即阻断发布；见「发布前预检」一节）
uv run python scripts/check_agent_capacity_deployment.py

# 1) 命名空间与共享依赖（PG/Redis/NFS 按 docs/01 §4.3 作为外部依赖另行部署）
kubectl apply -f deploy/k8s/base/namespace.yaml

# 2) Secret：键名见 secret.example.yaml —— DATABASE_URL / REDIS_URL / INTERNAL_SERVICE_TOKEN
kubectl -n muad create secret generic muad-external \
  --from-literal=DATABASE_URL='postgresql+asyncpg://muad:***@postgres:5432/muad' \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=INTERNAL_SERVICE_TOKEN='<足够随机的值>'

# 3) 配置（服务间地址 + 默认租户）与共享产物卷
kubectl apply -f deploy/k8s/base/configmap.yaml -f deploy/k8s/base/pvc-artifacts.yaml

# 4) **先迁移再起服务**：维护窗口的备份/排空/单次迁移见下一节；
#    Job 用 console-platform 镜像做载体（见 migrate-job.yaml 的注释）
kubectl -n muad delete job muad-migrate --ignore-not-found
kubectl apply -f deploy/k8s/base/migrate-job.yaml
kubectl -n muad wait --for=condition=complete job/muad-migrate --timeout=300s

# 5) 四个单元
kubectl apply -k deploy/k8s/base        # 或逐个 apply 四个 *.yaml
kubectl -n muad rollout status deploy/muad-console-platform --timeout=180s
```

## 发布前预检（渲染清单 → typed 容量配置，失败阻断发布）

压测拓扑：**Console 1 Pod（容量口径）、Gateway/Connector N 副本（按 profile 声明）、Runtime/Worker 可多副本**。
清单预检不会自动核对集群中的手动扩容；每轮施压前额外执行实际拓扑检查：

```bash
uv run python scripts/check_agent_capacity_deployment.py --live-namespace muad-cap

# 启用企微（5 单元）：
uv run python scripts/check_agent_capacity_deployment.py \
  --base deploy/k8s/with-connector \
  --profile tests/capacity/profiles/agent-capacity-webchat-connector.example.yml
kubectl apply -k deploy/k8s/with-connector
```

该检查读取实际 Deployment、Pod、HPA：Console 期望/实际/就绪副本不为 1、存在额外或终止中
Pod、未就绪或被 HPA 扩容时非零退出；Gateway/Connector/Runtime/Worker 要求期望=实际=就绪
（扩缩未结束不得开始压测），不再约束 Gateway 单 Pod。

渲染正式清单，把**将要 apply 的那份**与 typed 容量配置逐项对齐（design §3.7 配置契约与
生效边界 / §4.2 发布与迁移）：对不上就不要发布。

```bash
# 默认渲染 deploy/k8s/base、校验 tests/capacity/profiles/agent-capacity-webchat.example.yml；
# 正式测量请换成锁定过的那份 profile（profile hash 记进容量报告）：
uv run python scripts/check_agent_capacity_deployment.py --profile tests/capacity/profiles/<锁定的>.yml
```

| 检查项 | 口径 |
|---|---|
| 渲染 | `kubectl kustomize deploy/k8s/base`（与 `kubectl apply -k` 同一条渲染路径）；没被 kustomization 收集的清单不生效、也不参与判定 |
| 单元集合 | 由 profile 的 `resources.hardware`/`process_counts`/`replica_counts`/`max_replica_counts` 声明：四个核心单元必填，`connector` 可选；渲染集合的 Deployment/Service 必须恰好等于该集合 |
| 资源声明 | 各单元的 requests/limits 必须等于 profile 的 `resources.hardware` |
| 总连接 | PG 总连接 = Σ 进程数 × 最大副本数 × (pool+overflow)（取自真实 engine 工厂），须留维护余量并 ≤ `pg_connection_budget` |
| FD | 每 Pod FD = `process_counts` ×（出站池连接 + PG 上限 + Redis + 入站订阅），须 ≤ `fd_budget` |
| DB 池 | 允许按单元声明 `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`；`pool+overflow` 必须 ≥ 该单元的并发闸门，且只在持库单元声明 |
| Connector | 启用时必须渲染 PDB、显式 lease 节拍（TTL ≥ 5s、renew < TTL/2）、且没有任何 Ingress 指向它 |
| 内存缓冲 | 进程级 SSE 总缓冲 ≤ 内存上限的 50%（`sse_total_buffer_bytes` 受进程内存预算约束） |
| 下游扇入 | Gateway 流池上界 ≤ Runtime 上界订阅容量（`runtime_max_sse_subscribers` × 最大副本） |
| 业务出站并发 | Gateway 的 egress 池上界（`EGRESS_HTTP_MAX_CONNECTIONS` × 最大副本）≥ `workload.users` × `active_runs_per_user`：单 Pod 下每个用户建会话/提交消息各占一条连接，池不够会在池里排队到超时变 500 |
| 排空 | `terminationGracePeriodSeconds` ≥ 60 秒；HPA 缩容稳定化窗口覆盖终止宽限 |
| 探针 | readiness 接 `/readyz`、liveness 接 `/healthz`；显式超时 ≥5s、周期 ≥10s、连续失败次数 ≥3，避免突发调度延迟触发重启 |
| 迁移 | `muad-migrate` Job 在渲染集合里，且真的调用 `migrations/db_migrate.py` |

**退出码**：`0` 通过；`1` 存在阻断发布的违规（逐项打印单元与预算明细）；`2` 渲染/配置读取
失败（预检无法给出结论，同样阻断）。预检不连库、不读 Secret 值，输出只有预算数字与单元名。

## 维护窗口：备份、排空、单次迁移（§4.2 发布与迁移）

先记录基线、再排空、再迁移，最后才发布四单元；迁移只走 Job（集群里唯一的 schema 变更入口）。

```bash
# 1) 记录生产版本/head，做备份（回退按备份恢复；不能假设回滚旧镜像即可降级 schema）
kubectl -n muad get deploy -o wide > release-$(date +%Y%m%d%H%M).txt
kubectl -n muad exec deploy/muad-console-platform -- sh -c \
  'printf "[alembic]\nscript_location = migrations\nprepend_sys_path = .\nsqlalchemy.url = %s\n" "$DATABASE_URL" > /tmp/alembic.window.ini && python migrations/db_migrate.py -c /tmp/alembic.window.ini current'
pg_dump --format=custom --file=muad-pre-release.dump "$DATABASE_URL"   # 在 PG 主机/备份系统上执行

# 2) 停新受理并排空：把容量设置降到最小（新受理暂停），等 Run/Task/提炼 job 全部到终态；
#    已有 Run 不因降额被回收，readiness 先停新受理/领取、终止宽限 60 秒后有界排空。
#    模型池迁移的排空门禁与迁移清单见 docs/model-pool-control-migration.md。

# 3) 单次迁移到完整 head（先 delete 再 apply：Job 规格不可变）
kubectl -n muad delete job muad-migrate --ignore-not-found
kubectl apply -f deploy/k8s/base/migrate-job.yaml
kubectl -n muad wait --for=condition=complete job/muad-migrate --timeout=300s

# 4) 结构 parity 与受控探针通过后，发布四单元（先跑「发布前预检」）
kubectl apply -k deploy/k8s/base
```

历史快照只读审计：迁移不双写、不做旧快照运行兼容层，历史 Run/快照只读查看，新执行只按新
结构；数据库变更回退需确认新写入数据的保留策略（见 `docs/model-pool-control-migration.md`）。

## 三条容易踩的坑（都是实测踩出来的）

1. **服务间地址必须显式给**：`CONSOLE_PLATFORM_URL` / `AGENT_RUNTIME_URL` / `AGENT_WORKER_URL` /
   `IM_GATEWAY_URL` 都在 `configmap.yaml` 里。缺了应用会回落到 `SharedSettings` 的
   `http://127.0.0.1:8000` 这类本机默认值，而 Pod 里的 `127.0.0.1` 是它自己 ⇒ 表现为
   "服务都 ready 了但什么都跑不动"。
2. **`INTERNAL_SERVICE_TOKEN` 必须给且四个单元一致**：内部端点要求 `X-Internal-Service`，
   缺它时 Console 侧一律拒绝（Gateway 取 Bot 快照、Runtime 取定义/凭据、Worker 取快照都走这条路）。
3. **`DEFAULT_TENANT_ID` 决定 Gateway 取哪个租户的 Bot 快照**：不给就用设置默认值 `default`；
   单租户部署把它设成实际租户，否则 `/readyz` 会显示 `bots_revision` 为空（= 一个 bot 都没拉到）。

## 探针口径

`readinessProbe` 接 `/readyz`（依赖缺失回 503，**不该往这种 Pod 转发流量**），
`livenessProbe` 接 `/healthz`（存活，不因依赖抖动重启 Pod）。四个清单都按这个接，且均显式给出
5s 超时、10s 周期、连续 3 次失败的预算。800 并发实测中，省略超时会沿用 Kubernetes 默认的
1s：一个仍在执行任务的 Runtime 被存活探针重启，同秒网关出现 77 次传输错误。
发布预检同时检查路径和预算，防止只核对探针路径而放过这个回归。

## 多 Pod（横向扩展）验收

见 `verify-multipod/README.md`。要点：Runtime 与 Worker 的"任意 Pod 都能接手"不靠 sticky
session，权威状态全在 PostgreSQL/Redis（`docs/10 §11`），所以撑副本数不需要额外配置；
验收跑的是 `tests/k8s_multipod_smoke.py`（跨 Pod 接续同一会话 + 任务只被执行一次 + 杀 Pod 后接管）。
