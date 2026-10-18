"""规则与账号保护的离线自测：python -m unittest discover tests"""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import automation as a  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_defaults_are_conservative(self):
        rule = a.validate_rule({})
        self.assertGreaterEqual(rule["interval"], a.MIN_INTERVAL)
        self.assertTrue(1 <= rule["hour_limit"] <= a.MAX_HOUR)
        self.assertTrue(1 <= rule["day_limit"] <= a.MAX_DAY)
        self.assertEqual(rule["publish_mode"], "auto")

    def test_rejects_unsafe_values(self):
        for bad in ({"interval": 60}, {"hour_limit": 0}, {"day_limit": 0}, {"day_limit": 500}, {"publish_mode": "x"}):
            with self.assertRaises(ValueError):
                a.validate_rule(bad)

    def test_migrates_old_unlimited_rules(self):
        rule = a.migrate_rule({"interval": 30, "hour_limit": 0, "day_limit": 0, "tags": "AI"})
        a.validate_rule(rule)
        self.assertEqual(rule["interval"], a.MIN_INTERVAL)
        self.assertEqual(rule["tags"], "AI")

    def test_contains_word_boundary(self):
        self.assertTrue(a.contains("Building AI tools", "AI"))
        self.assertFalse(a.contains("daily notes", "AI"))
        self.assertTrue(a.contains("独立开发者，做AI产品", "独立开发"))

    def test_match_profile(self):
        rule = a.validate_rule({"tags": "AI|人工智能\n创业|founder"})
        ok, _ = a.match_profile({"badge": "blue", "bio": "AI founder"}, rule)
        self.assertTrue(ok)
        self.assertFalse(a.match_profile({"badge": "gold", "bio": "AI founder"}, rule)[0])
        self.assertFalse(a.match_profile({"badge": "blue", "bio": "AI researcher"}, rule)[0])
        rule["exclude"] = "crypto"
        self.assertFalse(a.match_profile({"badge": "blue", "bio": "AI founder, crypto"}, rule)[0])

    def test_window(self):
        rule = a.validate_rule({"start_time": "09:00", "end_time": "23:00"})
        inside = dt.datetime(2026, 1, 1, 12, 0, tzinfo=a.TZ)
        outside = dt.datetime(2026, 1, 1, 23, 30, tzinfo=a.TZ)
        self.assertEqual(a.window_delay(rule, inside), 0)
        self.assertGreater(a.window_delay(rule, outside), 0)

    def test_similarity(self):
        self.assertTrue(a.too_similar("这个观点很有意思，期待后续分享！", ["这个观点很有意思，期待后续分享!"]))
        self.assertFalse(a.too_similar("数据集是公开的吗？想复现一下", ["这个观点很有意思，期待后续分享！"]))

    def test_like_escape(self):
        self.assertEqual(a.like_escape("a_b%"), "a\\_b\\%")


if __name__ == "__main__":
    unittest.main()
