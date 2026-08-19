# -*- coding: utf-8 -*-
"""The one signal shape every collector must produce.

Everything downstream (dedupe, scoring, clustering, reporting) reads only these
fields. Adding a channel therefore never means touching the pipeline -- it means
writing something that emits this dict.
"""

from __future__ import annotations

import datetime as _dt
import email.utils
import hashlib
import re
from typing import Any, Dict, Optional

FIELDS = (
    "source_id",     # which configured source produced it
    "source_label",  # human label, shown in reports
    "tier",          # source tier -> source score (regulator/media/social/...)
    "date",          # YYYY-MM-DD, publication date; "" when genuinely unknown
    "title",
    "body",
    "url",
    "author",        # handle without @, when the channel has one
    "engagement",    # int: views/likes/replies -- whatever the channel gives
    "extra",         # free-form dict kept for traceability
)


def make_signal(
    source_id: str,
    source_label: str,
    tier: str,
    title: str = "",
    body: str = "",
    url: str = "",
    date: str = "",
    author: str = "",
    engagement: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "source_id": source_id,
        "source_label": source_label,
        "tier": tier,
        "date": normalize_date(date),
        "title": (title or "").strip(),
        "body": (body or "").strip(),
        "url": (url or "").strip(),
        "author": (author or "").lstrip("@").strip(),
        "engagement": int(engagement or 0),
        "extra": extra or {},
    }


def text_of(signal: Dict[str, Any]) -> str:
    return ("%s %s" % (signal.get("title", ""), signal.get("body", ""))).strip()


_DATE_PATTERNS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%B %d, %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
)


def normalize_date(value: Any) -> str:
    """Best-effort date normalisation to YYYY-MM-DD.

    Returns "" rather than guessing. A wrong date is worse than a missing one:
    the window filter and the freshness rules both trust this field, so a bad
    parse silently promotes stale items into the current week.
    """
    if not value:
        return ""
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.utcfromtimestamp(float(value)).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return ""
    text = str(value).strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    try:  # RFC 2822, the RSS default
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        pass
    for pattern in _DATE_PATTERNS:
        try:
            return _dt.datetime.strptime(text[:len(pattern) + 10], pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        y, m, d = (int(x) for x in match.groups())
        try:
            return _dt.date(y, m, d).isoformat()
        except ValueError:
            return ""
    return ""


def age_days(signal: Dict[str, Any], today: str) -> int:
    """Age in days; unknown dates are treated as very old, never as fresh."""
    if not signal.get("date"):
        return 9999
    try:
        d0 = _dt.date.fromisoformat(signal["date"])
        d1 = _dt.date.fromisoformat(today)
    except ValueError:
        return 9999
    return (d1 - d0).days


def in_window(signal: Dict[str, Any], today: str, window_days: int) -> bool:
    age = age_days(signal, today)
    return 0 <= age <= window_days if age != 9999 else False


TRACKING_PARAM = re.compile(r"^(utm_[a-z]+|fbclid|gclid|ref|si|s|igshid|mc_cid|mc_eid)=", re.I)


def canonical_url(url: str) -> str:
    """Strip tracking params only.

    Do NOT drop the whole query string: some channels put the record id there
    (app-store review ids are the classic case), and cutting it collapses every
    review of one app into a single item.
    """
    url = (url or "").strip()
    if not url.startswith("http"):
        return ""
    base, _, query = url.partition("?")
    base = base.rstrip("/")
    keep = "&".join(kv for kv in query.split("&") if kv and not TRACKING_PARAM.match(kv))
    return base + ("?" + keep if keep else "")


def fingerprint(signal: Dict[str, Any]) -> str:
    """Identity for dedupe: canonical url, else source+author+first 80 chars."""
    url = canonical_url(signal.get("url", ""))
    if url:
        return "u:" + url
    seed = "%s|%s|%s" % (
        signal.get("source_id", ""),
        signal.get("author", "")[:20],
        re.sub(r"\s+", " ", text_of(signal))[:80].lower(),
    )
    return "h:" + hashlib.md5(seed.encode("utf-8")).hexdigest()


def body_fingerprint(signal: Dict[str, Any]) -> str:
    """Same author saying the same thing twice under two different urls."""
    author = (signal.get("author") or signal.get("source_id") or "").lower()
    body = re.sub(r"\s+", " ", text_of(signal)).strip().lower()[:100]
    return hashlib.md5(("%s|%s" % (author, body)).encode("utf-8")).hexdigest()
