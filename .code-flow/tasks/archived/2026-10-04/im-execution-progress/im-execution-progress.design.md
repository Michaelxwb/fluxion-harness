# IM 对话执行状态与计时设计简报

> 日期：2026-10-04；状态：已批准。依据：本会话用户已确认原生动画、彩色图标和每秒计时效果，并要求开始编码。

## 1. 文档控制

真机结论：方块逐帧轮播卡顿；原生思考占位流畅；font 颜色标签被原样输出；think 被客户端渲染为带标题的块，不能承诺修改客户端标题；图标正常；每秒更新计时已获用户确认。回执证据保存在 /tmp/fluxion_loading_timer_result.json，25 次计时更新和一次终态均成功，不代表生产容量已验证。

## 2. 需求分析

### 2.1 需求概述

FEAT-01：用户发消息后，在实际执行期间看到动画、当前阶段与真实耗时。来源：本会话需求。
FEAT-02：模型调用显示思考中，工具/技能运行显示执行中，阶段切换立即生效。来源：本会话需求。
FEAT-03：计时不写库、不调用模型；发送慢时合并计时更新；正文和收尾优先。来源：用户容量顾虑。
FEAT-04：渠道中立的可选回复会话能力，按消息绑定回调上下文，避免同会话并发互相覆盖。来源：用户要求未来扩展 IM。

### 2.2 范围与边界

覆盖 IM 同步 Run（含技能/工具执行）、提交准备、等待确认、完成/取消/失败/断流、后台任务受理后的明确交接。现有后台 Task 继续使用 Worker 的可靠最终结果投递；独立 Task 的全生命周期通知属于独立产品流程，不能用已结束的 Run 的计时器假装后台仍在执行。没有剩余时间、虚假百分比、网页入口或自定义客户端动画。
执行时间按当前 submission 的真实运行起点计算；resume 从当前执行段计时，等待用户时间不计入本段。Run 重放以已持久化事件时间恢复本段计时，不把 HTTP 重连当成新执行。

### 2.3 验收条件

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 预期结果 |
|--------|--------|----------|--------------|----------|
| S-401 | FEAT-01..04 | E2E | 真实 WS → Gateway → Runtime/PG → 模型 HTTP 探针 → 原生流回复 | 模型事件已落库；动画占位/计时出现；正文不含占位；终态后无更新 |
| S-402 | FEAT-01..04 | integration | 真实 Runner → Executor 模型与工具事件 | 每次模型调用的开始/结束事件包围实际调用；工具阶段由真实工具事件驱动 |
| E-401 | FEAT-01..04 | integration | 真实 Pipeline → 回复会话 → 等待/终态/断流清理 | 等待用户、受理后台任务、错误与终态停止计时；发送失败不阻断执行 |
| B-401 | FEAT-01..04 | unit | 进度状态机与有界异步调度 | 并行工具优先执行中；终态不可被旧更新覆盖；慢发送不积压；负载退让 |
| B-402 | FEAT-01..04 | integration | 真实适配器 → 本地 TLS WS → 官方 SDK | 消息回调隔离；状态全量替换；正文追加；finish 移除占位 |


## 3. 技术设计

### 3.1 方案选型

沿用 Python asyncio、现有 SSE 持久事件和渠道适配器，不新增部署单元、数据库状态机或队列。原生动画由适配器渲染，Gateway 传递已本地化的渠道中立状态文本。

### 3.2 架构设计

Runner 发模型生命周期回调，Executor 转 model.started/model.completed；RunService 先持久化再 SSE；Gateway 进度状态机从 run.created/model/tool/message/terminal 事件推导阶段；每条入站消息建立独立 ReplySession，状态与正文复用该会话。非状态能力渠道保留原回复路径。
ReplySession 从 message_id 取本条回调引用，stream_id 独立，不按用户/会话共享活跃流。状态字符串从消息目录读取，标签仅在适配器生成。

### 3.3 接口设计

MODEL-01：Runner 新增可选模型开始/结束回调；所有重试路径包围真实模型调用，异常仍继续传播。
SESSION-01：可选 ReplySessionFactory.open_reply(route, message_id) → ReplySession；ReplySession 提供 update_status(text)、stream(text)、send(message)、finish()。finish 幂等；状态不进入正文 buffer。
PROGRESS-01：Gateway 异步调度使用单消费者，按最新阶段/时间渲染；同一 session 不并发写入；事件输入有界；终态/正文优先于 tick。按实际时间计算 mm:ss，不通过 tick 次数累加。

### 3.4 性能与容量考量

默认计时节拍 1 秒。不写每秒 canonical_event、不每秒查库，不创建线程。状态 tick 不排队，只在发送者空闲时计算最新值。通道/机器人发送预算有界且可配置，预算不足跳过 tick；阶段变化和正文沿主发送路径执行，不能让计时阻塞真实结果。真实企微吞吐容量未验证，不承诺固定大并发上限。

## 4. 风险与依赖

原生标题不可定制已向用户说明。思考语义只作状态展示，不输出模型私有思维链。单用户25秒实测不能外推限流额度；通过有界队列与可配置计时发送预算处理。进度发送失败留日志并继续正文；取消时回收发送任务和打开的异步生成器。以上风险由 E-401/B-401/B-402 覆盖。

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态 |
|-----------|-------------|----------|----------|----------|------|
| harness-api#RULE-api-002 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-api#RULE-api-001 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-im#RULE-im-001 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-im#RULE-im-002 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-snapshot#RULE-snapshot-001 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-test#RULE-test-001 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-arch#RULE-arch-001 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
| harness-model#RULE-model-001 | required | 保持现有权威状态/幂等/渠道边界/验收机制 | 3.2 架构设计 | S-401, B-402；原规范 verifier | applied |
