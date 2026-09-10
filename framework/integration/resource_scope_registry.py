import hashlib
import json

import jsonschema
from jsonschema.protocols import Validator

from framework.contracts.resource_scope import (
    SERVICE_SCOPE_SCHEMA_INVALID,
    ResourceScopeType,
)
from framework.web.errors import AppError


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ResourceScopeRegistry:
    """Registry of Integration-declared scope types (V1.7 D01).

    Built from manifest ``resource_scope_types`` at load time. Framework Core
    never imports project code; it only sees names + JSON Schemas.

    Schemas are validated and **compiled once, at registration**:

    - a malformed schema fails the load, instead of surfacing as an unhandled
      ``SchemaError`` on the first execution that happens to use that type;
    - per-request validation reuses the compiled validator rather than
      recompiling the schema on every call.

    A type declared twice fails fast — the design forbids silent overwrite
    (S-04/E-02: 冲突 key 默认 fail-fast，不采用隐式后注册覆盖).
    """

    def __init__(self, scope_types: dict[str, dict[str, object]] | None = None) -> None:
        self._types: dict[str, ResourceScopeType] = {}
        self._validators: dict[str, Validator] = {}
        for name, declaration in (scope_types or {}).items():
            self.register(name, declaration)

    def register(self, name: str, declaration: dict[str, object]) -> None:
        """Register one declared scope type, failing fast on any problem."""
        if name in self._types:
            raise AppError(
                code=SERVICE_SCOPE_SCHEMA_INVALID,
                message=f"resource_scope_type declared more than once: {name}",
                status_code=422,
            )
        schema = declaration.get("schema", {})
        if not isinstance(schema, dict):
            raise AppError(
                code=SERVICE_SCOPE_SCHEMA_INVALID,
                message=f"resource_scope_type {name}: schema must be a JSON object",
                status_code=422,
            )
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise AppError(
                code=SERVICE_SCOPE_SCHEMA_INVALID,
                message=f"resource_scope_type {name}: invalid JSON Schema: {exc.message}",
                status_code=422,
            ) from exc
        self._types[name] = ResourceScopeType(
            name=name,
            schema=schema,
            schema_hash=hashlib.sha256(_canonical(schema).encode("utf-8")).hexdigest(),
        )
        self._validators[name] = jsonschema.Draft202012Validator(schema)

    def get(self, scope_type: str) -> ResourceScopeType | None:
        return self._types.get(scope_type)

    def validator(self, scope_type: str) -> Validator | None:
        """The compiled validator for a registered type, or ``None`` if unknown."""
        return self._validators.get(scope_type)

    def names(self) -> list[str]:
        return sorted(self._types)
