import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config_json


class TestConfigJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)  # start from "doesn't exist"

    def tearDown(self):
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_ensure_config_exists_creates_defaults(self):
        self.assertFalse(os.path.exists(self.tmp.name))
        config_json.ensure_config_exists(self.tmp.name)
        self.assertTrue(os.path.exists(self.tmp.name))
        rows = config_json.load_raw_rows(self.tmp.name)
        self.assertGreater(len(rows), 0)

    def test_save_and_load_roundtrip(self):
        rows = [
            {"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/a",
             "display_type": "AUTO", "title_override": "", "time_range": "AUTO", "notes": ""},
            {"enabled": False, "platform": "Kalshi", "url": "https://kalshi.com/events/B",
             "display_type": "TABLE", "title_override": "Custom", "time_range": "7D", "notes": "hi"},
        ]
        config_json.save_raw_rows(self.tmp.name, rows)
        loaded = config_json.load_raw_rows(self.tmp.name)
        self.assertEqual(loaded, rows)

    def test_read_config_order_matches_list_order(self):
        rows = [
            {"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/third"},
            {"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/first"},
            {"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/second"},
        ]
        config_json.save_raw_rows(self.tmp.name, rows)
        result = config_json.read_config(self.tmp.name)
        urls = [r.url for r in result.rows]
        self.assertEqual(urls, [
            "https://polymarket.com/event/third",
            "https://polymarket.com/event/first",
            "https://polymarket.com/event/second",
        ])

    def test_missing_url_skipped_with_issue(self):
        rows = [{"enabled": True, "platform": "Polymarket", "url": ""}]
        config_json.save_raw_rows(self.tmp.name, rows)
        result = config_json.read_config(self.tmp.name)
        self.assertEqual(len(result.rows), 0)
        self.assertEqual(len(result.issues), 1)
        self.assertIn("Missing URL", result.issues[0].message)

    def test_invalid_platform_skipped(self):
        rows = [{"enabled": True, "platform": "Betfair", "url": "https://example.com/x"}]
        config_json.save_raw_rows(self.tmp.name, rows)
        result = config_json.read_config(self.tmp.name)
        self.assertEqual(len(result.rows), 0)
        self.assertTrue(any("Unrecognized Platform" in i.message for i in result.issues))

    def test_invalid_time_range_defaults_to_auto(self):
        rows = [{"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/a",
                  "time_range": "LAST_WEEK"}]
        config_json.save_raw_rows(self.tmp.name, rows)
        result = config_json.read_config(self.tmp.name)
        self.assertEqual(result.rows[0].time_range, "AUTO")
        self.assertTrue(any(i.severity == "warning" for i in result.issues))

    def test_duplicate_url_flagged_but_kept(self):
        rows = [
            {"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/a"},
            {"enabled": True, "platform": "Polymarket", "url": "https://polymarket.com/event/a"},
        ]
        config_json.save_raw_rows(self.tmp.name, rows)
        result = config_json.read_config(self.tmp.name)
        self.assertEqual(len(result.rows), 2)
        self.assertTrue(any("Duplicate URL" in i.message for i in result.issues))

    def test_enabled_defaults_false_if_missing(self):
        rows = [{"platform": "Polymarket", "url": "https://polymarket.com/event/a"}]
        config_json.save_raw_rows(self.tmp.name, rows)
        result = config_json.read_config(self.tmp.name)
        self.assertFalse(result.rows[0].enabled)


if __name__ == "__main__":
    unittest.main()
