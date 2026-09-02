"""REQ-CAP-005（TASK-004）：Skill 采用「Agent baseline + User Binding 扩展」语义。

B-S-03：用户 grant 不在 Agent baseline 的 skill → 有效集包含该 skill（扩展）；
baseline 内 skill 的版本 pin 由 Agent 声明优先（不因 binding 漂移）。

注意（对齐 foundation §4.1）：TenantPolicy 是 Tool/MCP 的硬闸门；Skill 按
visibility + grant 授权，其「受 Tenant Policy 约束」由 Agent baseline 声明 +
Skill required_capabilities closure（test_skill_closure）共同承载，不设独立
skill-tenant 闸门。
"""

from __future__ import annotations

from fluxion.resources import ResourceBinding, ResourceKind, SubjectType
from fluxion.runtime.resolver import LATEST_PUBLISHED, ResourceSelector, _effective_skill_selectors


def _binding(resource_id: str, selector: str = LATEST_PUBLISHED) -> ResourceBinding:
    return ResourceBinding(
        binding_id=f"b-{resource_id}",
        tenant_id="tenant-a",
        subject_type=SubjectType.USER,
        subject_id="user-a",
        resource_type=ResourceKind.SKILL,
        resource_id=resource_id,
        resource_version_selector=selector,
    )


def test_B_S03_user_binding_extends_skill_beyond_agent_baseline() -> None:
    """用户 grant 不在 baseline 的 skill → 进入有效集；baseline 内不漂移。"""
    agent = [
        ResourceSelector("skill-a", "v1"),
        ResourceSelector("skill-b", LATEST_PUBLISHED),
    ]
    bindings = [
        _binding("skill-c", "v2"),  # 不在 baseline → 用户扩展
        _binding("skill-b", "v9"),  # 在 baseline → 版本合并
    ]
    effective = _effective_skill_selectors(agent, bindings)
    by_id = {s.resource_id: s for s in effective}

    assert set(by_id) == {"skill-a", "skill-b", "skill-c"}
    # 扩展语义：baseline 外 skill 进入有效集
    assert by_id["skill-c"].selector == "v2"
    # baseline 内 skill：Agent pin v1 优先（不因 binding 漂移）
    assert by_id["skill-a"].selector == "v1"
    # baseline 未 pin + binding pin：取 binding
    assert by_id["skill-b"].selector == "v9"


def test_B_S03_no_binding_keeps_agent_baseline() -> None:
    """无用户 binding 时有效集 = Agent baseline（不隐式扩权）。"""
    agent = [ResourceSelector("skill-a", "v1")]
    effective = _effective_skill_selectors(agent, [])
    assert [s.resource_id for s in effective] == ["skill-a"]
    assert effective[0].selector == "v1"
