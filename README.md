# signal-desk

A configurable, reproducible intelligence beat: collect from many sources, score
against **your** keyword tables and **your** weights, and get a ranked brief per
audience — with mechanical checks that catch the mistakes people keep making by
hand.

Zero dependencies. Python 3.8+. Nothing to install.

[中文说明](README.zh-CN.md) · [Configuration](docs/configuration.md) ·
[Adapters](docs/adapters.md) · [Lessons](docs/lessons.md)

```
collect → dedupe → classify → score → cluster → rank → report → validate
```

## Try it

```bash
git clone https://github.com/xueuncia-product/signal-desk && cd signal-desk

python3 -m signaldesk doctor --config examples/minimal          # check the config
python3 -m signaldesk run    --config examples/minimal --workdir runs/demo
```

That collects two live feeds plus a bundled sample file, ranks them, writes a
brief and runs the acceptance checks. Add `--offline` to run with no network at
all.

Then copy `examples/minimal/` (or `examples/full/`, which shows every field),
point it at your own sources, and you have your own beat.

## What it is for

You already follow an industry, a competitor set, a regulator. The work is not
reading — it is that the same failures repeat every week: a source quietly
breaks and its silence reads as calm; the same story arrives five times and
looks like five stories; a keyword list nobody has audited matches nothing; two
teams get near-identical "different" reports.

signal-desk makes each of those a config decision and a mechanical check.

## The model

**Sources** are typed blocks in a config file. Four built-in kinds, none needing
an API key:

| Kind | For |
|---|---|
| `rss` | Feeds — news, blogs, Google News searches, forums, YouTube channels |
| `page_watch` | Pages with no feed: licence registers, tariff pages, programme pages. Baselines first, then reports keyword-relevant changes |
| `json_import` | Whatever another tool already produced |
| `command` | Any CLI that prints JSON — with caching, retry and rate-limit discipline built in |

The last two are the extension points: no logged-in scraping ships here, because
credentials and terms of use are yours, not this repo's. See
[docs/adapters.md](docs/adapters.md).

**Keywords** are two-tier, per perspective:

```jsonc
"brand_gate": ["acme"],                  // gate only — scores nothing
"proper":     {"acme ledger": 5},        // self-attributing, scores alone
"gated":      {"refund": 3}              // generic, needs a brand word present
```

`refund` on its own could be any company on earth. Brand words score zero
because they appear in most of what you collect — they are a gate, not a signal.

**Scoring** is arithmetic and table-driven, so a ranking is explainable by
pointing at a config line:

```
total = source (20) + author (15) + engagement (10) + relevance (55) = 100
```

Relevance dominates deliberately: source and author say how much to *trust* an
item, only relevance says whether it is about your beat.

**Events, not items.** One announcement reprinted by four outlets is one event
with four pieces of evidence. Corroboration across different source tiers earns
a bounded bonus; repetition inside one tier earns nothing.

**Perspectives.** One collection, a ranked report per audience. Assignment is by
relevance alone — an item matching nobody's keywords belongs to nobody, and
stays in the ledger rather than being pushed at everyone.

## Keyword discovery

`run` also produces a candidate list — terms rising in this week's corpus that
are not in your config yet, ranked by frequency x source span x freshness. It
never edits your config: the keyword table is the definition of the beat, and
auto-appending changes that definition silently.

Drop your own documents in `internal/` and you also get a **gap scan**: terms
your organisation talks about that your sources are not returning. Those are
usually the most valuable output of the whole run, and they need their own pass
— an internal-only term appears in one corpus, so any "must span N sources" rule
would filter it out.

## Acceptance checks

`validate` exits non-zero when any of these fail, so a scheduled run can gate on
it:

- nothing stale or undated ranked core/important
- excluded content (recruitment, promo, …) stays out of the top tiers
- perspectives' assigned top lists are actually different
- every required source tier returned data
- source failures within tolerance

## Commands

```bash
python3 -m signaldesk doctor    --config DIR              # validate config, check adapters
python3 -m signaldesk collect   --config DIR --workdir W  # fetch, window-filter
python3 -m signaldesk score     --config DIR --workdir W  # dedupe, score, cluster
python3 -m signaldesk report    --config DIR --workdir W  # markdown briefs
python3 -m signaldesk discover  --config DIR --workdir W  # keyword candidates + gaps
python3 -m signaldesk validate  --config DIR --workdir W  # acceptance checks
python3 -m signaldesk run       --config DIR --workdir W  # all of the above
```

Flags: `--today YYYY-MM-DD` (run date), `--offline` (skip network sources),
`--force` (ignore adapter caches).

Self-test: `python3 tests/selftest.py`

## Design notes

The design decisions here came from running a beat like this weekly and getting
it wrong in specific, repeatable ways: filters that silently matched everything,
caches overwritten by rate-limited failures, keyword lists where two-thirds of
the terms never fired, two "different" reports that were the same report.
[docs/lessons.md](docs/lessons.md) is the list. Read it before changing the
scoring model — most of it is not obvious until it has cost you a week.

## License

MIT — see [LICENSE](LICENSE).
