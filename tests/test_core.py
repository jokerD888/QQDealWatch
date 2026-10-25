import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from qqym.config import Rule, load_config
from qqym.core import alert_key, candidate_rules, evaluate_result, parse_onebot
from qqym.storage import Storage


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
        raw["group_ids"] = [123]
        raw["voice"]["enabled"] = True
        self.path = Path(self.tmp.name) / "config.json"
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.config = load_config(self.path)

    def event(self, text="2TB SSD 到手 599 元 https://example.com/deal", mid=1):
        return {"post_type": "message", "message_type": "group", "group_id": 123,
                "user_id": 5, "message_id": mid,
                "message": [{"type": "text", "data": {"text": text}}]}

    def test_group_filter_and_message(self):
        event = self.event()
        self.assertIsNone(parse_onebot({**event, "group_id": 999}, self.config.group_ids))
        msg = parse_onebot(event, self.config.group_ids)
        self.assertEqual(msg.source_urls, ("https://example.com/deal",))
        self.assertEqual(candidate_rules(msg, self.config.rules)[0].id, "ssd-2tb")

    def test_price_and_voice_are_conservative(self):
        msg = parse_onebot(self.event(), self.config.group_ids)
        candidates = candidate_rules(msg, self.config.rules)
        result = {"matches": [{"rule_id": "ssd-2tb", "match": True, "price": 599,
                               "price_basis": "final", "conditions": "无", "evidence": "到手 599 元"}]}
        matches = evaluate_result(result, candidates, self.config)
        self.assertEqual(matches[0].price, Decimal("599"))
        self.assertTrue(matches[0].voice)
        self.assertEqual(len(evaluate_result({"matches": [{**result["matches"][0], "price": 700}]},
                                             candidates, self.config)), 0)
        ambiguous = {"matches": [{**result["matches"][0], "price_basis": "conditional"}]}
        self.assertEqual(evaluate_result(ambiguous, candidates, self.config), [])
        no_price_evidence = {"matches": [{**result["matches"][0], "evidence": "好价"}]}
        self.assertFalse(evaluate_result(no_price_evidence, candidates, self.config)[0].voice)

    def test_dedupe_and_message_replay(self):
        msg = parse_onebot(self.event(), self.config.group_ids)
        match = evaluate_result({"matches": [{"rule_id": "ssd-2tb", "match": True,
            "price": 599, "price_basis": "final", "evidence": "599 元"}]},
            candidate_rules(msg, self.config.rules), self.config)[0]
        storage = Storage(Path(self.tmp.name) / "test.db")
        self.assertTrue(storage.first_seen(msg.group_id, msg.message_id))
        self.assertFalse(storage.first_seen(msg.group_id, msg.message_id))
        key = alert_key(msg, match)
        self.assertTrue(storage.queue_alert(key, msg.group_id, "title", "body", True, 3600))
        self.assertFalse(storage.queue_alert(key, msg.group_id, "title", "body", True, 3600))
        self.assertEqual(len(storage.pending_apps()), 1)
        storage.close()

    def test_s9_pro_alias_accepts_model_spacing(self):
        event = self.event("网易严选小蛮腰 S9Pro 椅子有活动")
        message = parse_onebot(event, self.config.group_ids)
        rule = Rule("chair", "网易严选 S9 Pro", ("S9 Pro",), "人体工学椅", None, None, False)
        self.assertEqual(candidate_rules(message, (rule,))[0].id, "chair")


if __name__ == "__main__":
    unittest.main()
