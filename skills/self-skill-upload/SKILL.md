---
name: self-skill-upload
description: 把当前用户的 Skill 草稿提交到控制台，生成待审批的 PENDING 版本。用户明确说“上传 / 提交 / 让我的 skill 生效”时调用；提交后需管理员审批通过才会生效。
---

# Skill 上传（提交草稿）

把当前用户已有的 Skill 草稿提交给管理员审批。**这是本平台唯一的用户侧提交通道**：本 skill 只做提交，不创建新草稿、不修改草稿内容（写草稿走 `self-skill-creator`）。

## 何时使用

**用户明确要求上传 / 提交 / 生效一个 skill 时调用**，包括：“上传我的 skill / 把写的 skill 生效 / 提交我写的技能”。

**不要自动提交**：用户说“写/创建/帮我写一个 skill”时，只创建并写草稿，提示可修改；**等用户明确说“上传”后再调用本 skill**。

## 前置条件

1. 草稿已存在：知道 `draft_id`（或知道草稿 `key`，用 `skill_draft`（action=`create`） 复用已有草稿并拿回 `draft_id` 与当前 `revision`）。
2. 草稿内容完整：`SKILL.md`、`muad.skill.json`（`author` 必填）、可选 `scripts/*.py` 与 `references/`。

## 执行步骤

### Step 1：拿到 draft_id 与当前 revision

- 已知 `draft_id`：调用 `skill_draft {"action": "get", "draft_id": "..."}`，取 `revision`。
- 只知道草稿 key：调用 `skill_draft {"action": "create", "key": "<key>"}`（幂等，已存在时返回同一个草稿），取 `draft_id` 和 `revision`。

**不要猜测 revision**：提交必须带草稿当前的真实 revision，否则会返回 `DRAFT_REVISION_CONFLICT`。

### Step 2：提交前审查（必须，没跑过不提交）

1. **规范审查**：用 `self-skill-review` 审当前草稿——
   - `workspace_shell` 执行 `pwd` 取工作区根；
   - `execute_skill` `{"skill_key": "self-skill-review", "input": {}, "args": ["--target", "<工作区根>/skill-drafts/<key>"]}`；
   - 把 error/warning 摘要原样告诉用户。审查**不阻断提交**，但有 error 时要说明风险并建议先修；
   - 审查之后草稿又被改过 ⇒ 旧报告失效，必须重跑；拿不到报告就**不要提交**。
2. **平台结构校验**：调用 `skill_draft {"action": "review", "draft_id": "<draft_id>"}`：
   - 有 `error`：原样转述问题（`file:line` + 原因），**不要提交**，让用户改完再试；
   - 只有 `warning`：可以继续，但把 warning 一并告知用户。

需要验证脚本能否跑通时，可先调用 `skill_draft_shell`（例如 `python -m py_compile scripts/run.py`），它不是提交流程的必要步骤。

### Step 3：提交

调用 `skill_draft`（action=`submit`）：

```
skill_draft {
  "action": "submit",
  "draft_id": "<draft_id>",
  "expected_revision": <revision>,
  "version": "<可选，如 1.0.0>",
  "default_script": "<可选，如 scripts/run.py>"
}
```

成功后返回 `{skill_id, artifact_id, review_status: "PENDING", version, checksum}`。

### Step 4：告知用户

输出固定信息：

> Skill「<key/版本>」已提交，当前状态 **待管理员审批**；审批通过后才会对 Agent 生效。需要撤回或修改，请告诉我。

## 错误处理（原样转述原因，不要吞掉）

| 错误码 | 含义 | 处理 |
|---|---|---|
| `DRAFT_REVISION_CONFLICT` | 草稿在提交前被改过 | 重新 `skill_draft`（action=`get`） 取新 revision，重试**一次**；仍冲突就告知用户草稿正在被修改 |
| `SKILL_PACKAGE_INVALID` / `SKILL_MD_MISSING` / `MANIFEST_INVALID` | 草稿格式不合法 | 转述具体原因，回到 `self-skill-creator` 修正 |
| `SKILL_KEY_EXISTS` / `SKILL_PENDING_EXISTS` | 同名 Skill 或已有待审版本 | 告知用户：等管理员处理，或联系管理员删除旧 Skill 后重提 |
| `SKILL_APPROVED_LOCKED` | 已有审批通过的 Skill | 告知用户：已审批版本锁定，需管理员解绑+软删后才能重提新版本 |
| `DRAFT_QUOTA_EXCEEDED` | 草稿超过配额（文件数/大小） | 告知用户清理草稿后重试 |
| `COMMON_FORBIDDEN` | 草稿不属于当前用户 | 不要重试；告知用户只能提交自己的草稿 |

## 查询审批状态

用户问“审批到哪了 / 通过了吗”时，调用 `skill_draft {"action": "status", "draft_id": "<draft_id>"}`，返回最近一次提交的 artifact、`review_status`、`review_note`、`reviewed_at`、`version`。

## 不要做的事

- **不要**在用户没说“上传”时提交。
- **不要**在没跑 `self-skill-review`（或报告已过期）的情况下提交。
- **不要**为了让提交通过而修改草稿内容（写草稿是 `self-skill-creator` 的职责）；review 有 error 先让用户确认。
- **不要**直接调用控制台内部接口或拼装凭据；上传只经 `skill_draft`（action=`submit`）。
- **不要**把 approval 说成“已生效”：提交后是 PENDING，只有管理员审批通过才生效。
