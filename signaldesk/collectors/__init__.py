# -*- coding: utf-8 -*-
"""Collector registry + shared HTTP helpers.

A collector takes one source config block and returns
`(signals, status)` where status is a dict with at least
`{"state": "ok|thin|error|baseline|cached|deferred", "count": int, "note": str}`.

Status is not cosmetic. A source that failed must never be reported as
"nothing happened this week" -- those two look identical in the output and only
one of them means the beat is healthy.
"""

from __future__ import annotations

import gzip
import io
import ssl
import urllib.error
import urllib.request
import zlib
from typing import Any, Callable, Dict, List, Tuple

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

Result = Tuple[List[Dict[str, Any]], Dict[str, Any]]
_REGISTRY: Dict[str, Callable[..., Result]] = {}


def register(kind: str):
    def wrap(fn):
        _REGISTRY[kind] = fn
        return fn
    return wrap


def get(kind: str):
    return _REGISTRY.get(kind)


def kinds() -> List[str]:
    return sorted(_REGISTRY)


def status(state: str, count: int = 0, note: str = "") -> Dict[str, Any]:
    return {"state": state, "count": count, "note": note}


def _degzip(raw: bytes) -> bytes:
    """Tolerant gunzip.

    Some feeds answer with a couple of stray bytes before the gzip magic and a
    truncated end-of-stream marker. `gzip.decompress` raises EOFError on those
    and the feed silently parses as zero entries; decompressobj from the magic
    onwards recovers the whole body.
    """
    if not raw[:2] == b"\x1f\x8b":
        index = raw.find(b"\x1f\x8b")
        if index < 0 or index > 8:
            return raw
        raw = raw[index:]
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw) or raw
    except zlib.error:
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except OSError:
            return raw


def http_get(url: str, timeout: int = 45, accept: str = "*/*") -> bytes:
    """Plain urllib GET with a browser-shaped header set.

    No retry-on-403 magic here: when a site blocks this, that is a real finding
    about the source, and the honest move is to record the failure and let the
    operator wire a browser-backed adapter for that one source.
    """
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return _degzip(response.read())


from . import rss, page_watch, json_import, command  # noqa: E402,F401  (register side effects)
