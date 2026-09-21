from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urljoin

from ..types import (
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PreparedRequest,
    SecretValue,
    SessionMode,
)

_DEFAULT_TIMEOUT_MS = 3000
_ALLOWED_AUTH_SCHEMES = ("none", "bearer", "basic")
_CONFIG_KEYS = frozenset({"auth_scheme", "auth_header", "timeout_ms"})

PLATFORM_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "auth_scheme": {"type": "string", "enum": list(_ALLOWED_AUTH_SCHEMES), "default": "bearer"},
        "auth_header": {"type": "string", "minLength": 1, "default": "Authorization"},
        "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 60000, "default": _DEFAULT_TIMEOUT_MS},
    },
    "additionalProperties": False,
}

CREDENTIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "token": {"type": "string", "minLength": 1, "x-secret": True},
        "username": {"type": "string", "minLength": 1, "x-secret": True},
        "password": {"type": "string", "minLength": 1, "x-secret": True},
    },
    "additionalProperties": False,
    "anyOf": [
        {"required": ["token"]},
        {"required": ["username", "password"]},
    ],
}


class GenericHttpAdapter:
    """无状态 HTTP 参考适配器：Bearer/Basic/无鉴权 + BASE_URL 寻址。"""

    session_mode = SessionMode.NONE
    platform_config_schema = PLATFORM_CONFIG_SCHEMA
    credential_schema = CREDENTIAL_SCHEMA

    def __init__(self, *, key: str = "generic-http", name: str = "通用 HTTP", version: str = "1") -> None:
        self.key = key
        self.name = name
        self.version = version

    async def authenticate(
        self,
        platform: PlatformConfig,
        credential: SecretValue,
    ) -> PlatformSession | None:
        self._credential_headers(platform, credential)
        return None

    async def validate(self, platform: PlatformConfig, session: PlatformSession) -> bool:
        del session
        config = platform.adapter_config
        if not isinstance(config, dict) or set(config) - _CONFIG_KEYS:
            return False
        scheme = config.get("auth_scheme", "bearer")
        if scheme not in _ALLOWED_AUTH_SCHEMES:
            return False
        header = config.get("auth_header", "Authorization")
        if not isinstance(header, str) or not header.strip():
            return False
        timeout = config.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            return False
        return bool(100 <= timeout <= 60000)

    async def prepare_request(
        self,
        platform: PlatformConfig,
        session: PlatformSession | None,
        request: PlatformRequest,
        credential: SecretValue | None,
    ) -> PreparedRequest:
        del session
        if platform.resolver_type != "BASE_URL":
            raise ValueError("generic-http requires BASE_URL resolver")
        base_url = str(platform.resolver_config.get("base_url") or "").strip()
        if not base_url:
            raise ValueError("generic-http requires resolver_config.base_url")
        target = request.target
        if target.method is None or target.path is None:
            raise ValueError("generic-http supports only HTTP method/path targets")
        path = target.path
        if "://" in path or path.startswith("//"):
            raise ValueError("generic-http requires a relative target path")
        headers = {"Accept": "application/json"}
        if request.payload:
            headers["Content-Type"] = "application/json"
        headers.update(self._credential_headers(platform, credential))
        body = json.dumps(request.payload, ensure_ascii=False) if request.payload else None
        return PreparedRequest(
            method=target.method.upper(),
            url=urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
            headers=headers,
            body=body,
        )

    def _credential_headers(
        self,
        platform: PlatformConfig,
        credential: SecretValue | None,
    ) -> dict[str, str]:
        scheme = str(platform.adapter_config.get("auth_scheme") or "bearer")
        header_name = str(platform.adapter_config.get("auth_header") or "Authorization")
        if scheme == "none":
            return {}
        if credential is None:
            raise ValueError(f"credential required for auth scheme {scheme}")
        payload = self._credential_payload(credential)
        if scheme == "bearer":
            token = str(payload.get("token") or "").strip()
            if not token:
                raise ValueError("bearer scheme requires credential.token")
            return {header_name: f"Bearer {token}"}
        if scheme == "basic":
            username = str(payload.get("username") or "")
            password = str(payload.get("password") or "")
            if not username or not password:
                raise ValueError("basic scheme requires credential.username/password")
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {header_name: f"Basic {encoded}"}
        raise ValueError(f"unsupported auth scheme: {scheme}")

    @staticmethod
    def _credential_payload(credential: SecretValue) -> dict[str, Any]:
        try:
            payload = json.loads(credential.value)
        except json.JSONDecodeError as exc:
            raise ValueError("credential payload must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("credential payload must be a JSON object")
        return payload
