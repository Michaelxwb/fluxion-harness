"""Exact event → model audit linkage, scoped to one runner execution."""

import uuid


class ModelCallTrace:
    def __init__(self) -> None:
        self.audit_id: uuid.UUID | None = None

    def begin(self) -> dict[str, str]:
        self.audit_id = uuid.uuid4()
        return self.event_fields()

    def event_fields(self) -> dict[str, str]:
        return {"model_audit_id": str(self.audit_id)} if self.audit_id is not None else {}
