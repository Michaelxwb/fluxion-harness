"""artifact:// URI 引用模型（Phase 5 TASK-001，design §3.3 / 规则 6/10）。

`artifact://{tenant}/{namespace}/{key}@{version}` —— Resource spec 与
ExecutionSnapshot 中的版本化引用形态（pin 语义：published 不可变、回滚选历史版本）。
"""

from __future__ import annotations

from dataclasses import dataclass

from fluxion.resources.contracts import ARTIFACT_REF_PATTERN

# 规范 grammar 由契约层（resources/contracts.py）定义；此处复用，保持
# snapshot pin 校验与 plugin 侧解析一致（Kernel 不依赖 Plugin 方向不变）。
_REF_PATTERN = ARTIFACT_REF_PATTERN


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    tenant_id: str
    namespace: str
    key: str
    version: str

    def __str__(self) -> str:
        return build_artifact_ref(
            tenant_id=self.tenant_id,
            namespace=self.namespace,
            key=self.key,
            version=self.version,
        )


def build_artifact_ref(tenant_id: str, namespace: str, key: str, version: str) -> str:
    """组装 `artifact://{tenant}/{namespace}/{key}@{version}`。"""
    return f"artifact://{tenant_id}/{namespace}/{key}@{version}"


def parse_artifact_ref(ref: str) -> tuple[str, str, str, str]:
    """解析 artifact URI → (tenant_id, namespace, key, version)；非法形态抛 ValueError。"""
    match = _REF_PATTERN.match(ref)
    if match is None:
        raise ValueError(f"非法 artifact 引用: {ref}（须为 artifact://{{tenant}}/{{ns}}/{{key}}@{{version}}）")
    tenant_id, namespace, key, version = match.groups()
    return tenant_id, namespace, key, version


def parse_ref(ref: str) -> ArtifactRef:
    """结构化解析（ArtifactRef dataclass）。"""
    tenant_id, namespace, key, version = parse_artifact_ref(ref)
    return ArtifactRef(key=key, namespace=namespace, tenant_id=tenant_id, version=version)
