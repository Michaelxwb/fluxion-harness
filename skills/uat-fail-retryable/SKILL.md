---
name: uat-fail-retryable
description: "B-02 阈值对照夹具：每次调用都以相同的可重试错误失败，不产生副作用。"
invocation_contract:
  version: 1
  invocation: stdin
  schema:
    type: object
    properties: {}
    required: []
    additionalProperties: false
---

# uat-fail-retryable

只用于 B-02 同类失败阈值对照。每次调用向 stderr 输出相同的结构化错误并退出 1：

```json
{"error":{"code":"UAT_ALWAYS_FAIL","message":"uat fail retryable","retryable":true}}
```

不读文件、不访问网络、不写数据。
