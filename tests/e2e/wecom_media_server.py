"""本地真实媒体服务 + 官方加密算法：企微取件链路的共享测试基座。

用途：集成/验收测试里替代企微的媒体 CDN —— **真实 HTTP、真实字节、真实 AES-256-CBC 密文**，
使"流式下载 → 解密 → 类型与校验和判定"走完整真实路径。它只替代外部端点，**不替代生产
`download_media`/适配器**，也不代表企微实网已验收。

放在 `tests/e2e/` 是因为网关单元测试（`tests/gateway/test_wecom_media.py`）与验收栈
（`tests/acceptance/im_gateway/`）都要用它：验收栈需要**网关子进程也能访问**的真实媒体源，
而这两处的服务端语义必须完全一致，否则"同一套断言在两层得到同一结论"就无从谈起。
"""

from __future__ import annotations

import base64
import http.server
import threading
import time
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

#: 官方算法：AES-256-CBC，IV = 密钥前 16 字节，PKCS#7 填充。
AES_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
SERVER_CHUNK_BYTES = 64 * 1024


class MediaServer:
    """本地真实 HTTP 服务：按路径返回字节，可配置文件名、响应前延迟与发送节流。"""

    def __init__(self) -> None:
        self.routes: dict[str, bytes] = {}
        self.filenames: dict[str, str] = {}
        self.delays: dict[str, float] = {}
        self.chunk_delay = 0.0
        self.sent: dict[str, int] = {}
        handler = _media_handler(self)
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve(self, path: str, body: bytes, *, filename: str | None = None, delay: float = 0.0) -> str:
        self.routes[path] = body
        self.delays[path] = delay
        if filename is not None:
            self.filenames[path] = filename
        return f"{self.base_url}{path}"

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _media_handler(media: MediaServer) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
            body = media.routes.get(self.path)
            if body is None:
                self.send_error(404)
                return
            delay = media.delays.get(self.path, 0.0)
            if delay:
                time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            filename = media.filenames.get(self.path)
            if filename is not None:
                # 非 ASCII 文件名只能走 RFC 5987：`send_header` 按 latin-1 编码，直接塞中文会抛
                self.send_header("Content-Disposition", disposition_header(filename))
            self.end_headers()
            written = 0
            try:
                for offset in range(0, len(body), SERVER_CHUNK_BYTES):
                    chunk = body[offset : offset + SERVER_CHUNK_BYTES]
                    self.wfile.write(chunk)
                    written += len(chunk)
                    if media.chunk_delay:
                        time.sleep(media.chunk_delay)
            except (BrokenPipeError, ConnectionResetError):
                # 客户端在超上限时主动断开——这正是"没读完"的证据
                pass
            finally:
                media.sent[self.path] = written

        def log_message(self, *args: object) -> None:
            """静音默认的 stderr 访问日志。"""

    return Handler


def disposition_header(filename: str) -> str:
    """按服务端实际做法构造 `Content-Disposition`：ASCII 走 `filename=`，非 ASCII 走 RFC 5987。"""
    if filename.isascii():
        return f'attachment; filename="{filename}"'
    return f"attachment; filename*=UTF-8''{quote(filename, safe='')}"


def wecom_ciphertext(plaintext: bytes, *, aes_key: str = AES_KEY) -> bytes:
    """按官方算法产出**真实密文**：AES-256-CBC，IV = 密钥前 16 字节，PKCS#7 填充。"""
    key = base64.b64decode(aes_key)
    pad_len = 16 - len(plaintext) % 16
    padded = plaintext + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return encryptor.update(padded) + encryptor.finalize()
