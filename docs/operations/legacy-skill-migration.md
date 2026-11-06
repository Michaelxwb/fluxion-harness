# 老 Skill 迁移手册（用户自建 Skill 沙箱）

日期：2026-11-07  
状态：自动化兼容验收已完成（S-13/S-14 的可重复本地部分）；内网真实 mssp 平台验收仍为
`manual_pending`，需在目标环境用原始 Skill 包重跑并回填证据。  
适用范围：`login-mssp-platform`、`mss-report-skill` 及同形态的历史 Skill。

---

## 0. 结论

旧 Skill 不需要适配新的沙箱协议：

1. **登录态**统一从 `session-manager get-state` 获取，不再维护 `cookies.txt` 或本地会话文件；
2. **运行数据**写 `SKILL_DATA_DIR`，**本次产物**写 `SKILL_OUTPUT_DIR`，Skill 包本身只读；
3. **平台地址**只认 `state.platforms.<key>.requestProfile.baseUrl`，不读历史硬编码配置；
4. 调度与 webhook 由平台承担，Skill 内不再维护计划任务、后台循环或硬编码密钥；
5. `SKILL.md` 的运行方式保持 `python scripts/run.py`（或历史入口），`-E -s -B` 由平台注入。

仓库内没有内网真实老 Skill 源码，自动化验收使用
`tests/acceptance/user_skill_sandbox/fixtures/legacy_skills/` 下按真实形态构造的包。
真实内网仍需用**原始包**重跑 S-13/S-14，夹具通过不等于真实平台通过。

---

## 1. 老 Skill 兼容契约

| 老 Skill 现状 | 新契约 | 说明 |
|---|---|---|
| `cache` / `logs` / `companies` 写在 Skill 包根目录 | `SKILL_DATA_DIR` | 包只读；持久数据迁到 user + Agent data 目录 |
| 报告/导出写 Skill 包根目录或 `/tmp` | `SKILL_OUTPUT_DIR` | 每次执行独立；脚本先判空，空时不写 |
| `cookies.txt` / 硬编码 Cookie | `session-manager get-state` | 从 `sessionStateFile` 读取 cookies |
| `common_config.json` 写死 base_url | `requestProfile.baseUrl` | 平台地址以当前配置为准 |
| Windows 路径 / `Get-Content` / 盘符 | POSIX 路径 / Linux 命令 | 沙箱为 Linux |
| `schtasks` / 常驻 daemon | 平台 Schedule | 不在 Skill 内维护 Windows 计划任务或后台循环 |
| `webhook_config.json` 硬编码 key | 平台交付 / Secret 配置 | 密钥不进 Skill 包、日志或快照 |
| `sys.path.insert(0, dirname(__file__))` | 可保留，也可删除 | `-E -s -B` 保留入口脚本目录在 `sys.path[0]` |
| `SKILL.md` 写 `python scripts/run.py` | 保持不变 | 框架注入 `-E -s -B`，不要求作者写参数 |

`session-manager get-state` 的旧 stdout 契约：

```json
{
  "version": 1,
  "status": "ready",
  "skillName": "login-mssp-platform",
  "sessionStateFile": "/tmp/fx-session-xxx/state.json",
  "platforms": [
    {"platform": "mssp", "source": "session", "expiresAt": "2026-11-06T12:00:00+08:00"}
  ]
}
```

- `platforms` list 只为旧 Skill 的 stdout 解析兼容；`expiresAt` 必须存在。
- `sessionStateFile` 内部仍是 `platforms.<key>.cookies` / `requestProfile` dict。
- state 文件是执行专属只读文件，执行结束删除；脚本复制到 workspace/output 的 Cookie 副本不会随之删除。
- 禁止把 Cookie/Token 原文写入日志、报告、审计或证据。

---

## 2. 最小改造步骤

### 2.1 运行数据与产物目录

```python
# ❌ 旧写法：写包根目录，包在沙箱里只读会直接失败
with open("cache/last-run.json", "w") as handle:
    handle.write(now)

# ✅ 新写法：数据进 SKILL_DATA_DIR，报告进 SKILL_OUTPUT_DIR
import os
from pathlib import Path

data_dir = Path(os.environ["SKILL_DATA_DIR"])
output_dir = Path(os.environ.get("SKILL_OUTPUT_DIR", ""))

(data_dir / "cache").mkdir(parents=True, exist_ok=True)
(data_dir / "cache" / "last-run.json").write_text(now)

if output_dir:
    (output_dir / "reports").mkdir(parents=True, exist_ok=True)
    (output_dir / "reports" / "report.json").write_text(report)
```

约定：

- `SKILL_DATA_DIR` 跨执行持久，按 tenant + user + Agent 隔离；`cache` / `logs` / `companies` 都放这里。
- `SKILL_OUTPUT_DIR` 每次执行独立；执行器会扫描并登记为本次执行的产物。
- 本地直接调试时 `SKILL_OUTPUT_DIR` 可能为空：脚本必须判空，不能回退写当前目录或包根目录。

### 2.2 登录态：session-manager 替代 cookies.txt

保持旧入口不变，只把本地会话读取替换为：

```python
import json
import subprocess

info = json.loads(
    subprocess.check_output(
        ["session-manager", "get-state", "--skill-name", "login-mssp-platform"],
        text=True,
    )
)
state = json.load(open(info["sessionStateFile"], encoding="utf-8"))
platform = state["platforms"]["mssp"]
cookies = platform["cookies"]                 # 可读，但不要打印值
base_url = platform["requestProfile"]["baseUrl"]
expires_at = platform["expiresAt"]
```

- 不再需要 `cookies.txt`；把历史文件从包中删除。
- 不要自行拼 Cookie header 后写入日志；需要时按 `requestProfile.csrf` / `staticHeaders` 使用。
- 未授权平台会返回 `PLATFORM_SESSION_REQUIRED`；Redis/session store 不可用返回
  `PLATFORM_SESSION_STORE_UNAVAILABLE`。

### 2.3 平台地址：requestProfile.baseUrl

```python
# ❌ 旧写法：包内配置写死
base_url = json.load(open("common_config.json"))["baseUrl"]

# ✅ 新写法：平台地址来自当前会话
base_url = state["platforms"]["mssp"]["requestProfile"]["baseUrl"]
```

`common_config.json` 可以保留非端点配置（报告名称、业务开关等），但其中的旧 `baseUrl`
必须删除或明确标注不再使用。切换环境（测试/生产 mssp）不要求重新打包 Skill。

### 2.4 POSIX 路径与命令

- 盘符、反斜杠、`Get-Content`、`schtasks`、`.bat` 启动器都不能进入沙箱路径；
- 文件 API 一律用 `pathlib.Path` / POSIX 路径；
- 外部命令使用 Linux 可用命令（`cat`、`grep`、`python`、`node`），不要依赖 Windows 专属工具；
- 解释器参数由平台注入（Python 为 `-E -s -B`），`SKILL.md` 仍写 `python scripts/run.py`。

### 2.5 调度与 webhook

- 删除 `schtasks`、Windows 服务、常驻 daemon、`while True` 后台循环；改由平台 Schedule 触发；
- 删除硬编码 webhook URL/key 与 `webhook_config.json` 中的密钥；改由平台交付通道/Secret 配置；
- Skill 包、日志、执行快照中不得出现 webhook key；需要回调时由平台侧配置注入。

---

## 3. 可重复演示命令

### 3.1 前置条件

- Docker Desktop 可用（macOS 没有 bwrap，测试把真实 `bwrap` 放进 Linux 探针容器）；
- 基础镜像 `fx-sandbox-test:local` 已存在，或设置 `MUAD_SKILL_EXECUTOR_BASE_IMAGE`；
- `.env` 的 `REDIS_URL` 可用（真实 Redis，用于 session-manager 会话）；
- 仓库根目录执行，且 `e2e/node_modules` 可用（worktree 首次 collect 需要）。

### 3.2 两个 Skill 的自动化验收

```bash
# S-13 + S-14 全跑
uv run pytest -q tests/acceptance/user_skill_sandbox/test_legacy_skills.py

# 只跑 S-13（未修改的 login-mssp-platform）
uv run pytest -q tests/acceptance/user_skill_sandbox/test_legacy_skills.py -k s13

# 只跑 S-14（最小改造的 mss-report-skill）
uv run pytest -q tests/acceptance/user_skill_sandbox/test_legacy_skills.py -k s14
```

真实边界（未 mock）：真实 `SandboxedSkillExecutor` → 真实 `BwrapRunner`/`bwrap`（Docker
探针内）→ 真实 `session-manager` broker → 真实 Redis + Console 凭据解析响应记录桩 +
真实 mssp adapter 登录 → 夹具 Skill 包。探针通过 `host.docker.internal` 与宿主 Redis/
Console 桩通信，不使用假 bwrap、假文件系统或假登录链。

夹具位置：

```text
tests/acceptance/user_skill_sandbox/fixtures/legacy_skills/
├── login-mssp-platform/      # 旧形态：只调 session-manager，不改代码
│   ├── SKILL.md
│   └── scripts/main.py
└── mss-report-skill/         # 最小改造：data/output 目录 + requestProfile
    ├── SKILL.md
    ├── common_config.json    # 保留旧 baseUrl，仅用于证明运行时不再读取
    └── scripts/run.py
```

---

## 4. 内网真实 mssp 平台人工验收（S-13 / S-14）

### 4.1 为什么是 manual_pending

- S-13/S-14 的真实边界包含**内网真实 mssp 平台凭据与受保护业务接口**；仓库与本地环境没有
  这些凭据，不能用测试平台桩冒充真实平台。
- 自动化用例已经覆盖兼容契约和最小改造契约；真实平台仍需在目标环境用原始包重跑，确认
  平台侧登录、Cookie、baseUrl、调度和交付链路。

### 4.2 S-13：`login-mssp-platform` 不改代码

前置：真实 mssp 平台配置与用户凭据已在 Console 配好；Skill 已导入/批准并绑定测试 Agent。

1. 记录原始包 `sha256sum login-mssp-platform.zip`，确认上传的包与生产包一致（不做任何修改）。
2. 通过 Console 上传并批准该包，绑定测试 Agent；记录 `skill_id` / `artifact_id`。
3. 用真实 mssp 用户完成一次平台登录（Console 平台设置/凭据页），确认会话可用。
4. 在 Console 对话中让该 Agent 执行 `login-mssp-platform`（`execute_skill`），或触发既有
   Run；记录 `run_id` / `execution_id`。
5. 从执行结果/运行日志读取 stdout JSON，逐项核对：
   - 退出码为 `0`；
   - `platforms` list 含 `{"platform": "mssp", "source": "session", "expiresAt": "..."}`；
   - `sessionStateFile` 位于 `/tmp/fx-session/` 且执行结束后已删除；
   - cookies 可读（只记录名称/数量/布尔值，不记录值）；
   - `baseUrl` 等于当前 mssp 平台配置的地址（`requestProfile.baseUrl`），不是历史硬编码值。
6. 记录证据（见 §4.4），把 S-13 状态从 `manual_pending` 回填为 `verified`。

### 4.3 S-14：`mss-report-skill` 最小改造

前置：按 §2 完成 `SKILL_DATA_DIR` / `SKILL_OUTPUT_DIR` / session-manager / baseUrl / POSIX /
调度 / webhook 改造；重新打包并上传/批准。

1. 记录改造后包的 `sha256sum`，并保存包树 checksum（执行前后必须一致）。
2. 通过 Console 上传/批准并绑定测试 Agent；记录 `skill_id` / `artifact_id`。
3. 连续执行两次（验证 data 持久、output 每次独立）：
   - 报告出现在 `SKILL_OUTPUT_DIR`（执行产物列表/附件可见）；
   - `cache` / `logs` / `companies` 只出现在 `SKILL_DATA_DIR`；
   - Skill 包根目录没有新增 `cache` / `logs` / `companies` / `reports`；
   - 报告中的平台地址来自 `requestProfile.baseUrl`，旧 `common_config.json` 的 `baseUrl` 不生效；
   - 第二次执行能读到第一次写入的 data（如 `cache/last-run.json`）。
4. 检查执行产物登记：报告在本次执行的 outputs/产物列表中，checksum 与文件内容一致。
5. 记录证据（见 §4.4），把 S-14 状态从 `manual_pending` 回填为 `verified`。

### 4.4 证据记录格式

每次验收在 `.code-flow/tasks/<需求目录>/` 对应任务文件的 Acceptance Evidence 中登记，并附
脱敏后的执行记录。建议格式：

| 字段 | 示例 |
|---|---|
| 场景 | S-13 / S-14 |
| 环境 | 内网 K8s 集群名 / 命名空间 / 镜像 digest |
| 执行时间 | 2026-11-07T15:04:05+08:00 |
| 操作人 | 姓名/工号 |
| Skill 包 | 文件名 + `sha256:...`（执行前后一致） |
| 包来源 | 生产原始包 / 最小改造包（附 diff 摘要） |
| tenant / skill / artifact | `...` / `...`（UUID） |
| run_id / execution_id | `...` |
| 平台 | mssp 平台实例 key + 配置版本（不写凭据） |
| 命令 | `execute_skill(skill_key=...)` 或触发路径 |
| 退出码 | `0` |
| stdout 关键字段 | `platforms`、`expiresAt`、`sessionStateFile` 路径、`baseUrl`（脱敏） |
| Cookie | 只记录名称列表/数量/`readable=true`，**不记录值** |
| state 文件 | 执行结束后 `deleted=true` |
| 产物 | 报告相对路径 + `sha256:...` + 是否登记 |
| 数据目录 | `cache/logs/companies` 相对路径列表 |
| 包树 checksum | before / after（相等） |
| 结论 | `verified` / `failed` + 失败原因/错误码 |

证据中禁止出现：Cookie/Token 原文、Authorization header、webhook key、完整内网 URL 的
敏感参数。stdout 可保留结构字段，但脱敏值（如 `baseUrl` 只保留 scheme+host）。

---

## 5. 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `PLATFORM_SESSION_REQUIRED` | 平台未授权 / Skill 未声明平台 | 检查 `SKILL.md` 的 `platforms`、Console 授权与凭据 |
| `PLATFORM_SESSION_STORE_UNAVAILABLE` | Redis 不可用 | 检查 `REDIS_URL` 与 session store 健康 |
| `SANDBOX_UNAVAILABLE` | 无 bwrap / 非 Linux 且未启用 dev-unsafe | 使用带 bwrap 的镜像；生产 fail closed，不要回退宿主执行 |
| 写包根目录失败（`EROFS`/`PermissionError`） | 仍在写 Skill 包 | 按 §2.1 改到 `SKILL_DATA_DIR` / `SKILL_OUTPUT_DIR` |
| 报告未登记 | 写到了 `/tmp` 或包根目录 | 只写 `SKILL_OUTPUT_DIR`，并确认目录非空 |
| `baseUrl` 是旧环境地址 | 仍读 `common_config.json` | 改用 `requestProfile.baseUrl` |
| 真实平台 401/403 | Cookie 过期/平台会话失效 | 重新登录；不要复制旧 `cookies.txt` |

---

## 6. 验收检查清单

- [ ] Skill 包内没有 `cookies.txt`、硬编码 Cookie、webhook key；
- [ ] 运行数据只写 `SKILL_DATA_DIR`，报告只写 `SKILL_OUTPUT_DIR`；
- [ ] 平台地址来自 `requestProfile.baseUrl`；
- [ ] 路径/命令为 POSIX，可在 Linux 沙箱执行；
- [ ] 无 `schtasks`/daemon/后台循环，调度在平台配置；
- [ ] `SKILL.md` 仍写 `python scripts/run.py`（或历史入口），未要求作者加解释器参数；
- [ ] 本地自动化 `uv run pytest -q tests/acceptance/user_skill_sandbox/test_legacy_skills.py` 通过；
- [ ] 内网原始包 S-13/S-14 已按 §4 重跑并回填证据（否则保持 `manual_pending`）。
