# Skill 自建与执行沙箱方案探索与定稿

日期：2026-11-06  
状态：**技术选型确定，执行策略与生产集成待验收**；P0 前置镜像、session-manager probe、真实 Runtime broker canary 已完成，待 P0.4 安全门控与执行层接入。
范围：用户自建 Skill 的草稿、审查、上传、审批、执行沙箱，以及 macOS Dev / 本地 K8s / 内网 Linux K8s 三端运行方式。  
手动测试证据：[`evidence/sandbox-2026-11-06/`](evidence/sandbox-2026-11-06/)

---

## 0. 结论

**采用“现有 Agent Runtime / Worker 镜像内置 bwrap 的进程内沙箱”，不新增独立沙箱服务、不新增 K8s Controller/CRD、不新增镜像流水线。**

> 这是**技术选型结论**，不等于生产验收。执行策略与生产集成的阻断项见 §13；
> 其中“受信 Runtime API 门控”和“真实 session-manager 链路”完成前，不能宣称执行方案闭环。

| 环境 | 沙箱方式 |
|---|---|
| 内网 Linux K8s（runtime / worker） | `bwrap`，在现有 Pod 内执行 |
| Mac 本地 K8s（OrbStack） | `bwrap`，与生产同一路径 |
| macOS 原生 Dev（`uv run` / uvicorn） | `sandbox-exec`（Seatbelt）或显式 `dev-unsafe` |
| 管理员 / 系统 Skill 脚本 | 现有进程内执行（管理员可信） |
| 用户 Skill 脚本 / Shell | `source=USER` 才进沙箱 |

不采用：

- OpenSandbox / Kubernetes Agent Sandbox：需要独立控制面或 Controller/CRD；
- gVisor / Kata / microsandbox：需要 RuntimeClass、节点运行时或 `/dev/kvm`，改动过大；
- 每任务一个 K8s Job：同步 Skill 延迟和生命周期管理成本高；
- Landlock / nono-py：生产内核 4.18 实测 `ENOSYS`；
- Anthropic `srt`：Linux 仍然依赖 bwrap，额外引入 Node CLI 和策略层，当前不是最轻方案。

---

## 1. 背景与约束

### 1.1 产品需求

用户可以在对话中自建 Skill：

1. 在草稿目录写 `SKILL.md`、`muad.skill.json`、`scripts/`、`references/`；
2. 在提交前用 `session-manager` 获取登录态并测试业务接口；
3. 明确说“上传”后提交审批；
4. 管理员审批/驳回、下载压缩包审计；
5. 审批通过后自动绑定上传时 Agent、自动授权上传者；
6. 用户脚本在受控沙箱中执行，不能读取生产 Artifact、其他用户草稿、服务 Token。

### 1.2 技术约束

- 不部署独立沙箱服务；
- 不新增构建/发布流水线；
- 同时支持 macOS 本地开发、本地 K8s、内网 Linux K8s；
- 内网基础镜像为 `docker.sangfor.com/mss-base/python-node:3.12.13-node24`（PlatOS 1.3，glibc 2.28，无包管理器）；
- 生产内核 `4.18.0-372.19.1.30.po1.x86_64`，不能依赖 Landlock；
- 现有 runtime/worker 用同一基础镜像，但部署在不同节点池。

---

## 2. 候选方案对比

| 方案 | 独立服务/组件 | macOS Dev | Linux 生产 | 结论 |
|---|---|---|---|---|
| **bwrap + Seatbelt/dev-unsafe** | 否 | `sandbox-exec` 或显式 dev | bwrap | **选定** |
| Anthropic `srt` | 否（Node CLI） | Seatbelt | bwrap | 可选，当前不引入 |
| OpenSandbox | 是（Server/Operator） | Docker/K8s | K8s | 过重，排除 |
| Agent Sandbox + gVisor | 是（Controller/CRD + RuntimeClass） | 需 Linux K8s | 强隔离 | 过重，排除 |
| Agent Sandbox + Kata | 是（Controller + RuntimeClass） | 需 Linux K8s | VM 级 | 过重，排除 |
| microsandbox | 否但依赖 `/dev/kvm` | 取决于 VM | 需 KVM | 环境不确定，排除 |
| K8s Job per execution | 否但需自管生命周期 | 不适用 | 强隔离 | 同步延迟高，暂不选 |
| Landlock / nono-py | 否 | 无 | 内核 4.18 不支持 | 实测排除 |

### 2.1 为什么不选 `srt`

`srt` 的价值是统一 macOS Seatbelt 与 Linux bwrap 的 CLI 和策略语言；但：

- Linux 仍然要 bwrap；
- 增加 Node CLI/依赖和一层策略转换；
- 我们已有 Python 运行时，薄封装更透明，便于审计和定制 session socket / 挂载。

保留为后续可选项，不作为首批依赖。

### 2.2 为什么不选独立沙箱服务

- OpenSandbox / Agent Sandbox 都需要新增控制面、CRD、Controller 或路由；
- gVisor/Kata 需要集群预配置 RuntimeClass 和节点运行时；
- 当前威胁模型是“内网可信员工自建 Skill”，不是公网多租户不可信代码；
- 进程级 bwrap + 文件/环境隔离 + 管理员审批已能满足当前需求。

---

## 3. 实测证据摘要

完整原始输出见 [`evidence/sandbox-2026-11-06/`](evidence/sandbox-2026-11-06/)。

### 3.1 基础镜像

```text
docker.sangfor.com/mss-base/python-node:3.12.13-node24
PlatOS 1.3 (LTS)
glibc 2.28
无 apt-get / apk / dnf / yum
无 bwrap / nsjail / unshare / prlimit
Python 3.12.13（/opt/python3.12/bin/python）
Node v24.22.2
```

因此 bwrap 不能通过包管理器安装，必须在 builder stage 构建后复制进镜像，或者使用只读 volume/ConfigMap 挂载。

### 3.2 macOS / OrbStack 本地

在**实际 PlatOS 基础镜像**上构建 bwrap 测试镜像（探索阶段为 0.13.2；最终底座 pin
v0.13.1，冒烟结论一致，见 `new-base-bwrap-smoke.txt`）：

```text
bubblewrap 0.13.2
uid=10001
python: cwd /work, mnt_exists False, proc_exists False, token_env False
python: net_connect_ex 101      # --unshare-net 断网
node: node_ok /work
shared net: net_shared 0        # --share-net 可用
Unix socket: {"session":"ok"}   # session-manager 桥接机制可用
```

同一底座的内核 smoke：

```text
--security-opt seccomp=unconfined:
  OK user / OK namespaces / OK mount / OK pid 1 / OK net 101 / RESULT: PASS

默认 Docker seccomp:
  FAIL unshare-user 1 Operation not permitted
```

session-manager 前置 probe（本地与内网 K8s 均 PASS）：

```text
bwrap_rc 0
stdout session_ok probe-cookie
stderr
RESULT: PASS
```

probe 使用真实 `session_cli.py`：在 bwrap 外启动 Unix socket broker，bwrap 内执行
`session-manager get-state --skill-name sandbox-session-probe`，读取返回的
`sessionStateFile` 并打印 cookie。它验证了 bwrap 内的 Unix socket、session 目录
bind、真实 CLI 和 state 文件可见性。内网 K8s Job 的相同输出见
`evidence/sandbox-2026-11-06/session-probe.txt`。

真实 Runtime broker canary 也已通过（2026-11-06）：

```text
bwrap_rc 0
{"ok": true, "status": 200, "url": "http://soar-inner.sangfor.com.cn:30001/",
 "cookie_names": ["login-source", "soc-token"], "content_type": "text/html"}
RESULT: PASS
```

它使用真实 Console/Redis/平台凭据，不是模拟 broker；详见
`evidence/sandbox-2026-11-06/real-broker-probe.txt`。

注意：本地 Docker 默认 seccomp 会拦截 `unshare`；内网 K8s 默认策略不同，见下。

### 3.3 内网 runtime / worker 真实 Pod

```text
kernel: 4.18.0-372.19.1.30.po1.x86_64
/proc/sys/user/max_user_namespaces=126141
/proc/sys/kernel/unprivileged_userns_clone=<No such file or directory>

OK user
OK namespaces
OK mount
OK pid 1
OK net 101
RESULT: PASS
```

- runtime Pod：`middleware03` / `middleware02`；
- worker Pod：`middleware01` / `middleware02`；
- 两个 Deployment 的 `seccompProfile` 均为空；
- 实际 Pod 默认策略允许 `unshare` / `mount`，**不需要改 seccomp**。

### 3.4 Landlock

```text
本地 amd64 模拟底座: landlock_abi -1 errno 38 (ENOSYS)
生产 Pod（GPT 文档记录）: errno=38 Function not implemented
```

结论：Landlock / nono-py 路线不可用；重建镜像不能修复内核能力。

### 3.5 证据边界

已验证：

- bwrap 二进制 + PlatOS 底座兼容；
- Python / Node 在 bwrap 内可执行；
- `/mnt`、`/proc`、服务 Token 环境变量不可见；
- 网络 namespace 可断网，`--share-net` 可访问内网；
- Unix socket 可桥接；
- 真实 `session_cli.py` 可在 bwrap 内通过 Unix socket 获取 state 文件并读取 cookie；
- 内网 runtime/worker Pod 支持所需 namespace/mount；
- 内网 K8s Job 内直接执行 bwrap + 真实 `session_cli.py`（模拟 broker）PASS；
- 真实 Runtime broker + Console/Redis/用户 AK-SK + `session-manager` + `requests`
  调 `soar-inner` 返回 HTTP 200（P0.3 PASS）。

尚未验证（P0.4/P1）：

- 真实用户 Skill 包通过 `SandboxedSkillExecutor` 在 bwrap 内执行；
- Worker async Skill 通过 bwrap 执行；
- 受信 Runtime API 门控后的拒绝路径；
- 输出流式上限、只读包/独立工作目录、审批事务。

---

## 4. 最终架构

```text
                         Agent Runtime / Worker
                                  │
                          Skill / Shell 执行入口
                                  │
                        SandboxPolicy（按 source 路由）
                    ┌─────────────┴─────────────┐
                    │                           │
              USER + scripts             ADMIN / SYSTEM
                    │                           │
                    ▼                           ▼
             SandboxRunner              现有 ScriptSkillExecutor
             ┌──────┴──────┐              （进程内，可信）
             │             │
        Linux Pod      macOS 原生 Dev
          bwrap        sandbox-exec（可选）
```

### 4.1 代码抽象

建议新增一个很薄的沙箱层，不引入 Provider 框架：

```text
packages/agent-core/src/muad_agent_core/sandbox/
├── runner.py       # SandboxRunner 协议 + SandboxSpec / SandboxResult
├── bwrap.py        # Linux 实现
├── seatbelt.py     # macOS 实现（可选）
└── policy.py       # source/环境 → 选择 backend；生产 fail closed
```

注意：当前 `SkillExecutor` Protocol 的签名（`artifact/local_path/input_data/context`）与
实际 `ScriptSkillExecutor.execute(request)` 不一致，Runtime/Worker 依赖的是具体类。P1 先
统一实际使用的 `execute(request) -> SkillExecutionResult` 协议，再装配策略：

- Runtime `SkillToolSet._execute` 和 Worker `SkillTaskExecutor.execute` 都改为按 `source` 选择 executor；
- `ADMIN/SYSTEM` 继续使用现有 `ScriptSkillExecutor`；
- `USER` 使用 `SandboxedSkillExecutor`；
- 沙箱不可用时返回 `SKILL_SANDBOX_UNAVAILABLE`，绝不回退进程内；
- `source` 必须来自服务端记录并进入冻结快照，缺失/非法值拒绝执行。

平台选择只发生在装配处一次：

```python
def build_sandbox_runner(settings, platform=sys.platform) -> SandboxRunner:
    if settings.sandbox_mode == "disabled":
        if settings.environment != "dev":
            raise SandboxUnavailable(...)   # 生产禁止 disabled
        return DisabledRunner(...)
    if platform == "linux":
        return BwrapRunner(...)
    if platform == "darwin":
        return SeatbeltRunner(...)          # 失败不自动降级
    raise SandboxUnavailable(...)
```

Skill 工具、Shell、草稿、审批逻辑都不判断平台，只调用 `runner.run(spec)`。

`disabled` 只允许显式 dev；生产配置成 disabled 必须启动/执行报错。Seatbelt 启动失败
也不能自动退回 dev-unsafe。

### 4.2 bwrap 执行参数

```text
bwrap --unshare-all --die-with-parent --new-session
  --clearenv
  --setenv PATH /opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin
  --setenv HOME /tmp
  --ro-bind /usr /usr
  --ro-bind /lib /lib
  --ro-bind-try /lib64 /lib64
  --ro-bind /bin /bin
  --ro-bind /etc /etc
  --ro-bind /opt /opt
  --dev /dev
  --size <limit> --tmpfs /tmp
  --ro-bind <package> /work        # 已批准 Skill：只读
  # 或 --bind <draft-copy> /work  # 草稿 Shell：独立副本，可写
  --chdir /work
```

- 不 bind `/mnt`、`/var/cache/muad/skills`、`/proc`、`/var/run`；
- **不能把共享 `READY` 缓存直接可写 bind 到 `/work`**：一次执行改脚本会污染后续执行；
  已批准包只读，每次执行使用独立可写临时目录；草稿使用独立副本；
- `/bin` 必须 bind：`session-manager` 启动器使用 `#!/bin/sh`；
- 纯脚本：`--unshare-net`；
- 声明了 `platforms` 的脚本：`--share-net`，并把当前执行的 session 目录 bind 进去；
  使用前必须完成受信 API 门控（见 §13）；
- 资源：`resource.setrlimit` + 容器 cgroup + 超时 + **流式输出上限**；
- 进程：`--die-with-parent` + 进程组回收；
- 输出：不能先用 `communicate()` 收完整流再截断；按块读取，超限终止或做有界丢弃，
  否则用户脚本持续输出会先吃满父进程内存。

### 4.3 session-manager

`SkillPlatformContext` 继续负责平台登录态解析。bwrap 只做三件事：

1. 把当前执行的 session 目录 bind 进沙箱；
2. 把 `MUAD_SESSION_SOCKET` 传给子进程；
3. 给 `session-manager` 启动器所在的目录加入 `PATH`，并 bind `/bin` 供 `#!/bin/sh` 使用。

状态文件交付约定：

- **沙箱模式强制使用执行专属目录里的普通只读 state 文件**，不使用 memfd /
  `/proc/<pid>/fd`；否则 `/proc` 被隐藏后 CLI 读不到。
- session 目录由父进程写入，沙箱只读/仅需要 connect；执行结束即删除。
- PlatOS 当前 `os.memfd_create` 不可用，实际走普通文件分支；但代码不能依赖这个巧合。

用户草稿声明 `platforms` 后，Shell 内可以执行：

```bash
session-manager get-state --skill-name <draft_key>
```

拿到 `sessionStateFile` 后，用 `requests` 访问业务平台。这样上传前可以验证登录态和接口逻辑。
真实链路仍需在内网 runtime/worker 中验收。

### 4.4 macOS 原生 Dev

bwrap 是 Linux-only，macOS 原生进程不能用。两种模式：

| 模式 | 行为 | 适用 |
|---|---|---|
| `dev-unsafe`（默认推荐） | 显式开启，脚本直接在本机执行，日志/审计留痕 | 本地快速开发 |
| `sandbox-exec` | macOS 自带 Seatbelt，按 profile 限制文件/网络 | 需要本地硬隔离 |

本地 K8s（OrbStack）仍然是 Linux 容器，走 bwrap，与生产一致。

`sandbox-exec` 如果要启用，需要先做一天左右的 profile PoC：允许 workspace、venv、`/usr`、`/opt/homebrew`、`/tmp`，拒绝 `~/.ssh`、`~/.kube`、`~/.aws`、`~/.docker`，并按需允许 network-outbound。若 profile 阻塞 Python，就先保持 `dev-unsafe`，不阻塞主线。

---

## 5. 用户自建 Skill 流程

### 5.1 草稿

- 独立 PVC：`muad-skill-drafts-rwx`，只挂 Console；
- 路径：`/mnt/muad-skill-drafts/{tenant}/{user_id}/{draft_key}/`；
- Runtime/Worker 不挂该 PVC；Shell 通过 Console 内部 API 取草稿 zip，执行后回写变更；
- 沙箱只看到当前草稿副本，看不到其他用户草稿。

### 5.2 工具

| 工具 | 作用 |
|---|---|
| `skill_draft_write` | 写/覆盖草稿文件 |
| `skill_draft_read` | 读文件或列目录 |
| `skill_draft_delete` | 删文件或整个草稿 |
| `skill_draft_review` | 只读确定性检查 |
| `skill_draft_submit` | 打包 + 校验 + 创建 PENDING artifact |
| `skill_draft_status` | 查最近提交状态/驳回原因 |
| `sandbox_shell` | 一次性命令，草稿范围内测试 |

Shell 是一次性命令，不做持久会话；支持 `python -m py_compile`、`python scripts/main.py`、`node --check`、`session-manager get-state` 等。

**草稿从第一次 Shell 开始就按用户代码执行，必须进沙箱。** 草稿里的 `platforms`
声明只是“我想用哪些平台”，不是授权依据；父进程必须根据受信执行身份、用户凭据模式
（USER_ONLY / SHARED_ONLY / USER_THEN_SHARED）和平台权限计算允许集合。审批只决定
发布，不补运行期授权。

### 5.3 source / author / 审批

| 字段 | 说明 |
|---|---|
| `skill.source` | `SYSTEM` / `ADMIN` / `USER` |
| `skill.author` | 从 `muad.skill.json` 读取，自由文本，仅展示 |
| `skill.owner_user_id` | `USER` Skill 的内部鉴权归属；不用于 UI 展示，不是 author 的外键 |
| `skill_artifact.submitted_by_user_id` | 提交者平台用户，用于默认授权/审计 |
| `skill_artifact.submitted_agent_id` | 上传时所在 Agent |
| `skill_artifact.submitted_channel` | WECOM / WEBCHAT |
| `skill_artifact.review_status` | `PENDING` / `APPROVED` / `REJECTED` / `SUPERSEDED` |
| `review_note` / `reviewed_by` / `reviewed_at` | 审批记录 |

规则：

- 待审批同名覆盖：只允许同一 `owner_user_id` 覆盖自己的 PENDING；旧 PENDING →
  `SUPERSEDED`，新 PENDING；每个 Skill 同时只允许一个 PENDING（partial unique index）；
- **覆盖判定只认 `skill.owner_user_id`（首次提交的 PlatformUser）**，不认
  `skill.author`、不认 Console 账号、不认上传时 Agent；不同用户提交同 key 返回
  `SKILL_KEY_EXISTS`，不能覆盖、不能看到对方的草稿；
- 已审批锁定：用户不能提交新版；管理员解绑 + 软删旧 Skill 后重新提交；
- 被驳回可修改重提；
- 审批通过自动：
  - `SkillUserGrant(skill, submitted_by_user_id)`；
  - `AgentSkillBinding(skill, submitted_agent_id)`；
  - **不自动恢复已撤销的 `AgentAccessGrant`**：审批时复核该授权仍 active；若已撤销，
    审批失败并返回 `AGENT_ACCESS_REVOKED`，由管理员先决定是否恢复；
- 审批必须在同一事务内完成：锁定 artifact 行、校验仍为 PENDING、切换
  `current_artifact_id`、写授权/绑定/审计；防止“管理员批准旧提交的同时用户提交新版”；
- 管理员可下载待审包和已审批包，带 `SKILL_ARTIFACT_DOWNLOAD` 审计；
- 管理员测试：用现有用户范围 UI 把测试用户加入 grant，并确保该用户有目标 Agent 访问权。

### 5.4 系统 Skill

- 不做独立 Tab；`source=SYSTEM` 用标签/筛选区分；
- 管理员在 Console 上传 zip 时选择 `source=SYSTEM`；
- 自动 `user_scope=ALL`、`enabled=true`、`review_status=APPROVED`；
- 全局生效，不需要绑定；
- 三个 self skill 作为说明型 zip 手动上传；
- `/skills` 按系统/平台/用户三类分组回答。

---

## 6. 数据模型

| 对象 | 字段 |
|---|---|
| `skill` | `source`、`author`；`owner_user_id` 仅作为 `USER` Skill 的内部鉴权归属，不作为展示字段 |
| `skill_artifact` | `review_status`、`review_note`、`reviewed_by`、`reviewed_at`、`submitted_by_user_id`、`submitted_agent_id`、`submitted_channel` |
| `skill_draft` | tenant、owner_user_id、key、revision、last_submitted_artifact_id、last_submitted_revision |
| 草稿文件 | NFS `skill-drafts/{tenant}/{user}/{key}/`，不进 DB |

存量数据：

- `skill.source` 回填 `ADMIN`；
- `skill_artifact.review_status` 回填 `APPROVED`；
- 现有管理员导入保持自动通过。

---

## 7. 代码改造清单

| 模块 | 改动 |
|---|---|
| `build/docker-build/base/Dockerfile.python-node` | builder stage 构建 bwrap；复制 musl loader/libcap；wrapper |
| `packages/agent-core/.../sandbox/` | `SandboxRunner`、`BwrapRunner`、`SeatbeltRunner`、`policy` |
| `packages/agent-core/.../skill/executor.py` | 统一实际 `execute(request)` 协议；新增 `SandboxedSkillExecutor`，流式有界读取 stdout/stderr |
| `apps/agent-runtime/.../skill_tools.py` | 按 `source=USER` 路由；已批准包只读挂载 |
| `apps/agent-worker/.../worker/executor.py` | 按 `source=USER` 路由；独立可写工作目录 |
| `apps/agent-runtime/.../api/runs.py` | **P0 阻断项**：给 `/v1/runs` 等受信接口加 `require_internal_service`，或改为受控代理 |
| `packages/contracts` | `ResolvedSkill` / snapshot 增加 `source`；非法/缺失拒绝执行 |
| Console | `source`/`author`/`owner_user_id`/`review_status`、审批事务、并发约束、下载、草稿 API |
| Runtime | `skill_draft_*`、`sandbox_shell`、Console 内部客户端 |
| 前端 | source 标签/筛选、审批抽屉、下载、状态 |
| 测试 | 沙箱对抗、真实 session 链路、输出洪泛、进程回收、跨用户草稿、审批并发、E2E |

---

## 8. 基础镜像构建

已在 `build/docker-build/base/Dockerfile.python-node` 增加 builder stage：

```dockerfile
FROM alpine:3.20 AS bwrap-build
RUN apk add --no-cache build-base meson ninja linux-headers libcap-dev git
RUN git clone --depth 1 --branch v0.13.1 \
  https://github.com/containers/bubblewrap.git /src
WORKDIR /src
RUN meson setup build -Dselinux=disabled -Dman=disabled -Dtests=false \
 && ninja -C build \
 && mkdir -p /out \
 && cp build/bwrap /out/bwrap \
 && cp /lib/ld-musl-*.so.1 /out/ld-musl.so.1 \
 && cp /usr/lib/libcap.so.2 /out/libcap.so.2

FROM ${BASE_IMAGE}
# copy /out/* to /usr/local/lib/muad-sandbox
# copy bwrap-wrapper.sh to /usr/local/bin/bwrap
```

wrapper 内容（`build/docker-build/base/bwrap-wrapper.sh`）：

```sh
#!/bin/sh
set -eu
DIR=/usr/local/lib/muad-sandbox
exec "$DIR/ld-musl.so.1" --library-path "$DIR" "$DIR/bwrap" "$@"
```

本地构建命令：

```bash
docker build --platform linux/amd64 \
  --build-arg BASE_IMAGE=docker.sangfor.com/mss-base/python:3.12.13 \
  --build-arg TARGETARCH=amd64 \
  -t docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  -f build/docker-build/base/Dockerfile.python-node \
  build/docker-build/base

docker tag \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24
```

注意事项：

- 底座没有包管理器，不能 `apt-get install bubblewrap`；
- Python 在 `/opt/python3.12`，bwrap 必须 bind `/opt`；
- PlatOS Python 需要 `/lib64/ld-linux-x86-64.so.2`，使用 `--ro-bind-try /lib64 /lib64`；
- bwrap 启动 `session-manager` 时还需要 bind `/bin`（launcher 的 `#!/bin/sh`）；
- 只重建 `python-node` 底座，runtime/worker 自动继承；不新增镜像/流水线。

session-manager 前置测试镜像：

```bash
docker build --platform linux/amd64 \
  --build-arg BASE_IMAGE=docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106 \
  -t docker.sangfor.com/mss-base/sandbox-session-probe:0.1 \
  -f build/docker-build/sandbox/Dockerfile.session-probe .
```

本地验证命令见证据目录。生产构建机无法构建时，可在 Mac 构建 `linux/amd64` 镜像并推送到内网 registry。

---

## 9. 分阶段计划

### P0：底座 + 真实 bwrap / session-manager PoC

本次已在本地完成：

- `build/docker-build/base/Dockerfile.python-node` 增加 bwrap builder stage；
- `build/docker-build/base/bwrap-wrapper.sh`；
- 重建 `python-node` 底座，bwrap 0.13.1；
- 构建 `sandbox-session-probe:0.1`，本地 probe 已 `RESULT: PASS`。

一键重建：

```bash
bash build/docker-build/sandbox/build.sh
```

本地产物：

```text
/Users/jahan/Downloads/muad-bwrap-images/muad-python-node-bwrap-20261106.tar
sha256: f1d76f96fed62619e1da2a76bf047b0c366800ce0ff472f398527c558982eeec
```

tar 内包含：

```text
docker.sangfor.com/mss-base/python-node:3.12.13-node24
docker.sangfor.com/mss-base/python-node:3.12.13-node24-bwrap-20261106
docker.sangfor.com/mss-base/sandbox-session-probe:0.1
```

#### P0.1 内网导入

```bash
docker load -i muad-python-node-bwrap-20261106.tar
docker images | grep -E 'mss-base/python-node|sandbox-session-probe'
```

> 导入位置取决于节点容器运行时：
>
> - Docker 节点：`docker load -i ...`
> - containerd 节点：`ctr -n k8s.io images import muad-python-node-bwrap-20261106.tar`
> - 或把 tar 推到内网 registry 后让 K8s 拉取。
>
> 镜像必须对实际调度到的 runtime/worker 节点可见；只在 bastion 上 `docker load`
> 不够。

验证底座：

```bash
docker run --rm --platform linux/amd64 \
  docker.sangfor.com/mss-base/python-node:3.12.13-node24 \
  sh -c 'bwrap --version; python --version; node --version'
```

预期：

```text
bubblewrap 0.13.1
Python 3.12.13
v24.22.2
```

#### P0.2 前置 session-manager probe（已完成）

在 K8s 里用测试 Job 跑（不依赖 Runtime 代码）：

```bash
kubectl apply -f docs/operations/evidence/sandbox-2026-11-06/session-probe-job.yaml
kubectl -n muad wait --for=condition=complete job/fx-session-probe --timeout=120s
kubectl -n muad logs job/fx-session-probe
```

2026-11-06 内网实测结果：

```text
bwrap_rc 0
stdout session_ok probe-cookie
stderr
RESULT: PASS
```

注意：这是**模拟 broker**。它证明 bwrap 能在内网节点执行，`session-manager` CLI 能
通过 Unix socket 读取 state 文件；还没有证明 Runtime 的真实
`session_platform_environment` + Console/Redis + 真实平台凭据链路。

也可以在已导入镜像的 Docker 节点上直接跑：

```bash
docker run --rm --platform linux/amd64 \
  docker.sangfor.com/mss-base/sandbox-session-probe:0.1
```

如果 Docker 默认 seccomp 拦截 `unshare`，本地/单机 Docker 加：

```bash
--security-opt seccomp=unconfined
```

内网 K8s 已验证默认策略允许 `unshare`，Job 不需要设置 `seccompProfile`。

#### P0.3 真实 Runtime broker canary（已完成）

脚本：`docs/operations/evidence/sandbox-2026-11-06/real_broker_probe.py`

它直接调用真实的 `skill_platform_environment`，不经过对话 Skill、不改业务代码：

```text
真实 broker → Console resolve-egress-access → Redis Session
  → 真实平台凭据 → bwrap → session-manager get-state
  → sessionStateFile → requests 调测试平台
```

前提：

- runtime/worker 已用新底座重建，Pod 内 `bwrap` 可用；
- 有一个有测试平台凭据的 PlatformUser；
- 有一个非破坏性的测试接口。

在 canary runtime Pod 内执行：

```bash
kubectl cp docs/operations/evidence/sandbox-2026-11-06/real_broker_probe.py \
  <canary-runtime-pod>:/tmp/real_broker_probe.py

kubectl exec -it <canary-runtime-pod> -- env \
  TEST_ACTOR_USER_ID='<platform-user-uuid>' \
  TEST_PLATFORM_KEY='<platform-key>' \
  TEST_URL='https://test-platform.example/health' \
  TEST_SKILL_NAME='real-broker-probe' \
  python /tmp/real_broker_probe.py
```

可选参数：

```text
TEST_PATH            TEST_URL 为空时使用的相对路径
TEST_PARAMS_JSON     查询参数 JSON，例如 '{"page":1}'
TEST_BODY_JSON       请求体 JSON，例如 '{"id":"A-1"}'（配合 TEST_METHOD=POST）
TEST_TENANT_ID       默认 SharedSettings.default_tenant_id
TEST_EXECUTION_ID    默认随机 UUID
TEST_METHOD          默认 GET
TEST_TIMEOUT_SEC     默认 15
TEST_EXPECT_STATUS   精确期望状态码；默认 2xx/3xx
TEST_INSECURE=1      仅测试环境跳过 TLS 校验
TEST_SHOW_BODY=1     输出 200 字节 body 前缀（默认关闭，避免泄露业务数据）
```

`TEST_URL` 可以直接带完整 path 和 query，不需要把参数写死在脚本里：

```bash
TEST_URL='http://soar-inner.sangfor.com.cn:30001/api/v1/health?foo=bar'
```

需要 query 对象或 POST body 时：

```bash
TEST_METHOD=POST \
TEST_URL='/api/v1/query' \
TEST_PARAMS_JSON='{"page":1,"size":20}' \
TEST_BODY_JSON='{"customer_id":"C-1001"}' \
python /tmp/real_broker_probe.py
```

预期：

```text
bwrap_rc 0
stdout {"ok": true, "status": 200, ...}
RESULT: PASS
```

失败时脚本会明确输出：

- `bwrap not found`：镜像是旧底座；
- `sessionStateFile points into /proc`：需要强制文件模式；
- `PLATFORM_ACCESS_DENIED` / `CREDENTIAL_MISSING`：测试用户没有该平台凭据；
- HTTP 非预期状态：测试 URL、CSRF/Cookie 或平台权限问题。

#### P0.3.1 通过后的下一步

P0.2 + P0.3 PASS 说明：

- bwrap 能在实际底座里启动；
- `/bin`、`/usr`、`/lib`、`/lib64`、`/etc`、`/opt` 的 bind 组合正确；
- Unix socket 能进入沙箱；
- 真实 `session_cli.py` 能通过 `session-manager` 读取 state 文件；
- 真实 Console/Redis/用户凭据 + `requests` 调测试平台成功。

P0.3 实测：

```text
bwrap_rc 0
{"ok": true, "status": 200, "url": "http://soar-inner.sangfor.com.cn:30001/",
 "cookie_names": ["login-source", "soc-token"]}
RESULT: PASS
```

之后再：

1. 通过 P0.4 的受信 API 门控、流式输出、只读包/独立工作目录；
2. 进入 P1 `SandboxedSkillExecutor` 代码改造；
3. 用真实 Skill 包做完整执行测试。

如果 P0.2 FAIL：

| 失败点 | 处理 |
|---|---|
| `bwrap: No permissions to create new namespace` | 检查 Pod `seccompProfile`/节点 user namespace；生产 Pod 已用 Python smoke 证明默认允许，优先看测试 Job 是否落在不同节点 |
| `session-manager: not found` | 检查 `/bin` 是否 bind；launcher 需要 `/bin/sh` |
| `session_cli` 读不到 state 文件 | 检查 session 目录是否 bind 到沙箱内同一路径 |
| `probe-cookie` 不匹配 | 检查 broker 的 `skillName` 与 `--skill-name` 是否一致 |

#### P0.4 进入 P1 前的安全阻断项

以下任一项未完成，不开始用户脚本/Shell 的业务接入：

1. **受信 API 门控**：`/v1/runs` 等 Runtime 受信接口必须加
   `require_internal_service`，并让 Gateway/Console 的出站客户端携带
   `X-Internal-Service`；或者改成父进程受控代理。否则 `--share-net` 下的用户脚本
   可以直接打 Runtime 未鉴权接口。
2. **输出流式上限**：用户脚本执行不能先用 `communicate()` 收完整输出；必须按块读取，
   超限终止/有界丢弃，避免 stdout 洪泛吃满父进程内存。
3. **只读包 / 独立工作目录**：已批准包只读，草稿用独立副本，不能可写 bind 共享
   `READY` 缓存。
4. **真实 session 链路**：已完成（P0.3 PASS，真实 broker + 真实平台 HTTP 200）。
5. **最终指纹**：记录最终 Pod 的 kernel、`seccompProfile`、UID、capabilities、节点、
   base image digest、runtime/worker image digest、bwrap 源码 commit（v0.13.1）。

### P1：沙箱执行层（1–2 周）

- `SandboxRunner` / `BwrapRunner`；
- `SandboxedSkillExecutor`；
- Runtime/Worker 按 `source` 路由；
- Shell 工具；
- 超时、取消、进程组回收、审计、fail closed；
- 对抗测试：越权文件、网络、fork bomb、环境变量泄漏。

### P2：Skill 自建业务（2–3 周）

- 草稿 PVC + 内部 API；
- `skill_draft_*` / `sandbox_shell`；
- `source`/`author`/`review_status`/审批队列/下载；
- 待审批覆盖、已审批锁定、自动绑定/授权；
- 前端筛选、审批抽屉、`/skills` 分组。

### P3（可选）：macOS Seatbelt

- 仅在要求“原生 dev 也必须硬隔离”时做；
- 不影响 Linux 生产路径。

---

## 10. 风险与边界

| 风险 | 说明 | 处理 |
|---|---|---|
| bwrap 共享内核 | 不是 gVisor/Kata 级隔离 | 内网可信用户 + 管理员审批；未来可升级独立沙箱 |
| 网络未做 host 白名单 | `--share-net` 可访问内网任意地址和未鉴权 Runtime `/v1` | **P0 先门控受信 API**；真正限制出网需父进程代理 |
| 输出洪泛 | 现有 `communicate()` 先收全量再截断 | P1 改为流式有界读取，超限终止 |
| 共享 READY 缓存被写 | 可写 bind 会污染后续执行 | 已批准包只读；每次独立工作目录 |
| `/proc` 不可见 | 依赖 `/proc` 的脚本/memfd state 会失败 | 强制普通只读 state 文件；self-skill-creator 说明 |
| 内核 4.18 较老 | 安全补丁/维护状态需关注 | 运维跟进；沙箱不替代内核升级 |
| `sandbox-exec` 可能被移除 | macOS 已标记 deprecated | 原生 dev 可退回 `dev-unsafe`；生产不受影响 |
| 基础镜像需重建推送 | 已构建服务镜像不会自动继承新底座 | 重建 runtime/worker；记录 base/service digest 与 bwrap commit |
| 真实 session-manager 未在真实 broker 上跑过 | 目前只验证了 probe 模拟 broker | P0 必做完整链路 |
| seccomp 结论范围有限 | `seccompProfile` 为空不代表具体策略 | 最终 Pod 记录实际 seccomp/UID/caps/节点 |

---

## 11. 待确认

1. P0 是否按“先重建基础镜像，再改业务代码”的顺序执行；
2. 原生 macOS Dev 默认使用 `dev-unsafe`，还是要求 `sandbox-exec` 硬隔离；
3. 平台脚本网络是否接受“内网放开 + session-manager 按执行上下文发登录态”；
4. 已审批 Skill 删除时，是否提供“一键解绑 + 软删”的管理员操作（推荐提供，需二次确认和审计）。

---

## 12. 证据索引

| 文件 | 内容 |
|---|---|
| [`evidence/sandbox-2026-11-06/README.md`](evidence/sandbox-2026-11-06/README.md) | 证据目录与复现命令 |
| [`kernel_smoke.py`](evidence/sandbox-2026-11-06/kernel_smoke.py) | 无 bwrap 的内核能力测试 |
| [`bwrap_smoke.sh`](evidence/sandbox-2026-11-06/bwrap_smoke.sh) | bwrap 隔离/网络/Unix socket 测试 |
| [`Dockerfile.sandbox-test`](evidence/sandbox-2026-11-06/Dockerfile.sandbox-test) | PlatOS + bwrap 测试镜像 |
| [`base-image-inspection.txt`](evidence/sandbox-2026-11-06/base-image-inspection.txt) | 底座镜像检查 |
| [`local-macos-bwrap.txt`](evidence/sandbox-2026-11-06/local-macos-bwrap.txt) | macOS/OrbStack 探索阶段实测输出（bwrap 0.13.2） |
| [`new-base-bwrap-smoke.txt`](evidence/sandbox-2026-11-06/new-base-bwrap-smoke.txt) | 最终底座 bwrap 0.13.1 冒烟输出 |
| [`intranet-k8s-kernel-smoke.txt`](evidence/sandbox-2026-11-06/intranet-k8s-kernel-smoke.txt) | 内网 runtime/worker Pod 实测输出 |
| [`session-probe.txt`](evidence/sandbox-2026-11-06/session-probe.txt) | bwrap + 真实 session_cli + Unix socket 前置测试输出 |
| [`session-probe-job.yaml`](evidence/sandbox-2026-11-06/session-probe-job.yaml) | 内网 K8s 运行 session probe 的 Job |
| [`session_probe.py`](evidence/sandbox-2026-11-06/session_probe.py) | session-manager 前置 probe 源码（模拟 broker） |
| [`Dockerfile.session-probe`](evidence/sandbox-2026-11-06/Dockerfile.session-probe) | probe 测试镜像 Dockerfile |
| [`real_broker_probe.py`](evidence/sandbox-2026-11-06/real_broker_probe.py) | P0.3 真实 Runtime broker canary |
| [`real-broker-probe.txt`](evidence/sandbox-2026-11-06/real-broker-probe.txt) | P0.3 真实平台 HTTP 200 实测输出 |
| [`landlock-seccomp.txt`](evidence/sandbox-2026-11-06/landlock-seccomp.txt) | Landlock 不可用与 seccomp 结论 |

> 外部复查反馈的原文没有纳入仓库；逐项结论与修正见本文第 13 节。

---

## 13. 复查反馈与修正

GPT 的复查结论成立：**bwrap 技术选型可以保留，但不能把当前状态写成“执行方案已闭环”**。
以下问题已进入设计和验收条件：

| # | 复查问题 | 处理 |
|---|---|---|
| 1 | `--share-net` 保留 Pod 网络，包含 loopback 与未鉴权 Runtime `/v1/runs` | **P0 阻断项**：先给 `/v1/runs` 等受信接口加 `require_internal_service`，Gateway/Console 客户端带内部身份；否则不改用户脚本接入。真正限制业务出网另做父进程代理 |
| 2 | `session-manager` 启动器依赖 `/bin/sh`，只 bind `/usr` 会找不到 | bwrap 参数补 `--ro-bind /bin /bin`；probe 已按此修正并通过 |
| 3 | `sessionStateFile` 可能走 memfd，返回父进程 `/proc/<pid>/fd`，与隐藏 `/proc` 冲突 | 沙箱模式强制使用执行专属目录中的普通只读 state 文件；不再依赖 memfd；验收必须覆盖真实 CLI → 真实 broker → 读 state → 请求平台 |
| 4 | 草稿在审批前已执行代码并取得登录态，审批不能承担这段安全保障 | 草稿 Shell 从第一次执行就进沙箱；平台 key 的允许集合从受信执行身份/凭据计算，**不能只信草稿 `platforms` 声明**；审批只决定发布，不补运行期授权 |
| 5 | 现有输出上限先 `communicate()` 收全量再截断，stdout 洪泛会吃内存 | 新执行层必须流式读取、超限终止/有界丢弃；补并发、工作目录、文件数、子进程、session broker 请求预算 |
| 6 | 已批准包不能可写 bind 共享 `READY` 缓存，否则污染后续执行 | 已批准包只读；每次执行独立可写临时目录；草稿用独立副本；回写按 revision 做条件更新 |
| 7 | 没有稳定 owner 时，用户 B 可能覆盖用户 A 的 PENDING | `skill.owner_user_id` 仅作为 `USER` Skill 内部鉴权归属；PENDING 只允许同一 owner 覆盖（不认 author/Console 账号/Agent）；不同用户同 key 拒绝；每个 Skill 最多一个 PENDING；审批事务锁定并复核状态 |
| 8 | 审批通过自动恢复 `AgentAccessGrant` 会覆盖审批期间的撤权 | 审批时复核 `AgentAccessGrant` 仍 active；已撤销则失败并返回 `AGENT_ACCESS_REVOKED`，不自动恢复 |
| 9 | `SkillExecutor` 协议签名与实际 `ScriptSkillExecutor.execute(request)` 不一致 | 统一实际使用的 `execute(request)` 协议，再装配 `SandboxedSkillExecutor`；`source` 必须来自服务端记录并进入冻结快照，缺失/非法拒绝执行 |
| 10 | `source=SYSTEM` 全局生效会改现有 effective 查询 | 同步改 `list/count/catalog/snapshot/execution` 所有入口，并补测试 |
| 11 | 已有 runtime/worker 镜像不会随底座自动更新；`build-image.sh` 本地有同名底座会跳过拉取 | 底座更新后重建 runtime/worker；记录 base/service digest 与 bwrap commit；构建前强制按 digest 或 `docker pull` 获取底座 |
| 12 | seccomp 结论只能限定于当前受测环境 | 最终 Pod 记录实际 `seccompProfile`、UID、capabilities、节点、kernel；本地 `seccomp=unconfined` 结果不作为生产结论 |

### 13.1 修正后的状态

- **已完成**：bwrap 可行性、PlatOS 底座 + bwrap 构建、本地与内网 K8s session-manager probe（模拟 broker）、内网 runtime/worker 内核 primitives、真实 Runtime broker canary（真实平台 HTTP 200）。
- **未完成/未闭环**：受信 API 门控、流式输出、只读包/独立工作目录、owner/审批事务、服务镜像 digest 记录、最终 Pod 指纹。
- **结论**：可以继续按 bwrap 方向做 P0/P1，但在上述阻断项完成前，不宣称“生产执行方案已验收”。
