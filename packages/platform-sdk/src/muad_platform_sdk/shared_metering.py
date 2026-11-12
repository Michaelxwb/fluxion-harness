"""Lazy shared-metering assembly, independent of backend implementation."""
from collections.abc import Callable

from muad_contracts.shared_egress import FrozenIdentityBudgets, SharedEgressPolicy


def create_shared_metering[Meter](
    policy: SharedEgressPolicy, backend_factory: Callable[[SharedEgressPolicy], Meter],
    *, budget: FrozenIdentityBudgets | None = None,
) -> Meter | None:
    """No configured rule means no metering client, connection or implicit limit."""
    if not policy.enabled and not (budget is not None and budget.enabled):
        return None
    return backend_factory(policy)
