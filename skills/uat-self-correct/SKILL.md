---
name: uat-self-correct
description: "UAT fixture: an unknown customer returns a correctable error that lists known customers."
invocation_contract:
  version: 1
  invocation: stdin
  schema:
    type: object
    properties:
      customer:
        type: string
        description: "客户名称或编号"
    required: [customer]
    additionalProperties: false
---

# uat-self-correct

用于验证 Agent 自修复：第一次传入未知客户时返回可重试错误，并在错误信息中列出可用客户编号；模型应据此修正后重试。
