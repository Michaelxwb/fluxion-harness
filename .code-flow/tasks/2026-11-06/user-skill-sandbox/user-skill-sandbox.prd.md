# PRD: 用户自建 Skill 与统一沙箱执行

> **文档编号**: PRD-2026-11-06-01
> **文档版本**: v0.1
> **创建日期**: 2026-11-06
> **产品负责人**: 待定
> **状态**: 草稿（待评审）

**评审边界说明**:
- **需求基线**: 第 2-8 章（背景/用户/功能/非功能/范围/依赖/已有规范约束）→ 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、Spec/Rule（已有规范引用）

**适用场景**: 需求早期阶段，在设计之前
**不适用**: 已有明确技术方案（请直接用 cf-task:align）

---

## 目录

- [1. 文档控制](#1-文档控制)
  - [1.1 责任人](#11-责任人)
  - [1.2 修订历史](#12-修订历史)
- [2. 背景与目标](#2-背景与目标)
  - [2.1 问题陈述](#21-问题陈述-必填)
  - [2.2 目标与价值](#22-目标与价值-必填)
- [3. 用户与场景](#3-用户与场景)
  - [3.1 目标用户](#31-目标用户-必填)
  - [3.2 用户故事](#32-用户故事-必填)
- [4. 功能需求](#4-功能需求)
  - [4.1 功能清单](#41-功能清单-必填)
  - [4.2 边缘情况](#42-边缘情况-可选)
- [5. 非功能需求](#5-非功能需求-可选)
- [6. 范围与边界](#6-范围与边界-必填)
- [7. 依赖与风险](#7-依赖与风险-可选)
- [8. Existing Spec Constraints](#8-existing-spec-constraints)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | 待定 | 需求定义、业务验收 |
| 开发负责人 | 待定 | 技术方案确认 |
| 安全负责人 | 待定 | 沙箱边界、服务身份门控、凭据处理 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-06 | OpenCode Agent | 基于沙箱方案探索记录形成初始需求基线 |

---

## 2. 背景与目标

### 2.1 问题陈述 [必填]

| 维度 | 内容 |
|------|------|
| **问题描述** | 当前平台只支持管理员/系统导入 Skill，普通用户无法把自己的业务流程封装成 Skill。现有 Skill 脚本在 Runtime/Worker 进程内直接执行，没有文件、网络、凭据和资源边界；一旦允许用户写脚本，用户代码会继承服务进程权限，可能读写宿主目录、访问内网、读取服务 Token 或拖垮 Runtime。同时，旧 Skill 把 `cache`/`logs`/`companies` 写在 Skill 包根目录，依赖 Windows 路径、硬编码平台地址和 `schtasks`，在 Linux 只读包和沙箱环境下无法直接运行。 |
| **当前替代方案** | 管理员手工导入经过审查的 Skill；用户需求由管理员代为开发或使用固定系统 Skill。该方式无法规模化，也无法支持用户自助迭代、测试和审批。 |
| **触发原因** | 业务希望把 Skill 生产能力开放给一线用户，同时保持平台安全、可审计、可运维；需要统一 Skill 与通用 Shell 的执行环境，避免“用户脚本一套、系统 Skill 另一套”。 |

补充事实：

- 现有 `script_command()` 使用 `python -I`，会阻止入口脚本目录加入 `sys.path`，导致老 Skill 的相邻模块 `import shared` 失败；
- 现有执行流先 `communicate()` 收完整 stdout/stderr 再截断，输出洪泛会先吃满父进程内存；
- 现有 `session-manager` 已能通过 Unix socket 交付 session state，但 state 文件仍可能走 memfd/`/proc` 路径；
- 现有已批准 Skill 缓存在共享 `READY` 目录，若可写挂载会被一次执行污染；
- 现有 Runtime `/v1` 内部接口未做服务身份门控，沙箱内脚本在 `--share-net` 下可以直接访问。

### 2.2 目标与价值 [必填]

| 维度 | 内容 |
|------|------|
| **核心目标** | 在现有 Runtime/Worker 镜像内落地统一 bwrap 沙箱，让用户可安全地创建、测试、提交、审批和执行 Skill，并让所有 Skill 与 `workspace_shell` 共享同一套执行、隔离、配额和审计能力。 |
| **预期价值** | 用户可自助把业务流程沉淀为 Skill，减少管理员代开发成本；用户脚本不再以服务进程权限运行；Skill 版本、归属、审批和下载可审计；老 Skill 通过少量路径/配置改造继续可用；通用 Shell 与 Skill 执行环境一致，降低维护成本。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 用户脚本进程内执行路径 | 存在（`ScriptSkillExecutor` 直接起子进程） | 0 条；所有脚本统一走 `SandboxedSkillExecutor` | 代码检索 + 执行路由测试 |
| 用户提交 Skill 审批覆盖 | 无用户提交 | 100% 用户 Skill 经 PENDING → APPROVED/REJECTED | 审批流水与审计 |
| 跨用户 PENDING 覆盖 | 无 owner 约束 | 0 次成功；不同用户同 key 返回 `SKILL_KEY_EXISTS` | 并发/越权测试 |
| 未鉴权 `/v1` 调用 | 可访问 | 缺失/错误服务身份全部拒绝 | 安全测试与 E2E |
| 旧 Skill 兼容 | 直接执行，写包根目录 | 样例老 Skill 在沙箱内通过最小改造运行 | 真实 Skill 验收 |
| 沙箱执行可观测 | 仅进程内日志 | 每次执行有审计、指标、错误码 | 审计查询 + 指标 |
| workspace 磁盘增长 | 无统一约束 | 每 workspace 配额 + 30 天 TTL 自动清理 | 配额/清理验收 |

> 未给出量化业务指标（如转化率）时，不编造数字；以上为可验证的交付指标。

---

## 3. 用户与场景

### 3.1 目标用户 [必填]

| 维度 | 内容 |
|------|------|
| **用户画像** | 1）租户终端用户：有业务流程自动化需求，能写 Python/Node 脚本，但不具备平台运维权限；2）租户管理员：负责 Skill 审批、复查、下载审计、用户范围和 Agent 绑定；3）系统管理员：上传系统级 Skill；4）平台运维：负责 Runtime/Worker、沙箱、PVC、容量和故障处理。 |
| **使用场景** | 用户在对话中创建 Skill 草稿，编写 `SKILL.md`、`muad.skill.json` 和脚本，用草稿 Shell 调平台接口验证登录态；提交后管理员在 Console 审批；通过后 Skill 自动授权给提交者并绑定上传时 Agent；用户在日常对话或定时任务中执行 Skill；用户也可以用 `workspace_shell` 处理文件、跑脚本、安装依赖。 |

### 3.2 用户故事 [必填]

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为租户终端用户，我希望在对话/Console 中创建 Skill 草稿并读写文件，以便把可复用流程封装成 Skill。 | P0 |
| US-02 | 作为租户终端用户，我希望在提交前用一次性沙箱 Shell 测试草稿脚本，并能通过 `session-manager` 获取平台登录态，以便验证真实平台调用。 | P0 |
| US-03 | 作为租户终端用户，我希望在对话中通过 `skill_draft_status` 查询 PENDING/APPROVED/REJECTED 状态和驳回原因，以便知道何时可用、如何修改。 | P0 |
| US-04 | 作为租户终端用户，我希望只能覆盖自己尚未审批的 PENDING 提交，不能覆盖其他用户的同名 Skill，以便防止误覆盖和越权。 | P0 |
| US-05 | 作为租户终端用户，我希望已审批的 Skill 版本不可修改，只有管理员解绑并软删后我才能重新提交，以便执行版本稳定可审计。 | P0 |
| US-06 | 作为租户终端用户，我希望审批通过后 Skill 自动授权给我并绑定到我上传时所在的 Agent，以便无需额外配置即可使用。 | P0 |
| US-07 | 作为租户终端用户，我希望通过 `execute_skill` / `run_skill_script` 以结构化输入输出、同步/异步方式运行 Skill，行为与现有 Skill 一致。 | P0 |
| US-08 | 作为租户终端用户，我希望所有 Skill 脚本在统一沙箱中执行，包只读、每次执行独立工作目录、输出流式有界，以便我的脚本不会破坏其他执行或拖垮服务。 | P0 |
| US-09 | 作为租户终端用户，我希望平台登录态按 Skill 声明的 `platforms` 自动注入，旧 Skill 的 `session-manager get-state` stdout 结构继续可用。 | P0 |
| US-10 | 作为租户终端用户，我希望有按 user + Agent 隔离的持久数据目录 `SKILL_DATA_DIR` 和每次执行独立的输出目录 `SKILL_OUTPUT_DIR`，以便报告不互相覆盖、配置可跨 Run 复用。 | P0 |
| US-11 | 作为租户终端用户，我希望通过 `workspace_shell` 在自己的 workspace 中执行命令、处理文件、安装 pip/npm 依赖，且不同 Agent 的 workspace 互不可见。 | P0 |
| US-12 | 作为租户终端用户，我希望老 Skill 只需少量路径/配置改造即可在新沙箱运行，不需要改 Python 命令行参数或 `SKILL.md` 调用方式。 | P0 |
| US-13 | 作为租户管理员，我希望查看待审批 Skill 的包内容、元数据、校验结果和提交者信息，并批准或驳回（填写原因）。 | P0 |
| US-14 | 作为租户管理员，我希望下载待审/已审批 Skill 包，并留下下载审计记录。 | P0 |
| US-15 | 作为租户管理员，我希望一键解绑并软删已审批 Skill（二次确认 + 审计），以便撤销有问题或不再使用的 Skill。 | P0 |
| US-16 | 作为租户管理员，我希望管理 Skill 的用户范围和 Agent 绑定，并能把测试用户加入 grant 以验证 Skill。 | P0 |
| US-17 | 作为系统管理员，我希望上传 `source=SYSTEM` 的 Skill 时自动通过、当前租户全局生效，不需要逐个 Agent 绑定。 | P0 |
| US-18 | 作为平台运维，我希望 `/v1` Runtime 受信接口只接受服务身份，Gateway/Console/Worker 的调用都带内部身份，以便沙箱脚本不能直接调用内部 API。 | P0 |
| US-19 | 作为平台运维，我希望看到沙箱执行、审批、下载、workspace 配额/TTL 清理的审计和指标，以便定位失败和容量问题。 | P1 |
| US-20 | 作为租户终端用户，我希望在本地 macOS Dev、本地 K8s 和内网 K8s 获得一致的执行语义（macOS 原生 Dev 使用显式 `dev-unsafe`），以便开发和生产行为一致。 | P1 |
| US-21 | 作为租户终端用户，我希望 Sandbox 失败（超时、输出超限、平台鉴权失败、配额不足、沙箱不可用）返回明确错误码和可重试性，以便修正脚本或重试。 | P0 |
| US-22 | 作为租户终端用户/管理员，我希望 workspace 在 30 天未访问后自动清理，且清理失败可见，以便磁盘不会无界增长。 | P0 |

---

## 4. 功能需求

### 4.1 功能清单 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|--------|---------|---------|--------|-------------|
| FEAT-01 | 草稿工作区与文件操作 | 创建/读取/写入/删除用户 Skill 草稿，按 tenant + user + draft_key 隔离，带 revision 和配额。 | P0 | US-01, US-04 |
| FEAT-02 | 草稿校验与打包 | 校验 `SKILL.md`/`muad.skill.json`/入口脚本/文件安全，生成可提交的 PENDING artifact。 | P0 | US-01, US-03 |
| FEAT-03 | 草稿沙箱测试 Shell | 在独立草稿副本上执行一次性命令，支持 Python/Node/`session-manager`，流式输出、超时、变更校验和回写。 | P0 | US-02, US-08, US-09 |
| FEAT-04 | 提交与管理员审批事务 | PENDING/APPROVED/REJECTED/SUPERSEDED 状态机、owner 覆盖规则、单 PENDING 约束、审批事务、自动授权/绑定。 | P0 | US-03, US-04, US-05, US-06, US-13 |
| FEAT-05 | 已审批包冻结与下载审计 | 已审批 artifact 只读、版本快照、完整性校验；管理员下载待审/已审批包并审计。 | P0 | US-05, US-14 |
| FEAT-06 | 统一沙箱执行器 | 所有 `source` 的 Skill 脚本统一走 `SandboxedSkillExecutor`；只读包、独立工作目录、流式输出、超时/取消/进程树回收、fail closed。 | P0 | US-07, US-08, US-12 |
| FEAT-07 | `session-manager` 登录态注入 | 按 Skill `platforms` 和受信执行身份注入 session broker、Unix socket、只读 state 文件，兼容旧 stdout 结构。 | P0 | US-09, US-12 |
| FEAT-08 | user + Agent workspace 与目录契约 | 按 tenant + user + Agent 隔离的持久 workspace，拆分 `data/` 与 `outputs/{run_id}/`，注入 `SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR`。 | P0 | US-10, US-11 |
| FEAT-09 | `workspace_shell` 通用命令 | 所有 Agent 默认可用的一次性 Shell；无 Skill 包、无 `session-manager`；Pod 网络；pip/npm 安装到 workspace；配额/超时/审计。 | P0 | US-11 |
| FEAT-10 | 老 Skill 兼容改造 | 老 Skill 的缓存/日志/数据/报告目录、Cookie、base_url、Linux 路径、调度、webhook 做最小改造。 | P0 | US-12 |
| FEAT-11 | `/v1` 服务身份门控 | Runtime `/v1` 受信接口（runs/conversations）要求内部服务身份；Gateway/Console/Worker 调用统一携带；浏览器和沙箱不得持有。 | P0 | US-18 |
| FEAT-12 | 管理员 Skill 管理界面与接口 | `/skills` 的 source/状态筛选、审批抽屉、下载、用户范围/Agent 绑定、解绑+软删、系统 Skill 上传。 | P0 | US-13, US-14, US-15, US-16 |
| FEAT-13 | 系统 Skill 与 `source=SYSTEM` | 管理员上传系统 Skill 后自动通过、当前租户全局生效、无需绑定；所有 effective 查询入口一致。 | P0 | US-17 |
| FEAT-14 | 审计、指标与错误码 | Skill 全生命周期审计、沙箱/清理指标、统一错误码与 retryable，日志不泄密。 | P0 | US-19, US-21 |
| FEAT-15 | workspace 配额、TTL 与自动清理 | 每个 workspace/用户配额、30 天 TTL、Worker 内置清理循环、多副本认领、活跃执行跳过。 | P0 | US-22 |
| FEAT-16 | 多环境部署与运行模式 | bwrap 内置基础镜像；macOS 原生 Dev 显式 `dev-unsafe`；本地 K8s/内网 K8s 走 bwrap；不可用 fail closed；记录镜像/内核指纹。 | P1 | US-20 |
| FEAT-17 | 数据模型、迁移与存量兼容 | Skill/Artifact/Draft/Workspace 字段与索引、迁移回填、旧数据保持可用、契约与快照升级。 | P0 | US-01, US-05, US-07 |

> P0=核心必做，P1=重要。

#### FEAT-01: 草稿工作区与文件操作

- **描述**: 为租户终端用户提供按 tenant + user + draft_key 隔离的 Skill 草稿目录和文件操作能力。
- **验收标准**:
  - [ ] 用户可以创建草稿、写入/覆盖文件、读取文件、列目录、删除单文件和删除整个草稿。
  - [ ] 草稿路径固定为 `skill-drafts/{tenant_id}/{user_id}/{draft_key}/`，只挂 Console，Runtime/Worker 不挂载该 PVC。
  - [ ] 文件路径规范化，拒绝绝对路径、`..`、symlink escape 和跨草稿路径；跨用户草稿不可读、不可写。
  - [ ] 草稿 revision 单调递增；写入携带 expected revision，冲突返回 `DRAFT_REVISION_CONFLICT`，不静默覆盖。
  - [ ] 单文件大小、草稿总大小、文件数有上限；超限返回 `DRAFT_QUOTA_EXCEEDED`。
  - [ ] 管理员默认不能浏览用户草稿；审批只能查看已提交的 artifact。
  - [ ] 草稿操作写审计，记录 tenant、user、draft_key、操作、文件数/大小摘要，不记录文件内容。

#### FEAT-02: 草稿校验与打包

- **描述**: 在提交前对草稿做确定性校验、预览和安全检查，并生成不可变的 PENDING artifact。
- **验收标准**:
  - [ ] 校验 `SKILL.md` frontmatter、`muad.skill.json`、`entrypoint`、脚本路径、`platforms` 声明和文件清单；缺失/非法返回字段级错误。
  - [ ] 拒绝空草稿、可执行文件越界、symlink、路径穿越、超大包、超多文件、明显密钥/凭据文件。
  - [ ] `skill_draft_review` 只读、无副作用，返回 pass/warn/error 和可定位的检查结果。
  - [ ] `skill_draft_submit` 生成 zip、checksum 和 `skill_artifact`（`review_status=PENDING`），记录 `submitted_by_user_id`、`submitted_agent_id`、`submitted_channel`、`author`。
  - [ ] 同一草稿 revision 重复提交有确定行为（返回已有 PENDING 或明确冲突），不产生重复 PENDING。
  - [ ] 打包结果包含文件清单、总大小、文件数，后续审批/执行使用同一 checksum。

#### FEAT-03: 草稿沙箱测试 Shell

- **描述**: 在独立草稿副本上执行一次性命令，让用户在提交前验证脚本、依赖和平台登录态。
- **验收标准**:
  - [ ] `skill_draft_shell` 只接受当前用户当前草稿的命令，不保持交互式会话，一次调用执行一条命令。
  - [ ] 命令在 bwrap 中执行，只挂当前草稿副本；不挂生产 PVC、其他用户草稿、`/mnt`、Docker socket、SA Token。
  - [ ] 支持 `python -m py_compile`、`python scripts/main.py`、`node --check` 和 `session-manager get-state`。
  - [ ] 草稿声明 `platforms` 时，服务端按受信执行身份、用户凭据模式和平台权限计算允许集合，不能只信草稿声明。
  - [ ] stdout/stderr 流式有界读取，超限终止/有界丢弃；超时杀完整进程树，禁止后台进程。
  - [ ] 执行后校验变更文件路径、扩展名、大小和 symlink，只把合法变更按 revision CAS 回写草稿；冲突返回 `DRAFT_REVISION_CONFLICT`。
  - [ ] 执行写审计，包含 user、draft_key、命令摘要、退出码、耗时；不记录 Cookie/Token 原文。

#### FEAT-04: 提交与管理员审批事务

- **描述**: 建立用户 Skill 的提交、审批、驳回、覆盖和锁定状态机，保证并发和越权安全。
- **验收标准**:
  - [ ] 审批通过自动创建 `SkillUserGrant(submitted_by_user_id)` 和 `AgentSkillBinding(submitted_agent_id)`；不重复创建。
  - [ ] 审批时复核 `AgentAccessGrant` 仍 active；已撤销则审批失败并返回 `AGENT_ACCESS_REVOKED`，不自动恢复授权。
  - [ ] 待审批同名覆盖只允许同一 `owner_user_id`；旧 PENDING 置 `SUPERSEDED`，同一 Skill 同时最多一个 PENDING（partial unique index）。
  - [ ] 不同用户提交同 key 返回 `SKILL_KEY_EXISTS`；不能覆盖、不能看到对方草稿或 PENDING。
  - [ ] 已审批 Skill 锁定，用户不能提交新版；管理员解绑 + 软删后才能重新提交。
  - [ ] 被驳回可修改重提；驳回必须填写 `review_note`，用户可看到原因。
  - [ ] 审批在单事务内锁定 artifact 行、校验仍为 PENDING、切换 `current_artifact_id`、写 grant/binding/audit；并发提交/审批只有一方成功。
  - [ ] 审批、驳回、覆盖、锁定均写审计，记录 actor、skill、artifact、agent、结果。

#### FEAT-05: 已审批包冻结与下载审计

- **描述**: 保证已审批 Skill 包不可变、可校验，并支持管理员受控下载和审计。
- **验收标准**:
  - [ ] 已审批 artifact 不可修改、不可覆盖；执行使用 `current_artifact_id` 对应快照和 checksum。
  - [ ] 执行前校验 artifact checksum；不匹配返回 `SKILL_ARTIFACT_INTEGRITY_ERROR`，不执行。
  - [ ] 管理员可下载 PENDING 和 APPROVED artifact；下载写 `SKILL_ARTIFACT_DOWNLOAD` 审计，记录 actor、skill、artifact、结果。
  - [ ] 普通用户不能下载其他用户/其他租户的 artifact；下载链接不泄露存储 key。
  - [ ] 软删后 artifact 和审计仍保留，但执行入口拒绝该 Skill。
  - [ ] 包只读挂载，脚本不能修改 `/skill`；一次执行不能污染共享 `READY` 缓存。

#### FEAT-06: 统一沙箱执行器

- **描述**: 所有 Skill 脚本（SYSTEM/ADMIN/USER）统一走 `SandboxedSkillExecutor`，按 `source` 选择 policy，不再有进程内直跑路径。
- **验收标准**:
  - [ ] Runtime 和 Worker 的 Skill 执行都路由到 `SandboxedSkillExecutor`；`source` 来自服务端记录并进入冻结快照，缺失/非法拒绝执行。
  - [ ] `script_command()` 使用 `python -E -s -B <script>`；入口脚本目录可 `import` 相邻模块；`SKILL.md` 仍写 `python scripts/run.py`。
  - [ ] 已批准包只读挂载 `/skill`；每次执行独立可写 `/workspace`；不把共享 `READY` 缓存可写挂载。
  - [ ] stdout/stderr 按块读取，超限终止或有界丢弃；不能先 `communicate()` 收完整流。
  - [ ] 超时、取消、父进程退出、`setsid`、双重 fork、后台进程都能回收完整后代，不留孤儿。
  - [ ] 沙箱内没有 `INTERNAL_SERVICE_TOKEN`、DB/Redis 连接串、SA Token、`/mnt`、`/proc`、`/var/run`、Docker socket。
  - [ ] bwrap 不可用、policy 非法、镜像/内核不满足时 fail closed；除显式 `dev-unsafe` 外不回退宿主执行。
  - [ ] 执行结果保留现有 CLI 契约：退出码、stdout/stderr、结构化 JSON 解析和错误码不变。

#### FEAT-07: `session-manager` 登录态注入

- **描述**: 按 Skill `platforms` 和受信执行身份注入 session broker，让 Skill 通过 `session-manager get-state` 获取登录态。
- **验收标准**:
  - [ ] 只有 Skill 执行和草稿 Shell 按 `platforms` 注入 `session-manager`；`workspace_shell` 默认不注入。
  - [ ] 允许平台集合由服务端根据 Run/Task 身份、用户凭据模式和平台权限计算，不只信 Skill 声明。
  - [ ] 沙箱内注入 `MUAD_SESSION_SOCKET` 和启动器 PATH，并 bind `/bin` 供 `#!/bin/sh` 使用。
  - [ ] 沙箱模式强制使用执行专属目录里的普通只读 state 文件，不使用 memfd/`/proc/<pid>/fd`；执行结束删除。
  - [ ] `session-manager get-state` stdout 同时返回 `sessionStateFile` 和旧版 `platforms` list（`platform`、`source`、`expiresAt`），旧 Skill 不改代码可用。
  - [ ] `sessionStateFile` 内仍是 `platforms.<key>.cookies` / `requestProfile`，新 Skill 读取方式不变。
  - [ ] broker 的连接、并发、请求次数、state 文件数/大小有执行级预算；Cookie 不写日志。
  - [ ] 真实平台正负向验收：有效 Cookie 200、无 Cookie 403、无效 Cookie 403。

#### FEAT-08: user + Agent workspace 与目录契约

- **描述**: 为 Skill 执行和 `workspace_shell` 提供按 tenant + user + Agent 隔离的持久 workspace。
- **验收标准**:
  - [ ] PVC 路径为 `<workspace-root>/{tenant_id}/{user_id}/{agent_id}/`，包含 `data/` 和 `outputs/{run_id}/`。
  - [ ] 同一 user + Agent 跨会话共享 `data/`；不同 Agent 的 workspace 不可见、不可写。
  - [ ] 沙箱只绑定当前 user + Agent 目录到 `/workspace`，不暴露 workspace 根、其他用户/Agent 目录。
  - [ ] Skill 执行注入 `SKILL_DATA_DIR=/workspace/data`、`SKILL_OUTPUT_DIR=/workspace/outputs/{run_id}`。
  - [ ] 每次执行/每条 Shell 命令使用独立 `outputs` 目录，两次执行结果互不影响；`data/` 跨 Run 持久。
  - [ ] 执行结束后，`SKILL_OUTPUT_DIR` 下的文件按文件数/大小/路径/符号链接校验，登记为本次执行产物（路径、大小、checksum），供现有附件交付机制使用；本需求不新增独立下载页。
  - [ ] 脚本在 `SKILL_OUTPUT_DIR` 为空时不写文件；持久数据建议临时文件 + `os.replace` 原子写。
  - [ ] workspace 元数据记录 `last_access_time`、`size_bytes`、`state`、`lease_until`，供配额和清理使用。
  - [ ] 不把 `/mnt/muad-artifacts`、其他用户 workspace、`/var/run`、Docker socket 挂进沙箱。

#### FEAT-09: `workspace_shell` 通用命令

- **描述**: 为所有 Agent 提供默认可用的一次性通用 Shell，用于文件处理、跑脚本、安装依赖。
- **验收标准**:
  - [ ] `workspace_shell` 对所有 Agent 默认注册，不增加 Agent 配置项。
  - [ ] 使用当前 user + Agent workspace；不挂 Skill 包；不注入 `session-manager`。
  - [ ] 网络与 Pod 一致（`--share-net`），可访问 Pod 网络和公网；`/v1` 门控是硬前置。
  - [ ] 允许 `pip`/`npm` 安装到 workspace 内 venv/node_modules，不写 `/usr`、不写共享缓存。
  - [ ] 一次性命令，不保持交互式会话；超时、输出上限、进程树回收、并发信号量与 Skill 执行一致。
  - [ ] 沙箱内没有服务 Token、DB/Redis 连接串、SA Token；不挂 `/mnt`、`/proc`、`/var/run`、Docker socket。
  - [ ] 命令摘要、退出码、耗时、文件变更清单写审计；不记录完整密钥/Token。
  - [ ] 配额不足、超时、输出超限、沙箱不可用返回明确错误码。

#### FEAT-10: 老 Skill 兼容改造

- **描述**: 让已有 Skill 以最小代码/路径改造迁移到统一沙箱和 workspace 目录契约。
- **验收标准**:
  - [ ] 老 Skill 的 `cache`/`logs`/`companies` 改到 `SKILL_DATA_DIR`；报告等单次结果改到 `SKILL_OUTPUT_DIR`。
  - [ ] Cookie 从 `cookies.txt`/硬编码改为 `session-manager get-state` 读取。
  - [ ] 平台 `base_url` 从 `requestProfile.baseUrl` 获取，不写死生产/测试地址。
  - [ ] `SKILL.md` 使用 POSIX 路径和 Linux 命令，不依赖 `Get-Content`、盘符、`schtasks` 或常驻 daemon。
  - [ ] 定时执行改用平台 Schedule，不在 Skill 内维护 Windows 计划任务或后台循环。
  - [ ] 硬编码 webhook key 移除，走平台交付/Secret 配置。
  - [ ] `SKILL.md` 继续写 `python scripts/run.py`，不需要写 `-E -s -B`；Python CLI 参数保持兼容。
  - [ ] `login-mssp-platform` 不改代码可在新执行器运行；`mss-report-skill` 完成路径/配置改造后通过验收。

#### FEAT-11: `/v1` 服务身份门控

- **描述**: 给 Runtime `/v1` 受信接口加服务身份门控，防止沙箱内用户脚本直接调用内部 API。
- **验收标准**:
  - [ ] Runtime `/v1` 受信 router（含 runs 和 conversations）要求 `X-Internal-Service`；缺失、错误、服务端未配置 Token 均拒绝。
  - [ ] Gateway 的 `create_run`、`submit_run`、`resume_run`、`open_events`、`open_event_stream`、`get_run`、`cancel_run`、`cancel_active`、`create_conversation` 等调用携带服务身份。
  - [ ] SSE 建连/重连校验一次；不逐事件校验；不增加网络往返。
  - [ ] 浏览器不能传入/伪造服务身份头；服务 Token 不发给浏览器或沙箱。
  - [ ] Console/Worker 内部客户端、e2e/容量/调试客户端同步改造，避免只覆盖首个 POST 漏掉 SSE 重连。
  - [ ] 门控使用启动时缓存配置和异步本地比较，不新增 DB/Redis/远程鉴权调用。
  - [ ] 安全测试覆盖：无 header、错误 header、正确 header、浏览器透传、SSE 重连。

#### FEAT-12: 管理员 Skill 管理界面与接口

- **描述**: 扩展现有 `/skills` 管理页，支持用户 Skill 审批、下载、范围/绑定管理和解绑软删。
- **验收标准**:
  - [ ] `/skills` 列表增加 `source`（SYSTEM/ADMIN/USER）、审批状态、author、绑定 Agent 数、用户范围、更新时间；支持筛选和 pending 标记。
  - [ ] 审批抽屉展示 artifact 元数据、文件清单、校验结果、提交者、提交渠道、上传时 Agent、`review_note`；支持批准/驳回。
  - [ ] 支持下载 PENDING/APPROVED 包；下载触发审计。
  - [ ] 支持用户范围（ALL/SELECTED）、用户 grant、Agent 绑定管理；支持把测试用户加入 grant 并确认其有 Agent 访问权。
  - [ ] 已审批 Skill 删除提供“一键解绑 + 软删”，二次确认，展示将解绑的 Agent 列表，写审计。
  - [ ] 系统 Skill 上传可选择 `source=SYSTEM`；上传后自动通过、全局生效。
  - [ ] 非管理员不能执行审批、下载、解绑、软删、系统 Skill 上传；接口和 UI 双重校验。
  - [ ] 空态、加载、错误、无权限、已删除等状态有明确提示；状态不能只用颜色表达；支持 i18n 和键盘操作。

#### FEAT-13: 系统 Skill 与 `source=SYSTEM`

- **描述**: 系统 Skill 由管理员上传，自动通过并在当前租户全局生效。
- **验收标准**:
  - [ ] 管理员上传时可选择 `source=SYSTEM`；系统 Skill 自动 `user_scope=ALL`、`enabled=true`、`review_status=APPROVED`。
  - [ ] 系统 Skill 不需要 `AgentSkillBinding`，当前租户所有用户/Agent 的 effective 查询都可见。
  - [ ] `list`/`count`/`catalog`/`snapshot`/`execution` 所有入口都包含系统 Skill，不能只改列表。
  - [ ] `/skills` 按系统/管理员/用户分组或标签展示，不用独立 Tab。
  - [ ] 普通用户不能修改/删除系统 Skill；管理员删除遵循软删和审计。
  - [ ] 存量管理员导入 Skill 回填 `source=ADMIN`、`review_status=APPROVED`，行为不变。

#### FEAT-14: 审计、指标与错误码

- **描述**: 为 Skill 全生命周期和沙箱执行提供可查询、可告警、可排障的审计、指标和错误码。
- **验收标准**:
  - [ ] 审计覆盖创建、更新、删除、提交、批准、驳回、下载、解绑、软删、执行、清理、沙箱失败。
  - [ ] 审计字段包含 tenant、actor、skill、artifact、agent、run、命令摘要、退出码、耗时、错误码；不记录 Cookie/Token/密钥。
  - [ ] 指标包含执行次数、耗时、失败原因、输出超限、超时、沙箱不可用、审批数量、清理扫描/删除/失败/释放字节。
  - [ ] 错误码表包含 `SKILL_KEY_EXISTS`、`SKILL_APPROVED_LOCKED`、`SKILL_PENDING_EXISTS`、`AGENT_ACCESS_REVOKED`、`DRAFT_REVISION_CONFLICT`、`DRAFT_QUOTA_EXCEEDED`、`SKILL_ARTIFACT_INTEGRITY_ERROR`、`SANDBOX_UNAVAILABLE`、`SANDBOX_TIMEOUT`、`SANDBOX_OUTPUT_LIMIT`、`PLATFORM_SESSION_REQUIRED`、`WORKSPACE_QUOTA_EXCEEDED`，并带 `retryable`。
  - [ ] 用户侧错误信息不包含堆栈、宿主路径、Cookie/Token；内部日志可按 run/task/skill 关联。
  - [ ] 管理员可通过现有审计查询定位一次执行的 actor、skill、agent、run 和结果。

#### FEAT-15: workspace 配额、TTL 与自动清理

- **描述**: 控制 workspace 磁盘增长，按 TTL 自动清理 `outputs` 和 `data`，不新增独立清理服务。
- **验收标准**:
  - [ ] 每个 user + Agent workspace 默认 500 MiB，每用户聚合默认 500 MiB；文件数上限 5,000；超限返回 `WORKSPACE_QUOTA_EXCEEDED`。
  - [ ] 系统 Python/Node 解释器和系统依赖只读继承 Pod 基础镜像，不复制到 workspace；`pip`/`npm` 只把安装包写入 workspace 的 venv/target/node_modules，并计入配额。
  - [ ] `outputs` 和 `data` 默认 30 天未访问清理；`last_access_time` 由 Skill 执行/Shell 访问更新，不依赖不可靠的 NFS atime。
  - [ ] 清理由 Worker 内置 `workspace.maintenance` 循环执行，随进程启动；不需要管理员创建 Schedule，也不新增 K8s CronJob。
  - [ ] 多副本清理使用 PG advisory lock 或 DB 认领 + 租约，避免重复删除/重复计数。
  - [ ] 有活跃 Run/租约的 workspace 跳过；先标记 `DELETING`，再删文件，最后置 `DELETED`。
  - [ ] 删除失败保留状态、记日志和指标，不静默吞掉；下一次循环可重试。
  - [ ] 清理指标包含扫描数、删除数、失败数、释放字节；容量按 PVC 规划。
  - [ ] workspace 根所在 PVC 同时挂到 Runtime 和 Worker；否则不得启用清理。

#### FEAT-16: 多环境部署与运行模式

- **描述**: 让同一个 Skill 在 macOS 原生 Dev、本地 K8s 和内网 K8s 有一致的执行语义。
- **验收标准**:
  - [ ] bwrap 内置现有 runtime/worker 基础镜像，不新增独立沙箱服务、Controller/CRD 或镜像流水线。
  - [ ] 内网 K8s 和本地 K8s（OrbStack）使用 bwrap，与生产同一执行路径。
  - [ ] macOS 原生 Dev 默认 `dev-unsafe`，必须显式开启；日志和审计留痕；生产禁止 `dev-unsafe`。
  - [ ] bwrap 不可用、内核不允许 `unshare`、镜像缺少依赖时 fail closed，不静默回退宿主执行。
  - [ ] 部署记录 base image digest、runtime/worker image digest、各副本 imageID、kernel、UID、capabilities、seccompProfile、节点。
  - [ ] 同一 Skill 在本地 K8s 和内网 K8s 的 stdout/exit code/结构化输出一致。

#### FEAT-17: 数据模型、迁移与存量兼容

- **描述**: 为 source/owner/审批/草稿/workspace 增加持久化字段和表，保证存量数据平滑迁移。
- **验收标准**:
  - [ ] `skill` 增加 `source`、`author`、`owner_user_id`；`owner_user_id` 仅用于 USER Skill 鉴权归属。
  - [ ] `skill_artifact` 增加 `review_status`、`review_note`、`reviewed_by`、`reviewed_at`、`submitted_by_user_id`、`submitted_agent_id`、`submitted_channel`。
  - [ ] 新增 `skill_draft`（tenant、owner、key、revision、最后提交信息）和 workspace 元数据表。
  - [ ] 迁移回填存量 `source=ADMIN`、`review_status=APPROVED`，现有管理员导入和 effective 查询行为不变。
  - [ ] 建立单 Skill 最多一个 PENDING 的 partial unique index，以及 owner/key、review_status、workspace last_access_time 等必要索引。
  - [ ] 软删保留 artifact、审批、下载、审计记录；执行入口按删除状态拒绝。
  - [ ] API/contracts 增加 `source` 等字段并更新快照；旧客户端/旧数据缺失字段时有明确默认或拒绝策略。
  - [ ] 迁移可重复执行/可回滚，配套迁移测试和存量兼容测试。

### 4.2 边缘情况 [可选]

| 场景 | 预期行为 |
|------|----------|
| 用户 A 和用户 B 提交同名 Skill | B 返回 `SKILL_KEY_EXISTS`；A 的 PENDING 不受影响；双方互相看不到草稿/PENDING |
| 同一 owner 连续提交两次 | 旧 PENDING 置 `SUPERSEDED`，新 PENDING 成为唯一待审；审批旧包返回冲突 |
| 管理员批准旧提交的同时用户提交新版 | 审批事务锁定 artifact；只有一方成功；另一方得到明确冲突 |
| 审批时上传 Agent 的访问授权已被撤销 | 审批失败 `AGENT_ACCESS_REVOKED`，不自动恢复授权 |
| 已审批 Skill 被用户尝试覆盖 | 返回 `SKILL_APPROVED_LOCKED`；需管理员解绑+软删后重提 |
| 被驳回后修改重提 | 允许；新 PENDING；驳回原因保留可查 |
| 草稿含 symlink/路径穿越/超大文件 | 校验失败，指出具体文件和原因；不打包 |
| 草稿 Shell 修改文件后 revision 已变 | 返回 `DRAFT_REVISION_CONFLICT`；不覆盖新内容 |
| Skill 包 checksum 不匹配 | 拒绝执行/下载，返回 `SKILL_ARTIFACT_INTEGRITY_ERROR` |
| `session-manager` 未注入或 socket 不可用 | 返回 `PLATFORM_SESSION_REQUIRED` 或明确 broker 错误；不静默回退无登录态 |
| 平台 Cookie 缺失/无效 | 业务请求 403；错误可定位到平台，不打印 Cookie 原文 |
| 脚本持续输出 | 流式读取，超过上限终止/有界丢弃；不耗尽父进程内存 |
| 脚本 fork 后台进程/脱离进程组 | 超时/取消时回收完整后代；不留孤儿 |
| 脚本尝试写 `/skill` | 只读挂载失败；错误可见；不污染已审批包 |
| 输出目录包含 symlink/超大文件/超多文件 | 不登记为产物或按上限截断，错误可见，不跟随 symlink |
| 两个 Run 同时写同一 workspace `data/` | 每次输出独立；持久数据要求原子写；不保证业务级并发合并 |
| workspace 超过配额 | 写入/执行前拒绝，返回 `WORKSPACE_QUOTA_EXCEEDED`；不静默截断 |
| 清理时 workspace 有活跃 Run | 跳过；不删除活跃目录 |
| 多副本同时清理 | advisory lock/DB 认领保证只有一个执行；重复扫描幂等 |
| 清理删除失败 | 保留 `DELETING`/失败状态，记日志和指标，下轮重试 |
| `/v1` 请求缺服务身份 | 拒绝；不泄露内部接口细节 |
| 浏览器伪造 `X-Internal-Service` | 网关剥离/覆盖；不转发浏览器值 |
| SSE 建连后重连 | 重连请求同样携带服务身份；不逐事件校验 |
| macOS 原生 Dev 未显式开启 `dev-unsafe` | 拒绝执行并提示；不静默宿主执行 |
| 本地 K8s 节点 seccomp 阻止 `unshare` | 沙箱启动失败并 fail closed；记录环境结论，不写成生产已支持 |
| 老 Skill 写包根目录 | 包只读导致失败；按 `SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR` 改造 |
| 老 Skill 使用 Windows 路径/`schtasks` | Linux 沙箱不支持；改用 POSIX 路径和平台 Schedule |
| `SKILL_OUTPUT_DIR` 为空 | 脚本不写文件；不落到 cwd/根目录 |
| 系统 Skill effective 查询遗漏 | 列表/计数/catalog/snapshot/执行全部回归；系统 Skill 对所有用户可见 |

---

## 5. 非功能需求 [可选]

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-SEC-01 | 安全 | 所有 Skill 脚本必须经过 bwrap；bwrap 不可用或 policy 非法时 fail closed；除显式 `dev-unsafe` 外禁止宿主执行。 |
| NFR-SEC-02 | 安全 | 沙箱内不得出现 `INTERNAL_SERVICE_TOKEN`、DB/Redis 连接串、Kubernetes ServiceAccount Token、`/mnt`、`/proc`、`/var/run`、Docker socket；只挂当前 workspace 和只读包。 |
| NFR-SEC-03 | 安全 | Runtime `/v1` 受信接口必须校验服务身份；浏览器不能透传/伪造；服务 Token 不进入沙箱。 |
| NFR-SEC-04 | 安全 | Skill 归属、覆盖、审批、执行授权以服务端 `owner_user_id`、`submitted_by_user_id`、`submitted_agent_id` 为准，不能由模型或请求参数自报。 |
| NFR-SEC-05 | 安全 | session state 文件按执行隔离、只读、结束删除；Cookie/Token 不写日志、不写审计原文；`workspace_shell` 不注入 session-manager。 |
| NFR-PERF-01 | 性能 | bwrap 启动开销保持在实测基线量级；执行不得先用 `communicate()` 收完整输出；流式读取内存有界。 |
| NFR-PERF-02 | 性能 | workspace bind 为零拷贝；主要成本在 NFS/PVC I/O；并发安装/执行有信号量，避免打满磁盘和网络。 |
| NFR-PERF-03 | 性能 | `/v1` 门控只做本地比较，使用启动缓存配置和异步依赖；SSE 建连/重连只校验一次，不增加每事件开销。 |
| NFR-REL-01 | 可靠性 | 超时、取消、父进程退出、`setsid`、双重 fork 和后台进程必须回收完整后代，不留孤儿。 |
| NFR-REL-02 | 可靠性 | 审批事务原子；并发提交/审批只有一方成功；旧 PENDING 覆盖和 `current_artifact_id` 切换不产生中间态。 |
| NFR-REL-03 | 可靠性 | 清理失败必须记录日志和指标并可重试；活跃 Run 必须跳过；多副本清理幂等；不得静默吞掉失败。 |
| NFR-REL-04 | 可靠性 | 沙箱、session broker、workspace、审批、`/v1` 门控的错误必须显式返回，不允许静默降级或无边界重试。 |
| NFR-COMPAT-01 | 兼容性 | Python 3.12、Node 24；`SKILL.md` 继续使用 `python scripts/run.py`；执行器统一 `-E -s -B`；现有 Python CLI 参数不变。 |
| NFR-COMPAT-02 | 兼容性 | macOS 原生 Dev `dev-unsafe`；本地 K8s/内网 K8s 使用 bwrap；不新增独立沙箱服务、Controller/CRD 或镜像流水线。 |
| NFR-COMPAT-03 | 兼容性 | 保留 `SKILL_OUTPUT_DIR`；新增 `SKILL_DATA_DIR`；旧 `session-manager get-state` stdout 的 `platforms` list 继续可用；老 Skill 只做最小路径/配置改造。 |
| NFR-COMPAT-04 | 兼容性 | 存量 Skill 回填 `source=ADMIN`、`review_status=APPROVED`；现有管理员导入、effective 查询和执行行为不变。 |
| NFR-OBS-01 | 可观测性 | 每次 Skill 执行、审批、下载、解绑、软删、清理都有审计；执行/清理有指标；日志可按 tenant/user/agent/run/skill 关联；不记录密钥。 |
| NFR-CAP-01 | 容量 | 每 workspace 500 MiB、每用户聚合 500 MiB、文件数 5,000、TTL 30 天；容量按 PVC 规划；配额覆盖 workspace venv/node_modules/pip/npm cache 和 inode，不计系统解释器/系统 site-packages。 |
| NFR-UI-01 | 可用性 | 管理界面状态不能只用颜色表达；破坏性操作二次确认；支持键盘操作和 i18n；空态/错误/无权限有明确提示。 |

---

## 6. 范围与边界 [必填]

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | 用户 Skill 草稿、校验、沙箱测试、提交、审批、驳回、覆盖、锁定、自动授权/绑定、下载审计、解绑+软删；统一 `SandboxedSkillExecutor`；bwrap 沙箱；`session-manager` 注入；`SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR`；user + Agent workspace；`workspace_shell`；老 Skill 最小改造；`/v1` 服务身份门控；管理员 Console 界面；系统 Skill；审计/指标/错误码；workspace 配额/TTL/清理；多环境运行；数据模型和迁移。 |
| **非范围（Out of Scope）** | 独立沙箱微服务、K8s Controller/CRD、每任务一个 Sandbox Pod；gVisor/Kata/独立内核隔离（后续评估）；Skill 表关联平台字段（后续需求）；Legacy 可写副本模式；按 Agent 开关 `workspace_shell`；内网 PyPI/npm mirror allowlist 或 egress proxy（当前跟 Pod 网络一致）；macOS Seatbelt 硬隔离（后续可选）；Skill 市场、评分、跨租户共享、计费；Skill 多版本并存 UI/一键回滚；Skill 包内任意系统依赖安装；**每个 workspace 复制完整 Python/Node 运行时或系统 site-packages**（系统环境只读共享，workspace 只装增量包）。 |
| **前置假设** | 现有 Console 登录、租户、PlatformUser、Agent、AgentAccessGrant、审计和 PVC/RWX 能力可复用；runtime/worker 可重建基础镜像；`session-manager` 和平台 adapter 已可用；Worker 已有进程内维护循环模式；管理员角色和 Console `/skills` 页面已存在；内网 Pod 允许 bwrap 所需的 namespace/mount。 |

---

## 7. 依赖与风险 [可选]

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|--------|----------|--------------|---------|
| 基础镜像 | `python-node` 底座内置 bwrap 0.13.1；runtime/worker 重建并部署 | P1 首批 | 高 |
| Console 后端 | Skill/Artifact/Draft/Workspace schema、迁移、审批事务、下载、用户范围/绑定 API | P1/P2 | 高 |
| Console 前端 | `/skills` source/审批/下载/解绑软删/系统 Skill 上传 | P2 | 中 |
| Runtime | `SandboxedSkillExecutor`、`skill_draft_*`、`workspace_shell`、`/v1` 门控 | P1 | 高 |
| Worker | 统一执行器、workspace 清理循环、异步 Skill 验收 | P1 | 高 |
| Gateway | `/v1` 调用携带 `X-Internal-Service`，覆盖 JSON/SSE/重连 | P1 首批 | 高 |
| Platform SDK | `session-manager` state 文件、legacy stdout 摘要、平台 adapter | P1 | 中 |
| 存储 | 共享 RWX PVC 容量、配额、TTL；workspace 根同时挂 Runtime/Worker | P1 | 中 |
| 测试环境 | 真实 PostgreSQL/Redis/HTTP/浏览器；本地 K8s 和内网 K8s canary | P1/P2 | 中 |

### 7.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|---------|
| R-01 | bwrap 与 Runtime 共享内核，用户代码可执行未审批脚本 | 内核漏洞或提权影响同 Pod | 接受 bwrap 级隔离；最小挂载/凭据/网络边界；后续评估 gVisor/Kata/独立 Pod |
| R-02 | Pod 有公网出口，`workspace_shell` 和 pip/npm 可访问公网 | 供应链包可能外传 workspace 数据 | 接受 Pod 网络边界；安装到 workspace；不提供平台 session；审计和配额；不承诺包安全 |
| R-03 | NFS/PVC 可能无法强制硬配额 | workspace 超限拖垮存储 | 配额 + 文件数 + TTL + 容量规划；验证 PVC/NFS 后端能力 |
| R-04 | `/v1` 门控改动面大，漏掉 SSE 重连或内部客户端 | 沙箱脚本可访问内部 API，或正常调用被误拒 | 全调用面清单 + E2E + 安全测试；启动缓存本地比较；分批回归 |
| R-05 | 审批并发/覆盖规则实现错误 | 跨用户覆盖、批准旧包、授权错乱 | owner 唯一约束 + partial unique index + 事务锁 + 并发测试 |
| R-06 | 老 Skill 兼容点遗漏 | 迁移后无法运行 | 建立老 Skill 清单；逐包改造验收；`SKILL_DATA_DIR`/`SKILL_OUTPUT_DIR`/base_url/路径/调度/webhook 检查表 |
| R-07 | 输出洪泛/进程逃逸/孤儿进程 | Runtime 内存耗尽或资源泄漏 | 流式有界输出 + 进程树回收 + 超时/取消 + 对抗测试 |
| R-08 | workspace 清理与执行竞争 | 活跃目录被删或清理重复 | 元数据 `last_access_time` + 活跃租约跳过 + advisory lock/DB 认领 + `DELETING` 状态 |
| R-09 | 镜像/底座漂移 | 实际 Pod 与验收环境不一致 | 记录 base/service digest、imageID、kernel、seccomp、UID、caps；部署后复测 bwrap |
| R-10 | 用户误解 `workspace_shell` 能获取平台登录态 | 老 Skill 在工作区跑拿不到 Cookie | 文档和错误码明确：平台登录态只跟 Skill 执行绑定；需要 session 时走 `execute_skill`/`run_skill_script` |
| R-12 | 误解 workspace 配额需要复制 Python 环境 | 配额估算失真、workspace 膨胀 | 明确系统解释器/site-packages 只读共享；只统计 workspace venv/node_modules/安装包；禁止复制完整运行时 |

---

## 8. Existing Spec Constraints

> 本节仅列出 `stages` 含 `prd` 且已绑定到 `spec-context.yml` 的 Rule。

本次执行：

```bash
code-flow spec catalog --root "$PWD" --stage prd --paths <预计路径...> --json
```

返回：

```json
{"ok": true, "stage": "prd", "total": 0, "candidates": [], "dropped_count": 0, "dropped_domains": []}
```

当前仓库没有 `stages` 含 `prd` 的 Spec，故无 required Rule、无 N/A 决策。设计/实现阶段由 `design`/`plan`/`code`/`review` 阶段的 Spec 承接，重点域包括：

| 后续 Spec 域 | 预计承接内容 |
|---|---|
| `artifact/harness-skill.md` | Skill/Artifact 生命周期、导入、effective 查询、source、快照 |
| `api/harness-api.md` | Console/Runtime/Worker API、错误码、内部服务身份 |
| `auth/harness-auth.md` | 租户、用户、管理员、Agent 访问授权 |
| `worker/harness-worker.md` | 异步执行、租约、维护循环、清理 |
| `persistence/harness-data.md` | schema、迁移、索引、事务 |
| `secret/harness-secret.md` | Secret Scan、凭据不落盘、日志脱敏 |
| `test/harness-test.md` | 单元/契约/验收/E2E 分层、真实边界、隔离 |
| `frontend/harness-frontend.md` + `ui`/`ui-detail` | `/skills` 页面、审批抽屉、状态、可访问性 |
| `arch/harness-arch.md` | 执行器抽象、sandbox policy、fail closed |

> 以上为后续阶段导航，不是 PRD 阶段的 required Rule。

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| PRD | Product Requirements Document，产品需求文档 |
| US | User Story，用户故事 |
| FEAT | Feature，功能项（US 的实现载体） |
| NFR | Non-Functional Requirement，非功能性需求 |
| SLA | Service Level Agreement，服务等级协议 |
| bwrap | bubblewrap，Linux 用户态沙箱工具 |
| `source` | Skill 来源：SYSTEM/ADMIN/USER |
| `owner_user_id` | USER Skill 的内部鉴权归属；不是 UI 展示的 author |
| PENDING / APPROVED / REJECTED / SUPERSEDED | Skill artifact 审批状态 |
| `SKILL_DATA_DIR` | 按 user + Agent 持久化的数据目录 |
| `SKILL_OUTPUT_DIR` | 每次执行独立的输出目录 |
| `workspace_shell` | 通用用户 workspace 一次性 Shell |
| `skill_draft_shell` | Skill 草稿测试一次性 Shell |
| `TRUSTED` / `UNTRUSTED` | 沙箱执行 policy，按 Skill `source` 选择 |
| `dev-unsafe` | macOS 原生开发模式，显式关闭沙箱；生产禁止 |

---

*文档结束*
