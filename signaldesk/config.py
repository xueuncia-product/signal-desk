# -*- coding: utf-8 -*-
"""Config loading.

Configs are JSONC (JSON + `//` comments + trailing commas). Rationale: the whole
toolkit must run on a stock Python with zero third-party packages, and plain JSON
gives users nowhere to write down *why* a source or a weight is what it is.
Comments in a config file are not decoration here -- most of the cost of running
a beat is remembering why you dropped a source three months ago.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

CONFIG_FILES = {
    "sources": "sources.jsonc",
    "keywords": "keywords.jsonc",
    "scoring": "scoring.jsonc",
}


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, keeping strings intact."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    body = "".join(out)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return body


def load_jsonc(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        return json.loads(strip_jsonc(raw))
    except json.JSONDecodeError as exc:
        raise SystemExit("config parse error in %s: %s" % (path, exc))


class Config:
    """The three config files, loaded and lightly validated."""

    def __init__(self, sources: Dict, keywords: Dict, scoring: Dict, root: str = ""):
        self.root = root
        self.sources_doc = sources
        self.keywords = keywords
        self.scoring = scoring

    @classmethod
    def load(cls, config_dir: str) -> "Config":
        config_dir = os.path.abspath(os.path.expanduser(config_dir))
        docs = {}
        for key, name in CONFIG_FILES.items():
            path = os.path.join(config_dir, name)
            if not os.path.exists(path):
                raise SystemExit("missing config file: %s" % path)
            docs[key] = load_jsonc(path)
        cfg = cls(docs["sources"], docs["keywords"], docs["scoring"], root=config_dir)
        cfg.validate()
        return cfg

    # --- accessors -------------------------------------------------------
    @property
    def window_days(self) -> int:
        return int(self.sources_doc.get("window_days", 7))

    @property
    def sources(self) -> List[Dict]:
        return [s for s in self.sources_doc.get("sources", []) if s.get("enabled", True)]

    @property
    def perspectives(self) -> Dict[str, Dict]:
        return self.keywords.get("perspectives", {})

    @property
    def brand_gate(self) -> List[str]:
        return [w.lower() for w in self.keywords.get("brand_gate", [])]

    @property
    def weights(self) -> Dict[str, float]:
        w = {"source": 20, "author": 15, "engagement": 10, "relevance": 55}
        w.update(self.scoring.get("weights", {}))
        return w

    def source_tier_score(self, tier: str) -> float:
        tiers = self.scoring.get("source_tiers", {})
        return float(tiers.get(tier, tiers.get("default", 6)))

    # --- validation ------------------------------------------------------
    def validate(self) -> List[str]:
        problems = []
        ids = [s.get("id") for s in self.sources_doc.get("sources", [])]
        if not ids:
            problems.append("sources.jsonc: no sources defined")
        for sid in ids:
            if not sid:
                problems.append("sources.jsonc: a source has no id")
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append("sources.jsonc: duplicate source ids: %s" % ", ".join(sorted(map(str, dupes))))
        if not self.perspectives:
            problems.append("keywords.jsonc: at least one perspective is required")
        total = sum(self.weights.values())
        if abs(total - 100) > 0.01:
            problems.append("scoring.jsonc: weights must sum to 100, got %s" % total)
        for name, per in self.perspectives.items():
            if not per.get("proper") and not per.get("gated"):
                problems.append("keywords.jsonc: perspective '%s' has no keywords" % name)
        if problems:
            raise SystemExit("config problems:\n  - " + "\n  - ".join(problems))
        return problems
