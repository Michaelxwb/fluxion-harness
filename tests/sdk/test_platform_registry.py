import pytest
from muad_platform_sdk import (
    PlatformAdapter,
    PlatformAdapterAlreadyRegistered,
    PlatformAdapterNotFound,
    PlatformAdapterRegistry,
)


def test_register_get_and_list_keep_registration_order(adapter: PlatformAdapter) -> None:
    registry = PlatformAdapterRegistry()
    registry.register(adapter)

    assert registry.get("dummy") is adapter
    assert registry.list() == (adapter,)


def test_duplicate_adapter_key_is_rejected(adapter: PlatformAdapter) -> None:
    registry = PlatformAdapterRegistry()
    registry.register(adapter)

    with pytest.raises(PlatformAdapterAlreadyRegistered) as error:
        registry.register(adapter)

    assert error.value.key == "dummy"
    assert isinstance(error.value, ValueError)


def test_missing_adapter_raises_typed_error() -> None:
    registry = PlatformAdapterRegistry()

    with pytest.raises(PlatformAdapterNotFound) as error:
        registry.get("does-not-exist")

    assert error.value.key == "does-not-exist"
    assert isinstance(error.value, LookupError)


def test_empty_registry_lists_nothing() -> None:
    registry = PlatformAdapterRegistry()

    assert registry.list() == ()
