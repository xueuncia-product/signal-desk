# -*- coding: utf-8 -*-
"""Hard, repeatable acceptance checks on a finished run.

These are the rules that kept being violated by hand: stale items promoted as
news, recruitment posts ranked as product signal, two perspectives producing
near-identical top lists, a whole channel silently missing. Each is cheap to
check mechanically and expensive to catch by reading.

Exit code is non-zero when any check fails, so this can gate a scheduled job.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


def run_checks(events: List[Dict], signals: List[Dict], statuses: List[Dict], cfg) -> Tuple[List[Dict], bool]:
    rules = cfg.scoring.get("hard_rules", {})
    checks: List[Dict] = []

    def check(label: str, ok: bool, detail: str):
        checks.append({"label": label, "passed": bool(ok), "detail": detail})

    urls = [s["url"] for s in signals if s.get("url")]
    check("no duplicate links survived dedupe", len(urls) == len(set(urls)),
          "%d duplicates" % (len(urls) - len(set(urls))))

    top_tiers = {"core", "important"}
    max_age = int(rules.get("max_age_days_for_core", 30))
    stale = [e["event_id"] for e in events
             if any(t in top_tiers for t in e["tiers"].values()) and e.get("age_days", 9999) > max_age]
    check("nothing older than %d days is core/important" % max_age, not stale,
          "%d events: %s" % (len(stale), ", ".join(stale[:5])))

    undated = [e["event_id"] for e in events
               if any(t in top_tiers for t in e["tiers"].values()) and not e.get("date")]
    check("no undated item is core/important", not undated,
          "%d events: %s" % (len(undated), ", ".join(undated[:5])))

    excluded = rules.get("exclude_from_core_patterns", [])
    if excluded:
        pattern = re.compile("|".join(excluded), re.I)
        bad = []
        for event in events:
            if not any(t in top_tiers for t in e_tiers(event)):
                continue
            blob = " ".join([event["title"]] + [s.get("body", "") for s in event["evidence"][:3]])
            if pattern.search(blob):
                bad.append(event["event_id"])
        check("excluded content types stay out of core/important", not bad,
              "%d events: %s" % (len(bad), ", ".join(bad[:5])))

    names = list(cfg.perspectives)
    if len(names) > 1:
        limit = int(rules.get("max_top_overlap", 14))
        size = int(rules.get("overlap_window", 20))
        # Compare what each perspective actually *reports*, i.e. the events
        # assigned to it -- not the global ranking, which is identical by
        # construction for everything with no keyword match.
        tops = {}
        for n in names:
            owned = [e for e in events if n in e.get("assigned", [])]
            tops[n] = {e["event_id"] for e in sorted(owned, key=lambda x: -x["scores"].get(n, 0))[:size]}
        worst, pair = 0, ""
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                overlap = len(tops[left] & tops[right])
                if overlap > worst:
                    worst, pair = overlap, "%s/%s" % (left, right)
        check("perspectives produce different top lists", worst <= limit,
              "worst overlap %d/%d (%s), limit %d" % (worst, size, pair, limit))

    required = set(rules.get("required_source_tiers", []))
    if required:
        present = {s.get("tier") for s in signals}
        missing = sorted(required - present)
        check("all required source tiers returned data", not missing,
              "missing: %s" % ", ".join(missing) if missing else "complete")

    failed_sources = [s["id"] for s in statuses if s.get("state") == "error"]
    tolerance = int(rules.get("max_failed_sources", 0))
    check("source failures within tolerance", len(failed_sources) <= tolerance,
          "%d failed: %s" % (len(failed_sources), ", ".join(failed_sources[:6])))

    ok = all(c["passed"] for c in checks)
    return checks, ok


def e_tiers(event: Dict[str, Any]):
    return list(event.get("tiers", {}).values())


def render(checks: List[Dict]) -> str:
    lines = []
    for check in checks:
        lines.append("[%s] %s: %s" % ("PASS" if check["passed"] else "FAIL", check["label"], check["detail"]))
    passed = sum(1 for c in checks if c["passed"])
    lines.append("%d/%d checks passed" % (passed, len(checks)))
    return "\n".join(lines)
