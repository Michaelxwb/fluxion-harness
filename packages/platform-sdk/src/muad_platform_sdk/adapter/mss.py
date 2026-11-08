"""Unified MSS adapter; no shared cookie jar, credentials, or implicit business retries."""

from __future__ import annotations

import json
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from ..types import (
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PreparedRequest,
    SecretValue,
    SessionMode,
)
from .mss_config import (
    MSSCredential,
    MSSSettings,
    PlatformAuthError,
    credentials,
    platform_url,
    settings,
    signing_credentials,
)
from .mss_signing import login_authorization

MAX_RESPONSE = 1024 * 1024
#: 平台失败原文的展示上限：够定位原因，又不把整页错误灌进工具结果/模型上下文。
MAX_ERROR_DETAIL_CHARS = 512


async def send(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], body: str | None = None
) -> httpx.Response:
    try:
        async with client.stream(
            method, url, headers=headers, content=body if body is not None else b""
        ) as response:
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_RESPONSE:
                    raise PlatformAuthError("PLATFORM_RESPONSE_TOO_LARGE")
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=bytes(content),
                request=response.request,
            )
    except httpx.HTTPError:
        raise PlatformAuthError("PLATFORM_NETWORK_ERROR", retryable=True) from None


def _clip(text: str) -> str:
    """截断并去控制字符：原文只用于定位原因，不做任何本机渲染。"""
    printable = "".join(char for char in text if char.isprintable() or char in "\n\t")
    return printable.strip()[:MAX_ERROR_DETAIL_CHARS]


def _message_of(payload: object, response: httpx.Response) -> str:
    """平台原文：只认 JSON 的 ``msg``/``message``/``error``；文本类响应才退回截断正文。"""
    if isinstance(payload, dict):
        for key in ("msg", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _clip(value)
            if isinstance(value, dict):
                inner = value.get("message") or value.get("msg")
                if isinstance(inner, str) and inner.strip():
                    return _clip(inner)
        return ""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type or "text" in content_type:
        return _clip(response.content.decode("utf-8", errors="replace"))
    return ""


def _http_detail(response: httpx.Response) -> dict[str, object]:
    """非 2xx 的可读原因（HTTP 状态 + 平台原文）；二进制响应不带正文。"""
    payload: object = None
    if response.content:
        try:
            payload = response.json()
        except ValueError:
            payload = None
    return {"http_status": response.status_code, "message": _message_of(payload, response)}


def check(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise PlatformAuthError("PLATFORM_AUTH_REJECTED", detail=_http_detail(response))
    if not response.is_success:
        raise PlatformAuthError(
            "PLATFORM_HTTP_ERROR",
            retryable=response.status_code >= 500 or response.status_code == 429,
            detail=_http_detail(response),
        )
    if not response.content:
        return
    try:
        payload = response.json()
    except ValueError:
        raise PlatformAuthError("PLATFORM_RESPONSE_INVALID") from None
    if not isinstance(payload, dict):
        raise PlatformAuthError("PLATFORM_RESPONSE_INVALID")
    code = payload.get("code", 0)
    if code != 0:
        detail = {
            "http_status": response.status_code,
            "business_code": code,
            "message": _message_of(payload, response),
        }
        if code in (9348, 9001):
            raise PlatformAuthError("PLATFORM_AUTH_REJECTED", detail=detail)
        raise PlatformAuthError(
            "PLATFORM_BUSINESS_ERROR", retryable=code in (9000, 12001), detail=detail
        )


def _scrub(exc: PlatformAuthError, secret: MSSCredential) -> PlatformAuthError:
    """把本次凭据的具体值从平台原文里擦掉（平台可能回显 ak/sk/自定义头）。"""
    detail = exc.detail
    if not isinstance(detail, dict):
        return exc
    message = detail.get("message")
    if not isinstance(message, str):
        return exc
    values: list[str] = []
    if secret.ak is not None:
        values.append(secret.ak.get_secret_value())
    if secret.sk is not None:
        values.append(secret.sk.get_secret_value())
    values.extend(str(value) for value in secret.headers.values())
    for value in values:
        if len(value) >= 4:
            message = message.replace(value, "***")
    return PlatformAuthError(exc.code, retryable=exc.retryable, detail={**detail, "message": message})


def _checked(response: httpx.Response, secret: MSSCredential) -> None:
    """登录链路专用：失败原因先擦除本次凭据值再向上抛。"""
    try:
        check(response)
    except PlatformAuthError as exc:
        raise _scrub(exc, secret) from None


def restore_cookies(session: PlatformSession) -> httpx.Cookies:
    jar = httpx.Cookies()
    from http.cookiejar import Cookie

    for item in json.loads(session.cookie or "[]"):
        jar.jar.set_cookie(
            Cookie(
                0,
                item["name"],
                item["value"],
                None,
                False,
                item["domain"],
                item["domain"].startswith("."),
                item["domain"].startswith("."),
                item["path"],
                True,
                item["secure"],
                item["expires"],
                False,
                None,
                None,
                {},
            )
        )
    return jar


def login_body(config: MSSSettings, credential: MSSCredential) -> str:
    if config.login_body is None and credential.body is None:
        return ""
    return json.dumps({**(config.login_body or {}), **(credential.body or {})}, ensure_ascii=False)


class MSSAdapter:
    key = "mss-session"
    name = "mss-session"
    version = "2"
    session_mode = SessionMode.SESSION
    platform_config_schema = MSSSettings.model_json_schema()
    credential_schema = MSSCredential.model_json_schema()

    def _client(self, config: MSSSettings) -> httpx.AsyncClient:
        context = ssl.create_default_context(cafile=config.ca_bundle or None)
        return httpx.AsyncClient(
            verify=context, timeout=config.timeout_seconds, follow_redirects=False, trust_env=False
        )

    async def authenticate(self, platform: PlatformConfig, credential: SecretValue) -> PlatformSession:
        config, secret = settings(platform), credentials(credential)
        url = platform_url(platform, config.session_endpoint)
        headers = {key.lower(): value for key, value in {**config.login_headers, **secret.headers}.items()}
        if config.host_header:
            headers["host"] = config.host_header
        async with self._client(config) as client:
            if config.csrf_enabled:
                response = await send(client, "GET", platform_url(platform, config.csrf_endpoint), headers)
                _checked(response, secret)
                csrf = next((c.value for c in client.cookies.jar if c.name == "csrf_token"), "")
                if not csrf:
                    raise PlatformAuthError("PLATFORM_CSRF_MISSING")
                headers["x-csrftoken"] = csrf
            headers.update(
                {
                    "content-type": "application/json",
                    "x-branch-tag": config.branch_tag,
                    "agent-nonce": uuid.uuid4().hex,
                }
            )
            body = login_body(config, secret)
            if config.auth_mode == "hmac":
                signing = signing_credentials(secret)
                headers["authorization"] = login_authorization(
                    url, signing.ak.get_secret_value(), signing.sk.get_secret_value(), int(time.time()), body
                )
            response = await send(client, "POST", url, headers, body)
            _checked(response, secret)
            if not any(c.name == config.login_cookie_name and c.value for c in response.cookies.jar):
                raise PlatformAuthError("PLATFORM_LOGIN_NO_COOKIE")
            return self._session(client, platform, config)

    def _session(
        self, client: httpx.AsyncClient, platform: PlatformConfig, config: MSSSettings
    ) -> PlatformSession:
        # Keep domain/path/secure/expiry so cookies cannot leak to other endpoints.
        cookies = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expires": c.expires,
            }
            for c in client.cookies.jar
        ]
        expiry = datetime.now(UTC) + timedelta(seconds=config.session_ttl_seconds)
        return PlatformSession(
            cookie=json.dumps(cookies),
            expires_at=expiry,
            adapter_state={"platform_config": platform.model_dump(mode="json")},
        )

    def _headers(self, platform: PlatformConfig, session: PlatformSession, url: str) -> dict[str, str]:
        if session.adapter_state.get("platform_config") != platform.model_dump(mode="json"):
            raise PlatformAuthError("PLATFORM_SESSION_CONFIG_CHANGED")
        if not session.expires_at or session.expires_at <= datetime.now(UTC):
            raise PlatformAuthError("PLATFORM_SESSION_EXPIRED")
        jar = restore_cookies(session)
        request = httpx.Request("GET", url)
        jar.set_cookie_header(request)
        cookie = request.headers.get("cookie", "")
        if not cookie:
            raise PlatformAuthError("PLATFORM_SESSION_EXPIRED")
        config = settings(platform)
        headers = {"Cookie": cookie, "Timezone": config.timezone, "Accept": "application/json"}
        for pair in cookie.split("; "):
            if pair.startswith("csrf_token="):
                headers[config.csrf_header] = pair.partition("=")[2]
        if config.host_header:
            headers["Host"] = config.host_header
        if config.referer:
            headers["Referer"] = config.referer
        return headers

    async def validate(self, platform: PlatformConfig, session: PlatformSession) -> bool:
        config = settings(platform)
        if not config.health_endpoint:
            raise PlatformAuthError("PLATFORM_HEALTH_ENDPOINT_REQUIRED")
        url = platform_url(platform, config.health_endpoint)
        try:
            headers = self._headers(platform, session, url)
            async with self._client(config) as client:
                check(await send(client, "GET", url, headers))
            return True
        except PlatformAuthError as exc:
            if exc.code in {
                "PLATFORM_AUTH_REJECTED",
                "PLATFORM_SESSION_EXPIRED",
                "PLATFORM_SESSION_CONFIG_CHANGED",
            }:
                return False
            raise

    async def prepare_request(
        self,
        platform: PlatformConfig,
        session: PlatformSession | None,
        request: PlatformRequest,
        credential: SecretValue | None,
    ) -> PreparedRequest:
        if session is None:
            raise PlatformAuthError("PLATFORM_SESSION_REQUIRED")
        settings(platform)
        method = (request.target.method or "").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or request.target.path is None:
            raise PlatformAuthError("PLATFORM_HTTP_TARGET_REQUIRED")
        url = platform_url(platform, request.target.path)
        headers = self._headers(platform, session, url)
        body = None
        if method == "GET":
            url = str(httpx.URL(url).copy_merge_params(request.payload))
        elif request.payload:
            headers["Content-Type"] = "application/json"
            body = json.dumps(request.payload)
        return PreparedRequest(method=method, url=url, headers=headers, body=body)
