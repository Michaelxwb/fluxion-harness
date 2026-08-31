"""TASK-014：ConfigChangeEvent 细化为 ResourcePublished/PolicyChanged 领域事件。"""

from __future__ import annotations

from fluxion.resources import ResourceKind
from fluxion.runtime.hot_reload import (
    ConfigChangeEvent,
    PolicyChangedEvent,
    ResourcePublishedEvent,
)


def _base(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "tenant-a",
        "kind": ResourceKind.RUNTIME_PROFILE,
        "resource_id": "assistant",
        "version": "2",
        "revision": 9,
    }
    base.update(overrides)
    return base


def test_T014_event_type_discriminates_by_kind() -> None:
    published = ConfigChangeEvent(**_base(kind=ResourceKind.SKILL))
    changed = ConfigChangeEvent(**_base(kind=ResourceKind.POLICY))

    assert published.event_type == "resource_published"
    assert changed.event_type == "policy_changed"


def test_T014_named_events_are_typed_and_carry_type_in_payload() -> None:
    published = ResourcePublishedEvent(**_base(kind=ResourceKind.MCP))
    changed = PolicyChangedEvent(**_base(kind=ResourceKind.POLICY, resource_id="tenant-policy"))

    assert isinstance(published, ConfigChangeEvent)
    assert isinstance(changed, ConfigChangeEvent)
    assert published.event_type == "resource_published"
    assert changed.event_type == "policy_changed"
    # payload 携带 event_type，下游可区分领域事件。
    assert published.to_payload()["event_type"] == "resource_published"
    assert changed.to_payload()["event_type"] == "policy_changed"
    assert changed.to_payload()["resource_id"] == "tenant-policy"
