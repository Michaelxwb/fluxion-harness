"""Preserve the old MSS Claw canonical-request byte contract."""

import hashlib
import hmac
from urllib.parse import urlsplit


def login_authorization(url: str, ak: str, sk: str, timestamp: int) -> str:
    parsed = urlsplit(url)
    path = parsed.path
    if path.startswith("/gateway/"):
        rest = path[len("/gateway/") :]
        path = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    canonical = f"POST\n{path}\n"
    if parsed.query:
        # TS passes URL.search including '?'; retain it for parity.
        canonical += "&".join(sorted(("?" + parsed.query).split("&"))) + "\n"
    canonical += f"content-type:application/json\nsign-date:{timestamp}\ncontent-type;sign-date"
    digest = hashlib.sha256(canonical.encode()).hexdigest().upper()
    signing = f"HMAC-SHA256\n{timestamp}\n{digest}"
    signature = hmac.new(sk.encode(), signing.encode(), hashlib.sha256).hexdigest().upper()
    return (
        f"algorithm=HMAC-SHA256,Access={ak},SignedHeaders=content-type;sign-date,"
        f"Signature={signature},sign-date={timestamp}"
    )
