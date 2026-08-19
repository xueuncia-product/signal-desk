# -*- coding: utf-8 -*-
"""Run someone else's CLI and treat its JSON as a channel.

This is the pluggable half of the adapter contract. Anything that can print a
JSON array to stdout can become a source: a social-platform CLI, a scraping
tool, a curl one-liner, an internal exporter.

Three behaviours here are load-bearing, all of them learned the hard way on a
rate-limited social API:

1. Successful query output is cached per query id. Re-running the week does not
   re-spend quota, and a later failure can never overwrite an earlier success.
2. The first rate-limit answer stops the whole source. Remaining queries are
   marked `deferred`, not `failed` -- so the next run knows to resume them
   rather than treat them as "nothing found".
3. An empty result is never written over a non-empty cached one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List

from . import register, status
from .json_import import map_row, rows_from

RATE_LIMIT_MARKERS = ("429", "rate limit", "rate-limited", "too many requests", "quota")


def _cache_path(ctx: Dict[str, Any], source_id: str, query_id: str) -> str:
    cache_dir = os.path.join(ctx["workdir"], "cache", re.sub(r"\W+", "_", source_id))
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "%s.json" % re.sub(r"\W+", "_", query_id))


def _valid_cache(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) < 3:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            return isinstance(json.load(fh), (list, dict))
    except (OSError, json.JSONDecodeError):
        return False


def _render(template: List[str], values: Dict[str, Any]) -> List[str]:
    out = []
    for part in template:
        for key, value in values.items():
            part = part.replace("{%s}" % key, str(value))
        out.append(part)
    return out


@register("command")
def collect(source: Dict[str, Any], ctx: Dict[str, Any]):
    """source keys:
        command      list[str], supports {query} {limit} and any query field
        queries      list of {id, query, limit, priority, perspective}
        map          field mapping, same syntax as json_import
        root         optional path to the array inside the response
        cooldown_seconds, retries, timeout, max_priority
    """
    template = source.get("command") or []
    if not template:
        return [], status("error", 0, "command source has no `command` template")
    queries = source.get("queries") or [{"id": source["id"], "query": source.get("query", "")}]
    max_priority = int(source.get("max_priority", 99))
    cooldown = float(source.get("cooldown_seconds", 8))
    retries = int(source.get("retries", 2))
    timeout = int(source.get("timeout", 180))
    force = bool(ctx.get("force"))
    offline = bool(ctx.get("offline"))

    signals: List[Dict[str, Any]] = []
    manifest, halted = [], False
    for index, query in enumerate(queries):
        if int(query.get("priority", 1)) > max_priority:
            continue
        query_id = str(query.get("id") or "q%d" % index)
        cache = _cache_path(ctx, source["id"], query_id)
        record = {"id": query_id, "query": query.get("query", ""), "state": "", "count": 0}

        if halted:
            record["state"] = "deferred"
            manifest.append(record)
            continue
        if _valid_cache(cache) and not force:
            payload = json.load(open(cache, encoding="utf-8"))
            rows = rows_from(payload, source.get("root", ""))
            signals.extend(_rows_to_signals(rows, source, query))
            record.update({"state": "cached", "count": len(rows)})
            manifest.append(record)
            continue
        if offline:
            record["state"] = "skipped_offline"
            manifest.append(record)
            continue

        if index:
            time.sleep(max(cooldown, 0))
        payload, error, rate_limited = _run(template, query, retries, cooldown, timeout)
        if payload is not None:
            rows = rows_from(payload, source.get("root", ""))
            if rows or not _valid_cache(cache):
                tmp = cache + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, cache)
            signals.extend(_rows_to_signals(rows, source, query))
            record.update({"state": "ok", "count": len(rows)})
        elif rate_limited:
            halted = True
            record.update({"state": "rate_limited", "note": error[:200]})
        else:
            record.update({"state": "error", "note": error[:200]})
        manifest.append(record)

    manifest_path = os.path.join(ctx["workdir"], "cache", "%s_manifest.json" % re.sub(r"\W+", "_", source["id"]))
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    counts = {}
    for record in manifest:
        counts[record["state"]] = counts.get(record["state"], 0) + 1
    note = ", ".join("%s=%d" % kv for kv in sorted(counts.items()))
    if halted:
        return signals, status("rate_limited", len(signals), note + " (stopped early, resume next run)")
    if counts.get("error") and not signals:
        return signals, status("error", 0, note)
    return signals, status("ok" if signals else "thin", len(signals), note)


def _rows_to_signals(rows, source, query):
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        signal = map_row(row, source)
        signal["extra"].update({"query_id": query.get("id", ""), "query": query.get("query", "")})
        if query.get("perspective"):
            signal["extra"]["perspective_hint"] = query["perspective"]
        out.append(signal)
    return out


def _run(template, query, retries, cooldown, timeout):
    """Returns (payload|None, error, rate_limited)."""
    values = dict(query)
    values.setdefault("limit", query.get("limit", 30))
    argv = _render(template, values)
    last_error = ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, "%s: %s" % (type(exc).__name__, exc), False
        last_error = (proc.stderr or "").strip()
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout or "[]"), "", False
            except json.JSONDecodeError:
                last_error = "stdout was not JSON: %s" % (proc.stdout or "")[:160]
        blob = (last_error + " " + (proc.stdout or "")[:200]).lower()
        if any(marker in blob for marker in RATE_LIMIT_MARKERS):
            return None, last_error or "rate limited", True
        if attempt < retries:
            time.sleep(max(cooldown, 4) * (2 ** attempt))
    return None, last_error or "command failed", False
