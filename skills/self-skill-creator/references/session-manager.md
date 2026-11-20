# 平台登录态：Skill 先调 session-manager get-state

Skill 访问受保护业务平台时，自己调用 `session-manager get-state --skill-name <skill>` 获取登录态文件，然后直接读该文件发 `requests` 请求。平台不代发业务请求，登录态只由 Skill 自行读取。

## 列 session 与取登录态

- 用户问“有哪些平台 / 平台 session 状态”时，用 `session-manager list-platforms`；它会逐平台**实际检查登录态**，返回 `available`、有效期或失败原因，不再只表示“有凭据配置”。
- 执行 Skill 时不要先跑 `list-platforms`；直接 `get-state --skill-name <skill>`，避免为无关平台触发登录、续期和预算消耗。

## 脚本写法

```python
import json
import subprocess

import requests

SKILL_NAME = "my-skill"  # 必须与 muad.skill.json 的 name 一致
PLATFORM = "smoke_platform"


def resolve_state_file():
    completed = subprocess.run(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        raise RuntimeError(
            "session-manager get-state failed: "
            + (detail[:1000] if detail else f"exit {completed.returncode}")
        )
    return json.loads(completed.stdout)["sessionStateFile"]


with open(resolve_state_file(), "r", encoding="utf-8") as handle:
    state = json.load(handle)
section = state["platforms"][PLATFORM]
base_url = section["requestProfile"]["baseUrl"].rstrip("/")
cookie = "; ".join(
    "%s=%s" % (item["name"], item["value"])
    for item in section.get("cookies") or []
)
response = requests.get(base_url + "/api/v1/items", headers={"Cookie": cookie}, timeout=30)
```

- `SKILL_NAME` 必须是硬编码字符串，且等于 `muad.skill.json` 的 `name`（`self-skill-review` 的 SS-R011 会检查）。
- `PLATFORM` 是 `muad.skill.json` 的 `platforms` 中的平台名，必须是字面量。
- 状态文件缺少对应平台、`baseUrl` 为空时 fail loud（stderr + 非 0）。
- 401/403 不会自动刷新登录态；重新执行 `get-state` 后再发起请求。

## 本地开发

本地没有 session-manager。把同形状的 state 文件放到 `MUAD_LOCAL_SESSION_STATE` 指向的路径，
模板脚本在 `MUAD_SESSION_SOCKET` 不存在时会优先使用它（`tools/fake-business-platform/`
可以生成这种 state 并做端到端验证）。不要提交填好的状态文件——里面是真实 Cookie。

## browser 能力

声明 `capabilities: ["browser"]` 的 Skill 仍通过脚本入口执行；登录态仍由脚本内的 `session-manager` 获取。当前没有独立的 `session_get_state` 模型工具，不要引导模型调用不存在的工具；SKILL.md 顶部直接列出脚本命令（如 `python3 scripts/run.py`）。

## 不需要在 SKILL.md 里做的事

- 不要让模型直接调用不存在的 `session_get_state` 工具；登录态从脚本内的 `session-manager` 获取。
- 不要指导用户手工保存 cookie 或 token；登录态由 `session-manager` 管理（本地开发文档除外）。
