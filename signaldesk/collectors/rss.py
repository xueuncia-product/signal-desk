# -*- coding: utf-8 -*-
"""RSS / Atom collector -- zero dependency, stdlib XML only.

Also covers Google News, Reddit, YouTube channels and most blogs, since all of
them expose RSS. Prefer this over scraping whenever a feed exists.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from . import http_get, register, status
from ..model import make_signal, normalize_date

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}

TAG_RE = re.compile(r"<[^>]+>")


def _text(node, *paths) -> str:
    for path in paths:
        found = node.find(path, NS)
        if found is not None:
            if found.text:
                return found.text.strip()
            href = found.get("href")
            if href:
                return href.strip()
    return ""


def strip_html(value: str, limit: int = 600) -> str:
    value = TAG_RE.sub(" ", value or "")
    value = value.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#8217;", "'")
    return re.sub(r"\s+", " ", value).strip()[:limit]


def parse_feed(raw: bytes) -> List[Dict[str, str]]:
    """Parse RSS 2.0 and Atom into plain dicts."""
    text = raw.decode("utf-8", errors="replace")
    start = min([i for i in (text.find("<?xml"), text.find("<rss"), text.find("<feed")) if i >= 0] or [0])
    root = ET.fromstring(text[start:])
    items = root.findall(".//item") or root.findall(".//atom:entry", NS)
    out = []
    for item in items:
        out.append({
            "title": strip_html(_text(item, "title", "atom:title"), 300),
            "link": _text(item, "link", "atom:link[@rel='alternate']", "atom:link"),
            "date": _text(item, "pubDate", "atom:published", "atom:updated", "dc:date"),
            "summary": strip_html(_text(item, "description", "atom:summary", "atom:content")),
            "author": strip_html(_text(item, "author", "dc:creator", "atom:author/atom:name"), 80),
        })
    return out


@register("rss")
def collect(source: Dict[str, Any], ctx: Dict[str, Any]):
    """source keys: url, tier, label, title_filter(bool), keywords[list]"""
    url = source.get("url", "")
    try:
        raw = http_get(url, accept="application/rss+xml, application/xml, text/xml, */*")
    except Exception as exc:  # noqa: BLE001 -- any failure is a source-health fact
        return [], status("error", 0, "fetch failed: %s: %s" % (type(exc).__name__, str(exc)[:140]))
    try:
        entries = parse_feed(raw)
    except ET.ParseError as exc:
        # HTTP 200 does not mean you got a feed. Sites answer soft-404 HTML pages
        # at retired feed URLs, and a raw XML parse error tells the operator
        # nothing about what to do next.
        head = raw[:400].decode("utf-8", errors="replace").lstrip().lower()
        if head.startswith("<!doctype html") or "<html" in head[:200]:
            return [], status("error", 0, "URL returned an HTML page, not a feed "
                                          "(retired or wrong feed URL?): %s" % str(exc)[:80])
        return [], status("error", 0, "feed did not parse as RSS/Atom: %s" % str(exc)[:120])

    keywords = [k.lower() for k in source.get("keywords", [])]
    title_only = bool(source.get("title_filter"))
    signals, dropped = [], 0
    for entry in entries:
        title = entry["title"]
        summary = entry["summary"]
        if title_only and keywords:
            # Match the TITLE only. Matching title+summary with broad words makes
            # the filter a no-op on general-news outlets: local currency and
            # payment words appear in nearly every article body.
            blob = title.lower()
            hits = [k for k in keywords if k in blob]
            if not hits:
                dropped += 1
                continue
        elif keywords:
            blob = ("%s %s" % (title, summary)).lower()
            hits = [k for k in keywords if k in blob]
            if not hits:
                dropped += 1
                continue
        else:
            hits = []
        signals.append(make_signal(
            source_id=source["id"],
            source_label=source.get("label", source["id"]),
            tier=source.get("tier", "media"),
            title=title,
            body=summary,
            url=entry["link"],
            date=normalize_date(entry["date"]),
            author=entry["author"],
            extra={"matched": hits},
        ))
    note = "fetched %d, kept %d" % (len(entries), len(signals))
    if dropped:
        note += ", keyword-filtered %d" % dropped
    if not entries:
        return signals, status("thin", 0, "feed parsed but contained no items")
    return signals, status("ok", len(signals), note)
