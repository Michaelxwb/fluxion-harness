"""weather-query 包：可导入、契约可校验、Open-Meteo 响应投影有界且不伪造缺失值。"""

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
SKILL = ROOT / "skills/weather-query"
SCRIPT = SKILL / "scripts/main.py"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def _script() -> dict[str, Any]:
    return runpy.run_path(str(SCRIPT))


def test_weather_package_loads_and_declares_the_invocation_contract() -> None:
    package = SkillPackage.load(SKILL)
    assert package.manifest.name == "weather-query"
    assert package.manifest.version == "1.0.3"
    contract = package.manifest.invocation_contract
    assert contract is not None
    assert contract.invocation == "stdin"
    assert contract.contract_schema["required"] == ["city"]
    assert contract.contract_schema["properties"]["days"]["maximum"] == 7

    with validated_package(_zip_directory(SKILL)) as validated:
        assert validated.manifest.name == "weather-query"


def test_weather_report_projects_current_and_daily_values() -> None:
    calls: list[str] = []

    def fake_get(url: str, _params: dict[str, Any]) -> dict[str, Any]:
        calls.append(url)
        if url == GEOCODE_URL:
            return {
                "results": [
                    {
                        "name": "北京",
                        "country": "中国",
                        "latitude": 39.9042,
                        "longitude": 116.4074,
                        "timezone": "Asia/Shanghai",
                    }
                ]
            }
        assert url == FORECAST_URL
        return {
            "timezone": "Asia/Shanghai",
            "current": {
                "time": "2026-11-27T21:00",
                "weather_code": 0,
                "temperature_2m": 8.14,
                "apparent_temperature": 5.36,
                "relative_humidity_2m": 42,
                "precipitation": 0,
                "wind_speed_10m": 12.34,
            },
            "daily": {
                "time": ["2026-11-27", "2026-11-28"],
                "weather_code": [0, 61],
                "temperature_2m_max": [11, 9.5],
                "temperature_2m_min": [1.2, 2.8],
                "precipitation_probability_max": [5, 80],
                "wind_speed_10m_max": [18.4, 22.1],
            },
        }

    report = _script()["build_report"]("北京", 2, get_json=fake_get)

    assert calls == [GEOCODE_URL, FORECAST_URL]
    assert report["ok"] is True
    assert report["city"] == "北京"
    assert report["current"] == {
        "time": "2026-11-27T21:00",
        "condition": "晴",
        "temperature_c": 8.1,
        "apparent_c": 5.4,
        "humidity_pct": 42,
        "precipitation_mm": 0.0,
        "wind_kmh": 12.3,
    }
    assert report["forecast"] == [
        {
            "date": "2026-11-27",
            "condition": "晴",
            "high_c": 11.0,
            "low_c": 1.2,
            "precipitation_probability_pct": 5,
            "wind_kmh_max": 18.4,
        },
        {
            "date": "2026-11-28",
            "condition": "小雨",
            "high_c": 9.5,
            "low_c": 2.8,
            "precipitation_probability_pct": 80,
            "wind_kmh_max": 22.1,
        },
    ]


def test_weather_rejects_unknown_city_without_inventing_weather() -> None:
    script = _script()

    def fake_get(_url: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"results": []}

    with pytest.raises(script["WeatherError"]) as exc:
        script["build_report"]("不存在市", 3, get_json=fake_get)
    assert exc.value.code == "CITY_NOT_FOUND"


@pytest.mark.parametrize("days", [0, 8, True, "3", 3.5])
def test_weather_rejects_invalid_day_ranges_before_network(days: object) -> None:
    script = _script()
    called = False

    def fake_get(_url: str, _params: dict[str, Any]) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    with pytest.raises(script["WeatherError"]) as exc:
        script["build_report"]("北京", days, get_json=fake_get)
    assert exc.value.code == "DAYS_INVALID"
    assert called is False
