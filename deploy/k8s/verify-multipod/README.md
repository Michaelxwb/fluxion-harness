# 多 Pod 冒烟（Runtime 与 Worker 横向扩展）

回答一个问题：**Runtime 与 Worker 撑到 2 副本后，「任意 Pod 都能接手」是否真的成立**。
设计口径见 `harness-arch#RULE-arch-001`、`docs/09 §10`、`docs/10 §11`（Pod 本地只许放 lease 期对象、
skill cache、连接池、非权威 metrics buffer；权威状态一律在 PostgreSQL/Redis）。

## 跑法

```bash
# 1) 集群里按 base 清单部署好（含 Secret/ConfigMap/迁移），再把 runtime/worker 撑到 2 副本
kubectl apply -k deploy/k8s/verify-multipod       # = base + patch-replicas
kubectl -n muad rollout status deploy/muad-agent-runtime --timeout=180s
kubectl -n muad rollout status deploy/muad-agent-worker  --timeout=180s

# 2) **一次性隔离依赖**：别把冒烟跑在 dev 库上 —— 脚本会清掉 e2e-* 租户再种 e2e 数据、还投真实任务，
#    落在开发库里就是往里塞假数据（harness-test 有专门的污染记录）。
kubectl apply -f deploy/k8s/verify-multipod/deps.yaml
kubectl -n muad port-forward svc/verify-postgres 5433:5432 &
export DATABASE_URL=postgresql+asyncpg://muad:muad@localhost:5433/muad
kubectl -n muad wait --for=condition=available deploy/verify-postgres deploy/verify-redis --timeout=120s
#    应用侧（Deployment 的 DATABASE_URL/REDIS_URL）也要指到这套隔离依赖，跑完再切回去

# 3) 模型探针（Runtime 要真的调模型；worker 腿用它当"任务活多久"的开关）
uv run uvicorn tests.e2e.openai_probe_app:app --host 0.0.0.0 --port 19100 &
export MODEL_URL_IN_POD=http://host.docker.internal:19100   # Pod 视角
export MODEL_URL_ON_HOST=http://127.0.0.1:19100             # 本机视角

# 4) 跑
uv run python tests/k8s_multipod_smoke.py --leg all
```

`--leg runtime` / `--leg worker` 可只跑一条腿。脚本会**自己清租户再种**，可重复跑。

## 两条腿各自断言什么

| 腿 | 断言 |
|---|---|
| runtime A | Turn1 打 Pod A、Turn2 打 Pod B（同一 conversation）：都 `COMPLETED`，且 Turn2 送进模型的 prompt 里**带着 Turn1 的文本与上一轮助手回复** ⇒ 上下文由库 + 产物 store 重建，不是 Pod 内存 |
| runtime B | 硬删承载过 Turn1 的 Pod 后，经 Service 的 Turn3 落在替代 Pod 上，同一会话接续且三轮历史完整 |
| worker C | 2 个 Pod 在跑、投 4 个任务：每个任务 `CLAIMED=1 / attempt=1`（`FOR UPDATE SKIP LOCKED` 不重复领） |
| worker D | 任务跑到一半**硬杀**持租约的 Pod：另一 Pod 在租约过期后 `RECLAIMED` 并跑完（`attempt≥2`、`lease_owner` 换 Pod） |

## 实测结论（2026-10-04，单节点 OrbStack k3s）

四条判据全过。同一台机器上验证的是**多 Pod 副本 + Pod 重建**语义；跨物理节点的调度/网络分区不在本冒烟范围。

## 注意

- **别和 `dev.sh` 同时跑**：两者连同一个库时，任务 claim **不带租户谓词** ⇒ 两套 worker 会互相抢任务。
- `kubectl port-forward svc/...` 在它当时选中的 Pod 被删后会自己退出 —— 脚本在删 Pod 之后会重新建转发；
  自己手搓流程时注意这一点，否则会看到 `COMMON_INTERNAL_ERROR` 这类"连接断了"的假象（本次就踩过）。
- worker 腿的 D 用例给 worker 注入过小租约（`TASK_LEASE_SEC=20`）以免等满 60s；这属验收惯例里的
  **节拍注入**，不是改生产语义（`harness-test` 有明确取舍说明）。
