import tempfile
import unittest
from pathlib import Path

from boss_java_apply_panel import (
    DEFAULT_PANEL_PORT,
    JobPanelState,
    open_page_with_reconnect,
    parse_start_payload,
    render_index_html,
)
from DrissionPage.boss_java_apply import AppliedJobsStore, DEFAULT_URL, JobInfo


class BrokenPage:
    def get(self, url):
        raise RuntimeError("connection disconnected")


class FreshPage:
    def __init__(self):
        self.opened_urls = []

    def get(self, url):
        self.opened_urls.append(url)


class BossJavaApplyPanelTests(unittest.TestCase):
    def test_state_snapshot_contains_default_idle_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = JobPanelState(applied_summary_file=Path(tmpdir) / "applied.csv")

            snapshot = state.snapshot()

        self.assertFalse(snapshot["running"])
        self.assertEqual(snapshot["processed"], 0)
        self.assertEqual(snapshot["applied"], 0)
        self.assertEqual(snapshot["skipped"], 0)
        self.assertEqual(snapshot["applied_jobs"], [])
        self.assertEqual(snapshot["logs"], [])

    def test_state_log_keeps_recent_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = JobPanelState(max_logs=2, applied_summary_file=Path(tmpdir) / "applied.csv")

            state.log("one")
            state.log("two")
            state.log("three")

            self.assertEqual(state.snapshot()["logs"], ["two", "three"])

    def test_stop_request_log_is_chinese(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = JobPanelState(applied_summary_file=Path(tmpdir) / "applied.csv")

            state.request_stop()

            self.assertIn("已请求停止任务。", state.snapshot()["logs"])

    def test_index_html_exposes_required_controls(self):
        html = render_index_html()

        self.assertIn('class="app-shell"', html)
        self.assertIn('class="storage-inline"', html)
        self.assertIn('class="dashboard-grid"', html)
        self.assertIn('class="config-panel"', html)
        self.assertIn('class="status-panel"', html)
        self.assertIn('class="log-panel"', html)
        self.assertIn('class="history-panel"', html)
        self.assertIn('id="autoApply" type="checkbox" checked', html)
        self.assertIn("默认开启", html)
        self.assertIn('id="requireHrOnline"', html)
        self.assertIn('id="appliedJobsBody"', html)
        self.assertIn('id="historyFilterKeyword"', html)
        self.assertIn('id="historyFilterHrStatus"', html)
        self.assertIn('id="historyFilterResult"', html)
        self.assertIn('id="historyFilterMinSalary"', html)
        self.assertIn('id="historyFilterMaxSalary"', html)
        self.assertIn('id="openBtn"', html)
        self.assertIn('id="startBtn"', html)
        self.assertIn('id="stopBtn"', html)
        self.assertIn("boss_java_processed_jobs.txt", html)
        self.assertIn("boss_java_applied_jobs.csv", html)
        self.assertIn("spring cloud alibaba", html)
        self.assertIn("kubernetes", html)
        self.assertIn("rag", html)
        self.assertNotIn("__DEFAULT_KEYWORDS__", html)
        self.assertIn("<table", html)
        self.assertNotIn('class="storage-note"', html)
        self.assertNotIn('id="summaryFile"', html)
        self.assertNotIn('id="recordFile"', html)
        self.assertIn("/api/open", html)
        self.assertIn("/api/start", html)
        self.assertIn("/api/status", html)

    def test_index_html_uses_readable_dashboard_layout(self):
        html = render_index_html()

        self.assertIn("grid-template-areas", html)
        self.assertIn(".topbar { margin-bottom: 14px;", html)
        self.assertIn('"config status"', html)
        self.assertIn('"log log"', html)
        self.assertIn('"history history"', html)
        self.assertIn("table-layout: fixed", html)
        self.assertIn("min-height: 260px", html)
        self.assertIn("投递历史自动刷新", html)
        self.assertIn("function filterAppliedJobs", html)
        self.assertIn("function parseSalaryRange", html)
        self.assertIn("historyFilterKeyword", html)
        self.assertIn("logBox.scrollTop = logBox.scrollHeight", html)
        self.assertIn('document.createElement("a")', html)
        self.assertIn('link.target = "_blank"', html)

    def test_default_panel_port_is_separate_from_existing_image_app(self):
        self.assertEqual(DEFAULT_PANEL_PORT, 8766)

    def test_index_html_uses_apply_script_default_url(self):
        self.assertIn(DEFAULT_URL, render_index_html())

    def test_parse_start_payload_defaults_to_auto_apply_mode(self):
        config = parse_start_payload({})

        self.assertTrue(config.auto_apply)
        self.assertTrue(config.require_hr_online)
        self.assertEqual(config.limit, 10)

    def test_parse_start_payload_converts_form_strings(self):
        config = parse_start_payload(
            {
                "url": "https://example.test/jobs",
                "limit": "3",
                "pause": "1.5",
                "auto_apply": True,
                "keywords": "java, spring\nmybatis",
                "require_hr_online": False,
            }
        )

        self.assertEqual(config.url, "https://example.test/jobs")
        self.assertEqual(config.limit, 3)
        self.assertEqual(config.pause, 1.5)
        self.assertTrue(config.auto_apply)
        self.assertFalse(config.require_hr_online)
        self.assertEqual(config.keywords, ("java", "spring", "mybatis"))
        self.assertEqual(str(config.record_file), "boss_java_processed_jobs.txt")
        self.assertEqual(str(config.applied_summary_file), "boss_java_applied_jobs.csv")

    def test_state_snapshot_loads_persisted_applied_jobs_from_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "applied.csv"
            store = AppliedJobsStore(path)
            store.add(JobInfo(title="Java开发", company="历史公司", url="https://example.test/job/history", text="", salary="15-20K", hr_status="今日活跃"))
            state = JobPanelState(applied_summary_file=path)

            snapshot = state.snapshot()

            self.assertEqual(snapshot["applied_jobs"][0]["company"], "历史公司")
            self.assertEqual(snapshot["applied_jobs"][0]["salary"], "15-20K")
            self.assertEqual(snapshot["applied_jobs_file"], str(path))

    def test_state_snapshot_includes_applied_jobs_from_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = JobPanelState(applied_summary_file=Path(tmpdir) / "applied.csv")

            class Stats:
                processed = 1
                applied = 1
                skipped = 0
                current_job = "Java开发 | 博彦科技"
                applied_jobs = [{"title": "Java开发", "company": "博彦科技"}]

            state.apply_stats(Stats())

            self.assertEqual(state.snapshot()["applied_jobs"], [{"title": "Java开发", "company": "博彦科技"}])

    def test_open_page_reconnects_when_cached_page_is_disconnected(self):
        fresh_pages = []

        def page_factory():
            page = FreshPage()
            fresh_pages.append(page)
            return page

        page = open_page_with_reconnect(BrokenPage(), "https://example.test/jobs", page_factory)

        self.assertIs(page, fresh_pages[0])
        self.assertEqual(page.opened_urls, ["https://example.test/jobs"])


if __name__ == "__main__":
    unittest.main()
