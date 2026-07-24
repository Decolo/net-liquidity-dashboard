#!/usr/bin/env python3
"""Unit tests for voice.py and build_site.py — stdlib unittest, no network."""

import unittest


class TestRating(unittest.TestCase):
    """The rating rule is the site's single most visible behavior."""

    def rate(self, balance_sheet="stable", rates="on_hold", funding="neutral", flags=()):
        import voice
        return voice.rate(
            {"balance_sheet": balance_sheet, "rates": rates, "funding": funding},
            list(flags),
        )

    def test_funding_stress_is_red(self):
        self.assertEqual(self.rate(funding="stress"), "red")

    def test_vol_elevated_flag_is_red(self):
        self.assertEqual(self.rate(flags=["vol_elevated"]), "red")

    def test_qt_active_is_yellow(self):
        self.assertEqual(self.rate(balance_sheet="qt_active"), "yellow")

    def test_hiking_is_yellow(self):
        self.assertEqual(self.rate(rates="hiking"), "yellow")

    def test_yellow_flags(self):
        for f in ("credit_tightening", "h8_loans_contracting", "debt_ceiling_watch"):
            with self.subTest(flag=f):
                self.assertEqual(self.rate(flags=[f]), "yellow")

    def test_all_clear_is_green(self):
        self.assertEqual(self.rate(), "green")

    def test_unknown_states_empty_flags_is_green(self):
        self.assertEqual(
            self.rate(balance_sheet="unknown", rates="unknown", funding="unknown"),
            "green",
        )

    def test_rrp_near_zero_alone_stays_green(self):
        # rrp_T ≈ 0 is a multi-year structural state, not a signal. If it
        # triggered yellow the site would cry wolf permanently.
        self.assertEqual(self.rate(flags=["rrp_near_zero"]), "green")

    def test_layer_heuristic_flags_are_ignored(self):
        # server.py merges per-layer heuristic_flags (buyback_proxy_weak etc.)
        # into snapshot.flags — the rating must only know regime.py's 5 flags.
        self.assertEqual(self.rate(flags=["buyback_proxy_weak"]), "green")


class TestVoiceLines(unittest.TestCase):
    """Drivers / headline / card lines — the deterministic zh voice."""

    def setUp(self):
        import voice
        self.voice = voice

    def test_drivers_empty_when_green(self):
        regime = {"states": {"balance_sheet": "qe_active", "rates": "on_hold",
                             "funding": "easy"},
                  "flags": ["rrp_near_zero"], "inputs": {}, "thresholds": {}}
        self.assertEqual(self.voice.rating_drivers(regime, {}), [])

    def test_drivers_carry_values_and_thresholds(self):
        regime = {"states": {"balance_sheet": "stable", "rates": "on_hold",
                             "funding": "stress"},
                  "flags": ["vol_elevated"], "inputs": {},
                  "thresholds": {"sofr_stress_bp": 15.0}}
        d = self.voice.rating_drivers(regime, {"sofr_minus_iorb_bp": 18.0, "vix": 31.2})
        self.assertTrue(any("18" in x and "15" in x for x in d), d)
        self.assertTrue(any("31.2" in x for x in d), d)

    def test_drivers_capped_at_three(self):
        regime = {"states": {"balance_sheet": "qt_active", "rates": "hiking",
                             "funding": "stress"},
                  "flags": ["vol_elevated", "credit_tightening"],
                  "inputs": {"walcl_chg_30d_B": -40.0, "iorb_chg_60d_bp": 25.0},
                  "thresholds": {"sofr_stress_bp": 15.0}}
        d = self.voice.rating_drivers(regime, {"sofr_minus_iorb_bp": 20.0,
                                               "vix": 30.0, "hy_oas": 5.1})
        self.assertLessEqual(len(d), 3)

    def test_headline_green_flow_direction(self):
        h = self.voice.headline("green", {"net_liquidity_T": 5.917, "delta_30d_T": 0.153})
        self.assertIn("5.92 万亿", h)
        self.assertIn("净流入", h)
        self.assertIn("充裕", h)

    def test_headline_outflow_and_missing_data(self):
        h = self.voice.headline("yellow", {"net_liquidity_T": 5.9, "delta_30d_T": -0.05})
        self.assertIn("净流出", h)
        self.assertIn("500 亿", h)  # 0.05T = 500亿
        self.assertIn("暂缺", self.voice.headline("green", {}))

    def test_card_lines_rrp_info_suffix(self):
        lines = self.voice.card_lines(
            {"net_liquidity_T": 5.9, "delta_30d_T": 0.1,
             "sofr_minus_iorb_bp": -3.0, "hy_oas": 3.1, "vix": 16.0},
            {"balance_sheet": "qe_active", "rates": "on_hold", "funding": "easy"},
            ["rrp_near_zero"],
        )
        self.assertIn("缓冲垫", lines["net_liq"])
        self.assertIn("不堵", lines["sofr"])
        self.assertIn("不闹心", lines["credit_vol"])
        self.assertIn("扩表注水中", lines["regime"])

    def test_card_lines_missing_values_degrade(self):
        lines = self.voice.card_lines({}, {}, [])
        self.assertTrue(all("暂缺" in v or "数据未知" in v for v in lines.values()), lines)

    def test_regime_labels_zh(self):
        out = self.voice.regime_labels_zh(
            {"balance_sheet": "qt_active", "rates": "cutting", "funding": "neutral"},
            ["rrp_near_zero", "unknown_flag_xyz"],
        )
        self.assertEqual(out["states"]["balance_sheet"], "缩表抽水中")
        self.assertEqual(out["flags"][0], "缓冲水池见底")
        self.assertEqual(out["flags"][1], "unknown_flag_xyz")  # unknown flags pass through


class TestSanitize(unittest.TestCase):
    """The site artifact must never leak local paths or personal holdings."""

    def test_strips_users_paths_at_any_depth(self):
        import build_site
        dirty = {"a": {"b": ["/Users/decolo/Github/net-liquidity-dashboard/data/x.csv"]}}
        out = build_site.sanitize(dirty, build_site.ROOT)
        self.assertEqual(out["a"]["b"][0], "x.csv")

    def test_strips_stale_foreign_root_path(self):
        # latest_finra_margin.json embeds /Users/decolo/net-liquidity-dashboard/...
        # — a DIFFERENT root than this repo. Value-based matching must catch it.
        import build_site
        out = build_site.sanitize({"history_csv": "/Users/decolo/net-liquidity-dashboard/data/f.csv"},
                                  build_site.ROOT)
        self.assertEqual(out["history_csv"], "f.csv")

    def test_keeps_normal_strings(self):
        import build_site
        out = build_site.sanitize({"note": "SOFR above IORB", "v": 1.5}, build_site.ROOT)
        self.assertEqual(out, {"note": "SOFR above IORB", "v": 1.5})

    def test_site_snapshot_drops_holdings_and_project(self):
        import build_site
        snap = build_site.site_snapshot()
        self.assertNotIn("project", snap)
        self.assertNotIn("holdings", snap.get("layers") or {})

    def test_serialized_site_jsons_are_leak_free(self):
        """The real safety net: scan the final serialized artifacts."""
        import json as _json
        import build_site

        def _keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield k
                    yield from _keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from _keys(v)

        for artifact in (build_site.site_snapshot(),
                         build_site.build_brief(build_site.site_snapshot(), [])):
            text = _json.dumps(artifact, ensure_ascii=False)
            self.assertNotIn("/Users/", text)
            self.assertNotIn(str(build_site.ROOT), text)
            # no dict key named "holdings" at any depth (prose mentions like
            # "holdings microstructure" in discipline text are fine)
            self.assertNotIn("holdings", set(_keys(artifact)))


class TestHistory(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        from pathlib import Path
        self.path = Path(self._tmp.name) / "h.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_creates_file(self):
        import build_site
        build_site.append_history({"date": "2026-07-24", "v": 1}, self.path)
        self.assertEqual(len(build_site.load_history(self.path)), 1)

    def test_same_date_replaces_not_duplicates(self):
        import build_site
        build_site.append_history({"date": "2026-07-24", "v": 1}, self.path)
        build_site.append_history({"date": "2026-07-24", "v": 2}, self.path)
        recs = build_site.load_history(self.path)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["v"], 2)

    def test_trim_at_max_lines(self):
        import build_site
        for i in range(11):
            build_site.append_history({"date": f"2026-07-{i + 1:02d}", "v": i},
                                      self.path, max_lines=10, keep=5)
        recs = build_site.load_history(self.path)
        self.assertEqual(len(recs), 5)
        self.assertEqual(recs[-1]["v"], 10)

    def test_corrupt_lines_tolerated(self):
        import build_site
        self.path.write_text('{"date": "x", "v": 1}\nnot-json\n\n')
        self.assertEqual(len(build_site.load_history(self.path)), 1)

    def test_load_missing_file(self):
        import build_site
        self.assertEqual(build_site.load_history(self.path), [])

    def test_spark_takes_last_n_non_null(self):
        import build_site
        h = [{"net_liquidity_T": v} for v in (5.0, None, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7)]
        self.assertEqual(build_site.spark(h, "net_liquidity_T", n=7),
                         [5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7])

    def test_write_site_same_day_rebuild_dedupes_today(self):
        """Rebuilding twice on one data date must not double-count today
        in history_days or the sparkline."""
        import tempfile
        from pathlib import Path
        from unittest import mock
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "h.jsonl"
            site = Path(tmp) / "site"
            with mock.patch.object(build_site, "HISTORY_PATH", hist):
                b1 = build_site.write_site(site)
                b2 = build_site.write_site(site)
            self.assertEqual(b2["history_days"], 1)
            self.assertEqual(len(b2["net_liquidity"]["spark_7d"]), 1)
            self.assertEqual(len(build_site.load_history(hist)), 1)


class TestBriefSchema(unittest.TestCase):
    """brief.json is the contract consumed by the us-liquidity-monitor skill."""

    REQUIRED = ("schema_version", "as_of", "generated_utc", "rating",
                "rating_drivers", "headline", "history_days",
                "net_liquidity", "regime", "vitals")

    def brief(self, history=()):
        import build_site
        return build_site.build_brief(build_site.site_snapshot(), list(history))

    def test_required_keys(self):
        b = self.brief()
        for k in self.REQUIRED:
            self.assertIn(k, b)

    def test_rating_is_valid(self):
        self.assertIn(self.brief()["rating"], ("green", "yellow", "red"))

    def test_schema_version(self):
        self.assertEqual(self.brief()["schema_version"], 1)

    def test_spark_capped_at_seven_and_includes_today(self):
        h = [{"net_liquidity_T": 5.0 + i * 0.1} for i in range(10)]
        b = self.brief(h)
        self.assertLessEqual(len(b["net_liquidity"]["spark_7d"]), 7)

    def test_history_days_counts_records(self):
        self.assertEqual(self.brief([{"date": "a"}, {"date": "b"}])["history_days"], 2)

    def test_vitals_keys(self):
        v = self.brief()["vitals"]
        for k in ("vix", "hy_oas_pct", "sofr_iorb_bp", "t10y2y", "breadth_pct_above_50d"):
            self.assertIn(k, v)


if __name__ == "__main__":
    unittest.main()
