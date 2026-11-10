"""Lazy shared-metering assembly, independent of backend implementation."""
from collections.abc import Callable

from muad_contracts.shared_egress import SharedEgressPolicy


def create_shared_metering[Meter](
    policy: SharedEgressPolicy, backend_factory: Callable[[SharedEgressPolicy], Meter],
) -> Meter | None:
    """No configured rule means no metering client, connection or implicit limit."""
    if not policy.enabled:
        return None
    return backend_factory(policy)
