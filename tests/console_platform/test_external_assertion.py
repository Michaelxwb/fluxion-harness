"""外部断言认证的纯单元测试（不依赖数据库）。

覆盖 RULE-auth-002 的五步链中「可脱离存储验证」的部分：算法白名单、签名、
密文比对、claim 策略、身份提取与指纹计算。身份查表部分需要真实 PG，留给集成层。
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from muad_console_platform.application.external_assertion.aes_ecb import (
    derive_key,
    encrypt_expected,
    matches_expected,
)
from muad_console_platform.application.external_assertion.config import (
    ExternalPlatformConfig,
    PlatformConfigError,
    load_platform_configs,
)
from muad_console_platform.application.external_assertion.principal import (
    compute_identity_hash,
    extract_identity,
)
from muad_console_platform.application.external_assertion.verifier import (
    AssertionRejected,
    verify_assertion,
)

ISSUER = "mssw-issuer"


def _default_config() -> ExternalPlatformConfig:
    """构造一个只关心默认时效口径的配置（供 `_session_expires_at` 直调测试用）。

    公钥/因子字段在这些测试里用不到，随便给一个占位 PEM 即可；`issue_time_*` / `channel`
    走 dataclass 默认值（`login_time`/`time`、`ms`、`MSSWWEB`），与 MSSW 上游一致。
    """
    return ExternalPlatformConfig(
        platform_key="mssw",
        public_key=None,  # type: ignore[arg-type]
        issuer=ISSUER,
        audience="aud",
        salt=b"0" * 32,
        identity_keys=("expert_id", "tenant_id"),
        identity_field_labels={},
        display_name="mssw",
    )
AUDIENCE = "mssw-audience"
SALT = b"mssw-test-salt"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture(scope="module")
def keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture()
def config(keypair):
    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    payload = {
        "mssw": {
            "public_key": pem,
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "salt": SALT.decode("utf-8"),
            "identity_keys": ["expert_id", "tenant_id"],
            "identity_field_labels": {"expert_id": "专家 ID", "tenant_id": "租户 ID"},
        }
    }
    return load_platform_configs(json.dumps(payload))["mssw"]


def _sign(private, claims: dict, *, alg: str = "RS256") -> str:
    header = _b64url(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signature = private.sign(f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64url(signature)}"


def _claims(**overrides) -> dict:
    """上游真实形态的 claims。

    签发时刻用 `login_time`（字符串毫秒）+ `time`（int64 毫秒），**不是 `iat`**——
    上游从不发 `iat`，用 `iat` 造样本会让绝对上限校验取不到值，测试因而看不见该校验失效。
    """
    issued_ms = int(time.time() * 1000)
    base = {
        "_id": "6a4ca2554a2a45d4b1ad5996",
        "expert_id": "6a4ca2554a2a45d4b1ad5996",
        "tenant_id": "1001",
        "job_number": "14806",
        "aud": _b64(encrypt_expected(AUDIENCE, SALT)),
        "iss": [_b64(encrypt_expected(ISSUER, SALT))],
        "exp": -1,
        "login_time": str(issued_ms),
        "time": issued_ms,
    }
    base.update(overrides)
    return base


# --- AES 因子 -------------------------------------------------------------


def test_derive_key_right_pads_salt_to_32_bytes() -> None:
    assert derive_key(b"abc") == b"abc" + b"\x00" * 29


def test_derive_key_rejects_oversized_salt() -> None:
    with pytest.raises(ValueError):
        derive_key(b"x" * 33)


def test_matches_expected_accepts_single_element_array() -> None:
    encrypted = _b64(encrypt_expected(ISSUER, SALT))
    assert matches_expected([encrypted], ISSUER, SALT) is True


def test_matches_expected_rejects_wrong_plaintext() -> None:
    encrypted = _b64(encrypt_expected("someone-else", SALT))
    assert matches_expected(encrypted, AUDIENCE, SALT) is False


def test_matches_expected_rejects_multi_element_array() -> None:
    encrypted = _b64(encrypt_expected(ISSUER, SALT))
    assert matches_expected([encrypted, encrypted], ISSUER, SALT) is False


# --- 验签 -----------------------------------------------------------------


def test_valid_assertion_is_accepted(keypair, config) -> None:
    private, _ = keypair
    verified = verify_assertion(_sign(private, _claims()), config)
    assert verified.claims["expert_id"] == "6a4ca2554a2a45d4b1ad5996"


def test_alg_none_is_rejected(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(), alg="none")
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "algorithm_not_allowed"


def test_wrong_signature_is_rejected(keypair, config) -> None:
    _, public_key = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _sign(other, _claims())
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "bad_signature"
    assert public_key is not None


def test_tampered_payload_is_rejected(keypair, config) -> None:
    private, _ = keypair
    header, _, signature = _sign(private, _claims()).split(".")
    forged = _b64url(json.dumps(_claims(tenant_id="9999"), separators=(",", ":")).encode())
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(f"{header}.{forged}.{signature}", config)
    assert exc.value.reason == "bad_signature"


def test_audience_mismatch_is_rejected(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(aud=_b64(encrypt_expected("other-audience", SALT))))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "audience_mismatch"


def test_issuer_mismatch_is_rejected(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(iss=[_b64(encrypt_expected("other-issuer", SALT))]))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "issuer_mismatch"


def test_malformed_token_is_rejected(config) -> None:
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion("not-a-jwt", config)
    assert exc.value.reason == "malformed_token"


def test_stale_login_time_is_rejected_when_max_age_enforced(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(login_time="1000", time=1000))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config, max_session_age_sec=60)
    assert exc.value.reason == "assertion_expired"


def test_max_age_is_skipped_when_not_configured(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(login_time="1000", time=1000))
    assert verify_assertion(token, config, max_session_age_sec=0).claims["exp"] == -1


def test_missing_issue_time_is_rejected_when_max_age_enforced(keypair, config) -> None:
    """签发时间不可知就没有绝对上限，`exp=-1` 的 token 会无限续订——必须拒绝而非跳过。"""
    private, _ = keypair
    claims = _claims()
    claims.pop("login_time")
    claims.pop("time")
    token = _sign(private, claims)
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config, max_session_age_sec=60)
    assert exc.value.reason == "missing_issue_time"


def test_future_issue_time_is_rejected(keypair, config) -> None:
    """签名方时钟超前会让上限失效，宁可误杀也不放行。"""
    private, _ = keypair
    future_ms = int((time.time() + 3600) * 1000)
    token = _sign(private, _claims(login_time=str(future_ms), time=future_ms))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config, max_session_age_sec=60)
    assert exc.value.reason == "assertion_expired"


def test_seconds_granularity_login_time_is_not_misread_as_ms(keypair, config) -> None:
    """真实上游形态：`login_time` 是**秒级**字符串，`time` 是**毫秒级**整数。

    同一 token 内两个字段单位不同；若按单一 `issue_time_unit='ms'` 统一除 1000，秒值会算到
    1970 年，`age` 变成上万年而被误判 `assertion_expired`。`_seconds_from_epoch` 按量级自适应后
    应正确放行。
    """
    private, _ = keypair
    now_sec = int(time.time())
    token = _sign(private, _claims(login_time=str(now_sec), time=now_sec * 1000))
    verified = verify_assertion(token, config, max_session_age_sec=86400)
    assert verified.claims["login_time"] == str(now_sec)


def test_issue_time_unit_adapts_to_magnitude_both_keys(keypair, config) -> None:
    """两个候选 key 各自按量级换算：秒级与毫秒级都应被识别为「刚签发」，不误判过期。"""
    from muad_console_platform.application.external_assertion.verifier import _issued_at_seconds

    now = time.time()
    # 秒级 login_time 命中：应约等于 now。
    sec_claims = {"login_time": str(int(now)), "time": int(now * 1000)}
    assert abs(_issued_at_seconds(sec_claims, config) - now) < 2

    # 只有毫秒级 time 时，也应约等于 now。
    ms_claims = {"time": int(now * 1000)}
    assert abs(_issued_at_seconds(ms_claims, config) - now) < 2


def test_iat_alone_is_not_accepted_as_issue_time(keypair, config) -> None:
    """上游从不发 `iat`：只带 `iat` 的样本必须被判为「缺签发时间」，防止字段名回退。"""
    private, _ = keypair
    claims = _claims(iat=int(time.time()))
    claims.pop("login_time")
    claims.pop("time")
    token = _sign(private, claims)
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config, max_session_age_sec=60)
    assert exc.value.reason == "missing_issue_time"


# --- 身份提取与指纹 -------------------------------------------------------


def test_extract_identity_requires_every_configured_key(config) -> None:
    with pytest.raises(AssertionRejected) as exc:
        extract_identity({"expert_id": "x"}, config)
    assert exc.value.reason == "identity_key_missing:tenant_id"


def test_extract_identity_rejects_blank_value(config) -> None:
    with pytest.raises(AssertionRejected):
        extract_identity({"expert_id": "  ", "tenant_id": "1001"}, config)


def test_identity_hash_is_order_independent() -> None:
    left = compute_identity_hash({"expert_id": "a", "tenant_id": "b"})
    right = compute_identity_hash({"tenant_id": "b", "expert_id": "a"})
    assert left == right
    assert len(left) == 64


def test_identity_hash_changes_with_tenant() -> None:
    left = compute_identity_hash({"expert_id": "a", "tenant_id": "b"})
    right = compute_identity_hash({"expert_id": "a", "tenant_id": "c"})
    assert left != right


# --- 配置 ---------------------------------------------------------------


def test_empty_config_means_no_platform() -> None:
    assert load_platform_configs("") == {}
    assert load_platform_configs(None) == {}


def _pem(keypair) -> str:
    _, public = keypair
    return public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def _platform_payload(keypair, **extra) -> dict:
    return {
        "mssw": {
            "public_key": _pem(keypair),
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "salt": SALT.decode("utf-8"),
            "identity_keys": ["expert_id", "tenant_id"],
            **extra,
        }
    }


def test_config_new_keys_default_to_mssw_behaviour(keypair) -> None:
    """不配新键时，时效/通道取 MSSW 默认值——存量部署零变化。"""
    cfg = load_platform_configs(json.dumps(_platform_payload(keypair)))["mssw"]
    assert cfg.issue_time_keys == ("login_time", "time")
    assert cfg.issue_time_unit == "ms"
    assert cfg.channel == "MSSWWEB"


def test_config_parses_overridden_time_keys_unit_and_channel(keypair) -> None:
    payload = _platform_payload(
        keypair, issue_time_keys=["iat"], issue_time_unit="s", channel="ACMEWEB"
    )
    cfg = load_platform_configs(json.dumps(payload))["mssw"]
    assert cfg.issue_time_keys == ("iat",)
    assert cfg.issue_time_unit == "s"
    assert cfg.channel == "ACMEWEB"


def test_config_rejects_bad_time_unit(keypair) -> None:
    payload = _platform_payload(keypair, issue_time_unit="minute")
    with pytest.raises(PlatformConfigError):
        load_platform_configs(json.dumps(payload))


@pytest.mark.parametrize("channel", ["MSSWWEB", "MSSPWEB", "msspweb", "ACMEWEB", "xweb"])
def test_config_accepts_valid_channel_names(keypair, channel: str) -> None:
    """合规通道名（字母数字 + web 结尾，任意大小写）通过解析（RULE-im-013）。"""
    payload = _platform_payload(keypair, channel=channel)
    cfg = load_platform_configs(json.dumps(payload))["mssw"]
    assert cfg.channel == channel


@pytest.mark.parametrize(
    "channel",
    ["MSSPWEBUAT", "msspweb_uat", "mssp-web", "MSSP", "web", " Web", "w e b"],
)
def test_config_rejects_invalid_channel_name(keypair, channel: str) -> None:
    """不合规通道名在解析期显式失败，而非运行期静默 404（RULE-im-013）。

    回归点：路由前缀取自 channel.lower()，nginx 用 `^/api/v1/[a-z0-9]+web` 转发；名字带
    下划线/横线/非 web 结尾会让 nginx 匹配不到 → 404，且无明确报错。
    """
    payload = _platform_payload(keypair, channel=channel)
    with pytest.raises(PlatformConfigError):
        load_platform_configs(json.dumps(payload))


def test_config_empty_issue_time_keys_falls_back_to_default(keypair) -> None:
    """空列表视同未配置，回退默认候选键（不报错、不产生"没有任何时效字段"）。"""
    payload = _platform_payload(keypair, issue_time_keys=[])
    cfg = load_platform_configs(json.dumps(payload))["mssw"]
    assert cfg.issue_time_keys == ("login_time", "time")


def test_missing_factor_is_a_config_error(keypair) -> None:
    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    with pytest.raises(PlatformConfigError):
        load_platform_configs(json.dumps({"mssw": {"public_key": pem, "issuer": ISSUER}}))


def test_oversized_salt_is_a_config_error(keypair) -> None:
    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    payload = {
        "mssw": {
            "public_key": pem,
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "salt": "x" * 33,
        }
    }
    with pytest.raises(PlatformConfigError):
        load_platform_configs(json.dumps(payload))


def test_config_repr_hides_factors(config) -> None:
    rendered = repr(config)
    assert ISSUER not in rendered
    assert AUDIENCE not in rendered


# --- 未配置平台：503（E-03），且配置检查先于任何存储访问 ---------------------------


async def test_resolve_raises_platform_unavailable_when_no_platform_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 `EXTERNAL_ASSERTION_PLATFORMS_JSON`（Secret 缺失/未配）= 该平台 503，而非 401。"""
    from muad_common import SharedSettings
    from muad_console_platform.application import external_assertion_service as svc_mod

    monkeypatch.setattr(
        svc_mod, "process_settings", lambda: SharedSettings(external_assertion_platforms_json="")
    )
    service = svc_mod.ExternalAssertionService(session=None)  # type: ignore[arg-type]

    with pytest.raises(svc_mod.PlatformUnavailable):
        await service.resolve("t1", "mssw", "any.assertion.token")


async def test_resolve_rejects_assertion_on_another_channel(
    monkeypatch: pytest.MonkeyPatch,
    keypair,
) -> None:
    """断言只能用在**该平台配置的通道**上；声明了别的通道直接拒绝，且不访问存储。"""
    from muad_common import SharedSettings
    from muad_console_platform.application import external_assertion_service as svc_mod

    touched: list[str] = []

    class _RecordingRepo:
        def __init__(self, *_a, **_k) -> None:
            pass

        def __getattr__(self, name: str):
            touched.append(name)
            raise AssertionError(f"通道不匹配不得访问存储方法 {name}")

    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    # `mssw` 默认通道是 MSSWWEB；这里声明 WEBCHAT 必须被拒。
    platform_json = json.dumps(
        {
            "mssw": {
                "public_key": pem,
                "issuer": ISSUER,
                "audience": AUDIENCE,
                "salt": SALT.decode("utf-8"),
                "identity_keys": ["expert_id", "tenant_id"],
            }
        }
    )
    monkeypatch.setattr(svc_mod, "ExternalIdentityRepository", _RecordingRepo)
    monkeypatch.setattr(
        svc_mod,
        "process_settings",
        lambda: SharedSettings(external_assertion_platforms_json=platform_json),
    )
    service = svc_mod.ExternalAssertionService(session=None)  # type: ignore[arg-type]

    with pytest.raises(svc_mod.ExternalAssertionRejected):
        await service.resolve("t1", "mssw", "any.assertion.token", channel="WEBCHAT")
    assert touched == [], "通道检查必须先于任何存储访问"


async def test_resolve_treats_missing_channel_as_unspecified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未声明通道（老调用方）保持向后兼容：不因通道被拒，继续走配置检查。"""
    from muad_common import SharedSettings
    from muad_console_platform.application import external_assertion_service as svc_mod

    monkeypatch.setattr(
        svc_mod, "process_settings", lambda: SharedSettings(external_assertion_platforms_json="")
    )
    service = svc_mod.ExternalAssertionService(session=None)  # type: ignore[arg-type]

    with pytest.raises(svc_mod.PlatformUnavailable):
        await service.resolve("t1", "mssw", "any.assertion.token", channel=None)


async def test_resolve_raises_platform_unavailable_before_touching_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置缺该 key 时，resolve 不调用任何存储方法 —— 直接 503，不进查询分支。"""
    from muad_common import SharedSettings
    from muad_console_platform.application import external_assertion_service as svc_mod

    touched: list[str] = []

    class _RecordingRepo:
        def __init__(self, *_a, **_k) -> None:
            pass

        def __getattr__(self, name: str):
            touched.append(name)
            raise AssertionError(f"未配置平台不得访问存储方法 {name}")

    monkeypatch.setattr(svc_mod, "ExternalIdentityRepository", _RecordingRepo)
    monkeypatch.setattr(
        svc_mod, "process_settings", lambda: SharedSettings(external_assertion_platforms_json="")
    )
    service = svc_mod.ExternalAssertionService(session=None)  # type: ignore[arg-type]

    with pytest.raises(svc_mod.PlatformUnavailable):
        await service.resolve("t1", "mssw", "any.assertion.token")
    assert touched == [], "配置检查必须先于任何存储访问"


def test_session_expiry_takes_the_soonest_of_three_bounds() -> None:
    """`expires_at = min(now + max_age, login_time + max_age, 正数 exp)`。

    三路取最小是设计口径：`now + max_age` 兜底、`login_time + max_age` 是绝对上限、
    正数 `exp` 若可信则尊重。这里让三者互不相同，断言取的确实是最早的那个。
    """
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application.external_assertion_service import _session_expires_at

    now = datetime.now(tz=UTC)
    issued = now - timedelta(seconds=30)

    # exp 最早 → 应取 exp
    expires = _session_expires_at(
        {
            "login_time": str(int(issued.timestamp() * 1000)),
            "exp": int((now + timedelta(seconds=5)).timestamp()),
        },
        _default_config(),
        max_session_age_sec=600,
    )
    assert expires < now + timedelta(seconds=10)
    assert expires > now

    # 无正数 exp、login_time 较旧 → 应取 login_time + max_age（毫秒截断，允许亚秒误差）
    expires = _session_expires_at(
        {"login_time": str(int(issued.timestamp() * 1000)), "exp": -1},
        _default_config(),
        max_session_age_sec=60,
    )
    assert abs((expires - (issued + timedelta(seconds=60))).total_seconds()) < 1

    # 缺 login_time 且 exp 非正 → 只剩 now + max_age 兜底
    before = datetime.now(tz=UTC)
    expires = _session_expires_at({"exp": -1}, _default_config(), max_session_age_sec=60)
    assert expires >= before + timedelta(seconds=59)


def test_session_expiry_ignores_non_positive_exp() -> None:
    """`exp=-1` 不可信，必须被忽略而不是当成「已过期」或「永不过期」。"""
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application.external_assertion_service import _session_expires_at

    now = datetime.now(tz=UTC)
    issued_ms = int((now - timedelta(seconds=10)).timestamp() * 1000)
    expires = _session_expires_at(
        {"login_time": str(issued_ms), "exp": -1}, _default_config(), max_session_age_sec=600
    )
    # 落在 login_time + 600s 附近，且显然在「现在」之后（-1 没把会话判死）
    assert expires > now + timedelta(seconds=500)


def test_session_expiry_without_age_cap_only_honours_positive_exp() -> None:
    """`max_session_age_sec` 为 0 / None 时关闭年龄闸，与 settings 注释和 `_check_max_age` 一致。

    此前的实现会回退到硬编码的 300 秒，使「不配置」的实际语义变成 5 分钟：验签放行，
    但 SSE 订阅时早已过期，网关抛 `CHAT_SESSION_EXPIRED`，而报错发生在响应头发出之后，
    前端只看到事件流突然断开。设计文档从未规定这个 300 秒默认值。
    """
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application.external_assertion_service import _session_expires_at

    now = datetime.now(tz=UTC)
    stale_login_ms = int((now - timedelta(hours=8)).timestamp() * 1000)
    claims = {"login_time": str(stale_login_ms), "time": stale_login_ms, "exp": -1}

    # 关闭年龄闸：登录已过去 8 小时也不再因年龄被判过期。
    for disabled in (0, None):
        expires = _session_expires_at(claims, _default_config(), max_session_age_sec=disabled)
        assert expires > now + timedelta(days=365), "关闭年龄闸后不应设任何年龄上限"

    # 但上游若给了正数 exp，仍要尊重它——闸关闭 ≠ 无视一切约束。
    exp = int((now + timedelta(minutes=30)).timestamp())
    bounded = _session_expires_at({**claims, "exp": exp}, _default_config(), max_session_age_sec=0)
    assert abs((bounded - datetime.fromtimestamp(exp, tz=UTC)).total_seconds()) < 1


def test_issue_time_unit_and_keys_follow_platform_config() -> None:
    """时效字段与单位来自平台配置：`s` 单位不除以 1000，自定义键名按配置读取。"""
    import dataclasses
    from datetime import UTC, datetime

    from muad_console_platform.application.external_assertion.verifier import _issued_at_seconds

    now = datetime.now(tz=UTC)
    now_s = int(now.timestamp())  # 秒
    cfg = dataclasses.replace(
        _default_config(), issue_time_keys=("iat",), issue_time_unit="s"
    )
    # `s` 单位：iat 是秒，原样返回，不 /1000
    assert abs(_issued_at_seconds({"iat": now_s}, cfg) - now_s) < 1
    # 默认 ms 配置读不到 `iat`（不在候选键里），返回 None
    assert _issued_at_seconds({"iat": now_s}, _default_config()) is None


# --- 凭证层/权限层写入：Console 是唯一的写入者 ----------------------------------------


class _FakeCache:
    def __init__(self) -> None:
        self.credentials: dict[tuple[str, str, str], str] = {}
        self.permissions: dict[tuple[str, str], str] = {}
        self.fail = False

    async def write_credential_plain(
        self,
        tenant_id: str,
        platform_key: str,
        token_hash: str,
        user_id: object,
        payload: str,
        *,
        expires_at_epoch: float,
        now_epoch: float,
    ) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.credentials[(tenant_id, platform_key, token_hash)] = payload

    async def write_permission_plain(
        self,
        tenant_id: str,
        user_id: object,
        payload: str,
        *,
        expires_at_epoch: float,
        now_epoch: float,
    ) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.permissions[(tenant_id, str(user_id))] = payload


def _principal(user_id: object) -> object:
    import uuid as _uuid

    from muad_contracts.webchat import ChatPrincipal

    return ChatPrincipal(
        tenant_id="t1",
        platform_user_id=user_id,
        session_id=_uuid.uuid4(),
        auth_method="EXTERNAL_ASSERTION",
    )


def _resolved(msg) -> object:  # noqa: ANN001 - 测试内联结构
    from muad_console_platform.application.external_assertion.principal import ResolvedPrincipal

    return ResolvedPrincipal(
        allowed=True,
        reason=None,
        platform_user_id=msg,
        agents=(),
        grants=(),
    )


async def test_write_credential_stores_only_identity_and_expiry() -> None:
    """凭证层只存重建主体所需字段 + 绝对到期；**不含任何授权**。"""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application import external_assertion_service as svc_mod

    cache = _FakeCache()
    service = svc_mod.ExternalAssertionService(session=None, cache=cache)  # type: ignore[arg-type]
    uid = _uuid.uuid4()
    expires = datetime.now(tz=UTC) + timedelta(hours=1)
    await service._write_credential("t1", "mssw", "assertion.x", _principal(uid), expires)

    payloads = list(cache.credentials.values())
    assert len(payloads) == 1
    stored = json.loads(payloads[0])
    assert set(stored) == {"platform_user_id", "session_id", "expires_at_epoch"}
    assert stored["platform_user_id"] == str(uid)


async def test_write_permission_stores_full_conclusion_with_expiry() -> None:
    """权限层存 allowed/reason/agents/grants/expires_at，网关可原样还原并复用判定。"""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application import external_assertion_service as svc_mod

    cache = _FakeCache()
    service = svc_mod.ExternalAssertionService(session=None, cache=cache)  # type: ignore[arg-type]
    uid = _uuid.uuid4()
    expires = datetime.now(tz=UTC) + timedelta(hours=2)
    await service._write_permission("t1", uid, _resolved(uid), expires)

    stored = json.loads(cache.permissions[("t1", str(uid))])
    assert stored["allowed"] is True
    assert stored["agents"] == []
    assert stored["grants"] == []
    assert stored["expires_at"] == expires.isoformat()


async def test_cache_failure_does_not_break_resolution() -> None:
    """Redis 抖动时写入失败只记日志，不影响本次验签结果（网关回源即可）。"""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application import external_assertion_service as svc_mod

    cache = _FakeCache()
    cache.fail = True
    service = svc_mod.ExternalAssertionService(session=None, cache=cache)  # type: ignore[arg-type]
    uid = _uuid.uuid4()
    expires = datetime.now(tz=UTC) + timedelta(hours=1)
    # 不抛异常即为通过。
    await service._write_credential("t1", "mssw", "a.b", _principal(uid), expires)
    await service._write_permission("t1", uid, _resolved(uid), expires)
