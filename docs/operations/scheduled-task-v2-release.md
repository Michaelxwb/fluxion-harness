# 定时任务 v2：发布、回滚与监控操作

适用范围：`scheduled-task-v2` 的四个部署单元（console-platform / agent-runtime / agent-worker /
im-gateway）与一次性重建迁移 `0050`。本文是**可执行步骤**，不是承诺；每一步都有可核对的出口。

硬约束（§4.2）：

- 一个版本切换所有消费者，不做双写、不做兼容视图。
- **旧镜像不得直接接新 schema**：迁移 `0050` 重建调度表，旧代码读不懂新列。
- 破坏性迁移的回滚只有一条路：**从备份恢复** + 对应镜像版本。`0050` 的 downgrade 主动抛错，
  不是可选项。
- 本文不写端口/IP/容量常量的具体值：取值以 `SharedSettings` 与 ConfigMap/Secret 键为准，
  部署后从 `/metrics`、`/readyz` 与数据库事实核对。

## 1. 发布前：配置与依赖核对

逐项核对**所有者**，不在部署清单里复制第二份值：

| 事实 | 所有者 | 核对方式 |
|---|---|---|
| 数据库/Redis 连接 | Secret `muad-external` 的 `DATABASE_URL` / `REDIS_URL` | `kubectl -n muad get secret muad-external -o jsonpath=...` 存在且指向目标环境 |
| 内部服务身份 | Secret 的 `INTERNAL_SERVICE_TOKEN`；Worker 通过 `X-Internal-Service` 调 Runtime/Console | 四单元 Secret 同源；Worker 日志无 401 |
| Runtime 内部 API | ConfigMap `muad-common` 的 `AGENT_RUNTIME_URL` | Worker `/readyz` 通过；产物上传/容量预留/ASYNC 操作三条内部调用有 2xx |
| Console/网关地址 | ConfigMap 的 `CONSOLE_PLATFORM_URL` / `IM_GATEWAY_URL` | 平台设置快照抓取与投递尝试成功 |
| Worker 执行槽 | Deployment env `WORKER_EXECUTION_CONCURRENCY` | `/metrics` 的 `runtime_execution_slots`（Runtime）与 Worker 启动日志 `execution_slots_configured` 一致 |
| 产物可达 | ConfigMap `ARTIFACT_ROOT` + `muad-artifacts-rwx` PVC；Worker 同挂 | Worker `/readyz` 的 `artifact_storage` 为就绪；Runtime 写入的 Task 产物可被 Gateway 解析 |
| workspace | `WORKSPACE_ROOT` + `muad-workspaces-rwx`；PVC 同时挂 Runtime/Worker | 缺挂载时 Worker 明确记录 `workspace_cleanup_disabled_root_missing`，不静默启用 |

不要在本清单里新增端口/IP 字面量：改地址只改 ConfigMap/Secret，改容量只改对应 Settings 或
Deployment env。

## 2. 停旧服务并查残留

发布前必须停掉 dev/旧服务，否则 `--reload` Worker 子进程会领取本环境的任务，用**它自己的**
`ARTIFACT_ROOT` 执行，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类假故障。

```bash
# 1) 停 dev（若在跑）
scripts/dev.sh stop
# 2) 查残留（进程名由四个单元与 dev 启动器构成）
pgrep -af 'muad_agent_worker|muad_agent_runtime|muad_console_platform|muad_im_gateway|scripts/dev.sh'
# 3) 逐个终止后复查，输出必须为空
pgrep -af 'muad_agent_worker|muad_agent_runtime|muad_console_platform|muad_im_gateway|scripts/dev.sh'
```

集群内则记录发布前副本数并缩到零（见第 4 步），不要只依赖滚动更新。

## 3. 备份（迁移前唯一恢复材料）

```bash
STAMP=$(date +%Y%m%dT%H%M%S)
pg_dump "$DATABASE_URL" --format=custom --file="scheduled-task-v2-${STAMP}.dump"
pg_restore --list "scheduled-task-v2-${STAMP}.dump" >/dev/null   # 备份可读
sha256sum "scheduled-task-v2-${STAMP}.dump" >"scheduled-task-v2-${STAMP}.dump.sha256"
```

把 dump 与校验文件传到备份存储并在发布记录里写明 `STAMP`。**恢复演练**（设计批准前的门槛）
在一次性环境用同一份 dump 完整恢复一次，确认 `task.workspace` / `task.workspace_lease` 与
`runtime.*` 行数一致。

## 4. 排空旧 Run / Task / job

先停消费者再迁移：

```bash
kubectl -n muad scale deploy/muad-agent-worker --replicas=0
kubectl -n muad scale deploy/muad-agent-runtime --replicas=0
```

确认没有在飞工作（只读、按租户）：

```sql
-- 仍会被 Worker 推进的 Task（含三类等待）
SELECT status, count(*) FROM task.task_execution
WHERE tenant_id = :tenant AND NOT is_deleted
  AND status IN ('QUEUED','RUNNING','WAITING_EXTERNAL','WAITING_HUMAN')
GROUP BY status;

-- 仍在跑的交互式 Run
SELECT status, count(*) FROM runtime.run_record
WHERE tenant_id = :tenant AND NOT is_deleted
  AND status IN ('CREATED','RUNNING','WAITING_TOOL','WAITING_INPUT')
GROUP BY status;
```

等待到两类计数都为 0；若到期仍未收尾，先让 deadline sweep/租约回收走完，再继续。
**不要**在有非终态 Task 时迁移（`0050` 会重建调度表，这些行只存在于备份里）。

## 5. 迁移

```bash
# migrate-job 只引用只读 alembic.ini；具体镜像版本与发布版本一致
kubectl -n muad delete job muad-migrate --ignore-not-found
kubectl -n muad apply -f deploy/k8s/base/migrate-job.yaml
kubectl -n muad wait --for=condition=complete job/muad-migrate --timeout=30m
kubectl -n muad logs job/muad-migrate
```

出口检查：

1. `alembic current` = `0050`（Downgrade 会主动失败，见第 7 步）。
2. schema contract 测试通过（`tests/contracts/` 中调度/迁移一致性用例）。
3. 关键表存在且为空：`task.task_schedule`、`task.task_schedule_revision`、
   `task.schedule_fire`、`task.task_execution`、`task.task_delivery`；
   `task.workspace` / `task.workspace_lease` 保留原行数。

## 6. 统一切换消费者

- 四个单元使用**同一镜像版本**（同一 commit 构建的 tag），按
  Runtime/Console/Gateway → Worker 的顺序拉起，Worker 最后恢复调度。
- 不启动旧版本副本做灰度：新旧同时存在就是双写/双读风险。
- 切回 Worker 后核对：
  - `/metrics` 出现 `schedule_active_count`、`worker_loop_tick_total{status="ok"}`；
  - Worker 日志 `execution_slots_configured`、无 `worker_loop_stale`/`worker_loop_restarted`；
  - 下一次 due Schedule 产生 Fire（`schedule_fire_created_total` 增长）或按策略
    `schedule_fire_skipped_total{reason}` 增长。

## 7. 回滚

### 7.1 迁移前（阶段 2/3/4 的代码回滚）

数据库仍是旧 schema 时，回滚是镜像回滚：把四个单元设回上一个 tag，重新拉起，验证
`task.task_schedule` 等旧表仍可读。此路径不触碰数据。

### 7.2 迁移后（`0050` 已应用）

**没有 schema downgrade**：`0050` 的 downgrade 主动抛
`restore from backup instead of downgrading`。唯一正确路径是恢复备份 + 旧镜像：

```bash
# 1) 再次停全部消费者
kubectl -n muad scale deploy/muad-agent-worker --replicas=0
kubectl -n muad scale deploy/muad-agent-runtime --replicas=0

# 2) 从备份恢复（--clean 先删新对象；确认目标库就是发布库）
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" \
  "scheduled-task-v2-${STAMP}.dump"
sha256sum -c "scheduled-task-v2-${STAMP}.dump.sha256"

# 3) 恢复旧版本镜像（同一 tag 应用于四个单元）
kubectl -n muad set image deploy/muad-agent-worker app=ghcr.io/michaelxwb/muad-agent-worker:<old-tag>
kubectl -n muad set image deploy/muad-agent-runtime app=ghcr.io/michaelxwb/muad-agent-runtime:<old-tag>
kubectl -n muad set image deploy/muad-console-platform app=ghcr.io/michaelxwb/muad-console-platform:<old-tag>
kubectl -n muad set image deploy/muad-im-gateway app=ghcr.io/michaelxwb/muad-im-gateway:<old-tag>
```

出口检查：旧表恢复后的行数与发布前记录一致；`task.workspace` 未被动过；
四单元 `/readyz` 就绪；**不得**在恢复前用旧镜像连新 schema。

## 8. 监控与告警

指标由各单元的 `/metrics` 暴露（Prometheus 文本）。阈值以容量 profile/压测预算为准，
页面与告警只引用指标名与设置所有者，不复制具体秒数。

| 信号 | 指标（实现名） | 条件 | 级别 | 处理 |
|---|---|---|---|---|
| 到期延迟 | `schedule_due_lag_seconds` | 超过压测预算 | P1 | 查 Scheduler/DB 行锁与 `worker_loop_tick_total{loop="worker.scheduler.fire"}` |
| Fire/Execution 对账 | `scheduled_fire_total{status}` vs `schedule_fire_created_total` | 差异 >0 且不可解释 | P1 | 查事务/唯一键，不先重放 |
| 重复 Fire | `schedule_fire_duplicate_total` | 出现**重复执行**（不是唯一键拦截） | P0 | 停调度、查 `uq_schedule_fire_occurrence` |
| 准入失败 | `schedule_admission_failed_total{reason}` | 突增 | P1 | 查 Skill/Tool/Grant 与 `reason` 错误码 |
| 需修复 | `schedule_needs_attention_count` | 长期非零 | P2 | 提醒用户修复；不要人工改状态绕过 |
| Human 超时 | `task_human_timeout_total` | 超阈值 | P2 | 查交互/deadline 与 `task_waiting_human_count` |
| 外部等待 | `task_waiting_external_count` | 积压不降 | P2 | 查 ASYNC operation 与补扫循环心跳 |
| 投递失败 | `delivery_total{status="FAILED"}` / `delivery_failed_total` | 持续 | P1 | 查 IM Gateway 与投递尝试 |
| 时区/规则 | `schedule_timezone_resolution_error_total` | >0 | P1 | 查 TZDB 与用户输入 |
| 循环卡死 | `worker_loop_stale_total{loop}` / `worker_loop_restart_total{loop}` | 出现 | P1 | 看该 loop 的慢扫描/网络；监督器已重启，仍需查根因 |
| Snapshot 膨胀 | `task_snapshot_bytes` | 接近 `ScheduleSettings.max_snapshot_bytes` | P2 | 查 Agent 工具/MCP 定义规模 |
| ASYNC 提交 | `task_async_operation_total{status}` | `SUBMIT_FAILED` 突增 | P1 | 查 Runtime 内部 API 与服务身份 |
| 产物下载 | `task_artifact_download_total{outcome}`（Runtime 目录） | `REJECTED` 突增 | P2 | 查 owner/agent 核属与版本/checksum |
| 压缩降级 | `context_compaction_total{layer="*",status="FAILED"}` | 出现 | P2 | 明确降级为不压缩；查摘要/外置失败原因 |

保留清理按平台设置执行（`schedule.max_fire_history_days` / `schedule.max_execution_history_days`
与批量参数的所有者是平台设置/`ScheduleRetentionLoop`），不要在部署清单或脚本里写死天数。
每轮清理的可对账计数见 Worker 日志 `schedule_retention_tick`。

## 9. 发布后验证

- S-07（完整调度历史）、S-08（多 Scheduler/多 Worker 幂等）、S-17（ASYNC Skill）按
  `.code-flow/tasks/2026-11-15/scheduled-task-v2/scheduled-task-v2.scenarios.md` 人工执行并记录证据。
- 未执行的浏览器/人工场景保持"待验证"，不以本文或静态测试通过代替。
