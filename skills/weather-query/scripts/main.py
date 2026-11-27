#!/usr/bin/env python3
"""Read-only city weather via Open-Meteo; no platform session or secret required."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from typing import Any

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_DAYS = 3
MAX_DAYS = 7

WEATHER_CODES: dict[int, str] = {
    0: "晴",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}

GetJson = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class WeatherError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_json(url: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        raise WeatherError(
            "WEATHER_UPSTREAM_ERROR", f"weather upstream request failed: {type(exc).__name__}"
        ) from exc
    if response.status_code >= 400:
        raise WeatherError(
            "WEATHER_UPSTREAM_ERROR", f"weather upstream returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherError(
            "WEATHER_UPSTREAM_ERROR", "weather upstream returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise WeatherError(
            "WEATHER_UPSTREAM_ERROR", "weather upstream returned a non-object payload"
        )
    return payload


def _condition(code: object) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "未知天气"
    return WEATHER_CODES.get(value, f"未知天气({value})")


def _number(value: object, digits: int = 1) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate(city: object, days: object) -> tuple[str, int]:
    if not isinstance(city, str) or not city.strip():
        raise WeatherError("CITY_REQUIRED", "city must be a non-empty string")
    normalized_city = city.strip()
    if len(normalized_city) > 80:
        raise WeatherError("CITY_INVALID", "city must be at most 80 characters")
    if isinstance(days, bool) or not isinstance(days, int):
        raise WeatherError("DAYS_INVALID", "days must be an integer")
    if not 1 <= days <= MAX_DAYS:
        raise WeatherError("DAYS_INVALID", f"days must be between 1 and {MAX_DAYS}")
    return normalized_city, days


def build_report(
    city: object,
    days: object = DEFAULT_DAYS,
    *,
    get_json: GetJson = get_json,
) -> dict[str, Any]:
    normalized_city, forecast_days = _validate(city, days)
    geocode = get_json(
        GEOCODE_URL,
        {"name": normalized_city, "count": 1, "language": "zh", "format": "json"},
    )
    results = geocode.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise WeatherError("CITY_NOT_FOUND", f"unknown city: {normalized_city}")
    place = results[0]
    latitude, longitude = place.get("latitude"), place.get("longitude")
    if latitude is None or longitude is None:
        raise WeatherError("CITY_NOT_FOUND", f"city has no coordinates: {normalized_city}")

    forecast = get_json(
        FORECAST_URL,
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "precipitation,weather_code,wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,wind_speed_10m_max"
            ),
            "timezone": "auto",
            "forecast_days": forecast_days,
        },
    )
    current = forecast.get("current") if isinstance(forecast.get("current"), dict) else {}
    daily = forecast.get("daily") if isinstance(forecast.get("daily"), dict) else {}
    dates = daily.get("time") if isinstance(daily.get("time"), list) else []
    codes = daily.get("weather_code") if isinstance(daily.get("weather_code"), list) else []
    highs = (
        daily.get("temperature_2m_max")
        if isinstance(daily.get("temperature_2m_max"), list)
        else []
    )
    lows = (
        daily.get("temperature_2m_min")
        if isinstance(daily.get("temperature_2m_min"), list)
        else []
    )
    probabilities = (
        daily.get("precipitation_probability_max")
        if isinstance(daily.get("precipitation_probability_max"), list)
        else []
    )
    winds = (
        daily.get("wind_speed_10m_max")
        if isinstance(daily.get("wind_speed_10m_max"), list)
        else []
    )

    rows: list[dict[str, Any]] = []
    for index, date in enumerate(dates):
        rows.append(
            {
                "date": date,
                "condition": _condition(codes[index] if index < len(codes) else None),
                "high_c": _number(highs[index] if index < len(highs) else None),
                "low_c": _number(lows[index] if index < len(lows) else None),
                "precipitation_probability_pct": _integer(
                    probabilities[index] if index < len(probabilities) else None
                ),
                "wind_kmh_max": _number(winds[index] if index < len(winds) else None),
            }
        )

    return {
        "ok": True,
        "city": place.get("name") or normalized_city,
        "country": place.get("country") or "",
        "timezone": forecast.get("timezone") or place.get("timezone") or "",
        "current": {
            "time": current.get("time"),
            "condition": _condition(current.get("weather_code")),
            "temperature_c": _number(current.get("temperature_2m")),
            "apparent_c": _number(current.get("apparent_temperature")),
            "humidity_pct": _integer(current.get("relative_humidity_2m")),
            "precipitation_mm": _number(current.get("precipitation")),
            "wind_kmh": _number(current.get("wind_speed_10m")),
        },
        "forecast": rows,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise WeatherError("INPUT_INVALID", "input must be a JSON object")
        report = build_report(payload.get("city"), payload.get("days", DEFAULT_DAYS))
    except WeatherError as exc:
        print(
            json.dumps(
                {"error": {"code": exc.code, "message": exc.message}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"error": {"code": "INPUT_INVALID", "message": str(exc)}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
