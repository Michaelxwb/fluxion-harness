---
name: login-mssp-platform
description: 登录 MSSP 平台并输出当前平台会话摘要（旧形态，不改造）
platforms: [mssp]
---

# login-mssp-platform（旧形态，不改造）

本包刻意保持旧形态，用于验证兼容契约：

- `scripts/main.py` 只调用平台注入的 `session-manager get-state --skill-name login-mssp-platform`；
- 从 stdout 的 `platforms` list 和 `sessionStateFile` 指向的 state 文件读取 cookies 与
  `requestProfile`；
- 不读取 `cookies.txt`、不把 base_url 写进包内配置、不写包根目录。

运行方式保持历史 SKILL.md 的写法：`python scripts/main.py`（解释器参数由平台注入）。
