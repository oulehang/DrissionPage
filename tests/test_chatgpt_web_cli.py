import unittest

from chatgpt_web_cli import ChatGPTWebClient


class FakeElement:
    def __init__(self, text):
        self.text = text

    def eles(self, *args, **kwargs):
        return []


class FakeChatGPTWebClient(ChatGPTWebClient):
    def __init__(self):
        self.calls = []
        self.last_saved_images = []
        self._seen_image_keys = set()

    def _assistant_count(self):
        self.calls.append("_assistant_count")
        return 0

    def _assistant_texts(self):
        self.calls.append("_assistant_texts")
        return []

    def _current_image_keys(self):
        self.calls.append("_current_image_keys")
        return set()

    def _wait_prompt_box(self):
        self.calls.append("_wait_prompt_box")
        return object()

    def _fill_prompt(self, box, prompt):
        self.calls.append("_fill_prompt")

    def _attach_files(self, files):
        self.calls.append("_attach_files")
        return len(files)

    def _wait_attachments_ready(self, expected_count=0, require_send_ready=True):
        self.calls.append(f"_wait_attachments_ready:{require_send_ready}")

    def _click_send(self):
        self.calls.append("_click_send")

    def _wait_answer(self, before_count, before_image_keys=None, before_answer_texts=None, expect_images=False, progress_callback=None):
        self.calls.append("_wait_answer")
        return "ok"

    def save_latest_images(self):
        self.calls.append("save_latest_images")
        return []


class FakeAlreadyAnsweredClient(FakeChatGPTWebClient):
    def _assistant_count(self):
        self.calls.append("_assistant_count")
        return 1 if "_fill_prompt" in self.calls else 0

    def _wait_attachments_ready(self, expected_count=0, require_send_ready=True):
        self.calls.append(f"_wait_attachments_ready:{require_send_ready}")
        if require_send_ready:
            raise TimeoutError("Timed out waiting for ChatGPT reference image upload to finish.")


class ChatGPTWebClientReferenceImageTests(unittest.TestCase):
    def test_reference_images_finish_uploading_before_prompt_text_is_filled(self):
        bot = FakeChatGPTWebClient()

        self.assertEqual(bot.ask("生成图片", reference_images=["ref.png"]), "ok")

        self.assertLess(bot.calls.index("_attach_files"), bot.calls.index("_fill_prompt"))
        self.assertLess(bot.calls.index("_wait_attachments_ready:False"), bot.calls.index("_fill_prompt"))
        self.assertLess(bot.calls.index("_fill_prompt"), bot.calls.index("_wait_attachments_ready:True"))
        self.assertLess(bot.calls.index("_wait_attachments_ready:True"), bot.calls.index("_click_send"))

    def test_timeout_after_chatgpt_already_answered_does_not_fail_or_resend(self):
        bot = FakeAlreadyAnsweredClient()

        self.assertEqual(bot.ask("生成图片", reference_images=["ref.png"]), "ok")

        self.assertNotIn("_click_send", bot.calls)
        self.assertIn("_wait_answer", bot.calls)

    def test_composer_state_does_not_scan_whole_body_for_upload_busy_text(self):
        js = ChatGPTWebClient._composer_state_js()

        self.assertIn("composerRoots", js)
        self.assertIn("attachmentRoots", js)
        self.assertNotIn("const searchRoots = [root, root?.parentElement, document.body]", js)
        self.assertIn("const uploadText = composerRoots", js)

    def test_wait_answer_returns_new_latest_json_even_when_answer_count_does_not_increase(self):
        bot = ChatGPTWebClient.__new__(ChatGPTWebClient)
        bot.timeout = 1
        bot._assistant_elements = lambda: [FakeElement('{"theme":"新主题","meanings":["卷毛"]}')]
        bot._answer_image_keys = lambda answer: set()
        bot._answer_has_images = lambda answer: False
        bot._is_busy = lambda: False
        bot.save_latest_images = lambda: []

        answer = bot._wait_answer(
            before_count=1,
            before_answer_texts={'{"theme":"旧主题"}'},
        )

        self.assertIn("新主题", answer)


if __name__ == "__main__":
    unittest.main()
