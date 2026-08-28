# 05 安全、Plugin 与治理详细设计

## 1. 安全层

Schema Validation、Authorization、Semantic Validation、Risk Classification、Approval、Execution 是不同职责。

建议风险级别：

- L0：纯结构校验；
- L1：静态业务规则；
- L2：上下文/权限/状态语义；
- L3：高风险写操作/人工确认。

## 2. Approval

审批不是 Hook 的临时返回值，而是 durable domain state。生产必须有共享 ApprovalDecisionStore；InMemory 仅 test/dev。

审批策略按风险触发，避免所有写操作都人工确认造成 approval fatigue。

## 3. Secret

Definition/Binding 只存 SecretRef。CredentialResolver 按 tenant scope 解析。日志、Trace、Audit 不写 Secret 明文。

## 4. Plugin 信任边界

- trusted infra plugin：可 in-process；
- untrusted/business extension：MCP/RPC/sandbox/worker；
- `run_command/code.exec` 无可用 Sandbox Backend 时 fail-closed。

## 5. Hook

Typed lifecycle hook 保留 priority、timeout、fail policy、scope。Hook 不得成为第二授权系统；授权先于会接触敏感参数的 Hook。

## 6. Tenant Isolation

所有 Resource/Binding/User/Memory/Secret/Trace/Approval 查询必须带 tenant scope。architecture tests 扫描无 tenant 条件的高风险 repository path。
