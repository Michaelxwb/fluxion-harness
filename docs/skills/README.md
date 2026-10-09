# docs/skills

用于**手动平台验证**的技能包示例（不属于产品代码，不参与验收）。

## demo-async

演示用异步技能（`execution: ASYNC`）：模型调用后先拿提交回执，Worker 在后台执行
`scripts/main.py`；Run 进入 `WAITING_TOOL`，结果回流后同一 Run 自动接续。

### 打包（Console 导入需要 zip）

```bash
cd docs/skills/demo-async && zip -rX ../demo-async.zip SKILL.md scripts
```

产物 `docs/skills/demo-async.zip` 的根目录就是 `SKILL.md` + `scripts/`（平铺包，
也兼容单层文件夹包装）。

### 导入与绑定（Console）

1. **Skill 管理 → 导入**：上传 `demo-async.zip`；
2. **Agent 管理**：把 `demo-async` 绑定到目标 Agent（作用域 ALL），并在 instructions
   里明确要求“必须使用 demo-async 技能处理请求”（否则真实模型不一定会去调它）；
3. 确认目标 Agent 的机器人与用户绑定（`/bind <绑定码>`）已就绪。

### 触发与观察

给机器人发一条会触发该技能的消息（例如「用 demo-async 跑一下订单 A-1001」）：

1. 先收到“已提交 / 等待结果”类**中间回复**；
2. Console → 审计可观测 → 该 Run 进入 **WAITING_TOOL**（等待横幅、等待起点、未完成关联任务）；
3. Worker 执行 `scripts/main.py`（默认 3 秒）→ 结果经 `/internal/tool-results` 回流；
4. **同一条消息**收到最终答复；Run 完成、关联操作状态走完。

输入字段：`delay_sec`（0–30，默认 3）、`fail: true`（演示失败结果回流）。

### 本地直接跑脚本（不经过平台）

```bash
echo '{"delay_sec": 1, "order_id": "A-1001"}' | uv run python docs/skills/demo-async/scripts/main.py
```
