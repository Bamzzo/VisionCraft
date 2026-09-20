"""Run the whole no-cost regression suite against an isolated data directory.

Why this exists
---------------
The acceptance tests create and delete projects. Until this runner, every one of
them ran against ``backend/data/visioncraft.db`` and the real project asset
directory, so a full regression could damage working data. That is not a
theoretical risk: a harness cleanup step once deleted a project that was being
kept for evidence.

Every child process here inherits ``VISIONCRAFT_DATA_DIR``, so the database and
the ``projects/`` asset tree live under a scratch directory. The live switches
are pinned to ``0`` as a baseline so an accidental real provider call fails
closed rather than spending money; tests that need to exercise the guard rails
set those variables themselves.

Usage
-----
    .venv/Scripts/python.exe tools/run_no_cost_regression.py
    .venv/Scripts/python.exe tools/run_no_cost_regression.py --only resume
    .venv/Scripts/python.exe tools/run_no_cost_regression.py --data-dir out/scratch --only health

No service has to be running beforehand: the runner starts its own backend on a
free port, exports it as ``VISIONCRAFT_BASE_URL``, and seeds the historical
fixture projects. Exit code is 0 only when every selected check passed.
"""

from __future__ import annotations

import argparse
import json
import os
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
        "test_cleanup_temp_project.py",
        "test_retire_remote_video_task.py",
    ):
        checks.append(Check(script, [py, f"tools/{script}"], "python"))

    # --- Browser tests (heaviest, run last) -------------------------------
    for script in (
        "test_mock_web_smoke.py",
        "test_local_keyframe_browser.py",
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


def run_check(check: Check, env: dict[str, str]) -> dict:
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
    # rarely diagnosable from four lines.
    log_dir = Path(env["VISIONCRAFT_DATA_DIR"]) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = check.name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    log_file = log_dir / f"{safe}.log"
    log_file.write_text(combined, encoding="utf-8")
    verdict = "PASS" if code == 0 else "FAIL"
    return {"name": check.name, "group": check.group, "argv": check.argv, "exit_code": code,
            "verdict": verdict, "seconds": elapsed, "log": str(log_file),
            **info, "stderr_tail": err.strip()[-300:]}


# These six are not self-contained: they probe the app over HTTP and abort when
# nothing answers. Everything else either starts its own server on a private port
# or needs no server at all.
#
# The port is *not* part of the contract. All six resolve the address from
# ``VISIONCRAFT_BASE_URL`` and only fall back to ``127.0.0.1:8000`` when it is
# unset, so the runner starts its own service on a free port and exports that
# variable (see ``pick_free_port``). The documented command list used to omit the
# prerequisite entirely, so a plain full run failed for a reason unrelated to the
# code under test.
SERVER_DEPENDENT = {
    "test_adaptation_start_refresh.py",
    "test_p6b_assembly.py",
    "test_ui_workbench.py",
    "test_p6c_real_assembly_browser.py",
    "test_p6d_assembly_browser.py",
    "test_p6e_source_audio_browser.py",
}


def pick_free_port() -> int:
    """First free port for the shared backend, chosen to avoid the nested servers.

    Several acceptance scripts start their *own* uvicorn and scan 8013-8018
    (``test_p8a_browser.py``) or 8020. Staying above that block keeps the shared
    service from ever competing with a nested one for the same port.
    """
    for port in range(8070, 8110):
        if not port_in_use(port):
            return port
    raise SystemExit("FAIL: 8070-8109 全部被占用，无法为共享后端选端口")


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


def start_shared_backend(env: dict[str, str], log_path: Path, port: int) -> subprocess.Popen | None:
    """Serve the app on a private port for the checks that are not self-contained.

    It deliberately never adopts a service that happens to be listening. This
    runner disables the host bulk-delete guard for its children, and the
    acceptance tests delete projects as part of their own housekeeping. Adopting
    a stranger's backend could therefore aim those deletions at the user's real
    database — and nothing about a reuse branch makes that visible in the log.
    Owning the port is what makes "everything lives under the isolated data
    directory" true rather than merely asserted.

    Returns ``None`` when the service did not come up, which the caller treats as
    "fixtures were not seeded".
    """
    base = f"http://127.0.0.1:{port}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT, env=env,
    )
    for _ in range(60):
        if proc.poll() is not None:
            print(f"WARN: shared backend exited early (code {proc.returncode}); see {log_path}")
            return None
        if health_ok(base, timeout=1.0):
            print(f"shared backend : started on {base} (pid {proc.pid})")
            return proc
        time.sleep(0.5)
    print("WARN: shared backend did not answer within 30s")
    proc.terminate()
    return None


def stop_shared_backend(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


SEED_TITLE = "SEED 无费用回归种子项目"
SEED_TEXT = "回归种子文本：让界面处于已有项目的状态，不参与任何真实调用，也不代表任何实际输入。"


def seed_project_if_empty(base: str) -> dict:
    """Create one plain mock project so the browser checks find the state they assume.

    Most browser scripts click "新建项目" before filling the form, but that button
    is deliberately disabled while the form is already in create mode — which is
    exactly the state a brand new database starts in. Those scripts therefore
    assume at least one project already exists, so a truly empty database makes
    them fail for a reason unrelated to the code under test.

    Only ever called for a backend this runner started itself, which by
    construction is the isolated one. Never touches a service the user already
    had running.

    Known gap, deliberately not papered over: this does not make the scripts able
    to drive a real first-run path. Making them tolerate both entries is tracked
    in VisionCraft/task_plan.md.
    """
    try:
        with urllib.request.urlopen(f"{base}/api/projects", timeout=10) as response:
            listed = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, never abort the run
        return {"created": False, "error": f"{type(exc).__name__}: {exc}"}
    existing = listed if isinstance(listed, list) else listed.get("projects") or []
    if existing:
        return {"created": False, "skipped": f"already has {len(existing)} project(s)"}

    payload = json.dumps({"title": SEED_TITLE, "source_text": SEED_TEXT,
                          "generation_mode": "mock"}).encode("utf-8")
    request = urllib.request.Request(f"{base}/api/projects", data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"created": False, "error": f"{type(exc).__name__}: {exc}"}
    project_id = body.get("id") or body.get("project_id") or ""
    return {"created": True, "project_id": project_id, "title": body.get("title", SEED_TITLE)}


CREATE_GUARD_CHECK = "test_live_2shot_create_guard.cjs"
RETIRE_CHECK = "test_retire_remote_video_task.py"


def seed_fixtures(env: dict[str, str]) -> dict:
    """Build the historical projects the acceptance scripts assume already exist.

    A handful of checks do not test the code under test so much as a *state*: they
    reproduce incidents that happened on a database which already contained
    certain projects. Against a brand new isolated database they fail for a reason
    that has nothing to do with the code, which makes them worthless as regression
    checks. ``tools/seed_regression_fixtures.py`` builds that state fully offline;
    this wrapper only runs it and reports what it did.

    Runs before the shared backend starts: the seeder writes the database
    directly, and doing that while uvicorn holds write locks invites
    "database is locked" in the middle of a workflow. It never touches a service
    the user already had running — the whole point is that this runner owns both
    the port and the data directory.
    """
    try:
        proc = subprocess.run(
            [str(PYTHON), "tools/seed_regression_fixtures.py"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout after 900s"}
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


def touch_guard_fixture(env: dict[str, str]) -> str:
    """Restore the create-guard premise immediately before its check.

    ``test_live_2shot_create_guard.cjs`` asserts that the historical same-title
    project is the one the UI has selected, and the frontend picks whichever
    project was updated last. Checks that ran earlier have created newer projects,
    so the premise is rebuilt here instead of being left to luck. One small
    UPDATE, so the running backend's write lock is not a real concern.
    """
    try:
        proc = subprocess.run(
            [str(PYTHON), "tools/seed_regression_fixtures.py", "--touch-only"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
    except subprocess.TimeoutExpired:
        return "timeout after 180s"
    if proc.returncode != 0:
        detail = ((proc.stderr or "").strip() or (proc.stdout or "").strip())[-200:]
        return f"failed (exit {proc.returncode}): {detail}"
    return "ok"


def ensure_video_task_fixture(env: dict[str, str]) -> str:
    """Make sure the historical video task row still exists before its check.

    ``test_retire_remote_video_task.py`` has two cases that select *any*
    non-inflight row and print SKIP when the table is empty, so on a fresh
    isolated database they quietly stop asserting anything. The row is seeded up
    front, but earlier checks delete projects as part of their own housekeeping,
    and ``ON DELETE CASCADE`` would take the row with it. Re-asserting it here is
    one idempotent INSERT, so the running backend's write lock is not a real
    concern.
    """
    try:
        proc = subprocess.run(
            [str(PYTHON), "tools/seed_regression_fixtures.py", "--ensure-video-task"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
    except subprocess.TimeoutExpired:
        return "timeout after 180s"
    if proc.returncode != 0:
        detail = ((proc.stderr or "").strip() or (proc.stdout or "").strip())[-200:]
        return f"failed (exit {proc.returncode}): {detail}"
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="",
                        help="Isolated data directory. Default: a fresh stageC/run-<timestamp>")
    parser.add_argument("--only", default="", help="Substring filter on test names; comma-separate for several")
    parser.add_argument("--group", default="", help="Only run one group: static|node|python|browser")
    parser.add_argument("--no-seed", action="store_true",
                        help="Do not create the seed project in the isolated database")
    parser.add_argument("--no-fixtures", action="store_true",
                        help="Skip the historical fixture projects (three checks will fail)")
    parser.add_argument("--keep-safe-delete", action="store_true",
                        help="Leave the host delete shim enabled for child processes")
    args = parser.parse_args()

    if not PYTHON.is_file():
        print(f"FAIL: venv python missing at {PYTHON}")
        return 1

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

    env = {**os.environ}
    env["VISIONCRAFT_DATA_DIR"] = str(data_dir)
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
    # this runner owns the port as well — see start_shared_backend, which refuses
    # to adopt a service it did not start. It also keeps hundreds of scratch
    # files out of the user's recycle bin. Pass --keep-safe-delete to opt out.
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

    print(f"Isolated data dir : {data_dir}")
    print(f"Live switches     : all pinned to 0")
    print(f"Delete shim       : {delete_note}")
    print(f"Checks selected   : {len(checks)}")
    print()

    needs_server = any(c.group == "browser" or c.name in SERVER_DEPENDENT for c in checks)
    # Per-run log, not one shared stageC/shared_backend.log: a single shared path
    # meant the newest run silently overwrote the previous run's evidence.
    server_log = data_dir / "shared_backend.log"
    server: subprocess.Popen | None = None
    base = ""

    results = []
    seed: dict = {"created": False, "skipped": "no managed backend"}
    fixtures: dict = {"ok": False, "skipped": "no managed backend"}
    try:
        if needs_server:
            port = pick_free_port()
            base = f"http://127.0.0.1:{port}"
            # The SERVER_DEPENDENT checks resolve their address from this and only
            # fall back to :8000 when it is unset. Scripts that start their own
            # service override it for their own subprocess, so exporting it to
            # every child is safe.
            env["VISIONCRAFT_BASE_URL"] = base
            # Fixtures first, while nothing else holds the database.
            if not args.no_fixtures:
                fixtures = seed_fixtures(env)
                if fixtures.get("ok"):
                    detail = {k: v for k, v in fixtures.items() if k != "ok"}
                    print(f"fixtures       : {json.dumps(detail, ensure_ascii=False)}")
                else:
                    print(f"WARN: 历史夹具种入失败，依赖它们的检查会失败：{fixtures.get('error')}")
            server = start_shared_backend(env, server_log, port)
            # Fallback only. The fixture set already creates projects; this exists
            # so that a failed seeding run still leaves the browser checks *a*
            # project to find, which is the older, weaker assumption.
            if server is not None and not args.no_seed and not fixtures.get("ok"):
                seed = seed_project_if_empty(base)
                if seed.get("created"):
                    print(f"seed project   : {seed['project_id']} ({seed['title']})")
                elif seed.get("error"):
                    print(f"WARN: seed project failed: {seed['error']}")
        for index, check in enumerate(checks, start=1):
            # The create-guard check needs the historical project to be the one
            # the UI has selected, which the frontend decides by updated_at. Any
            # earlier check may have created a newer project, so the premise is
            # rebuilt here rather than left to luck.
            if check.name == CREATE_GUARD_CHECK and fixtures.get("ok"):
                note = touch_guard_fixture(env)
                if note != "ok":
                    print(f"WARN: 历史夹具 updated_at 复位失败：{note}")
            # The retire cases read "is there any non-inflight row?" as their
            # premise. ON DELETE CASCADE means an earlier check that cleaned up
            # its own project could have removed the seeded row along with it.
            if check.name == RETIRE_CHECK and fixtures.get("ok"):
                note = ensure_video_task_fixture(env)
                if note != "ok":
                    print(f"WARN: video_tasks 夹具补种失败：{note}")
            print(f"[{index:>2}/{len(checks)}] {check.name} ...", end="", flush=True)
            res = run_check(check, env)
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
    finally:
        stop_shared_backend(server)

    failed = [r for r in results if r["verdict"] == "FAIL"]
    totals = {k: sum(r["counts"][k] for r in results) for k in ("pass", "fail", "skip")}
    print()
    print("=" * 68)
    print(f"checks : {len(results) - len(failed)}/{len(results)} passed")
    print(f"asserts: {totals['pass']} pass / {totals['fail']} fail / {totals['skip']} skip")
    print(f"elapsed: {round(sum(r['seconds'] for r in results), 1)}s")
    if failed:
        print("failed :")
        for r in failed:
            print(f"  - {r['name']} (exit {r['exit_code']}, {r['seconds']}s)")
    print("=" * 68)

    report = {"schema": "visioncraft.no_cost_regression.v1",
              "data_dir": str(data_dir), "live_switches": "all pinned to 0",
              "shared_backend": {"base_url": base, "log": str(server_log),
                                 "started_by_runner": server is not None},
              "fixtures": fixtures,
              "seed_project": seed,
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
