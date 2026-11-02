"""Public platform settings and private account material have separate schemas."""

from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from ..types import PlatformConfig, SecretValue


class PlatformAuthError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class MSSPSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_endpoint: str = Field(
        default="/gateway/mss-auth-acl-service/v1/certification/login_agent", title="登录接口路径"
    )
    health_endpoint: str = Field(default="/v1/rtt", title="登录态校验路径")
    csrf_enabled: bool = Field(default=False, title="登录前获取 CSRF")
    csrf_endpoint: str = Field(default="/v1/certification/get_token", title="CSRF 接口路径")
    host_header: str = Field(default="", title="Host 请求头（可选）")
    referer: str = Field(default="", title="Referer 请求头（可选）")
    timezone: str = Field(default="+08:00", title="业务时区")
    session_ttl_seconds: int = Field(default=7200, ge=60, le=86400, title="登录态最长缓存秒数")
    timeout_seconds: int = Field(default=20, ge=1, le=120, title="请求超时秒数")
    ca_bundle: str = Field(default="", title="内部 CA 文件路径（可选）")


class AKSKCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ak: SecretStr = Field(min_length=1, title="AK", json_schema_extra={"x-secret": True})
    sk: SecretStr = Field(min_length=1, title="SK", json_schema_extra={"x-secret": True})


def settings(platform: PlatformConfig) -> MSSPSettings:
    if not platform.enabled or platform.resolver_type != "BASE_URL":
        raise PlatformAuthError("PLATFORM_UNAVAILABLE")
    try:
        return MSSPSettings.model_validate(platform.adapter_config)
    except ValidationError:
        raise PlatformAuthError("PLATFORM_CONFIG_INVALID") from None


def credentials(value: SecretValue) -> AKSKCredential:
    try:
        return AKSKCredential.model_validate_json(value.value)
    except ValidationError:
        raise PlatformAuthError("CREDENTIAL_INVALID") from None


def platform_url(platform: PlatformConfig, path: str) -> str:
    base = str(platform.resolver_config.get("base_url", ""))
    origin, target = urlsplit(base), urlsplit(path)
    if (
        origin.scheme not in {"http", "https"}
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or target.scheme
        or target.netloc
        or target.fragment
        or "\\" in path
        or not path.startswith("/")
        or path.startswith("//")
    ):
        raise PlatformAuthError("PLATFORM_TARGET_INVALID")
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
