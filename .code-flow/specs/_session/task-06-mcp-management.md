# TASK-002 Spec Context

- Context-SHA256: `5acaf4db8dcc7b1d8cccab5eba0aba64caded4b255f9b009f146ad7c4b84b798`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-06 | unit | 真实本地 HTTP MCP 探针（不 mock HTTP） | initialize+tools/list 成功路径；normalize 结构；超时与失败传播 | tests/console_mcp/test_mcp_client.py | uv run pytest -q tests/console_mcp/test_mcp_client.py | planned |
