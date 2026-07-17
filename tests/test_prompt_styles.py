import unittest

from chatgpt_image_web_app import (
    INDEX_HTML,
    SPRITE_SHEET_PROMPT,
    apply_prompt_style_to_prompt,
    build_copy_prompt,
    normalize_prompt_style,
    reference_style_note,
)


class PromptStyleTests(unittest.TestCase):
    def test_unknown_prompt_style_falls_back_to_standard(self):
        self.assertEqual(normalize_prompt_style("missing-style"), "standard")

    def test_no_text_style_adds_image_text_ban(self):
        prompt = apply_prompt_style_to_prompt("表情图：根据主题「{theme}」生成。", "no_text")

        self.assertIn("提示词风格：无文字版", prompt)
        self.assertIn("不要任何文字", prompt)
        self.assertIn("字母", prompt)
        self.assertIn("数字", prompt)

    def test_no_text_style_removes_sticker_text_requirements(self):
        prompt = apply_prompt_style_to_prompt(SPRITE_SHEET_PROMPT, "no_text")

        self.assertNotIn("必须包含对应含义词的1-4个中文大字短梗文案", prompt)
        self.assertNotIn("必须包含1-4个中文大字短梗文案", prompt)
        self.assertIn("不要任何文字", prompt)

    def test_copy_prompt_includes_selected_style_note(self):
        prompt = build_copy_prompt("小狗早安问候", "no_text")

        self.assertIn("小狗早安问候", prompt)
        self.assertIn("提示词风格：无文字版", prompt)
        self.assertIn("图片不放字", prompt)

    def test_default_sticker_prompt_asks_for_diverse_non_ai_style(self):
        self.assertIn("风格多样化", SPRITE_SHEET_PROMPT)
        self.assertIn("拒绝AI感", SPRITE_SHEET_PROMPT)
        self.assertIn("避免塑料感", SPRITE_SHEET_PROMPT)

    def test_default_sticker_prompt_is_concise_image_generation_request(self):
        self.assertTrue(SPRITE_SHEET_PROMPT.startswith("请直接生成一张图片"))
        self.assertLess(len(SPRITE_SHEET_PROMPT), 520)
        self.assertIn("不要回复文字说明", SPRITE_SHEET_PROMPT)

    def test_prompt_style_note_is_not_duplicated_when_already_present(self):
        prompt = "请直接生成一张图片。\n提示词风格：无文字版。图片不要任何文字。"
        styled = apply_prompt_style_to_prompt(prompt, "no_text")

        self.assertEqual(styled.count("提示词风格：无文字版"), 1)

    def test_page_updates_sticker_prompt_when_prompt_style_changes(self):
        self.assertIn('promptStyle").addEventListener("change"', INDEX_HTML)
        self.assertIn("function stickerPromptForStyle", INDEX_HTML)
        self.assertIn("removeStickerTextRequirements", INDEX_HTML)

    def test_page_has_dashboard_layout_and_reference_previews(self):
        self.assertIn('class="app-layout"', INDEX_HTML)
        self.assertIn('class="side-column"', INDEX_HTML)
        self.assertIn('class="control-stack"', INDEX_HTML)
        self.assertIn('class="task-actions"', INDEX_HTML)
        self.assertIn('class="reference-controls"', INDEX_HTML)
        self.assertIn('class="history-section"', INDEX_HTML)
        self.assertIn("overflow-x: hidden", INDEX_HTML)
        self.assertIn("URL.createObjectURL(file)", INDEX_HTML)
        self.assertIn("reference-thumb", INDEX_HTML)
        self.assertLess(INDEX_HTML.index('class="history-section"'), INDEX_HTML.index('class="side-column"'))

    def test_page_has_reference_mode_selector(self):
        self.assertIn('id="referenceMode"', INDEX_HTML)
        self.assertIn('value="borrow"', INDEX_HTML)
        self.assertIn('value="replicate"', INDEX_HTML)
        self.assertIn("referenceMode", INDEX_HTML)

    def test_reference_mode_can_replicate_reference_subject_and_style(self):
        borrow_note = reference_style_note(["ref.png"], "borrow")
        replicate_note = reference_style_note(["ref.png"], "replicate")

        self.assertIn("只借鉴", borrow_note)
        self.assertIn("不复制", borrow_note)
        self.assertIn("复刻参考图", replicate_note)
        self.assertIn("人物", replicate_note)
        self.assertIn("主体", replicate_note)


if __name__ == "__main__":
    unittest.main()
