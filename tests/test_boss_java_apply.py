import argparse
import tempfile
import unittest
from pathlib import Path

from DrissionPage.boss_java_apply import (
    AppliedJobsStore,
    BossApplyConfig,
    BossApplyStats,
    DEFAULT_KEYWORDS,
    DEFAULT_URL,
    JobInfo,
    ProcessedJobsStore,
    build_arg_parser,
    build_job_key,
    click_apply_button,
    extract_detail_salary,
    extract_job_info,
    extract_salary,
    find_job_cards,
    is_java_related,
    is_hr_online,
    parse_keywords,
    run_with_config,
)


class FakePage:
    def __init__(self, locator_results=None):
        self.locator_results = locator_results or {}
        self.requested_locators = []
        self.opened_urls = []

    def get(self, url):
        self.opened_urls.append(url)

    def eles(self, locator, timeout=None):
        self.requested_locators.append(locator)
        return self.locator_results.get(locator, [])


class FakeElement:
    def __init__(self, text="", attrs=None):
        self.text = text
        self.attrs = attrs or {}

    def attr(self, name):
        return self.attrs.get(name)


class FakeCard:
    def __init__(self, title="Java开发工程师", company="测试公司", text=None, url="https://example.test/job/1"):
        self.title = title
        self.company = company
        self.text = text or f"{title} 12-24K 5-10年 本科 {company} Spring MyBatis"
        self.url = url
        self.clicked = False

    def ele(self, locator, timeout=None):
        if locator in {"css:.job-name", "css:.job-title", "css:.job-title-name", "css:.job-card-left"}:
            return FakeElement(self.title)
        if locator in {"css:.company-name", "css:.boss-name", "css:.company-info", "css:.job-card-footer", "css:.job-card-right"}:
            return FakeElement(self.company)
        if locator == "css:a":
            return FakeElement(attrs={"href": self.url})
        return None

    def attr(self, name):
        if name == "href":
            return self.url
        return None

    def click(self):
        self.clicked = True


class FakeSalaryCard(FakeCard):
    def __init__(self):
        super().__init__(
            title="Java开发工程师",
            company="薪资测试公司",
            text="Java开发工程师 本科 薪资测试公司 Spring MyBatis",
            url="https://example.test/job/salary",
        )

    def ele(self, locator, timeout=None):
        if locator in {"css:.salary", "css:.job-salary", "css:.red"}:
            return FakeElement("18-28K")
        return super().ele(locator, timeout=timeout)


class FakeButton:
    def __init__(self, page, locator):
        self.page = page
        self.locator = locator

    def click(self):
        self.page.clicked_locators.append(self.locator)
        if self.locator == "text=立即沟通":
            self.page.apply_clicked = True


class FakeApplyPage:
    def __init__(self):
        self.apply_clicked = False
        self.clicked_locators = []

    def ele(self, locator, timeout=None):
        if locator == "text=立即沟通":
            return FakeButton(self, locator)
        if locator == "text=留在此页" and self.apply_clicked:
            return FakeButton(self, locator)
        return None


class FakeRunPage(FakeApplyPage):
    def __init__(self, cards, detail_text):
        super().__init__()
        self.cards = cards
        self.text = detail_text

    def get(self, url):
        self.opened_url = url

    def eles(self, locator, timeout=None):
        return self.cards if locator == "css:.job-card-box" else []


class FakeDetailSalaryRunPage(FakeRunPage):
    def ele(self, locator, timeout=None):
        if locator in {
            "css:.job-detail .salary",
            "css:.job-detail .job-salary",
            "css:.job-detail-box .salary",
            "css:.job-detail-box .job-salary",
            "css:.job-info .salary",
            "css:.job-info .job-salary",
            "css:.job-banner .salary",
            "css:.job-banner .job-salary",
            "css:.salary",
            "css:.job-salary",
            "css:.red",
        }:
            return FakeElement("20-25K")
        return super().ele(locator, timeout=timeout)


class FakeDelayedDetailSalaryPage:
    text = ""

    def __init__(self):
        self.polls = 0

    def ele(self, locator, timeout=None):
        if locator == "css:.job-detail .salary":
            self.polls += 1
            if self.polls >= 2:
                return FakeElement("25—35K·14薪")
        return None


class FakeScroller:
    def __init__(self, page):
        self.page = page

    def down(self, pixel=300):
        self.page.scroll_count += 1
        self.page.stage = min(self.page.stage + 1, len(self.page.card_stages) - 1)
        return self.page


class FakeScrollingRunPage(FakeApplyPage):
    def __init__(self, card_stages, detail_text):
        super().__init__()
        self.card_stages = card_stages
        self.stage = 0
        self.scroll_count = 0
        self.text = detail_text
        self.scroll = FakeScroller(self)

    def get(self, url):
        self.opened_url = url

    def eles(self, locator, timeout=None):
        return self.card_stages[self.stage] if locator == "css:.job-card-box" else []


class BossJavaApplyTests(unittest.TestCase):
    def test_java_keywords_match_common_backend_titles(self):
        self.assertTrue(is_java_related("Senior Java Backend Engineer"))
        self.assertTrue(is_java_related("Java Spring Boot developer"))
        self.assertTrue(is_java_related("J2EE engineer"))
        self.assertTrue(is_java_related("Java开发"))
        self.assertTrue(is_java_related("Java技术专家/架构师-武汉"))
        self.assertTrue(is_java_related("高级应用研发工程师(Java)"))

    def test_non_java_titles_are_skipped(self):
        self.assertFalse(is_java_related("Python crawler engineer"))
        self.assertFalse(is_java_related("frontend Vue developer"))
        self.assertFalse(is_java_related("JavaScript frontend developer"))

    def test_parser_defaults_to_manual_apply_mode(self):
        parser = build_arg_parser()
        args = parser.parse_args([])

        self.assertEqual(DEFAULT_URL, "https://www.zhipin.com/web/geek/jobs?city=101200100&query=java")
        self.assertEqual(args.url, DEFAULT_URL)
        self.assertFalse(args.auto_apply)
        self.assertEqual(args.limit, 10)

    def test_parser_enables_auto_apply_only_with_explicit_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--auto-apply", "--limit", "3"])

        self.assertTrue(args.auto_apply)
        self.assertEqual(args.limit, 3)

    def test_processed_store_persists_job_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "processed.txt"
            store = ProcessedJobsStore(path)
            key = build_job_key("Java Engineer", "Example Co", "https://example.test/job/1")

            self.assertFalse(store.has(key))

            store.add(key)
            reloaded = ProcessedJobsStore(path)

            self.assertTrue(reloaded.has(key))

    def test_build_job_key_is_stable_and_trims_whitespace(self):
        left = build_job_key(" Java Engineer ", " Example Co ", " https://example.test/job/1 ")
        right = build_job_key("Java Engineer", "Example Co", "https://example.test/job/1")

        self.assertEqual(left, right)

    def test_parse_keywords_splits_commas_and_newlines(self):
        keywords = parse_keywords("java, spring boot\nmybatis，jvm")

        self.assertEqual(keywords, ("java", "spring boot", "mybatis", "jvm"))

    def test_default_keywords_cover_current_java_backend_stack(self):
        for keyword in (
            "spring cloud alibaba",
            "spring ai",
            "mybatis plus",
            "redis",
            "kafka",
            "rocketmq",
            "elasticsearch",
            "nacos",
            "docker",
            "kubernetes",
            "rag",
        ):
            self.assertIn(keyword, DEFAULT_KEYWORDS)

    def test_config_defaults_to_manual_mode(self):
        config = BossApplyConfig()

        self.assertEqual(config.url, DEFAULT_URL)
        self.assertFalse(config.auto_apply)
        self.assertEqual(config.limit, 10)

    def test_stats_defaults_to_zero_counts(self):
        stats = BossApplyStats()

        self.assertEqual(stats.processed, 0)
        self.assertEqual(stats.applied, 0)
        self.assertEqual(stats.skipped, 0)
        self.assertEqual(stats.current_job, "")

    def test_find_job_cards_supports_current_boss_job_card_box_layout(self):
        cards = [FakeCard()]
        page = FakePage({"css:.job-card-box": cards})

        self.assertEqual(find_job_cards(page), cards)
        self.assertIn("css:.job-card-box", page.requested_locators)

    def test_extract_job_info_reads_salary_from_card_salary_node(self):
        job = extract_job_info(FakeSalaryCard())

        self.assertEqual(job.salary, "18-28K")

    def test_extract_salary_supports_current_boss_salary_formats(self):
        self.assertEqual(extract_salary("薪资 20—30K·14薪"), "20-30K·14薪")
        self.assertEqual(extract_salary("15–25K"), "15-25K")
        self.assertEqual(extract_salary("30-45k"), "30-45K")

    def test_extract_detail_salary_waits_for_async_detail_render(self):
        page = FakeDelayedDetailSalaryPage()

        self.assertEqual(extract_detail_salary(page, wait_seconds=0.2, interval=0), "25-35K·14薪")

    def test_no_cards_log_is_chinese(self):
        logs = []
        page = FakePage()

        run_with_config(BossApplyConfig(limit=1), page=page, open_page=False, logger=logs.append)

        self.assertTrue(any("未找到职位卡片" in line for line in logs))
        self.assertFalse(any("No job cards found" in line for line in logs))

    def test_apply_click_stays_on_current_page_after_success_dialog(self):
        page = FakeApplyPage()

        self.assertTrue(click_apply_button(page))

        self.assertEqual(page.clicked_locators, ["text=立即沟通", "text=留在此页"])

    def test_hr_online_detection_requires_explicit_online_status(self):
        self.assertTrue(is_hr_online(FakeElement("张女士 在线")))
        self.assertTrue(is_hr_online(FakeElement("张女士 刚刚活跃")))
        self.assertTrue(is_hr_online(FakeElement("张女士 今日活跃")))
        self.assertTrue(is_hr_online(FakeElement("张女士 2小时前活跃")))
        self.assertFalse(is_hr_online(FakeElement("张女士 离线")))
        self.assertFalse(is_hr_online(FakeElement("张女士 昨日活跃")))
        self.assertFalse(is_hr_online(FakeElement("张女士 3天前活跃")))

    def test_run_skips_java_job_when_hr_is_not_online(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card = FakeCard()
            page = FakeRunPage([card], "张女士 3天前活跃")
            summary_path = Path(tmpdir) / "applied.csv"

            stats = run_with_config(
                BossApplyConfig(
                    auto_apply=True,
                    limit=1,
                    record_file=Path(tmpdir) / "processed.txt",
                    applied_summary_file=summary_path,
                ),
                page=page,
                open_page=False,
                logger=lambda message: None,
            )

            self.assertEqual(stats.applied, 0)
            self.assertEqual(stats.skipped, 1)
            self.assertEqual(page.clicked_locators, [])
            self.assertFalse(summary_path.exists())

    def test_run_applies_java_job_when_hr_is_active_today(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card = FakeCard(title="Java开发", company="今日活跃公司", url="https://example.test/job/today")
            page = FakeRunPage([card], "招聘者 今日活跃")
            summary_path = Path(tmpdir) / "applied.csv"

            stats = run_with_config(
                BossApplyConfig(
                    auto_apply=True,
                    limit=1,
                    record_file=Path(tmpdir) / "processed.txt",
                    applied_summary_file=summary_path,
                ),
                page=page,
                open_page=False,
                logger=lambda message: None,
            )

            self.assertEqual(stats.applied, 1)
            self.assertTrue(summary_path.exists())

    def test_run_records_summary_when_online_job_is_applied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card = FakeCard(title="Java开发", company="博彦科技", url="https://example.test/job/2")
            page = FakeRunPage([card], "招聘者 在线")
            summary_path = Path(tmpdir) / "applied.csv"

            stats = run_with_config(
                BossApplyConfig(
                    auto_apply=True,
                    limit=1,
                    record_file=Path(tmpdir) / "processed.txt",
                    applied_summary_file=summary_path,
                ),
                page=page,
                open_page=False,
                logger=lambda message: None,
            )

            self.assertEqual(stats.applied, 1)
            self.assertTrue(summary_path.exists())
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("Java开发", summary)
            self.assertIn("博彦科技", summary)
            self.assertIn("https://example.test/job/2", summary)

    def test_run_records_salary_from_detail_page_when_card_salary_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            card = FakeCard(
                title="Java寮€鍙戝伐绋嬪笀",
                company="钖祫璇︽儏鍏徃",
                text="Java寮€鍙戝伐绋嬪笀 鏈 钖祫璇︽儏鍏徃 Spring MyBatis",
                url="https://example.test/job/detail-salary",
            )
            page = FakeDetailSalaryRunPage([card], "鎷涜仒鑰?鍦ㄧ嚎")
            summary_path = Path(tmpdir) / "applied.csv"

            stats = run_with_config(
                BossApplyConfig(
                    auto_apply=True,
                    limit=1,
                    require_hr_online=False,
                    record_file=Path(tmpdir) / "processed.txt",
                    applied_summary_file=summary_path,
                ),
                page=page,
                open_page=False,
                logger=lambda message: None,
            )

            self.assertEqual(stats.applied, 1)
            rows = AppliedJobsStore(summary_path).recent()
            self.assertEqual(rows[0]["salary"], "20-25K")

    def test_run_scrolls_job_list_when_current_cards_are_exhausted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = FakeCard(title="Java开发", company="第一家公司", url="https://example.test/job/scroll-1")
            second = FakeCard(title="Java后端开发", company="第二家公司", url="https://example.test/job/scroll-2")
            page = FakeScrollingRunPage([[first], [second]], "招聘者 在线")
            summary_path = Path(tmpdir) / "applied.csv"

            stats = run_with_config(
                BossApplyConfig(
                    auto_apply=True,
                    limit=2,
                    pause=0,
                    record_file=Path(tmpdir) / "processed.txt",
                    applied_summary_file=summary_path,
                ),
                page=page,
                open_page=False,
                logger=lambda message: None,
            )

            self.assertEqual(stats.applied, 2)
            self.assertEqual(page.scroll_count, 1)
            self.assertTrue(first.clicked)
            self.assertTrue(second.clicked)

    def test_applied_jobs_store_writes_csv_header_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "applied.csv"
            store = AppliedJobsStore(path)

            store.add(JobInfo(title="Java开发", company="博彦科技", url="https://example.test/job/3", text=""))
            store.add(JobInfo(title="Java架构师", company="趣链", url="https://example.test/job/4", text=""))

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0].split(",")[:4], ["applied_at", "title", "company", "url"])
            self.assertEqual(len(lines), 3)

    def test_applied_jobs_store_reads_recent_rows_from_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "applied.csv"
            store = AppliedJobsStore(path)

            store.add(JobInfo(title="Java开发", company="第一家公司", url="https://example.test/job/1", text="", salary="15-20K", hr_status="在线"))
            store.add(JobInfo(title="Java后端", company="第二家公司", url="https://example.test/job/2", text="", salary="20-30K", hr_status="今日活跃"))

            rows = store.recent()

            self.assertEqual([row["company"] for row in rows], ["第二家公司", "第一家公司"])
            self.assertEqual(rows[0]["salary"], "20-30K")
            self.assertEqual(rows[0]["hr_status"], "今日活跃")

    def test_applied_jobs_store_reads_mixed_encoding_legacy_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "applied.csv"
            header = "applied_at,title,company,url,salary,hr_status,result\r\n".encode("ascii")
            gbk_row = "2026/6/29 16:17,java开发工程师,精臣,https://example.test/gbk,,,已投递\r\n".encode("gbk")
            utf8_row = "2026-07-01 10:00:00,Java后端,新公司,https://example.test/utf8,20-30K,今日活跃,已投递\r\n".encode("utf-8")
            path.write_bytes(header + gbk_row + utf8_row)

            rows = AppliedJobsStore(path).recent()

            self.assertEqual([row["company"] for row in rows], ["新公司", "精臣"])
            self.assertEqual(rows[0]["hr_status"], "今日活跃")


if __name__ == "__main__":
    unittest.main()
