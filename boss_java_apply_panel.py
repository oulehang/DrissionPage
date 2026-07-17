# -*- coding: utf-8 -*-
"""Local web panel for controlling the BOSS Java apply helper."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import JSONDecodeError, dumps, loads
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from DrissionPage import ChromiumPage
from DrissionPage.boss_java_apply import (
    AppliedJobsStore,
    DEFAULT_APPLIED_SUMMARY_FILE,
    DEFAULT_KEYWORDS,
    DEFAULT_RECORD_FILE,
    DEFAULT_URL,
    BossApplyConfig,
    BossApplyStats,
    parse_keywords,
    run_with_config,
)


DEFAULT_PANEL_HOST = "127.0.0.1"
DEFAULT_PANEL_PORT = 8766


class JobPanelState:
    def __init__(self, max_logs: int = 200, applied_summary_file: Path | str = DEFAULT_APPLIED_SUMMARY_FILE):
        self._lock = Lock()
        self._logs = deque(maxlen=max_logs)
        self.applied_summary_file = Path(applied_summary_file)
        self.running = False
        self.stop_requested = False
        self.processed = 0
        self.applied = 0
        self.skipped = 0
        self.current_job = ""
        self.error = ""
        self.applied_jobs = []

    def reset_for_run(self) -> None:
        with self._lock:
            self.running = True
            self.stop_requested = False
            self.processed = 0
            self.applied = 0
            self.skipped = 0
            self.current_job = ""
            self.error = ""
            self.applied_jobs = []
            self._logs.clear()

    def request_stop(self) -> None:
        with self._lock:
            self.stop_requested = True
            self._logs.append("已请求停止任务。")

    def finish(self) -> None:
        with self._lock:
            self.running = False

    def set_error(self, message: str) -> None:
        with self._lock:
            self.error = message
            self._logs.append(message)

    def apply_stats(self, stats: BossApplyStats) -> None:
        with self._lock:
            self.processed = stats.processed
            self.applied = stats.applied
            self.skipped = stats.skipped
            self.current_job = stats.current_job
            self.applied_jobs = list(getattr(stats, "applied_jobs", []))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            persisted_applied_jobs = AppliedJobsStore(self.applied_summary_file).recent()
            return {
                "running": self.running,
                "stop_requested": self.stop_requested,
                "processed": self.processed,
                "applied": self.applied,
                "skipped": self.skipped,
                "current_job": self.current_job,
                "error": self.error,
                "applied_jobs": persisted_applied_jobs or list(self.applied_jobs),
                "applied_jobs_file": str(self.applied_summary_file),
                "processed_jobs_file": str(DEFAULT_RECORD_FILE),
                "logs": list(self._logs),
            }

    def log(self, message: str) -> None:
        with self._lock:
            self._logs.append(message)


def render_index_html() -> str:
    return (
        INDEX_HTML
        .replace("__DEFAULT_URL__", DEFAULT_URL)
        .replace("__DEFAULT_KEYWORDS__", ", ".join(DEFAULT_KEYWORDS))
    )


def parse_start_payload(payload: dict[str, Any]) -> BossApplyConfig:
    return BossApplyConfig(
        url=str(payload.get("url") or BossApplyConfig().url),
        auto_apply=bool(payload.get("auto_apply", True)),
        limit=max(1, int(payload.get("limit") or 10)),
        record_file=DEFAULT_RECORD_FILE,
        applied_summary_file=DEFAULT_APPLIED_SUMMARY_FILE,
        pause=max(0.0, float(payload.get("pause") or 1.0)),
        keywords=parse_keywords(payload.get("keywords")),
        require_hr_online=bool(payload.get("require_hr_online", True)),
    )


def open_page_with_reconnect(cached_page, url: str, page_factory=ChromiumPage):
    if cached_page is not None:
        try:
            cached_page.get(url)
            return cached_page
        except Exception:
            pass
    page = page_factory()
    page.get(url)
    return page


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BOSS Java 投递控制台</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      background: #eef2f6;
      color: #1f2328;
      --panel: #ffffff;
      --line: #d7dee8;
      --muted: #667085;
      --field: #fbfcfe;
      --primary: #1264d8;
      --primary-dark: #0b56bf;
      --danger: #c62828;
      --success: #16833f;
      --neutral: #5c6675;
      --warn-bg: #fff6d7;
      --warn-text: #7a5a00;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: #eef2f6; color: #1f2328; }
    .app-shell { width: min(100%, 1480px); margin: 0 auto; padding: 28px 24px 42px; }
    .topbar { margin-bottom: 14px; }
    .topbar p { margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .storage-inline { margin: 6px 0 0; color: #475467; font-size: 12px; line-height: 1.45; }
    h1 { display: flex; align-items: center; gap: 10px; font-size: 24px; line-height: 1.2; margin: 0; letter-spacing: 0; }
    h1::before { content: ""; width: 10px; height: 28px; border-radius: 4px; background: #20a464; flex: 0 0 auto; }
    h2 { font-size: 16px; line-height: 1.25; margin: 0 0 14px; letter-spacing: 0; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: 0 1px 2px rgba(16, 24, 40, .04); }
    label { display: block; font-size: 13px; font-weight: 700; margin-bottom: 7px; color: #344054; }
    input, textarea {
      width: 100%; border: 1px solid #c7d0dd; border-radius: 7px; padding: 10px 11px;
      font: inherit; background: var(--field); color: #1f2328; outline: none;
    }
    textarea { min-height: 86px; resize: vertical; line-height: 1.5; }
    input:focus, textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(18,100,216,.12); background: #fff; }
    button {
      border: 0; border-radius: 7px; padding: 10px 14px; min-height: 40px;
      background: var(--primary); color: white; font-weight: 700; cursor: pointer;
    }
    button:hover { background: var(--primary-dark); }
    button.secondary { background: var(--neutral); }
    button.danger { background: var(--danger); }
    button:disabled { background: #98a2b3; cursor: not-allowed; }
    .dashboard-grid {
      display: grid;
      grid-template-columns: minmax(620px, 1.2fr) minmax(420px, .8fr);
      grid-template-areas:
        "config status"
        "log log"
        "history history";
      gap: 16px;
      align-items: start;
    }
    .config-panel { grid-area: config; min-width: 0; }
    .status-panel { grid-area: status; min-width: 0; }
    .log-panel { grid-area: log; min-width: 0; }
    .history-panel { grid-area: history; min-width: 0; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .wide { grid-column: 1 / -1; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 12px; }
    .toggle { display: flex; gap: 10px; align-items: center; padding: 11px; border: 1px solid #f0d98c; background: var(--warn-bg); border-radius: 8px; color: var(--warn-text); }
    .toggle input { width: auto; }
    .metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .metric { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfe; min-height: 72px; }
    .metric b { display: block; font-size: 12px; color: var(--muted); margin-bottom: 7px; }
    .metric span { font-size: 18px; font-weight: 700; overflow-wrap: anywhere; }
    .log {
      min-height: 260px; max-height: 360px; overflow-y: auto; white-space: pre-wrap; overflow-wrap: anywhere;
      background: #111827; color: #dbeafe; border-radius: 8px; padding: 13px 14px;
      font-family: Consolas, "Courier New", monospace; font-size: 12px; line-height: 1.55;
    }
    .table-toolbar { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin-bottom: 12px; }
    .table-toolbar p { margin: 0; color: var(--muted); font-size: 13px; }
    .history-filters {
      display: grid;
      grid-template-columns: minmax(220px, 1.5fr) minmax(130px, .8fr) minmax(110px, .7fr) repeat(2, minmax(92px, .6fr));
      gap: 10px;
      margin-bottom: 12px;
      align-items: end;
    }
    .history-filters label { margin-bottom: 5px; }
    select {
      width: 100%; border: 1px solid #c7d0dd; border-radius: 7px; padding: 10px 11px;
      font: inherit; background: var(--field); color: #1f2328; outline: none;
    }
    select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(18,100,216,.12); background: #fff; }
    .table-wrap { width: 100%; max-width: 100%; max-height: 480px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    table { width: 100%; min-width: 1040px; table-layout: fixed; border-collapse: collapse; font-size: 12px; line-height: 1.45; }
    th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; background: #f3f6fa; color: #344054; z-index: 1; }
    td { overflow-wrap: anywhere; }
    td a { color: var(--primary); text-decoration: none; }
    td a:hover { text-decoration: underline; }
    .col-time { width: 150px; }
    .col-title { width: 220px; }
    .col-company { width: 170px; }
    .col-salary { width: 88px; }
    .col-hr { width: 100px; }
    .col-result { width: 88px; }
    .hint { color: var(--muted); font-size: 13px; line-height: 1.55; margin: 9px 0 0; }
    @media (max-width: 1100px) {
      .dashboard-grid {
        grid-template-columns: 1fr;
        grid-template-areas: "status" "config" "log" "history";
      }
    }
    @media (max-width: 900px) { .history-filters { grid-template-columns: 1fr 1fr; } }
    @media (max-width: 700px) { .grid, .metrics, .history-filters { grid-template-columns: 1fr; } .app-shell { padding: 18px 14px 30px; } }
  </style>
</head>
<body>
  <main class="app-shell">
    <header class="topbar">
      <h1>BOSS Java 投递控制台</h1>
      <p>控制浏览器页面、筛选在线或今日活跃 HR、查看实时日志和持久化投递历史。</p>
      <p class="storage-inline">存储位置：已处理岗位记录 boss_java_processed_jobs.txt；已投递汇总 boss_java_applied_jobs.csv。</p>
    </header>
    <div class="dashboard-grid">
        <section class="config-panel">
          <h2>任务配置</h2>
          <div class="grid">
            <div class="wide">
              <label for="targetUrl">目标 URL</label>
              <input id="targetUrl" value="__DEFAULT_URL__">
            </div>
            <div>
              <label for="limit">最多处理职位数</label>
              <input id="limit" type="number" min="1" value="10">
            </div>
            <div>
              <label for="pause">打开职位后暂停秒数</label>
              <input id="pause" type="number" min="0" step="0.5" value="1">
            </div>
            <div class="wide">
              <label for="keywords">Java 关键词</label>
              <textarea id="keywords">__DEFAULT_KEYWORDS__</textarea>
            </div>
          </div>
          <div class="toggle">
            <input id="requireHrOnline" type="checkbox" checked>
            <label for="requireHrOnline">只投递 HR 在线或今日活跃的岗位：无法识别该状态时跳过。</label>
          </div>
          <div class="toggle">
            <input id="autoApply" type="checkbox" checked>
            <label for="autoApply">开启全自动投递：脚本会直接点击“立即沟通/继续沟通”。默认开启。</label>
          </div>
          <div class="actions">
            <button id="openBtn" class="secondary">打开页面</button>
            <button id="startBtn">开始处理</button>
            <button id="stopBtn" class="danger">停止任务</button>
          </div>
          <p class="hint">先打开页面并在真实浏览器中完成登录、验证码和异常弹窗处理，然后回到这里开始处理。</p>
        </section>
        <section class="status-panel">
          <h2>状态</h2>
          <div class="metrics">
            <div class="metric"><b>运行状态</b><span id="running">-</span></div>
            <div class="metric"><b>当前职位</b><span id="currentJob">-</span></div>
            <div class="metric"><b>已处理</b><span id="processed">0</span></div>
            <div class="metric"><b>已投递</b><span id="applied">0</span></div>
            <div class="metric"><b>已跳过</b><span id="skipped">0</span></div>
            <div class="metric"><b>错误</b><span id="error">-</span></div>
          </div>
        </section>
        <section class="log-panel">
          <h2>日志</h2>
          <div id="log" class="log"></div>
        </section>
        <section class="history-panel">
          <div class="table-toolbar">
            <h2>已投递汇总</h2>
            <p>投递历史自动刷新，服务重启后会从 CSV 重新加载。</p>
          </div>
          <div class="history-filters">
            <div>
              <label for="historyFilterKeyword">关键词</label>
              <input id="historyFilterKeyword" placeholder="职位、公司、链接">
            </div>
            <div>
              <label for="historyFilterHrStatus">HR 状态</label>
              <select id="historyFilterHrStatus">
                <option value="">全部</option>
                <option value="在线">在线</option>
                <option value="今日活跃">今日活跃</option>
                <option value="刚刚活跃">刚刚活跃</option>
                <option value="-">未知</option>
              </select>
            </div>
            <div>
              <label for="historyFilterResult">结果</label>
              <select id="historyFilterResult">
                <option value="">全部</option>
                <option value="已投递">已投递</option>
              </select>
            </div>
            <div>
              <label for="historyFilterMinSalary">最低 K</label>
              <input id="historyFilterMinSalary" type="number" min="0" placeholder="如 15">
            </div>
            <div>
              <label for="historyFilterMaxSalary">最高 K</label>
              <input id="historyFilterMaxSalary" type="number" min="0" placeholder="如 30">
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <colgroup>
                <col class="col-time">
                <col class="col-title">
                <col class="col-company">
                <col class="col-salary">
                <col class="col-hr">
                <col class="col-result">
                <col>
              </colgroup>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>职位</th>
                  <th>公司</th>
                  <th>薪资</th>
                  <th>HR 状态</th>
                  <th>结果</th>
                  <th>链接</th>
                </tr>
              </thead>
              <tbody id="appliedJobsBody"></tbody>
            </table>
          </div>
          <p id="appliedJobsEmpty" class="hint">暂无投递记录</p>
        </section>
    </div>
  </main>
  <script>
    async function requestJson(url, options) {
      const response = await fetch(url, options);
      return response.json();
    }
    function payload() {
      return {
        url: document.getElementById("targetUrl").value,
        limit: document.getElementById("limit").value,
        pause: document.getElementById("pause").value,
        keywords: document.getElementById("keywords").value,
        require_hr_online: document.getElementById("requireHrOnline").checked,
        auto_apply: document.getElementById("autoApply").checked
      };
    }
    function setCell(row, value) {
      const cell = document.createElement("td");
      cell.textContent = value || "-";
      row.appendChild(cell);
    }
    function setLinkCell(row, value) {
      const cell = document.createElement("td");
      if (value) {
        const link = document.createElement("a");
        link.href = value;
        link.textContent = value;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        cell.appendChild(link);
      } else {
        cell.textContent = "-";
      }
      row.appendChild(cell);
    }
    let latestAppliedJobs = [];
    function parseSalaryRange(value) {
      const matches = String(value || "").match(/\d+(?:\.\d+)?/g);
      if (!matches || !matches.length) return null;
      const numbers = matches.map(Number);
      return {min: numbers[0], max: numbers[numbers.length - 1]};
    }
    function filterAppliedJobs(jobs) {
      const keyword = document.getElementById("historyFilterKeyword").value.trim().toLowerCase();
      const hrStatus = document.getElementById("historyFilterHrStatus").value;
      const result = document.getElementById("historyFilterResult").value;
      const minSalary = Number(document.getElementById("historyFilterMinSalary").value || 0);
      const maxSalary = Number(document.getElementById("historyFilterMaxSalary").value || 0);
      return (jobs || []).filter((job) => {
        const haystack = [job.title, job.company, job.url, job.salary, job.hr_status, job.result].join(" ").toLowerCase();
        if (keyword && !haystack.includes(keyword)) return false;
        if (hrStatus && String(job.hr_status || "-") !== hrStatus) return false;
        if (result && String(job.result || "") !== result) return false;
        if (minSalary || maxSalary) {
          const salaryRange = parseSalaryRange(job.salary);
          if (!salaryRange) return false;
          if (minSalary && salaryRange.max < minSalary) return false;
          if (maxSalary && salaryRange.min > maxSalary) return false;
        }
        return true;
      });
    }
    function renderAppliedJobs(jobs) {
      const body = document.getElementById("appliedJobsBody");
      const filteredJobs = filterAppliedJobs(jobs);
      body.replaceChildren();
      for (const job of filteredJobs) {
        const row = document.createElement("tr");
        setCell(row, job.applied_at);
        setCell(row, job.title);
        setCell(row, job.company);
        setCell(row, job.salary);
        setCell(row, job.hr_status);
        setCell(row, job.result);
        setLinkCell(row, job.url);
        body.appendChild(row);
      }
      document.getElementById("appliedJobsEmpty").textContent = (jobs || []).length ? "没有符合筛选条件的投递记录" : "暂无投递记录";
      document.getElementById("appliedJobsEmpty").hidden = Boolean(filteredJobs.length);
    }
    async function refreshStatus() {
      const data = await requestJson("/api/status");
      document.getElementById("running").textContent = data.running ? "运行中" : "空闲";
      document.getElementById("currentJob").textContent = data.current_job || "-";
      document.getElementById("processed").textContent = data.processed;
      document.getElementById("applied").textContent = data.applied;
      document.getElementById("skipped").textContent = data.skipped;
      document.getElementById("error").textContent = data.error || "-";
      const logBox = document.getElementById("log");
      logBox.textContent = (data.logs || []).join("\n");
      logBox.scrollTop = logBox.scrollHeight;
      latestAppliedJobs = data.applied_jobs || [];
      renderAppliedJobs(latestAppliedJobs);
      document.getElementById("openBtn").disabled = data.running;
      document.getElementById("startBtn").disabled = data.running;
      document.getElementById("stopBtn").disabled = !data.running;
    }
    document.getElementById("openBtn").addEventListener("click", async () => {
      await requestJson("/api/open", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload())
      });
      refreshStatus();
    });
    document.getElementById("startBtn").addEventListener("click", async () => {
      await requestJson("/api/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload())
      });
      refreshStatus();
    });
    document.getElementById("stopBtn").addEventListener("click", async () => {
      await requestJson("/api/stop", {method: "POST"});
      refreshStatus();
    });
    for (const id of ["historyFilterKeyword", "historyFilterHrStatus", "historyFilterResult", "historyFilterMinSalary", "historyFilterMaxSalary"]) {
      document.getElementById(id).addEventListener("input", () => renderAppliedJobs(latestAppliedJobs));
      document.getElementById(id).addEventListener("change", () => renderAppliedJobs(latestAppliedJobs));
    }
    refreshStatus();
    setInterval(refreshStatus, 1200);
  </script>
</body>
</html>
"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the BOSS Java apply web panel.")
    parser.add_argument("--host", default=DEFAULT_PANEL_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PANEL_PORT)
    return parser


def json_bytes(data: dict[str, Any]) -> bytes:
    return dumps(data, ensure_ascii=False).encode("utf-8")


class BossPanelHandler(BaseHTTPRequestHandler):
    state = JobPanelState()
    worker: Thread | None = None
    browser_page = None
    browser_config: BossApplyConfig | None = None

    def do_GET(self) -> None:
        if self.path == "/":
            self._send_html(render_index_html())
        elif self.path == "/api/status":
            self._send_json(self.state.snapshot())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/open":
            self._handle_open()
        elif self.path == "/api/start":
            self._handle_start()
        elif self.path == "/api/stop":
            self.state.request_stop()
            self._send_json({"ok": True})
        else:
            self.send_error(404)

    def _handle_open(self) -> None:
        if self.state.snapshot()["running"]:
            self._send_json({"ok": False, "error": "任务运行中，不能重新打开页面。"}, status=409)
            return
        try:
            config = parse_start_payload(self._read_json())
            page = open_page_with_reconnect(self.__class__.browser_page, config.url)
            self.__class__.browser_page = page
            self.__class__.browser_config = config
        except Exception as exc:
            self.__class__.browser_page = None
            self.state.set_error(f"打开页面失败：{exc}")
            self._send_json({"ok": False, "error": str(exc)}, status=500)
            return
        self.state.log(f"{datetime.now().strftime('%H:%M:%S')} 已打开 BOSS 页面。请在浏览器中完成登录，然后点击开始处理。")
        self._send_json({"ok": True})

    def _handle_start(self) -> None:
        if self.state.snapshot()["running"]:
            self._send_json({"ok": False, "error": "任务已在运行。"}, status=409)
            return
        try:
            payload = self._read_json()
            config = parse_start_payload(payload)
        except (ValueError, TypeError, JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        self.state.reset_for_run()
        self.state.log("任务已启动，正在控制 BOSS 页面。")
        if config.auto_apply:
            self.state.log("全自动投递已开启：脚本会自动点击沟通/投递按钮。")
        else:
            self.state.log("人工模式已开启：脚本只打开匹配职位，不会自动点击沟通/投递按钮。")

        self.__class__.worker = Thread(target=self._run_worker, args=(config,), daemon=True)
        self.__class__.worker.start()
        self._send_json({"ok": True})

    def _run_worker(self, config: BossApplyConfig) -> None:
        page = self.__class__.browser_page
        try:
            run_with_config(
                config,
                page=page,
                open_page=page is None,
                logger=self._log_from_worker,
                confirm_apply=lambda job: config.auto_apply,
                stop_requested=lambda: self.state.snapshot()["stop_requested"],
                on_stats=self.state.apply_stats,
            )
        except Exception as exc:
            self.state.set_error(f"任务失败：{exc}")
        finally:
            self.state.finish()

    def _log_from_worker(self, message: str) -> None:
        self.state.log(f"{datetime.now().strftime('%H:%M:%S')} {message}")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return loads(self.rfile.read(length).decode("utf-8"))

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = DEFAULT_PANEL_HOST, port: int = DEFAULT_PANEL_PORT) -> None:
    server = ThreadingHTTPServer((host, port), BossPanelHandler)
    print(f"BOSS Java apply panel: http://{host}:{port}")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
