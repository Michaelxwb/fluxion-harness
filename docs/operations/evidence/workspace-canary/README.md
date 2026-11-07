# Workspace canary 证据（TASK-018）

本目录保存 `scripts/workspace_canary.py` 在真实 K8s 上的输出，用于 P1 发布门禁：
bwrap 可执行、`muad-workspaces-rwx` 共享 PVC 可写、Runtime/Worker 跨 Pod 可见，并记录
镜像 digest 与节点指纹。

## 运行

```bash
python3 scripts/workspace_canary.py --namespace muad \
  --runtime deployment/muad-agent-runtime \
  --worker deployment/muad-agent-worker \
  --expected-root /mnt/muad-workspaces \
  --output docs/operations/evidence/workspace-canary/$(date +%Y-%m-%d)-canary.json
```

脚本只用标准库，宿主机有 Python 3.9+ 即可（不需要 uv）。宿主机没有 Python 时，把
`scripts/workspace_canary.py` 拷进 Runtime/Worker Pod，用 `--probe-only` 跑探针、
`--cleanup-only` 清理；完整命令见 `deploy/k8s/README.md`「P1 workspace canary」。
Pod 内方式没有编排模式的证据 JSON，需把两段探针输出与 kubectl 采集的 PVC/镜像/节点
信息一并存入本目录。

先在 **1 个 canary Runtime/Worker 副本**上跑，通过后再全量（design §4.2）。退出码
`0` 通过、`1` 检查失败、`2` kubectl/参数错误；失败即阻断发布。

## 证据字段

| 字段 | 说明 |
|---|---|
| `pvc` | `muad-workspaces-rwx` 的 phase / accessModes / capacity / storageClass；必须 `Bound` + `ReadWriteMany` |
| `pods.runtime` / `pods.worker` | Pod 名、节点、image、`imageID`（`sha256:` digest）、workspace 挂载路径/claim/只读标记 |
| `pods.*.probe` | 运行 UID、`bwrap --version`、PVC 写入、bwrap `/workspace` bind 写入、Worker 跨 Pod 读取结果 |
| `nodes.<name>` | 节点内核版本、OS 镜像、容器运行时、kubelet 版本 |
| `cleanup_ok` | canary 临时目录 `.canary/<run-id>` 是否清理 |
| `failures` / `ok` | 失败项与最终结论 |

不包含 Secret、Token、Cookie 或用户 Skill 数据；探针只写 `.canary/` 临时目录并清理。

## 已记录证据

| 文件 | 环境 | 结果 |
|---|---|---|
| `2026-11-07-local-orbstack.json` | 本地 OrbStack K8s，`muad-workspace-canary` 临时命名空间；Runtime/Worker 用 `muad-skill-executor-probe`（bwrap 0.13.2 + UID 10001）验证 PVC 跨 Pod 共享与 bwrap bind 写入 | PASS（PVC Bound/RWX、两 Pod 可写、Worker 跨 Pod 读到 Runtime 标记、imageID digest 与节点指纹已记录） |

内网生产 canary 必须在真实 runtime/worker 镜像的 1 个副本上重跑同一命令，并把输出写入
本目录；本地 probe 结果不能替代内网真实镜像记录。
