SUPPORTED_LOCALES = ("zh-CN", "en-US")


def normalize_locale(value: str | None, default: str = "zh-CN") -> str:
    if not value:
        return default
    lower = value.strip().lower()
    if lower.startswith("zh"):
        return "zh-CN"
    if lower.startswith("en"):
        return "en-US"
    return default
