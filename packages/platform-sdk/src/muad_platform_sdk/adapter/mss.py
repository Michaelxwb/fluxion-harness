"""Unified MSS adapter; no shared cookie jar, credentials, or implicit business retries."""

from __future__ import annotations

import json
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from ..platform_transport import PlatformBudgetScope
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


async def send(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], body: str | None = None,
    *, platform_key: str = "", budget: PlatformBudgetScope | None = None,
) -> httpx.Response:
    if budget is None or budget.controller is None:
        return await _send(client, method, url, headers, body)
    async with budget.attempt(platform_key, method, url) as lease:
        response = await _send(client, method, url, headers, body)
        if lease is not None:
            if response.status_code == 429:
                lease.outcome = "proxy_rate_limited"
            elif response.is_error:
                lease.outcome = "error"
        return response


async def _send(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], body: str | None,
) -> httpx.Response:
    try:
        async with client.stream(
            method, url, headers=headers, content=body if body is not None else b"", follow_redirects=False
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


def check(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise PlatformAuthError("PLATFORM_AUTH_REJECTED")
    if not response.is_success:
        raise PlatformAuthError(
            "PLATFORM_HTTP_ERROR", retryable=response.status_code >= 500 or response.status_code == 429
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
        if code in (9348, 9001):
            raise PlatformAuthError("PLATFORM_AUTH_REJECTED")
        raise PlatformAuthError("PLATFORM_BUSINESS_ERROR", retryable=code in (9000, 12001))


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

    def __init__(self, *, budget: PlatformBudgetScope | None = None) -> None:
        # Fixed execution context, never a mutable current-user field on a global Adapter.
        self._budget = budget

    async def _send(self, platform: PlatformConfig, client: httpx.AsyncClient, method: str,
                    url: str, headers: dict[str, str], body: str | None = None) -> httpx.Response:
        return await send(client, method, url, headers, body,
                          platform_key=platform.key, budget=self._budget)

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
                response = await self._send(
                    platform, client, "GET", platform_url(platform, config.csrf_endpoint), headers
                )
                check(response)
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
            response = await self._send(platform, client, "POST", url, headers, body)
            check(response)
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
                check(await self._send(platform, client, "GET", url, headers))
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
