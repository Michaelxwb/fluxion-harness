# PR22 CI入口404修复

## 1. B-09范围与证据

CI仅capacity离线入箱用例在创建会话时404，未发生SDK发送。该用例本地独立运行通过；同文件按序复跑中离线用例也通过，但旧Worker接管场景在macOS超时，未计pass，不继续本地重跑重型场景。现有CI产物缺服务端快照明细，不能声称已证明404的唯一根因。

排查发现明确就绪漏洞：首次快照不完整或应用失败时，console_reachable=true、is_ready=false，现有OR判断仍报告ready。以真实BotSnapshotCache/ASGI健康端点和故障来源替身复现。修复为必须存在成功应用的完整快照；已有完整快照时Console临时不可用仍允许服务。验收栈额外在所有Gateway副本上确认种子入口可见，只等待前置条件，不重试失败的创建请求，不伪造业务响应。

B-09契约：首次不完整/应用失败均503；首次应用成功及已缓存快照均200；不得把Console可连通当作配置已应用。保留原离线入箱真实端到端断言。无数据库结构或数据迁移。

## Spec Compliance Matrix

| Rule | 应用与验证 |
|---|---|
| RULE-im-001 | 网关只持入口完整快照；本次不引入Agent到Pod绑定。完整IM套件按review/CI延期 |
| RULE-im-002 | 修改仅在Gateway健康检查及测试夹具，不向Runtime/Worker添加渠道差异；neutrality检查 |
| RULE-test-001 | unit/contract复现与真实离线用例结果分开；重型接管超时不算pass |
