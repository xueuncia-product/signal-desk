# -*- coding: utf-8 -*-
"""Dedupe -> classify -> keyword relevance -> weighted score -> cluster -> rank.

The scoring model is deliberately arithmetic and table-driven, not a model call:
the same input must produce the same ranking next week, and every number in a
report must be explainable by pointing at a config line.

    total = source (S) + author (A) + engagement (E) + relevance (R)   = 100 by default

Why relevance carries the most weight: source and author only calibrate how much
to trust an item, not how much it matters to you. An official announcement from
a top-tier source about something outside your beat is still noise.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .config import Config
from .model import age_days, body_fingerprint, fingerprint, text_of


# --------------------------------------------------------------------------
# dedupe
# --------------------------------------------------------------------------
def dedupe(signals: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """Two passes, because they catch different things.

    Pass 1 (identity): the same url arriving from several queries or feeds.
    Pass 2 (body): the same author posting the same text twice under different
    ids -- invisible to url dedupe, and it is what makes one voice look like a
    trend.
    """
    seen, first_pass, dropped_url = set(), [], 0
    for signal in signals:
        key = fingerprint(signal)
        if key in seen:
            dropped_url += 1
            continue
        seen.add(key)
        first_pass.append(signal)

    seen_body, out, dropped_body = set(), [], 0
    for signal in first_pass:
        author = (signal.get("author") or "").strip().lower()
        key = body_fingerprint(signal)
        if author and key in seen_body:
            dropped_body += 1
            continue
        seen_body.add(key)
        out.append(signal)
    return out, {"duplicate_url": dropped_url, "duplicate_body": dropped_body, "kept": len(out)}


# --------------------------------------------------------------------------
# content type
# --------------------------------------------------------------------------
def classify(signal: Dict, cfg: Config) -> Tuple[str, float]:
    """Content type + multiplier.

    Without this, one loud category floods the top of the list: support replies,
    complaint boilerplate and promo posts are individually low-value but
    collectively numerous, and you only ever need two or three of them as
    representatives.
    """
    rules = cfg.scoring.get("content_types", [])
    # Tier-scoped rules win over general ones regardless of file order. Without
    # this, a regulator notice containing the words "we are sorry" gets damped
    # to a fifth of its score by a support-reply rule written above it -- and
    # the fix would be an ordering constraint nobody can see in the config.
    scoped = [r for r in rules if r.get("tiers")]
    general = [r for r in rules if not r.get("tiers")]
    for rule in scoped + general:
        applies_to = rule.get("tiers")
        if applies_to and signal.get("tier") not in applies_to:
            continue
        pattern = rule.get("pattern")
        if pattern and re.search(pattern, text_of(signal), re.I):
            return rule.get("name", "unnamed"), float(rule.get("multiplier", 1.0))
    default = cfg.scoring.get("default_content_type", {"name": "other", "multiplier": 1.0})
    return default.get("name", "other"), float(default.get("multiplier", 1.0))


# --------------------------------------------------------------------------
# keyword relevance
# --------------------------------------------------------------------------
def has_brand(text: str, cfg: Config) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in cfg.brand_gate)


def relevance(text: str, perspective: Dict, cfg: Config) -> Tuple[float, List[str]]:
    """Two-tier keyword scoring.

    proper -- terms that carry their own attribution (product names, programme
      names, tickers). Seeing one is already evidence the item is about your
      beat, so it scores on its own.
    gated  -- generic terms (api, refund, licence, commission). They only score
      when a brand-gate word is also present, otherwise 'refund' matches every
      company on earth.

    Brand-gate words themselves score nothing: they appear in a large share of
    the corpus, so they have no discriminating power -- they are a gate, not a
    signal.
    """
    lowered = (text or "").lower()
    points, hits = 0.0, []
    for term, value in (perspective.get("proper") or {}).items():
        if term.lower() in lowered:
            points += float(value)
            hits.append("%s+%s" % (term, value))
    if has_brand(lowered, cfg):
        for term, value in (perspective.get("gated") or {}).items():
            if term.lower() in lowered:
                points += float(value)
                hits.append("%s+%s" % (term, value))
    elif perspective.get("gated"):
        hits.append("(no brand-gate word; generic terms not counted)")
    for term, value in (perspective.get("penalty") or {}).items():
        if term.lower() in lowered:
            points -= float(value)
            hits.append("%s-%s" % (term, value))
    return max(points, 0.0), hits


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    index = min(int(len(ordered) * pct / 100.0), len(ordered) - 1)
    return ordered[index] or 1.0


def score_all(signals: List[Dict], cfg: Config, today: str) -> List[Dict]:
    weights = cfg.weights
    identities = {k.lower(): v for k, v in cfg.scoring.get("identities", {}).items()}
    perspectives = cfg.perspectives

    raw: List[Dict[str, float]] = []
    for signal in signals:
        text = text_of(signal)
        raw.append({name: relevance(text, per, cfg)[0] for name, per in perspectives.items()})

    flat = [value for row in raw for value in row.values()]
    anchor = _percentile(flat, float(cfg.scoring.get("relevance_normalizer_percentile", 95)))
    scale = weights["relevance"] / anchor

    # engagement is only comparable within a channel family, so percentile-rank
    # inside the tier instead of comparing raw counts across channels.
    by_tier: Dict[str, List[int]] = {}
    for signal in signals:
        by_tier.setdefault(signal.get("tier", "default"), []).append(int(signal.get("engagement", 0)))
    for tier in by_tier:
        by_tier[tier].sort()

    out = []
    for signal, relevance_row in zip(signals, raw):
        text = text_of(signal)
        source_score = min(cfg.source_tier_score(signal.get("tier", "default")), weights["source"])

        identity = identities.get((signal.get("author") or "").lower())
        author_score = min(float(identity.get("weight", 0)), weights["author"]) if identity else 0.0

        peers = by_tier.get(signal.get("tier", "default"), [])
        value = int(signal.get("engagement", 0))
        rank = (sum(1 for x in peers if x < value) / len(peers)) if peers else 0.0
        engagement_score = round(rank * weights["engagement"], 1)

        type_name, multiplier = classify(signal, cfg)
        record = dict(signal)
        record.update({
            "age_days": age_days(signal, today),
            "content_type": type_name,
            "type_multiplier": multiplier,
            "has_brand": has_brand(text, cfg),
            "author_identity": identity.get("label", "") if identity else "",
            "score_source": source_score,
            "score_author": author_score,
            "score_engagement": engagement_score,
            "scores": {},
            "hits": {},
        })
        for name, per in perspectives.items():
            points, hits = relevance(text, per, cfg)
            relevance_score = round(min(points * scale, weights["relevance"]) * multiplier, 1)
            record["scores"][name] = round(source_score + author_score + engagement_score + relevance_score, 1)
            record["hits"][name] = hits[:6]
            record.setdefault("score_relevance", {})[name] = relevance_score
        record["assigned"] = assign(record["score_relevance"], float(cfg.scoring.get("assign_gap", 0.15)))
        out.append(record)
    return out


def assign(relevance_scores: Dict[str, float], gap: float) -> List[str]:
    """Which perspective(s) an item belongs to.

    Deliberately decided on the *relevance* component alone, not on the total.
    Source, author and engagement say how much to trust an item, not who should
    read it: rank an item by total and a high-authority piece about nothing in
    your keyword tables lands in every perspective at once, which is how two
    audiences end up with the same report.

    Zero relevance means no perspective owns it. It stays in the ledger, still
    collected and still searchable, just not addressed to anyone.
    """
    if not relevance_scores:
        return []
    best = max(relevance_scores.values())
    if best <= 0:
        return []
    return [name for name, value in relevance_scores.items() if (best - value) / best <= gap]


def tier_of(score: float, cfg: Config) -> str:
    thresholds = cfg.scoring.get("tiers", {"core": 70, "important": 55, "watch": 40})
    for name in ("core", "important", "watch"):
        if score >= float(thresholds.get(name, 0)):
            return name
    return "ledger"


# --------------------------------------------------------------------------
# event clustering
# --------------------------------------------------------------------------
STOP = set("""a an the and or of to in on for with at by from is are was were be as new that this it
its into over under after before how why what when who will can may""".split())


def _shingle(text: str) -> set:
    words = [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in STOP and len(w) > 2]
    return set(words)


def cluster(scored: List[Dict], cfg: Config) -> List[Dict]:
    """Group multi-source coverage of one event.

    A press release, three outlets reprinting it and a forum thread about it are
    one event with four pieces of evidence, not four events. Corroboration
    across *different* source tiers adds a bounded bonus; reprints inside the
    same tier add nothing.
    """
    settings = cfg.scoring.get("clustering", {})
    threshold = float(settings.get("title_similarity", 0.6))
    bonus_per_tier = float(settings.get("multi_source_bonus", 2.0))
    bonus_cap = float(settings.get("multi_source_bonus_cap", 6.0))

    clusters: List[Dict] = []
    for signal in sorted(scored, key=lambda s: -max(s["scores"].values() or [0])):
        words = _shingle(signal.get("title") or signal.get("body", "")[:120])
        placed = False
        for group in clusters:
            if not words or not group["words"]:
                continue
            overlap = len(words & group["words"]) / max(len(words | group["words"]), 1)
            if overlap >= threshold:
                group["evidence"].append(signal)
                group["words"] |= words
                placed = True
                break
        if not placed:
            clusters.append({"words": words, "evidence": [signal]})

    events = []
    for index, group in enumerate(clusters):
        evidence = group["evidence"]
        lead = evidence[0]
        tiers = {e.get("tier") for e in evidence}
        bonus = min((len(tiers) - 1) * bonus_per_tier, bonus_cap)
        scores, relevance_scores = {}, {}
        for name in lead["scores"]:
            scores[name] = round(max(e["scores"].get(name, 0) for e in evidence) + bonus, 1)
            relevance_scores[name] = max(e.get("score_relevance", {}).get(name, 0) for e in evidence)
        events.append({
            "event_id": "E%03d" % (index + 1),
            "title": lead.get("title") or lead.get("body", "")[:100],
            "url": lead.get("url", ""),
            "date": max((e.get("date", "") for e in evidence), default=""),
            "age_days": min(e.get("age_days", 9999) for e in evidence),
            "source_tiers": sorted(t for t in tiers if t),
            "evidence_count": len(evidence),
            "corroboration_bonus": bonus,
            "scores": scores,
            "relevance": {k: round(v, 1) for k, v in relevance_scores.items()},
            "tiers": {name: tier_of(value, cfg) for name, value in scores.items()},
            "assigned": assign(relevance_scores, float(cfg.scoring.get("assign_gap", 0.15))),
            "content_type": lead.get("content_type", ""),
            "evidence": evidence,
        })
    return events


# --------------------------------------------------------------------------
# cross-run history
# --------------------------------------------------------------------------
def apply_history(events: List[Dict], history: Dict, today: str, resurface_gap: int = 21) -> Dict:
    """Mark events new / continuing / resurfaced against previous runs.

    Without this, a story that has been running for a month keeps re-entering
    the top of the report as if it broke today.
    """
    updated = dict(history)
    for event in events:
        key = fingerprint({"url": event.get("url", ""), "source_id": "event",
                           "author": "", "title": event.get("title", ""), "body": ""})
        record = updated.get(key)
        if not record:
            event["continuity"] = "new"
            updated[key] = {"first_seen": today, "last_seen": today, "runs": 1,
                            "title": event.get("title", "")[:120]}
            continue
        try:
            import datetime as _dt
            gap = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(record["last_seen"])).days
        except (ValueError, KeyError):
            gap = 0
        event["continuity"] = "resurfaced" if gap >= resurface_gap else "continuing"
        event["first_seen"] = record.get("first_seen", today)
        record.update({"last_seen": today, "runs": int(record.get("runs", 1)) + 1})
    return updated
