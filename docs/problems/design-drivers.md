# Fluxion Design Drivers

These problems are retained as architecture evidence. Do not delete them when simplifying docs.

| ID | Problem | Architecture response |
|---|---|---|
| P01 | `openclaw.json` changes required runtime restart | Registry + immutable versions + hot reload |
| P02 | Agent/runtime held durable facts | Stateless Runtime + external stores |
| P03 | Skill/MCP bound to agent caused user data fragmentation | Definition + User/Tenant Binding |
| P04 | Local and production config models diverged | SQLite dev + PostgreSQL prod behind one Store contract |
| P05 | Logical Agent lifecycle coupled to K8s Pod | RuntimeProfile + shared Runtime Pool |
| P06 | No practical local config entry without Console | Agent+Console dev bundle over SQLite（YAML 仅 import/export，非运行期事实源）; Kernel still SDK/CLI-callable |
| P07 | Hot reload could change config mid-execution | ExecutionSnapshot |
| P08 | Agent Core grows with every capability | Microkernel + Plugin Runtime |
| P09 | Security/audit/approval logic bloats executor | Typed lifecycle hooks |
| P10 | In-process plugins expand trust boundary | Trusted in-process vs isolated extensions |
| P11 | LLM tool chains are not durable workflows | Workflow Engine boundary |
| P12 | Tool/Workflow/API duplicated business logic | Capability contract |
| P13 | Channel users and internal users split identity | Unified PlatformUser + identity mapping |
| P14 | Schema-valid output can be semantically wrong | L0-L3 validation levels |
| P15 | Too much approval causes approval fatigue | Risk-based approval |
| P16 | User owns MCP does not imply every Agent may call it | User Grant ∩ Agent Allowlist ∩ Tenant Policy |
| P17 | Agent-private user profile creates inconsistent facts | User Context/Profile resource |
| P18 | One-time eval becomes stale | Versioned continuous Eval |
| P19 | Safety layers can destroy latency | Explicit latency budgets |
| P20 | Multi-agent direct code coupling does not scale | Minimal A2A contract |
| P21 | SOP hard-coded in Python blocks versioning/console management | Workflow DSL |
| P22 | Architecture patterns can become cargo cult | Problem -> Constraint -> Decision -> Validation |
| P23 | Policy 校验模型（name+rules）与运行时消费（allowed_tools/denied_tools）脱节，控制台建不出有效策略 | PolicyDefinition 对齐运行时+展示层字段；死字段清单见 resource-spec-field-injection.md |
