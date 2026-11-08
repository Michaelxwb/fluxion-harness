# `muad.skill.json` 清单文件

每个自建 Skill 必须有 `muad.skill.json`，声明 name/author/platforms/version 等元数据。Console 上传时按本文件校验。

## 最小模板

```json
{
  "name": "<skill名>",
  "author": "<作者姓名/工号>",
  "platforms": ["<平台名>"],
  "version": "0.1.0",
  "entrypoint": "scripts/run.py"
}
```

不对接业务平台时 `platforms` 设为 `[]` 或省略该字段。

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | skill 名，必须与 SKILL.md frontmatter 的 `name` 一致，必须与草稿 key 一致 |
| `author` | ✅ | 作者署名：姓名/工号/用户名，非空字符串，用于追溯“谁写的”。不要写密钥或内部信息 |
| `platforms` | ❌ | 平台名数组（Console 里配置的自由字符串，如 `mssw`、`smoke_platform`）。声明后，`session-manager get-state --skill-name <skill>` 一次返回该 Skill 声明的全部平台凭证；任一平台未配置时调用失败（不做部分降级） |
| `version` | ✅ | 语义化版本号，如 `0.1.0` |
| `entrypoint` | ❌ | 显式声明主入口脚本（相对路径，如 `scripts/run.py`）；缺省时按 SKILL.md 约定选择 |

## 命名要求

- `name`：小写字母、数字、`-` 或 `_`，必须以字母开头，长度 ≤ 63
- `name` 必须与目录名、SKILL.md frontmatter `name` 三者一致
- **`name`（skill 名）≠ `platforms`（平台名）**，两者不要长得像，详见 [`naming-pitfalls.md`](naming-pitfalls.md)

## 禁止写入的字段

- 密钥、Token、密码
- 内部 URL
- 业务数据
- SQL

## 上传校验

Console 上传时对 `muad.skill.json` 做以下校验（任一失败返回"上传失败：<原因>"，原样转述给用户）：

- 文件存在且 JSON 合法
- `name` 合法且与 SKILL.md frontmatter `name` 一致
- `author` 存在且为非空字符串（作者署名）
- 声明的非空 `platforms` 都已在 Console 配置存在
- `entrypoint` 若声明，是合法相对路径且文件存在
