# 移除出站专用文件配置

## 1. 范围与验收 B-08

用户确认专用JSON和Dockerfile入口冗余。本修复删除EGRESS_CONTROL_FILE、默认JSON、文件加载器与文件规则入口。域、epoch和生命周期使用SharedSettings；接口规则唯一来源为Console公共策略投影。未启用规则且没有执行预算时不创建计量Redis连接。保留规则版本一致性、执行预算冻结、状态丢失fail-closed及HTTP响应透传。无需新增数据库字段、表或初始化业务数据。

迁移真实PG/Redis/HTTP的既有定向夹具到统一Settings；Linux恢复/进程组重型探针只更新输入方式，仍按既有review延期，不声称本轮运行通过。

B-08契约测试使用真实SharedSettings和SDK装配、可观察的依赖替身：没有文件也能构建受控宿主；空策略不触发Redis；非法生命周期明确拒绝。真实HTTP、数据库及恢复链路分别运行已有定向回归，不以替身验证冒充E2E。

## Spec Compliance Matrix

沿用需求的17项Spec Context和已有规则所有者。SDK嵌入现有服务（RULE-arch-001），平台地址/认证仍由平台配置及Adapter负责（RULE-platform-001）；脚本不获取计量凭据（RULE-secret-001）；冻结预算/Task权威不变（RULE-snapshot-001、RULE-worker-001、RULE-data-001）；真实边界和重型延期按RULE-test-001记录。
