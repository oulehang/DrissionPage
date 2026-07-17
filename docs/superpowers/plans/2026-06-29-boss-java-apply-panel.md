# BOSS Java Apply Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local web control panel for the BOSS Zhipin Java application helper.

**Architecture:** Keep the real browser automation in `DrissionPage/boss_java_apply.py`, add a small reusable runner/config surface, and create `boss_java_apply_panel.py` as a standard-library HTTP server with JSON APIs and embedded HTML. The panel starts one background task at a time, exposes status/log polling, and keeps manual confirmation as the default behavior.

**Tech Stack:** Python 3.12, `ThreadingHTTPServer`, `unittest`, DrissionPage `ChromiumPage`.

---

### Task 1: Panel State And HTML Tests

**Files:**
- Create: `tests/test_boss_java_apply_panel.py`
- Create: `boss_java_apply_panel.py`

- [ ] **Step 1: Write the failing tests**

```python
import unittest

from boss_java_apply_panel import DEFAULT_PANEL_PORT, JobPanelState, render_index_html


class BossJavaApplyPanelTests(unittest.TestCase):
    def test_state_snapshot_contains_default_idle_values(self):
        state = JobPanelState()

        snapshot = state.snapshot()

        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["processed"], 0)
        self.assertEqual(snapshot["applied"], 0)
        self.assertEqual(snapshot["skipped"], 0)
        self.assertEqual(snapshot["logs"], [])

    def test_state_log_keeps_recent_entries(self):
        state = JobPanelState(max_logs=2)

        state.log("one")
        state.log("two")
        state.log("three")

        self.assertEqual(state.snapshot()["logs"], ["two", "three"])

    def test_index_html_exposes_required_controls(self):
        html = render_index_html()

        self.assertIn('id="autoApply"', html)
        self.assertIn('id="startBtn"', html)
        self.assertIn('id="stopBtn"', html)
        self.assertIn("/api/start", html)
        self.assertIn("/api/status", html)

    def test_default_panel_port_is_separate_from_existing_image_app(self):
        self.assertEqual(DEFAULT_PANEL_PORT, 8766)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_boss_java_apply_panel -v`
Expected: import failure because `boss_java_apply_panel.py` does not exist.

- [ ] **Step 3: Implement minimal state and HTML**

Create `boss_java_apply_panel.py` with `DEFAULT_PANEL_PORT = 8766`, `JobPanelState`, and `render_index_html()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_boss_java_apply_panel -v`
Expected: 4 tests pass.

### Task 2: Reusable Runner Configuration

**Files:**
- Modify: `DrissionPage/boss_java_apply.py`
- Modify: `tests/test_boss_java_apply.py`

- [ ] **Step 1: Write the failing tests**

Add tests that import `BossApplyConfig`, `BossApplyStats`, and `parse_keywords`. Verify custom keyword parsing and default stats values.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_boss_java_apply -v`
Expected: import failure for the new symbols.

- [ ] **Step 3: Implement minimal dataclasses/helpers**

Add `BossApplyConfig`, `BossApplyStats`, and `parse_keywords()` to `DrissionPage/boss_java_apply.py`. Keep CLI behavior unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_boss_java_apply -v`
Expected: all BOSS helper tests pass.

### Task 3: Local Panel APIs And Background Task

**Files:**
- Modify: `boss_java_apply_panel.py`
- Modify: `DrissionPage/boss_java_apply.py`
- Modify: `tests/test_boss_java_apply_panel.py`

- [ ] **Step 1: Write failing tests**

Add tests for JSON config parsing: default `auto_apply` is false, `limit` is parsed as an int, and keywords are split into a tuple.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_boss_java_apply_panel -v`
Expected: missing `parse_start_payload`.

- [ ] **Step 3: Implement panel API helpers**

Add `parse_start_payload()`, JSON response helpers, and `BossPanelHandler` routes for `/`, `/api/status`, `/api/start`, and `/api/stop`.

- [ ] **Step 4: Run panel and tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_boss_java_apply_panel tests.test_boss_java_apply -v`
Expected: both focused test modules pass.

### Task 4: Verification And Serve

**Files:**
- Verify: `boss_java_apply_panel.py`
- Verify: `DrissionPage/boss_java_apply.py`
- Verify: `tests/test_boss_java_apply_panel.py`
- Verify: `tests/test_boss_java_apply.py`

- [ ] **Step 1: Compile Python files**

Run: `.\.venv\Scripts\python.exe -m py_compile boss_java_apply_panel.py DrissionPage\boss_java_apply.py tests\test_boss_java_apply_panel.py tests\test_boss_java_apply.py`
Expected: exit 0.

- [ ] **Step 2: Run full test discovery**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: all tests pass.

- [ ] **Step 3: Start local panel**

Run a background PowerShell process for `.\.venv\Scripts\python.exe boss_java_apply_panel.py --port 8766`.
Expected: `http://127.0.0.1:8766` responds with the panel HTML.
