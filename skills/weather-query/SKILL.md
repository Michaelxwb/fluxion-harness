---
name: weather-query
description: "查询城市实时天气与未来多日预报（免密钥 Open-Meteo 数据源，无需平台登录态）。Trigger on: 查天气/天气怎么样/今天冷不冷/明天会下雨吗/未来三天天气/气温/降水概率/风力/weather/forecast/temperature。"
invocation_contract:
  version: 1
  invocation: stdin
  schema:
    type: object
    properties:
      city:
        type: string
        minLength: 1
        maxLength: 80
        description: "城市名称，例如 北京、上海、广州；不要猜测用户没有给出的城市。"
      days:
        type: integer
        minimum: 1
        maximum: 7
        default: 3
        description: "预报天数；默认 3 天，最多 7 天。"
    required: ["city"]
    additionalProperties: false
---

# weather-query

只读天气查询：先按城市名做地理编码，再读取 Open-Meteo 的实时天气与逐日预报。

## 输入

模型通过统一执行入口调用：

```text
execute(target="skill", skill_key="weather-query", input={"city": "北京", "days": 3})
```

- `city` 必填。用户没有给出城市时先问清楚，不要猜默认城市。
- `days` 可选，范围 1–7，默认 3。

## 输出

stdout 返回一个 JSON 对象：

```json
{
  "ok": true,
  "city": "北京",
  "country": "中国",
  "timezone": "Asia/Shanghai",
  "current": {
    "time": "2026-11-27T21:00",
    "condition": "晴",
    "temperature_c": 8.1,
    "apparent_c": 5.4,
    "humidity_pct": 42,
    "precipitation_mm": 0.0,
    "wind_kmh": 12.3
  },
  "forecast": [
    {
      "date": "2026-11-27",
      "condition": "晴",
      "high_c": 11.0,
      "low_c": 1.2,
      "precipitation_probability_pct": 5,
      "wind_kmh_max": 18.4
    }
  ]
}
```

数据源：`https://geocoding-api.open-meteo.com` 与 `https://api.open-meteo.com`。该 Skill 只做只读 GET，不写平台数据；失败时以非零退出码和结构化 `error.code` 返回，不伪造天气结果。

## 本地检查

```bash
echo '{"city":"北京","days":3}' | python3 skills/weather-query/scripts/main.py
```
