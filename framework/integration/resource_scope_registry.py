import hashlib
import json

from framework.contracts.resource_scope import ResourceScopeType


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ResourceScopeRegistry:
    """Registry of Integration-declared scope types (V1.7 D01).

    Built from manifest `resource_scope_types` at load time. Framework Core
    never imports project code; it only sees names + JSON Schemas.
    """

    def __init__(self, scope_types: dict[str, dict[str, object]] | None = None):
        self._types: dict[str, ResourceScopeType] = {}
        for name, declaration in (scope_types or {}).items():
            schema = declaration.get("schema", {})
            schema_hash = hashlib.sha256(_canonical(schema).encode("utf-8")).hexdigest()
            self._types[name] = ResourceScopeType(
                name=name,
                schema=schema if isinstance(schema, dict) else {},
                schema_hash=schema_hash,
            )

    def get(self, scope_type: str) -> ResourceScopeType | None:
        return self._types.get(scope_type)

    def names(self) -> list[str]:
        return sorted(self._types)
