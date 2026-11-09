# 状态与输出目录

使用平台注入的目录变量，不覆盖变量、不硬编码路径。

| 内容 | 目录 | 生命周期 |
|------|------|------------|
| 游戏进度、缓存等跨轮次状态 | `SKILL_DATA_DIR` | 跨调用、跨 Run 保留；受 workspace 配额和 TTL 管理 |
| 报告、单次执行结果 | `SKILL_OUTPUT_DIR` | 每次执行独立；草稿测试的输出目录为临时目录 |
| 无需保留的临时文件 | `/tmp` | 当前沙箱结束即丢弃 |

草稿通过 `skill_draft_shell` 执行时，`SKILL_DATA_DIR` 是该草稿独享的测试目录。
正式 Skill 的 data 按用户与 Agent 共享，状态文件应放在业务自己的子目录中，避免同名覆盖。
草稿测试数据不进入提交包，审批通过后也不迁移到正式 data。

## Python 最小用法

```python
import json
import os
from pathlib import Path

# 使用相同代码测试和正式运行，平台负责区分两种数据目录。
data_dir = Path(os.environ["SKILL_DATA_DIR"]) / "my-skill"
data_dir.mkdir(parents=True, exist_ok=True)
(data_dir / "state.json").write_text(json.dumps({"step": 1}), encoding="utf-8")

output_dir = Path(os.environ["SKILL_OUTPUT_DIR"])
output_dir.mkdir(parents=True, exist_ok=True)
(output_dir / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
```

需要目录变量时应明确校验；缺失时报告错误，不回落到源码目录。
Skill 根目录在正式执行中只读，不承载状态或结果。

## 草稿测试

使用同一 `draft_id` 分次运行真实草稿，验证前一次写入的状态能在下一次读取。
代码修改、失败、超时或 revision 冲突都不会自动清空已经写入的测试状态。
需要重新测试初始状态时，给 `skill_draft_shell` 传 `reset_data=true`，只清空本草稿的数据。

最终结果仍通过脚本 stdout 交给模型总结；正式执行的输出文件由平台登记，供后续读取或下载。
