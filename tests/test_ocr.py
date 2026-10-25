import unittest

from qqym.ocr import _secure_qq_url


class OcrUrlTests(unittest.TestCase):
    def test_snowluma_http_qq_image_is_upgraded_to_https(self):
        self.assertEqual(
            _secure_qq_url("http://gchat.qpic.cn/gchatpic_new/abc?x=1"),
            "https://gchat.qpic.cn/gchatpic_new/abc?x=1",
        )

    def test_untrusted_image_host_is_rejected(self):
        for url in ("http://qpic.cn.evil.test/a", "https://example.com/a",
                    "file:///C:/secret.png"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _secure_qq_url(url)


if __name__ == "__main__":
    unittest.main()
