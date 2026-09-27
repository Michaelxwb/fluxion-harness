"""[B-05] 前端 auth 请求层与词条契约（设计 §3.5 数据获取层 / §2.4 B-FE-02，重编号后 B-05）。

真实边界：前端真实源码 + 仓库检查脚本 + 真实 tsc（`RULE-front-001` 的 verifier argv 会跑
`check_frontend_api_usage.py`、`check_frontend_i18n.py` 与 `npm --prefix ... run typecheck`）。
本文件补的是这两个脚本**看不到**的部分，避免与它们重复：

- 脚本只查「组件不裸用 axios/fetch」，不查拦截器**是否真的注入了三个头**；
- 脚本不区分 `code != 0` 与 401 两条分支的口径（401 只能在**非** `/login` 时跳转，否则登录页
  自身的失败会退化成重定向循环）；
- 脚本看不见「密码/令牌是否被写进浏览器存储」（locale 与 theme 是仅有的两处 storage 用途）。

B-05 本身只覆盖 i18n 双语完整性；其余断言属 TASK-010 的「契约断言」checklist 项，与 B-05 同在
本文件取证。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
API_DIR = SRC / "api"
LOCALES = SRC / "locales"

AUTH_TS = API_DIR / "auth.ts"
CLIENT_TS = API_DIR / "client.ts"
AUTH_CONTEXT = SRC / "auth/AuthContext.tsx"
LOGIN_PAGE = SRC / "pages/LoginPage.tsx"
I18N = SRC / "i18n/index.ts"
SHELL = SRC / "layout/AppLayout.tsx"

# 本模块的源码面：认证请求层 + 会话上下文 + 登录页 + i18n 单例
AUTH_SOURCES = (AUTH_TS, CLIENT_TS, AUTH_CONTEXT, LOGIN_PAGE, I18N)

# 允许访问浏览器存储的文件与键（locale / theme 是仅有的两处；不含任何凭据）
ALLOWED_STORAGE_KEYS = {"muad.locale", "muad.theme"}
STORAGE_FILES = (I18N, SRC / "theme.ts")

# 组件/展示层不得出现的裸 HTTP 客户端
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")

T_KEY = re.compile(r"(?<![A-Za-z_])t\(\s*'([^']+)'")

CJK = re.compile(r"[　-〿一-鿿！-～]")


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(source))


def _frontend_sources() -> list[Path]:
    return sorted(
        path
        for path in list(SRC.rglob("*.ts")) + list(SRC.rglob("*.tsx"))
        if path.suffix in {".ts", ".tsx"}
    )


def _load_locale(name: str) -> dict[str, str]:
    payload = json.loads(_read(LOCALES / f"{name}.json"))
    assert all(isinstance(value, str) for value in payload.values()), (
        "词条须是扁平 JSON + 点号键（值为字符串），不得嵌套对象"
    )
    return payload


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    return _load_locale("zh-CN"), _load_locale("en-US")


def _referenced_keys() -> set[str]:
    """本模块源码里静态 `t('key')` 引用的键。"""
    referenced: set[str] = set()
    for path in AUTH_SOURCES:
        referenced |= set(T_KEY.findall(_strip_comments(_read(path))))
    return referenced


def _shell_referenced_keys() -> set[str]:
    """壳层（AppLayout）里静态 `t('key')` 引用的键——本模块部分词条由壳层消费。"""
    return set(T_KEY.findall(_strip_comments(_read(SHELL))))


def _storage_keys() -> dict[str, set[str]]:
    """各文件里出现的 storage 键字面量（含经常量间接使用的键名声明）。"""
    found: dict[str, set[str]] = {}
    for path in _frontend_sources():
        source = _strip_comments(_read(path))
        keys = set(re.findall(r"localStorage\.(?:get|set|remove)Item\(\s*'([^']+)'", source))
        keys |= set(re.findall(r"^const\s+\w*KEY\w*\s*=\s*'([^']+)'", source, re.MULTILINE))
        if keys:
            found[path.relative_to(ROOT).as_posix()] = keys
    return found


def test_b05_auth_entries_are_complete_in_both_locales() -> None:
    """[B-05] 登录页与本模块引用的词条在 zh-CN/en-US 双侧齐备、非空、键集一致。"""
    zh, en = _locales()
    referenced = _referenced_keys()

    # 非空过：登录页与壳层的关键键必须在解析结果里，正则失效时立刻暴露
    assert {
        "login.title",
        "login.username",
        "login.password",
        "login.usernameRequired",
        "login.passwordRequired",
        "login.submit",
        "app.brand",
        "app.title",
    } <= referenced, f"静态键解析疑似失效：{sorted(referenced)}"

    missing = {
        name: sorted(key for key in referenced if key not in payload)
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"词条缺失：{missing}"

    blank = sorted(key for key in referenced if not zh[key].strip() or not en[key].strip())
    assert not blank, f"空词条：{blank}"


def test_b05_auth_key_sets_are_identical_and_all_referenced() -> None:
    """[B-05] 两侧 `login.*`/`auth.*` 键集一致；本任务拥有的 `login.*` 无多余词条。

    孤儿判定只覆盖 `login.*`（本任务的交付面）。`auth.role.admin`/`auth.role.builder` 是设计
    FEAT-FE-05 要求的角色展示词条，由**壳层 Header** 的 `display_name · 角色` 消费——该 Header
    属 TASK-011（登录页/守卫/角色过滤/退出）的实现面，本任务阶段尚未引用，故不计为孤儿；
    `auth.logout` 已在 `layout/AppLayout.tsx` 被引用（见下方非空过断言）。
    """
    zh, en = _locales()
    prefixes = ("login.", "auth.")
    zh_keys = {key for key in zh if key.startswith(prefixes)}
    en_keys = {key for key in en if key.startswith(prefixes)}
    assert zh_keys, "未解析到 login.*/auth.* 词条"
    assert zh_keys == en_keys, (
        f"键集不一致：仅 zh-CN={sorted(zh_keys - en_keys)}；仅 en-US={sorted(en_keys - zh_keys)}"
    )

    # 非空过：角色词条确实存在（TASK-011 依赖它们），且退出词条已被壳层引用
    assert {"auth.role.admin", "auth.role.builder", "auth.logout"} <= zh_keys
    assert "auth.logout" in _shell_referenced_keys(), "auth.logout 应已由壳层 Header 引用"

    referenced = _referenced_keys()
    orphans = sorted(key for key in zh_keys if key.startswith("login.") and key not in referenced)
    assert not orphans, f"login.* 词条无人引用（多余词条）：{orphans}"


def test_only_api_layer_reaches_the_http_client() -> None:
    """[RULE-front-001] 只有 `src/api/` 可直接用 axios/fetch；组件与页面一律经服务层。"""
    offenders: dict[str, list[str]] = {}
    for path in _frontend_sources():
        if API_DIR in path.parents:
            continue
        hits = [
            line.strip()
            for line in _strip_comments(_read(path)).splitlines()
            if BARE_HTTP.search(line)
        ]
        if hits:
            offenders[path.relative_to(ROOT).as_posix()] = hits
    assert not offenders, f"组件/展示层出现裸 HTTP 客户端：{offenders}"

    # 非空过：客户端本身必须命中该正则，否则说明模式失效、上面的断言是空转
    assert BARE_HTTP.search(_read(CLIENT_TS)), "src/api/client.ts 未命中 axios 模式，断言失效"


def test_api_layer_exposes_the_four_auth_methods() -> None:
    """[RULE-front-001] 设计 §3.5 的四个 Service 方法齐备且打对应后端接口。"""
    source = _compact(_read(AUTH_TS))
    methods = {
        "login": "/auth/login",
        "logout": "/auth/logout",
        "fetchCurrentAccount": "/auth/me",
        "changePassword": "/auth/password",
    }
    for name, endpoint in methods.items():
        assert f"exportasyncfunction{name}(" in source, f"缺少 Service 方法 {name}"
        assert f"'{endpoint}'" in source, f"{name} 未打 {endpoint}"
    assert "api.post" in source and "api.get" in source, "须经 src/api/client 的 axios 实例"


def test_request_interceptor_injects_three_headers() -> None:
    """[RULE-front-001] 请求拦截器注入 `X-Locale` / `X-Request-Id` / `X-CSRF-Token`。"""
    client = _compact(_read(CLIENT_TS))
    assert "constapi=axios.create({" in client, "缺少 axios 实例"
    assert "api.interceptors.request.use(" in client, "缺少请求拦截器"
    assert "config.headers['X-Locale']=currentLocale()" in client, "未注入 X-Locale"
    assert "config.headers['X-Request-Id']=newRequestId()" in client, "未注入 X-Request-Id"
    assert "config.headers[CSRF_HEADER]=csrfToken" in client, "未注入 X-CSRF-Token"
    assert "readCookie(CSRF_COOKIE)" in client, "CSRF 令牌须取自 Cookie"
    assert "if(csrfToken)" in client, "无 CSRF Cookie 时不得写入空头"

    # 非安全上下文（http://<内网IP>）没有 crypto.randomUUID，请求 ID 必须有兜底，否则拦截器抛错
    assert "typeofcrypto.randomUUID==='function'" in client, "randomUUID 未做存在性判断"
    assert "crypto.getRandomValues" in client, "缺少非 secure context 的兜底实现"


def test_envelope_error_toasts_and_rejects() -> None:
    """[RULE-front-001] 统一封套：`code != '0'` → Toast 本地化 `msg` 且 reject（调用方不失联）。"""
    client = _compact(_read(CLIENT_TS))
    assert "body.code!=='0'" in client, "未按封套 code 判定成功与否"
    assert "Toast.error({content:body.msg})" in client, "封套失败未 Toast 本地化 msg"
    assert "returnPromise.reject(body)" in client, "封套失败必须 reject，调用方才能复位 loading"


def test_unauthorized_redirects_only_outside_the_login_route() -> None:
    """[RULE-front-001] 401 → `window.location.assign('/login')`，且**仅**在非 `/login` 时跳转。"""
    client = _compact(_read(CLIENT_TS))
    assert "exportconstUNAUTHORIZED_STATUS=401" in client, "401 口径须有单一常量"
    assert "error.response?.status===UNAUTHORIZED_STATUS" in client, "未按状态码判定 401"
    assert "window.location.pathname!=='/login'" in client, (
        "缺少 /login 守卫：登录页自身的 401 会造成重定向循环"
    )
    assert "window.location.assign(" in client and "`/login?returnUrl=${returnUrl}`" in client, (
        "401 须跳登录页并带 returnUrl"
    )


def test_credentials_never_reach_browser_storage() -> None:
    """[RULE-front-001] 密码/令牌不落 localStorage/sessionStorage（设计 §3.5 数据获取层）。"""
    storage_keys = _storage_keys()
    assert storage_keys, "未解析到任何 storage 键，断言疑似失效"

    unexpected = {
        path: sorted(keys - ALLOWED_STORAGE_KEYS)
        for path, keys in storage_keys.items()
        if keys - ALLOWED_STORAGE_KEYS
    }
    assert not unexpected, f"出现白名单外的 storage 键：{unexpected}"

    allowed_files = {path.relative_to(ROOT).as_posix() for path in STORAGE_FILES}
    assert set(storage_keys) <= allowed_files, (
        f"仅 {sorted(allowed_files)} 可访问浏览器存储，实际：{sorted(storage_keys)}"
    )

    for path in AUTH_SOURCES:
        if path in STORAGE_FILES:
            continue
        source = _strip_comments(_read(path))
        assert "localStorage" not in source and "sessionStorage" not in source, (
            f"{path.name} 触碰浏览器存储：凭据只允许存在于提交函数参数中"
        )

    # 存储键与写入值都不得含凭据语义
    for path, keys in storage_keys.items():
        for key in keys:
            assert not re.search(r"password|token|secret|credential", key, re.IGNORECASE), (
                f"{path} 的存储键 {key} 带凭据语义"
            )


def test_auth_sources_carry_no_hardcoded_chinese() -> None:
    """[RULE-i18n-001] 本模块源码不承载硬编码中文文案（文案一律走词条）。"""
    offenders = {
        path.name: [
            text
            for text in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", _strip_comments(_read(path)))
            for text in text
            if text and CJK.search(text)
        ]
        for path in AUTH_SOURCES
    }
    assert not any(offenders.values()), f"出现硬编码中文文案：{offenders}"
