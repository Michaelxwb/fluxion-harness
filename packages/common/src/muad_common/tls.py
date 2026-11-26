"""TLS 版本上限的共享口径：配置值白名单 + 解析（默认不设限）。

只用于**显式降级**：例如内网安全设备把 TLS 1.3 握手 RST 掉时，把某个出站客户端限制在
TLS 1.2。这里只提供「配置值 → `ssl.TLSVersion`」的映射；构造 `SSLContext` 的一方仍须保持
证书校验开启（`check_hostname` / `CERT_REQUIRED`），降级只降协议版本，不放松校验。
"""

from __future__ import annotations

import ssl

#: 合法配置值 → `ssl.TLSVersion`。设置校验与客户端构造共用这一份，避免两处各写一遍。
TLS_MAX_VERSIONS: dict[str, ssl.TLSVersion] = {
    "TLSv1_2": ssl.TLSVersion.TLSv1_2,
    "TLSv1_3": ssl.TLSVersion.TLSv1_3,
}


def parse_max_tls_version(value: str | None) -> ssl.TLSVersion | None:
    """`None`/空串 = 不设上限；其它值必须命中白名单，否则在**启动期**失败。"""
    if value is None or value == "":
        return None
    try:
        return TLS_MAX_VERSIONS[value]
    except KeyError:
        allowed = ", ".join(TLS_MAX_VERSIONS)
        raise ValueError(f"unknown TLS max version {value!r}; allowed: {allowed}") from None
