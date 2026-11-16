"""Pure agent-core logic: no database, no Redis (see tests/conftest.py)."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.no_datastore)
