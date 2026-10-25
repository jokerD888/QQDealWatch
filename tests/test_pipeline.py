import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qqym.app import App
from qqym.config import load_config


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_only_message_uses_local_ocr_before_glm(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
            raw["group_ids"] = [123]
            raw["database"] = str(Path(temp) / "alerts.db")
            raw["dry_run"] = True
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            app = App(load_config(path))
            event = {"post_type": "message", "message_type": "group", "group_id": 123,
                     "message_id": 100, "user_id": 5,
                     "message": [{"type": "image", "data": {
                         "url": "https://gchat.qpic.cn/example.jpg"}}]}
            seen = []
            def fake_analyze(message, rules):
                seen.append((message.text, [rule.id for rule in rules]))
                return {"matches": []}
            with patch.dict("os.environ", {"AI_VISION_MODEL": ""}), \
                 patch("qqym.app.read_image_text", return_value="2TB SSD 到手 599 元"), \
                 patch("qqym.app.analyze", side_effect=fake_analyze):
                await app.process(event)
            self.assertEqual(len(seen), 1)
            self.assertIn("2TB SSD 到手 599 元", seen[0][0])
            self.assertIn("ssd-2tb", seen[0][1])
            app.storage.close()

    async def test_message_to_app_and_voice_with_replay_dedupe(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
            raw["group_ids"] = [123]
            raw["voice"]["enabled"] = True
            raw["voice"]["allowed_hours"] = [0, 24]
            raw["dry_run"] = False
            raw["database"] = str(Path(temp) / "alerts.db")
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            app = App(load_config(path))
            event = {"post_type": "message", "message_type": "group", "group_id": 123,
                     "message_id": 99, "user_id": 5,
                     "message": [{"type": "text", "data": {"text": "2TB SSD 现价 599 元"}}]}
            ai = {"matches": [{"rule_id": "ssd-2tb", "match": True, "price": 599,
                               "price_basis": "final", "evidence": "现价 599 元", "conditions": "无"}]}
            calls = []
            with patch("qqym.app.analyze", return_value=ai), \
                 patch("qqym.app.pushplus", side_effect=lambda *x: calls.append(x)):
                await app.process(event)
                await app.process(event)
                await app.deliver()
                await app.deliver()
            self.assertEqual([x[2] for x in calls], ["app", "voice"])
            app.storage.close()

    async def test_followup_price_uses_recent_same_sender_context(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
            raw["group_ids"] = [123]
            raw["database"] = str(Path(temp) / "alerts.db")
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            app = App(load_config(path))
            def event(mid, text):
                return {"post_type": "message", "message_type": "group", "group_id": 123,
                        "message_id": mid, "user_id": 5,
                        "message": [{"type": "text", "data": {"text": text}}]}
            seen = []
            def fake_analyze(message, rules):
                seen.append(message.text)
                return {"matches": []}
            with patch("qqym.app.analyze", side_effect=fake_analyze):
                await app.process(event(1, "2TB SSD 有活动"))
                await app.process(event(2, "到手 599 元"))
            self.assertIn("2TB SSD 有活动", seen[1])
            self.assertIn("到手 599 元", seen[1])
            app.storage.close()


if __name__ == "__main__":
    unittest.main()
