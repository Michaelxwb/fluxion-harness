# Agent ZIP 打包内置工具

- 记录日期：2026-10-03
- 状态：首版已实现并验证，尚未部署
- 类型：功能缺口
- 涉及模块：Agent Runtime 内置工具、Artifact Store、文件交付、Skill 导入
- 来源：用户提供的 Agent 回复与代码检查

## 原问题

用户要求 Agent 创建 greeting Skill。Agent 按 self-skill-creator 规范生成 `SKILL.md`、`muad.skill.json` 和 `scripts/run.mjs`，但没有 ZIP 创建工具，只能分别发送文本文件，要求用户自行整理目录并打包后上传 Console。

`write_artifact` 只写 UTF-8 文本，把文件名改成 `.zip` 不会产生真实压缩包。Console 只接受 ZIP，直接上传文件夹也不可行。

## 已实现方案

新增内置 `create_archive`，由后端使用 Python 标准库生成真实 ZIP，无需通用 Shell，也无需增加数据库字段或第三方压缩依赖。

```json
{
  "filename": "greeting.zip",
  "files": [
    {"path": "SKILL.md", "content": "..."},
    {"path": "muad.skill.json", "content": "..."},
    {"path": "scripts/run.mjs", "content": "..."}
  ]
}
```

- 只包含显式输入的文件，保留目录结构，不扫描磁盘或自动加入隐藏文件。输入使用根目录布局即可供现有 Console 导入。
- 限制：1–2000 个文件；原始内容合计与 ZIP 成品均不超过 50 MiB；路径不超过 1024 UTF-8 字节。
- 拒绝绝对路径、目录逃逸、反斜杠、控制字符、重复路径、文件与目录冲突、非文本内容与未知参数，返回明确错误。
- ZIP 保存为不可变 `AGENT_OUTPUT` 制品，媒体类型为 `application/zip`，记录文件名、大小及 SHA-256 checksum。
- 复用共享输出服务的原子写入；数据库提交失败时清理文件。使用 `outbound/{run_id}/{artifact_id}/v1` 前缀，避免 Skill 孤儿清理误删。
- 租户、Run 和会话归属从可信运行上下文获取，不接受模型提供的租户标识；后续读取和交付沿用既有权限边界。
- 工具返回 `artifact_id`、文件名、媒体类型、大小、checksum、`file_count`、最多三个路径的 `files` 预览及 `files_truncated`。清单预览限制只影响回执，ZIP 包含全部输入文件。
- 在有 Run 上下文时注册为 `ToolEffect.WRITE`；生成后可继续调用 `deliver_artifact` 发送给用户。没有交付路由时仍可生成，交付另行处理。

## 文件与模块

- `apps/agent-runtime/src/muad_agent_runtime/application/attachments/archive_tools.py`：工具契约、路径和大小校验、ZIP 构建。
- `apps/agent-runtime/src/muad_agent_runtime/application/attachments/output_service.py`：文本与二进制输出制品的共同保存服务。
- `apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py`：文本写出复用保存服务，保持原返回协议。
- `apps/agent-runtime/src/muad_agent_runtime/application/executor.py`：运行上下文内注册工具。

## 验收与回归

- [x] 三个文件生成真实 ZIP，保留 `scripts/run.mjs`，不加入未提供的文件。
- [x] 真实模型 HTTP 探针 → Runtime 工具 → PG / Artifact Store → Gateway / 企业微信协议探针 → 文件交付。
- [x] 真实 HTTP 下载的 ZIP 与渠道上传的字节相同，成员路径和内容逐项相同。
- [x] 用生产归档函数生成的合规包，经真实浏览器上传至 Console，数据库状态为 READY；测试清理业务数据及制品。
- [x] 非法路径、重复成员、文件与目录冲突、超限输入、无效 UTF-8 与未知制品引用输入明确拒绝。
- [x] 验证归属、媒体类型、大小与 checksum；其他租户不能读取该制品。
- [x] 真实数据库提交失败和存储失败覆盖；数据库失败后文件已清理。
- [x] 多文件回执保持紧凑，归档实际内容完整。
- [x] 文本写出、追加和文件交付回归测试通过。

测试入口：`tests/agent_runtime/test_archive_tools.py`、`tests/agent_runtime/test_output_artifacts.py`、既有附件与架构测试、`tests/acceptance/attachment_round_trip/test_archive_e2e.py`、浏览器 `S-05m`。

## 后续范围

首版只接受路径与 UTF-8 文本。引用已有制品、二进制输入、专用 `package_skill` 暂未实现；若增加制品引用，必须延续租户和会话 / Run 的读取边界。专用 Skill 打包校验应复用现有 manifest、入口脚本和目录布局校验。

打包、交付、导入以及用户和 Agent 授权仍是独立步骤。成功生成 ZIP 不代表 Skill 已导入或已生效。当前代码尚未部署，运行中的服务需更新 Runtime 后才能使用新工具。

## 当前工作树完整 Review 说明（2026-10-03）

本节覆盖当前相对 `HEAD` 的全部未提交改动，包括暂存、未暂存和未跟踪文件。Review 范围比 ZIP 工具本身更大：同一工作树同时包含 JavaScript Skill 支持、附件应用层整理及两份其他问题记录。以下说明描述实际代码差异；本轮只补充本文件，未修改其他实现。

### 1. JavaScript Skill 导入与清单解析

| 文件 | 改动 |
|---|---|
| `apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py` | 文件白名单新增 `.mjs`、`.cjs`；两者同时进入文本密钥扫描范围，原有 `.js` 继续支持。 |
| `packages/skill-sdk/src/muad_skill_sdk/skill_package.py` | 脚本发现由仅 `.py` 扩展到 `.py/.js/.mjs/.cjs`；新增 `declared_entrypoint`，加载包时校验可选 `muad.skill.json`。 |
| `packages/skill-sdk/src/muad_skill_sdk/__init__.py` | 导出 `declared_entrypoint`，供执行器复用同一入口校验逻辑。 |

JSON 清单必须是对象；`runtime` 若存在必须为 `script`，未提供时默认按 script 校验；`entrypoint` 若存在必须为非空相对路径，指向包内真实存在、扩展名受支持的文件。拒绝非法 JSON/UTF-8、绝对路径、Windows 盘符、反斜杠、`..`、符号链接逃逸、缺失文件及不支持的脚本扩展名。

兼容性：没有 JSON 清单或没有声明入口的历史包继续有效；`SKILL.md` frontmatter 仍决定名称、描述和执行模式，Console 的导入版本仍独立管理。没有脚本的说明型 Skill 并未被全局禁止。已有包若包含之前被忽略的非法 `muad.skill.json`，现在会在导入/加载时拒绝，review 时需要注意这一校验收紧。

### 2. 脚本执行器及 Runtime / Worker 镜像

| 文件 | 改动 |
|---|---|
| `packages/agent-core/src/muad_agent_core/skill/executor.py` | 按实际脚本扩展名选择 Python 或 Node；复用 JSON 入口声明；支持包外层单目录包装；所有选中脚本都检查包内路径、文件存在及扩展名。 |
| `apps/agent-runtime/Dockerfile` | 从 `node:24-bookworm-slim` 阶段复制 Node 可执行文件，安装 `libstdc++6`，构建时检查 `node --version`。 |
| `apps/agent-worker/Dockerfile` | 同步加入 Node，确保后台 Skill 与交互 Skill 使用相同解释器能力。 |

入口优先级：显式指定脚本 → 清单入口 → `scripts/main.py` → 按名称排序的 Python 脚本 → 按名称排序的其他受支持脚本。没有清单时保留 Python 的既有优先级。

Python 仍使用 `sys.executable -I`；JavaScript 通过受控子进程环境的 PATH 查找 Node，找不到时明确报错，子进程启动失败转为 `SkillExecutionError`。两者共用 JSON stdin、stdout/stderr、超时和取消机制。没有为 Agent 增加通用 Shell 工具；Node 支持也没有新增 OS 级沙箱、依赖自动安装或 npm 能力。新增默认脚本符号链接校验，防止默认入口绕过已有的显式路径检查。

部署影响：JavaScript Skill 需要同时更新 Runtime、Worker 镜像，并更新 Console 后端的导入校验；只更新白名单无法执行脚本。镜像使用 Node 24 主版本标签，未固定 digest。**review 结论（2026-10-03）：保持现状，不改**——仓库里所有基镜像都用 tag（`ghcr.io/astral-sh/uv:python3.12-bookworm-slim` 亦然），只钉 Node 一个的 digest 反而制造不一致；而全仓钉 digest 是**独立的运维议题**（每次安全更新都要手工改，需要一条改 digest 的流程），不属于本批改动。

### 3. Worker 保留成功脚本的纯文本输出

`apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py`：成功执行、`result is None` 且 stdout 非空时，将去除首尾空白的 stdout 保存为 `{"text": "..."}`，避免 greeting 这类只打印文字的 Skill 在后台执行后丢失结果。

该变化同时适用于 Python 和 JavaScript。结构化 JSON 结果优先，即使是空对象也不会被 stdout 日志覆盖；失败执行的 stdout 不作为成功结果。仍复用原来的任务完成投递格式，没有实现“定时原样发送固定文本”任务类型，也没有取消调度的 Skill 依赖。

### 4. Runtime 附件应用层迁移

旧平铺模块迁入 `application/attachments/`，旧路径删除，不保留兼容转发模块：

| 旧文件（均在 Runtime `application/` 下） | 新文件 |
|---|---|
| `attachment_tools.py` | `attachments/tools.py` |
| `inbound_attachments.py` | `attachments/inbound.py` |
| `artifact_reference.py` | `attachments/reference.py` |
| `artifacts.py` | `attachments/tool_results.py` |

新增 `attachments/__init__.py` 说明模块职责。同步更新 `api/artifacts.py`、`api/deps.py`、`application/context_builder.py`、`application/executor.py`、`application/run_service.py` 的导入，以及迁移模块内部对 infrastructure、metrics 和同包模块的相对导入。

`inbound.py`、`reference.py`、`tool_results.py` 的本轮迁移不改变原业务逻辑。`tools.py` 除路径迁移和格式调整外，包含上文已介绍的文本输出保存服务抽取。原本已有的入站附件转发、图片重看、大结果外置等能力不是本次新增。仓库外若有代码直接导入旧内部模块，需要同步更新。

### 5. ZIP 工具、共享输出保存及 Review 重点

新增 `attachments/archive_tools.py`、`attachments/output_service.py`，通过 `application/executor.py` 注册工具，具体输入和行为见上文。`attachments/tools.py` 的文本写出复用新服务，保留原字符串返回协议、默认文件名、MIME、大小限制和版本存储布局；追加仍走原版本路径逻辑。

建议重点检查以下实现边界：

- 制品归属从 Run 上下文获取，归档本身只接受文本，不提供服务器目录扫描、任意文件读取或已有制品引用。
- 输出服务先原子写文件再提交数据库，捕获提交阶段异常后删除文件；删除本身失败时给原异常添加说明并重新抛出，未吞掉错误。
- ZIP 压缩在线程中执行；首版将输入和 ZIP 保存在内存，50 MiB 单请求上限不代表全局并发内存限额。本次未新增压缩任务队列、并发配额或输出孤儿扫描器。
- 回执最多三个路径，JSON 编码后达到 8 KiB 时继续减少清单预览，保证后续模型能看到 `artifact_id`；ZIP 文件内容不受预览裁剪影响。
- 打包与发送分开，沿用现有交付权限、路由、幂等和下载机制。通用归档不承诺输入一定是合规 Skill，未增加自动上传、审批或授权。

### 6. 测试与 E2E 配置改动

| 文件或范围 | 改动及验证对象 |
|---|---|
| `tests/sdk/test_skill_package.py` | JavaScript 脚本发现、清单格式、runtime、入口路径、文件存在和扩展名校验。 |
| `tests/agent_core/test_skill_executor.py` | 真实 Node 的 `.js/.mjs/.cjs` 执行、stdin、环境隔离、超时、取消、非零退出、缺失 Node 和默认入口符号链接逃逸。 |
| `tests/console_skill/test_javascript_package.py` | 根目录/单目录包装的 ZIP 校验、真实制品保存及缓存、Node 执行、Worker 文本结果与交付消息；验证结构化结果优先、失败输出不当成功结果及新增扩展名仍扫描密钥。 |
| `tests/agent_runtime/test_skill_tools.py` | 新增通过 `execute_skill` / `run_skill_script` 真实执行 Node 的用例；同步新模块导入，并有格式调整及既有断言缩进调整。 |
| `tests/agent_runtime/test_artifact_results.py`、`test_attachment_tools.py`、`test_execution_audit.py`、`test_inbound_attachments.py`、`test_memory_observability.py`、`test_memory_tools.py` | 同步迁移后的模块导入，没有新增这些文件对应的业务能力。 |
| `tests/architecture/test_runtime_attachments.py` | 检查附件子包不反向依赖 API、bootstrap、执行器、Run 编排或上下文组装；相对导入也纳入检查。 |
| `tests/agent_runtime/test_archive_tools.py` | ZIP 内容与路径、非法输入/超限、工具契约、真实 PG 归属与 checksum、跨租户读取拒绝、多文件及 JSON 转义较长时的紧凑回执。 |
| `tests/agent_runtime/test_output_artifacts.py` | 真实数据库提交失败后的文件清理、缺少 Run 拒绝、存储错误显式返回。 |
| `tests/acceptance/attachment_round_trip/test_archive_e2e.py` | 模型 HTTP 探针调用工具，经真实 Runtime、PG、共享存储和 Gateway 到企业微信协议探针，再进行真实 HTTP 下载，校验字节和成员内容。 |
| `tests/e2e/create_archive_fixture.py` | 浏览器测试调用生产 ZIP 构建函数的桥接脚本，仅用于测试。 |
| `e2e/tests/skill-management/skill-management.spec.ts` | 新增 `S-05m`，使用上述桥接脚本生成 Node greeting ZIP，经真实浏览器导入并检查 READY 状态和文件存在，最后清理数据。 |
| `e2e/playwright.skill.config.ts` | 支持 `SKILL_E2E_API_PORT`、`SKILL_E2E_WEB_PORT`，默认仍为 8000/5173；同步 API URL、代理和 preview 配置，避免测试占用已有开发服务。 |

真实交付测试和浏览器导入测试是两项独立验收：前者验证实际工具产生并交付的 ZIP，后者用相同生产打包函数另建包进行导入；没有把同一次交付下载的文件继续送入浏览器。模型使用真实 HTTP 协议探针，企业微信使用真实协议 WS 探针，并非调用公网模型或生产企业微信账号。

已有执行记录（来自此前实现阶段，本轮文档补充未重新运行）：

- Runtime、架构与原附件往返联合回归：278 项通过。
- 最后追加回执边界覆盖后，归档与共享输出测试：29 项通过。与上一行有重叠，不相加为测试总数。
- 新归档交付/下载验收：1 项通过；浏览器 `S-05m`：1 项通过。
- Python 语法/typecheck、新增文件 Ruff、`git diff --check` 均通过；Console 前端 typecheck 和生产构建通过，构建仍有现有大 chunk / 混合导入提示。
- 之前 JavaScript 支持阶段已构建 Runtime 和 Worker 镜像，并分别执行用户原始 greeting ZIP；此后追加的归档代码尚未再次构建镜像或部署。
- 未宣称跑过当前工作树的全仓 Pytest，或完成生产环境验收。

可供 reviewer 复跑的主要命令（项目依赖、Node、PG、Redis 可用时）：

```bash
uv run pytest -q tests/sdk/test_skill_package.py tests/agent_core/test_skill_executor.py tests/console_skill/test_javascript_package.py tests/agent_runtime tests/architecture
uv run pytest -q tests/acceptance/attachment_round_trip/test_archive_e2e.py tests/acceptance/attachment_round_trip/test_round_trip_e2e.py
npm --prefix apps/console-platform/frontend run build
cd e2e
SKILL_E2E_API_PORT=8861 SKILL_E2E_WEB_PORT=8862 npm test -- --config=playwright.skill.config.ts --grep S-05m
```

### 7. 设计、规范及其他问题记录

| 文件 | 改动 |
|---|---|
| `docs/04-Agent-Runtime详细设计.md` | 记录附件功能子包布局、依赖方向、ZIP 工具契约及共享输出服务。 |
| `docs/06-Skill-SDK与Egress-Boundary详细设计.md` | 更新 Python / JavaScript 执行说明、可选 JSON 清单、入口优先级、Node 镜像和 Worker stdout 兼容行为。 |
| `.code-flow/specs/arch/harness-arch.md` | 补充 application 可按功能划分子包的规范与正反例，保留原技术分层和依赖方向。 |
| `.code-flow/specs/artifact/harness-skill.md` | 大结果保存模块的规范引用从旧路径更新至 `attachments/tool_results.py`，没有改变外置阈值或存储规则。 |
| `docs/issues/2026-10-03-agent-archive-tool.md` | 本问题实现、限制、验收及当前完整 Review 说明。 |
| `docs/issues/2026-10-03-audit-run-link.md` | 仅记录审计 Run 链接误跳后台任务列表的问题和建议，相关前后端实现未修复。 |
| `docs/issues/2026-10-03-schedule-custom-message.md` | 仅记录自定义消息定时任务的能力缺口、实现涉及模块及粗估，未增加任务类型、调度分支或数据库迁移。 |

当前没有数据库 migration、共享 API 契约字段或 Console 产品界面代码变更。Skill 用户范围/Agent 授权逻辑没有改动；首次提到的“所有用户是否仍需授权”未在本批次实施修改。

### 8. Review 文件快照

下列列表由补充本节时的 `git status --short --untracked-files=all` 获取。`D` 与对应新文件构成目录迁移；`A` 为已暂存新增，`AM` 为暂存后又有修改，`??` 为尚未跟踪。仅执行 `git diff` 看不到全部内容，review 应同时查看暂存区与未跟踪文件，可使用 `git diff HEAD --find-renames` 辅助识别迁移，再单独打开 `??` 文件。

```text
 M .code-flow/specs/arch/harness-arch.md
 M .code-flow/specs/artifact/harness-skill.md
 M apps/agent-runtime/Dockerfile
 M apps/agent-runtime/src/muad_agent_runtime/api/artifacts.py
 M apps/agent-runtime/src/muad_agent_runtime/api/deps.py
 D apps/agent-runtime/src/muad_agent_runtime/application/artifact_reference.py
 D apps/agent-runtime/src/muad_agent_runtime/application/artifacts.py
 D apps/agent-runtime/src/muad_agent_runtime/application/attachment_tools.py
A  apps/agent-runtime/src/muad_agent_runtime/application/attachments/__init__.py
A  apps/agent-runtime/src/muad_agent_runtime/application/attachments/inbound.py
A  apps/agent-runtime/src/muad_agent_runtime/application/attachments/reference.py
A  apps/agent-runtime/src/muad_agent_runtime/application/attachments/tool_results.py
AM apps/agent-runtime/src/muad_agent_runtime/application/attachments/tools.py
 M apps/agent-runtime/src/muad_agent_runtime/application/context_builder.py
 M apps/agent-runtime/src/muad_agent_runtime/application/executor.py
 D apps/agent-runtime/src/muad_agent_runtime/application/inbound_attachments.py
 M apps/agent-runtime/src/muad_agent_runtime/application/run_service.py
 M apps/agent-worker/Dockerfile
 M apps/agent-worker/src/muad_agent_worker/worker/execution_outcomes.py
 M apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py
 M docs/04-Agent-Runtime详细设计.md
 M docs/06-Skill-SDK与Egress-Boundary详细设计.md
AM docs/issues/2026-10-03-agent-archive-tool.md
A  docs/issues/2026-10-03-audit-run-link.md
A  docs/issues/2026-10-03-schedule-custom-message.md
 M e2e/playwright.skill.config.ts
 M e2e/tests/skill-management/skill-management.spec.ts
 M packages/agent-core/src/muad_agent_core/skill/executor.py
 M packages/skill-sdk/src/muad_skill_sdk/__init__.py
 M packages/skill-sdk/src/muad_skill_sdk/skill_package.py
 M tests/agent_core/test_skill_executor.py
 M tests/agent_runtime/test_artifact_results.py
 M tests/agent_runtime/test_attachment_tools.py
 M tests/agent_runtime/test_execution_audit.py
 M tests/agent_runtime/test_inbound_attachments.py
 M tests/agent_runtime/test_memory_observability.py
 M tests/agent_runtime/test_memory_tools.py
 M tests/agent_runtime/test_skill_tools.py
A  tests/console_skill/test_javascript_package.py
 M tests/sdk/test_skill_package.py
?? apps/agent-runtime/src/muad_agent_runtime/application/attachments/archive_tools.py
?? apps/agent-runtime/src/muad_agent_runtime/application/attachments/output_service.py
?? tests/acceptance/attachment_round_trip/test_archive_e2e.py
?? tests/agent_runtime/test_archive_tools.py
?? tests/agent_runtime/test_output_artifacts.py
?? tests/architecture/test_runtime_attachments.py
?? tests/e2e/create_archive_fixture.py
```
