from pathlib import Path


def test_postgres_repositories_explicitly_filter_soft_deleted_rows() -> None:
    root = Path(__file__).parents[2] / "adapters" / "postgres"
    repository_files = [
        path for path in root.glob("*repository*.py") if path.name not in {"repository.py"}
    ] + list(root.glob("repositories.py"))

    offenders: list[str] = []
    for path in repository_files:
        text = path.read_text(encoding="utf-8")
        if "select(" in text and "is_deleted" not in text:
            offenders.append(path.name)

    assert not offenders, (
        f"repositories with SELECT must include the framework soft-delete predicate: {offenders}"
    )
