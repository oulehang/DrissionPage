# Concurrent Sticker Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent task list that can run up to ten isolated sticker-generation jobs concurrently in separate ChatGPT tabs.

**Architecture:** Introduce focused task-domain classes in `sticker_tasks.py`, while keeping prompt construction and HTTP serving in `chatgpt_image_web_app.py`. A shared browser page creates one `ChromiumTab` per running task; every task owns its state, cancellation event, client, run directory, logs, metadata, and images. The browser UI moves from global status endpoints to list/detail task endpoints.

**Tech Stack:** Python 3, `ThreadingHTTPServer`, `threading`, DrissionPage `ChromiumPage`/`ChromiumTab`, Pillow, vanilla HTML/CSS/JavaScript, `unittest`.

---

## File map

- Create `sticker_tasks.py`: task states, `GenerationTask`, persistence, scheduling, concurrency limits, stop/retry/delete behavior.
- Modify `chatgpt_web_cli.py`: allow `ChatGPTWebClient` to operate an injected tab without creating another browser.
- Modify `chatgpt_image_web_app.py`: task-aware generation pipeline, task APIs, per-task files, shared browser factory, task-aware download and WeChat sync, task-list UI.
- Create `tests/test_sticker_tasks.py`: deterministic scheduler, state, persistence, isolation, stop/retry/delete tests.
- Create `tests/test_task_api.py`: HTTP handler routing and response-shape tests without a real browser.
- Modify `tests/test_chatgpt_web_cli.py`: injected-tab coverage.
- Modify `tests/test_run_batch_reference_images.py`: replace global `STATE` assumptions with an explicit task.

### Task 1: Inject an existing browser tab into `ChatGPTWebClient`

**Files:**
- Modify: `chatgpt_web_cli.py:49-63`
- Modify: `tests/test_chatgpt_web_cli.py`

- [ ] **Step 1: Write the failing injected-tab test**

Add:

```python
class InjectedPage:
    pass


class ChatGPTWebClientConstructionTests(unittest.TestCase):
    def test_existing_page_is_used_without_creating_chromium_page(self):
        page = InjectedPage()

        client = ChatGPTWebClient(page=page, timeout=45, image_dir="custom-images")

        self.assertIs(client.page, page)
        self.assertEqual(client.timeout, 45)
        self.assertEqual(client.image_dir, Path("custom-images"))
```

Also import `Path` in the test.

- [ ] **Step 2: Run the test and verify failure**

Run:

```powershell
python -m unittest tests.test_chatgpt_web_cli.ChatGPTWebClientConstructionTests -v
```

Expected: `TypeError` because `page` is not accepted.

- [ ] **Step 3: Implement page injection**

Change the constructor to:

```python
def __init__(
    self,
    user_data_path="chatgpt_profile",
    address=None,
    timeout=180,
    image_dir="chatgpt_images",
    page=None,
):
    if page is not None:
        self.page = page
    elif address:
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
```

- [ ] **Step 4: Run the client tests**

Run:

```powershell
python -m unittest tests.test_chatgpt_web_cli -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add chatgpt_web_cli.py tests/test_chatgpt_web_cli.py
git commit -m "feat: support task-specific ChatGPT tabs"
```

### Task 2: Build the task model, persistence, and ten-slot scheduler

**Files:**
- Create: `sticker_tasks.py`
- Create: `tests/test_sticker_tasks.py`

- [ ] **Step 1: Write failing scheduler and isolation tests**

Create tests using a blocking runner:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from sticker_tasks import TaskManager


class BlockingRunner:
    def __init__(self):
        self.started = []
        self.releases = {}

    def __call__(self, task):
        self.started.append(task.id)
        release = self.releases.setdefault(task.id, Event())
        release.wait(2)


class TaskManagerTests(unittest.TestCase):
    def test_only_ten_tasks_run_and_eleventh_is_queued(self):
        with TemporaryDirectory() as tmp:
            runner = BlockingRunner()
            manager = TaskManager(Path(tmp), runner, max_concurrency=10)
            tasks = [manager.create_task({"theme": f"主题{i}"}) for i in range(11)]

            self.assertTrue(manager.wait_until(lambda: len(runner.started) == 10))
            self.assertEqual("queued", tasks[10].snapshot()["state"])

            runner.releases[tasks[0].id].set()
            self.assertTrue(manager.wait_until(lambda: tasks[10].id in runner.started))

    def test_tasks_have_distinct_output_directories_and_state(self):
        with TemporaryDirectory() as tmp:
            manager = TaskManager(Path(tmp), lambda task: None)
            first = manager.create_task({"theme": "甲"})
            second = manager.create_task({"theme": "乙"})

            self.assertNotEqual(first.output_dir, second.output_dir)
            first.append_log("only first")
            self.assertEqual([], second.snapshot()["log"])
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```powershell
python -m unittest tests.test_sticker_tasks -v
```

Expected: module import fails because `sticker_tasks.py` does not exist.

- [ ] **Step 3: Implement task states and `GenerationTask`**

Create `GenerationTask` with:

```python
ACTIVE_STATES = {"queued", "running", "stopping", "syncing"}
RETRYABLE_STATES = {"stopped", "error", "interrupted"}


class TaskCancelled(RuntimeError):
    pass


class GenerationTask:
    def __init__(self, task_id, root_dir, params, state="queued", created_at=None):
        self.id = task_id
        self.root_dir = Path(root_dir)
        self.output_dir = self.root_dir / task_id
        self.params = dict(params)
        self.state = state
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.updated_at = self.created_at
        self.done = 0
        self.total = 0
        self.current = ""
        self.error = ""
        self.log = []
        self.images = []
        self.metadata = None
        self.hot_theme = None
        self.run_id = ""
        self.worker = None
        self.tab = None
        self.client = None
        self.hidden = False
        self.cancel_event = Event()
        self.lock = RLock()
        self.output_dir.mkdir(parents=True, exist_ok=True)
```

Implement `summary()`, `snapshot()`, `append_log()`, `set_state()`,
`raise_if_cancelled()`, `begin_run()`, and JSON serialization methods. Snapshots
must copy mutable values while holding the task lock.

- [ ] **Step 4: Implement atomic persistence**

Use one `task.json` per task:

```python
def atomic_write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
```

Persist after meaningful state, metadata, image, and error changes. On load,
convert `queued`, `running`, `stopping`, and `syncing` to `interrupted`.

- [ ] **Step 5: Implement `TaskManager` scheduling**

Required public methods:

```python
create_task(params)
list_tasks()
get_task(task_id)
stop_task(task_id)
retry_task(task_id)
delete_task(task_id)
wait_until(predicate, timeout=2.0)
```

Scheduling rules:

```python
def _dispatch_locked(self):
    running = sum(task.state == "running" for task in self.tasks.values())
    available = self.max_concurrency - running
    queued = [
        task for task in self.tasks.values()
        if task.state == "queued" and not task.hidden
    ][:available]
    for task in queued:
        task.state = "running"
        task.worker = Thread(target=self._execute, args=(task,), daemon=True)
        task.worker.start()
```

`_execute()` calls the injected runner and in `finally` releases the slot,
persists the task, and dispatches queued work. It must preserve `done`,
`stopped`, and `error` states already assigned by the runner.

- [ ] **Step 6: Add stop, retry, delete, and restart tests**

Cover:

```python
def test_queued_task_stops_immediately()
def test_running_task_moves_to_stopping_until_runner_returns()
def test_retry_resets_progress_but_preserves_previous_images()
def test_delete_hides_running_task_and_keeps_output_directory()
def test_loading_active_task_marks_it_interrupted()
def test_runner_failure_only_marks_that_task_error()
```

- [ ] **Step 7: Run task tests**

Run:

```powershell
python -m unittest tests.test_sticker_tasks -v
```

Expected: all task tests pass without opening Chromium.

- [ ] **Step 8: Commit**

```powershell
git add sticker_tasks.py tests/test_sticker_tasks.py
git commit -m "feat: add persistent concurrent task scheduler"
```

### Task 3: Make generation and image preparation task-scoped

**Files:**
- Modify: `chatgpt_image_web_app.py:1234-1628`
- Modify: `tests/test_run_batch_reference_images.py`
- Create: `tests/test_task_generation.py`

- [ ] **Step 1: Write failing task-isolation generation tests**

Use two explicit fake tasks and fake clients. Assert:

```python
self.assertNotEqual(first_run_dir, second_run_dir)
self.assertEqual(["first log"], first.snapshot()["log"])
self.assertEqual([], second.snapshot()["log"])
self.assertIs(first.client.page, first.tab)
self.assertIs(second.client.page, second.tab)
```

Add a cancellation test where the first `ask()` sets `cancel_event`; assert no
second image prompt is sent and the task becomes `stopped`.

- [ ] **Step 2: Run tests and verify old global-state API fails**

Run:

```powershell
python -m unittest tests.test_task_generation -v
```

Expected: failure because generation still reads `STATE`.

- [ ] **Step 3: Add task-aware path helpers**

Replace implicit global directory helpers with explicit arguments:

```python
def task_run_dir(task):
    return task.output_dir / "runs" / task.run_id


def prepared_dir(task):
    path = task_run_dir(task) / "wechat_ready"
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_dir(task):
    path = task_run_dir(task) / "source"
    path.mkdir(parents=True, exist_ok=True)
    return path
```

Update `image_url`, `prepare_image_for_wechat`, and `split_sprite_sheet` to
accept `task`. URLs must be `/task-images/<task_id>/<relative-path>`.

- [ ] **Step 4: Refactor `run_batch` to accept a task and client**

Use:

```python
def run_batch(task, client):
    params = task.params
    theme = params["theme"]
    templates = params.get("templates") or []
    mode = params.get("mode", "sprite24")
```

All former `STATE` reads and writes must become task methods or assignments
under `task.lock`. Check `task.raise_if_cancelled()` before copy generation,
before every prompt, and before/after image conversion. Do not close the tab on
successful completion because the accepted design retains completed tabs.

- [ ] **Step 5: Add shared-browser task runner**

In `chatgpt_image_web_app.py`, create:

```python
class BrowserTaskRunner:
    def __init__(self, config):
        self.config = config
        self.lock = Lock()
        self.page = None

    def _browser(self):
        with self.lock:
            if self.page is None:
                self.page = ChatGPTWebClient(
                    user_data_path=self.config["user_data_path"],
                    address=self.config["address"],
                    timeout=self.config["timeout"],
                    image_dir=self.config["image_dir"],
                ).page
            return self.page

    def __call__(self, task):
        tab = self._browser().new_tab(CHATGPT_URL, background=True)
        task.tab = tab
        task.client = ChatGPTWebClient(
            page=tab,
            timeout=self.config["timeout"],
            image_dir=source_dir(task),
        )
        run_batch(task, task.client)
```

The runner catches no task exceptions; `TaskManager._execute()` owns final
state/error handling.

- [ ] **Step 6: Update existing reference-image test**

Construct a `GenerationTask`, assign a `run_id`, and call
`run_batch(task, bot)`. Preserve the existing assertion that reference images
are sent only to copy generation.

- [ ] **Step 7: Run generation tests**

Run:

```powershell
python -m unittest tests.test_run_batch_reference_images tests.test_task_generation -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add chatgpt_image_web_app.py tests/test_run_batch_reference_images.py tests/test_task_generation.py
git commit -m "refactor: isolate sticker generation by task"
```

### Task 4: Add task HTTP APIs, downloads, and serialized WeChat sync

**Files:**
- Modify: `chatgpt_image_web_app.py:1918-2180`
- Create: `tests/test_task_api.py`

- [ ] **Step 1: Write failing API response-shape tests**

Create a handler test harness that injects a fake manager. Cover:

```python
def test_get_tasks_returns_summaries_without_logs_or_images()
def test_get_task_returns_full_snapshot()
def test_create_task_returns_201_and_task_id()
def test_stop_retry_delete_route_to_selected_task()
def test_unknown_task_returns_404()
def test_download_contains_only_selected_task_images()
```

- [ ] **Step 2: Run tests and verify routes are missing**

Run:

```powershell
python -m unittest tests.test_task_api -v
```

Expected: 404 or missing manager failures.

- [ ] **Step 3: Add route parsing and manager-backed endpoints**

Implement:

```text
POST   /api/tasks
GET    /api/tasks
GET    /api/tasks/<id>
POST   /api/tasks/<id>/stop
POST   /api/tasks/<id>/retry
DELETE /api/tasks/<id>
POST   /api/tasks/<id>/sync_wechat
GET    /api/tasks/<id>/download
GET    /task-images/<id>/<path>
```

`POST /api/tasks` validates non-empty normalized theme and stores every field
currently sent to `/api/start`.

- [ ] **Step 4: Make image serving task-safe**

Resolve requested paths under the selected task directory and reject traversal:

```python
candidate = (task.output_dir / relative_path).resolve()
base = task.output_dir.resolve()
if candidate != base and base not in candidate.parents:
    self.send_error(404)
    return
```

- [ ] **Step 5: Serialize WeChat sync**

Add one global `WECHAT_SYNC_LOCK = Lock()`. `sync_task_wechat(task)` acquires it
non-blocking; if unavailable return HTTP 409. It uses only
`task.snapshot()["metadata"]` and the selected task's image paths. Generation
slots remain unaffected.

- [ ] **Step 6: Keep compatibility endpoints temporarily**

Map `/api/start` to task creation and return `{"ok": true, "task_id": ...}`.
Do not retain a mutable global “current task”. `/api/status` may return the most
recent visible task only for compatibility until the UI is migrated.

- [ ] **Step 7: Run API tests**

Run:

```powershell
python -m unittest tests.test_task_api -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add chatgpt_image_web_app.py tests/test_task_api.py
git commit -m "feat: expose concurrent sticker task APIs"
```

### Task 5: Replace the single-task UI with list/detail task management

**Files:**
- Modify: `chatgpt_image_web_app.py:206-985`

- [ ] **Step 1: Add a lightweight HTML contract test**

In `tests/test_task_api.py`, assert `INDEX_HTML` contains:

```python
self.assertIn('id="taskList"', app.INDEX_HTML)
self.assertIn('id="newTaskBtn"', app.INDEX_HTML)
self.assertIn("/api/tasks", app.INDEX_HTML)
self.assertIn("selectedTaskId", app.INDEX_HTML)
self.assertNotIn('fetch("/api/status")', app.INDEX_HTML)
```

- [ ] **Step 2: Run contract test and verify failure**

Run:

```powershell
python -m unittest tests.test_task_api.TaskPageContractTests -v
```

Expected: missing task-list elements.

- [ ] **Step 3: Implement the confirmed A layout**

Keep the existing form fields, but place them in a new-task panel. Add:

```html
<div class="task-shell">
  <aside class="task-sidebar">
    <button id="newTaskBtn">＋ 新建任务</button>
    <div id="taskList"></div>
  </aside>
  <main class="task-detail" id="taskDetail">
    <!-- selected task status, controls, metadata, log, gallery -->
  </main>
</div>
```

On narrow screens stack the sidebar above detail.

- [ ] **Step 4: Implement list and selected-detail polling**

JavaScript state:

```javascript
let selectedTaskId = localStorage.getItem("selectedTaskId") || "";
let taskListKey = "";
let taskDetailKey = "";
```

Poll `/api/tasks` every 1200 ms. Fetch `/api/tasks/<id>` only for the selected
task. Store selection in `localStorage`. If the selected task disappears,
select the first visible task.

- [ ] **Step 5: Wire task operations**

Buttons call:

```javascript
postJson(`/api/tasks/${selectedTaskId}/stop`)
postJson(`/api/tasks/${selectedTaskId}/retry`)
fetch(`/api/tasks/${selectedTaskId}`, {method: "DELETE"})
window.location = `/api/tasks/${selectedTaskId}/download`
postJson(`/api/tasks/${selectedTaskId}/sync_wechat`, {metadata})
```

Show controls only when legal for the selected task state. Starting a task must
not disable the form globally; after creation select the returned task.

- [ ] **Step 6: Run HTML/API contract tests**

Run:

```powershell
python -m unittest tests.test_task_api -v
```

Expected: all pass.

- [ ] **Step 7: Manually verify local UI**

Run:

```powershell
python chatgpt_image_web_app.py --port 8765
```

Verify:

- left task list and right detail render;
- new task remains available while another task runs;
- selection changes logs/gallery without overwriting another task;
- queued/running/stopped/error/done badges render;
- buttons match the selected task state.

- [ ] **Step 8: Commit**

```powershell
git add chatgpt_image_web_app.py tests/test_task_api.py
git commit -m "feat: add concurrent task list interface"
```

### Task 6: Run full verification and harden concurrent behavior

**Files:**
- Modify if needed: `sticker_tasks.py`
- Modify if needed: `chatgpt_image_web_app.py`
- Modify if needed: tests under `tests/`

- [ ] **Step 1: Run the complete test suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run syntax checks**

Run:

```powershell
python -m py_compile chatgpt_web_cli.py sticker_tasks.py chatgpt_image_web_app.py
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run a deterministic ten-task smoke test**

Use a fake runner script that records concurrent executions, creates 11 tasks,
and asserts maximum observed concurrency is 10 and the eleventh starts after a
release. Expected output:

```text
max_concurrency=10
queued_started_after_release=True
```

- [ ] **Step 4: Check the diff for accidental global-state coupling**

Run:

```powershell
rg -n "STATE\\.(images|metadata|log|worker|state|current|done|total)" chatgpt_image_web_app.py
```

Expected: no generation-path dependency on the old singleton. Any compatibility
wrapper must only read from `TaskManager`.

- [ ] **Step 5: Check formatting and whitespace**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 6: Commit final hardening**

```powershell
git add chatgpt_image_web_app.py chatgpt_web_cli.py sticker_tasks.py tests
git commit -m "test: verify ten concurrent sticker tasks"
```
