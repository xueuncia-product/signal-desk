# Configuration

Three files per beat, all JSONC (JSON plus `//` comments and trailing commas).
Copy `examples/minimal/` and edit. The config directory is read-only to the
tool; everything it produces goes to `--workdir`.

```
my-beat/
├── sources.jsonc     what we look at
├── keywords.jsonc    what "relevant" means, per audience
├── scoring.jsonc     how trust, reach and relevance trade off
├── inbox/            (optional) JSON another tool produced
└── internal/         (optional) your own docs, for gap detection
```

## sources.jsonc

| Field | Meaning |
|---|---|
| `window_days` | Items older than this are dropped at collection, not at ranking |
| `sources[]` | One block per channel |

Common source fields:

| Field | Applies to | Meaning |
|---|---|---|
| `id` | all | Unique; used for cache and state filenames |
| `type` | all | `rss` · `page_watch` · `json_import` · `command` |
| `label` | all | Shown in reports |
| `tier` | all | Looked up in `scoring.source_tiers` |
| `enabled` | all | `false` keeps the block documented but inactive |
| `keywords` | rss, page_watch | Pre-filter; on `page_watch` it decides whether a change is worth reporting |
| `title_filter` | rss | Match `keywords` against the headline only — required for general-interest outlets |
| `url` | rss, page_watch | Feed or page URL |
| `path`, `root`, `map` | json_import | File/glob, optional path to the array, field mapping |
| `command`, `queries`, `map` | command | See [adapters.md](adapters.md) |

`map` values are dotted paths into each row: `author.username`,
`public_metrics.view_count`, `a.b.0.c`. Mappable targets: `title`, `body`,
`url`, `date`, `author`, `engagement`.

**Tiers are yours to name.** The only constraint is that a tier used here has a
score in `scoring.source_tiers`.

## keywords.jsonc

```jsonc
{
  "brand_gate": ["acme", "acme pay"],        // gate only — scores nothing
  "perspectives": {
    "product": {
      "label": "Product view",
      "proper":  {"acme ledger": 5},          // scores on its own
      "gated":   {"refund": 3},               // needs a brand_gate word present
      "penalty": {"job opening": 5}           // subtracts
    }
  },
  "discovery": {
    "domain_words": ["settlement", "licence"],
    "noise": ["giveaway"],
    "min_freq": 3,
    "min_sources": 2,
    "fresh_days": 10,
    "internal_corpus_dir": "internal"
  }
}
```

One perspective is fine. Two or more give each audience its own ranked report
out of one collection — and `validate` will tell you when they have stopped
being different enough to be worth separating.

Values are weights, any positive number; they are normalised later, so what
matters is their ratio to each other.

## scoring.jsonc

| Key | Meaning |
|---|---|
| `weights` | `source` + `author` + `engagement` + `relevance`, **must total 100** |
| `source_tiers` | tier → points, capped at `weights.source` |
| `relevance_normalizer_percentile` | Which percentile of observed keyword scores earns full relevance marks (default 95) |
| `content_types[]` | `{name, multiplier, pattern, tiers?}` — damp whole categories; blocks with `tiers` win over blocks without, regardless of order |
| `default_content_type` | Applied when nothing matched |
| `identities` | handle → `{weight, label}`; verified publishers only |
| `tiers` | score thresholds for `core` / `important` / `watch`; below `watch` is `ledger` |
| `assign_gap` | Relative distance within which an item belongs to several perspectives |
| `resurface_gap_days` | Silence after which a returning story counts as `resurfaced` rather than `continuing` |
| `clustering` | `title_similarity`, `multi_source_bonus`, `multi_source_bonus_cap` |
| `report_limits` | How many items per tier reach the markdown (the rest stay in `events.json`, and the count is printed) |
| `hard_rules` | Acceptance checks — see below |

### hard_rules

| Rule | Fails when |
|---|---|
| `max_age_days_for_core` | Something older is ranked core/important |
| `exclude_from_core_patterns` | A matching item is ranked core/important |
| `max_top_overlap` / `overlap_window` | Two perspectives' assigned top lists overlap too much |
| `required_source_tiers` | A tier returned nothing at all |
| `max_failed_sources` | More sources errored than tolerated |

`validate` exits non-zero on failure, so a scheduled run can gate on it.

## Outputs

| File | What it is |
|---|---|
| `signals_raw.json` | Everything collected, in-window, before dedupe |
| `scored.json` | Per-item scores with the breakdown and keyword hits |
| `events.json` | Clustered events with per-perspective scores and tiers |
| `report_<perspective>.md` | The brief for one audience |
| `digest.md` | Cross-perspective summary + source health |
| `keyword_candidates.md/.json` | New-term candidates and coverage gaps |
| `source_status.json` | Per-source state, count, note |
| `validation.json` | Every check, passed or failed |
| `../history.json` | Cross-run continuity (kept beside the run folders) |
| `state/`, `cache/` | Page baselines and per-query caches |

## Scheduling

Nothing about this is daemonised; use cron or any runner:

```cron
0 7 * * 1  cd /path/to/my-beat && python3 -m signaldesk run \
             --config . --workdir runs/$(date +\%F) >> runs/cron.log 2>&1
```

`validate`'s exit code makes it safe to chain a delivery step behind it.
