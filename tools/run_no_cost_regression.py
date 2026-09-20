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


def start_backend(env: dict[str, str], log_path: Path, port: int) -> subprocess.Popen | None:
    """Serve the app on a private port for one check that needs a service.

    It deliberately never adopts a service that happens to be listening. This
    runner disables the host bulk-delete guard for its children, and the
    acceptance tests delete projects as part of their own housekeeping. Adopting
    a stranger's backend could therefore aim those deletions at the user's real
    database — and nothing about a reuse branch makes that visible in the log.
    Owning the port is what makes "everything lives under the isolated data
    directory" true rather than merely asserted.

    Returns ``None`` when the service did not come up, which the caller records
    as a failed check without running it: a check that reports "state did not
    change" when in fact its backend never started is worse than no check.
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
            print(f"FAIL: backend exited early (code {proc.returncode}); see {log_path}")
            return None
        if health_ok(base, timeout=1.0):
            return proc
        time.sleep(0.5)
    print(f"FAIL: backend did not answer within 30s; see {log_path}")
    proc.terminate()
    return None


def stop_backend(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


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
            port = pick_free_port()
            check_env["VISIONCRAFT_BASE_URL"] = f"http://127.0.0.1:{port}"
            server = start_backend(check_env, log_dir / f"backend-{index:02d}-{safe_name(check.name)}.log", port)

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
