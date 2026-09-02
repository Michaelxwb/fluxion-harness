"""ADR-EXT-001 TASK-001 验收测试：Provider SPI Contract 形状 + PluginType enum 终态 + 契约落点。

覆盖：
- B-01: @runtime_checkable Protocol 拒绝缺方法假实现（真实边界=Protocol + 假实现 test double）
- B-02: PluginType enum 终态 = 6 成员；旧 TOOL/MEMORY/STORAGE 引用报 AttributeError
- B-03: Provider/RegistryProtocol Protocol 仅落点 plugins/contracts 或 plugins/providers，深度 ≤ 3

RED 约定（cf-task:start 规则 #7）：
- B-01/B-02 在 contracts.py 未扩展前必然失败（ImportError / AttributeError），属真实 RED。
- B-03 为静态落点测试；RED 阶段既有 ModelProvider/CapabilityProvider/ModelProviderRegistryProtocol
  已正确落点 contracts.py，故 B-03 断言本就成立——属"已有行为补测无法 RED"，
  记录原因，不伪造失败；GREEN 后 B-03 验证新增 6 SPI 全部落点正确。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from fluxion.plugins.contracts import (
    CapabilityDescriptor,
    ModelProvider,
    PluginType,
)

# --------------------------------------------------------------------------
# B-01: @runtime_checkable Protocol 拒绝缺方法假实现
# --------------------------------------------------------------------------


class _CompleteSemanticStore:
    async def store(self, tenant_id: str, user_id: str, record: dict) -> None:
        del tenant_id, user_id, record

    async def recall(
        self, tenant_id: str, user_id: str, query: str, top_k: int = 5
    ) -> list[dict]:
        del tenant_id, user_id, query, top_k
        return []

    async def search(self, tenant_id: str, user_id: str, filter: dict) -> list[dict]:
        del tenant_id, user_id, filter
        return []


class _IncompleteSemanticStore:
    async def store(self, tenant_id: str, user_id: str, record: dict) -> None:
        del tenant_id, user_id, record

    async def recall(
        self, tenant_id: str, user_id: str, query: str, top_k: int = 5
    ) -> list[dict]:
        del tenant_id, user_id, query, top_k
        return []

    # 故意缺 search → 必须被 isinstance 拒绝


def test_b01_semantic_store_isinstance_rejects_missing_search() -> None:
    """缺 search 的 SemanticStoreProvider 假实现 → isinstance 校验拒绝。"""
    from fluxion.plugins.contracts import SemanticStoreProvider

    assert isinstance(_CompleteSemanticStore(), SemanticStoreProvider)
    assert not isinstance(_IncompleteSemanticStore(), SemanticStoreProvider)


def test_b01_secret_provider_isinstance_rejects_missing_resolve() -> None:
    """缺 resolve 的 SecretProvider 假实现 → isinstance 拒绝（tenant_id 首参强制）。"""
    from fluxion.plugins.contracts import SecretProvider

    class _Complete:
        async def resolve(self, tenant_id: str, secret_ref: str) -> str:
            del tenant_id, secret_ref
            return ""

    class _Incomplete:
        async def get(self, tenant_id: str, secret_ref: str) -> str:  # 名字错
            del tenant_id, secret_ref
            return ""

    assert isinstance(_Complete(), SecretProvider)
    assert not isinstance(_Incomplete(), SecretProvider)


def test_b01_artifact_store_isinstance_rejects_missing_delete() -> None:
    """缺 delete 的 ArtifactStoreProvider → isinstance 拒绝。"""
    from fluxion.plugins.contracts import ArtifactStoreProvider

    class _Complete:
        async def put(self, tenant_id: str, namespace: str, key: str, value: bytes) -> None:
            del tenant_id, namespace, key, value

        async def get(self, tenant_id: str, namespace: str, key: str) -> bytes:
            del tenant_id, namespace, key
            return b""

        async def delete(self, tenant_id: str, namespace: str, key: str) -> None:
            del tenant_id, namespace, key

    class _Incomplete:
        async def put(self, tenant_id: str, namespace: str, key: str, value: bytes) -> None:
            del tenant_id, namespace, key, value

        async def get(self, tenant_id: str, namespace: str, key: str) -> bytes:
            del tenant_id, namespace, key
            return b""

        # 缺 delete

    assert isinstance(_Complete(), ArtifactStoreProvider)
    assert not isinstance(_Incomplete(), ArtifactStoreProvider)


def test_b01_tool_provider_alias_matches_capability_provider_shape() -> None:
    """ToolProvider = CapabilityProvider 形状（capabilities() -> list[CapabilityDescriptor]）。"""
    from fluxion.plugins.contracts import CapabilityProvider, ToolProvider

    class _Tool:
        def capabilities(self) -> list[CapabilityDescriptor]:
            return []

    assert isinstance(_Tool(), ToolProvider)
    assert isinstance(_Tool(), CapabilityProvider)
    # ToolProvider 是 CapabilityProvider 的统一模型别名
    assert ToolProvider is CapabilityProvider


def test_a009_tool_executor_replaces_tool_provider_kind() -> None:
    """ADR-A009（TASK-022）：PluginType.TOOL_EXECUTOR 取代 TOOL_PROVIDER。

    TOOL_PROVIDER 语义暗示「Tool 是 Plugin 类型」；TOOL_EXECUTOR 明确为
    Tool 的 SPI 实现载体——Plugin 提供 Tool 实现 ≠ Plugin 就是 Tool。
    """
    from fluxion.plugins.contracts import PluginType

    assert PluginType.TOOL_EXECUTOR == "tool_executor"
    assert not hasattr(PluginType, "TOOL_PROVIDER")


def test_b01_secret_resolve_signature_tenant_first() -> None:
    """SecretProvider.resolve 签名：tenant_id 首参强制（NFR-SEC-02 tenant scope）。"""
    from fluxion.plugins.contracts import SecretProvider

    params = list(inspect.signature(SecretProvider.resolve).parameters.keys())
    # self, tenant_id, secret_ref —— self 之后第一参必须是 tenant_id
    assert params[1] == "tenant_id"
    assert params[2] == "secret_ref"


# --------------------------------------------------------------------------
# B-02: PluginType enum 终态
# --------------------------------------------------------------------------


def test_b02_plugin_type_enum_terminal_members() -> None:
    expected = {
        PluginType.MODEL_PROVIDER,
        PluginType.TOOL_EXECUTOR,
        PluginType.ARTIFACT_STORE,
        PluginType.SEMANTIC_STORE,
        PluginType.SECRET_PROVIDER,
        PluginType.HOOK,
    }
    assert set(PluginType) == expected
    assert len(PluginType) == 6


def test_b02_old_plugin_type_members_removed() -> None:
    for removed in ("TOOL", "MEMORY", "STORAGE"):
        with pytest.raises(AttributeError):
            getattr(PluginType, removed)


def test_b02_plugin_type_values_stable() -> None:
    """值字符串与外部存储一致（resource spec_json / DB）。"""
    assert PluginType.MODEL_PROVIDER.value == "model_provider"
    assert PluginType.TOOL_EXECUTOR.value == "tool_executor"
    assert PluginType.ARTIFACT_STORE.value == "artifact_store"
    assert PluginType.SEMANTIC_STORE.value == "semantic_store"
    assert PluginType.SECRET_PROVIDER.value == "secret_provider"
    assert PluginType.HOOK.value == "hook"


# --------------------------------------------------------------------------
# B-03: Provider SPI Protocol 落点（static / architecture test）
# --------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _BACKEND_ROOT / "src" / "fluxion"
_ALLOWED_LOCATIONS = ("plugins/contracts.py", "plugins/providers/")
_PROVIDER_SUFFIXES = ("Provider", "RegistryProtocol")


def _provider_protocol_classes(root: Path) -> list[tuple[str, str]]:
    """扫描 src/fluxion 下所有 (Provider|RegistryProtocol) 且基类含 Protocol 的类定义。

    返回 [(相对 src/fluxion 的路径, 类名), ...]。
    """
    hits: list[tuple[str, str]] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(_SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not node.name.endswith(_PROVIDER_SUFFIXES):
                continue
            base_names = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if "Protocol" in base_names:
                hits.append((rel, node.name))
    return hits


def test_b03_provider_protocols_in_plugins_only_in_contracts_or_providers() -> None:
    """design verifier 范围=src/fluxion/plugins/（见 task Checklist）：
    plugins/ 内所有 (Provider|RegistryProtocol) Protocol 仅落点 contracts.py 或 providers/。
    services/approval.py:ApprovalProvider 不属于 6 统一模型 SPI（非 PluginType），不在本测试范围。
    """
    plugins = _SRC_ROOT / "plugins"
    assert plugins.is_dir(), "plugins 目录不存在"
    hits = _provider_protocol_classes(plugins)
    assert hits, "未发现任何 Provider/RegistryProtocol Protocol——落点测试无对象"
    violations = [
        (rel, name)
        for rel, name in hits
        if not rel.startswith(_ALLOWED_LOCATIONS)
    ]
    assert not violations, f"Provider Protocol 散落到 contracts/providers 之外: {violations}"


def test_b03_plugins_directory_depth_le_three() -> None:
    plugins = _SRC_ROOT / "plugins"
    assert plugins.is_dir(), "plugins 目录不存在"
    for path in plugins.rglob("*.py"):
        rel = path.relative_to(plugins)
        depth = len(rel.parts)
        assert depth <= 3, f"plugins 子包深度 >3: {rel} (depth={depth})"


# 6 个统一模型 SPI 的精确落点：canonical 类必须定义在 plugins/contracts 模块。
# 等价于"不散落 kernel/services/runtime/api"——用 __module__ 精确断言，避免后缀通配误伤
# services/approval.py:ApprovalProvider 等非 SPI 的内部 Protocol。
_SIX_SPIS = (
    "ModelProvider",
    "ToolProvider",
    "SemanticStoreProvider",
    "ArtifactStoreProvider",
    "SecretProvider",
    "HookRegistryProtocol",
)


def test_b03_six_spis_defined_in_contracts_only() -> None:
    from fluxion.plugins import contracts

    for name in _SIX_SPIS:
        cls = getattr(contracts, name, None)
        assert cls is not None, f"{name} 未定义在 plugins/contracts"
        assert cls.__module__ == "fluxion.plugins.contracts", (
            f"{name} canonical 定义不在 plugins/contracts：{cls.__module__}"
        )


# 用于静态确认 ModelProvider 仍是参考实现（未被破坏）
def test_b03_model_provider_remains_reference_contract() -> None:
    assert isinstance(ModelProvider, type)
    assert ModelProvider.__module__ == "fluxion.plugins.contracts"
