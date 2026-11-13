# MSSW 平台 JWT 规格与实测验证

> **权威来源**：签发方 `D:\Code\MSS\mss-auth-go`（Go，MSSW 鉴权中心）
> · `internal/utils/jwt.go` —— `JwtData` 结构、`PublishJwt` 签发、`DecodeJwt` 解析
> · `internal/middleware/jwtmiddleware.go` —— 服务端验签与取身份
> · `internal/logic/certification/loginJobNumberLogic.go` —— 工号登录签发点
> · `internal/types/constant.go`、`types.go` —— 常量与字段校验
>
> **实测验证**：2026-11-13 用开发环境真实 token 跑通（本文第 5 节）。
>
> 本文记录**对方代码的事实**，不是我们的设计决策；取舍见 `02-requirements.md`。

## 1. Token 结构

```
Header:  {"alg": "RS256", "typ": "JWT"}
签名:    RS256，PKCS#1 私钥签发（签发方持有）

Payload（JwtData，jwt.go:16-42）:
{
  // —— 身份字段（全部明文）——
  "job_number":    "<工号，5-7 位字母数字>",        // ← 我方映射键
  "_id":           "<专家ID，MongoDB ObjectId 24 位 hex>",  // ← 数据库主键，非映射键
  "name":          "<姓名>",
  "tenant_id":     "<租户ID>",
  "tenant_name":   ...,
  "expert_type":   ...,
  "sdsp_id":       ..., "prm_id": ..., "channel": ..., "channel_id": ...,
  "app_id":        "<如 mss_default>",
  "roles":         ["<角色ID>"],
  "role":          {"_id": "<角色ID>", "name": ..., "role_code": ...},  // ← 嵌套 _id 是角色
  "login-source":  ..., "login_time": ..., "time": <int64>,
  "ak":            "<仅 Agent 登录>",
  "X-External-Stream": ...,

  // —— AES-256-ECB 加密字段 ——
  "email":         "<密文>",
  "email_login":   "<密文>",
  "phone_login":   "<密文>",
  "aud":           "<密文>",
  "iss":           ["<密文>"],        // 单元素数组

  // —— 过期字段 ——
  "exp":           -1                 // 固定写死，服务端不校验
}
```

## 2. 关键事实

| # | 事实 | 依据 |
|---|---|---|
| 1 | **映射键是 `job_number`**（工号，5-7 位字母数字），全局唯一 | 需求方确认；`types.go:328` 校验 `min=5,max=7,alphanum` |
| 2 | `_id` 是**专家 ID**（MongoDB `ObjectId.Hex()`，24 位 hex），是数据库主键但**不作映射键** | `jwtmiddleware.go:19` 注释「//专家id」；`loginJobNumberLogic.go:101` 传 `expert.ID.Hex()` |
| 3 | `role` 对象内部另有一个 `_id`（角色 ID），不要与顶层 `_id` 混淆 | `jwt.go:11`（`RoleInfo`） |
| 4 | 只有 5 个字段被 AES 加密：`email`、`email_login`、`phone_login`、`aud`、`iss` | `jwt.go:81-85` |
| 5 | `job_number`、`_id`、`tenant_id` 等**均为明文** | 同上 |
| 6 | `aud` 是密文字符串，`iss` 是密文的**单元素数组** | `jwt.go:84-85` |
| 7 | 算法固定 RS256 | `jwt.go:94` |
| 8 | **`exp` 固定 -1，服务端 `WithoutClaimsValidation()`，不校验过期** | `jwt.go:86`、`jwt.go:67` |
| 9 | 公钥是 **PKCS#1**（`BEGIN RSA PUBLIC KEY`），非 SPKI | `jwt.go:53-58` |
| 10 | 服务端验签**未做算法白名单**（回调不检查 `token.Method`） | `jwt.go:65-67` |

### 2.1 `exp` 不承载过期语义

签发方无条件写 `Exp = -1`（`jwt.go:86`），解析时 `WithoutClaimsValidation()`（`jwt.go:67`）跳过全部 claims 校验。会话有效性由 Redis 管理（`jwtmiddleware.go:97` 只验签 + 取 `_id`/`app_id`；吊销靠 `ClearAgentJwt` 清 Redis，见 `types.go:14-18`）。

**影响**：我方**不能**依赖 `exp`。这也是 MSSW 通道 SSE 不能复用 `ChatStreamGuard` 的根本原因。

**签发方的真实过期语义（滑动续期，`authchecklogic.go:303-323`）**：

```
每次验签：
  1. CheckLoginJwt(redisKey, token)          ← Redis key 不在 → 失效
  2. 概率性 RefreshLoginJwt(LoginJwtTokenExpire)  ← 把 TTL 重置回完整时长
```

三个要点：① 过期判断在 **Redis key 存在性**上，不在 JWT 里；② 只要用户活跃就不断续期，会话**永不到期**；③ 注销靠删 key（`ClearAgentJwt`）。该 Redis 在 MSSW 侧，**我方不可见**，因此无法判断 token 是否已被吊销，也做不到与签发方**等价**的到期判定——只能做近似（策略见 `05` §4.3）。

### 2.1.1 时间线索：`login_time` / `time`（只用于可观测性）

`exp` 无意义，但 payload 里有两个**签发时刻**字段，同一次调用写入（`commonlogic.go:118`、`:123`）：

| 字段 | 类型 | 值 | 位置 |
|---|---|---|---|
| `login_time` | **string** | 签发时刻的 Unix 毫秒时间戳 | `jwt.go:30`、`commonlogic.go:118` |
| `time` | **int64** | 与 `login_time` 同值的毫秒时间戳 | `jwt.go:35`、`commonlogic.go:123` |

两者是同一个时刻的两种表示（一个是 `strconv.FormatInt` 的字符串，一个是原生 int64），**取其一即可**，推荐 `time`（无需字符串解析）。

**这两个字段不能用来做重放防护**：`time` 是**签发**时刻，不是**最后使用**时刻——同一枚 token 在签发后每次请求都携带同一个 `time`（BFF 复用转发，`RefreshLoginJwt` 只重置 MSSW 侧 Redis TTL、**不重签 JWT**）。因此：

- 一个活跃用户连续用满 2 小时以上，`time` 仍是登录那一刻，任何"签发时间窗"都会误杀他；
- 一枚 2 小时前签发、现在才被重放的旧 token，与上面那种在 JWT 字段上完全相同，本地分不开。

**结论**：本地做任何基于 `time` 的时间窗，都只会误杀合法用户而不解决重放。**token 有效性/吊销由 BFF 在转发前判定**（见 `02` §3.3.1）。`time`/`login_time` 在我方只用于日志可观测性（记录签发时刻、排查异常长寿命 token），**不参与任何拒绝决策**。

**参考值**：`LoginJwtTokenExpire` 是硬编码常量，值 **7200 秒（2 小时）**（`mss-auth-go` 的 `internal/types/constant.go:68`，无配置/环境变量覆盖）。它描述签发方 Redis 会话在用户停止活跃后的滑动 TTL，仅作背景事实记录，我方不使用。

### 2.2 公钥格式

签发方用 `x509.ParsePKCS1PublicKey` 且要求 PEM 块类型为 `RSA PUBLIC KEY`。

Python 侧（PyJWT）**实测可直接加载 PKCS#1**，无需转换。若遇到版本差异，可转换：

```bash
openssl rsa -RSAPublicKey_in -in pkcs1.pub.pem -pubout -out spki.pub.pem
```

## 3. AES 细节

两侧实现一致（`jwt.go` 的 `AesEncryptECB` 与 `mss_std` 的 `PyAesCipherECB`）。

| 属性 | 值 |
|---|---|
| 算法 | AES-256-ECB，无 IV |
| Key 派生 | `salt` 右侧补 `\x00` 到 32 字节；`salt` 长度须 ≤ 32 |
| Padding | PKCS7 风格：补 `16 - len % 16` 个对应字节 |
| 密文编码 | base64 |
| Unpad | 按末字符 ASCII 值截断，**非严格 PKCS7** |

> **关键做法**：校验 `aud`/`iss` 时**不需要解密**——用同一 `salt` 加密期望值后做字符串比较即可（签发方就是这么生成的）。这样完全绕开非标准 unpad 的兼容问题。

## 4. Token 传递方式

### 4.1 签发方（Go）

`jwtmiddleware.go:82-113` 的优先级：SMS 认证 Cookie → **`soc-token` Cookie** → 预登录 Cookie。**只读 Cookie，不读请求头。**

Cookie 属性（`loginJobNumberLogic.go:106-113`）：`Name=soc-token`、`Path=/`、`Secure=true`、`HttpOnly=true`、`SameSite=Strict`。

### 4.2 调用方（Python）

```python
if request.headers.get("X-From-Src") != "external":
    return None, "internal"      # 非 external 跳过校验
if request.cookies.get("soc-token"):
    token = request.cookies.get("soc-token")
if request.headers.get("soc-token"):
    token = request.headers.get("soc-token")   # 头优先于 Cookie
```

**两侧差异**：

| 维度 | mss-auth-go | mss_std (Python) |
|---|---|---|
| 读取位置 | 仅 Cookie | 头 + Cookie，头优先 |
| 外部标记头 | `X-External-Stream` | `X-From-Src: external` |
| 免校验 | 内部路径/白名单 | `X-From-Src != external` 即跳过 |

### 4.3 我方结论

**已实测确认**：token 以**请求头内的 Cookie** 传递，键名 `soc-token`（`-b 'soc-token=...'`）。

**BFF 是后端服务，直接转发请求，不是浏览器跨站访问**——因此 `SameSite=Strict` **不构成障碍**，Cookie 会正常送达。

我方**不实现**签发方的免校验分支：入口只服务外部平台，一律强制验签。

## 5. 实测验证

用开发环境真实 token + 配置在本机 Python 3 验证，**全部通过**。

### 5.1 输入物料

| 项 | 值 |
|---|---|
| `audience` | `9d7cdc4178c342482b0fb26fa75b8f71` |
| `issue` | `bce0e2127824dea11cb153bad3ae4512` |
| `salt` | `dxbwzhls2019qflzwcwwyc510zxgwlzm`（32 字节，无需补零） |
| 公钥 | PKCS#1 PEM |

### 5.2 结果

| 项 | 结果 |
|---|---|
| RS256 验签 | **VALID** |
| `aud` 密文比对 | **match** |
| `iss` 密文比对 | **match** |
| `_id` | `6a4ca2554a2a45d4b1ad5996`（24 位 hex） |
| **`job_number`** | **`14806`** |
| `tenant_id` | `1001` |
| `exp` | `-1` |
| `email` 解密 | `root@sangfor.com`（业务不需要） |
| `alg=none` 攻击 | **REJECTED**（`InvalidAlgorithmError`） |

### 5.3 可复现脚本

```python
import base64
import jwt as pyjwt
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

SALT = b"dxbwzhls2019qflzwcwwyc510zxgwlzm"
AUDIENCE = "9d7cdc4178c342482b0fb26fa75b8f71"
ISSUE = "bce0e2127824dea11cb153bad3ae4512"

def key32(salt: bytes) -> bytes:
    s = salt[:32]
    return s + b"\x00" * (32 - len(s))

def aes_enc(data: str) -> str:
    return base64.b64encode(
        AES.new(key32(SALT), AES.MODE_ECB).encrypt(pad(data.encode(), 16))
    ).decode()

claims = pyjwt.decode(
    token, public_key_pem,
    algorithms=["RS256"],
    options={"verify_exp": False, "verify_aud": False, "verify_iss": False},
)
assert claims["aud"] == aes_enc(AUDIENCE)
assert claims["iss"] == [aes_enc(ISSUE)]
job_number = claims["job_number"]
```

## 6. 参考实现要点

```python
claims = jwt.decode(
    token,
    key=public_key,
    algorithms=["RS256"],           # 仅 RS256，禁止 none / HS*
    options={
        "verify_exp": False,        # exp 恒为 -1
        "require": ["job_number"],  # 必需身份字段
    },
)
job_number = claims["job_number"]
```

**必须做**：RS256 白名单、必需 `job_number`、fail-closed 401。
**不要做**：解密 `email`/`phone_login`、实现 unpad、校验 `exp`、复刻免校验分支。

## 7. 与 `py_jwt_cipher` 的区分（避免误用）

`easm-exposure-service-mssw` 仓库的 `src/lib/tss_vms/utils/bjx_py_cipher/py_jwt_cipher.py` 是**另一套东西**：payload 无用户身份字段、用途是文件服务请求间令牌（`X-TOKEN` 调 fdfs）、AES 是 CBC 模式且 key 由三段拼接派生。

**不要参考、不要照搬。** 平台用户身份令牌以本文为准。
