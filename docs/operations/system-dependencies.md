# Skill 系统依赖基线（镜像预装）

日期：2026-11-06  
范围：用户自建 Skill / `workspace_shell` 运行所需的 Python、Node 第三方依赖。  
设计口径：`user-skill-sandbox.backend.design.md` §3.5「系统依赖基线」、§4.1「部署架构」；PRD FEAT-18。

## 0. 结论

生产依赖**只通过 runtime/worker 镜像构建预装**，与镜像一起版本化、滚动发布和回滚：

| 依赖类型 | 唯一生产入口 | 安装位置 |
|---|---|---|
| Python | `build/docker-build/skill-deps/python-requirements.txt`（精确 `name==version`） | 系统 site-packages（基础镜像 `/opt/python3.12`） |
| Node | `build/docker-build/skill-deps/node/package.json` + `package-lock.json`（精确版本） | `/usr/local/lib/node`（Node legacy `$PREFIX/lib/node` 查找路径） |
| 应用自身依赖 | 仓库根 `requirements.txt`（由 `uv.lock` 导出） | 系统 site-packages |

任一 runtime/worker 镜像都执行同一个 `build/docker-build/skill-deps/install.sh`；镜像 label
`org.muad.skill-deps.sha256` 记录基线内容 hash，构建脚本同时写出 `image_id.txt` /
`skill-deps.sha256` /（推送时）`image_digest.txt`。

**禁止**：

- **不在运行中的 Pod** 上执行 `kubectl exec ... pip install` / `npm install -g` / 修改
  `/opt/python3.12/lib/python3.12/site-packages` 或 `/usr/local/lib/node`；
- 把依赖装进容器可写层、共享缓存或镜像以外的路径，指望重启/换副本后仍然存在；
- 把用户 workspace 的临时安装当作 Skill 的生产依赖。

`workspace` 内的 `pip`/`npm` 临时安装只用于验证：只写 workspace 的 venv/target/node_modules，
计入 workspace 配额，可被 TTL 清理；跨 Pod、Pod 重建或清理后不保证存在。

本期不新增依赖管理 UI；管理员沿用现有镜像构建流程。

## 1. 基线文件

```text
build/docker-build/skill-deps/
├── baseline.json                 # 批准清单 + 模块映射（版本必须与下面两个文件一致）
├── python-requirements.txt       # Python 精确版本（pip 输入）
├── node/
│   ├── package.json              # Node 精确版本
│   └── package-lock.json         # npm ci 锁文件
└── install.sh                    # runtime/worker 镜像构建期唯一安装入口
```

当前初始基线：

| 生态 | 包 | 版本 | 用途 |
|---|---|---|---|
| Python | `tabulate` | 0.9.0 | 报告/表格文本输出 |
| Node | `uuid` | 14.0.3 | JS Skill 生成稳定标识 |

`baseline.json` 是人工可读的批准清单；`python-requirements.txt` 与 `node/package.json`
是构建输入。`scripts/skill_system_deps.py validate` 会拒绝三者不一致、非精确版本、
Dockerfile 未接入安装脚本或运行用户下仍出现安装命令的构建。

## 2. 新增 / 升级依赖（管理员流程）

1. **申请与审批**：记录包名、版本、用途、申请人、审批人；批准后才修改基线。
2. **改 Python 基线**：编辑 `build/docker-build/skill-deps/python-requirements.txt`
   和 `baseline.json`（`distribution`/`module`/`version` 三处一致）。应用自身依赖仍走
   `pyproject.toml` + `uv lock` + `build/docker-build/export-requirements.sh`，不要混入。
3. **改 Node 基线**：
   ```bash
   cd build/docker-build/skill-deps/node
   npm install --package-lock-only --save-exact <package>@<version>
   ```
   同步 `baseline.json` 的 `package`/`module`/`version`。
4. **本地机检**：
   ```bash
   uv run python scripts/skill_system_deps.py validate --json
   uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_48.py \
     tests/acceptance/user_skill_sandbox/test_e_17.py
   ```
5. **重建两个镜像**（同一条现有流水线；不新增镜像流水线）：
   ```bash
   ./build/docker-build/build-image.sh agent-runtime
   ./build/docker-build/build-image.sh agent-worker
   ```
   内网构建默认 `PIP_INDEX_URL` / `NPM_REGISTRY` 指向内网源；离线环境用构建机缓存或
   内网镜像源，不要在生产 Pod 上补装。
6. **记录 image digest 与变更记录**：
   ```bash
   uv run python scripts/skill_system_deps.py record \
     --image docker.sangfor.com/mss-dev/muad-agent-runtime:<tag> \
     --service agent-runtime \
     --output /tmp/muad-image-agent-runtime/system-deps-record.json
   uv run python scripts/skill_system_deps.py record \
     --image docker.sangfor.com/mss-dev/muad-agent-worker:<tag> \
     --service agent-worker \
     --output /tmp/muad-image-agent-worker/system-deps-record.json
   uv run python scripts/skill_system_deps.py verify-records \
     --runtime /tmp/muad-image-agent-runtime/system-deps-record.json \
     --worker /tmp/muad-image-agent-worker/system-deps-record.json
   uv run python scripts/skill_system_deps.py change \
     --record /tmp/muad-image-agent-runtime/system-deps-record.json \
     --applicant <申请人> --approver <审批人> \
     --log build/docker-build/skill-deps/change-log.jsonl
   ```
   记录包含：服务、镜像引用、`image_id`（OCI `sha256:` digest）、repo digest（推送后）、
   base image、基线 hash、Python/Node 实际安装版本、记录时间；变更记录额外包含包名、
   版本、申请人、审批人、镜像版本。
7. **发布 / 回滚**：按 `deploy/k8s/README.md` canary 一个 runtime/worker 副本；确认两个
   副本的 `imageID` 与记录一致、基线 import/require 通过后再全量。升级失败回滚到旧镜像
   tag/digest，不在 Pod 上做降级安装。

## 3. 验证与审计

### 3.1 构建期

- `scripts/skill_system_deps.py validate --json`：基线文件、Dockerfile、构建脚本一致性；
- `install.sh` 只在 Dockerfile 的 root 构建阶段执行，最终 `USER 10001:10001` 之后没有
  `pip install` / `npm install` / 包管理器命令；
- 镜像 label `org.muad.skill-deps.sha256` = 基线内容 hash。

### 3.2 真实镜像 + bwrap

```bash
uv run pytest -q tests/acceptance/user_skill_sandbox/test_s_48.py
uv run pytest -q tests/acceptance/user_skill_sandbox/test_e_17.py
```

S-48 用真实镜像 + 真实 bwrap 验证：Skill 只读挂载、无 workspace 安装时仍能
`import tabulate` / `require('uuid')`；运行用户对系统 site-packages 和 Node 全局目录只读；
record/verify/change 能产出并校验 image digest 与变更记录。

E-17 用两个真实容器模拟 Pod A/Pod B：Pod A 把测试包 `pip install --target` 到 workspace，
Pod B 空 workspace 执行同一 Skill 时 `ModuleNotFoundError` 可见；系统基线仍可用；删除
workspace（TTL 清理的最终效果）后临时依赖不可恢复。

### 3.3 运行时 / 多副本

```bash
# 只读探针，不修改 Pod
kubectl -n muad exec deploy/muad-agent-runtime -- python -c "import tabulate; print(tabulate.__version__)"
kubectl -n muad exec deploy/muad-agent-runtime -- node -e "console.log(require('uuid').version)"

# 所有副本的实际 imageID 必须与记录一致
kubectl -n muad get pods -l app=muad-agent-runtime \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}'
kubectl -n muad get pods -l app=muad-agent-worker \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}'
```

限制（诚实边界）：

- 依赖版本由镜像 digest 保证一致，不依赖 Pod 本地缓存；同一镜像的所有副本在文件系统层面相同。
- workspace 配额/TTL 的运行时实现在 TASK-005/TASK-006；本任务只保证临时安装发生在
  workspace 内、计入配额范围，并验证清理后不可复现。
- 内网构建机若不能访问 npm/pip 源，应由构建机缓存或内网 mirror 提供；**不允许**用
  `kubectl exec` 在生产 Pod 上补装来“修好”构建。

## 4. 变更记录格式

`build/docker-build/skill-deps/change-log.jsonl` 每行一条 JSON：

```json
{
  "recorded_at": "2026-11-06T12:00:00+00:00",
  "service": "agent-runtime",
  "image": "docker.sangfor.com/mss-dev/muad-agent-runtime:<tag>",
  "image_digest": "sha256:...",
  "baseline_sha256": "<64 hex>",
  "packages": [
    {"name": "tabulate", "version": "0.9.0", "ecosystem": "python"},
    {"name": "uuid", "version": "14.0.3", "ecosystem": "node"}
  ],
  "applicant": "<申请人>",
  "approver": "<审批人>"
}
```

变更记录与镜像 digest 一起保留，用于回答“某个版本装了什么、谁申请、谁批准、对应哪个镜像”。
