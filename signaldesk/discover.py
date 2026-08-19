# -*- coding: utf-8 -*-
"""Keyword discovery: what should be in the config next week, and what is missing.

Two separate scans, because they answer two different questions.

1. `candidates` -- terms rising in the collected corpus that are not in the
   config yet. This is how a new product name or a new regulation reaches the
   keyword table before someone happens to notice it by hand.

2. `gaps` -- terms your own internal documents talk about but the collection
   barely returns. These are blind spots, and they are structurally invisible to
   scan 1: an internal-only term appears in exactly one corpus, so any
   "must appear across N sources" rule filters it out. It gets its own pass.

Neither scan writes to the config. Keyword tables define what a beat means;
auto-appending to them quietly changes the definition, and nobody can tell later
which words a human chose. The output is a ranked candidate list with evidence.

Extraction uses a *shape whitelist*, not a stopword blacklist. A first version
that lowercased and removed stopwords produced a top-50 of ordinary English.
Terms that turn out to matter have recognisable shapes:
    - all-caps acronyms          BVN, CNG, PSP
    - proper nouns               TradeDepot, Sun King
    - hyphenated compounds       pay-as-you-go
    - domain bigrams             stock financing, virtual account
A single lowercase common word almost never becomes a useful keyword.
"""

from __future__ import annotations

import collections
import datetime as dt
import os
import re
from typing import Any, Dict, List

ACRONYM = re.compile(r"\b([A-Z][A-Z0-9]{2,7})\b")
PROPER = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")
# Product and company names are very often camel-cased, and the word-boundary
# pattern above cannot see them: in "TradeDepot" there is no boundary before
# "Depot", so nothing matches at all.
CAMEL = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")
COMPOUND = re.compile(r"\b([a-z]{2,}-[a-z]{2,}(?:-[a-z]{2,})?)\b")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9']{1,20}")

COMMON = set("""the and for you all new not but with from into over under after before since because
this that these those there their they when what where which while who whom whose why how have has
had will would can could should may might must now today tomorrow yesterday get got make made use
used using see saw know knew think thought say said tell told want need good great nice very just
only also then than such some most much many even still back down out off yes people life time day
days week weeks month months year years january february march april may june july august september
october november december mon tue wed thu fri sat sun jan feb mar apr jun jul aug sep oct nov dec
strong apply save other every here more less best top first last next full free open close big small
high low long short early late real true false right left old young about above across against among
around behind below beside between beyond during except inside near outside through toward within
without along amid onto upon company business industry market service services product products team
work working number amount total value price cost money cash account accounts customer customers
user users report reports data update updates change changes news story stories case cases please
thanks thank your our its it is are was were being been one two three four five six seven eight nine
ten hundred thousand million billion""".split())

ACRONYM_STOP = {"HTTP", "HTTPS", "RT", "AM", "PM", "USD", "CEO", "CTO", "CFO", "FAQ", "URL"}
PROPER_STOP = {w.capitalize() for w in COMMON} | {"Read", "More", "Click", "Learn", "Watch"}


def known_terms(cfg) -> set:
    """Every term already configured anywhere -- keyword tables, source filters, queries."""
    terms = set()

    def add(value):
        if isinstance(value, str) and len(value) > 2:
            terms.add(value.lower().strip())

    for word in cfg.brand_gate:
        add(word)
    for perspective in cfg.perspectives.values():
        for bucket in ("proper", "gated", "penalty"):
            for term in (perspective.get(bucket) or {}):
                add(term)
    for source in cfg.sources_doc.get("sources", []):
        for keyword in source.get("keywords", []):
            add(keyword)
        for query in source.get("queries", []) or []:
            for token in re.findall(r'"([^"]+)"|(\w[\w\- ]{2,})', query.get("query", "")):
                add(token[0] or token[1])
    for rule in cfg.scoring.get("content_types", []):
        for token in re.findall(r"[a-zA-Z][a-zA-Z \-]{3,}", rule.get("pattern", "")):
            add(token)
    return terms


def _extract(text: str) -> collections.Counter:
    bag = collections.Counter()
    if not text:
        return bag
    for match in ACRONYM.findall(text):
        if match not in ACRONYM_STOP and match.lower() not in COMMON:
            bag[match.lower()] += 1
    for match in PROPER.findall(text):
        if match.split()[0] not in PROPER_STOP:
            bag[match.lower()] += 1
    for match in CAMEL.findall(text):
        bag[match.lower()] += 1
    for match in COMPOUND.findall(text):
        bag[match.lower()] += 1
    return bag


def _domain_bigrams(text: str, domain: set) -> collections.Counter:
    bag = collections.Counter()
    words = [w.lower() for w in WORD.findall(text or "")]
    for left, right in zip(words, words[1:]):
        if (left in domain or right in domain) and len(left) > 2 and len(right) > 2:
            bag["%s %s" % (left, right)] += 1
    return bag


def _is_known(term: str, known: set) -> bool:
    return any(term == k or (len(k) > 4 and (term in k or k in term)) for k in known)


def _usable(term: str) -> bool:
    parts = term.replace("-", " ").split()
    return len(term) >= 3 and not all(p in COMMON for p in parts)


def candidates(docs: List[Dict], cfg, today: str) -> List[Dict]:
    """docs: [{source, date, text}]"""
    settings = cfg.keywords.get("discovery", {})
    domain = {w.lower() for w in settings.get("domain_words", [])}
    noise = re.compile("|".join(settings.get("noise", [])) or r"(?!x)x", re.I)
    min_freq = int(settings.get("min_freq", 3))
    min_sources = int(settings.get("min_sources", 2))
    fresh_days = int(settings.get("fresh_days", 10))
    known = known_terms(cfg)

    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=fresh_days)).isoformat()
    freq = collections.Counter()
    fresh = collections.Counter()
    sources: Dict[str, set] = collections.defaultdict(set)
    sample: Dict[str, Dict] = {}

    for doc in docs:
        text = doc.get("text") or ""
        if not text or noise.search(text):
            continue
        bag = _extract(text)
        bag.update(_domain_bigrams(text, domain))
        for term, count in bag.items():
            if not _usable(term) or _is_known(term, known):
                continue
            freq[term] += count
            sources[term].add(doc.get("source", "?"))
            if doc.get("date") and str(doc["date"]) >= cutoff:
                fresh[term] += count
            sample.setdefault(term, {
                "source": doc.get("source", "?"), "date": doc.get("date", ""),
                "text": re.sub(r"\s+", " ", text)[:180],
            })

    rows = []
    rejected = {"below_min_freq": 0, "below_min_sources": 0}
    for term, count in freq.items():
        span = len(sources[term])
        if count < min_freq:
            rejected["below_min_freq"] += 1
            continue
        if span < min_sources:
            rejected["below_min_sources"] += 1
            continue
        rows.append({
            "term": term,
            "freq": count,
            "n_sources": span,
            "sources": sorted(sources[term])[:6],
            "fresh_hits": fresh[term],
            # frequency alone floats generic words; the source span filters
            # single-source noise; the freshness factor lifts what is new
            "score": round(count * span * (1 + fresh[term] / max(count, 1)), 1),
            "sample": sample[term],
        })
    rows.sort(key=lambda r: -r["score"])
    # An empty candidate list is ambiguous: it can mean "nothing new this week"
    # or "the corpus is too small for the thresholds". Report which.
    stats = dict(rejected, extracted=len(freq), kept=len(rows),
                 min_freq=min_freq, min_sources=min_sources, docs=len(docs))
    return rows, stats


def gaps(docs: List[Dict], internal_docs: List[Dict], cfg, max_external: int = 2) -> List[Dict]:
    """Terms internal material discusses that collection barely returns."""
    known = known_terms(cfg)
    external = collections.Counter()
    for doc in docs:
        bag = _extract(doc.get("text", ""))
        for term, count in bag.items():
            external[term] += count

    internal = collections.Counter()
    sample: Dict[str, str] = {}
    for doc in internal_docs:
        text = doc.get("text", "")
        bag = _extract(text)
        for term, count in bag.items():
            if not _usable(term) or _is_known(term, known):
                continue
            internal[term] += count
            sample.setdefault(term, re.sub(r"\s+", " ", text)[:200])

    rows = []
    for term, count in internal.items():
        if external[term] > max_external:
            continue
        rows.append({"term": term, "internal_hits": count,
                     "external_hits": external[term], "sample": sample.get(term, "")})
    rows.sort(key=lambda r: (-r["internal_hits"], r["external_hits"]))
    return rows


def read_internal_corpus(path: str) -> List[Dict]:
    """Read .md/.txt files a user drops in, split on headings.

    Splitting matters: one long document counts as one source for every term in
    it, which flattens the frequency signal.
    """
    docs = []
    if not path or not os.path.isdir(path):
        return docs
    for name in sorted(os.listdir(path)):
        if not name.lower().endswith((".md", ".txt", ".markdown")):
            continue
        full = os.path.join(path, name)
        try:
            with open(full, encoding="utf-8", errors="ignore") as fh:
                raw = fh.read()
        except OSError:
            continue
        raw = re.sub(r"https?://\S+", " ", raw)
        for segment in re.split(r"\n(?=#{1,3} )", raw):
            if segment.strip():
                docs.append({"source": "internal:%s" % name, "date": "", "text": segment})
    return docs


def render_markdown(rows: List[Dict], gap_rows: List[Dict], today: str,
                    corpus_size: int, stats: Dict = None) -> str:
    stats = stats or {}
    lines = ["# Keyword candidates (%s)" % today, ""]
    lines.append("Corpus: %d documents. Nothing here is applied automatically -- "
                 "pick what belongs in your beat and paste it into `keywords.jsonc`." % corpus_size)
    lines.append("")
    lines.append("Rank = frequency x source span x freshness.")
    if stats:
        lines.append("")
        lines.append("Extracted %d distinct terms; %d passed the thresholds "
                     "(min_freq=%s, min_sources=%s). Filtered out: %d for frequency, "
                     "%d for single-source." % (
                         stats.get("extracted", 0), stats.get("kept", 0),
                         stats.get("min_freq"), stats.get("min_sources"),
                         stats.get("below_min_freq", 0), stats.get("below_min_sources", 0)))
        if not rows and stats.get("extracted"):
            lines.append("")
            lines.append("> No candidates does **not** mean nothing new happened -- with a corpus "
                         "this small nothing can clear `min_sources`. Lower the thresholds in "
                         "`keywords.jsonc` or widen the collection window.")
    lines.append("")
    lines.append("| term | score | freq | sources | fresh | example |")
    lines.append("|---|---|---|---|---|---|")
    for row in rows[:60]:
        example = row["sample"]["text"].replace("|", "/")[:90]
        lines.append("| `%s` | %s | %d | %d (%s) | %d | %s |" % (
            row["term"], row["score"], row["freq"], row["n_sources"],
            ", ".join(row["sources"][:3]), row["fresh_hits"], example))
    lines += ["", "# Coverage gaps", "",
              "Terms your internal corpus discusses that collection barely returns. "
              "These are the ones worth adding first -- your organisation already cares, "
              "your sources are not looking.", "",
              "| term | internal | collected | context |", "|---|---|---|---|"]
    for row in gap_rows[:40]:
        lines.append("| `%s` | %d | %d | %s |" % (
            row["term"], row["internal_hits"], row["external_hits"],
            row["sample"].replace("|", "/")[:90]))
    if not gap_rows:
        lines.append("| _(no internal corpus configured)_ | | | |")
    return "\n".join(lines) + "\n"
