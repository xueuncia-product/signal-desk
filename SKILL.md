---
name: signal-desk
description: Run a configurable multi-source intelligence beat — collect from RSS/news/regulator pages/imports/CLI adapters, dedupe, score against your own keyword tables and weights, cluster into events, and produce ranked briefs per audience with mechanical acceptance checks. Use when the user wants a recurring news/competitor/regulatory digest, asks to "set up a weekly brief", "monitor these sources", "track competitors", "build a news radar", or wants to tune what gets collected, which keywords count, or how items are ranked.
---

# signal-desk

A weekly (or daily) intelligence beat as code. Sources, keyword tables and
scoring weights all live in three config files; the pipeline is fixed and
deterministic, so the same input produces the same ranking twice.

    collect -> dedupe -> classify -> score -> cluster -> rank -> report -> validate

## When to use this

- The user wants a recurring digest of an industry, a competitor set, a
  regulator, a product category.
- The user already has a digest process and wants it reproducible, tunable, or
  handed to someone else.
- The user asks why something ranked where it did, or wants to change what
  counts as important.

Not for: one-off "what happened with X" lookups (just search), or summarising a
single document.

## Quick start

```bash
python3 -m signaldesk doctor  --config examples/minimal
python3 -m signaldesk run     --config examples/minimal --workdir runs/$(date +%F)
```

`run` = collect + score + report + discover + validate. Every command re-runs
safely; the config directory is never written to.

## The four things a user configures

| File | Question it answers |
|---|---|
| `sources.jsonc` | What do we look at, how far back, and what is each channel worth? |
| `keywords.jsonc` | What does "relevant to us" mean, per audience? |
| `scoring.jsonc` | How do trust, reach and relevance trade off, and what must never rank high? |
| `internal/` (optional) | What does the org already talk about? (drives gap detection) |

## Working with a user on this

1. **Sources first.** Recall you never collected cannot be recovered by better
   ranking. Ask what they read today, then find the feed behind each one.
2. **Keyword tables second, in two tiers.** `proper` = terms that carry their
   own attribution and score alone. `gated` = generic terms that only score when
   a brand-gate word is present. Brand-gate words themselves score zero.
3. **Weights last.** Default 20/15/10/55 (source/author/engagement/relevance).
   Change these only after seeing a real ranked list they disagree with.
4. **Run `discover` every cycle** and hand them the candidate list. Never write
   keywords into the config automatically — that silently redefines the beat.
5. **Read `source_status` before believing a quiet week.** A failed source and a
   quiet source look identical in the output; only the status table separates
   them.

## Extending

New channel = anything that emits the signal shape in `signaldesk/model.py`.
Two ways in without touching the pipeline: `json_import` (read a file another
tool wrote) and `command` (run a CLI, map its JSON). See `docs/adapters.md`.

## Reference

- `docs/configuration.md` — every config field
- `docs/adapters.md` — the adapter contract
- `docs/lessons.md` — failure modes this design exists to prevent; read before
  changing the scoring model
