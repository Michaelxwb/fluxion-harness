"""Local MSSP asset-query fixture: contract, truncation and full CSV export."""

from __future__ import annotations

import io
import runpy
import zipfile
from pathlib import Path
from typing import Any

import pytest
from muad_console_platform.infrastructure.skill_validator import validated_package
from muad_skill_sdk.skill_package import SkillPackage

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/mssp-asset-query-local"
SCRIPT = SKILL / "scripts/run.py"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def _script() -> dict[str, Any]:
    return runpy.run_path(str(SCRIPT))


def _asset(index: int) -> dict[str, Any]:
    return {
        "asset": f"10.60.0.{index}",
        "hostname": f"bulk-host-{index:04d}",
        "business_name": "批量业务",
        "asset_type": "server",
        "service_status": 1,
        "is_alive": 1,
        "first_time": 1735660800,
        "update_time": 1735660800,
    }


def test_asset_package_loads_and_requires_company_ids() -> None:
    package = SkillPackage.load(SKILL)
    assert package.manifest.name == "mssp-asset-query-local"
    assert package.manifest.version == "1.1.0"
    contract = package.manifest.invocation_contract
    assert contract is not None
    assert contract.contract_schema["required"] == ["company_ids"]

    with validated_package(_zip_directory(SKILL)) as validated:
        assert validated.manifest.name == "mssp-asset-query-local"


def test_default_query_reports_truncation_and_next_offset() -> None:
    script = _script()
    rows = [_asset(index) for index in range(1, 5)]

    def fake_query(
        _company_id: str, *, offset: int, limit: int, keyword: str
    ) -> dict[str, Any]:
        return {"total": len(rows), "list": rows[offset : offset + limit]}

    result = script["build_result"](
        {"company_ids": ["9001"], "limit": 2},
        lookup=lambda _company_id: "批量资产客户",
        query=fake_query,
    )

    assert result["ok"] is True
    assert result["total"] == 4
    assert result["returned"] == 2
    assert result["truncated"] is True
    assert result["next_offset"] == 2
    assert [row["asset"] for row in result["assets"]] == ["10.60.0.1", "10.60.0.2"]


def test_export_writes_complete_csv_instead_of_printing_all_rows(tmp_path: Path) -> None:
    script = _script()
    rows = [_asset(index) for index in range(1, 8)]

    def fake_query(
        _company_id: str, *, offset: int, limit: int, keyword: str
    ) -> dict[str, Any]:
        return {"total": len(rows), "list": rows[offset : offset + limit]}

    result = script["build_result"](
        {"company_ids": ["9001"], "export": True},
        lookup=lambda _company_id: "批量资产客户",
        query=fake_query,
        output_dir=tmp_path,
    )

    assert result["total"] == 7
    assert result["returned"] == 7
    assert result["truncated"] is False
    assert len(result["files"]) == 1
    exported = tmp_path / result["files"][0]["path"]
    assert exported.exists()
    assert result["files"][0]["rows"] == 7

    lines = exported.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("company_id,company_name,asset,")
    assert len(lines) == 8
    assert "10.60.0.7" in lines[-1]


def test_export_reports_duplicate_and_missing_rows(tmp_path: Path) -> None:
    script = _script()
    rows = [
        _asset(1),
        _asset(1),
        _asset(2),
        _asset(3),
        _asset(4),
    ]

    def fake_query(
        _company_id: str, *, offset: int, limit: int, keyword: str
    ) -> dict[str, Any]:
        return {"total": 5, "list": rows[offset : offset + limit]}

    result = script["build_result"](
        {"company_ids": ["9002"], "export": True},
        lookup=lambda _company_id: "分页异常客户",
        query=fake_query,
        output_dir=tmp_path,
    )

    assert result["incomplete"] is True
    assert {item["code"] for item in result["issues"]} == {"MISSING_ROWS", "DUPLICATE_ROWS"}
    assert result["files"][0]["rows"] == 4


def test_unknown_platform_is_rejected_before_http() -> None:
    script = _script()
    called = False

    def fake_query(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"total": 0, "list": []}

    with pytest.raises(script["AssetQueryError"]) as exc:
        script["build_result"](
            {"company_ids": ["1001"], "platform": "xdr"},
            lookup=lambda _company_id: "客户",
            query=fake_query,
        )
    assert exc.value.code == "PLATFORM_NOT_CONFIGURED"
    assert called is False


def test_unknown_company_fails_before_returning_assets() -> None:
    script = _script()
    with pytest.raises(script["AssetQueryError"]) as exc:
        script["build_result"](
            {"company_ids": ["9999"]},
            lookup=lambda _company_id: None,
            query=lambda *_args, **_kwargs: {"total": 0, "list": []},
        )
    assert exc.value.code == "COMPANY_NOT_FOUND"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"company_ids": []},
        {"company_ids": ["abc"]},
        {"company_ids": ["1001"], "limit": 0},
        {"company_ids": ["1001"], "limit": 201},
        {"company_ids": ["1001"], "offset": -1},
        {"company_ids": ["1001"], "export": "yes"},
    ],
)
def test_invalid_input_is_rejected_before_http(payload: object) -> None:
    script = _script()
    called = False

    def fake_query(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"total": 0, "list": []}

    with pytest.raises(script["AssetQueryError"]):
        script["build_result"](
            payload,
            lookup=lambda _company_id: "客户",
            query=fake_query,
        )
    assert called is False
