"""平台因子加载（RULE-secret-002）。

因子来自环境变量（生产由 K8s Secret 注入），**不落库、不回显、不写日志**。
一个平台缺失或损坏只影响它自己；这里把「缺失」与「损坏」区分开：
缺失返回 ``None``（调用方据此让该通道 503），损坏在启动期抛错。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key, load_pem_public_key
from muad_contracts import EXTERNAL_CHANNEL_NAME_PATTERN, is_valid_external_channel_name
from muad_logging import get_logger

_LOGGER = get_logger(__name__)

#: 平台元数据键名。``identity_keys`` 决定身份指纹；``identity_field_labels`` 只用于
#: Console 展示，缺失时前端回退显示 claim 名；``issue_time_keys`` / ``issue_time_unit``
#: 决定时效字段与单位；``channel`` 是该平台归属的外部断言通道名（决定路由与入口归属）。
_KNOWN_KEYS = (
    "public_key",
    "issuer",
    "audience",
    "salt",
    "identity_keys",
    "identity_field_labels",
    "display_name",
    "issue_time_keys",
    "issue_time_unit",
    "channel",
)

#: 允许的时效时间单位。``ms`` 表示上游字段是 Unix 毫秒，``s`` 表示 Unix 秒。
_ISSUE_TIME_UNITS = frozenset({"ms", "s"})

#: 时效字段与单位的默认值——等于 MSSW 上游的实际口径（``login_time``/``time`` 毫秒）。
#: 未在 Secret 里覆盖时按此行为，保证 MSSWWEB 现有部署零变化。
_DEFAULT_ISSUE_TIME_KEYS = ("login_time", "time")
_DEFAULT_ISSUE_TIME_UNIT = "ms"
#: 通道名默认值：与当前唯一外部通道一致。
_DEFAULT_CHANNEL = "MSSWWEB"



class PlatformConfigError(RuntimeError):
    """平台因子存在但无法解析 —— 启动期显式失败，不留到首个请求。"""


@dataclass(frozen=True, slots=True)
class ExternalPlatformConfig:
    """一个外部平台的验签配置。``repr`` 不暴露任何因子值。"""

    platform_key: str
    public_key: RSAPublicKey = field(repr=False)
    issuer: str = field(repr=False)
    audience: str = field(repr=False)
    #: AES key 种子：右侧补 0x00 到 32 字节，长度须 ≤ 32（见 01-jwt-spec）。
    salt: bytes = field(repr=False)
    identity_keys: tuple[str, ...]
    identity_field_labels: dict[str, str]
    display_name: str
    #: 时效 claim 候选键（按序取第一个可用值）与单位：``ms``/``s``。
    issue_time_keys: tuple[str, ...] = _DEFAULT_ISSUE_TIME_KEYS
    issue_time_unit: str = _DEFAULT_ISSUE_TIME_UNIT
    #: 该平台归属的外部断言通道名（决定 Gateway 路由前缀与入口 `bot_account.channel`）。
    channel: str = _DEFAULT_CHANNEL


def _decode_public_key(raw: str) -> RSAPublicKey:
    """接受 PEM 或 base64(DER)。仅允许 RSA —— 其它算法直接判为配置错误。"""
    text = raw.strip()
    try:
        if "BEGIN" in text:
            key = load_pem_public_key(text.encode("utf-8"))
        else:
            key = load_der_public_key(base64.b64decode(text))
    except Exception as exc:  # noqa: BLE001 - 统一转成配置错误，不带因子值
        raise PlatformConfigError("public_key is not a valid RSA public key") from exc
    if not isinstance(key, RSAPublicKey):
        raise PlatformConfigError("public_key must be RSA")
    return key


def _parse_one(platform_key: str, raw: object) -> ExternalPlatformConfig:
    if not isinstance(raw, dict):
        raise PlatformConfigError(f"platform {platform_key}: entry must be an object")

    missing = [key for key in ("public_key", "issuer", "audience", "salt") if not raw.get(key)]
    if missing:
        raise PlatformConfigError(f"platform {platform_key}: missing {', '.join(missing)}")

    salt_text = str(raw["salt"])
    salt = salt_text.encode("utf-8")
    if len(salt) > 32:
        raise PlatformConfigError(f"platform {platform_key}: salt must be at most 32 bytes")

    identity_keys = raw.get("identity_keys") or ["expert_id", "tenant_id"]
    if not isinstance(identity_keys, list) or not identity_keys:
        raise PlatformConfigError(f"platform {platform_key}: identity_keys must be a non-empty list")
    if not all(isinstance(item, str) and item for item in identity_keys):
        raise PlatformConfigError(f"platform {platform_key}: identity_keys must be non-empty strings")

    labels = raw.get("identity_field_labels") or {}
    if not isinstance(labels, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in labels.items()
    ):
        raise PlatformConfigError(f"platform {platform_key}: identity_field_labels must be a string map")

    issue_time_keys = raw.get("issue_time_keys") or list(_DEFAULT_ISSUE_TIME_KEYS)
    if not isinstance(issue_time_keys, list) or not issue_time_keys:
        raise PlatformConfigError(f"platform {platform_key}: issue_time_keys must be a non-empty list")
    if not all(isinstance(item, str) and item for item in issue_time_keys):
        raise PlatformConfigError(f"platform {platform_key}: issue_time_keys must be non-empty strings")

    issue_time_unit = str(raw.get("issue_time_unit") or _DEFAULT_ISSUE_TIME_UNIT)
    if issue_time_unit not in _ISSUE_TIME_UNITS:
        raise PlatformConfigError(
            f"platform {platform_key}: issue_time_unit must be one of {sorted(_ISSUE_TIME_UNITS)}"
        )

    channel = str(raw.get("channel") or _DEFAULT_CHANNEL).strip()
    if not channel:
        raise PlatformConfigError(f"platform {platform_key}: channel must be a non-empty string")
    if not is_valid_external_channel_name(channel):
        raise PlatformConfigError(
            f"platform {platform_key}: channel {channel!r} must match "
            f"{EXTERNAL_CHANNEL_NAME_PATTERN} (alphanumeric ending with 'web')"
        )

    return ExternalPlatformConfig(
        platform_key=platform_key,
        public_key=_decode_public_key(str(raw["public_key"])),
        issuer=str(raw["issuer"]),
        audience=str(raw["audience"]),
        salt=salt,
        identity_keys=tuple(identity_keys),
        identity_field_labels=dict(labels),
        display_name=str(raw.get("display_name") or platform_key),
        issue_time_keys=tuple(issue_time_keys),
        issue_time_unit=issue_time_unit,
        channel=channel,
    )


def load_platform_configs(platforms_json: str | None) -> dict[str, ExternalPlatformConfig]:
    """解析 ``external_assertion_platforms_json``。

    空值或空对象表示「一个平台都没配」—— 合法状态，对应通道认证 503。
    """
    if not platforms_json or not platforms_json.strip():
        return {}
    try:
        payload = json.loads(platforms_json)
    except json.JSONDecodeError as exc:
        raise PlatformConfigError("external_assertion_platforms_json is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PlatformConfigError("external_assertion_platforms_json must be an object")

    configs: dict[str, ExternalPlatformConfig] = {}
    for platform_key, raw in payload.items():
        if not isinstance(platform_key, str) or not platform_key:
            raise PlatformConfigError("platform keys must be non-empty strings")
        configs[platform_key] = _parse_one(platform_key, raw)

    unknown = sorted({k for cfg in payload.values() if isinstance(cfg, dict) for k in cfg} - set(_KNOWN_KEYS))
    if unknown:
        _LOGGER.warning("external assertion config has unknown keys: %s", unknown)
    return configs


@lru_cache(maxsize=1)
def get_platform_configs() -> dict[str, ExternalPlatformConfig]:
    """进程级缓存：因子在启动时加载，变更需重新部署（RULE-secret-002）。"""
    from muad_common.settings import process_settings

    return load_platform_configs(process_settings().external_assertion_platforms_json)
