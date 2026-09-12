"""S-06: user_agent_binding 已删除（V1.7 D06）回归门禁。

路由只走 ChannelAccount.default_agent + Routing Policy；任何残留引用都必须失败。
"""

from pathlib import Path

ROOT = Path(__file__).parents[2]
CODE_DIRS = ("framework", "adapters", "apps", "migrations", "tests")
FRONTEND_SRC = ROOT / "frontend" / "console" / "src"


def _hits(token: str) -> list[str]:
    hits: list[str] = []
    for dirname in CODE_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or path.name == "test_binding_removal.py":
                continue
            if token in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(ROOT)))
    if FRONTEND_SRC.exists():
        for ext in ("*.ts", "*.tsx"):
            for path in FRONTEND_SRC.rglob(ext):
                if token in path.read_text(encoding="utf-8"):
                    hits.append(str(path.relative_to(ROOT)))
    return hits


def test_no_user_agent_binding_references_in_code() -> None:
    assert _hits("user_agent_binding") == []


def test_migration_chain_has_single_head() -> None:
    versions = ROOT / "migrations" / "versions"
    revs: dict[str, str | None] = {}
    for path in versions.glob("*.py"):
        ns: dict[str, object] = {}
        exec(path.read_text(encoding="utf-8"), ns)  # noqa: S102 - controlled migration files
        revision = ns.get("revision")
        down_revision = ns.get("down_revision")
        revs[str(revision)] = str(down_revision) if down_revision is not None else None
    assert revs.get("0001") is None
    assert len(revs) == 1, f"V1.14 is a single-baseline chain, found: {sorted(revs)}"
    assert "0001" not in revs.values(), "0001 must be the migration head"


def test_routing_goes_through_default_agent() -> None:
    models = (ROOT / "adapters" / "postgres" / "models.py").read_text(encoding="utf-8")
    assert "default_agent" in models
