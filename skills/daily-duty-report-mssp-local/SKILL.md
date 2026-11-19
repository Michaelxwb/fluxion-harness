---
name: daily-duty-report-mssp-local
description: "本地回归用「每日值守总结」Skill（Smoke 测试平台 MSSP 端点）。与 daily-duty-report-mssp 同形态，保留 argparse 与 stdin 双入口，输出头三项计数、外部威胁趋势、威胁实体、安全事件和真实 xlsx 附件。"
---

# 本地值守日报（Smoke 测试平台 MSSP 端点）

这是 `skills/` 下的**手动验证用 Skill**，不是生产 Skill：

- 与 `daily-duty-report-mssp` 同形态：`scripts/run.py` 保留 argparse 入口，
  `scripts/cron_entry.py` 读 stdin，二者共用 `scripts/report.py`。
- 只访问本仓库共享 fake 平台的 MSSP 端点（`tools/fake-business-platform` 的 `platform`
  服务，和 Console 里的 Smoke 测试平台同一地址），**不访问内网、不需要 session-manager、
  不依赖 requests**；xlsx 附件使用平台已有的 openpyxl。
- `muad.skill.json` 的 `platforms` 是 `smoke_platform`：直接绑定 Console 里已有的
  Smoke 测试平台，不需要新建 MSSP 平台配置。
- 报告是简化版：头三项计数、外部威胁趋势、威胁实体、安全事件、真实 xlsx 附件。
  附件是按真实 Skill 形态生成的 `.xlsx`（威胁实体列表 + 事件列表；无可导出行时不生成）。

## 前置：启动共享 fake 平台

```bash
# 共享 platform 服务（18080）：smoke-platform / MSSW / MSSP 端点同一进程
./tools/fake-business-platform/services.sh start platform
```

默认固定 Host / CSRF / 客户路由：

| 项 | 值 |
|---|---|
| Host | `soar-inner.sangfor.com.cn` |
| CSRF 头 | `X-CSRFToken: 6c5c52cd-3ebb-47e2-946c-15c2569836e7` |
| 客户路由 | `X-MSSW-Company-Id: <company_id>`（api-proxy 必带） |

fixtures 见 `tools/fake-business-platform/fixtures/mssp.json`。
`MSSP_BASE_URL` / `MSSP_HOST_HEADER` / `MSSP_CSRF_TOKEN` 可覆盖默认值。

## 入口 1：argparse

```bash
MSSP_BASE_URL=http://127.0.0.1:18080 \
SKILL_OUTPUT_DIR=/tmp/mssp-report \
python3 scripts/run.py --company-id 1001 --date 2026-11-18 --no-wecom-push
```

参数：

- `--company-id`：必填，fixture 客户 ID（`1001`/`1002`/`1003`/`1004`/`2101`..`2111`）。
- `--date`：`YYYY-MM-DD`，缺省昨天。
- `--no-wecom-push`：跳过只读的企微群查询。

## 入口 2：stdin

兼容 Fire 信封与直接业务输入：

```bash
echo '{"mode":"SKILL_PLAN","skill_id":"daily-duty-report-mssp-local",
       "input":{"company_id":"1001","date":"2026-11-18","no_wecom_push":true},
       "context":{}}' | python3 scripts/cron_entry.py

echo '{"company_id":"1001","date":"2026-11-18","no_wecom_push":true}' \
  | python3 scripts/cron_entry.py
```

业务字段优先级：`input` > `context` > 顶层。缺 `company_id` 时退出码 2，
stderr 输出 `{"error":{"code":"MSSP_FETCH_FAILED",...}}`。

## 输出

stdout 只有正文；诊断走 stderr。正文结构：

```text
【深信服MSS每日总结-YYYYMMDD】
尊敬的客户，您好，… 安全运营值守情况如下：
安全运营中心主动分析组件上报安全日志N条，分析研判安全告警N条，生成有效事件N条，详情如下：

1、外部威胁：
{两期为零 / 上期为零 / 上升 / 下降 / 持平}
2、威胁实体处置：
{disabled / mock / security}
3、安全事件：
{空态或 top5}
附件：《安全事件及威胁实体封堵列表-YYYYMMDD.xlsx》
```

落盘目录：`$SKILL_OUTPUT_DIR/值守日报存档/<客户名>_<客户ID>/<日期>/`
（未设置 `SKILL_OUTPUT_DIR` 时是当前目录 `.skill-output/`），含 `report.txt`
与真实 xlsx 附件。

## 手动导入平台做定时任务验证

```bash
./skills/package.sh daily-duty-report-mssp-local
# 产物：skills/daily-duty-report-mssp-local.zip
```

### 1. 启动依赖

```bash
# 共享 platform 服务（Skill 取数；Console 里就是 Smoke 测试平台那个 base URL）
./tools/fake-business-platform/services.sh start platform
./tools/fake-business-platform/services.sh status

# 需要验证「Skill 自己经 MSSP 发企微」时才开写；平台 PUSH 不需要它
PLATFORM_WECOM_WRITE=1 ./tools/fake-business-platform/services.sh restart platform
```

Fake 平台默认绑定 `127.0.0.1:18080`，本机 Worker 可直接访问；Worker 在容器/别的机器时，
把 `MSSP_BASE_URL` 或 `config/api_config.json` 的 `base_url` 改成该网络内可达的地址。

### 2. 导入并绑定

在 Console「Skill」页导入 `skills/daily-duty-report-mssp-local.zip`、完成审批/启用，并把
Skill 绑到测试 Agent，平台选已有的 **Smoke 测试平台（key=`smoke_platform`）**；不要新建
MSSP 平台。`argv` 示例（fixture 固定为 `2026-11-18`）：

```json
["--company-id", "1001", "--date", "2026-11-18", "--no-wecom-push"]
```

平台既可以用上面的 `argv`，也可以只传 `input`（`run.py` 在没有 `--company-id` 时从
stdin 读同样的业务字段）：

```json
{"company_id": "1001", "date": "2026-11-18", "no_wecom_push": true}
```

两者都没有时退出码 2。`--no-wecom-push` 表示**不**让 Skill 自己经 MSSP 查群/发企微，
避免和平台投递重复。

### 3. 验证 WebChat 展示 + 附件

1. 在 WebChat 会话里创建 `SKILL_PLAN`，交付位置选站内（`NONE`），触发时间设为 2 分钟后。
2. 触发后在原会话应看到日报正文；任务中心应能看到同一次执行。
3. 打开附件应能下载 `report.txt` 与 `安全事件及威胁实体封堵列表-20261118.xlsx`。

### 4. 验证 WECOM 主动 push

1. 选择已绑定的企微入口，创建 `SKILL_PLAN`，交付位置选 `WECOM`（`PUSH`）。
2. 触发后企业微信会话应收到日报正文和 xlsx 附件。
3. 数据库/日志核对：
   - `runtime.message_delivery` 的 `status=DELIVERED`、`attempt_count` 合理；
   - Gateway 日志出现 `im_background_delivery_total` 对应的成功路径；
   - Connector 日志里对应 bot 仍是 `CONNECTED`。

> 注意：Fake MSSP 的 `--enable-wecom-write` 只控制 **Skill 自己**经 MSSP 的查群/上传/
> 发送接口，不会真的发到企业微信；真实主动 push 由平台的 WECOM 投递链路完成。

### 5. 全部场景

平台链路先各验一次 WebChat 和 WECOM 投递即可；11 个业务场景的逐条口径用真实包验证：

```bash
uv run python tools/fake-business-platform/real_skill_smoke.py --company-id 2101 --date 2026-11-18
# 2101..2111，以及 1001..1004
```

> 这只是**平台链路**的手动验证版：场景 1-11 的完整内网口径请用
> `tools/fake-business-platform/real_skill_smoke.py` 直接跑真实 Skill 包，不要用本
> 简化版冒充真实 Skill 的逐场景结果。

## Fixtures

| 客户 | 场景 |
|---|---|
| `1001` | 全资产，安全告警由 12 → 24，security 威胁实体 |
| `1002` | 非全资产，企微观测模式（mock） |
| `1003` | 空数据：日志/告警/事件/实体全 0 |
| `1004` | 智能对抗未开通（disabled） |
| `2101`..`2111` | 场景 1-11 的确定性输入（新增资产/组件、原有/新增源、平稳、类型变化、离线、白名单、下降、上期为零、原因不足） |

## 失败行为

- `service_info` 失败是致命错误（无法判断全资产/非全资产），退出码 2。
- 日志/告警/事件/威胁实体任一失败：报告仍生成，正文追加
  `本期以下项目取数暂不可用：…。`，退出码 0。
- 企微只做只读查群；**不会上传或发送**。查群失败显示
  `未获取到企业微信群，请联系管理员处理`。
