# -*- coding: utf-8 -*-
"""Targeted page watcher: baseline now, diff next run.

For sources that publish no feed -- regulator directories, licence registries,
programme pages, competitor pricing pages. First run only records a baseline.
Later runs emit a signal when the visible text changed AND the change touches a
configured keyword.

Three states are deliberately distinct:
  baseline -- first sighting, nothing to report yet
  thin     -- page fetched but almost no text (usually a JS-rendered SPA)
  error    -- fetch failed
None of them may be rendered as "no change this week".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict

from . import http_get, register, status
from ..model import make_signal

SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
THIN_CHARS = 400


def visible_text(html: str) -> str:
    html = SCRIPT_RE.sub(" ", html)
    text = TAG_RE.sub(" ", html)
    text = re.sub(r"&[a-z#0-9]{2,8};", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _state_path(ctx: Dict[str, Any], source_id: str) -> str:
    state_dir = os.path.join(ctx["workdir"], "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "page_%s.json" % re.sub(r"\W+", "_", source_id))


@register("page_watch")
def collect(source: Dict[str, Any], ctx: Dict[str, Any]):
    """source keys: url, tier, label, keywords[list]"""
    url = source.get("url", "")
    try:
        raw = http_get(url, accept="text/html,application/xhtml+xml,*/*")
        text = visible_text(raw.decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return [], status("error", 0, "%s: %s" % (type(exc).__name__, str(exc)[:160]))

    if len(text) < THIN_CHARS:
        return [], status(
            "thin", 0,
            "only %d chars of visible text -- likely JS-rendered, needs a browser adapter" % len(text),
        )

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = _state_path(ctx, source["id"])
    previous = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                previous = json.load(fh)
        except (OSError, json.JSONDecodeError):
            previous = {}

    snapshot = {"hash": digest, "length": len(text), "seen_at": ctx.get("today", ""), "url": url}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)

    if not previous:
        return [], status("baseline", 0, "baseline recorded (%d chars)" % len(text))
    if previous.get("hash") == digest:
        return [], status("ok", 0, "no change since %s" % previous.get("seen_at", "?"))

    keywords = [k.lower() for k in source.get("keywords", [])]
    lowered = text.lower()
    hits = [k for k in keywords if k in lowered]
    if keywords and not hits:
        return [], status("ok", 0, "changed but no watched keyword present")

    delta = len(text) - int(previous.get("length", 0))
    excerpt = _excerpt(text, hits)
    signal = make_signal(
        source_id=source["id"],
        source_label=source.get("label", source["id"]),
        tier=source.get("tier", "regulator"),
        title="[page changed] %s" % source.get("label", source["id"]),
        body=excerpt,
        url=url,
        date=ctx.get("today", ""),
        extra={"matched": hits, "length_delta": delta, "previous_seen": previous.get("seen_at", "")},
    )
    return [signal], status("ok", 1, "changed (%+d chars), keywords: %s" % (delta, ", ".join(hits) or "n/a"))


def _excerpt(text: str, hits, width: int = 220) -> str:
    if not hits:
        return text[:width]
    parts = []
    lowered = text.lower()
    for hit in hits[:3]:
        index = lowered.find(hit)
        if index >= 0:
            parts.append(text[max(0, index - width // 2): index + width // 2])
    return " … ".join(parts) or text[:width]
