---
{"version":1,"requirement":"chat-page-scroll-lock"}
---

# Tasks: chat-page-scroll-lock

<!--
用户反馈 2026-11-19：WebChat 对话页偶发可以整页上滚，出现输入框下方大块空白，无法稳定复现。
根因假设：CHAT 路由的根节点没有被锁定到视口高度，页面在窗口/扩展横幅/隐藏 Tab 切换后可能被撑高；
文档因此成为第二个滚动容器，消息区不再是唯一滚动视口。修复采用「页面根节点锁定 100dvh + 文档滚动锁」，
不改变消息区自身的滚动与底部跟随。
-->

## TASK-001: 锁定 WebChat 对话页到视口，消除整页滚动

- **Status**: done
- **Depends**:
- **Source**: 用户反馈 2026-11-19（偶发整页上滚，输入框下方空白）
- **Scenarios**: chat-page-scroll-lock:S-01
- **Spec-Refs**: frontend/harness-frontend#RULE-front-001, ui/harness-ui#RULE-ui-001, test/harness-test#RULE-test-001
- **Paths**: apps/console-platform/frontend/src/modules/webchat/ChatPage.tsx, apps/console-platform/frontend/src/modules/webchat/webchat.css, tests/frontend/test_capacity_webchat_webchat_layout.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "chat-page-scroll-lock",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend/test_capacity_webchat_webchat_layout.py", "tests/frontend/test_capacity_webchat_chat_route.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- `.chat-page` 固定为 `100dvh`（不支持时回退 `100%`），并 `overflow: hidden`，页面本身不再成为滚动容器。
- `.chat-workspace` / `.chat-workspace-tabs` 补齐 `height: 100%`，确保 Tab 面板填满可用高度。
- ChatPage 挂载时给 `html` 加 `chat-route-active`，锁定 `html/body` 滚动并复位到顶部；卸载时移除并恢复 Console 页面滚动。
- 唯一滚动视口仍为 `.chat-panel-messages`；输入区保持底部，消息跟随逻辑不变。
- 新增 CSS 布局断言，防止根节点再次失去视口锁定。

### Checklist

- [x] 对话页根节点高度等于视口，`html/body` 不再出现第二条滚动条。
- [x] 消息区仍可独立滚动，输入框固定在底部。
- [x] 离开 CHAT 路由后 Console 页面恢复自身滚动。
- [x] 布局与路由测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-19] created

### Verification

- 尚未执行
- 2026-11-19T13:58:30.504Z PASS commands=1/1 executed=1 deduped=0 elapsed=15508ms tests=588b28b281f0 head=8e02f467cab4 dirty=1
  - chat-page-scroll-lock PASS exit=0
