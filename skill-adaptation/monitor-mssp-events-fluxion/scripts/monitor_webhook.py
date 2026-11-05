"""独立发送监控报告与事件产物的企微消息，不复用事件文案发送流程。

支持两种消息：纯文本（监控报告 / C2 处置通告）与图片（处置跟进卡）。
图片必须走 base64 直发：企微群机器人的 upload_media 不支持 type=image，
拿不到可用的 media_id。
"""

import base64
import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


MAX_TEXT_BYTES = 2048
SEND_INTERVAL_SECONDS = 3.1
WEBHOOK_PREFIX = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="


def validate_url(url: str) -> str:
    """接受完整 URL 或单独的 key；只在内存中补全固定企微前缀。"""
    url = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", url):
        url = WEBHOOK_PREFIX + url
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "qyapi.weixin.qq.com"
            or parts.path != "/cgi-bin/webhook/send" or parts.fragment
            or not urllib.parse.parse_qs(parts.query).get("key", [""])[0].strip()):
        raise ValueError("请提供企微群机器人 Webhook 地址或单独的 key")
    return url


def split_text(content: str) -> list[str]:
    """按 UTF-8 字符边界拆分，拼接后与监控报告原文一致。"""
    chunks, buffer, size = [], [], 0
    for char in content:
        length = len(char.encode("utf-8"))
        if size + length > MAX_TEXT_BYTES:
            chunks.append("".join(buffer))
            buffer, size = [], 0
        buffer.append(char)
        size += length
    if buffer:
        chunks.append("".join(buffer))
    return chunks


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)


def post_text(url: str, content: str) -> None:
    payload = {"msgtype": "text", "text": {"content": content}}
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        raise RuntimeError("监控文字发送失败：网络或回执异常") from None
    if not isinstance(result, dict) or type(result.get("errcode")) is not int:
        raise RuntimeError("监控文字发送失败：缺少有效 errcode")
    if result["errcode"] != 0:
        raise RuntimeError(f"监控文字发送失败：企微 errcode={result['errcode']}")


def send_image(url: str, image_path: str) -> int:
    """把本地图片以 base64 直发到企微群机器人。返回发送字节数。

    ⚠️ 企微群机器人 upload_media 不支持 type=image（拿不到可用 media_id），
    图片**只能**走 `msgtype=image` + base64/md5 直发。
    """
    url = validate_url(url)
    path = str(image_path or "").strip()
    if not path or not os.path.isfile(path):
        raise RuntimeError(f"图片不存在：{os.path.basename(path) or '未提供路径'}")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise RuntimeError(f"图片读取失败：{exc}") from None
    if not raw:
        raise RuntimeError("图片为空文件")
    # 企微图片上限 2MB（base64 前）
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError(f"图片超过 2MB 限制（{len(raw) // 1024}KB）")

    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(raw).decode("ascii"),
            "md5": hashlib.md5(raw).hexdigest(),
        },
    }
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        raise RuntimeError("图片发送失败：网络或回执异常") from None
    if not isinstance(result, dict) or type(result.get("errcode")) is not int:
        raise RuntimeError("图片发送失败：缺少有效 errcode")
    if result["errcode"] != 0:
        raise RuntimeError(f"图片发送失败：企微 errcode={result['errcode']}")
    return len(raw)


def send_report(url: str, report: str) -> int:
    url = validate_url(url)
    chunks = split_text(report)
    for index, chunk in enumerate(chunks):
        if index:
            time.sleep(SEND_INTERVAL_SECONDS)
        try:
            post_text(url, chunk)
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}（已发送 {index}/{len(chunks)} 段，后续段未发送）") from None
    return len(chunks)
