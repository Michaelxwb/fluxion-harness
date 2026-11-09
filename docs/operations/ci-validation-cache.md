# CI validation cache 复用与本地归档

本文是 `.code-flow/specs/test/harness-test.md` CI 规则的**操作手册**：说明如何把 CI 已经跑过的
heavy validator / review 结果复用到本地 `task verify-e2e` / `task archive`，以及常见故障怎么排查。

规范层只规定“必须/禁止”；具体命令、artifact 名称和排障步骤以本文为准。

## 1. 适用范围

适用：

- `.code-flow/validation.yml` 里的 heavy validator（4 个单测分片、acceptance、静态检查）
- `harness-test#RULE-test-001` 的 review 终验链（acceptance + 前端 build + 基础浏览器旅程）

不适用：

- 业务代码本身的单元测试；它们仍然由 CI 分片执行
- 普通的业务失败；不要用 accepted risk 或缓存绕过
- 需要真实环境但当前不可执行的场景（容量、外部供方）；按 §7 走 accepted risk

## 2. CI 产物

手动触发 `check` 工作流后，下载：

| Artifact | 内容 | 用途 |
|---|---|---|
| `validation-cache` | `validation-cache.json` + `validation-cache-provenance.json` | 本地合并后复用 4 个单测分片 + acceptance |
| `verifier-cache` | `.code-flow/.verifier-cache.json` | 复用 review 层已通过的 verifier |
| `validation-cache-entry-*` | 单 validator 分片 | 排障用；正常只需要合并后的 `validation-cache` |
| `review-cache-failures` | `/tmp/pytest-failures.log` | review-cache 失败时定位验收用例 |

当前 cache 生产者：

- `backend-tests` 四个 job：分别写 `Pytest (unit: fast/runtime/console/worker)`
- `review-cache` job：跑 review 层，内部用 `scripts/ci_validation_cache.py` 写
  `Pytest (acceptance)` 的 validation cache，并写 verifier cache

`acceptance` 七个分片目前只是 CI 信号，不直接写 cache；它们的命令与 `validation.yml` 的完整
acceptance 命令不同。

## 3. 触发 CI（默认 kimi-webbridge）

**默认用 kimi-webbridge 控制用户已登录的浏览器完成触发和 artifact 下载，不要等用户提供
token/gh，也不要先尝试用 curl 调 GitHub API。** 只有浏览器扩展不可用时，才回退到用户手动操作。

流程：

1. 用 kimi-webbridge 打开 `check` 工作流页面：
   `https://github.com/Michaelxwb/fluxion-harness/actions/workflows/check.yml`
2. 点 **Run workflow**，分支选择本地 HEAD 所在分支
3. 输入：
   ```text
   export_validation_cache: true
   review_task_dir: .code-flow/tasks/<日期>/<需求目录>
   ```
4. 运行后在同一页面等待完成，下载 artifacts：
   - `validation-cache`
   - `verifier-cache`
5. 如果扩展未连接，按 kimi-webbridge 的错误提示让用户连接；不要静默改用 token。

`review_task_dir` 为空时只导出单测 cache；要复用 review 层就必须填需求目录。

> 不要用 PR 的 merge commit 触发/复用；必须是本地 `HEAD` 对应的分支提交。

## 4. 本地合并

先确认本地和 CI 是同一条提交、工作区干净：

```bash
git rev-parse HEAD
git status --short
```

合并 validation cache，并重绑定 execution identity：

```bash
python3 scripts/merge_validation_cache.py \
  --output .code-flow/.validation-cache.json \
  --rebind-local \
  --provenance /path/to/validation-cache-provenance.json \
  /path/to/validation-cache.json
```

脚本会：

1. 校验 provenance 的 commit == 本地 `HEAD`
2. 校验缓存 fingerprint == 本地工作树指纹
3. 只把 `execution_identity` 重写成本机环境
4. 保留 `ci_execution_identity` / `ci_run_url` / `ci_commit` 作为审计

放置 verifier cache（artifact 解压后通常带 `.code-flow/` 前缀）：

```bash
cp /path/to/.code-flow/.verifier-cache.json .code-flow/.verifier-cache.json
```

然后跑：

```bash
code-flow task verify-e2e \
  --task-dir .code-flow/tasks/<日期>/<需求目录> \
  --root "$PWD" --json
```

如果 `code-flow` CLI 报 managed runtime drift，用 shim：

```bash
python .code-flow/scripts/cf_task_workflow.py verify-e2e \
  --task-dir .code-flow/tasks/<日期>/<需求目录> \
  --root "$PWD" --json
```

归档同理：

```bash
python .code-flow/scripts/cf_task_workflow.py archive \
  --task-dir .code-flow/tasks/<日期>/<需求目录> \
  --root "$PWD" --json
```

## 5. 预期结果

单测与 acceptance 应命中缓存：

```text
validators: executed 0, reused 8
acceptance scenarios: 全部 reused（learning 10 个；experience 22 个）
```

review 层可能仍有 9–13 分钟的执行时间；它取决于 verifier cache 是否命中、以及该需求包含多少
spec 规则。acceptance 场景本身通常只需几秒到几十秒。

## 6. 不变量

- CI 必须跑在**本地 HEAD** 上；PR 的 `pull_request` 事件默认是 merge commit，不能直接复用
- 合并缓存时工作区必须干净；`.code-flow/` 下的改动会被 fingerprint 忽略，但其它 tracked 文件
  一改，所有 cache 都会 miss
- 从 CI 跑完到本地合并之间**不要再提交**；提交会改变 `HEAD` 和指纹
- 不要手工伪造 `execution_identity` 或把失败结果写成 passed
- 缓存文件是本地性能缓存（`.code-flow/.validation-cache.json`、`.verifier-cache.json` 均 gitignore）

## 7. Deferred E2E / 容量场景

浏览器 E2E 骨架、1000 用户容量档等当前环境无法真实执行的场景，不能写成 verified。走
`acceptance risk`：

```bash
python .code-flow/scripts/cf_acceptance_manifest.py \
  --task-dir <需求目录> \
  --output <需求目录>/.acceptance-manifest.json \
  --accept-risk \
  --check-id CHK-... \
  --risk-owner TASK-0XX \
  --risk-reason "defer 到 release 环境执行" \
  --expires-at 2026-12-31 \
  --confirmed-by "user:<用户>" \
  --source "<用户确认原文>"
```

需要把 scenario 的 `status` 同步为 `accepted_risk` 时，再改 manifest 的 scenario 行；不要只改
checkbox 而不登记 risk。accepted risk 只用于**环境/阶段延后**，不用于普通失败。

CI 环境依赖的现行处理：

- `tests/acceptance/redis_cluster`：CI 在 `acceptance (redis_cluster)` 和 `review-cache` job 内
  启动真实 3M3R Redis Cluster（单容器 6 进程，6391–6396），不 skip；集群不可达仍按
  `env_blocked` 失败。
- `tests/acceptance/user_skill_sandbox`：需要带 bwrap 的测试镜像。GitHub runner 不提供该私有
  镜像，CI 内由该域 `conftest.py` 在镜像不可用时明确 skip；本地有 `fx-sandbox-test:local` 或
  显式 `MUAD_SKILL_EXECUTOR_*` 镜像时仍真实执行。

## 8. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 生成器 `No matching records found` | 平台应用记录缺失，或 `docker_cmd_name` 不匹配 | 先在平台注册应用；与 cache 无关 |
| `no cache entries matched this commit/fingerprint` | 本地 HEAD 不是 CI 那次提交，或工作区有 tracked 改动 | checkout 同一 commit、清理工作区后重跑 CI |
| helper 仍然开始跑 acceptance | execution identity 不一致（cwd/env/包装命令不同） | 确认使用本仓库的 `ci_validation_cache.py`；不要用其它命令替代 |
| `validation-cache` 里没有 acceptance 条目 | `review-cache` 失败 | 看 `review-cache-failures`，必要时本地跑 `--layers review` |
| review-cache 整跑失败、但单测 cache 正常 | 完整 acceptance + build + e2e 在单进程下偶发失败 | 先看失败日志；可本地跑 review 层，确认不是产品失败 |
| cache 文件条目很多但新条目没命中 | `.validation-cache.json` 上限 256 条，旧条目会被淘汰 | 重跑对应 validator，或清理旧 cache 后重新导入 |
| `code-flow` 报 managed runtime drift | `.code-flow/scripts` 被本地改动，和 CLI 包不一致 | 用 `.code-flow/scripts/cf_task_workflow.py` shim；或 `code-flow migrate --runtime --dry-run` 评估后再 apply |

## 9. 实测收益

一次最终 CI cache 运行（所有 job 并行）：

```text
总墙钟：约 20m39s
```

本地归档（cache 命中后）：

```text
单需求：约 10–15 分钟
```

本地全量基线（单需求，串行）：

```text
heavy validators：约 50–55 分钟
review 层：约 30–35 分钟
合计：约 80–90 分钟
```

两个需求合计：

```text
全本地：约 160–180 分钟
CI 后台跑 + 本地 archive：约 20–25 分钟本地 + 21 分钟 CI
等 CI 再 archive：约 40–45 分钟
整体节省：约 2 小时（70%–85%）
```

数字是实测参考，不是 SLA；CI 失败、重跑 review、平台重试都会拉长实际墙钟。
