# -*- coding: utf-8 -*-
"""signal-desk command line.

    python3 -m signaldesk doctor   --config examples/minimal
    python3 -m signaldesk run      --config examples/minimal --workdir runs/demo

Every command is safe to re-run: the config directory is read-only to this tool
and all output goes to --workdir.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, List

from . import collectors, discover, pipeline, report as reporting, validate as validation
from .config import Config
from .model import in_window, text_of

NETWORK_KINDS = {"rss", "page_watch"}


# ---------------------------------------------------------------- helpers
def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _context(args, cfg: Config) -> Dict[str, Any]:
    return {
        "workdir": os.path.abspath(args.workdir),
        "config_dir": cfg.root,
        "today": args.today,
        "offline": bool(getattr(args, "offline", False)),
        "force": bool(getattr(args, "force", False)),
    }


# ---------------------------------------------------------------- commands
def cmd_doctor(args) -> int:
    cfg = Config.load(args.config)
    print("config: %s" % cfg.root)
    print("window: last %d days, perspectives: %s" % (
        cfg.window_days, ", ".join(cfg.perspectives) or "none"))
    weights = cfg.weights
    print("weights: %s (total %g)" % (
        ", ".join("%s %g" % kv for kv in weights.items()), sum(weights.values())))
    print("collector kinds available: %s" % ", ".join(collectors.kinds()))
    problems = 0
    for source in cfg.sources_doc.get("sources", []):
        kind = source.get("type", "")
        mark = "ok "
        note = ""
        if not collectors.get(kind):
            mark, note, problems = "ERR", "unknown collector type '%s'" % kind, problems + 1
        elif kind == "command":
            binary = (source.get("command") or [""])[0]
            found = any(os.access(os.path.join(p, binary), os.X_OK)
                        for p in os.environ.get("PATH", "").split(os.pathsep) if p)
            if not found:
                mark, note = "warn", "`%s` not on PATH — this source will be skipped" % binary
        elif kind == "json_import":
            path = source.get("path", "")
            full = path if os.path.isabs(path) else os.path.join(cfg.root, path)
            if not os.path.exists(os.path.dirname(full) or "."):
                mark, note = "warn", "input directory does not exist yet: %s" % full
        enabled = "" if source.get("enabled", True) else " (disabled)"
        print("  [%s] %-22s %-12s %s%s" % (mark, source.get("id", "?"), kind, note, enabled))
    keywords = sum(len(p.get("proper", {})) + len(p.get("gated", {})) for p in cfg.perspectives.values())
    print("keyword terms configured: %d (brand gate: %d)" % (keywords, len(cfg.brand_gate)))
    if problems:
        print("\n%d blocking problem(s)." % problems)
    return 1 if problems else 0


def cmd_collect(args) -> int:
    cfg = Config.load(args.config)
    ctx = _context(args, cfg)
    os.makedirs(ctx["workdir"], exist_ok=True)

    signals: List[Dict] = []
    statuses: List[Dict] = []
    for source in cfg.sources:
        kind = source.get("type", "")
        collector = collectors.get(kind)
        label = source.get("label", source.get("id", "?"))
        if not collector:
            statuses.append({"id": source.get("id"), "label": label, "state": "error",
                             "count": 0, "note": "unknown type %s" % kind, "tier": source.get("tier")})
            continue
        if ctx["offline"] and kind in NETWORK_KINDS:
            statuses.append({"id": source["id"], "label": label, "state": "skipped_offline",
                             "count": 0, "note": "network source skipped (--offline)",
                             "tier": source.get("tier")})
            continue
        produced, status = collector(source, ctx)
        fresh = [s for s in produced if in_window(s, ctx["today"], cfg.window_days)]
        outside = len(produced) - len(fresh)
        signals.extend(fresh)
        note = status.get("note", "")
        if outside:
            note = (note + "; " if note else "") + "%d outside the %d-day window" % (outside, cfg.window_days)
        statuses.append({"id": source["id"], "label": label, "state": status["state"],
                         "count": len(fresh), "note": note, "tier": source.get("tier")})
        print("  [%s] %-22s %3d items  %s" % (status["state"][:4], source["id"], len(fresh), note[:80]))

    _write_json(os.path.join(ctx["workdir"], "signals_raw.json"), signals)
    _write_json(os.path.join(ctx["workdir"], "source_status.json"), statuses)
    print("collected %d in-window signals from %d sources -> %s" % (
        len(signals), len(statuses), ctx["workdir"]))
    return 0


def cmd_score(args) -> int:
    cfg = Config.load(args.config)
    ctx = _context(args, cfg)
    raw = _read_json(os.path.join(ctx["workdir"], "signals_raw.json"), [])
    if not raw:
        print("no signals_raw.json in %s — run `collect` first" % ctx["workdir"])
        return 1
    kept, stats = pipeline.dedupe(raw)
    scored = pipeline.score_all(kept, cfg, ctx["today"])
    events = pipeline.cluster(scored, cfg)

    history_path = os.path.join(ctx["workdir"], "..", "history.json")
    history = _read_json(os.path.abspath(history_path), {}) or {}
    history = pipeline.apply_history(events, history, ctx["today"],
                                     int(cfg.scoring.get("resurface_gap_days", 21)))
    _write_json(os.path.abspath(history_path), history)

    stats["collected"] = len(raw)
    _write_json(os.path.join(ctx["workdir"], "scored.json"), scored)
    _write_json(os.path.join(ctx["workdir"], "events.json"), events)
    _write_json(os.path.join(ctx["workdir"], "stats.json"), stats)
    print("deduped %d -> %d (links %d, bodies %d), %d events" % (
        len(raw), len(kept), stats["duplicate_url"], stats["duplicate_body"], len(events)))
    for name in cfg.perspectives:
        top = sorted(events, key=lambda e: -e["scores"].get(name, 0))[:3]
        print("  %s top:" % name)
        for event in top:
            print("    %5.1f  %s" % (event["scores"].get(name, 0), event["title"][:70]))
    return 0


def cmd_report(args) -> int:
    cfg = Config.load(args.config)
    ctx = _context(args, cfg)
    events = _read_json(os.path.join(ctx["workdir"], "events.json"), [])
    statuses = _read_json(os.path.join(ctx["workdir"], "source_status.json"), [])
    stats = _read_json(os.path.join(ctx["workdir"], "stats.json"), {})
    if not events:
        print("no events.json — run `score` first")
        return 1
    for name in cfg.perspectives:
        text = reporting.render_perspective(name, events, cfg, statuses, ctx["today"])
        path = os.path.join(ctx["workdir"], "report_%s.md" % name)
        _write_text(path, text)
        print("wrote %s" % path)
    digest = reporting.render_digest(events, cfg, statuses, ctx["today"], stats)
    _write_text(os.path.join(ctx["workdir"], "digest.md"), digest)
    print("wrote %s" % os.path.join(ctx["workdir"], "digest.md"))
    return 0


def cmd_discover(args) -> int:
    cfg = Config.load(args.config)
    ctx = _context(args, cfg)
    signals = _read_json(os.path.join(ctx["workdir"], "signals_raw.json"), [])
    if not signals:
        print("no signals_raw.json — run `collect` first")
        return 1
    docs = [{"source": s.get("source_label", s.get("source_id", "?")),
             "date": s.get("date", ""), "text": text_of(s)} for s in signals]
    internal_dir = cfg.keywords.get("discovery", {}).get("internal_corpus_dir", "")
    if internal_dir and not os.path.isabs(internal_dir):
        internal_dir = os.path.join(cfg.root, internal_dir)
    internal = discover.read_internal_corpus(internal_dir)
    rows, stats = discover.candidates(docs, cfg, ctx["today"])
    gap_rows = discover.gaps(docs, internal, cfg) if internal else []
    _write_json(os.path.join(ctx["workdir"], "keyword_candidates.json"),
                {"candidates": rows[:150], "gaps": gap_rows[:120], "stats": stats})
    _write_text(os.path.join(ctx["workdir"], "keyword_candidates.md"),
                discover.render_markdown(rows, gap_rows, ctx["today"], len(docs), stats))
    print("%d candidate terms, %d coverage gaps (internal corpus: %d docs) -> keyword_candidates.md" % (
        len(rows), len(gap_rows), len(internal)))
    print("  extracted %d distinct terms; dropped %d below min_freq=%s, %d below min_sources=%s" % (
        stats["extracted"], stats["below_min_freq"], stats["min_freq"],
        stats["below_min_sources"], stats["min_sources"]))
    for row in rows[:10]:
        print("  %7.1f  %-28s freq %d, %d sources" % (
            row["score"], row["term"][:28], row["freq"], row["n_sources"]))
    return 0


def cmd_validate(args) -> int:
    cfg = Config.load(args.config)
    ctx = _context(args, cfg)
    events = _read_json(os.path.join(ctx["workdir"], "events.json"), [])
    signals = _read_json(os.path.join(ctx["workdir"], "scored.json"), [])
    statuses = _read_json(os.path.join(ctx["workdir"], "source_status.json"), [])
    if not events:
        print("no events.json — run `score` first")
        return 1
    checks, ok = validation.run_checks(events, signals, statuses, cfg)
    text = validation.render(checks)
    print(text)
    _write_json(os.path.join(ctx["workdir"], "validation.json"), {"checks": checks, "passed": ok})
    return 0 if ok else 1


def cmd_run(args) -> int:
    for step in (cmd_collect, cmd_score, cmd_report, cmd_discover):
        code = step(args)
        if code:
            return code
    return cmd_validate(args)


COMMANDS = {
    "doctor": cmd_doctor, "collect": cmd_collect, "score": cmd_score,
    "report": cmd_report, "discover": cmd_discover, "validate": cmd_validate, "run": cmd_run,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="signal-desk", description="Configurable multi-source signal desk")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--config", default="examples/minimal", help="config directory")
    parser.add_argument("--workdir", default="", help="output directory (default: runs/<date>)")
    parser.add_argument("--today", default=dt.date.today().isoformat(), help="YYYY-MM-DD, the run date")
    parser.add_argument("--offline", action="store_true", help="skip network sources; use caches and imports")
    parser.add_argument("--force", action="store_true", help="ignore command-adapter caches")
    args = parser.parse_args(argv)
    if not args.workdir:
        args.workdir = os.path.join("runs", args.today)
    try:
        dt.date.fromisoformat(args.today)
    except ValueError:
        print("--today must be YYYY-MM-DD")
        return 2
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
