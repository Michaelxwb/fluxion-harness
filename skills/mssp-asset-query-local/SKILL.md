---
name: mssp-asset-query-local
description: "本地 MSSP 资产查询夹具：按客户 ID 查询资产 IP 明细，支持关键字、分页和完整 CSV 导出。用于验证资产查询、结果截断、提问续跑和超限产物交付。"
invocation_contract:
  version: 1
  invocation: stdin
  schema:
    type: object
    properties:
      platform:
        type: string
        maxLength: 32
        default: "mssp"
        description: "平台标识；本地夹具只配置了 mssp，其它平台应明确说明不可用，不要跨平台复用客户 ID。"
      company_ids:
        type: array
        items:
          type: string
          pattern: "^[0-9]+$"
        minItems: 1
        maxItems: 20
        description: "客户 ID 列表，例如 [\"1001\"] 或 [\"9001\"]。用户没有给出客户 ID 时先提问，不要猜测。"
      keyword:
        type: string
        maxLength: 128
        description: "可选的 IP、主机名或业务名关键字；用于缩小范围。"
      offset:
        type: integer
        minimum: 0
        default: 0
        description: "从第几条开始返回；默认 0。"
      limit:
        type: integer
        minimum: 1
        maximum: 200
        default: 20
        description: "本次最多返回多少条；默认 20，最多 200。"
      export:
        type: boolean
        default: false
        description: "为 true 时导出全部匹配资产为 CSV 文件；用于完整清单或超限结果。"
    required: ["company_ids"]
    additionalProperties: false
---

# mssp-asset-query-local

这是本地 fake MSSP 平台的资产查询夹具，不是生产 Skill。数据源是
`tools/fake-business-platform` 的 MSSP 端点，默认地址 `http://127.0.0.1:18080`。

## 调用方式

统一执行入口：

```text
execute(target="skill", skill_key="mssp-asset-query-local",
        input={"company_ids": ["1001"], "limit": 20})
```

## 输入约定

- `platform` 默认 `mssp`；本地夹具只有 MSSP。用户点名其它平台时，明确说明该平台未配置，不要拿 MSSP 的客户 ID 冒充跨平台结果。
- `company_ids` 必填。用户没有给出客户 ID 时，先用 `ask_user` 问客户名或客户 ID，不要猜。
- `keyword` 可选，用于按 IP、主机名或业务名缩小范围。
- `limit` 默认 20，最多 200。结果中的 `truncated=true` 表示还有更多记录，
  必须说明未取全；需要完整清单时用 `export=true`。
- `export=true` 时把全部匹配资产写成 CSV 到 `SKILL_OUTPUT_DIR`，返回 `files`。
  若返回 `incomplete=true` 或 `issues` 含 `MISSING_ROWS` / `DUPLICATE_ROWS`，只能交付已核实行并明确说明缺口，不能声称完整。
  执行结果会额外带 `outputs[].artifact_id`；必须用该 id 调用
  `deliver_artifact(artifact_id=...)` 把文件交给用户，不要只说“已生成”，也不要用
  `files[].path` 当 workspace 路径。

## 输出

```json
{
  "ok": true,
  "companies": [{"company_id": "9001", "company_name": "批量资产客户", "total": 1200}],
  "total": 1200,
  "returned": 50,
  "truncated": true,
  "next_offset": 50,
  "assets": [
    {"asset": "10.60.0.1", "hostname": "bulk-host-0000", "business_name": "批量资产客户批量业务1"}
  ],
  "files": []
}
```

导出模式下 `assets` 只保留少量预览，完整内容在 CSV 中：

```json
{"ok": true, "total": 1200, "returned": 5, "truncated": false,
 "files": [{"path": "asset-query-9001-20261127T133000Z.csv", "rows": 1200}]}
```

## 本地检查

```bash
# 需要 fake platform（18080）已启动
echo '{"company_ids":["1001"],"limit":10}' | python3 skills/mssp-asset-query-local/scripts/run.py
echo '{"company_ids":["9001"],"export":true}' | python3 skills/mssp-asset-query-local/scripts/run.py
```
