"""网页正文抓取 connector —— httpx 拉 HTML + trafilatura 抽正文/标题/发布日期。

证据链最后一公里：web_search 只有标题 + snippet，没法核一手来源（财报 / 公告 /
transcript 原文）；本 connector 把 URL 变成可引用的正文文本。

护栏（与 web_search 同一治理思路）：
- SSRF：仅 http/https；目标主机解析为回环 / 私网 / 链路本地地址一律拒绝
  （data 服务同机还跑着 paper / research 等内网端点，不能让 LLM 借 fetch 探内网）
- 大小：响应体 max_bytes 截断（流式读，防大文件吃内存）
- 类型：仅 text/html / text/plain / xml 族
- 并发：Semaphore 限同时在飞数；trafilatura 解析走 to_thread（纯 CPU，别堵事件循环）
- 失败语义：所有错误返回 ``{"error": ...}``，不抛——fetch 是尽力而为的增强项
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from inalpha_shared import get_logger

from ..config import get_data_settings

VENUE = "web"
_logger = get_logger(__name__)

_ALLOWED_CONTENT_PREFIXES = ("text/html", "text/plain", "application/xhtml", "application/xml", "text/xml")


def _is_private_host(host: str) -> bool:
    """主机名解析后任一地址是回环 / 私网 / 链路本地 / 保留地址 → True。

    解析失败按"非私网"放行——连接阶段自然会失败，错误语义更准确。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
            return True
    return False


def _extract_sync(html: str, url: str) -> dict[str, Any]:
    """trafilatura 抽正文 + 元数据；失败 / 空结果回退 bs4 纯文本。"""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html, url=url, output_format="json", with_metadata=True
        )
        if extracted:
            data = json.loads(extracted)
            text = (data.get("text") or "").strip()
            if text:
                return {
                    "title": data.get("title"),
                    "published_at": data.get("date"),
                    "text": text,
                }
    except Exception as exc:
        _logger.debug("web_fetch_trafilatura_failed", url=url[:200], error=str(exc))

    # fallback：bs4 去 script/style 后的纯文本（boilerplate 较多但聊胜于无）
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else None
        text = " ".join(soup.get_text(" ").split())
        return {"title": title, "published_at": None, "text": text}
    except Exception as exc:
        _logger.debug("web_fetch_bs4_failed", url=url[:200], error=str(exc))
        return {"title": None, "published_at": None, "text": ""}


class WebFetchConnector:
    """URL → 正文文本。零 key；外部站点走系统代理（trust_env 默认）。"""

    def __init__(self) -> None:
        s = get_data_settings()
        self._timeout = s.web_fetch_timeout_s
        self._max_bytes = s.web_fetch_max_bytes
        self._max_chars = s.web_fetch_max_chars
        self._sem = asyncio.Semaphore(s.web_fetch_max_concurrency)

    async def fetch_page(self, url: str, max_chars: int | None = None) -> dict[str, Any]:
        """抓取 URL 并抽正文。

        Returns:
            成功：``{url, final_url, title, published_at, text, truncated, fetched_at}``；
            失败：``{url, error}``。永不抛。
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"url": url, "error": "only http/https URLs are allowed"}
        if not parsed.hostname:
            return {"url": url, "error": "invalid URL: missing host"}
        if await asyncio.to_thread(_is_private_host, parsed.hostname):
            return {"url": url, "error": "private/loopback hosts are not allowed"}

        limit = min(max_chars or self._max_chars, self._max_chars)
        async with self._sem:
            try:
                return await asyncio.wait_for(
                    self._fetch_and_extract(url, limit), timeout=self._timeout
                )
            except TimeoutError:
                _logger.warning("web_fetch_timeout", url=url[:200], timeout_s=self._timeout)
                return {"url": url, "error": f"fetch timed out after {self._timeout}s"}
            except Exception as exc:
                _logger.warning("web_fetch_error", url=url[:200], error=str(exc))
                return {"url": url, "error": str(exc)}

    async def _fetch_and_extract(self, url: str, max_chars: int) -> dict[str, Any]:
        headers = {
            # 不少财经站对裸 UA 返 403；用常规浏览器 UA（公开页面，非绕权限）
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
            "Accept-Language": "en,zh;q=0.8",
        }
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=5, headers=headers
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return {"url": url, "error": f"HTTP {resp.status_code}"}
                ctype = resp.headers.get("content-type", "").lower()
                if ctype and not any(ctype.startswith(p) for p in _ALLOWED_CONTENT_PREFIXES):
                    return {"url": url, "error": f"unsupported content-type: {ctype}"}
                # 重定向落点二次校验（防 302 跳内网）
                final_host = resp.url.host or ""
                if await asyncio.to_thread(_is_private_host, final_host):
                    return {"url": url, "error": "redirect target is a private host"}

                chunks: list[bytes] = []
                read = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    read += len(chunk)
                    if read >= self._max_bytes:
                        break
                html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                final_url = str(resp.url)

        data = await asyncio.to_thread(_extract_sync, html, final_url)
        text = data.get("text") or ""
        truncated = len(text) > max_chars
        return {
            "url": url,
            "final_url": final_url,
            "title": data.get("title"),
            "published_at": data.get("published_at"),
            "text": text[:max_chars],
            "truncated": truncated,
            "fetched_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    async def close(self) -> None:
        return None


# ---------- module-level singleton ----------

_connector: WebFetchConnector | None = None


def init_connector() -> WebFetchConnector:
    global _connector
    if _connector is not None:
        raise RuntimeError("WebFetch connector already initialized")
    _connector = WebFetchConnector()
    return _connector


async def close_connector() -> None:
    global _connector
    if _connector is None:
        return
    await _connector.close()
    _connector = None


def get_connector() -> WebFetchConnector:
    if _connector is None:
        raise RuntimeError("WebFetch connector not initialized; call init_connector() first")
    return _connector
