# -*- coding: utf-8 -*-
r"""
Control the ChatGPT web page with DrissionPage.

Usage:
    .\.venv\Scripts\python.exe chatgpt_web_cli.py

The first run opens ChatGPT in a Chromium browser. Log in manually if needed,
then enter prompts in this terminal. Type /exit to quit.
"""
from argparse import ArgumentParser
from hashlib import sha256
from json import JSONDecodeError, loads
from datetime import datetime
from pathlib import Path
from time import perf_counter, sleep

from DrissionPage import ChromiumOptions, ChromiumPage


CHATGPT_URL = "https://chatgpt.com/"


PROMPT_SELECTORS = (
    "css:textarea[data-testid='prompt-textarea']",
    "css:#prompt-textarea",
    "css:textarea",
    "css:div[contenteditable='true'][data-testid='prompt-textarea']",
    "css:div[contenteditable='true']",
)

SEND_BUTTON_SELECTORS = (
    "css:button[data-testid='send-button']",
    "css:button[aria-label='Send prompt']",
    "css:button[aria-label='Send message']",
    "css:button[type='submit']",
)

ANSWER_SELECTORS = (
    "css:[data-message-author-role='assistant']",
    "css:div.markdown",
)

BUSY_SELECTORS = (
    "css:button[data-testid='stop-button']",
    "css:button[aria-label='Stop streaming']",
    "css:button[aria-label='Stop generating']",
)

IMAGE_GENERATION_BUSY_TEXTS = (
    "正在创建图片",
    "正在生成图片",
    "生成图片中",
    "创建图片中",
    "creating image",
    "generating image",
)

IMAGE_GENERATION_DONE_TEXTS = (
    "图片已创建",
    "image created",
)


class ChatGPTWebClient:
    def __init__(self, user_data_path="chatgpt_profile", address=None, timeout=180, image_dir="chatgpt_images"):
        if address:
            self.page = ChromiumPage(address)
        else:
            opts = ChromiumOptions()
            opts.set_user_data_path(user_data_path)
            opts.set_timeouts(base=10, page_load=60, script=30)
            self.page = ChromiumPage(opts)
        self.timeout = timeout
        self.image_dir = Path(image_dir)
        self.saved_images = []
        self.last_saved_images = []
        self._seen_image_keys = set()

    def open(self):
        self.page.get(CHATGPT_URL)
        print("Opened ChatGPT. If you are not logged in, finish login in the browser.")
        input("After the chat input box is visible, press Enter here to continue...")

    def ask(self, prompt, expect_images=False, progress_callback=None, reference_images=None):
        before_count = self._assistant_count()
        before_answer_texts = set(self._assistant_texts())
        before_image_keys = self._current_image_keys()
        self._seen_image_keys.update(before_image_keys)
        attached_count = 0
        if reference_images:
            attached_count = self._attach_files(reference_images)
            self._wait_attachments_ready(attached_count, require_send_ready=False)
        box = self._wait_prompt_box()
        self._fill_prompt(box, prompt)
        sent_already = False
        if reference_images:
            try:
                self._wait_attachments_ready(attached_count, require_send_ready=True)
            except TimeoutError:
                sent_already = self._assistant_count() > before_count
                if not sent_already:
                    raise
        if not sent_already:
            self._click_send()
        answer = self._wait_answer(
            before_count,
            before_image_keys,
            before_answer_texts=before_answer_texts,
            expect_images=expect_images,
            progress_callback=progress_callback,
        )
        if expect_images:
            self.last_saved_images = self.save_answer_images(before_count, before_image_keys)
        else:
            self.last_saved_images = self.save_latest_images()
        return answer

    def _wait_prompt_box(self):
        end = perf_counter() + self.timeout
        while perf_counter() < end:
            for selector in PROMPT_SELECTORS:
                ele = self.page.ele(selector, timeout=0.2)
                if ele:
                    return ele
            sleep(0.2)
        raise TimeoutError("Could not find the ChatGPT prompt input box.")

    def _fill_prompt(self, box, prompt):
        box.click()
        try:
            box.clear()
            box.input(prompt)
            return
        except Exception:
            pass

        js = """
        const text = arguments[0];
        const candidates = [
          document.querySelector("textarea[data-testid='prompt-textarea']"),
          document.querySelector("#prompt-textarea"),
          document.querySelector("textarea"),
          document.querySelector("div[contenteditable='true'][data-testid='prompt-textarea']"),
          document.querySelector("div[contenteditable='true']")
        ].filter(Boolean);
        const el = candidates[0];
        if (!el) return false;
        el.focus();
        if (el.tagName === "TEXTAREA") {
          el.value = text;
          el.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: text}));
        } else {
          el.textContent = text;
          el.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: text}));
        }
        return true;
        """
        if not self.page.run_js(js, prompt):
            raise RuntimeError("Failed to fill the prompt input box.")

    def _attach_files(self, files):
        paths = [str(Path(path).absolute()) for path in files if path and Path(path).exists()]
        if not paths:
            return 0

        input_ele = self._file_input()
        if not input_ele:
            raise RuntimeError("Could not find ChatGPT file upload input.")
        input_ele.input(paths)
        return len(paths)

    def _wait_attachments_ready(self, expected_count=0, require_send_ready=True):
        started_at = perf_counter()
        end = perf_counter() + min(90, max(20, self.timeout // 2))
        stable_ready_hits = 0
        while perf_counter() < end:
            state = self._composer_state()
            send_ready = state.get("send_ready")
            upload_busy = state.get("upload_busy")
            attachment_count = int(state.get("attachment_count") or 0)
            enough_time_elapsed = perf_counter() - started_at >= 3
            has_expected = not expected_count or attachment_count >= expected_count or enough_time_elapsed

            can_continue = (send_ready or not require_send_ready) and not upload_busy and has_expected
            if can_continue:
                stable_ready_hits += 1
                if stable_ready_hits >= 3:
                    return
            else:
                stable_ready_hits = 0
            sleep(0.5)

        raise TimeoutError("Timed out waiting for ChatGPT reference image upload to finish.")

    def _composer_state(self):
        try:
            return self.page.run_js(self._composer_state_js()) or {}
        except Exception:
            return {"send_ready": bool(self._send_button()), "upload_busy": False, "attachment_count": 0}

    @staticmethod
    def _composer_state_js():
        return """
        const visible = el => {
          if (!el) return false;
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
        };
        const prompt = document.querySelector("textarea[data-testid='prompt-textarea'], #prompt-textarea, div[contenteditable='true'][data-testid='prompt-textarea'], div[contenteditable='true']");
        const root = prompt ? (prompt.closest("form") || prompt.closest("[data-testid*='composer']") || prompt.parentElement) : document.body;
        const buttons = [...document.querySelectorAll("button[data-testid='send-button'], button[aria-label='Send prompt'], button[aria-label='Send message'], button[type='submit']")].filter(visible);
        const sendButton = buttons[0] || null;
        const sendReady = !!sendButton && !sendButton.disabled && sendButton.getAttribute("aria-disabled") !== "true";
        const composerRoots = [root, root?.parentElement].filter(Boolean);
        const attachmentRoots = composerRoots.length ? composerRoots : [document.body];
        const uploadText = composerRoots.map(el => (el.innerText || "").toLowerCase()).join("\\n");
        const uploadBusyWords = [
          "uploading", "upload failed", "processing", "preparing",
          "上传中", "正在上传", "上传失败", "处理中", "正在处理", "准备中"
        ];
        const uploadBusy = uploadBusyWords.some(word => uploadText.includes(word));
        const attachmentNodes = new Set();
        for (const scope of attachmentRoots) {
          for (const el of scope.querySelectorAll("img, [data-testid*='attachment'], [data-testid*='file'], [aria-label*='附件'], [aria-label*='attachment']")) {
            if (visible(el)) attachmentNodes.add(el);
          }
        }
        return {send_ready: sendReady, upload_busy: uploadBusy, attachment_count: attachmentNodes.size};
        """

    def _file_input(self):
        def candidates():
            inputs = self.page.eles("css:input[type='file']", timeout=0.5)
            image_inputs = []
            for ele in inputs:
                accept = (ele.attr("accept") or "").lower()
                if not accept or "image" in accept or ".png" in accept or ".jpg" in accept or ".jpeg" in accept:
                    image_inputs.append(ele)
            return image_inputs or inputs

        found = candidates()
        if found:
            return found[-1]

        labels = ("上传", "添加", "附加", "Attach", "Upload", "Add photos", "Add files")
        for button in self.page.eles("css:button", timeout=0.5):
            text = " ".join(filter(None, [button.text, button.attr("aria-label"), button.attr("title")]))
            if any(label.lower() in text.lower() for label in labels):
                try:
                    button.click()
                    sleep(0.5)
                    found = candidates()
                    if found:
                        return found[-1]
                except Exception:
                    pass
        return None

    def _click_send(self):
        end = perf_counter() + 10
        while perf_counter() < end:
            button = self._send_button()
            if button:
                try:
                    button.click()
                    return
                except Exception:
                    pass
            sleep(0.2)

        # Keyboard fallback: focused prompt box + Enter.
        self.page.actions.key_down("ENTER")

    def _send_button(self):
        for selector in SEND_BUTTON_SELECTORS:
            button = self.page.ele(selector, timeout=0.2)
            if not button:
                continue
            try:
                disabled = (
                    button.attr("disabled") is not None
                    or button.attr("aria-disabled") == "true"
                    or bool(button.property("disabled"))
                )
                if disabled:
                    continue
            except Exception:
                pass
            return button
        return None

    def _wait_answer(self, before_count, before_image_keys=None, before_answer_texts=None, expect_images=False, progress_callback=None):
        before_image_keys = before_image_keys or set()
        before_answer_texts = before_answer_texts or set()
        end = perf_counter() + self.timeout
        last_text = ""
        stable_hits = 0
        last_image_keys = set()
        image_stable_hits = 0
        image_visual_signatures = {}
        last_progress_at = 0

        while perf_counter() < end:
            answers = self._assistant_elements()
            current_answer = self._target_answer(answers, before_count)
            if not current_answer and answers:
                latest_text = (answers[-1].text or "").strip()
                if latest_text and latest_text not in before_answer_texts:
                    current_answer = answers[-1]
            new_image_keys = self._answer_image_keys(current_answer) - before_image_keys if current_answer else set()
            latest_answer = current_answer
            use_page_images = False
            if expect_images and not new_image_keys:
                first_new_image = self._first_new_page_image(before_image_keys)
                new_image_keys = {self._image_key(first_new_image)} if first_new_image else set()
                use_page_images = True
            elif not expect_images and latest_answer and not new_image_keys and self._answer_mentions_image_generation(latest_answer):
                new_image_keys = self._current_image_keys() - before_image_keys
                use_page_images = True
            if new_image_keys:
                if new_image_keys == last_image_keys:
                    image_stable_hits += 1
                else:
                    last_image_keys = set(new_image_keys)
                    image_stable_hits = 1

                if self._new_images_ready(
                    before_image_keys,
                    image_stable_hits,
                    use_page_images,
                    expect_images,
                    before_count,
                    image_visual_signatures,
                ):
                    return "[image answer ready]"

            if expect_images and progress_callback and perf_counter() - last_progress_at >= 5:
                last_progress_at = perf_counter()
                try:
                    progress_callback(self._image_wait_status(
                        before_image_keys,
                        before_count,
                        use_page_images,
                        image_stable_hits,
                        image_visual_signatures,
                    ))
                except Exception:
                    pass

            if current_answer:
                text = current_answer.text.strip() if current_answer else ""
                has_images = self._answer_has_images(current_answer) if current_answer else False
                text_stable = text and text == last_text and not new_image_keys
                if text_stable and self._looks_like_complete_json(text):
                    stable_hits += 1
                    if stable_hits >= 2:
                        return text
                elif (text or has_images) and not new_image_keys and text == last_text and not self._is_busy():
                    stable_hits += 1
                    if stable_hits >= 2:
                        return text
                else:
                    stable_hits = 0
                    last_text = text
            sleep(0.8)

        if last_text:
            return last_text
        saved = self.save_latest_images()
        if saved:
            return f"[image answer saved: {len(saved)} file(s)]"
        raise TimeoutError("Timed out waiting for ChatGPT answer.")

    def _image_wait_status(self, before_image_keys, before_count, use_page_images, stable_hits, visual_signatures):
        images = self._target_page_images(before_image_keys) if use_page_images else self._target_answer_images(before_count)
        new_images = [img for img in images if self._image_key(img) not in before_image_keys]
        if not new_images:
            first_new_image = self._first_new_page_image(before_image_keys)
            if first_new_image:
                images = [first_new_image]
                new_images = images

        if not new_images:
            return "未发现当前轮新图片，继续等待"

        complete_count = sum(1 for img in new_images if self._image_is_complete(img))
        loading_count = sum(1 for img in new_images if self._image_has_local_loading_state(img))
        visual_hits = []
        for img in new_images:
            key = self._image_key(img)
            _, hits = visual_signatures.get(key, (None, 0)) if key else (None, 0)
            visual_hits.append(str(hits))
        return (
            f"检测到当前轮图片 {len(new_images)} 张，"
            f"加载完成 {complete_count}/{len(new_images)}，"
            f"加载控件 {loading_count}，"
            f"图片节点稳定 {stable_hits} 次，"
            f"视觉稳定 {','.join(visual_hits) or '0'} 次"
        )

    def _assistant_elements(self):
        for selector in ANSWER_SELECTORS:
            eles = self.page.eles(selector, timeout=0.5)
            if eles:
                return eles
        return []

    def _assistant_count(self):
        return len(self._assistant_elements())

    def _assistant_texts(self):
        return [(item.text or "").strip() for item in self._assistant_elements()]

    def _is_busy(self):
        for selector in BUSY_SELECTORS:
            if self.page.ele(selector, timeout=0.1):
                return True
        return False

    def _looks_like_complete_json(self, text):
        text = (text or "").strip()
        if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
            return False
        try:
            loads(text)
            return True
        except JSONDecodeError:
            return False

    def _target_answer(self, answers, before_count):
        if len(answers) <= before_count:
            return None
        return answers[before_count]

    def _new_images_ready(
        self,
        before_image_keys,
        stable_hits,
        use_page_images=False,
        expect_images=False,
        before_count=0,
        visual_signatures=None,
    ):
        if not expect_images and (self._is_busy() or self._page_has_image_generation_busy_text()):
            return False

        images = self._target_page_images(before_image_keys) if use_page_images else self._target_answer_images(before_count)
        new_images = [img for img in images if self._image_key(img) not in before_image_keys]
        if not new_images:
            return False
        if not all(self._image_is_complete(img) for img in new_images):
            return False
        if any(self._image_has_local_loading_state(img) for img in new_images):
            return False

        # Prefer ChatGPT's explicit completion label when it is present. Some
        # layouts omit it, so fall back to several stable polls after busy UI
        # and busy text have disappeared.
        stable_required = 3 if self._page_has_image_generation_done_text() else 5
        return stable_hits >= stable_required and self._images_visually_stable(new_images, visual_signatures)

    def _answer_images(self):
        answers = self._assistant_elements()
        images = []
        if answers:
            images.extend(answers[-1].eles("css:img", timeout=0.2))

        # ChatGPT may render generated images outside the final markdown node.
        images.extend(self.page.eles("css:img", timeout=0.2))

        result = []
        seen = set()
        for img in images:
            if not self._looks_like_answer_image(img):
                continue
            key = self._image_key(img)
            if key and key not in seen:
                seen.add(key)
                result.append(img)
        return result

    def _latest_answer_images(self):
        answers = self._assistant_elements()
        if not answers:
            return []
        return self._element_images(answers[-1])

    def _target_answer_images(self, before_count):
        answers = self._assistant_elements()
        answer = self._target_answer(answers, before_count)
        if not answer:
            return []
        return self._element_images(answer)

    def _target_page_images(self, before_image_keys):
        img = self._first_new_page_image(before_image_keys)
        return [img] if img else []

    def _first_new_page_image(self, before_image_keys):
        for img in self.page.eles("css:img", timeout=0.2):
            if not self._looks_like_answer_image(img):
                continue
            key = self._image_key(img)
            if key and key not in before_image_keys and key not in self._seen_image_keys:
                return img
        return None

    def _answer_image_keys(self, answer):
        if not answer:
            return set()
        return {key for key in (self._image_key(img) for img in self._element_images(answer)) if key}

    def _element_images(self, element):
        result = []
        seen = set()
        for img in element.eles("css:img", timeout=0.2):
            if not self._looks_like_answer_image(img):
                continue
            key = self._image_key(img)
            if key and key not in seen:
                seen.add(key)
                result.append(img)
        return result

    def _image_key(self, img):
        try:
            return img.attr("src") or img.property("currentSrc") or img.css_path
        except Exception:
            return None

    def _image_is_complete(self, img):
        try:
            return bool(self.page.run_js(
                "const img = arguments[0];"
                "return !!img && img.complete && img.naturalWidth >= 128 && img.naturalHeight >= 128;",
                img,
            ))
        except Exception:
            try:
                return int(img.property("naturalWidth") or 0) >= 128 and int(img.property("naturalHeight") or 0) >= 128
            except Exception:
                return False

    def _image_has_local_loading_state(self, img):
        js = """
        const img = arguments[0];
        const busyTexts = arguments[1];
        if (!img) return true;

        function visible(el) {
          if (!el || !el.getClientRects || !el.getClientRects().length) return false;
          const style = getComputedStyle(el);
          return style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity || 1) !== 0;
        }

        let root = img;
        for (let i = 0; i < 8 && root && root.parentElement; i++) {
          root = root.parentElement;
          const text = (root.innerText || "").toLowerCase();
          if (busyTexts.some(t => t && text.includes(t))) return true;
          if (root.matches?.('[aria-busy="true"], [data-state="loading"], [data-loading="true"]')) return true;
          const loading = [...root.querySelectorAll(
            '[aria-busy="true"], [role="progressbar"], [data-state="loading"], [data-loading="true"], .animate-spin, .animate-pulse, [class*="spinner"], [class*="loading"]'
          )].some(visible);
          if (loading) return true;

          // The ChatGPT image card shows a small options/loading control near the image.
          // Treat it as busy only while the surrounding card also exposes a loading signal;
          // a completed card may keep a normal conversation-options button.
          const optionButtons = [...root.querySelectorAll('button[data-testid="conversation-options-button"], button[aria-label*="对话选项"], button[aria-label*="conversation options"]')].filter(visible);
          const hasAnimatedSvg = optionButtons.some(btn =>
            !!btn.closest('[class*="animate"], [class*="loading"], [aria-busy="true"], [data-state="loading"]') ||
            [...btn.querySelectorAll('svg, use')].some(node => {
              const cls = String(node.getAttribute('class') || '');
              return /animate|spin|loading/i.test(cls);
            })
          );
          if (hasAnimatedSvg) return true;
        }
        return false;
        """
        try:
            return bool(self.page.run_js(js, img, list(IMAGE_GENERATION_BUSY_TEXTS)))
        except Exception:
            return False

    def _images_visually_stable(self, images, visual_signatures):
        if visual_signatures is None:
            return True
        all_stable = True
        for img in images:
            key = self._image_key(img)
            if not key:
                return False
            signature = self._image_visual_signature(img)
            if not signature:
                return False
            previous, hits = visual_signatures.get(key, (None, 0))
            hits = hits + 1 if signature == previous else 1
            visual_signatures[key] = (signature, hits)
            if hits < 3:
                all_stable = False
        return all_stable

    def _image_visual_signature(self, img):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        temp_dir = self.image_dir / ".wait_checks"
        temp_dir.mkdir(parents=True, exist_ok=True)
        name = f"wait_{stamp}.png"
        path = None
        try:
            path = Path(img.get_screenshot(path=temp_dir, name=name))
            data = path.read_bytes()
            return sha256(data).hexdigest()
        except Exception:
            try:
                rect = img.rect
                key = self._image_key(img) or ""
                return f"{key}|{rect.size}|{img.property('naturalWidth')}x{img.property('naturalHeight')}"
            except Exception:
                return None
        finally:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _page_has_image_generation_busy_text(self):
        text = self._page_text().lower()
        return any(item in text for item in IMAGE_GENERATION_BUSY_TEXTS)

    def _page_has_image_generation_done_text(self):
        text = self._page_text().lower()
        return any(item in text for item in IMAGE_GENERATION_DONE_TEXTS)

    def _page_text(self):
        try:
            return self.page.run_js("return document.body ? document.body.innerText : '';") or ""
        except Exception:
            return ""

    def save_latest_images(self):
        saved = []
        for index, img in enumerate(self._answer_images(), start=1):
            if not self._image_is_complete(img):
                continue
            key = self._image_key(img)
            if not key or key in self._seen_image_keys:
                continue

            self._seen_image_keys.add(key)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"chatgpt_image_{stamp}_{len(self.saved_images) + index}.png"
            try:
                path = img.save(path=self.image_dir, name=name, timeout=20)
            except Exception:
                try:
                    path = img.get_screenshot(path=self.image_dir, name=name)
                except Exception as e:
                    print(f"Image save failed: {e}")
                    continue

            self.saved_images.append(path)
            saved.append(path)
            print(f"Saved image: {path}")
        return saved

    def save_answer_images(self, before_count, before_image_keys=None):
        before_image_keys = before_image_keys or set()
        saved = []
        images = self._target_answer_images(before_count)
        if not images:
            images = self._target_page_images(before_image_keys)
        for index, img in enumerate(images, start=1):
            if not self._image_is_complete(img):
                continue
            key = self._image_key(img)
            if not key or key in before_image_keys or key in self._seen_image_keys:
                continue

            self._seen_image_keys.add(key)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"chatgpt_image_{stamp}_{len(self.saved_images) + index}.png"
            try:
                path = img.save(path=self.image_dir, name=name, timeout=20)
            except Exception:
                try:
                    path = img.get_screenshot(path=self.image_dir, name=name)
                except Exception as e:
                    print(f"Image save failed: {e}")
                    continue

            self.saved_images.append(path)
            saved.append(path)
            print(f"Saved image: {path}")
        return saved

    def _current_image_keys(self):
        keys = set()
        for img in self.page.eles("css:img", timeout=0.2):
            if not self._looks_like_answer_image(img):
                continue
            key = img.attr("src") or img.property("currentSrc") or img.css_path
            if key:
                keys.add(key)
        return keys

    def _answer_has_images(self, answer):
        return any(self._looks_like_answer_image(img) for img in answer.eles("css:img", timeout=0.2))

    def _answer_mentions_image_generation(self, answer):
        try:
            text = (answer.text or "").lower()
        except Exception:
            return False
        return any(item in text for item in IMAGE_GENERATION_BUSY_TEXTS + IMAGE_GENERATION_DONE_TEXTS)

    def _looks_like_answer_image(self, img):
        try:
            width = int(img.property("naturalWidth") or 0)
            height = int(img.property("naturalHeight") or 0)
            src = img.attr("src") or img.property("currentSrc") or ""
            alt = (img.attr("alt") or "").lower()
        except Exception:
            return False

        if src.startswith("data:image/svg"):
            return False
        if width >= 256 and height >= 256:
            return True
        return "generated" in alt and width >= 128 and height >= 128

    def print_image_summary(self):
        if not self.saved_images:
            print("\nNo images were saved in this session.")
            return

        print("\nSaved images in this session:")
        for path in self.saved_images:
            print(f"- {path}")


def main():
    parser = ArgumentParser(description="Control ChatGPT web page with DrissionPage.")
    parser.add_argument("--address", help="Connect to an existing Chrome debug address, for example 127.0.0.1:9222.")
    parser.add_argument("--user-data-path", default="chatgpt_profile",
                        help="Persistent browser profile path used when --address is not set.")
    parser.add_argument("--image-dir", default="chatgpt_images", help="Directory for images found in answers.")
    parser.add_argument("--timeout", type=int, default=180, help="Answer wait timeout in seconds.")
    args = parser.parse_args()

    bot = ChatGPTWebClient(
        user_data_path=args.user_data_path,
        address=args.address,
        timeout=args.timeout,
        image_dir=args.image_dir,
    )
    bot.open()

    try:
        while True:
            prompt = input("\nYou> ").strip()
            if not prompt:
                continue
            if prompt.lower() in {"/exit", "/quit", "exit", "quit"}:
                break
            if prompt.lower() == "/save":
                saved = bot.save_latest_images()
                print(f"\nSaved {len(saved)} new image(s).")
                continue

            try:
                answer = bot.ask(prompt)
                print(f"\nChatGPT>\n{answer}")
            except Exception as e:
                print(f"\nError: {e}")
    finally:
        bot.print_image_summary()


if __name__ == "__main__":
    main()
