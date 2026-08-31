"""Run one authorized live 2-shot frontend loop. Does not write .env or change product code."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db
from backend.providers.live_budget import estimate_closed_loop_cny, COST_BUFFER, estimate_minimax_i2v_cny
from backend.services.project_service import delete_project
from tools.live_run_audit import (
    collect_project_lineage,
    cleanup_decision,
    estimate_occurred_cny,
    has_secret_leak,
    redact_remote_task_id,
    summarize_ffprobe,
    verify_post_cleanup,
    verify_pre_cleanup,
)
from tools.p6c_ffmpeg import ensure_process_path, ffmpeg_available

OUT = ROOT / "output" / "playwright" / "live-2shot"
PROTECTED = {"v1demo_main", "project_5fdac03f50"}
TITLE_PREFIX = "LIVE2SHOT"
SOURCE = "春秋蝉鸣少年归。"
GYFY = ROOT.parent / "gyfy.jpg"


def _health_ok(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _load_result() -> dict:
    path = OUT / "result.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_result(data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def preflight() -> dict:
    os.environ["VISIONCRAFT_LIVE_MAX_VIDEO_CALLS"] = "2"
    os.environ["VISIONCRAFT_LIVE_BUDGET_CNY"] = "8"
    os.environ["VISIONCRAFT_ALLOW_LIVE_LLM"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VISION"] = "0"
    os.environ["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "0"
    plan = estimate_closed_loop_cny(SOURCE, video_seconds=4)
    buffered = round(plan["text_cny"] + plan["vision_cny"] + estimate_minimax_i2v_cny(4) * 2 * COST_BUFFER, 4)
    init_environment()
    init_db()
    with connect() as conn:
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM video_tasks WHERE status IN ('submitted','running','pending_remote','waiting_remote')"
        ).fetchone()["n"]
    ensure_process_path()
    blocked = []
    if not plan["within_budget"] or buffered > 8:
        blocked.append(f"预计费用清单 {plan['total_cny']} / 含缓冲 {buffered} 超过 8 元")
    if int(plan["video_calls"]) != 2:
        blocked.append("视频上限不是 2")
    if active:
        blocked.append(f"存在 {active} 个活动远程视频任务")
    if not ffmpeg_available():
        blocked.append("FFmpeg/ffprobe 不可用")
    if not GYFY.is_file():
        blocked.append("未找到工作区旁 gyfy.jpg")
    return {
        "ok": not blocked,
        "blocked": blocked,
        "plan": plan,
        "buffered_cny": buffered,
        "active_remote_video_tasks": int(active),
    }


def live_backend_env() -> dict[str, str]:
    env = dict(os.environ)
    env["VISIONCRAFT_LIVE_MAX_VIDEO_CALLS"] = "2"
    env["VISIONCRAFT_LIVE_BUDGET_CNY"] = "8"
    env["VISIONCRAFT_ALLOW_LIVE_LLM"] = "1"
    env["VISIONCRAFT_ALLOW_LIVE_VIDEO"] = "1"
    env["VISIONCRAFT_ALLOW_LIVE_VISION"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def start_backend() -> tuple[subprocess.Popen, str]:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python if python.exists() else sys.executable)
    env = live_backend_env()
    log_path = OUT / "backend.log"
    log_handle = log_path.open("w", encoding="utf-8")
    for port in range(8040, 8049):
        if _port_in_use(port):
            continue
        proc = subprocess.Popen(
            [exe, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 25
        while time.time() < deadline:
            if proc.poll() is not None:
                log_handle.close()
                raise SystemExit(f"临时后端启动失败，见 {log_path}")
            if _health_ok(base):
                print(f"INFO: live backend {base}")
                return proc, base
            time.sleep(0.3)
        proc.terminate()
        proc.wait(timeout=5)
    log_handle.close()
    raise SystemExit("8040-8048 端口均不可用")


def stop_backend(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if os.name == "nt" and proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def ensure_playwright(harness: Path) -> None:
    module_dir = harness / "node_modules" / "playwright"
    harness.mkdir(parents=True, exist_ok=True)
    manifest = harness / "package.json"
    if not manifest.exists():
        manifest.write_text('{"name":"visioncraft-playwright","private":true}\n', encoding="utf-8")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("未找到 npm")
    if not module_dir.exists():
        subprocess.run([npm, "install", "playwright@1.55.1"], cwd=harness, check=True)
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    subprocess.run([npx, "playwright", "install", "chromium"], cwd=harness, check=True)


def copy_final_cut(project_id: str, lineage: dict) -> Path | None:
    dest = OUT / "final-cut.mp4"
    downloaded = OUT / "downloaded-final.mp4"
    if downloaded.is_file():
        shutil.copyfile(downloaded, dest)
        return dest
    finals = lineage.get("final_videos") or []
    if not finals:
        return dest if dest.is_file() else None
    raw = str(finals[0].get("file_path") or "")
    name = raw.rsplit("/", 1)[-1]
    candidate = PROJECTS_DIR / project_id / name
    if candidate.is_file():
        shutil.copyfile(candidate, dest)
        return dest
    return dest if dest.is_file() else None


def write_audits(result: dict, lineage: dict, ffprobe: dict, pre: dict) -> None:
    lineage_counts = lineage.get("counts") or {}
    text_calls = int(result.get("live_text_call_count") or lineage.get("live_text_call_count") or 0)
    vision_calls = int(result.get("live_vision_call_count") or lineage.get("live_vision_call_count") or 0)
    new_submits = int(result.get("live_video_call_count") or result.get("video_submits_new") or 0)
    unique = int(lineage_counts.get("unique_remote_tasks") or result.get("unique_remote_tasks") or 0)
    inflight = int(lineage_counts.get("remote_tasks_inflight") or result.get("remote_tasks_inflight") or 0)
    counts = {
        "text_calls_total": text_calls,
        "vision_calls_total": vision_calls,
        "video_submits_new": new_submits,
        "preexisting_remote_tasks": int(result.get("preexisting_remote_tasks") or 0),
        "video_tasks_reused": int(result.get("video_tasks_reused") or 0),
        "unique_remote_tasks": unique,
        "remote_tasks_completed": int(lineage_counts.get("remote_tasks_completed") or 0),
        "remote_tasks_inflight": inflight,
        "downloaded_videos": int(lineage_counts.get("video_assets") or 0),
        "duplicate_submits": int(lineage_counts.get("duplicate_submits") or 0),
        "duplicate_assets": int(lineage_counts.get("duplicate_assets") or 0),
    }
    occurred = estimate_occurred_cny(
        text_calls=text_calls,
        vision_calls=vision_calls,
        video_submits=new_submits,
        source_text=SOURCE,
        video_seconds=4,
    )
    audit = {
        "schema": "visioncraft.live_run_audit.v2",
        "reconstructed_after_cleanup": False,
        "real_network_this_phase": True,
        "cost_cny_this_phase": occurred["local_estimate_cny"],
        "cost_visibility": "无法确认",
        "platform_cost": "无法确认",
        "local_estimate_cny": occurred["local_estimate_cny"],
        "local_estimate_breakdown": occurred,
        "project_id": result.get("project_id"),
        "title": result.get("title"),
        "generation_mode": "live_strict",
        "status_vocabulary": ["PASS", "FAIL", "SKIP", "BLOCKED_BEFORE_CALL"],
        "stages": result.get("stages") or {},
        "counts": counts,
        **counts,
        "ffmpeg_ran": bool(result.get("ffmpeg_ran") or ffprobe.get("ok")),
        "final_cut": bool(result.get("final_cut") or ffprobe.get("ok")),
        "preview_ok": bool(result.get("preview_ok")),
        "download_ok": bool(result.get("download_ok")),
        "cleanup_verified": bool(result.get("cleanup_verified")),
        "retain_for_resume": bool(result.get("retain_for_resume")),
        "resume_note": result.get("resume_note")
        or "每镜最多提交一次 MiniMax I2V；等待中只 refresh 同一 remote_task_id。",
        "pre_cleanup": pre,
        "providers": {
            "text": "deepseek / deepseek-v4-flash",
            "vision": "deepseek / deepseek-v4-flash-vision-exp",
            "video": "minimax / MiniMax-H3 I2V 768P 4s",
        },
    }
    paths = {
        "audit": OUT / "live_run_audit.json",
        "lineage": OUT / "live_run_lineage.json",
        "ffprobe": OUT / "live_run_ffprobe.json",
    }
    paths["audit"].write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["lineage"].write_text(json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["ffprobe"].write_text(json.dumps(ffprobe, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in paths.values():
        if has_secret_leak(path.read_text(encoding="utf-8")):
            raise SystemExit(f"审计文件疑似包含密钥：{path.name}")


def _db_has_inflight(project_id: str) -> dict[str, int]:
    with connect() as conn:
        tasks = conn.execute(
            """
            SELECT COUNT(*) AS n FROM video_tasks
            WHERE project_id = ? AND status IN ('submitted','running','pending_remote','waiting_remote')
            """,
            (project_id,),
        ).fetchone()["n"]
        shots = conn.execute(
            """
            SELECT COUNT(*) AS n FROM shots
            WHERE project_id = ? AND status IN ('video_running','video_waiting_remote')
            """,
            (project_id,),
        ).fetchone()["n"]
    return {"tasks": int(tasks), "shots": int(shots)}


def maybe_cleanup(project_id: str, created_this_run: bool, lineage: dict | None = None) -> dict:
    if not created_this_run or not project_id or project_id in PROTECTED:
        print("SKIP: 本次未创建临时项目，跳过清理")
        return {"ok": False, "skipped": True, "retain_for_resume": False}
    decision = cleanup_decision(lineage or {}, project_id=project_id, protected=PROTECTED, title_prefix=TITLE_PREFIX)
    inflight = _db_has_inflight(project_id)
    if inflight["tasks"] or inflight["shots"]:
        print(f"SKIP: inflight_remote_tasks db_tasks={inflight['tasks']} db_shots={inflight['shots']}")
        with connect() as conn:
            protected = conn.execute("SELECT id FROM projects WHERE id = ?", ("project_5fdac03f50",)).fetchone()
        return {
            "cleanup": False,
            "reason": "inflight_remote_tasks",
            "retain_for_resume": True,
            "ok": False,
            "skipped": True,
            "protected_untouched": bool(protected),
            "inflight_task_count": inflight["tasks"],
            "inflight_shot_count": inflight["shots"],
        }
    if not decision["cleanup"]:
        print(f"SKIP: {decision['reason']} retain_for_resume={decision.get('retain_for_resume')}")
        if decision.get("note"):
            print(f"INFO: {decision['note']}")
        with connect() as conn:
            protected = conn.execute("SELECT id FROM projects WHERE id = ?", ("project_5fdac03f50",)).fetchone()
        return {**decision, "ok": False, "skipped": True, "protected_untouched": bool(protected)}
    with connect() as conn:
        row = conn.execute("SELECT title FROM projects WHERE id = ?", (project_id,)).fetchone()
    title = str(row["title"] if row else "")
    if row and not title.startswith(TITLE_PREFIX):
        print("SKIP: 拒绝清理非本次 LIVE2SHOT 项目")
        return {"ok": False, "skipped": True, "retain_for_resume": False}
    delete_project(project_id)
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    print(f"CLEANED: {project_id}")
    after = verify_post_cleanup(project_id)
    if after["temp_project_exists"] or after["temp_dir_exists"]:
        print("FAIL: 临时项目清理不完整")
    else:
        print("PASS: 仅清理无远程任务的本次临时项目")
    with connect() as conn:
        protected = conn.execute("SELECT id FROM projects WHERE id = ?", ("project_5fdac03f50",)).fetchone()
    after["protected_untouched"] = bool(protected)
    after["retain_for_resume"] = False
    return after


def pid_listening(port: int) -> bool:
    return _port_in_use(port)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stale = OUT / "result.json"
    if stale.is_file():
        stale.unlink()
    check = preflight()
    print("INFO: preflight", json.dumps({k: check[k] for k in ("ok", "buffered_cny", "active_remote_video_tasks", "blocked")}, ensure_ascii=False))
    if not check["ok"]:
        print("BLOCKED_BEFORE_CALL")
        for item in check["blocked"]:
            print(f"  - {item}")
        _save_result({"stages": {"preflight": {"status": "BLOCKED_BEFORE_CALL", "blocked": check["blocked"]}}, "plan": check["plan"]})
        return 2
    jpeg = OUT / "gyfy.jpg"
    shutil.copyfile(GYFY, jpeg)
    harness = ROOT / ".playwright-cli"
    ensure_playwright(harness)
    server = None
    port = None
    code = 1
    try:
        server, base = start_backend()
        port = int(base.rsplit(":", 1)[-1])
        env = live_backend_env()
        env["NODE_PATH"] = str(harness / "node_modules")
        env["VISIONCRAFT_BASE_URL"] = base
        env["LIVE2SHOT_JPEG"] = str(jpeg)
        completed = subprocess.run(["node", str(ROOT / "tools" / "live_2shot.cjs")], cwd=ROOT, env=env, check=False)
        code = completed.returncode
        result = _load_result()
        result["budget_cny"] = 8
        result["cost_visibility"] = "无法确认"
        result["platform_cost"] = "无法确认"
        project_id = result.get("project_id")
        lineage = collect_project_lineage(project_id) if project_id else {"ok": False, "reason": "no_project"}
        if lineage.get("ok"):
            result["live_text_call_count"] = lineage.get("live_text_call_count")
            result["live_vision_call_count"] = lineage.get("live_vision_call_count")
            result["live_video_call_count"] = lineage.get("live_video_call_count")
            result["video_submits_new"] = int(lineage.get("live_video_call_count") or result.get("video_submits_new") or 0)
            result["unique_remote_tasks"] = (lineage.get("counts") or {}).get("unique_remote_tasks")
            result["remote_tasks_completed"] = (lineage.get("counts") or {}).get("remote_tasks_completed")
            result["video_tasks"] = [
                {**item, "remote_task_id": redact_remote_task_id(item.get("remote_task_id"))}
                for item in (lineage.get("video_tasks") or [])
            ]
        occurred = estimate_occurred_cny(
            text_calls=int(result.get("live_text_call_count") or 0),
            vision_calls=int(result.get("live_vision_call_count") or 0),
            video_submits=int(result.get("video_submits_new") or 0),
            source_text=SOURCE,
            video_seconds=4,
        )
        result["estimated_cny"] = occurred["local_estimate_cny"]
        result["local_estimate_cny"] = occurred["local_estimate_cny"]
        result["planned_buffered_cny"] = check["buffered_cny"]
        final_cut = copy_final_cut(project_id, lineage) if project_id else None
        ffprobe = summarize_ffprobe(final_cut) if final_cut else {"ok": False, "reason": "no_final"}
        pre = verify_pre_cleanup(lineage, shot_count=2) if lineage.get("ok") else {"ok": False, "checks": {}}
        result["ffprobe"] = ffprobe
        result["pre_cleanup"] = pre
        _save_result(result)
        write_audits(result, lineage, ffprobe, pre)
        print("INFO: audits written under", OUT)
        created_this_run = bool(project_id) and project_id not in PROTECTED
        if created_this_run:
            after = maybe_cleanup(project_id, True, lineage)
            result = _load_result()
            result["cleanup_verified"] = bool(after.get("ok"))
            result["protected_untouched"] = after.get("protected_untouched")
            result["retain_for_resume"] = bool(after.get("retain_for_resume"))
            result["cleanup_reason"] = after.get("reason")
            _save_result(result)
            audit_path = OUT / "live_run_audit.json"
            if audit_path.is_file():
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                audit["cleanup_verified"] = bool(after.get("ok"))
                audit["retain_for_resume"] = bool(after.get("retain_for_resume"))
                audit["cleanup_reason"] = after.get("reason")
                audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return code
    finally:
        stop_backend(server)
        if port:
            leftover = pid_listening(port)
            print(f"INFO: leftover_backend_port_{port}={leftover}")


if __name__ == "__main__":
    raise SystemExit(main())
