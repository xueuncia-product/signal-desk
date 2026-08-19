# -*- coding: utf-8 -*-
"""Self-test: python3 tests/selftest.py

Covers the behaviours that are easy to break silently -- the ones where a bug
produces a plausible-looking report rather than a crash.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from signaldesk import discover, pipeline, validate  # noqa: E402
from signaldesk.config import Config, strip_jsonc  # noqa: E402
from signaldesk.model import canonical_url, make_signal, normalize_date  # noqa: E402
from signaldesk.collectors.json_import import dig, rows_from  # noqa: E402
from signaldesk.collectors.rss import parse_feed  # noqa: E402

MINIMAL = os.path.join(ROOT, "examples", "minimal")
FULL = os.path.join(ROOT, "examples", "full")


class ConfigTest(unittest.TestCase):
    def test_jsonc_keeps_string_content(self):
        text = '{"a": "http://x/y", /* c */ "b": "not // a comment", "c": [1,2,],}'
        self.assertEqual(json.loads(strip_jsonc(text)),
                         {"a": "http://x/y", "b": "not // a comment", "c": [1, 2]})

    def test_examples_load(self):
        for path in (MINIMAL, FULL):
            cfg = Config.load(path)
            self.assertTrue(cfg.sources, "%s has no sources" % path)
            self.assertAlmostEqual(sum(cfg.weights.values()), 100)

    def test_weights_must_total_100(self):
        cfg = Config({"sources": [{"id": "x", "type": "rss"}]},
                     {"perspectives": {"p": {"proper": {"a": 1}}}},
                     {"weights": {"source": 10, "author": 10, "engagement": 10, "relevance": 10}})
        with self.assertRaises(SystemExit):
            cfg.validate()


class ModelTest(unittest.TestCase):
    def test_tracking_params_stripped_but_ids_kept(self):
        # Cutting the whole query string collapses every app-store review of one
        # app into a single item, because the review id lives in the query.
        self.assertEqual(canonical_url("https://a.com/p?utm_source=x&id=99"), "https://a.com/p?id=99")
        self.assertEqual(canonical_url("https://a.com/p/?utm_source=x"), "https://a.com/p")

    def test_dates(self):
        self.assertEqual(normalize_date("Tue, 12 Aug 2026 09:00:00 +0000"), "2026-08-12")
        self.assertEqual(normalize_date("2026-08-12T10:11:12Z"), "2026-08-12")
        self.assertEqual(normalize_date("garbage"), "")  # never guess


class DedupeTest(unittest.TestCase):
    def signals(self):
        return [
            make_signal("s", "S", "media", title="A", url="https://x.com/1"),
            make_signal("s", "S", "media", title="A", url="https://x.com/1?utm_source=feed"),
            make_signal("s", "S", "social", title="same words here", author="bob", url="https://x.com/2"),
            make_signal("s", "S", "social", title="same words here", author="bob", url="https://x.com/3"),
            make_signal("s", "S", "social", title="different entirely", author="ann", url="https://x.com/4"),
        ]

    def test_two_passes(self):
        kept, stats = pipeline.dedupe(self.signals())
        self.assertEqual(stats["duplicate_url"], 1)
        self.assertEqual(stats["duplicate_body"], 1)  # same author, same text, different url
        self.assertEqual(len(kept), 3)


class RelevanceTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(MINIMAL)

    def test_gated_terms_need_a_brand(self):
        per = self.cfg.perspectives["tooling"]
        without, hits = pipeline.relevance("pricing changes and rate limit news", per, self.cfg)
        self.assertEqual(without, 0.0)
        self.assertIn("no brand-gate word", " ".join(hits))
        with_brand, _ = pipeline.relevance("openai pricing changes and rate limit news", per, self.cfg)
        self.assertGreater(with_brand, 0)

    def test_proper_terms_score_alone(self):
        per = self.cfg.perspectives["tooling"]
        score, _ = pipeline.relevance("a note about mcp servers", per, self.cfg)
        self.assertGreater(score, 0)

    def test_penalty_subtracts(self):
        per = self.cfg.perspectives["tooling"]
        clean, _ = pipeline.relevance("mcp registry update", per, self.cfg)
        penalised, _ = pipeline.relevance("mcp registry update, we are hiring", per, self.cfg)
        self.assertLess(penalised, clean)


class AssignTest(unittest.TestCase):
    def test_zero_relevance_belongs_to_nobody(self):
        # Regression: assigning on the *total* score sent every high-authority
        # item with no keyword match to every perspective at once.
        self.assertEqual(pipeline.assign({"a": 0.0, "b": 0.0}, 0.15), [])

    def test_close_scores_belong_to_both(self):
        self.assertEqual(sorted(pipeline.assign({"a": 10.0, "b": 9.5}, 0.15)), ["a", "b"])
        self.assertEqual(pipeline.assign({"a": 10.0, "b": 2.0}, 0.15), ["a"])


class ClassifyTest(unittest.TestCase):
    def test_support_reply_is_damped(self):
        cfg = Config.load(FULL)
        signal = make_signal("s", "S", "social",
                             body="Thank you for contacting us, kindly send your merchant ID via DM")
        name, multiplier = pipeline.classify(signal, cfg)
        self.assertEqual(name, "support reply")
        self.assertLess(multiplier, 1.0)

    def test_primary_sources_are_never_damped(self):
        cfg = Config.load(FULL)
        signal = make_signal("s", "S", "regulator", body="we are sorry to announce a licence revocation")
        _, multiplier = pipeline.classify(signal, cfg)
        self.assertEqual(multiplier, 1.0)


class ImportTest(unittest.TestCase):
    def test_nested_field_paths(self):
        row = {"a": {"b": [{"c": "hit"}]}, "n": {"views": "12"}}
        self.assertEqual(dig(row, "a.b.0.c"), "hit")
        self.assertEqual(dig(row, "a.missing.0.c", "fallback"), "fallback")

    def test_envelope_unwrapping(self):
        self.assertEqual(rows_from({"data": [1, 2]}), [1, 2])
        self.assertEqual(rows_from([1, 2]), [1, 2])
        self.assertEqual(rows_from({"payload": {"items": [3]}}, "payload"), [3])


class FeedTest(unittest.TestCase):
    RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Hello</title><link>https://a.com/1</link>
      <pubDate>Tue, 12 Aug 2026 09:00:00 +0000</pubDate>
      <description>&lt;p&gt;body text&lt;/p&gt;</description></item></channel></rss>"""
    ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom item</title><link rel="alternate" href="https://a.com/2"/>
      <published>2026-08-13T00:00:00Z</published><summary>sum</summary></entry></feed>"""

    def test_rss_and_atom(self):
        rss = parse_feed(self.RSS)
        self.assertEqual(rss[0]["title"], "Hello")
        self.assertEqual(rss[0]["link"], "https://a.com/1")
        self.assertEqual(rss[0]["summary"], "body text")  # html stripped
        atom = parse_feed(self.ATOM)
        self.assertEqual(atom[0]["link"], "https://a.com/2")


class DiscoveryTest(unittest.TestCase):
    def test_shape_whitelist_and_threshold_reporting(self):
        cfg = Config(
            {"sources": []},
            {"brand_gate": [], "perspectives": {"p": {"proper": {"zzz": 1}}},
             "discovery": {"domain_words": ["stock", "financing"], "min_freq": 2, "min_sources": 2}},
            {"weights": {"source": 20, "author": 15, "engagement": 10, "relevance": 55}},
        )
        docs = [
            {"source": "a", "date": "2026-08-18", "text": "The NQR standard and TradeDepot expand stock financing"},
            {"source": "b", "date": "2026-08-18", "text": "NQR adoption grows; TradeDepot raises for stock financing"},
        ]
        rows, stats = discover.candidates(docs, cfg, "2026-08-19")
        terms = {r["term"] for r in rows}
        self.assertIn("nqr", terms)
        self.assertTrue({"tradedepot", "stock financing"} & terms)
        self.assertNotIn("the", terms)          # common words never survive
        self.assertEqual(stats["docs"], 2)
        self.assertIn("below_min_sources", stats)  # empty output must be explainable

    def test_gap_scan_finds_internal_only_terms(self):
        cfg = Config.load(MINIMAL)
        external = [{"source": "feed", "date": "", "text": "unrelated market news"}]
        internal = [{"source": "internal", "date": "", "text": "ACME Ledger rollout and BVN checks"}]
        gaps = discover.gaps(external, internal, cfg)
        self.assertTrue(any(g["term"] in ("bvn", "acme ledger", "acme") for g in gaps))


class EndToEndTest(unittest.TestCase):
    def test_offline_run_produces_a_report_and_passes_validation(self):
        workdir = tempfile.mkdtemp(prefix="signaldesk-test-")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "signaldesk", "run", "--config", MINIMAL,
                 "--workdir", workdir, "--today", "2026-08-19", "--offline"],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            for name in ("signals_raw.json", "events.json", "digest.md",
                         "report_tooling.md", "keyword_candidates.md", "validation.json"):
                self.assertTrue(os.path.exists(os.path.join(workdir, name)), "missing %s" % name)
            with open(os.path.join(workdir, "events.json"), encoding="utf-8") as fh:
                events = json.load(fh)
            self.assertTrue(events)
            # The job posting in the fixture must not reach core/important.
            for event in events:
                if "hiring" in event["title"].lower():
                    self.assertNotIn(event["tiers"]["tooling"], ("core", "important"))
            with open(os.path.join(workdir, "validation.json"), encoding="utf-8") as fh:
                self.assertTrue(json.load(fh)["passed"])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
