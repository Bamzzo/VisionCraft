"""收费前空跑：闸门关着时，走**真实 HTTP 路由**点一次生成，期望被拦。

为什么必须有这一步
------------------
"闸门在" 是声明；"拦得住" 才是证据。既有的 tools/test_video_provider_guard.py
只测到 generate_video_asset 这一层（函数级）。路由层到那一层之间还隔着
端点 → prepare_shot_video_generation → generate_shot_video，任何一处把
调用点改掉，函数级用例都发现不了。本脚本从 HTTP 打进去。

设计约束
--------
1. 临时 VISIONCRAFT_DATA_DIR —— 绝不碰真实库。
2. 强制清掉所有 live 开关（即使环境里有也清掉）。
3. **网络绊线**：把 urllib.request.urlopen 换成"记录 + 抛错"。
   只要有任何一个模块想把请求发出去，脚本立刻失败并留下 URL。
   这同时是"没打开 HTTP"的证伪手段，而不只是日志缺一行。
4. 断言的判据是**计数与状态**，不是耗时。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

TS = time.strftime("%Y%m%d-%H%M%S")
TMP_DATA = ROOT / "output" / f"tmp-emptyrun-{TS}"
REPORT_DIR = Path(os.getenv("VISIONCRAFT_CHECK_OUT")
                  or (ROOT / "output" / "playwright" / "live-4shot-20261002"))
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# --- 1. 环境：临时数据目录 + 清掉所有 live 开关（只清进程级） ---------------
LIVE_KEYS = (
    "VISIONCRAFT_ALLOW_LIVE_LLM",
    "VISIONCRAFT_ALLOW_LIVE_VIDEO",
    "VISIONCRAFT_ALLOW_LIVE_VISION",
    "VISIONCRAFT_LIVE_MAX_VIDEO_CALLS",
    "VISIONCRAFT_LIVE_BUDGET_CNY",
)
os.environ["VISIONCRAFT_DATA_DIR"] = str(TMP_DATA)
cleared = {}
for key in LIVE_KEYS:
    cleared[key] = os.environ.pop(key, None)

# --- 2. 网络绊线 -------------------------------------------------------------
import urllib.request  # noqa: E402

ATTEMPTS: list[str] = []
_real_urlopen = urllib.request.urlopen


def _tripwire(req, *args, **kwargs):  # noqa: ANN001
    url = getattr(req, "full_url", None) or str(req)
    ATTEMPTS.append(url)
    raise AssertionError(f"NETWORK TRIPWIRE 被触发，有人想发请求：{url}")


urllib.request.urlopen = _tripwire  # type: ignore[assignment]

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))
    return bool(ok)


def main() -> int:
    from backend.config import PROJECTS_DIR, init_environment  # noqa: E402
    from backend.database import connect, init_db  # noqa: E402

    init_environment()
    init_db()

    # 环境自检：确认真的没开闸
    from backend.providers import live_budget as lb  # noqa: E402

    check("授权开关处于关闭态（live_llm_authorized == False）",
          lb.live_llm_authorized() is False, f"实际 {lb.live_llm_authorized()!r}")
    check("视频授权处于关闭态（live_video_authorized == False）",
          lb.live_video_authorized() is False, f"实际 {lb.live_video_authorized()!r}")

    from fastapi.testclient import TestClient  # noqa: E402
    from backend.main import app  # noqa: E402

    client = TestClient(app)

    # --- 夹具：建项目（走真实路由）+ 直接落一个镜头 --------------------------
    # 为什么镜头直接落库：本用例要验的是**视频生成路由 → 闸门**这一段，
    # 不是分镜流水线。分镜流水线依赖 mock 工作流与视觉锚点门，会引入
    # 与本用例无关的失败面（首版实测它 120s 内没产出镜头）。
    title = f"空跑闸门-{TS}"
    source = ("方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
              "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
              "但是族中长老已经设下阻碍，他只能选择冒险一搏。")
    resp = client.post("/api/projects", json={
        "title": title,
        "source_text": source,
        "style": "cinematic clean realism",
        "aspect_ratio": "16:9",
        "duration_seconds": 5,
        "output_resolution": "1280x720",
        "shot_count_mode": "auto",
        "review_mode": False,
        "generation_mode": "mock",
    })
    if resp.status_code != 200:
        check("创建 mock 项目", False, f"HTTP {resp.status_code} {resp.text[:200]}")
        return 1
    mock_pid = resp.json()["id"]
    check("创建 mock 项目", True, mock_pid)

    from backend.database import utc_now  # noqa: E402

    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        # 批量端点前还有一道"分镜已确认 / 视觉锚点已过门"的守卫
        # （assert_batch_generation_allowed，黑名单式放行）。
        # 首版夹具停在 created，被那道门以 HTTP 400 拦下——拦在付费闸门**之前**，
        # 于是批量用例根本没走到闸门。这里把状态推到 production_ready，
        # 让批量路径真的抵达付费闸门。
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            ("production_ready", now, mock_pid),
        )
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, mock_pid, 1, "镜01", "动作", "[]", "场景", "固定",
             "prompt", "", "", "draft", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "动作", "prompt", "", "", None, None, None,
             "t2v", "dashscope", "wan2.7-t2v", "emptyrun", now),
        )
    proj = client.get(f"/api/projects/{mock_pid}").json()
    shots = proj.get("shots") or []
    check("夹具镜头已挂到项目上", len(shots) == 1, f"共 {len(shots)} 个镜头")
    if not shots:
        return 1

    with connect() as conn:
        before_tasks = conn.execute(
            "SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?", (mock_pid,)
        ).fetchone()["n"]
        before_count = conn.execute(
            "SELECT live_video_call_count FROM projects WHERE id = ?", (mock_pid,)
        ).fetchone()["live_video_call_count"]

    # --- 用例 1：逐镜视频端点 -----------------------------------------------
    r1 = client.post(f"/api/projects/{mock_pid}/shots/{shot_id}/video",
                     json={"video_mode": "t2v"})
    ok1 = r1.status_code == 200
    job1 = (r1.json() or {}).get("job_id") if ok1 else None
    job1_body = client.get(f"/api/jobs/{job1}").json() if job1 else {}
    blob1 = json.dumps(job1_body, ensure_ascii=False)
    check("逐镜视频：端点接受了排队请求", ok1, f"HTTP {r1.status_code}")
    check("逐镜视频：任务被判定为失败（而不是成功）",
          (job1_body.get("status") == "failed"), f"status={job1_body.get('status')!r}")
    check("逐镜视频：失败原因指向「尚未授权」",
          ("尚未授权" in blob1), blob1[:260])

    # --- 用例 2：批量视频端点 -----------------------------------------------
    r2 = client.post(f"/api/projects/{mock_pid}/videos")
    ok2 = r2.status_code == 200
    job2 = (r2.json() or {}).get("job_id") if ok2 else None
    job2_body = client.get(f"/api/jobs/{job2}").json() if job2 else {}
    blob2 = json.dumps(job2_body, ensure_ascii=False)
    check("批量视频：端点接受了排队请求", ok2, f"HTTP {r2.status_code}")
    check("批量视频：任务被判定为失败",
          (job2_body.get("status") == "failed"), f"status={job2_body.get('status')!r}")
    check("批量视频：失败原因指向「尚未授权」",
          ("尚未授权" in blob2), blob2[:260])

    # --- 用例 3：文本阶段（live_strict，未授权） ----------------------------
    resp_live = client.post("/api/projects", json={
        "title": f"空跑闸门-live-{TS}",
        "source_text": source,
        "style": "cinematic clean realism",
        "aspect_ratio": "16:9",
        "duration_seconds": 5,
        "output_resolution": "1280x720",
        "shot_count_mode": "auto",
        "review_mode": False,
        "generation_mode": "live_strict",
    })
    text_outcome = {}
    if resp_live.status_code == 200:
        live_pid = resp_live.json()["id"]
        r3 = client.post(f"/api/projects/{live_pid}/run")
        job3 = (r3.json() or {}).get("job_id") if r3.status_code == 200 else None
        job3_body = client.get(f"/api/jobs/{job3}").json() if job3 else {}
        blob3 = json.dumps(job3_body, ensure_ascii=False)
        text_outcome = {"http": r3.status_code, "job": job3_body}
        check("文本阶段（live_strict 未授权）：没有静默降级成成功",
              job3_body.get("status") in {"failed", "paused"},
              f"status={job3_body.get('status')!r} | {blob3[:240]}")
        check("文本阶段：原因里出现「尚未授权」或 LIVE_LLM_FAILED",
              ("尚未授权" in blob3) or ("LIVE_LLM_FAILED" in blob3), blob3[:280])
    else:
        check("文本阶段：live_strict 项目可创建", False, resp_live.text[:200])

    # --- 零费用证据 -----------------------------------------------------------
    with connect() as conn:
        after_tasks = conn.execute(
            "SELECT COUNT(*) AS n FROM video_tasks WHERE project_id = ?", (mock_pid,)
        ).fetchone()["n"]
        after_count = conn.execute(
            "SELECT live_video_call_count FROM projects WHERE id = ?", (mock_pid,)
        ).fetchone()["live_video_call_count"]

    check("零费用：没有新增 video_tasks 行", after_tasks == before_tasks,
          f"{before_tasks} -> {after_tasks}")
    check("零费用：live_video_call_count 未自增", after_count == before_count,
          f"{before_count} -> {after_count}")
    check("零费用：网络绊线一次也没被触发", len(ATTEMPTS) == 0,
          f"实际 {len(ATTEMPTS)} 次：{ATTEMPTS[:3]}")

    report = {
        "status": "done",
        "generated_at": TS,
        "temp_data_dir": str(TMP_DATA),
        "cleared_live_flags": cleared,
        "live_llm_authorized": lb.live_llm_authorized(),
        "live_video_authorized": lb.live_video_authorized(),
        "live_max_video_calls": lb.live_max_video_calls(),
        "network_attempts": ATTEMPTS,
        "video_tasks_before_after": [before_tasks, after_tasks],
        "live_video_call_count_before_after": [before_count, after_count],
        "text_stage_outcome": text_outcome,
        "checks": RESULTS,
        "passed": sum(1 for r in RESULTS if r["ok"]),
        "total": len(RESULTS),
    }
    out = REPORT_DIR / "emptyrun_gate.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告 -> {out}")
    print(f"通过 {report['passed']}/{report['total']}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    finally:
        urllib.request.urlopen = _real_urlopen  # type: ignore[assignment]
        if TMP_DATA.exists():
            shutil.rmtree(TMP_DATA, ignore_errors=True)
            print(f"已清理临时数据目录 {TMP_DATA}")
    sys.exit(code)
