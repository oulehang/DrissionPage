# -*- coding: utf-8 -*-
"""Small BOSS Zhipin helper for Java-related resume delivery.

The default mode is manual: it opens the job list, finds Java-related jobs,
then asks before clicking the chat/apply button. Pass --auto-apply to remove
that per-job prompt.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence


if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from DrissionPage import ChromiumPage


DEFAULT_URL = "https://www.zhipin.com/web/geek/jobs?city=101200100&query=java"
DEFAULT_RECORD_FILE = Path("boss_java_processed_jobs.txt")
DEFAULT_APPLIED_SUMMARY_FILE = Path("boss_java_applied_jobs.csv")
DEFAULT_KEYWORDS = (
    "java",
    "j2ee",
    "spring",
    "springboot",
    "spring boot",
    "spring cloud",
    "spring cloud alibaba",
    "spring ai",
    "spring security",
    "spring mvc",
    "mybatis",
    "mybatis plus",
    "jpa",
    "hibernate",
    "dubbo",
    "nacos",
    "sentinel",
    "seata",
    "redis",
    "mysql",
    "postgresql",
    "oracle",
    "kafka",
    "rocketmq",
    "rabbitmq",
    "elasticsearch",
    "docker",
    "kubernetes",
    "k8s",
    "microservices",
    "cloud native",
    "devops",
    "ci/cd",
    "maven",
    "gradle",
    "linux",
    "jvm",
    "java 17",
    "java 21",
    "llm",
    "rag",
    "大模型",
    "微服务",
    "云原生",
)


@dataclass(frozen=True)
class JobInfo:
    title: str
    company: str
    url: str
    text: str
    salary: str = ""
    hr_status: str = ""


@dataclass(frozen=True)
class BossApplyConfig:
    url: str = DEFAULT_URL
    auto_apply: bool = False
    limit: int = 10
    record_file: Path = DEFAULT_RECORD_FILE
    applied_summary_file: Path = DEFAULT_APPLIED_SUMMARY_FILE
    pause: float = 1.0
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    require_hr_online: bool = True


@dataclass
class BossApplyStats:
    processed: int = 0
    applied: int = 0
    skipped: int = 0
    current_job: str = ""
    applied_jobs: list[dict[str, str]] = field(default_factory=list)


Logger = Callable[[str], None]
ConfirmApply = Callable[[JobInfo], bool]
StopRequested = Callable[[], bool]
StatsCallback = Callable[[BossApplyStats], None]


class ProcessedJobsStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._keys = self._load()

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        return {
            line.strip()
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    def has(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        if key in self._keys:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(f"{key}\n")
        self._keys.add(key)


class AppliedJobsStore:
    fieldnames = ("applied_at", "title", "company", "url", "salary", "hr_status", "result")

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def add(self, job: JobInfo, result: str = "已投递") -> dict[str, str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "salary": job.salary,
            "hr_status": job.hr_status,
            "result": result,
        }
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=self.fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        return row

    def recent(self, limit: int = 200) -> list[dict[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        lines = [decode_csv_line(line) for line in self.path.read_bytes().splitlines()]
        rows = [
            {field: row.get(field, "") for field in self.fieldnames}
            for row in csv.DictReader(lines)
        ]
        return list(reversed(rows[-limit:]))


def decode_csv_line(line: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return line.decode(encoding)
        except UnicodeDecodeError:
            continue
    return line.decode("utf-8", errors="replace")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open BOSS Zhipin jobs and apply to Java-related positions."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Job list URL to open.")
    parser.add_argument(
        "--auto-apply",
        action="store_true",
        help="Click the apply/chat button without per-job confirmation.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=10,
        help="Maximum number of matching jobs to process.",
    )
    parser.add_argument(
        "--record-file",
        type=Path,
        default=DEFAULT_RECORD_FILE,
        help="File used to remember processed jobs.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Seconds to wait after opening a job card.",
    )
    return parser


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_keywords(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_KEYWORDS
    if isinstance(value, str):
        raw_keywords = re.split(r"[,，\n\r]+", value)
    else:
        raw_keywords = value
    keywords = tuple(keyword.strip().lower() for keyword in raw_keywords if keyword and keyword.strip())
    return keywords or DEFAULT_KEYWORDS


def is_java_related(text: str, keywords: Sequence[str] = DEFAULT_KEYWORDS) -> bool:
    normalized = text.lower()
    for keyword in keywords:
        if keyword == "java" and re.search(r"(?<![a-z])java(?![a-z])", normalized):
            return True
        if keyword != "java" and keyword in normalized:
            return True
    return False


def build_job_key(title: str, company: str, url: str) -> str:
    parts = [_clean_key_part(part) for part in (title, company, url)]
    return "\t".join(parts)


def _clean_key_part(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def config_from_args(args: argparse.Namespace) -> BossApplyConfig:
    return BossApplyConfig(
        url=args.url,
        auto_apply=args.auto_apply,
        limit=args.limit,
        record_file=args.record_file,
        pause=args.pause,
    )


def run(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    page = ChromiumPage()
    page.get(config.url)
    print("已打开 BOSS 直聘职位列表。")
    input("请先登录并处理验证码，然后按 Enter 继续...")
    run_with_config(
        config,
        page=page,
        open_page=False,
        logger=print,
        confirm_apply=lambda job: should_apply(config.auto_apply),
    )
    return 0


def run_with_config(
    config: BossApplyConfig,
    page=None,
    logger: Logger | None = None,
    confirm_apply: ConfirmApply | None = None,
    stop_requested: StopRequested | None = None,
    on_stats: StatsCallback | None = None,
    page_factory=ChromiumPage,
    open_page: bool = True,
) -> BossApplyStats:
    logger = logger or print
    confirm_apply = confirm_apply or (lambda job: config.auto_apply)
    stop_requested = stop_requested or (lambda: False)
    stats = BossApplyStats()
    store = ProcessedJobsStore(config.record_file)
    applied_store = AppliedJobsStore(config.applied_summary_file)
    seen_keys: set[str] = set()
    page = page or page_factory()
    if open_page:
        page.get(config.url)
        logger("已打开 BOSS 直聘职位列表。")

    def publish() -> None:
        if on_stats:
            on_stats(stats)

    while stats.processed < config.limit and not stop_requested():
        cards = find_job_cards(page)
        if not cards:
            logger("未找到职位卡片。请确认页面已登录、职位列表已加载，然后重新点击打开页面或开始处理。")
            break

        made_progress = False
        visible_keys = visible_job_keys(cards)
        for card in cards:
            if stats.processed >= config.limit or stop_requested():
                break

            job = extract_job_info(card)
            if not is_java_related(job.text, config.keywords):
                logger(f"跳过非 Java 相关职位：{job.title} | {job.company}")
                continue

            key = build_job_key(job.title, job.company, job.url)
            if key in seen_keys:
                continue
            if store.has(key):
                seen_keys.add(key)
                stats.skipped += 1
                publish()
                continue

            seen_keys.add(key)
            made_progress = True
            stats.processed += 1
            stats.current_job = f"{job.title} | {job.company}"
            publish()
            logger(f"[{stats.processed}/{config.limit}] {stats.current_job}")
            logger(job.url or "未找到职位链接")

            open_job_card(card, config.pause)
            detail_salary = extract_detail_salary(page)
            if detail_salary and not job.salary:
                job = replace(job, salary=detail_salary)
            hr_status = extract_hr_status(page, card)
            if config.require_hr_online and not is_hr_online(page, card):
                stats.skipped += 1
                publish()
                logger(f"跳过：HR 非在线或今日活跃，{job.title} | {job.company}")
                continue
            job = replace(job, hr_status=hr_status)

            if confirm_apply(job):
                if click_apply_button(page):
                    store.add(key)
                    applied_row = applied_store.add(job)
                    stats.applied_jobs.append(applied_row)
                    stats.applied += 1
                    publish()
                    logger(f"已点击沟通/投递按钮，并写入汇总：{config.applied_summary_file}")
                else:
                    logger("未找到沟通/投递按钮，请在浏览器中手动处理该职位。")
            else:
                stats.skipped += 1
                publish()
                logger("已跳过自动点击。人工模式会打开职位，由你在浏览器中手动处理。")

        if stats.processed >= config.limit or stop_requested():
            break
        if go_next_page(page):
            continue
        if scroll_to_more_jobs(page, cards, visible_keys, config.pause):
            logger("已向下滚动职位列表，继续处理新加载岗位。")
            continue
        if not made_progress:
            logger("当前可见职位都已处理或跳过，且没有加载出更多职位。")
            break
        logger("没有检测到更多新职位，任务结束。")
        break

    logger(f"任务结束。已处理={stats.processed}，已投递={stats.applied}，已跳过={stats.skipped}")
    publish()
    return stats


def find_job_cards(page) -> list:
    locators = (
        "css:.job-card-box",
        "css:.job-card-wrapper",
        "css:.job-card-body",
        "css:.job-list-box .job-card-box",
        "css:.job-list-box li",
        "css:.search-job-result .job-card-box",
        "css:.job-primary",
    )
    for locator in locators:
        try:
            cards = page.eles(locator, timeout=1)
        except Exception:
            cards = []
        if cards:
            return list(cards)
    return []


def visible_job_keys(cards: Sequence) -> set[str]:
    keys = set()
    for card in cards:
        job = extract_job_info(card)
        keys.add(build_job_key(job.title, job.company, job.url))
    return keys


def extract_job_info(card) -> JobInfo:
    title = first_text(
        card,
        (
            "css:.job-name",
            "css:.job-title",
            "css:.job-title-name",
            "css:.job-card-left",
        ),
    )
    company = first_text(
        card,
        (
            "css:.company-name",
            "css:.boss-name",
            "css:.company-info",
            "css:.job-card-footer",
            "css:.job-card-right",
        ),
    )
    url = first_attr(card, ("href",))
    if not url:
        link = first_ele(card, ("css:a",))
        url = link.attr("href") if link else ""
    text = safe_text(card)
    salary = first_text(
        card,
        (
            "css:.salary",
            "css:.job-salary",
            "css:.salary-text",
            "css:.job-limit .salary",
            "css:.job-card-right .salary",
            "css:.red",
        ),
    )
    return JobInfo(
        title=title or "(unknown title)",
        company=company or "(unknown company)",
        url=url or "",
        text=text,
        salary=extract_salary(salary) or extract_salary(text),
    )


def extract_salary(text: str) -> str:
    normalized = (text or "").replace("—", "-").replace("–", "-").replace("－", "-")
    match = re.search(r"\d+\s*[-~]\s*\d+\s*K(?:[·.]\d+薪|·\d+薪)?", normalized, re.IGNORECASE)
    return match.group(0).replace(" ", "").replace("k", "K") if match else ""


def extract_detail_salary(page, wait_seconds: float = 3.0, interval: float = 0.2) -> str:
    deadline = time.time() + max(0.0, wait_seconds)
    while True:
        salary = first_text(
            page,
            (
                "css:.job-detail .salary",
                "css:.job-detail .job-salary",
                "css:.job-detail-header .salary",
                "css:.job-detail-header .job-salary",
                "css:.job-detail-title .salary",
                "css:.job-detail-title .job-salary",
                "css:.job-detail-box .salary",
                "css:.job-detail-box .job-salary",
                "css:.job-info .salary",
                "css:.job-info .job-salary",
                "css:.job-banner .salary",
                "css:.job-banner .job-salary",
                "css:.job-primary .salary",
                "css:.info-primary .salary",
                "css:[class*=salary]",
                "css:.red",
            ),
        )
        extracted = extract_salary(salary) or extract_salary(safe_text(page)) or extract_salary(safe_html(page))
        if extracted or time.time() >= deadline:
            return extracted
        time.sleep(max(0.0, interval))


def is_hr_online(page, card=None) -> bool:
    return is_hr_online_or_active_today(extract_hr_status(page, card))


def is_hr_online_or_active_today(status_text: str) -> bool:
    if any(word in status_text for word in ("离线", "不在线", "昨日活跃", "昨天活跃", "天前活跃", "周前活跃", "月前活跃")):
        return False
    return any(
        word in status_text
        for word in ("在线", "今日活跃", "今天活跃", "刚刚活跃", "分钟前活跃", "小时前活跃")
    )


def extract_hr_status(page, card=None) -> str:
    texts = []
    for source in (page, card):
        if not source:
            continue
        texts.append(safe_text(source))
        for locator in (
            "css:.boss-online",
            "css:.online",
            "css:.user-status",
            "css:.recruiter-status",
            "css:.boss-info",
            "css:.job-boss-info",
            "css:.detail-boss",
        ):
            found = first_ele(source, (locator,))
            if found:
                texts.append(safe_text(found))
    status_text = "\n".join(texts)
    for pattern in (
        r"不在线",
        r"离线",
        r"在线",
        r"今日活跃",
        r"今天活跃",
        r"刚刚活跃",
        r"\d+\s*分钟前活跃",
        r"\d+\s*小时前活跃",
        r"昨日活跃",
        r"昨天活跃",
        r"\d+\s*天前活跃",
        r"\d+\s*周前活跃",
        r"\d+\s*月前活跃",
    ):
        match = re.search(pattern, status_text)
        if match:
            return match.group(0).replace(" ", "")
    return status_text.strip()


def first_ele(root, locators: Iterable[str]):
    for locator in locators:
        try:
            found = root.ele(locator, timeout=0.5)
        except Exception:
            found = None
        if found:
            return found
    return None


def first_text(root, locators: Iterable[str]) -> str:
    found = first_ele(root, locators)
    return safe_text(found) if found else ""


def first_attr(root, attrs: Iterable[str]) -> str:
    for attr in attrs:
        try:
            value = root.attr(attr)
        except Exception:
            value = None
        if value:
            return value
    return ""


def safe_text(element) -> str:
    try:
        return element.text or ""
    except Exception:
        return ""


def safe_html(element) -> str:
    try:
        return element.html or ""
    except Exception:
        return ""


def open_job_card(card, pause: float) -> None:
    try:
        card.click()
    except Exception as exc:
        print(f"无法自动点击职位卡片：{exc}")
    time.sleep(max(0, pause))


def should_apply(auto_apply: bool) -> bool:
    if auto_apply:
        return True
    answer = input("Apply/start chat for this job? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def click_apply_button(page) -> bool:
    locators = (
        "text=立即沟通",
        "text=继续沟通",
        "css:.btn-startchat",
        "css:.op-btn-chat",
        "css:.btn-apply",
    )
    for locator in locators:
        try:
            button = page.ele(locator, timeout=1)
        except Exception:
            button = None
        if not button:
            continue
        try:
            button.click()
            time.sleep(0.8)
            click_stay_on_page_button(page)
            return True
        except Exception as exc:
            print(f"无法点击由 {locator} 定位到的按钮：{exc}")
    return False


def click_stay_on_page_button(page) -> bool:
    locators = (
        "text=留在此页",
        "text=留在本页",
        "css:.boss-popup__footer .cancel-btn",
        "css:.dialog-footer .cancel-btn",
    )
    for locator in locators:
        try:
            button = page.ele(locator, timeout=1)
        except Exception:
            button = None
        if not button:
            continue
        try:
            button.click()
            time.sleep(0.3)
            return True
        except Exception as exc:
            print(f"无法点击留在此页按钮：{exc}")
    return False


def go_next_page(page) -> bool:
    locators = (
        "css:.options-pages a.next",
        "css:.ui-icon-arrow-right",
        "text=下一页",
    )
    for locator in locators:
        try:
            button = page.ele(locator, timeout=1)
        except Exception:
            button = None
        if not button:
            continue
        try:
            button.click()
            time.sleep(1.5)
            return True
        except Exception:
            continue
    return False


def scroll_to_more_jobs(page, cards: Sequence, previous_keys: set[str], pause: float) -> bool:
    anchor = cards[-1] if cards else None
    if not scroll_job_list(page, anchor, pause):
        return False
    new_cards = find_job_cards(page)
    if not new_cards:
        return False
    return bool(visible_job_keys(new_cards) - previous_keys)


def scroll_job_list(page, anchor_card=None, pause: float = 1.0) -> bool:
    for locator in (
        "css:.job-list-box",
        "css:.search-job-result",
        "css:.job-list",
        "css:.job-list-container",
    ):
        container = first_ele(page, (locator,))
        if container and scroll_target(page, container):
            time.sleep(max(0.5, pause))
            return True

    if anchor_card:
        try:
            anchor_card.scroll.to_see()
        except Exception:
            pass
        if scroll_target(page, anchor_card):
            time.sleep(max(0.5, pause))
            return True

    if scroll_target(page, None):
        time.sleep(max(0.5, pause))
        return True
    return False


def scroll_target(page, target) -> bool:
    if target is not None:
        try:
            target.scroll.down(900)
            return True
        except Exception:
            pass

    try:
        page.actions.scroll(delta_y=900, on_ele=target)
        return True
    except Exception:
        pass

    try:
        page.scroll.down(900)
        return True
    except Exception:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
