"""In-process verifier registry keyed by task family.

注册是**平台/部署配置**行为：同一进程内每个任务族至多一个 verifier；重复注册显式拒绝。
`default_verifier_registry` 是运行时的共享注册表，测试与后续装配（任务族→verifier）都从这里取。
"""

from __future__ import annotations

from collections.abc import Mapping

from muad_agent_core.agent import Verifier


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}

    def register(self, task_family: str, verifier: Verifier) -> None:
        family = task_family.strip() if isinstance(task_family, str) else ""
        if not family:
            raise ValueError("task_family must be a non-empty string")
        if not callable(verifier):
            raise ValueError("verifier must be callable")
        if family in self._verifiers:
            raise ValueError(f"verifier already registered: {family}")
        self._verifiers[family] = verifier

    def load(self, verifiers: Mapping[str, Verifier]) -> None:
        """从配置映射批量装载；任一重复即整体拒绝（不留下半注册状态）。"""
        for family, verifier in verifiers.items():
            self.register(family, verifier)

    def resolve(self, task_family: str | None) -> Verifier | None:
        if not task_family:
            return None
        return self._verifiers.get(task_family)

    def families(self) -> tuple[str, ...]:
        return tuple(sorted(self._verifiers))


#: 运行时共享注册表；进程内生效（跨 Pod 的持久注册属于后续需求）。
default_verifier_registry = VerifierRegistry()
