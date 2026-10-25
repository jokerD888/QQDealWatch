import os
import unittest
from unittest.mock import patch

from qqym.config import Rule
from qqym.core import Message
from qqym.services import analyze


class ServiceTests(unittest.TestCase):
    def test_local_glm_uses_existing_projects_endpoint_without_key(self):
        message = Message(123, "1", 5, "网易严选 S9 Pro 有活动", (), ())
        rule = Rule("chair", "网易严选 S9 Pro", ("S9 Pro",), "", None, None, False)
        with patch.dict(os.environ, {"AI_API_URL": "http://127.0.0.1:8080/v1/chat/completions",
                                     "AI_MODEL": "glm-5.3-flash"}, clear=True), \
             patch("qqym.services.post_json", return_value={
                 "choices": [{"message": {"content": '{"matches": []}'}}]
             }) as request:
            self.assertEqual(analyze(message, (rule,)), {"matches": []})
        args = request.call_args.args
        self.assertEqual(args[0], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertEqual(args[1]["model"], "glm-5.3-flash")
        self.assertEqual(args[1]["messages"][1]["content"][:1], "{")
        self.assertEqual(args[2], {})


if __name__ == "__main__":
    unittest.main()
