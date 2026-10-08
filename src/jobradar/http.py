"""Tiny HTTP helper on the standard library (no `requests` dependency)."""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = "JobRadar/0.1 (personal job search tool)"
TIMEOUT = 30
_CERTIFI_CTX: ssl.SSLContext | None = None


def _certifi_context() -> ssl.SSLContext | None:
    """Mozilla's CA bundle. Fallback for machines whose OS store holds an expired
    root (seen on Windows: an expired ISRG Root X2 breaks Let's Encrypt sites)."""
    global _CERTIFI_CTX
    if _CERTIFI_CTX is None:
        try:
            import certifi
        except ImportError:
            return None
        _CERTIFI_CTX = ssl.create_default_context(cafile=certifi.where())
    return _CERTIFI_CTX


def configure(user_agent: str | None = None, timeout: int | None = None) -> None:
    global USER_AGENT, TIMEOUT
    if user_agent:
        USER_AGENT = user_agent
    if timeout:
        TIMEOUT = timeout


def _request(url: str, data: bytes | None = None, headers: dict | None = None,
             method: str | None = None, retries: int = 2) -> bytes:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
    hdrs.update(headers or {})
    last: Exception | None = None
    ctx = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # 4xx (except 429) will not get better by retrying
            if e.code != 429 and 400 <= e.code < 500:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if ctx is None and isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                ctx = _certifi_context()
                if ctx is not None:  # retry right away with the other CA bundle
                    last = e
                    continue
            last = e
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    assert last is not None
    raise last


def get_json(url: str, headers: dict | None = None) -> Any:
    return json.loads(_request(url, headers=headers).decode("utf-8"))


def get_text(url: str, headers: dict | None = None) -> str:
    return _request(url, headers=headers).decode("utf-8", errors="replace")


def post_json(url: str, payload: Any, headers: dict | None = None) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json; charset=utf-8"}
    hdrs.update(headers or {})
    return _request(url, data=body, headers=hdrs, method="POST", retries=1)
