# Agent 容量与 Webchat 验收映射核对（B-318 / TASK-118）

本文是需求 `agent-capacity-webchat`（2026-10-16）的**验收收口运行手册**：说明每一层验收
跑什么、证明什么、失败怎么读，以及哪些容量边界**没有被验证**。设计来源：
`agent-capacity-webchat.backend.design.md` §2.5 验收条件 / §4.3 分层验收与报告 /
§6 需求追溯矩阵，`agent-capacity-webchat.frontend.design.md` §2.4 验收条件。

一句话口径：**映射审计只核对“真实盘面里记录了什么”，不替代真实运行**。全绿只代表
“场景/规则有唯一负责人、证据与 manifest 一致、未验证边界被显式保留”，不代表生产容量已达标。

---

## 1. 事实源

| 事实源 | 路径 | 审计用途 |
|---|---|---|
| 需求任务文档 | `.code-flow/tasks/2026-10-16/agent-capacity-webchat/agent-capacity-webchat.md` | 设计场景映射、Design Traceability、Spec Responsibility、Acceptance Coverage、每个 TASK 的契约/证据 |
| 验收 manifest | 同目录 `.acceptance-manifest.json` | 每个场景的 owner/level/boundary/cwd/timeout/command/status 与真实运行证据（run_id/exit_code/executed_at） |
| 后端设计 | `agent-capacity-webchat.backend.design.md` | §2.5 的 47 个后端场景（S-01…S-17 / E-01…E-21 / B-01…B-09） |
| 前端设计 | `agent-capacity-webchat.frontend.design.md` | §2.4 的 31 个前端场景（S-F01…S-F15 / E-F01…E-F11 / B-F01…B-F05） |
| 容量报告口径 | `docs/operations/agent-capacity-webchat-capacity.md` | 证据包、报告结论与副本建议公式 |
| 运行手册 | 本文件 | 分层命令、失败读法、未验证边界 |

审计实现：`tests/capacity/acceptance_audit.py`；行为验收：
`tests/capacity/test_capacity_webchat_acceptance_audit.py`。审计失败消息统一带
`[code] <场景/规则 ID>: <detail>`，可直接定位任务文档或 manifest 的对应行。

---

## 2. 分层验收（每层命令、证明什么、不证明什么）

| 层 | 命令（工作树根，逐字） | 证明什么 | 不证明什么 |
|---|---|---|---|
| L0 映射审计 | `uv run pytest -q tests/capacity/test_capacity_webchat_acceptance_audit.py` | 78 个设计场景各恰好一个 owner；manifest 与覆盖表同 ID/owner/level/boundary/cwd/timeout/command/status；verified 有命令+exit_code=0+run_id；e2e_deferred 有显式延后；27 条 Spec 规则唯一责任；40 条 RULE/RISK 追溯 owner 集合一致；未验证边界保留 | 任何业务代码/容量是否真的达标 |
| L1 容量组件 | `uv run pytest -q tests/capacity` | 容量 profile/harness、HTTP/企微协议探针、隔离栈、负载驱动、报告公式在真实组件上的行为 | 1000 用户全量、真实供方额度、浏览器交互 |
| L2 服务级验收 | `uv run pytest -q tests/acceptance` | 真实 API/DB/Runtime/Worker/Gateway/浏览器边界的整套验收（含 `tests/acceptance/capacity/test_capacity.py` 的 S-01/S-14/S-15 全量负载、断流恢复、单 Pod 重启/替换） | Playwright 域 spec（默认 `npm --prefix e2e test` 只跑 `foundation.spec.ts`） |
| L3 前端构建 | `npm --prefix apps/console-platform/frontend run build` | 前端 TypeScript/打包产物可真实构建；浏览器验收跑的是构建产物而不是 dev server | 任何浏览器断言 |
| L4 默认浏览器验收 | `npm --prefix e2e test` | 仓库默认 Playwright 配置（`e2e/playwright.config.ts`，`testMatch: foundation.spec.ts`）在真实 Chrome + 真实 uvicorn 上的基础链路 | Webchat 域（`playwright.webchat.config.ts`）的 S-*/E-* 浏览器场景 |
| L5 Webchat 浏览器域（需显式发起） | `npm --prefix e2e test -- --config playwright.webchat.config.ts <spec...>` | 真实构建产物 + 入口代理 + 四服务 + 探针下的 Webchat 浏览器场景（按 TASK-112～116 的登记命令） | 本任务默认 verifier 不包含此层；需求级浏览器复核需单独排期 |

需求级 verifier（`harness-test#RULE-test-001`，stage=review，timeout=2400s）的真实链是：

```bash
uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test
```

三段必须**逐段真实返回**；任一段失败/超时都不得写“全量通过”。本任务把每一段的
命令、退出码、通过/失败/超时计数与耗时登记在任务文档 TASK-118 的 Acceptance Evidence。
命令的输出不要接 `| head` 一类会提前关闭的管道（SIGPIPE 会打断 pytest 收尾并留下孤儿进程）。
pytest 验收使用 `tests/acceptance/conftest.py` 自动创建的独立空库（`muad_acc_<uuid>`）与
10–15 号 Redis，收尾自动 drop；不要改用共享 dev 库。

---

## 3. 怎么读审计失败

| finding code | 含义 | 先看哪里 |
|---|---|---|
| `duplicate_scenario` / `duplicate_manifest` | 同一场景在覆盖表或 manifest 出现两次 | 覆盖表该场景行；manifest `scenarios[].id` |
| `design_scenario_missing` | 设计 §2.5/§2.4 的场景没进覆盖表（整行被删，不是状态非终态） | 设计文档场景表 vs 覆盖表 |
| `frontend_mapping_missing` | 前端 `S-Fxx/E-Fxx/B-Fxx` 未映射，或映射到不存在的全局场景 | `## Design Scenario Map` |
| `owner_ref_missing` | 场景的 owner 没在 `Acceptance-Refs` 里登记它 | 该 TASK 头部 |
| `manifest_status_mismatch` / `manifest_*_mismatch` | manifest 与覆盖表字段不一致 | 两边对应行 |
| `verified_evidence_incomplete` | verified 缺命令/exit_code=0/run_id/executed_at/runs | manifest `evidence` 与 `runs` |
| `verified_evidence_not_passed` | manifest 说 verified，evidence 却不是 passed | 同一行 |
| `owner_not_done` | verified 场景的 owner 任务不是 done（收口任务自身由 Done Gate 裁决） | 该 TASK 的 Status |
| `deferred_evidence_claims_pass` | `e2e_deferred` 行携带 passed/exit_code（伪造 E2E 成功） | manifest evidence |
| `deferral_not_disclosed` | E2E 延后但 owner 任务文档没写“延后/verify-e2e/留” | owner 任务段落 |
| `red_green_missing` / `red_evidence_missing` / `green_evidence_missing` | 有 verified 契约的任务缺 RED/GREEN 或失败/通过计数 | 该 TASK 的 Acceptance Evidence |
| `traceability_owner_mismatch` | 设计 RULE/RISK 行声明的 owner 集合 ≠ 其场景实际 owner 集合 | `## Design Traceability` |
| `spec_rule_owner_mismatch` | 一条 Spec 规则不是恰好一个 owner，或 owner 的 `Spec-Refs` 没登记 | `## Spec Responsibility` + TASK 头部 |
| `boundary_section_missing` / `boundary_topic_missing` | 未验证边界小节/主题被删 | 本文件「未验证边界」 |
| `runbook_layer_missing` | 本文件缺某一层命令 | 本文件 §2 |

复现 RED/GREEN：先跑上面的 L0 命令；改造任务文档或 manifest 制造扰动后必须仍变红并指名条目，
逐字节还原后必须复绿。**不要用 `--output` 重生成 manifest**（会清空已有 evidence）；
新证据用 `cf_acceptance_runner.py --write-evidence --owner TASK-118` 落盘。

---

## 4. 证据更新流程

```bash
# 1) 只跑 B-318（映射审计）
uv run pytest -q tests/capacity/test_capacity_webchat_acceptance_audit.py

# 2) 把真实回执写入 manifest（不重生成、不清空其他场景）
python3 .code-flow/scripts/cf_acceptance_runner.py \
  --manifest .code-flow/tasks/2026-10-16/agent-capacity-webchat/.acceptance-manifest.json \
  --root "$PWD" --write-evidence --owner TASK-118

# 3) 复核：manifest 的 B-318 evidence 带 command/exit_code=0/run_id/executed_at
uv run pytest -q tests/capacity/test_capacity_webchat_acceptance_audit.py
```

E2E 场景默认保持 `e2e_deferred`；只有真实执行了 L5 域命令并记录回执才可改为 verified。
本任务不把 L4 的通过冒充成 L5。

---

## 未验证边界（Unverified boundaries）

- **真实供方额度未验证**：模型侧是 TASK-106 的真实 HTTP 协议探针（`CONTROLLED_PROBE`），
  探针吞吐不冒充真实模型吞吐；真实模型池的额度/限流/账户共享（RISK-07）没有实测数据，
  报告只能标注未验证。
- **企微单机器人外部容量边界未验证**：ACK/积压/限流只用真实协议探针核对，不是企业微信
  实网；§4.3 第 3 条要求的外部容量结论不在本需求内，不能靠增加机器人掩盖出口瓶颈。
- **1000 用户全量结论以本次 verifier 的真实返回为准**：`uv run pytest -q tests/acceptance`
  若未执行、超时或失败，容量报告不得写生产达标；`ACCEPTED` 只可能来自 `ACCEPTANCE` 运行
  + SLO 全过 + 预算全过。
- **浏览器 Webchat 域未包含在默认 L4**：`npm --prefix e2e test` 只跑 `foundation.spec.ts`；
  `playwright.webchat.config.ts` 的 S-*/E-* 浏览器场景需要显式域命令，未在本任务 verifier 内。
- **探针/单 Pod 容量口径**：受限运行中单 Pod 执行槽可能未被压满，报告口径为 `DECLARED`
  而不是 `MEASURED`；单 Pod 短暂不可用是事实记录，不是高可用 SLA 证明。
- **进程级重启的浏览器边界**：Playwright `webServer` 不会在运行中重启进程；浏览器侧的
  Gateway 重启由 `tests/acceptance/capacity/supervise.py` 看护进程承担，Console 重启与
  Runtime/Worker 替换由 pytest S-15 承担，两文件合起来才覆盖 S-15。
- **存储/磁盘 IO 未实测**：事件写入率与制品字节按声明估算，磁盘 IO、备份与 WAL 增长没有
  容量结论；共享卷吞吐边界不在本手册的“已通过”范围。
- **本机实测的容量短板（2026-10-20，锁定 profile 的 `ACCEPTANCE` 运行）**：
  `tests/acceptance/capacity/test_capacity.py` 的 S-01/S-14/S-15 未通过——S-01 在锁定窗口
  （60s ramp + 600s steady）内只受理到数百个不同用户（契约「1000 用户各 ≥1 Run」未达，驱动
  如实报告 `attempts`/distinct users）；S-14 的满载控制探测（取消/确认/续租）延迟超过 15s 阈值；
  S-15 的 Gateway 停机窗口内已受理 Run 落 `FAILED`（非 `COMPLETED`），单 Pod 恢复后的预算测量
  因前一用例失败留下停机实例而无法进行。这些是真实容量结论（不冒充通过），覆盖表/manifest 保持
  `e2e_deferred`；需在满足设计容量的环境重跑同一命令后才能写生产达标。
- **2026-10-21 的本轮规模（200 用户受限验证）**：容量场景按 `CAPACITY_LOAD_USERS=200` 执行（`RunKind.VERIFICATION` + 显式 scale、每用户 1 Run），本地验收栈 4 条用例全部通过（89.68s）；目标栈（k8s，Console/Gateway 各 1 Pod + Runtime 4）200 用户实测 200/200 完成、准入 P50 4.30s、首帧 P50 10.86s。**1000 用户 × 8 轮的全量 `ACCEPTANCE` 仍未验证**——上面的2026-10-20 短板结论继续有效，需要在满足设计容量的环境用默认 `CAPACITY_LOAD_USERS`（1000）重跑。
- **共享 dev 数据未写入**：验收使用独立空库与 10–15 号 Redis，跑后 drop；本手册不把
  “dev 环境看起来正常”当作验收证据。
