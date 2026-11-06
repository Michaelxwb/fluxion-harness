# Console PR 提交前检查

修改 API 响应字段或 Console 列表、详情后，在推送 PR 前执行：

```bash
bash scripts/check_console_pr.sh
```

入口执行编译、词条、错误文案、全仓 Ruff/mypy、前端构建、配置盘点与前端源码契约测试，然后顺序执行 CI 的全部 component 域。本地脚本直接读取 `.github/workflows/check.yml` 的 component 域清单，使用同样的 Makefile 命令；不另维护一份清单，也不修改 CI workflow。`--component-only` 可复用已有构建产物。需要已安装 Python/前端/e2e 依赖及系统 Chrome；缺失环境、清单解析失败或用例失败必须处理，不能当作通过。Playwright 的 pretest 会停止本仓库 dev 服务。

各域日志保存在 `/tmp/component-logs/`；失败时保留对应的 trace 和 error-context。浏览器用例顺序运行，避免下一轮 pretest 清理上一轮 preview 进程，以及多个用例覆盖同一个 test-results 目录。

新增 API 字段时同时检索 E2E/组件 API 夹具、列表列数和详情断言。Skill 模拟数据使用服务层 DTO 类型的 `satisfies` 约束；覆盖空数组、单平台、多平台以及历史版本与当前版本声明不同的情况。不要通过放宽断言或把真实接口错误默认为空数据来消除失败。

这个入口覆盖 Console 静态和 component 检查，不能替代后端、验收、smoke 和真实 E2E。后端变更需要在独立 PostgreSQL/Redis 上迁移后执行匹配的测试分片；浏览器真实旅程使用项目域配置的隔离数据存储。不得指向 dev 数据库。完整 PR 结果以当前提交的 CI 为准，更新 PR 描述时区分本地已通过、CI 已通过和仍在等待的项目。

本次 Skill 平台展示回归的漏项：组件 API 夹具遗漏 `platforms` 导致页面崩溃；列表新增列未同步预期列数；DTO 插入行导致配置盘点中的常量位置过期。上述均可由本入口及对应后端检查在推送前发现。
