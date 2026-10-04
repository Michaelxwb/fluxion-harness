# Kubernetes 部署

四个部署单元（`harness-arch#RULE-arch-001`）：`console-platform` / `agent-runtime` / `agent-worker` / `im-gateway`。
Runtime 与 Worker **无状态、可横向扩展**（`docs/01 §4.2`：runtime 2~N、worker 2~N、gateway 1~N、console 1~2）。

## 目录

| 路径 | 内容 |
|---|---|
| `base/` | 四个 Deployment + Service + ConfigMap(=服务间接线) + RWX PVC + **迁移 Job** |
| `base/secret.example.yaml` | Secret 模板（**不含真实凭据**，键名是契约） |
| `verify-multipod/` | 多 Pod 冒烟用 overlay（runtime/worker 撑到 2 副本）+ 怎么跑 |

## 部署顺序

```bash
# 1) 命名空间与共享依赖（PG/Redis/NFS 按 docs/01 §4.3 作为外部依赖另行部署）
kubectl apply -f deploy/k8s/base/namespace.yaml

# 2) Secret：键名见 secret.example.yaml —— DATABASE_URL / REDIS_URL / INTERNAL_SERVICE_TOKEN
kubectl -n muad create secret generic muad-external \
  --from-literal=DATABASE_URL='postgresql+asyncpg://muad:***@postgres:5432/muad' \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=INTERNAL_SERVICE_TOKEN='<足够随机的值>'

# 3) 配置（服务间地址 + 默认租户）与共享产物卷
kubectl apply -f deploy/k8s/base/configmap.yaml -f deploy/k8s/base/pvc-artifacts.yaml

# 4) **先迁移再起服务**：Job 用 console-platform 镜像做载体（见 migrate-job.yaml 的注释）
kubectl -n muad delete job muad-migrate --ignore-not-found
kubectl apply -f deploy/k8s/base/migrate-job.yaml
kubectl -n muad wait --for=condition=complete job/muad-migrate --timeout=300s

# 5) 四个单元
kubectl apply -k deploy/k8s/base        # 或逐个 apply 四个 *.yaml
kubectl -n muad rollout status deploy/muad-console-platform --timeout=180s
```

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
`livenessProbe` 接 `/healthz`（存活，不因依赖抖动重启 Pod）。四个清单都按这个接。

## 多 Pod（横向扩展）验收

见 `verify-multipod/README.md`。要点：Runtime 与 Worker 的"任意 Pod 都能接手"不靠 sticky
session，权威状态全在 PostgreSQL/Redis（`docs/10 §11`），所以撑副本数不需要额外配置；
验收跑的是 `tests/k8s_multipod_smoke.py`（跨 Pod 接续同一会话 + 任务只被执行一次 + 杀 Pod 后接管）。
