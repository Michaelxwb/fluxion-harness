"""TASK-006（FEAT-05）Runtime 术语锚点验收测试（S-08）。"""

from __future__ import annotations

from fluxion.runtime import RuntimeInstance, RuntimePool


def test_S08_runtime_pool_symbols_importable() -> None:
    assert RuntimePool().name == "default"
    assert RuntimePool(name="shared").name == "shared"


def test_S08_runtime_instance_symbol() -> None:
    inst = RuntimeInstance(name="fluxion-859b9949d6-6k986")
    assert inst.name == "fluxion-859b9949d6-6k986"
