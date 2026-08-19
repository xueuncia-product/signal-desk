# Adapters

This toolkit collects from feeds, pages and files on its own. It deliberately
ships no logged-in social scraping, no app-store scraping and no headless
browser: those need credentials, change often, and their terms of use are yours
to accept, not this repo's.

Instead there are two ways to bring any channel in without touching the
pipeline.

## 1. `json_import` — read what another tool wrote

The lowest-friction path. Point at a file or glob, describe where the fields
live:

```jsonc
{
  "id": "reviews",
  "type": "json_import",
  "label": "App store reviews",
  "tier": "appstore",
  "path": "inbox/reviews_*.json",
  "root": "",                       // optional path to the array inside the doc
  "map": {
    "title": "app",
    "body": "content",
    "url": "link",
    "date": "at",
    "author": "userName",
    "engagement": "thumbsUp"
  }
}
```

Envelopes are unwrapped automatically: a top-level `data`, `items`, `rows`,
`results` or `messages` array is found without configuration.

## 2. `command` — run a CLI and map its JSON

For anything with a command-line client. The command must print JSON to stdout.

```jsonc
{
  "id": "social",
  "type": "command",
  "tier": "social",
  "command": ["your-cli", "search", "{query}", "--limit", "{limit}", "--json"],
  "map": {"body": "text", "url": "url", "date": "created_at",
          "author": "author.username", "engagement": "public_metrics.view_count"},
  "cooldown_seconds": 8,
  "retries": 2,
  "max_priority": 2,
  "queries": [
    {"id": "rivals_pricing", "priority": 1, "perspective": "commercial", "limit": 30,
     "query": "BrandB (pricing OR fees OR cashback)"}
  ]
}
```

Placeholders in `command` are filled from each query object, so any field you
add to a query (`{query}`, `{limit}`, `{lang}`, …) is available.

### What you get for free

- **Per-query caching.** A successful query is cached by id; re-running the week
  does not re-spend quota. `--force` ignores the cache.
- **Rate-limit discipline.** The first rate-limited answer stops that source.
  Remaining queries are marked `deferred` — not `failed` — so the next run
  resumes them instead of treating them as "nothing found".
- **No destructive writes.** An empty result never overwrites a non-empty cached
  one.
- **Priorities.** `max_priority` runs only the important queries on a short run.
- **A manifest.** `cache/<source>_manifest.json` records every query's state.

### Splitting queries by audience

If you produce more than one report, write queries per audience and set
`perspective`. Two audiences fed by one undifferentiated query set will produce
two nearly identical reports — and `validate` will fail the overlap check to
tell you so.

## 3. Writing a native collector

Only worth it for something you will reuse. Drop a module in
`signaldesk/collectors/`:

```python
from . import register, status
from ..model import make_signal

@register("my_channel")
def collect(source, ctx):
    """Return (signals, status)."""
    try:
        rows = fetch(source["url"])
    except Exception as exc:
        return [], status("error", 0, str(exc)[:160])
    signals = [make_signal(
        source_id=source["id"],
        source_label=source.get("label", source["id"]),
        tier=source.get("tier", "default"),
        title=row["title"], body=row["text"], url=row["url"],
        date=row["published"], author=row.get("handle", ""),
        engagement=row.get("views", 0),
    ) for row in rows]
    return signals, status("ok", len(signals), "fetched %d" % len(rows))
```

Import it from `signaldesk/collectors/__init__.py` and it becomes available as a
`type` in `sources.jsonc`.

`ctx` carries `workdir`, `config_dir`, `today`, `offline`, `force`. Persist
anything you need between runs under `ctx["workdir"]/state/`.

### Status states, and why they matter

| State | Meaning |
|---|---|
| `ok` | Fetched and parsed |
| `thin` | Fetched, but almost no content — usually a JS-rendered page |
| `error` | Fetch or parse failed |
| `baseline` | First sighting of a watched page; nothing to report yet |
| `rate_limited` | Stopped early, resume next run |
| `skipped_offline` | Network source skipped by `--offline` |

Return the honest one. A source that failed and a source with nothing to say
look identical in the output otherwise, and that is how an incomplete week gets
reported as a quiet one.
