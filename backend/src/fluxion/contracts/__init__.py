"""Fluxion 领域契约层（api/services 可依赖，runtime 内部实现不可反向依赖 services/api）。

依赖方向：`api/cli/sdk → services → domain contracts → repositories/providers`；
runtime 内部实现（`fluxion.runtime.*`）在其后，api/services 不得 import
`fluxion.runtime.*`（架构守护 `test_workflow_architecture.py`）。
"""
