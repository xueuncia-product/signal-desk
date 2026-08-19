# -*- coding: utf-8 -*-
"""Import whatever another tool already produced.

This is the low-tech half of the adapter contract: point at a JSON file (or a
glob of them), describe where each field lives, and the pipeline treats those
rows exactly like natively collected ones. Use it for channels this toolkit
cannot reach on its own -- app-store reviews, logged-in social platforms,
in-house exports, a colleague's spreadsheet dumped to JSON.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List

from . import register, status
from ..model import make_signal


def dig(row: Any, path: str, default: Any = "") -> Any:
    """Read `a.b.0.c` out of nested dicts/lists."""
    if not path:
        return default
    current = row
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return default
        if current is None:
            return default
    return current


def rows_from(payload: Any, root: str = "") -> List[Dict]:
    if root:
        payload = dig(payload, root, [])
    if isinstance(payload, dict):
        for key in ("data", "items", "rows", "results", "messages"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return payload if isinstance(payload, list) else []


def map_row(row: Dict, source: Dict[str, Any]) -> Dict[str, Any]:
    mapping = source.get("map", {})
    engagement = dig(row, mapping.get("engagement", ""), 0)
    try:
        engagement = int(float(engagement))
    except (TypeError, ValueError):
        engagement = 0
    return make_signal(
        source_id=source["id"],
        source_label=source.get("label", source["id"]),
        tier=source.get("tier", "import"),
        title=str(dig(row, mapping.get("title", "title"), "")),
        body=str(dig(row, mapping.get("body", "body"), "")),
        url=str(dig(row, mapping.get("url", "url"), "")),
        date=dig(row, mapping.get("date", "date"), ""),
        author=str(dig(row, mapping.get("author", "author"), "")),
        engagement=engagement,
        extra={"imported_from": source.get("path", "")},
    )


@register("json_import")
def collect(source: Dict[str, Any], ctx: Dict[str, Any]):
    """source keys: path (file or glob, relative to config dir or absolute), root, map"""
    pattern = os.path.expanduser(source.get("path", ""))
    if not os.path.isabs(pattern):
        pattern = os.path.join(ctx.get("config_dir", "."), pattern)
    paths = sorted(glob.glob(pattern))
    if not paths:
        return [], status(
            "error", 0,
            "no file matched %s -- the upstream tool has not written its output yet" % pattern,
        )
    signals, notes = [], []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            notes.append("%s: %s" % (os.path.basename(path), exc))
            continue
        rows = rows_from(payload, source.get("root", ""))
        signals.extend(map_row(row, source) for row in rows if isinstance(row, dict))
        notes.append("%s: %d rows" % (os.path.basename(path), len(rows)))
    state = "ok" if signals else "thin"
    return signals, status(state, len(signals), "; ".join(notes)[:300])
