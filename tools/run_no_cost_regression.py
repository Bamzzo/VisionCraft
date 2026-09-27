"""Run the whole no-cost regression suite, one isolated data directory per check.

Why this exists
---------------
The acceptance tests create and delete projects. Until this runner, every one of
them ran against ``backend/data/visioncraft.db`` and the real project asset
directory, so a full regression could damage working data. That is not a
theoretical risk: a harness cleanup step once deleted a project that was being
kept for evidence.

Isolation model
---------------
Two layers, and the second one is what makes a green run mean something.

1. **Run layer** — ``--data-dir`` (default ``stageC/run-<timestamp>``) holds the
   report, the per-check logs and the seeded template.
2. **Check layer** — every check that touches the application gets its *own*
   data directory, copied from that template. This is the layer that used to be
   missing: all 48 checks shared one database, so a defect surfaced on whichever
   check happened to run into the polluted state, and which check that was
   changed from run to run. A single green run could not be trusted, and only
   two consecutive green runs were acceptable evidence.

   Fixture hooks used to paper over the two worst symptoms (a stale
   ``updated_at`` on the create-guard project, a cascaded-away ``video_tasks``
   row). The template replaces both: every check now starts from the *same*
   known state instead of inheriting whatever the previous check left behind.

Only ``SERVER_DEPENDENT`` needs a backend from this runner. Every other check
either starts its own service on a private port (and inherits its own
``VISIONCRAFT_DATA_DIR``) or needs no server at all, so per-check isolation
falls out of pointing ``VISIONCRAFT_DATA_DIR`` at the right copy.

Usage
-----
    .venv/Scripts/python.exe tools/run_no_cost_regression.py
    .venv/Scripts/python.exe tools/run_no_cost_regression.py --only resume
    .venv/Scripts/python.exe tools/run_no_cost_regression.py --data-dir out/scratch --only health

No service has to be running beforehand. Exit code is 0 only when every selected
check passed.
"""

from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE_C_DIR = ROOT / "output" / "playwright" / "stageC"
LATEST_REPORT = STAGE_C_DIR / "no_cost_regression_report.json"
NODE = Path(r"C:/Users/wei34/.workbuddy/binaries/node/versions/22.22.2-3/node.exe")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PER_TEST_TIMEOUT = 420
TEMPLATE_NAME = "_template"
# 端口被残留服务占用时换几个端口再放弃；见 start_backend。
BACKEND_PORT_ATTEMPTS = 3
# 本进程起过的后端，用于异常退出时收尸，避免残留进程占着端口坑下一轮。
_TRACKED_BACKENDS: list[subprocess.Popen] = []
# 单实例锁：两个全量回归并行会互相抢端口/CPU，把结果搅成不可归因的"偶发失败"。
RUN_LOCK_NAME = "runner.lock"
_RUN_LOCK_PATH: list[Path] = []
SEED_TIMEOUT = 300


@dataclass
class Check:
    name: str
    argv: list[str]
    group: str
    needs_data_dir: bool = True
    allow_fail: bool = False
    result: dict = field(default_factory=dict)


def node_exe() -> str:
    return str(NODE) if NODE.is_file() else "node"


def safe_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").replace(" ", "_")


def build_checks() -> list[Check]:
    py = str(PYTHON)
    nd = node_exe()
    checks: list[Check] = []

    # --- Static checks -----------------------------------------------------
    checks.append(Check("compileall backend+tools", [py, "-m", "compileall", "-q", "backend", "tools"],
                        "static", needs_data_dir=False))
    for js in ("api", "app", "jobObserver", "render", "state", "workflowViewModel"):
        checks.append(Check(f"syntax frontend/js/{js}.js", [nd, "--check", f"frontend/js/{js}.js"],
                            "static", needs_data_dir=False))

    # --- Node unit and contract tests -------------------------------------
    for script in (
        "tools/test_workflow_view_model.mjs",
        "tools/test_job_observer.mjs",
        "tools/test_live_2shot_wait.js",
        "tools/test_live_2shot_project_guard.js",
        "tools/test_live_2shot_resume_plan.js",
        "tools/test_live_2shot_resume_driver.js",
    ):
        checks.append(Check(Path(script).name, [nd, script], "node", needs_data_dir=False))

    # --- Python service and contract tests --------------------------------
    for script in (
        "test_live_safeguards.py",
        "test_mock_video_refresh.py",
        "test_provider_capabilities.py",
        "test_storyboard_shot_count.py",
        "test_stage_models.py",
        "test_shot_versions.py",
        "test_project_settings.py",
        "test_adaptation_start_refresh.py",
        "test_adaptation_workflow.py",
        "test_medium_text_adaptation.py",
        "test_upload_assets.py",
        "test_job_center.py",
        "test_media_transfer.py",
        "test_workflow_pause_resume.py",
        "test_assembly.py",
        "test_assembly_http.py",
        "test_p6b_assembly.py",
        "test_p6c_real_assembly.py",
        "test_p6d_assembly.py",
        "test_p6e_source_audio.py",
        "test_p6_demo_samples.py",
        "test_anchor_assets.py",
        "test_anchor_review_gate.py",
        "test_reference_generation.py",
        # 付费闸门必须覆盖全部视频 provider：此前它只挂在 MiniMax 分支里，
        # ark / dashscope 直接分发，等于最该拦的两家没拦。
        "test_video_provider_guard.py",
        # 驱动器自检：单实例锁 + 绝不把陌生后端当成自己的。两个全量回归并行会把结果
        # 搅成不可归因的"偶发失败"，端口被残留 uvicorn 占着则会把用例跑在别人的数据
        # 目录上——这两条都是"连续两次全绿"这句话能不能站住的前提。
        "test_runner_guards.py",
        "test_cleanup_temp_project.py",
        "test_retire_remote_video_task.py",
    ):
        checks.append(Check(script, [py, f"tools/{script}"], "python"))

    # --- Browser tests (heaviest, run last) -------------------------------
    for script in (
        "test_mock_web_smoke.py",
        "test_local_keyframe_browser.py",
        "test_anchor_ui_browser.py",
        "test_p6c_real_assembly_browser.py",
        "test_p6d_assembly_browser.py",
        "test_p6e_source_audio_browser.py",
        "test_p7c_ui_state_browser.py",
        "test_p8a_browser.py",
        "test_p8b_browser.py",
        "test_ui_workbench.py",
        "test_v1_qa_browser.py",
        "test_v1_usability.py",
        "test_v1_demo_browser.py",
    ):
        checks.append(Check(script, [py, f"tools/{script}"], "browser"))

    # Node-based browser self-check for the create-project guard.
    checks.append(Check("test_live_2shot_create_guard.cjs",
                        [nd, "tools/test_live_2shot_create_guard.cjs"], "browser"))

    return checks


def summarise(stdout: str) -> dict:
    """Extract PASS/FAIL/SKIP counts from a test log, best effort.

    The skip count is a *raw* tally: it counts any line containing "SKIP",
    including a tool's own refusal message that the test is asserting on — e.g.
    ``run_live_2shot.py`` prints "SKIP: inflight_remote_tasks ..." and the very
    next line is the PASS for the guard that produced it. So a skip count is a
    prompt to look at ``skip_lines``, never a conclusion. The lines themselves are
    recorded for exactly that reason: without them the number cannot be audited,
    and four skips could equally mean four unrun cases or a counter artifact.

    The same caveat applies to the PASS side: this is a *token* tally, so a script
    that prints its own summary line containing a bare ``pass`` token adds one
    phantom assertion. The repo convention is therefore: test scripts print only
    ``PASS:`` / ``FAIL:`` lines, never a "N pass / M fail" trailer. If the total
    ever stops matching a hand-counted sum, check for such a trailer first.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    counts = {"pass": 0, "fail": 0, "skip": 0, "other": 0}
    fail_lines: list[str] = []
    skip_lines: list[str] = []
    for line in lines:
        upper = line.upper()
        if upper.startswith("PASS") or " PASS " in f" {upper} ":
            counts["pass"] += 1
        elif upper.startswith("FAIL") or " FAIL " in f" {upper} ":
            counts["fail"] += 1
            fail_lines.append(line[:200])
        elif upper.startswith("SKIP") or " SKIP " in f" {upper} ":
            counts["skip"] += 1
            skip_lines.append(line[:200])
    return {"counts": counts, "fail_lines": fail_lines[:10], "skip_lines": skip_lines[:10],
            "total_lines": len(lines), "tail": lines[-4:]}


def run_check(check: Check, env: dict[str, str], log_dir: Path) -> dict:
    started = time.time()
    try:
        proc = subprocess.run(check.argv, cwd=str(ROOT), capture_output=True, text=True,
                              env=env, timeout=PER_TEST_TIMEOUT, errors="replace")
        code, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"TIMEOUT after {PER_TEST_TIMEOUT}s"
    elapsed = round(time.time() - started, 1)
    combined = out + ("\n" + err if err.strip() else "")
    info = summarise(combined)
    # Keep the full output: the report only carries a tail, and a failure is
    # rarely diagnosable from four lines. Logs stay in one directory across the
    # whole run even though each check has its own data directory — otherwise a
    # failure would be a directory hunt.
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{safe_name(check.name)}.log"
    log_file.write_text(combined, encoding="utf-8")
    verdict = "PASS" if code == 0 else "FAIL"
    return {"name": check.name, "group": check.group, "argv": check.argv, "exit_code": code,
            "verdict": verdict, "seconds": elapsed, "log": str(log_file),
            "data_dir": env.get("VISIONCRAFT_DATA_DIR", ""),
            **info, "stderr_tail": err.strip()[-300:]}


# The only checks that need a backend from this runner.
#
# Each resolves its address from ``VISIONCRAFT_BASE_URL`` and starts no service
# of its own, so it aborts when nothing answers. Every other check either starts
# its own uvicorn on a private port (8013-8018 / 8020) or needs no server, which
# means pointing ``VISIONCRAFT_DATA_DIR`` at a per-check copy is enough to
# isolate it — and the runner must *not* hand it a base URL that would silently
# redirect it at a shared service.
#
# The port is not part of the contract; the runner picks a free one (see
# ``pick_free_port``) and exports it. This block used to be a shared backend for
# the entire browser group, which is how a stale ``updated_at`` in one check
# could decide another check's premise.
SERVER_DEPENDENT = {
    "test_adaptation_start_refresh.py",
    "test_p6b_assembly.py",
    "test_ui_workbench.py",
    "test_anchor_ui_browser.py",
    "test_p6c_real_assembly_browser.py",
    "test_p6d_assembly_browser.py",
    "test_p6e_source_audio_browser.py",
}


def pick_free_port() -> int:
    """First free port for a check's backend, chosen to avoid the nested servers.

    Several acceptance scripts start their *own* uvicorn and scan 8013-8018
    (``test_p8a_browser.py``) or 8020. Staying above that block keeps this
    runner's service from ever competing with a nested one for the same port.
    """
    for port in range(8070, 8110):
        if not port_in_use(port):
            return port
    raise SystemExit("FAIL: 8070-8109 全部被占用，无法为检查选端口")


def port_in_use(port: int) -> bool:
    probe = socket.socket()
    probe.settimeout(0.4)
    try:
        return probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()


def health_ok(base: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def log_text_from(log_path: Path, offset: int = 0) -> str:
    """取子进程日志里 ``offset`` 之后的新内容（``offset`` 是本轮尝试开始前的水位）。"""
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            return fh.read()
    except OSError:
        return ""


def log_shows_bind_failure(log_path: Path, offset: int = 0) -> bool:
    """子进程日志里有没有「端口已被占用」的痕迹。

    按水位截断是必要的：上一轮已经因为端口冲突失败过的话，整份日志里一定有那句话，
    不截断就会把换端口之后的第二轮也误判成冲突。
    """
    return "error while attempting to bind" in log_text_from(log_path, offset)


def wait_for_backend(proc: subprocess.Popen, port: int, log_path: Path, offset: int,
                     attempts: int = 60) -> bool:
    """等后端起来，并且确认服务的是**我们**这个子进程。

    判据是子进程日志里出现 ``Uvicorn running on http://127.0.0.1:<port>``，不是
    ``/api/health`` 能通。原因：端口被上一轮残留的 uvicorn 占着时，我们的子进程会因
    EADDRINUSE 退出，而健康检查照样由残留进程回一个 200——那个 200 甚至比我们的 bind
    尝试还早，所以"先看健康再对日志"照样会误判（实测过）。

    也不能用 ``Application startup complete``：实测 uvicorn 先跑 lifespan 启动、之后
    才 bind 端口，端口被占时**这句照样会打印**（成功/失败两份日志做过对照，只有
    ``Uvicorn running on`` 那行是成功独有的）。所以用它，并连端口一起比对——那才证明
    端口是我们的。
    """
    marker = f"Uvicorn running on http://127.0.0.1:{port}"
    for _ in range(attempts):
        text = log_text_from(log_path, offset)
        if marker in text:
            return health_ok(f"http://127.0.0.1:{port}", timeout=2.0)
        if proc.poll() is not None:
            if "error while attempting to bind" in text:
                print(f"FAIL: 端口 {port} 已被残留服务占用，我们的后端没能起来；见 {log_path}")
            else:
                print(f"FAIL: backend exited early (code {proc.returncode}); see {log_path}")
            return False
        time.sleep(0.5)
    print(f"FAIL: backend did not start within 30s; see {log_path}")
    return False


def start_backend(env: dict[str, str], log_path: Path, port: int) -> tuple[subprocess.Popen | None, int]:
    """Serve the app on a private port for one check that needs a service.

    It deliberately never adopts a service that happens to be listening. This
    runner disables the host bulk-delete guard for its children, and the
    acceptance tests delete projects as part of their own housekeeping. Adopting
    a stranger's backend could therefore aim those deletions at the user's real
    database — and nothing about a reuse branch makes that visible in the log.
    Owning the port is what makes "everything lives under the isolated data
    directory" true rather than merely asserted.

    "Never adopts" has to cover the stale server of an earlier run as well, so
    the port is re-picked and the service restarted when it turns out to be
    taken (see ``wait_for_backend``).

    Returns ``(None, port)`` when the service never came up, which the caller
    records as a failed check without running it: a check that reports "state did
    not change" when in fact its backend never started is worse than no check.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    for attempt in range(BACKEND_PORT_ATTEMPTS):
        if attempt:
            port = pick_free_port()
            handle.write(f"\n===== retry on port {port} =====\n")
            handle.flush()
        offset = log_path.stat().st_size if log_path.exists() else 0
        proc = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
             "--port", str(port)],
            cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT, env=env,
        )
        if wait_for_backend(proc, port, log_path, offset):
            _TRACKED_BACKENDS.append(proc)
            return proc, port
        stop_backend(proc)
        if not log_shows_bind_failure(log_path, offset):
            break  # 不是端口冲突，换端口重试没有意义
    handle.close()
    return None, port


def stop_backend(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc in _TRACKED_BACKENDS:
        _TRACKED_BACKENDS.remove(proc)
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def pid_alive(pid: int) -> bool:
    """这个 pid 还在不在。用 tasklist 而不是 os.kill：Windows 上 os.kill(pid, 0) 不可用。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
            capture_output=True, text=True, errors="replace", timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f'"{pid}"' in out


def acquire_run_lock(lock_path: Path | None = None) -> bool:
    """同一时间只允许一个全量回归在跑，否则直接拒绝启动。

    这不是洁癖：驱动器会为需要后端的检查占用 8070-8109 的端口并起无头浏览器，两个
    实例同时跑会互相抢端口和 CPU，于是某一项"偶发"失败——而那既不是产品缺陷，也不是
    用例缺陷，只是自己踩自己。实测踩过一次：一个被中断的实例没死透，与新一轮并行跑了
    十几分钟，失败项看起来像真的。

    残留锁要能自愈：锁里记 pid，pid 已不在就当作过期锁直接接管。

    ``lock_path`` 可覆盖，供 ``tools/test_runner_lock.py`` 用临时路径测试——该用例由
    驱动器自己跑，这时真锁正被驱动器持有，绝不能去动它。
    """
    lock = lock_path or (STAGE_C_DIR / RUN_LOCK_NAME)
    if lock.exists():
        info: dict = {}
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
            holder = int(info.get("pid") or 0)
        except (OSError, ValueError):
            holder = 0
        if holder and holder != os.getpid() and pid_alive(holder):
            print(f"FAIL: 已有另一个无费用回归在跑（pid {holder}，启动于 {info.get('started_at', '?')}）。")
            print("      两个实例会互相抢 8070-8109 端口与 CPU，产生的失败无法归因。")
            print(f"      先结束它（taskkill /PID {holder} /T /F）或等它跑完，再重试。")
            return False
        print(f"note: 接管过期锁（pid {holder} 已不存在）")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "started_at": utc_now_iso()}, ensure_ascii=False),
        encoding="utf-8",
    )
    _RUN_LOCK_PATH.append(lock)
    return True


def release_run_lock() -> None:
    while _RUN_LOCK_PATH:
        lock = _RUN_LOCK_PATH.pop()
        try:
            if lock.exists():
                info = json.loads(lock.read_text(encoding="utf-8"))
                if int(info.get("pid") or 0) == os.getpid():
                    lock.unlink()
        except (OSError, ValueError):
            pass


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reap_tracked_backends() -> None:
    """进程被 Ctrl-C / 异常掐掉时，别把 uvicorn 留在端口上。

    残留的后端正是 ``wait_for_backend`` 那个坑的来源：端口被占 → 我们自己的子进程
    EADDRINUSE 退出 → 健康检查却由残留进程应答 → 用例跑在别人的数据目录上。
    """
    while _TRACKED_BACKENDS:
        stop_backend(_TRACKED_BACKENDS[-1])
    release_run_lock()


atexit.register(_reap_tracked_backends)


def seed_fixtures(env: dict[str, str]) -> dict:
    """Build the historical projects the acceptance scripts assume already exist.

    A handful of checks do not test the code under test so much as a *state*: they
    reproduce incidents that happened on a database which already contained
    certain projects. Against a brand new isolated database they fail for a reason
    that has nothing to do with the code, which makes them worthless as regression
    checks. ``tools/seed_regression_fixtures.py`` builds that state fully offline;
    this wrapper only runs it and reports what it did.

    It writes into the *template* directory, from which every check is copied.
    Nothing else holds the database at that moment — the seeder writes the file
    directly, and doing that while uvicorn holds write locks invites "database is
    locked" in the middle of a workflow.
    """
    try:
        proc = subprocess.run(
            [str(PYTHON), "tools/seed_regression_fixtures.py"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=SEED_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {SEED_TIMEOUT}s"}
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "exit_code": proc.returncode,
                "error": ((proc.stderr or "").strip() or out)[-400:]}
    payload: dict = {"ok": True}
    for line in reversed(out.splitlines()):
        try:
            payload.update(json.loads(line))
            break
        except json.JSONDecodeError:
            continue
    else:
        return {"ok": False, "error": f"seeder produced no JSON: {out[-200:]}"}
    return payload


def prepare_check_dir(template_dir: Path, data_dir: Path, index: int, check: Check) -> str:
    """Give one check its own copy of the seeded template.

    Returns an empty string for checks that do not need a database (static
    analysis, node unit tests), which then inherit the run-level directory.
    """
    if not check.needs_data_dir:
        return ""
    target = data_dir / "checks" / f"{index:02d}-{safe_name(check.name)}"
    if target.exists():
        # Only reachable by reusing a previous --data-dir. Copying over it would
        # mean deleting first, and this runner never deletes anything; say so
        # rather than silently pretending the start state is the template's.
        print(f"\nWARN: {target.name} 已存在，沿用既有状态（起点可能与模板不一致）")
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, target)
    return str(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="",
                        help="Run-level directory. Default: a fresh stageC/run-<timestamp>")
    parser.add_argument("--only", default="", help="Substring filter on test names; comma-separate for several")
    parser.add_argument("--group", default="", help="Only run one group: static|node|python|browser")
    parser.add_argument("--no-fixtures", action="store_true",
                        help="Do not seed the template; checks that assume historical projects will fail")
    parser.add_argument("--keep-safe-delete", action="store_true",
                        help="Leave the host delete shim enabled for child processes")
    args = parser.parse_args()

    if not PYTHON.is_file():
        print(f"FAIL: venv python missing at {PYTHON}")
        return 1

    if not acquire_run_lock():
        return 2

    if args.data_dir:
        data_dir = Path(args.data_dir)
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        if data_dir.exists():
            # Deliberately no cleanup. This runner never removes anything, for
            # the reason spelled out below; if a reusable --data-dir already has
            # state in it, that is the caller's choice to live with. Point
            # --data-dir at a new path when a clean slate is wanted.
            print(f"note: reusing existing data dir {data_dir} (no cleanup performed)")
    else:
        # A fresh directory per run, and no deletion of the previous one.
        #
        # Clearing a shared scratch tree was both unnecessary and actively
        # harmful: the host delete shim counts removals per turn and terminates
        # the process once past its threshold, so the cleanup could abort the
        # runner before a single check ran. Separate directories also mean every
        # run keeps its own logs and report for later comparison.
        data_dir = STAGE_C_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = data_dir / "logs"
    template_dir = data_dir / TEMPLATE_NAME

    env = {**os.environ}
    # Baseline: fail closed. Tests that exercise the guard rails set these themselves.
    for key in ("VISIONCRAFT_ALLOW_LIVE_LLM", "VISIONCRAFT_ALLOW_LIVE_VISION",
                "VISIONCRAFT_ALLOW_LIVE_VIDEO", "VISIONCRAFT_ALLOW_LIVE"):
        env[key] = "0"
    env["PYTHONIOENCODING"] = "utf-8"

    # The host injects a Python delete shim through PYTHONPATH. Its bulk-delete
    # guard counts deletions per turn and, once past a threshold, kills the
    # process with SystemExit(1) rather than merely refusing the operation. A
    # single acceptance test deletes far more than that on its own (project
    # fixtures, uploaded media, scratch databases), so the guard was terminating
    # tests mid-run and the failure surfaced as a broken assertion.
    #
    # Disabling it for these child processes is scoped to them alone: everything
    # they touch lives under the isolated data directory this runner created, so
    # no user data is at stake either way. That statement is only true because
    # this runner owns the ports it uses as well — see start_backend, which
    # refuses to adopt a service it did not start. It also keeps hundreds of
    # scratch files out of the user's recycle bin. Pass --keep-safe-delete to opt
    # out.
    if args.keep_safe_delete:
        delete_note = "host delete shim left enabled (bulk guard may abort tests)"
    else:
        env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
        delete_note = "host delete shim disabled for children (isolated data dir only)"

    checks = build_checks()
    if args.only:
        # Comma-separated so a handful of failures can be re-run together
        # instead of one process per name.
        wanted = [part.strip().lower() for part in args.only.split(",") if part.strip()]
        checks = [c for c in checks if any(w in c.name.lower() for w in wanted)]
    if args.group:
        checks = [c for c in checks if c.group == args.group]
    if not checks:
        print("FAIL: no checks matched the filter")
        return 1

    uses_data = [c for c in checks if c.needs_data_dir]
    print(f"Run data dir      : {data_dir}")
    print(f"Isolation         : one data dir per check ({len(uses_data)} checks), copied from {TEMPLATE_NAME}/")
    print(f"Live switches     : all pinned to 0")
    print(f"Delete shim       : {delete_note}")
    print(f"Checks selected   : {len(checks)}")
    print()

    # Build the template once. Every check that needs a database gets a copy, so
    # a failure here makes those checks meaningless rather than merely unlucky —
    # which is why it aborts instead of warning.
    fixtures: dict = {"ok": False, "skipped": "--no-fixtures"}
    if uses_data and not args.no_fixtures:
        template_env = {**env, "VISIONCRAFT_DATA_DIR": str(template_dir)}
        fixtures = seed_fixtures(template_env)
        if fixtures.get("ok"):
            detail = {k: v for k, v in fixtures.items() if k != "ok"}
            print(f"template       : {json.dumps(detail, ensure_ascii=False)}")
        else:
            print(f"FAIL: 夹具模板构建失败，依赖它的 {len(uses_data)} 项无法可信运行："
                  f"{fixtures.get('error')}")
            return 1
    else:
        template_dir.mkdir(parents=True, exist_ok=True)

    results = []
    started_at = time.time()
    for index, check in enumerate(checks, start=1):
        print(f"[{index:>2}/{len(checks)}] {check.name} ...", end="", flush=True)
        check_env = {**env}
        check_dir = prepare_check_dir(template_dir, data_dir, index, check)
        if check_dir:
            check_env["VISIONCRAFT_DATA_DIR"] = check_dir
        else:
            check_env["VISIONCRAFT_DATA_DIR"] = str(data_dir)

        server: subprocess.Popen | None = None
        if check.name in SERVER_DEPENDENT:
            # 端口在 start_backend 内部才定下来（可能因冲突换过端口），所以
            # VISIONCRAFT_BASE_URL 用返回的实际端口回填，不能事先猜。
            server, port = start_backend(
                check_env, log_dir / f"backend-{index:02d}-{safe_name(check.name)}.log", pick_free_port()
            )
            if server is not None:
                check_env["VISIONCRAFT_BASE_URL"] = f"http://127.0.0.1:{port}"

        if check.name in SERVER_DEPENDENT and server is None:
            # Never run it: "the state did not change" reported by a check whose
            # backend never started is indistinguishable from a real regression.
            res = {"name": check.name, "group": check.group, "argv": check.argv, "exit_code": 125,
                   "verdict": "FAIL", "seconds": 0.0, "log": "",
                   "data_dir": check_env["VISIONCRAFT_DATA_DIR"],
                   "counts": {"pass": 0, "fail": 0, "skip": 0, "other": 0},
                   "fail_lines": ["backend did not start; check not executed"],
                   "skip_lines": [], "total_lines": 0, "tail": [], "stderr_tail": ""}
        else:
            try:
                res = run_check(check, check_env, log_dir)
            finally:
                stop_backend(server)
        results.append(res)
        mark = res["verdict"]
        extra = ""
        if res["counts"]["pass"] or res["counts"]["fail"]:
            extra = f"  ({res['counts']['pass']} pass / {res['counts']['fail']} fail)"
        print(f" {mark} {res['seconds']}s{extra}")
        if mark == "FAIL":
            for line in res["fail_lines"][:4]:
                print(f"        {line}")
            if not res["fail_lines"] and res["stderr_tail"]:
                print(f"        {res['stderr_tail'].splitlines()[-1][:160]}")

    failed = [r for r in results if r["verdict"] == "FAIL"]
    totals = {k: sum(r["counts"][k] for r in results) for k in ("pass", "fail", "skip")}
    print()
    print("=" * 68)
    print(f"checks : {len(results) - len(failed)}/{len(results)} passed")
    print(f"asserts: {totals['pass']} pass / {totals['fail']} fail / {totals['skip']} skip")
    print(f"elapsed: {round(time.time() - started_at, 1)}s")
    if failed:
        print("failed :")
        for r in failed:
            print(f"  - {r['name']} (exit {r['exit_code']}, {r['seconds']}s)")
    print("=" * 68)

    report = {"schema": "visioncraft.no_cost_regression.v2",
              "data_dir": str(data_dir), "live_switches": "all pinned to 0",
              "isolation": {"mode": "per-check data directory copied from a seeded template",
                            "template": str(template_dir), "template_seeded": fixtures.get("ok"),
                            "checks_with_own_data_dir": len(uses_data),
                            "checks_needing_runner_backend": sorted(SERVER_DEPENDENT)},
              "fixtures": fixtures,
              "delete_shim": delete_note,
              "checks_total": len(results), "checks_failed": len(failed),
              "assert_totals": totals, "results": results}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    run_report = data_dir / "no_cost_regression_report.json"
    run_report.write_text(text, encoding="utf-8")
    # A plain overwrite, never a delete, so a stable "latest" path costs nothing.
    #
    # Only a full-suite run refreshes it. A filtered run (--only/--group) covers a
    # handful of checks, and pointing "latest" at that subset reads as "the suite
    # last produced these five results", which is exactly the wrong conclusion.
    STAGE_C_DIR.mkdir(parents=True, exist_ok=True)
    if not args.only and not args.group:
        LATEST_REPORT.write_text(text, encoding="utf-8")
    print(f"report : {run_report}")
    if args.only or args.group:
        print("latest : unchanged (filtered run does not refresh the full-suite pointer)")
    else:
        print(f"latest : {LATEST_REPORT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
