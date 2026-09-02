from __future__ import annotations

from fluxion.resources import ResourceDefinition


def is_non_sensitive(resource: ResourceDefinition) -> bool:
    return not _contains_sensitive_ref(resource.spec_json)


def _contains_sensitive_ref(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if "secret" in lowered or "credential" in lowered:
                return True
            if _contains_sensitive_ref(item):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_ref(item) for item in value)
    return False
