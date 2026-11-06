# Skill 自建与执行沙箱方案探索与定稿

日期：2026-11-06  
状态：**技术选型确定，执行策略与生产集成待验收**；P0.3 已证明真实 broker 的 session 交付、真实受保护接口 Cookie 正负向鉴权和平台 HTTP 连通性；原 P0.4 安全门控并入 P1 首批，执行层接入待实施。当前对话触发 Skill 仍走原执行路径。
范围：用户自建 Skill 的草稿、审查、上传、审批、执行沙箱、通用 workspace_shell，以及 macOS Dev / 本地 K8s / 内网 Linux K8s 三端运行方式。
手动测试证据：[`evidence/sandbox-2026-11-06/`](evidence/sandbox-2026-11-06/)

---

## 0. 结论

**采用“现有 Agent Runtime / Worker 镜像内置 bwrap 的 Pod 内进程级沙箱”，不新增独立沙箱服务、不新增 K8s Controller/CRD、不新增镜像流水线。**

> 这是**技术选型结论**，不等于生产验收。执行策略与生产集成的阻断项见 §13；
> 真实 session-manager 的独立 canary 已通过；受信 Runtime API 门控、业务鉴权和真实
> Runtime/Worker Skill 执行等安全验收完成前，不能宣称执行方案闭环。

| 环境 | 沙箱方式 |
|---|---|
| 内网 Linux K8s（runtime / worker） | `bwrap`，在现有 Pod 内执行 |
| Mac 本地 K8s（OrbStack） | `bwrap`，与生产同一路径 |
| macOS 原生 Dev（`uv run` / uvicorn） | `sandbox-exec`（Seatbelt）或显式 `dev-unsafe` |
| 管理员 / 系统 Skill 脚本 | `bwrap`，`TRUSTED` policy |
| 用户 Skill 脚本 | `bwrap`，`UNTRUSTED` policy |
| `skill_draft_shell` | `bwrap`，草稿副本 |
| `workspace_shell` | `bwrap`，所有 Agent 默认可用，user + Agent 隔离 workspace |

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
| K8s Job per execution | 否但需自管生命周期 | 不适用 | Pod 网络/资源边界，仍共享宿主内核 | 同步延迟高，暂不选 |
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
- 首批使用者是内网员工，但草稿、通用 Shell 和第三方 pip/npm 包均可能执行未审批代码；
  员工身份和发布审批不能替代执行期隔离；
- 保留进程级 bwrap 选型，生产启用须完成文件、凭据、网络与资源边界验收。
  要求隔离宿主内核攻击面时另评估 gVisor/Kata；独立 Pod 主要增强网络、SA 和资源边界。

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

真实 Runtime broker canary 也已通过（2026-11-06），并补了 Cookie 正负向对照：

```text
valid   -> 200，cookies login-source + soc-token
none    -> 403，code 9451（非法操作）
invalid -> 403，code 9451（非法操作）
```

请求的是真实受保护接口 `/order/v1/get_expert`，不是平台根路径。它使用真实
Console/Redis/平台凭据，不是模拟 broker；详见
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
- 受测 Pod 的实际策略允许 `unshare` / `mount`，本次无需修改 seccomp；
  该结论不推广到其他节点、容器运行时或重建后的 Pod，最终指纹仍须复核实际策略。

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
  调真实受保护接口 `/order/v1/get_expert`：valid=200、none=403、invalid=403。
  这证明登录态真实生效，而不是只有 HTTP 连通性。

尚未验证（P1 首批）：

- 真实用户 Skill 包通过 `SandboxedSkillExecutor` 在 bwrap 内执行；
- Worker async Skill 通过 bwrap 执行；
- 受保护接口的有效/无/无效 Cookie 对照、业务成功断言及 POST/CSRF；
- Redis 冷 session 重新登录与热 session 复用；真实 Run/Task 与 actor、tenant 的绑定；
- 受信 Runtime API 门控后的拒绝路径；
- 输出流式上限、只读包/独立工作目录、审批事务。

---

## 4. 最终架构

```text
                         Agent Runtime / Worker
                                  │
                          Skill / Shell 执行入口
                                  │
                    SandboxedSkillExecutor（唯一执行路径）
                                  │
                        SandboxPolicy（按 source 选 profile）
                    ┌─────────────┴─────────────┐
                    │                           │
              USER + scripts             ADMIN / SYSTEM
                    │                           │
                    ▼                           ▼
             UNTRUSTED profile           TRUSTED profile
                    │                           │
                    └─────────────┬─────────────┘
                                  ▼
                            SandboxRunner
                    ┌─────────────┴─────────────┐
                    │                           │
               Linux Pod                  macOS 原生 Dev
                 bwrap                    sandbox-exec（可选）
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

- Runtime `SkillToolSet._execute` 和 Worker `SkillTaskExecutor.execute` 都改为同一个
  `SandboxedSkillExecutor`，不再保留进程内脚本路径；
- `source=SYSTEM/ADMIN` 使用 `TRUSTED` policy，`source=USER` 使用 `UNTRUSTED` policy；
  差异只在挂载/网络/资源，不在执行器代码；
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
  --share-net
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
  --ro-bind <package> /skill      # 已批准 Skill：只读
  --bind <workspace> /workspace
  --chdir /workspace
```

- 不 bind `/mnt`、`/var/cache/muad/skills`、`/proc`、`/var/run`；
- **不能把共享 `READY` 缓存直接可写 bind 到 `/workspace`**：一次执行改脚本会污染后续执行；
  已批准包只读，每次执行使用独立可写临时目录；草稿使用独立副本；
- `/bin` 必须 bind：`session-manager` 启动器使用 `#!/bin/sh`；
- `/etc`、`/opt` 只读仍可能泄露秘密，正式 policy 应清点并缩小到所需目录/文件；
  需要 `/proc` 兼容性时评估独立 PID namespace 内的新 procfs，不能 bind Pod 的 `/proc`；
- 网络与 Pod 一致：`--unshare-all` 后显式 `--share-net`，所有 Skill、草稿 Shell、
  `workspace_shell` 使用同一口径，不再按脚本类型分 `--unshare-net`；
- `/v1` 受信 API 门控是硬前置；`platforms` 声明不限制实际 socket 请求；
- 要求只访问声明平台或内网 mirror 时，必须另行引入独立 net namespace 和强制出口代理；
  当前版本不做这层限制；
- 资源：`resource.setrlimit` + 容器 cgroup + Pod 级预算 + 超时 + **流式输出上限**；
  容器 cgroup 不是单次执行配额，执行级硬限制的缺口见 §14.1；
- 进程：`--die-with-parent` + 完整后代回收；验收正常结束、取消、超时、父进程退出、
  `setsid`、双重 fork 和后台进程，不能只假定杀进程组即可完成清理；
- 输出：不能先用 `communicate()` 收完整流再截断；按块读取，超限终止或做有界丢弃，
  否则用户脚本持续输出会先吃满父进程内存。

### 4.2.1 Python 解释器参数

`script_command()` 统一使用：

```text
python -E -s -B <script>
```

而不是当前的 `python -I`：

| 参数 | 作用 |
|---|---|
| `-E` | 忽略 `PYTHONPATH` / `PYTHONHOME` 等所有 `PYTHON*` 环境变量 |
| `-s` | 不加载用户级 `site-packages` |
| 不加 `-P` | 保留入口脚本目录在 `sys.path[0]`，`import shared` 正常 |
| `-B` | 不写 `__pycache__`，避免往只读 Skill 包写 `.pyc` |

原因：

- `-I` 隐含 `-P`，不会把入口脚本目录加入 `sys.path`；同目录 `shared.py` 会
  `ModuleNotFoundError`；
- 设 `cwd` 或 `PYTHONPATH` 都不能绕过 `-I`；
- 在 bwrap 内，环境已清空、`/usr`/`/opt` 只读、用户 site 为空，`-E -s` 已足够保持
  环境隔离；真正的边界是 bwrap，不是 `-I`。

约定：

- **`-E -s -B` 由框架 `script_command()` 统一注入，不写进每个 `SKILL.md`**；
- `SKILL.md` 继续写 `python scripts/run.py`，业务调用方式不变；
- `execute_skill` / `run_skill_script` 自动带这些参数；
- Shell 工具里普通 `python scripts/run.py` 也可以工作；如果要和框架完全一致，
  可在 PATH 最前面放一个 `python` wrapper，或在 Shell 规范里使用
  `python -E -s -B`，但不需要改 Skill 文档；
- 老 Skill 之前加的 `sys.path.insert(0, dirname(__file__))` 可以保留，也可以删除；
- 该改动属于 P1 执行器改造，不改变现有 Python CLI 参数契约。

### 4.2.2 Skill 数据与输出目录

统一执行环境挂载：

```text
/skill                         已批准 Skill 包，只读
/workspace                     当前 tenant + user + Agent，可写
  ├── data/                    SKILL_DATA_DIR
  └── outputs/{run_id}/        SKILL_OUTPUT_DIR
```

约定：

- `SKILL_OUTPUT_DIR` 每次执行独立，两次执行结果互不影响；
- `SKILL_DATA_DIR` 按 user + Agent 持久化，跨 Run 共享；
- 老 Skill 的 `cache` / `logs` / `companies` 迁到 `SKILL_DATA_DIR`，报告等单次结果写
  `SKILL_OUTPUT_DIR`；
- `SKILL_OUTPUT_DIR` 可能为空（非 Skill 上下文），脚本必须先判空；
- `workspace_shell` 使用同一 workspace 根，但输出目录按本次命令生成，不注入
  session-manager；
- `execute_skill` / `run_skill_script` 与 `workspace_shell` 共用目录约定，区别只在
  Skill 包与 session-manager。

### 4.3 session-manager

`SkillPlatformContext` 继续负责平台登录态解析。bwrap 只做三件事：

1. 把当前执行的 session 目录 bind 进沙箱；
2. 把 `MUAD_SESSION_SOCKET` 传给子进程；
3. 给 `session-manager` 启动器所在的目录加入 `PATH`，并 bind `/bin` 供 `#!/bin/sh` 使用。

状态文件交付约定：

- **沙箱模式强制使用执行专属目录里的普通只读 state 文件**，不使用 memfd /
  `/proc/<pid>/fd`；否则 `/proc` 被隐藏后 CLI 读不到。
- 上一条是待实现的生产要求。当前 broker 仍自动选择 memfd/文件，canary 仅拒绝
  `/proc/` 路径；本次文件分支通过不等于强制文件模式已经实现。
- session 目录由父进程写入，沙箱只读/仅需要 connect；执行结束即删除。
- PlatOS 当前 `os.memfd_create` 不可用，实际走普通文件分支；但代码不能依赖这个巧合。
- broker 的连接、并发、请求次数、state 文件数/总大小应有执行级预算；仅校验 Skill 名
  不能承担身份授权，用户和平台集合必须来自受信 Run/Task。
- state 中的 Cookie 是凭据，脚本能复制到 workspace、stdout 或产物；删除临时文件
  不会撤销副本。要求脚本不能获取 Cookie 原文时，需要父进程代发业务请求。

`session-manager get-state` 的 stdout 兼容：

- 当前 `_write_state()` 只返回 `version` / `status` / `skillName` / `sessionStateFile`，
  没有旧版 `platforms` list；
- 旧 Skill（如 `login-mssp-platform`）会遍历 stdout 的 `platforms` list，读取
  `platform` / `expiresAt`；缺失时报 `platform_not_found`，退出码 3；
- 在 `_write_state()` 返回值中追加旧版摘要，**不改变 `sessionStateFile` 内容**：

```json
{
  "version": 1,
  "status": "ready",
  "skillName": "...",
  "sessionStateFile": "...",
  "platforms": [
    {"platform": "mssp", "source": "session", "expiresAt": "..."}
  ]
}
```

- `source` 当前固定 `"session"`（`acquire()` 不暴露 cache/refresh）；`expiresAt` 必须存在；
- 新 Skill 继续读 `sessionStateFile.platforms.<key>.cookies` / `requestProfile`，不受影响。

用户草稿声明 `platforms` 后，Shell 内可以执行：

```bash
session-manager get-state --skill-name <draft_key>
```

拿到 `sessionStateFile` 后，用 `requests` 访问业务平台。这样上传前可以验证登录态和接口逻辑。
Runtime 独立 canary 已通过；受保护业务接口、真实 Skill 包和 Worker async 仍需验收。

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
| `skill_draft_shell` | 一次性命令，草稿范围内测试，工具名称唯一 |
| `workspace_shell` | 通用用户工作区 Shell，所有 Agent 默认可用，不增加配置项；见 §14.3 |

`skill_draft_shell` 是一次性命令，不做持久会话；支持 `python -m py_compile`、
`python scripts/main.py`、`node --check`、`session-manager get-state` 等。
`workspace_shell` 使用独立的 user + Agent workspace，不与草稿混用。

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
| `packages/agent-core/.../skill/executor.py` | 统一实际 `execute(request)` 协议；`script_command()` 改为 `python -E -s -B`；新增 `SandboxedSkillExecutor`，流式有界读取 stdout/stderr |
| `apps/agent-runtime/.../skill_tools.py` | 所有脚本统一走 `SandboxedSkillExecutor`；按 `source` 选 policy；已批准包只读挂载 |
| `apps/agent-worker/.../worker/executor.py` | 所有脚本统一走 `SandboxedSkillExecutor`；按 `source` 选 policy；独立可写工作目录 |
| `apps/agent-runtime/.../api/runs.py` | **P1 首批阻断项**：给 `/v1/runs` 等受信接口加 `require_internal_service`，或改为受控代理 |
| `packages/contracts` | `ResolvedSkill` / snapshot 增加 `source`；非法/缺失拒绝执行 |
| Console | `source`/`author`/`owner_user_id`/`review_status`、审批事务、并发约束、下载、草稿 API |
| Runtime | `skill_draft_*`、`skill_draft_shell`、`workspace_shell`、Console 内部客户端 |
| Workspace | 按 tenant + user + Agent 隔离的持久目录、配额、TTL、附件/产物接入 |
| Egress | 与 Pod 网络一致（`--share-net`）；不做 mirror allowlist 或 egress proxy |
| 前端 | source 标签/筛选、审批抽屉、下载、状态 |
| 测试 | 沙箱对抗、真实 session 链路、输出洪泛、进程回收、workspace 配额/TTL、pip/npm 安装边界、跨用户草稿、审批并发、E2E |

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
- 底座更新后，必须用现有流水线重建并部署 runtime/worker 服务镜像；已有镜像不会自动继承。
  构建时显式拉取或固定底座 digest，记录服务 image digest 和所有副本的实际 imageID；
  不新增独立沙箱服务或镜像流水线。

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

受测内网 K8s 环境允许 `unshare`，本次 Job 未新增 `seccompProfile`；换节点或运行时须复测。

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
TEST_PATH            TEST_URL 为空时使用的路径；/ 开头按站点根路径，否则按 baseUrl 前缀解析
TEST_PARAMS_JSON     查询参数 JSON，例如 '{"page":1}'
TEST_BODY_JSON       请求体 JSON，例如 '{"id":"A-1"}'（配合 TEST_METHOD=POST）
TEST_TENANT_ID       默认 SharedSettings.default_tenant_id
TEST_EXECUTION_ID    默认随机 UUID
TEST_METHOD          默认 GET
TEST_TIMEOUT_SEC     默认 15
TEST_EXPECT_STATUS   精确期望状态码；默认仅 2xx；any 只采集观察结果，不作通过断言
TEST_REQUIRE_JSON=1  要求响应可解析为 JSON
TEST_EXPECT_JSON     非空 JSON 对象，匹配响应顶层字段；成功码须来自真实接口契约
TEST_HEADERS_JSON    非凭据请求头对象；禁止注入 Cookie/Authorization
TEST_COOKIE_MODE     valid（默认）/none/invalid；对照只改变 Cookie，保留相同 CSRF 请求头
TEST_AUTH_COOKIE     invalid 模式替换的认证 Cookie 名，默认 soc-token
TEST_INSECURE=1      仅测试环境跳过 TLS 校验
TEST_SHOW_BODY=1     输出脱敏 JSON 前缀，最多 200 字符；非 JSON 正文省略，业务数据仍需谨慎分享
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
stdout {"ok": true, "check_scope": "http_status", "status": 200, ...}
RESULT: PASS
```

`PASS` 只表示所选 `check_scope` 通过。探针已禁止自动跳转，默认只接受 2xx；
`http_json` 还检查 JSON，只有提供 `TEST_EXPECT_JSON` 才断言指定业务字段。

2026-11-06 已在内网对受保护接口重跑 Cookie 正负向对照，结果见 P0.3.1。

失败时脚本会明确输出：

- `bwrap not found`：镜像是旧底座；
- state 文件相关 `ValueError`：检查 broker 返回值及 `/proc` 路径，生产需强制文件模式；
- broker 拒绝访问：检查测试用户的平台凭据、配置和内部身份，诊断不打印凭据原文；
- HTTP 非预期状态：测试 URL、CSRF/Cookie 或平台权限问题。

#### P0.3.1 通过后的下一步

P0.2 + P0.3 PASS 说明：

- bwrap 能在实际底座里启动；
- `/bin`、`/usr`、`/lib`、`/lib64`、`/etc`、`/opt` 的 bind 组合足够运行受测解释器和 CLI，
  尚未验证最小挂载权限及所有真实 Skill 依赖；
- Unix socket 能进入沙箱；
- 真实 `session_cli.py` 能通过 `session-manager` 读取 state 文件；
- 真实 Console/Redis 解析与 session 文件交付可用；
- 真实受保护接口 `/order/v1/get_expert` 的正负向 Cookie 对照通过。

P0.3 受保护接口实测（`TEST_EXPECT_STATUS=any`，只观察）：

| Cookie 模式 | 状态 | Cookie | 结论 |
|---|---:|---|---|
| `valid` | 200 | `login-source` + `soc-token` | 真实登录态可用 |
| `none` | 403 | 无 | 缺失登录态被拒绝 |
| `invalid` | 403 | 存在但 `soc-token` 被篡改 | 无效登录态被拒绝 |

三种请求使用同一 URL、同一组 Host/Referer/Timezone 头和同一 CSRF 逻辑，只改变 Cookie。

这说明 P0.3 不再只是“根路径 200/请求能发出去”，而是**真实业务接口 + 正负向鉴权对照**。
响应正文含业务数据，未写入证据文件。

之后再：

1. 并行开发受信 API 门控和 P1 执行层，完成流式输出、只读包/独立工作目录及资源预算；
2. 补受保护接口的 Cookie/业务鉴权及真实 Run/Task 身份测试；
3. 用真实 Skill 包分别验收 Runtime 同步链路和 Worker async，再启用用户执行。

#### P0.3.2 受保护业务接口与 Cookie 对照（待内网实测）

测试目标：`GET https://sitsoar.sangfor.com.cn/order/v1/get_expert`。
使用真实 broker 获取当前用户 Cookie，不把浏览器复制的 Cookie/JWT/CSRF 写进命令、文档或证据。
补充 Accept、Referer、XHR 和时区头；CSRF 头由 requestProfile 与 broker 返回的 Cookie 动态生成。
`Host` 与显式测试 URL 对齐，浏览器指纹、缓存、priority、固定 traceid 不作为认证条件复制。

在仓库根目录执行，先复制**本次更新后的**探针，避免 Pod `/tmp` 中仍是旧版：

```bash
set -eu
POD=$(kubectl -n muad get pod -l app=muad-agent-runtime \
  -o jsonpath='{.items[0].metadata.name}')
test -n "$POD"
kubectl -n muad cp docs/operations/evidence/sandbox-2026-11-06/real_broker_probe.py \
  "$POD":/tmp/real_broker_probe.py

for COOKIE_MODE in valid none invalid; do
  kubectl -n muad exec "$POD" -- env \
    TEST_ACTOR_USER_ID='142c789b-cd22-4dc7-9e69-b6a84a493eef' \
    TEST_PLATFORM_KEY='mssp' \
    TEST_URL='https://sitsoar.sangfor.com.cn/order/v1/get_expert' \
    TEST_SKILL_NAME='mssp-canary' \
    TEST_METHOD='GET' \
    TEST_HEADERS_JSON='{"Accept":"application/json, text/javascript, */*; q=0.01","Content-Type":"application/json","Host":"sitsoar.sangfor.com.cn","Referer":"https://sitsoar.sangfor.com.cn/index.html","X-Requested-With":"XMLHttpRequest","Timezone":"+08:00"}' \
    TEST_COOKIE_MODE="$COOKIE_MODE" \
    TEST_AUTH_COOKIE='soc-token' \
    TEST_EXPECT_STATUS='any' \
    TEST_TIMEOUT_SEC='30' \
    TEST_SHOW_BODY='1' \
    python /tmp/real_broker_probe.py
done
```

- `valid` 使用 broker 返回的 Cookie；`none` 不发送 Cookie；`invalid` 仅替换 `soc-token`，
  其他 Cookie 和 CSRF 头保持一致。各组请求不复用 requests 会话，不携带其他 Authorization。
- `any` 是观察模式：返回 `ok=null`、`check_scope=observation`、`RESULT: OBSERVED`，
  任何 HTTP 状态都不自动计为鉴权通过；连接、TLS、broker 等错误仍失败。
- `body_prefix` 仅输出脱敏后的 JSON 前缀，非 JSON 正文省略；仍可能含个人/业务数据，
  归档前只保留判断所需的业务码、结构和经过处理的身份信息。
- 接口成功响应结构尚未提供，不猜测成功码是 `0` 还是 `200`。确认后为有效组设置
  `TEST_EXPECT_STATUS=200`、`TEST_REQUIRE_JSON=1`、`TEST_EXPECT_JSON`，匹配实际成功码和可验证身份。
  例如响应确认为顶层 `code=0` 才可用 `TEST_EXPECT_JSON='{"code":0}'`，不能直接套用示例。
- 有效组须为业务成功且内容符合当前测试用户；无/无效组须按平台契约拒绝（401/403、
  登录跳转或 HTTP 200 的未登录业务码均须区分）。三组都成功时，不能证明该接口受 Cookie 保护。
- 首选上述用户提供的 HTTPS 地址，不关闭 TLS 校验。若 Pod 只能走内网入口，须先确认
  `http://soar-inner.sangfor.com.cn:30001/order/v1/get_expert` 确为同一平台映射，再改 TEST_URL；
  代理/Host/Referer 关系需记录，不能用入口不同解释或掩盖鉴权差异。

正式验收还需：冷/热 session、路径/query/空对象与非空 JSON、POST 与正确/缺失/错误 CSRF、
明确的非 200 和业务失败响应、真实 Run/Task 的 tenant/actor 绑定。
当前 broker 以 `PLATFORM_SERVICE/GET` 解析 session 后交出 Cookie，后续 requests 的 URL/方法
不经过 SDK EgressBoundary；POST canary 不代表方法或路径授权已受控。

本阶段聚焦方案细化与手动联调。执行上述命令后再记录真实平台响应，更新鉴权结论；
本地检查不能替代真实平台、Redis、bwrap 或 Worker 的生产集成验收。

如果 P0.2 FAIL：

| 失败点 | 处理 |
|---|---|
| `bwrap: No permissions to create new namespace` | 检查 Pod `seccompProfile`/节点 user namespace；生产 Pod 已用 Python smoke 证明默认允许，优先看测试 Job 是否落在不同节点 |
| `session-manager: not found` | 检查 `/bin` 是否 bind；launcher 需要 `/bin/sh` |
| `session_cli` 读不到 state 文件 | 检查 session 目录是否 bind 到沙箱内同一路径 |
| `probe-cookie` 不匹配 | 检查 broker 的 `skillName` 与 `--skill-name` 是否一致 |

#### P1 首批安全阻断项（原 P0.4）

以下任一项未完成，不启用用户脚本/Shell 的生产执行；允许先开发和验收执行器本身：

1. **受信 API 门控**：`/v1/runs` 等 Runtime 受信接口必须加
   `require_internal_service`，并让 Gateway/Console 的出站客户端携带
   `X-Internal-Service`；或者改成父进程受控代理。否则 `--share-net` 下的用户脚本
   可以直接打 Runtime 未鉴权接口。
2. **输出流式上限**：用户脚本执行不能先用 `communicate()` 收完整输出；必须按块读取，
   超限终止/有界丢弃，避免 stdout 洪泛吃满父进程内存。
3. **只读包 / 独立工作目录**：已批准包只读，草稿用独立副本，不能可写 bind 共享
   `READY` 缓存。
4. **真实 session 链路**：P0.3 状态交付/HTTP 连通性已完成；受保护接口的业务鉴权
   和真实 Run/Task 身份绑定未完成，见 P0.3.2。
5. **最终指纹**：记录最终 Pod 的 kernel、`seccompProfile`、UID、capabilities、节点、
   base image digest、runtime/worker image digest 和各副本 imageID、bwrap 源码 commit SHA。
   `v0.13.1` 是版本标签，tar SHA256 是归档校验值，均不能替代 OCI image digest/commit SHA。

可用于检查镜像内文件的命令必须传播失败，不能靠最后一个 echo 的退出码判断：

```bash
kubectl -n muad exec "$POD" -- sh -ec '
  bwrap --version
  test -x /usr/local/lib/muad-sandbox/bwrap
  echo BASE_IMAGE_OK
'
```

### P1：沙箱执行层（1–2 周）

- **首批硬前置**：`/v1` 服务身份门控 + Gateway/Console 出站带 `X-Internal-Service`；
- `SandboxRunner` / `BwrapRunner`；
- `SandboxedSkillExecutor`；
- `script_command()` 改为 `python -E -s -B`，兼容相邻模块 import；
- 流式有界输出、只读包、每次执行独立工作目录；
- Runtime/Worker 统一走沙箱执行器，按 `source` 选择 policy；
- Skill 执行挂载 user + Agent workspace，注入 `SKILL_DATA_DIR` / `SKILL_OUTPUT_DIR`；
- `workspace_shell` 对所有 Agent 开放；`skill_draft_shell` 走草稿副本；
- workspace 配额/TTL 与 Worker 内置清理循环（与挂载同批，避免磁盘无界增长）；
- 超时、取消、进程组回收、审计、fail closed；
- 对抗测试：越权文件、网络、fork bomb、环境变量泄漏。

### P2：Skill 自建业务（2–3 周）

- 草稿 PVC + 内部 API；
- `skill_draft_*` / `skill_draft_shell`；
- `source`/`author`/`review_status`/审批队列/下载；
- 待审批覆盖、已审批锁定、自动绑定/授权；
- 老 Skill 按 `SKILL_DATA_DIR` / `SKILL_OUTPUT_DIR` / session-manager / Linux 路径做最小改造；
- 前端筛选、审批抽屉、`/skills` 分组。

### P3（可选）：macOS Seatbelt

- 仅在要求“原生 dev 也必须硬隔离”时做；
- 不影响 Linux 生产路径。

---

## 10. 风险与边界

| 风险 | 说明 | 处理 |
|---|---|---|
| bwrap 共享内核 | 草稿、Shell、第三方包可执行未审批代码 | 文件/网络/凭据/资源边界验收；需要内核隔离时评估 gVisor/Kata |
| 网络未做 host 白名单 | `--share-net` 与 Pod 网络一致，可访问内网其他服务和公网，包括未鉴权 Runtime `/v1` | **P1 首批先门控受信 API**；接受 Pod 网络边界，不承诺执行级出网限制 |
| 输出洪泛 | 现有 `communicate()` 先收全量再截断 | P1 改为流式有界读取，超限终止 |
| 共享 READY 缓存被写 | 可写 bind 会污染后续执行 | 已批准包只读；每次独立工作目录 |
| `/proc` 不可见 | 依赖 `/proc` 的脚本/memfd state 会失败 | 强制普通只读 state 文件；self-skill-creator 说明 |
| 内核 4.18 较老 | 安全补丁/维护状态需关注 | 运维跟进；沙箱不替代内核升级 |
| `sandbox-exec` 可能被移除 | macOS 已标记 deprecated | 原生 dev 使用 `dev-unsafe`；生产不受影响 |
| 基础镜像需重建推送 | 已构建服务镜像不会自动继承新底座 | 重建 runtime/worker；记录 base/service digest 与 bwrap commit |
| 真实业务鉴权已做 Cookie 对照 | 受保护接口 valid=200、none=403、invalid=403 | 仍需补冷/热 session、POST/CSRF、真实包和 Worker 链路 |
| seccomp 结论范围有限 | `seccompProfile` 为空不代表具体策略 | 最终 Pod 记录实际 seccomp/UID/caps/节点 |
| 用户 workspace 磁盘增长 | pip/npm/node_modules/cache 可能到 GB 级 | 每个 user + Agent workspace 配额、文件数上限、30 天 TTL 清理、容量规划 |
| pip/npm 供应链与出网 | 安装及后续导入均可能执行恶意代码；可以访问公网源 | 接受 Pod 网络边界；安装到 workspace venv/node_modules；安装时不提供平台 session；不承诺包安全 |
| 通用 Shell 越权 | 网络开放时可访问内网其他服务 | P1 首批门控 Runtime API；不挂生产目录/SA token；审计命令 |
| session 凭据残留 | 脚本可复制 Cookie 到持久 workspace/输出，删除临时文件不等于撤销 | `workspace_shell` 不注入 session；Skill 执行按 `platforms` 注入；需要隐藏 Cookie 时由父进程代发请求 |
| 资源限制粒度不足 | 单进程 rlimit 和 Pod cgroup 不等于执行进程树硬配额 | 加权预算与有界排队；硬隔离要求需执行级 cgroup/独立 Pod |

---

## 11. 已确认口径与剩余问题

已确认：

1. 所有 Skill 脚本统一走 `SandboxedSkillExecutor`，按 `TRUSTED` / `UNTRUSTED` policy 区分；
2. `workspace_shell` 对所有 Agent 默认可用，不增加 Agent 配置项；
3. workspace 按 **tenant + user + Agent** 隔离，同一 user + Agent 跨会话共享，不同 Agent 不共享；
4. 网络与 Pod 一致：`--share-net`，可访问 Pod 网络和公网；不做 mirror allowlist 或 egress proxy；
5. `workspace_shell` 默认不提供 `session-manager`；平台登录态只跟 Skill 执行绑定；
6. 接受 bwrap 级隔离；gVisor/Kata/独立 Sandbox Pod 只作为后续增强；
7. 原生 macOS Dev 默认 `dev-unsafe`；本地 K8s 与内网 K8s 走 bwrap；
8. 已审批 Skill 删除提供“一键解绑 + 软删”，二次确认 + 审计；
9. 老 Skill 做最小改造适配，不做 Legacy 可写副本模式；
10. `SKILL_OUTPUT_DIR` 每次执行独立，`SKILL_DATA_DIR` 按 user + Agent 持久化；
11. `outputs` 与 `data` 默认 TTL 均为 30 天；
12. 清理复用 Worker 内置维护循环，不需要管理员创建定时任务，也不新增 CronJob。

剩余：

1. P1 首批的排期、canary 和真实 Skill 验收安排；
2. workspace 配额初值（每 workspace 500 MiB、每用户聚合 500 MiB、文件数 5,000）和 PVC 容量规划；
3. 清理循环的独立节拍、批量大小和告警阈值；
4. 旧 Skill 清单和逐包改造验收。

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
| [`bwrap-overhead.txt`](evidence/sandbox-2026-11-06/bwrap-overhead.txt) | bwrap 每次执行的启动与 RSS 开销基准 |
| [`service-auth-overhead.txt`](evidence/sandbox-2026-11-06/service-auth-overhead.txt) | 受信门控的本地 ASGI 性能对照与复现命令 |

> 外部复查反馈的原文没有纳入仓库；逐项结论与修正见本文第 13 节。

---

## 13. 复查反馈与修正

GPT 的复查结论成立：**bwrap 技术选型可以保留，但不能把当前状态写成“执行方案已闭环”**。
以下问题已进入设计和验收条件：

| # | 复查问题 | 处理 |
|---|---|---|
| 1 | `--share-net` 保留 Pod 网络，包含 loopback 与未鉴权 Runtime `/v1/runs` | **P1 首批阻断项**：门控整个 Runtime 受信 router（含 conversations），Gateway 的 JSON/SSE/重连等调用带内部身份；完成前不启用用户生产执行。具体调用面/性能见 §13.2 |
| 2 | `session-manager` 启动器依赖 `/bin/sh`，只 bind `/usr` 会找不到 | bwrap 参数补 `--ro-bind /bin /bin`；probe 已按此修正并通过 |
| 3 | `sessionStateFile` 可能走 memfd，返回父进程 `/proc/<pid>/fd`，与隐藏 `/proc` 冲突 | 待实现：沙箱模式强制普通只读 state 文件；当前仅受测环境命中文件分支，不视为策略已实现 |
| 4 | 草稿在审批前已执行代码并取得登录态，审批不能承担这段安全保障 | 草稿 Shell 从第一次执行就进沙箱；平台 key 的允许集合从受信执行身份/凭据计算，**不能只信草稿 `platforms` 声明**；审批只决定发布，不补运行期授权 |
| 5 | 现有输出上限先 `communicate()` 收全量再截断，stdout 洪泛会吃内存 | 新执行层必须流式读取、超限终止/有界丢弃；补并发、工作目录、文件数、子进程、session broker 请求预算 |
| 6 | 已批准包不能可写 bind 共享 `READY` 缓存，否则污染后续执行 | 已批准包只读；每次执行独立可写临时目录；草稿用独立副本；回写按 revision 做条件更新 |
| 7 | 没有稳定 owner 时，用户 B 可能覆盖用户 A 的 PENDING | `skill.owner_user_id` 仅作为 `USER` Skill 内部鉴权归属；PENDING 只允许同一 owner 覆盖（不认 author/Console 账号/Agent）；不同用户同 key 拒绝；每个 Skill 最多一个 PENDING；审批事务锁定并复核状态 |
| 8 | 审批通过自动恢复 `AgentAccessGrant` 会覆盖审批期间的撤权 | 审批时复核 `AgentAccessGrant` 仍 active；已撤销则失败并返回 `AGENT_ACCESS_REVOKED`，不自动恢复 |
| 9 | `SkillExecutor` 协议签名与实际 `ScriptSkillExecutor.execute(request)` 不一致 | 统一实际使用的 `execute(request)` 协议，再装配 `SandboxedSkillExecutor`；`source` 必须来自服务端记录并进入冻结快照，缺失/非法拒绝执行 |
| 10 | `source=SYSTEM` 全局生效会改现有 effective 查询 | 同步改 `list/count/catalog/snapshot/execution` 所有入口，并补测试 |
| 11 | 已有 runtime/worker 镜像不会随底座自动更新；`build-image.sh` 本地有同名底座会跳过拉取 | 底座更新后重建 runtime/worker；记录 base/service digest 与 bwrap commit；构建前强制按 digest 或 `docker pull` 获取底座 |
| 12 | seccomp 结论只能限定于当前受测环境 | 最终 Pod 记录实际 `seccompProfile`、UID、capabilities、节点、kernel；本地 `seccomp=unconfined` 结果不作为生产结论 |
| 13 | 原 canary 跟随跳转、接受 3xx，空 JSON 丢失，根路径拼接不明确 | 已修手动探针：禁止跳转、默认仅 2xx、保留 `{}`、区分根/相对路径；新增 Cookie 对照、业务字段断言和观察模式。2026-11-06 内网重跑通过 |
| 14 | Cookie/业务鉴权和 Run 身份不能由根路径 HTTP 200 推导 | 已测受保护接口：valid=200、none=403、invalid=403；仍待冷/热 session、POST/CSRF 和 Run/Task 身份绑定 |

### 13.1 修正后的状态

- **已完成**：bwrap 可行性、PlatOS 底座 + bwrap 构建、本地与内网 K8s session-manager probe（模拟 broker）、内网 runtime/worker 内核 primitives、真实 Runtime broker canary、真实受保护接口 Cookie 正负向对照（valid=200、none=403、invalid=403）。
- **未完成/未闭环**：受信 API 门控、流式输出、只读包/独立工作目录、
  真实 Runtime/Worker 包执行、资源/网络边界、服务镜像 digest 记录、最终 Pod 指纹。
- **P2 功能门禁**：owner/审批事务可随自建发布功能实现，但在功能开放前必须验收。
  owner 只决定包修改归属，平台凭据按当前 actor 获取；审批绑定包 digest，执行使用已批准快照。
- **结论**：可以继续开发 P0/P1；当前 Skill 尚未进入 bwrap，不能宣称生产执行方案已验收。

### 13.2 Runtime 受信门控的调用面与性能

建议将服务身份依赖放在 Runtime `/v1` 受信 router，覆盖 runs 与 conversations；
缺失、错误、服务端未配置 Token 均拒绝。Gateway 继续执行用户、租户、Agent 和 Run 归属校验，
不透传浏览器传入的服务身份头，也不把服务 Token 发给浏览器或沙箱。

Gateway 当前有服务 Token 字段，但以下请求仍未携带：`create_run`、`submit_run`、
`resume_run`、`open_events`、`open_event_stream`、`get_run`、`cancel_run`、
`cancel_active`、`create_conversation`。`get_run_scope` 和内部 webchat 请求已有门控头。
Worker 的内部结果投递等调用已携带 Token；仍需回归 Console/Worker 内部客户端，
并同步修改直接访问 Runtime 的验收/容量/调试客户端，避免只覆盖首个 POST 而漏掉 SSE 重连。

服务 Token 按目标请求注入，不放在可能访问多个目标的共享 HTTP 池默认 headers 中。
门控仅需本地比较，不应新增 DB、Redis 或远程鉴权调用。SSE 在建连/重连时校验一次，
不逐事件校验，添加请求头也不增加网络往返。

当前 `require_internal_service` 每次调用 `SharedSettings()`，会重读环境、`.env` 并校验配置，
且同步 FastAPI 依赖会调度线程池。推广门控时应使用启动时加载的 `process_settings()`
缓存和异步依赖，只做本地检查，以 `hmac.compare_digest` 比较 Token，保留默认拒绝语义。
这是待实施的代码改造，本次仅更新探针和文档。
依赖调度行为见 [FastAPI 官方说明](https://fastapi.tiangolo.com/async/#dependencies)。

本地 arm64/Python 3.12.13 的进程内 ASGI 对照（每组顺序 200 请求，无网络/业务逻辑）：

| 实现 | 平均 | P95 |
|---|---:|---:|
| 空接口，无门控 | 0.1181 ms | 0.1420 ms |
| 当前同步门控 + 每次构造设置 | 3.1376 ms | 3.1949 ms |
| 临时对照：缓存 Token + 异步本地校验 | 0.1364 ms | 0.1500 ms |

这是实现成本诊断，不是生产 SLO 或已落地优化；配置构造单独测得约 2.807 ms/次。
复现代码和范围见 [`service-auth-overhead.txt`](evidence/sandbox-2026-11-06/service-auth-overhead.txt)。
生产仍须同负载对照 CPU、吞吐、P95 和 SSE 建连；256 个在飞 Run 不等于 256 QPS。

---

## 14. 资源开销与 Shell 边界

### 14.1 每次执行新建 bwrap 的开销

bwrap 是**进程级沙箱**，不是每次创建容器/VM。原生 arm64 容器的基准（详见
`evidence/sandbox-2026-11-06/bwrap-overhead.txt`）：

| 执行 | 平均耗时 | bwrap 额外 |
|---|---:|---:|
| `/bin/true` | 0.13 ms | — |
| `bwrap ... /bin/true` | 1.08 ms | ~0.95 ms |
| `python -c pass` | 3.50 ms | — |
| `bwrap ... python -c pass` | 4.77 ms | ~1.27 ms |

RSS：

- 直接 Python：约 9.3 MB；
- bwrap 父/子进程：约 1.9 MB + 1.4 MB；
- 沙箱内 Python：约 8.9 MB。

结论：

- 主要内存是 Python/Node 解释器，bwrap 只增加几 MB；
- 完整后代回收后没有常驻用户进程；文件 page cache 和其他 cgroup 计费不能直接视为归零；
- 真正需要控制的是**完整进程树峰值**、并发、tmpfs、缓存和输出缓冲；
- `--tmpfs /tmp` 要设小上限（例如 8–16 MiB）；大量临时文件建议用
  disk-backed per-execution 目录，避免 tmpfs 计入内存；
- 增加每 Pod 的 sandbox 并发信号量和每子进程 rlimit，按 runtime Pod 内存上限调优。

这些数字来自 arm64 Debian 空脚本，不能用于推算 amd64 PlatOS 上的 256 个执行槽。
Runtime 的 Run 槽包含等待模型/网络的任务，沙箱使用独立的 Pod 总预算，覆盖 Skill、草稿、
workspace 和安装；多进程部署不能让每个进程各自持有一份完整 Pod 预算。

```text
沙箱预算 = Pod memory limit − 目标 Run 并发下 Runtime 实测峰值 − 安全余量
并行执行的完整进程树/临时文件等预算之和 ≤ 沙箱预算
```

仅作为 canary 初值：总预算四单位，普通 Python 一单位、Node/较重 Python 两单位、
pip/npm 安装四单位。假设分别预留 256/512/1024 MiB，Runtime 占 2048 MiB、
另留 768 MiB，则 4 GiB Pod 剩 1280 MiB；这些均为待验证假设，不是安全保证。
两核 Pod 的 CPU 密集执行再限制到一至两个；按 1/2/4/8 并发测真实包峰值和延迟后调优。
排队必须有长度、截止时间和取消处理。

`RLIMIT_AS` 限单进程虚拟地址空间，可能影响 Node/V8 预留地址；
`RLIMIT_NPROC` 按真实 UID 计数；`RLIMIT_FSIZE` 只限单文件；
`--max-old-space-size` 只限 V8 老生代。它们都不等于执行进程树内存/磁盘硬配额。
要求单任务不能拖垮 Runtime 时，需要可用的执行级 cgroup 或独立 Pod 资源边界；
仅 cgroup namespace 不会创建资源配额。容量以实际 cgroup 版本和完整计费数据验证。

### 14.2 Shell 工具边界

`skill_draft_shell` 专用于 Skill 草稿；通用用户工作区由独立工具 `workspace_shell` 提供，
所有 Agent 默认可用，不增加 Agent 配置项。两者共用执行引擎，权限与工作区目标分别由服务端确定。

建议：

- 工具名：`skill_draft_shell`，不保留旧名或兼容别名；
- 只在存在用户草稿/Skill 编写上下文时注册，普通对话不提供；
- 一次调用执行一条命令，不保持交互式会话；
- Python 解释器参数由框架策略统一（`-E -s -B`），`SKILL.md` 仍写 `python scripts/run.py`；
  Shell 里普通 `python` 也可用，需要完全对齐时使用受控 wrapper 或显式参数；
- 只作用于当前用户草稿的独立副本，不挂生产 PVC、不带服务 Token；
- 网络与 Pod 一致（`--share-net`），可访问 Pod 网络和公网；`/v1` 门控是硬前置；
- 超时、输出上限、禁止后台进程、杀整个进程组；
- 执行后校验变更文件路径/扩展名/大小/symlink，再按 revision 回写草稿；
- 记录 `skill_draft_shell` 审计，包含用户、草稿、命令摘要、退出码、耗时。

不支持：

- 在草稿工具中提供通用用户 workspace；
- 常驻交互式 Shell 或后台服务；workspace 持久化及安装边界见 §14.4/§14.5；
- 访问 `/mnt`、其他用户草稿、Runtime `/v1` 内部接口；
- 绕过沙箱直接执行。

通用执行能力已纳入 workspace_shell 设计，按 §14.6 分阶段实施；不能把草稿目录当通用 workspace。

### 14.3 Workspace Shell（方案 A）

`workspace_shell` 与 `skill_draft_shell` 共用执行引擎、分离工作区目标：

```text
WorkspaceShellRunner（bwrap）
  ├─ target = DRAFT        # skill_draft_shell：草稿副本，执行后校验并回写
  └─ target = USER         # workspace_shell：user + Agent 持久工作区
```

两个入口共用：

- 受校验的 SandboxSpec 与 bwrap 参数构造器、超时、输出上限、后代回收；
- 资源限制、并发信号量、审计、错误码；
- 文件变更收集与安全校验。

差异只在：

| 项目 | `skill_draft_shell` | `workspace_shell` |
|---|---|---|
| 挂载 | 当前 Skill 草稿副本 | 当前 user + Agent workspace |
| 持久化 | 回写草稿，revision CAS | 跨轮持久 |
| 授权 | Skill 编写上下文 | 所有 Agent 默认可用，不增加配置项 |
| 网络 | 与 Pod 一致（`--share-net`） | 与 Pod 一致（`--share-net`） |
| 生命周期 | 草稿存在期间 | user + Agent 空闲 TTL + 配额 |

模型不能传 `owner_user_id` / `workspace_id` / 任意宿主机路径；target 由服务端根据 Run
上下文决定。
共用构造器不代表两种 policy 的最终 argv 相同。

### 14.4 按 user + Agent 持久化 workspace

workspace 以 tenant + user + Agent 三维定位。PVC 上的目录：

```text
<workspace-root>/{tenant_id}/{user_id}/{agent_id}/
# 默认 <workspace-root> = /mnt/muad-artifacts/workspaces
  ├── data/                 # SKILL_DATA_DIR：持久数据
  └── outputs/
      └── {run_id}/         # SKILL_OUTPUT_DIR：本次执行输出
```

沙箱内只绑定当前 user + Agent 目录，根路径统一为 `/workspace`：

```text
/workspace/
  ├── data/
  └── outputs/{run_id}/
```

- 同一 user + Agent 跨会话共享；
- 不同 Agent 不共享；
- 只有当前目录进入沙箱，父进程不把 workspace 根暴露给模型。

workspace 持久化的是**磁盘/PVC，不是运行内存**：

- 每条命令仍然新建一个 bwrap，执行完进程退出；
- 用户不使用时没有常驻 Shell/沙箱；
- 只有 workspace 文件留在磁盘，page cache 可回收；
- 主要资源风险是磁盘增长，不是常驻内存。

目录约定：

| 变量 | 沙箱内路径 | 生命周期 |
|---|---|---|
| `SKILL_DATA_DIR` | `/workspace/data` | user + Agent 持久，30 天未访问清理 |
| `SKILL_OUTPUT_DIR` | `/workspace/outputs/{run_id}` | 单次执行独立，30 天清理 |
| `/skill` | 已批准 Skill 包 | 只读，随 Artifact 版本 |

- `SKILL_OUTPUT_DIR` 每次执行新建，两次执行互不影响；
- `SKILL_DATA_DIR` 跨 Run 共享，用于 `companies`、`logs`、`cache` 等持久数据；
- 脚本必须先判空再写；`SKILL_OUTPUT_DIR` 为空时不写文件；
- 持久数据写入用临时文件 + `os.replace`，避免半写。

默认配额（待容量验证）：

| 项目 | 默认 |
|---|---|
| 每个 user + Agent workspace | 500 MiB |
| 每个用户聚合 | 500 MiB |
| 文件数上限 | 5,000，防止海量小文件拖垮 NFS |
| 空闲 TTL | 30 天未访问自动清理 |
| 清理前通知 | MVP 不做；如需要，后续再增加提前通知 |

容量估算：1000 用户 × 500 MiB = 约 500 GiB；如果允许每人装大量 `pip/npm` 依赖，实际会更高，
必须按配额做容量规划。系统 Python/Node 解释器和系统 site-packages 只读继承基础镜像，
不复制到 workspace；`pip`/`npm` 只写 workspace 内 venv/target/node_modules，并计入配额。

磁盘硬配额须覆盖 venv、node_modules、pip/npm cache 和 inode；定时 `du` 或单文件 rlimit
不能替代硬配额，需要确认 PVC/NFS 后端能否强制实施。

清理复用 Worker 现有能力：

- 在 Worker 中新增 `workspace.maintenance` 同级循环，随进程启动，按自己的节拍运行；
- **不需要管理员在 Console 创建 Schedule，也不新增 K8s CronJob 或常驻清理服务**；
- workspace 根复用 `muad-artifacts-rwx` 的 `workspaces/` 子目录，挂载 `/mnt/muad-artifacts/workspaces`，不新增 PVC/Helm volume；清理循环只处理该前缀，不得触碰 artifact 目录；同一 PVC 容量需同时容纳 Skill 包/附件和用户 workspace，必须监控使用率并预留容量；
- Worker 默认多副本，清理前必须用 PG advisory lock 或 DB 认领 + 租约，避免多个副本
  重复删除同一目录、重复计数；
- 元数据表记录 `last_access_time`、`size_bytes`、`state`、`lease_until`；
- 有活跃 Run/租约的 workspace 跳过；删除失败保留状态、记日志和指标，不静默吞掉；
- 先标记 `DELETING`，再删文件，最后置 `DELETED`。

“自动释放”分两层：

```text
命令结束 → 回收完整后代，进程内存释放；相关文件 cache 仍可能计费
workspace 空闲超过 TTL → 清理任务删除目录
```

父进程、broker 和 TRUSTED 执行不能导入用户可写依赖。不同 Agent 不共享 workspace，
避免 A Agent 的可执行文件被 B Agent 直接使用。

### 14.5 pip/npm、系统依赖与网络边界

workspace 主要用于保存用户 Skill 执行的数据和输出，不是生产依赖的分发通道。生产依赖分两层：

1. **系统依赖基线（生产路径）**
   - 管理员把批准的 Python 包加入 `requirements.txt`/基础镜像依赖清单，Node 依赖加入镜像基线；
   - 重建 runtime/worker 镜像并滚动发布，所有副本一致；
   - 不在运行中的 Pod 上 `pip install` 到系统 site-packages（重启/换副本会丢失）；
   - 依赖随 image digest 版本化，升级失败回滚旧镜像；
   - 本期不新增依赖管理 UI，沿用现有镜像构建流程。

2. **workspace 临时安装（仅验证）**
   - 用户可在 `workspace_shell` 里 `pip install` 到 workspace venv/target、`npm install` 到 `node_modules` 做临时验证；
   - 跨 Pod/重建/清理后不保证存在，不作为 Skill 生产依赖；
   - 不写 `/usr`、不写共享缓存、不改镜像；
   - 计入 workspace 配额。

3. **网络与 Pod 一致**
   - 沙箱使用 `--share-net`，与 Pod 网络完全一致；
   - 可访问 Pod 网络内服务；Pod 有公网出口时也可访问公网；
   - 不做内网 mirror allowlist，不做 egress proxy；
   - `/v1` 受信 API 门控是硬前置，否则 Shell 可以直接打 Runtime 内部接口；
   - 内网其他服务必须依赖自身鉴权；NetworkPolicy 只作为外围补充，不能区分同 Pod 内的
     Runtime/Shell，也不能替代执行级出网限制。

4. **文件与凭据边界**
   - 只挂当前 user + Agent workspace；
   - 不挂 `/mnt/muad-artifacts`、其他用户 workspace、`/var/run`、Docker socket；
   - 不挂 Kubernetes ServiceAccount Token；
   - 不把 `INTERNAL_SERVICE_TOKEN`、DB/Redis 连接串传入沙箱；
   - `workspace_shell` 不创建 session-manager socket；平台登录态只在
     `execute_skill` / `run_skill_script` 中按 Skill `platforms` 注入；
   - 安装阶段不提供平台 session；显式提供 session 时承认脚本可读取 Cookie，不能再称为无凭据；
   - 公网源不证明包安全；受控依赖可锁版本/哈希及限制源码包，安装脚本、后续 import
     和持久启动钩子始终按任意代码处理。

5. **资源与清理**
   - 超时、输出上限、`RLIMIT_AS/CPU/NPROC/FSIZE`；
   - 回收完整后代，覆盖脱离进程组和后台进程的情况；
   - `pip/npm` cache 计入 workspace 配额；
   - 并发信号量，避免多人同时安装依赖打满磁盘/网络。

6. **审计**
   - 命令摘要、退出码、耗时、文件变更清单；
   - 不记录完整密钥/Token；
   - 管理员可查。

**不能承诺“绝对没有漏洞”**：bwrap 与 Runtime 共享内核；`pip/npm` 会执行第三方安装
脚本；供应链包本身可能恶意；网络与 Pod 一致意味着恶意包可以外传 workspace 数据。准确表述是：

> bwrap + 最小凭据 + 配额 + 审计可以降低风险，但不提供执行级网络白名单。需要限制出网时，
> 必须另行引入独立 net namespace 和强制出口代理；独立 Sandbox Pod、gVisor/Kata 增强的是
> 网络/内核边界，不能自动替代出口策略。

### 14.6 落地阶段

1. **第一阶段**：`workspace_shell` 与统一 Skill 执行器上线；bwrap、`--share-net`、
   配额/TTL（30 天）、并发、审计先落地；`/v1` 门控和流式输出是同一批硬前置。
2. **第二阶段**：用户自建 Skill 的草稿、审批、owner、下载；老 Skill 按
   `SKILL_DATA_DIR` / `SKILL_OUTPUT_DIR` / session-manager / Linux 路径做最小改造；
   管理员把生产依赖加入镜像构建清单，重建 runtime/worker。
3. **第三阶段**：按需求增加独立 Sandbox Pod、gVisor/Kata 等更强边界；不改变上层工具契约。

### 14.7 已确认口径

| 项目 | 结论 |
|---|---|
| workspace 粒度 | tenant + user + Agent |
| 配额 | 每个 workspace 500 MiB、每用户聚合 500 MiB、文件数 5,000（待容量验证） |
| TTL | `outputs` 30 天、`data` 30 天 |
| 网络 | 与 Pod 一致，`--share-net`，允许公网 |
| 安装 | 生产依赖管理员预装系统镜像；workspace 临时 pip/npm 仅验证 |
| `workspace_shell` | 所有 Agent 默认可用，无 session-manager |
| Skill 执行 | `/skill` 只读 + `/workspace` 可写 + 按 `platforms` 注入 session-manager |
| 隔离 | bwrap；更强隔离后续评估 |
| 清理 | Worker 内置维护循环，多副本认领；管理员不创建定时任务 |
