"""只读取 QQ 图片 CDN 的图片，用本地 OCR 提取文字。"""

import io
import threading
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image


_OCR_LOCK = threading.Lock()
_OCR_ENGINE = None
_MAX_BYTES = 6 * 1024 * 1024
_HOST_SUFFIXES = ("qq.com", "qpic.cn", "gtimg.cn")


def _trusted_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix) for suffix in _HOST_SUFFIXES
    )


def _secure_qq_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "http":
        url = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path,
                                       parsed.query, parsed.fragment))
    if not _trusted_url(url):
        raise ValueError("只支持 QQ 图片 CDN 的 HTTPS 地址")
    return url


class _QQRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise ValueError("图片重定向到了非 QQ 图片域名")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _download(url: str) -> bytes:
    url = _secure_qq_url(url)
    opener = urllib.request.build_opener(_QQRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "QQYM/1.0"})
    with opener.open(request, timeout=12) as response:
        data = response.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValueError("图片超过 6 MB")
        return data


def read_image_text(url: str) -> str:
    global _OCR_ENGINE
    data = _download(url)
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 20_000_000:
            raise ValueError("图片尺寸过大")
        image = source.convert("RGB")
    image.thumbnail((2200, 2200))
    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            from rapidocr import RapidOCR
            _OCR_ENGINE = RapidOCR()
        result = _OCR_ENGINE(image)
    texts = getattr(result, "txts", None) or []
    scores = getattr(result, "scores", None)
    lines = []
    for index, value in enumerate(texts):
        if scores is not None and index < len(scores) and float(scores[index]) < 0.35:
            continue
        value = str(value).strip()
        if value:
            lines.append(value)
    return "\n".join(lines)[:4000]
