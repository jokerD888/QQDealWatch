import asyncio
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from qqym.app import App
from qqym.config import load_config
from qqym.watchlist import add_item, list_items, remove_item, set_price


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        raw = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
        raw["database"] = str(Path(self.temp.name) / "alerts.db")
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def test_add_price_change_and_remove_preserve_other_settings(self):
        original = json.loads(self.path.read_text(encoding="utf-8"))
        item_id = add_item(self.path, "网易严选 S9 Pro", "S9 Pro， 小蛮腰 S9 Pro", "所有版本", "699.99")
        items = list_items(self.path)
        self.assertEqual(items[-1]["id"], item_id)
        self.assertEqual(items[-1]["aliases"], ["网易严选 S9 Pro", "S9 Pro", "小蛮腰 S9 Pro"])
        self.assertEqual(load_config(self.path).rules[-1].max_price, Decimal("699.99"))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["group_ids"], original["group_ids"])
        set_price(self.path, item_id, None)
        self.assertIsNone(load_config(self.path).rules[-1].max_price)
        remove_item(self.path, item_id)
        self.assertEqual(len(list_items(self.path)), len(original["rules"]))

    def test_running_app_reloads_added_rules(self):
        app = App(load_config(self.path), self.path)
        self.addCleanup(app.storage.close)
        before = len(app.config.rules)
        add_item(self.path, "便携显示器 15.6 英寸", "便携屏", "全新", "")
        app.reload_rules_if_changed()
        self.assertEqual(len(app.config.rules), before + 1)
        self.assertIsNone(app.config.rules[-1].max_price)
        self.assertEqual(app.config.rules[-1].name, "便携显示器 15.6 英寸")

    def test_invalid_price_does_not_change_file(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            add_item(self.path, "商品", "", "", "not-a-price")
        self.assertEqual(self.path.read_bytes(), before)

    def test_delete_last_item_disables_monitoring_and_cancels_pending_alerts(self):
        app = App(load_config(self.path), self.path)
        self.addCleanup(app.storage.close)
        ids = [item["id"] for item in list_items(self.path)]
        app.storage.queue_alert("new", 123, "2TB 固态硬盘 ¥599", "body", True, 3600,
                                rule_id=ids[0])
        app.storage.queue_alert("legacy", 123, "纯牛奶 ¥39", "body", False, 3600)
        for item_id in ids:
            remove_item(self.path, item_id)
        self.assertEqual(list_items(self.path), [])
        app.reload_rules_if_changed()
        self.assertEqual(app.config.rules, ())
        asyncio.run(app.deliver())
        self.assertEqual(app.storage.pending_apps(), [])
        rows = app.storage.db.execute("SELECT app_sent_at, voice_requested, last_error FROM alerts").fetchall()
        self.assertTrue(all(row[0] is not None and row[1] == 0 and row[2] == "关注商品已删除"
                            for row in rows))


if __name__ == "__main__":
    unittest.main()
