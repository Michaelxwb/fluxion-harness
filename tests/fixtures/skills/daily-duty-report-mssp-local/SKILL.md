---
name: daily-duty-report-mssp-local
description: "本地回归用「每日值守总结」Skill（fake MSSP）。与 daily-duty-report-mssp 同形态，保留 argparse 与 stdin 双入口，输出头三项计数、外部威胁趋势、威胁实体、安全事件和附件占位。"
---

# 本地值守日报（fake MSSP）

这是 `tests/fixtures/skills/` 下的**本地回归 Skill**，不是生产 Skill：

- 与 `daily-duty-report-mssp` 同形态：`scripts/run.py` 保留 argparse 入口，
  `scripts/cron_entry.py` 读 stdin，二者共用 `scripts/report.py`。
- 只访问本仓库的 fake MSSP（`tools/fake-business-platform --platform mssp`），
  **不访问内网、不需要 session-manager、不依赖 requests/openpyxl**。
- 报告是简化版：头三项计数、外部威胁趋势、威胁实体、安全事件、附件占位。
  附件是 `.txt` 占位文件，不是真实 xlsx。

## 前置：启动 fake MSSP

```bash
python3 tools/fake-business-platform/server.py \
  --platform mssp --host 127.0.0.1 --port 18083
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
MSSP_BASE_URL=http://127.0.0.1:18083 \
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
附件：《安全事件及威胁实体封堵列表-YYYYMMDD.txt》（本地回归占位，未生成实际工作簿）
```

落盘目录：`$SKILL_OUTPUT_DIR/值守日报存档/<客户名>_<客户ID>/<日期>/`
（未设置 `SKILL_OUTPUT_DIR` 时是当前目录 `.skill-output/`），含 `report.txt`
与附件占位。

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
