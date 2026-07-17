import unittest

from chatgpt_image_web_app import build_hot_theme_prompt


class HotThemePromptTests(unittest.TestCase):
    def test_hot_theme_prompt_preserves_concrete_user_subject(self):
        prompt = build_hot_theme_prompt(
            "小狗的各类发型",
            reference_images=["ref.png"],
            today="2026-06-17",
        )

        self.assertIn("用户当前关键词：小狗的各类发型", prompt)
        self.assertIn("必须保留用户关键词里的核心主体和限定词", prompt)
        self.assertIn("小狗", prompt)
        self.assertIn("发型", prompt)
        self.assertIn("参考图只用于借鉴画风", prompt)

    def test_hot_theme_prompt_tells_variation_requests_to_use_variation_meanings(self):
        prompt = build_hot_theme_prompt("小狗的各类发型", today="2026-06-17")

        self.assertIn("各类发型", prompt)
        self.assertIn("meanings 应优先写成具体发型/造型标签", prompt)
        self.assertIn("卷毛", prompt)
        self.assertIn("中分", prompt)
        self.assertIn("主题不能变成泛泛的宠物问候", prompt)
    def test_hot_theme_prompt_can_replicate_reference_style(self):
        prompt = build_hot_theme_prompt(
            "小狗的各类发型",
            reference_images=["ref.png"],
            reference_mode="replicate",
            today="2026-06-17",
        )

        self.assertIn("复刻参考图", prompt)
        self.assertIn("人物", prompt)
        self.assertIn("主体", prompt)


if __name__ == "__main__":
    unittest.main()
