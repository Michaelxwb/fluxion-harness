# 对接平台的请求头：requests + 登录态

自建 Skill 访问受保护业务平台时统一使用 `requests`。Skill 只做两件事：

1. 调 `session-manager get-state --skill-name <skill>` 取登录态文件（单平台/多平台都用这一种；`--platform` 是 agent 工具，不用于 Skill 脚本）；
2. 从 `platforms.<平台名>` 读 `cookies` 与 `requestProfile`，拼请求头。

平台只校验这些请求头**存在**，不校验取值（实测 CSRF / TraceId 用随机 UUID 同样可用）。
但 Skill 侧不要手工构造敏感头——值从 `requestProfile` 取（审查规则 SS-R007 会告警手工构造）。

```python
import json
import subprocess

import requests

SKILL_NAME = "my-skill"  # 必须与 muad.skill.json 的 name 一致
PLATFORM = "mssw"        # 必须与 muad.skill.json 的 platforms 一致


def resolve_state_file():
    completed = subprocess.run(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        check=False, capture_output=True, text=True, timeout=30,
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
profile = section["requestProfile"]
cookies = section.get("cookies") or []

headers = {"Cookie": "; ".join("%s=%s" % (c["name"], c["value"]) for c in cookies)}
headers.update(profile.get("hostHeader") or {})
for mapping in profile.get("csrf") or []:
    value = next((c["value"] for c in cookies if c["name"] == mapping["cookie"]), None)
    if value:
        headers[mapping["header"]] = value
headers.update(profile.get("staticHeaders") or {})
if profile.get("referer"):
    headers["Referer"] = profile["referer"]

response = requests.get(
    profile["baseUrl"].rstrip("/") + "/api/v1/targets",
    headers=headers,
    timeout=30,
)
```

## requestProfile 字段

| 字段 | 用途 |
|---|---|
| `baseUrl` | URL 前缀 |
| `hostHeader` | `Host` 头；平台按 vhost 路由，缺了会 404 / 403 |
| `staticHeaders` | 原样附加，恒含 `Timezone`，也可能含平台固定业务头 |
| `csrf` | `[{cookie, header}]`：取同名 cookie 的值放进该 header |
| `referer` | 有则加 `Referer` |

## 平台差异

平台差异由平台配置（Console → 项目平台）表达，不要写进 Skill 代码：

- MSSP / MSSP-VMS：需要 `Host`、`Timezone`、CSRF 头；VMS 走 `/gateway/vuln-manager/` 前缀。
- MSSW：不要带 `traceid`（事件详情会返回空 `data`），也不要带 `timezone`。
- XDR 经 MSSP api-proxy：`Host` 用代理网关的值，`Referer` 用 XDR 自己的域名。

## Skill 不应做的事

- 不要手工拼 CSRF / TraceId（SS-R007 告警）；从 `requestProfile` 取。
- 不要硬编码 `baseUrl`、`Host`、内部域名或凭据（SS-R012）；客户 ID 由入参或客户查询接口得到。
- 不要用 `httpx` / `aiohttp` / `urllib.request` / `http.client` 直连业务接口（SS-R006）。
- 不要用 `subprocess` 调 `curl` / `wget` 发业务请求（SS-R008）：既不计入出网计量，也不进 trace。
- 不要把 cookie / CSRF token 写进 stdout、日志或错误信息。
- 401 / 403 不会自动续期；重新执行 `get-state` 后再发起请求。
