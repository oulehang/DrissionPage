import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import chatgpt_image_web_app as app


class FakePage:
    def get(self, url):
        return None


class FakeBot:
    def __init__(self):
        self.page = FakePage()
        self.calls = []
        self.last_saved_images = []

    def ask(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        if len(self.calls) == 1:
            return '{"name":"发型狗","intro":"小狗发型变化","copyright":"哈哈航","type":"静态表情","character":"小狗发型","style_tags":["日常"],"theme_tags":["万能通用"],"price":"免费","download_region":"中国大陆","sticker_meanings":["卷毛"],"reward_enabled":true,"reward_guide_text":"喜欢就赞赏"}'
        self.last_saved_images = ["sprite.png"]
        return "[image answer ready]"


class RunBatchReferenceImageTests(unittest.TestCase):
    def test_reference_images_are_only_sent_for_copy_generation(self):
        bot = FakeBot()
        with TemporaryDirectory() as tmp:
            ref1 = Path(tmp) / "ref1.png"
            ref2 = Path(tmp) / "ref2.png"
            ref1.write_bytes(b"x")
            ref2.write_bytes(b"y")

            with patch.object(app, "ensure_bot", return_value=bot), \
                 patch.object(app, "sleep", return_value=None), \
                 patch.object(app, "split_sprite_sheet", return_value=[]):
                app.STATE.config = {"image_dir": "chatgpt_images"}
                app.run_batch(
                    "小狗的各类发型",
                    templates=[],
                    mode="sprite24",
                    reference_images=[str(ref1), str(ref2)],
                )

            expected_refs = [str(ref1.resolve()), str(ref2.resolve())]
        self.assertEqual(bot.calls[0]["kwargs"].get("reference_images"), expected_refs)
        image_calls = bot.calls[1:]
        self.assertTrue(image_calls)
        self.assertTrue(all("reference_images" not in call["kwargs"] for call in image_calls))
        self.assertNotIn("已上传参考图", image_calls[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
