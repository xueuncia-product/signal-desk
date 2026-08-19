# -*- coding: utf-8 -*-
"""Render the ranked events into per-perspective markdown briefs.

Reports are evidence-first: every line carries its date, source and link, and
the score breakdown is printed so a reader can argue with the ranking instead of
having to trust it.
"""

from __future__ import annotations

from typing import Any, Dict, List

TIER_LABEL = {"core": "Core", "important": "Important", "watch": "Watch", "ledger": "Ledger"}
CONTINUITY_MARK = {"new": "NEW", "continuing": "cont.", "resurfaced": "back"}


def source_status_table(statuses: List[Dict]) -> str:
    lines = ["| source | state | items | note |", "|---|---|---|---|"]
    for row in statuses:
        lines.append("| %s | %s | %d | %s |" % (
            row.get("label", row.get("id", "?")), row.get("state", "?"),
            int(row.get("count", 0)), (row.get("note", "") or "").replace("|", "/")[:120]))
    return "\n".join(lines)


def _evidence_line(signal: Dict) -> str:
    bits = [signal.get("date") or "no date", signal.get("source_label", signal.get("source_id", "?"))]
    if signal.get("author"):
        bits.append("@" + signal["author"])
    if signal.get("engagement"):
        bits.append("engagement %d" % signal["engagement"])
    head = " · ".join(bits)
    text = (signal.get("title") or signal.get("body", ""))[:220].replace("\n", " ")
    url = signal.get("url", "")
    return "  - %s — %s%s" % (head, text, "\n    %s" % url if url else "")


def render_perspective(name: str, events: List[Dict], cfg, statuses: List[Dict], today: str) -> str:
    label = cfg.perspectives.get(name, {}).get("label", name)
    selected = [e for e in events if name in e.get("assigned", [])]
    selected.sort(key=lambda e: -e["scores"].get(name, 0))

    lines = ["# %s — %s" % (label, today), ""]
    counts = {}
    for event in selected:
        counts[event["tiers"][name]] = counts.get(event["tiers"][name], 0) + 1
    lines.append("%d events assigned · %s" % (
        len(selected),
        " · ".join("%s %d" % (TIER_LABEL.get(k, k), v) for k, v in sorted(counts.items())) or "none",
    ))
    lines.append("")

    for tier in ("core", "important", "watch", "ledger"):
        bucket = [e for e in selected if e["tiers"][name] == tier]
        if not bucket:
            continue
        limit = int(cfg.scoring.get("report_limits", {}).get(tier, 0)) or len(bucket)
        lines.append("## %s (%d)" % (TIER_LABEL.get(tier, tier), len(bucket)))
        lines.append("")
        for event in bucket[:limit]:
            mark = CONTINUITY_MARK.get(event.get("continuity", "new"), "")
            lines.append("### %s %s [%s]" % (
                event["event_id"], event["title"][:150], mark))
            meta = [
                "score %.1f" % event["scores"].get(name, 0),
                "%s" % event.get("date") or "no date",
                "%d source tier(s)" % len(event.get("source_tiers", [])),
                "%d piece(s) of evidence" % event.get("evidence_count", 1),
            ]
            if event.get("corroboration_bonus"):
                meta.append("corroboration +%.1f" % event["corroboration_bonus"])
            if event.get("content_type"):
                meta.append(event["content_type"])
            lines.append("*%s*" % " · ".join(meta))
            lines.append("")
            for signal in event["evidence"][:5]:
                lines.append(_evidence_line(signal))
            if event.get("evidence_count", 1) > 5:
                lines.append("  - … %d more" % (event["evidence_count"] - 5))
            lines.append("")
        if len(bucket) > limit:
            lines.append("_%d more %s items withheld by report_limits — they are in events.json, "
                         "not lost._" % (len(bucket) - limit, TIER_LABEL.get(tier, tier)))
            lines.append("")

    lines += ["## Source health", "", source_status_table(statuses), "",
              "_A source in `error` or `thin` state is not the same as a quiet week. "
              "Check this table before concluding nothing happened._", ""]
    return "\n".join(lines)


def render_digest(events: List[Dict], cfg, statuses: List[Dict], today: str, stats: Dict[str, Any]) -> str:
    lines = ["# Signal digest — %s" % today, ""]
    lines.append("Collected %d, kept %d after dedupe (%d duplicate links, %d duplicate bodies), "
                 "clustered into %d events." % (
                     stats.get("collected", 0), stats.get("kept", 0),
                     stats.get("duplicate_url", 0), stats.get("duplicate_body", 0), len(events)))
    lines.append("")
    for name in cfg.perspectives:
        label = cfg.perspectives[name].get("label", name)
        top = sorted([e for e in events if name in e.get("assigned", [])],
                     key=lambda e: -e["scores"].get(name, 0))[:5]
        lines.append("## %s" % label)
        lines.append("")
        for event in top:
            lines.append("- **%.1f** %s — %s%s" % (
                event["scores"].get(name, 0), event["title"][:130],
                event.get("date") or "no date",
                " (%s)" % event.get("continuity") if event.get("continuity") else ""))
        if not top:
            lines.append("- _nothing above threshold this run_")
        lines.append("")
    lines += ["## Source health", "", source_status_table(statuses), ""]
    return "\n".join(lines)
