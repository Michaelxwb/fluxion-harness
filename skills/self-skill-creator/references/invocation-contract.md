# 调用契约（`invocation_contract`）

可执行 Skill 在 `SKILL.md` frontmatter 声明 `invocation_contract`，告诉平台默认入口怎么收参数。
它只描述调用形状，不描述业务结果；定时任务和对话直接调用共用同一份契约。

纯指令 / MCP-only Skill 不需要写。

## 最小格式

```yaml
invocation_contract:
  version: 1
  invocation: argv        # argv | stdin
  schema:
    type: object
    properties: {}
    required: []
    additionalProperties: false
```

- `version`：P0 只接受 `1`。
- `invocation`：默认入口用 CLI 参数就是 `argv`，读 stdin JSON 就是 `stdin`；一个 Skill 只选一条。
- `schema`：JSON Schema，必须 `type: object`、`additionalProperties: false`。
- 契约只绑**默认入口**（`muad.skill.json` 的 `entrypoint`）；`check_status.py` 等辅助脚本不套用。

## argv 示例

```yaml
invocation_contract:
  version: 1
  invocation: argv
  schema:
    type: object
    properties:
      company-id:
        type: string
        pattern: "^[0-9]+$"
        description: "客户 ID"
      date:
        type: string
        format: date
      no-wecom-push:
        type: boolean
    required: [company-id]
    additionalProperties: false
```

调用形态：

```json
["--company-id", "22921947", "--date", "2026-11-19", "--no-wecom-push"]
```

规则：

- 只支持长选项：`--key value` / `--key=value`；值以 `-` 开头时用 `--key=value`。
- 属性名就是去掉 `--` 的选项名；`help` / `version` 是保留名。
- `type: boolean` 是存在型开关：true 传裸 `--flag`，false 省略；**不要**写 `--flag=true` / `--flag=false`。
- 零参数合法：`properties: {}`、`required: []`、`argv: []`。
- 未知参数、重复参数、短选项、位置参数、`--` 终止符都会被拒绝。

## stdin 示例

```yaml
invocation_contract:
  version: 1
  invocation: stdin
  schema:
    type: object
    properties:
      company_id:
        type: string
        pattern: "^[0-9]+$"
      date:
        type: string
        format: date
    required: [company_id]
    additionalProperties: false
```

调用形态：

```json
{"company_id": "22921947", "date": "2026-11-19"}
```

规则：

- `input` 必须是 JSON 对象；`argv` 必须为空。
- 允许空对象：`properties: {}`、`required: []`、`input: {}`。
- 建议 `additionalProperties: false`，这样 agent 拼错字段会在预检时被发现。

## 怎么从脚本推导

### argparse

```python
parser.add_argument("--company-id", required=True)
parser.add_argument("--date")
parser.add_argument("--no-wecom-push", action="store_true")
```

对应：

- `--company-id` → `company-id`，`required`
- `--date` → `date`，可选
- `--no-wecom-push` → `no-wecom-push`，`type: boolean`

不要用 `--help` 反推；按代码里的 `add_argument` 写。

### stdin

```python
payload = json.load(sys.stdin)
company_id = payload["company_id"]
date = payload.get("date")
```

对应 `properties: {company_id, date}`，`required: [company_id]`。

## 两段式校验

- 创建 / authoring：检查通道、未知参数、重复参数、必填项、模板变量。
- 预检 / Fire：先按 Schedule 时区渲染 `{{fire_date}}` / `{{previous_day}}` / `{{fire_time}}`，再校验 `format` / `pattern` / `enum` / 范围。

对话直接调用没有 occurrence：不接受这三个调度模板，必须给具体值。

## 常见错误

- `company_id` 和 `company-id` 混用：argv 用短横线，stdin 常用下划线；按真实脚本写。
- 漏必填项：`required` 必须和脚本的必填参数一致。
- 把布尔写成 `--flag=true`：真实 `argparse action="store_true"` 会退出 2。
- 把辅助脚本的参数写进主入口契约：契约只描述默认入口。
- 把 `allow_empty` / `output_format` 写进契约：这些是业务/执行语义，不属于调用契约。

## 自检

- [ ] 可执行 Skill 有 `invocation_contract`；纯指令/MCP-only 没有。
- [ ] `invocation` 与默认入口一致。
- [ ] `required` 与脚本必填参数一致。
- [ ] 布尔按存在型开关写。
- [ ] `additionalProperties: false`。
- [ ] 契约只描述默认入口，不含辅助脚本参数。
- [ ] 用真实 argv / stdin 调用跑过至少一次。
