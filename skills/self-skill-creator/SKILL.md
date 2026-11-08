---
name: self-skill-creator
description: 指导用户写一个符合本平台格式的自建 Skill。用户说“帮我写一个 skill / 创建一个 skill / 怎么写 skill”时调用。草稿写在平台草稿目录（`skill_draft` 工具），等用户明确说“上传”后再走 self-skill-upload。
---

# 自建 Skill 创作

指导用户在这个平台的草稿目录里写一个 Skill。**只写草稿，不提交**——提交走 `self-skill-upload`。

自建 Skill 统一使用 **Python**：脚本放 `scripts/*.py`，入口默认 `scripts/run.py`。不要生成其它语言的脚本。

## 何时使用

- 用户说“帮我写一个 skill / 创建一个 skill / 怎么写 skill / 我想要一个能 X 的 skill”时调用。
- 用户已有一份草稿想确认格式是否合规时也调用。

**不要自动提交**：写完草稿后只提示用户“草稿已写好，确认无误后告诉我上传”，**等用户明确说“上传”再调用 `self-skill-upload`**。

## 草稿工具（平台提供）

| 工具 | 用途 |
|---|---|
| `skill_draft`（action=`create`） | 按 `key` 创建（或复用）当前用户的草稿，返回 `draft_id` 与 `revision` |
| `skill_draft`（action=`write`） | 写一个文件；必须带当前 `expected_revision`（revision CAS） |
| `skill_draft`（action=`read`） | 读文件或列目录 |
| `skill_draft`（action=`delete`） | 删文件；不带 `path` 时删整个草稿 |
| `skill_draft`（action=`review`） | 只读确定性校验，返回 pass/warn/error 明细 |
| `skill_draft_shell` | 在当前草稿的沙箱副本里执行一条命令（验证脚本） |
| `skill_draft`（action=`status`） | 查看最近一次提交的 artifact 与审批状态 |
| `skill_draft`（action=`submit`） | 打包并提交，生成 PENDING artifact 等管理员审批 |

草稿文件是 UTF-8 文本；二进制文件不要通过 `skill_draft`（action=`write`） 写。

## 格式硬约束

每个自建 Skill 都要先确认这几件事，缺一项都要明确补上或明确不需要：

1. **`muad.skill.json` 清单文件**：声明 name/author/platforms/version 等元数据。`author` 必填，写作者姓名/工号/用户名，便于追溯。详见 [`references/skill-manifest.md`](references/skill-manifest.md)。
2. **对接平台 → 先取登录态，再用 requests 发请求**：脚本先用 `session-manager get-state --skill-name <skill>` 获取登录态，再从返回的 `sessionStateFile` 读取 `platforms.<平台名>.cookies` 和 `requestProfile.baseUrl`，用 `requests` 发请求。平台名按字面量写，必须与 `muad.skill.json` 的 `platforms` 一致。详见 [`references/http-headers.md`](references/http-headers.md) 与 [`references/session-manager.md`](references/session-manager.md)。
3. **有输出文件 → 接入 `SKILL_OUTPUT_DIR`**：Skill 若要写文件（报告、临时结果），必须写到环境变量 `SKILL_OUTPUT_DIR` 指向的目录，**不要写 Skill 根目录（只读）或 `/tmp`（不隔离不持久）**。详见 [`references/skill-output-dir.md`](references/skill-output-dir.md)。

## 创作流程

### Step 1：确认 skill 的形态与边界

先问用户这几件事（一次问完，不要挤牙膏）：

1. **作者署名**：必填，写姓名/工号/用户名，用于后续追溯。
2. **skill 名**：小写字母、数字、`-` 或 `_`，必须以字母开头，长度 ≤ 63。建议与平台名**显式区分**（见 [`references/naming-pitfalls.md`](references/naming-pitfalls.md)）。
3. **是否要对接业务平台**：要的话问平台名（Console 里配置的自由字符串）；可能对接多个平台，每个平台在状态文件里各取各的 section。
4. **是否有输出文件**：要写报告/临时文件的话，确认文件名与格式。
5. **是否长耗时**：脚本预计 >10s 或分多个阶段时，在 `SKILL.md` frontmatter 声明 `execution: ASYNC`（后台任务）；同步执行用默认值。

实现语言固定 Python，不需要询问。

### Step 2：创建草稿

调用 `skill_draft`（action=`create`），`key` 用 skill 名（小写、可复用）。记住返回的 `draft_id` 和 `revision`——后续每次写入都要带这个 revision。

```
skill_draft {"action": "create", "key": "<skill名>"}
```

### Step 3：写 `muad.skill.json`

最小模板：

```json
{
  "name": "<skill名>",
  "author": "<作者姓名/工号>",
  "platforms": ["<平台名>"],
  "version": "0.1.0",
  "entrypoint": "scripts/run.py"
}
```

字段含义详见 [`references/skill-manifest.md`](references/skill-manifest.md)。**不对接平台时 `platforms` 设为 `[]` 或省略**。

用 `skill_draft`（action=`write`） 写入：

```
skill_draft {"action": "write", "draft_id": "<draft_id>", "path": "muad.skill.json", "content": "<json>", "expected_revision": <revision>}
```

### Step 4：写 `SKILL.md`

frontmatter 只写 `name`、`description`，需要后台执行时再加 `execution: ASYNC`：

```yaml
---
name: <skill名>
description: "MANDATORY before calling <工具/场景>. Trigger on: <触发场景>。"
---
```

body 用祈使句写指令，遵循“渐进式披露”——核心流程放 SKILL.md，大段规范放 `references/`。详见 [`references/skill-md.md`](references/skill-md.md)。

### Step 5：写 Python 脚本（按需）

若 Step 1 确认要对接平台或有确定性逻辑，在 `scripts/` 下写 Python 脚本（默认入口 `scripts/run.py`）。脚本里三件事必须做对：

1. **登录态与业务请求拆分**：先调用 `session-manager get-state --skill-name <skill>`（`<skill>` 硬编码且等于 manifest `name`），从返回的 `sessionStateFile` 读取平台 `cookies` 与 `requestProfile.baseUrl`，再用 `requests` 发请求。平台名按字面量写，必须与 `muad.skill.json` 的 `platforms` 一致（不是 skill 名）。不要用 `subprocess` 调 `curl`/`wget` 发业务请求。
2. **写文件路径用 `os.environ.get("SKILL_OUTPUT_DIR")`**；为空时不要写文件。
3. **失败必须写 stderr 并 exit 非 0**（fail loud），成功 stdout 只保留一个机器可读结果。

每个文件都用 `skill_draft`（action=`write`） 写；每次 write 成功后 revision 会递增，下一次写入必须用新 revision。

### Step 6：自检

- [ ] `muad.skill.json` 合法，`name` 与 SKILL.md frontmatter `name`、草稿 key 一致
- [ ] `author` 非空且可追溯
- [ ] `entrypoint` 若声明，指向 `scripts/*.py`
- [ ] 若声明了 `platforms`：脚本调用 `session-manager get-state`，从状态文件取 cookies/baseUrl；平台名与 `platforms` 一致
- [ ] 脚本用 `requests` 发业务请求；没有手工拼装凭据/内部 URL
- [ ] 写文件只写 `SKILL_OUTPUT_DIR`，不写 Skill 根目录或 `/tmp`
- [ ] 失败路径写 stderr 且 exit 非 0；成功 stdout 只有一个机器可读结果
- [ ] SKILL.md frontmatter 只有 `name`/`description`（或加 `execution`），`description` 不含密钥/内部 URL/业务数据

然后按顺序做两件事：

1. `skill_draft`（action=`review`） 做平台结构校验；验证脚本能否跑通必须调用 `skill_draft_shell`（例如 `python -m py_compile scripts/run.py`、`python scripts/run.py <args>`），**不要用 `workspace_shell` 在工作区的草稿目录里直接运行**——那是活草稿，写入会绕过 revision CAS。
2. 按 `self-skill-review` 做规范审查：先用 `workspace_shell` 执行 `pwd` 取工作区根，再 `execute_skill` 调用 `self-skill-review`，`argv` 传 `["--target", "<工作区根>/skill-drafts/<key>"]`，然后按该 Skill 的语义清单逐项核对。把 error/warning 摘要**原文交给用户**；有 error 先修，修完重审。

### Step 7：交付草稿，等用户上传

写完后告知用户（把 `self-skill-review` 的 error/warning 数量与要点一并列出；没有跑审查就不要说“已通过审查”）：

> 草稿已写好并通过自检与审查（N 个 error / M 个 warning，明细见上）。确认无误后告诉我“上传”，我会提交到控制台审批。

**不要主动提交**。上传时机由用户决定。

## 不要做的事

- **不要**直接编辑平台托管的 Skill 包（只读）。
- **不要**在草稿阶段就调用 `self-skill-upload`——那是用户明确说“上传”后才走的流程。
- **不要**写 README.md / INSTALLATION_GUIDE.md / CHANGELOG.md 等辅助文档，草稿只放 AI 干活必需的文件。
- **不要**在 `muad.skill.json` 里写密钥、Token、内部 URL。
- **不要**生成非 Python 脚本或手工拼装平台请求头。

## 参考

- [`references/http-headers.md`](references/http-headers.md) requests 请求与请求头口径
- [`references/session-manager.md`](references/session-manager.md) 平台登录态由 `session-manager get-state` 获取
- [`references/skill-manifest.md`](references/skill-manifest.md) `muad.skill.json` 字段
- [`references/skill-md.md`](references/skill-md.md) SKILL.md 写法
- [`references/skill-output-dir.md`](references/skill-output-dir.md) 输出目录契约
- [`references/naming-pitfalls.md`](references/naming-pitfalls.md) 命名陷阱
- 上传流程见 `self-skill-upload` skill
