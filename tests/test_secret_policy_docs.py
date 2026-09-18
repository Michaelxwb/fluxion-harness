from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "docs/00-详细设计索引与设计基线.md",
    "docs/01-总体架构与部署详细设计.md",
    "docs/02-核心领域与数据库详细设计.md",
    "docs/03-Console-Platform详细设计.md",
    "docs/05-IM-Gateway详细设计.md",
    "docs/07-跨模块接口与协议详细设计.md",
    "docs/08-关键流程时序与状态机详细设计.md",
    "docs/12-项目平台适配与Session详细设计.md",
]
PATTERNS = (
    re.compile(r"secret://"),
    re.compile(r"SecretProvider"),
    re.compile(r"Secret Provider"),
    re.compile(r"secret_ref"),
)


def test_docs_have_no_secret_ref_residue() -> None:
    offenders: list[str] = []
    for relative in DOCS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                offenders.append(f"{relative}:{lineno}: {line.strip()[:100]}")
    assert offenders == [], "设计基线仍含 SecretRef 残留:\n" + "\n".join(offenders)


def test_secret_rule_is_plaintext_policy() -> None:
    rule = (ROOT / ".code-flow/specs/secret/harness-secret.md").read_text(encoding="utf-8")
    assert "密钥明文存于各 Owner 表" in rule
    assert "不再使用 `secret_ref`/SecretProvider" in rule
    assert "不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应" in rule


def test_platform_rule_has_no_secret_ref_clause() -> None:
    rule = (ROOT / ".code-flow/specs/platform/harness-project-platform.md").read_text(encoding="utf-8")
    assert "凭据明文存于凭据表并由主键引用" in rule
    assert "凭据只存 SecretRef" not in rule
