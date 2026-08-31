"""Verify a live 5-shot run, write desensitized audit files, then optionally clean up."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import PROJECTS_DIR, init_environment
from backend.database import connect, init_db
from backend.services.project_service import delete_project
from tools.live_run_audit import (
    DEFAULT_OUT,
    PROTECTED_PROJECTS,
    apply_count_fields,
    collect_project_lineage,
    reconstruct_last_live_run,
    summarize_ffprobe,
    verify_post_cleanup,
    verify_pre_cleanup,
    write_audit_reports,
)

RESULT = DEFAULT_OUT / "result.json"


def _load_result() -> dict:
    if not RESULT.is_file():
        return {}
    return json.loads(RESULT.read_text(encoding="utf-8"))


def _save_result(data: dict) -> None:
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def verify() -> int:
    init_environment()
    init_db()
    data = apply_count_fields(_load_result())
    project_id = data.get("project_id")
    print(f"INFO: result_project={project_id}")
    if not project_id:
        print("FAIL: result.json 没有 project_id")
        return 1
    if project_id in PROTECTED_PROJECTS or not str(project_id).startswith("project_"):
        print("FAIL: 拒绝校验受保护项目")
        return 1
    with connect() as conn:
        exists = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not exists:
        print("INFO: 临时项目已清理，改为从 leftover 文件重建审计报告")
        reconstruct_last_live_run(DEFAULT_OUT)
        print("PASS: reconstructed desensitized audit from leftover evidence")
        return 0
    _save_result(data)
    lineage = collect_project_lineage(project_id)
    pre = verify_pre_cleanup(lineage)
    final_cut = DEFAULT_OUT / "final-cut.mp4"
    if not final_cut.is_file():
        finals = lineage.get("final_videos") or []
        if finals:
            filename = str(finals[0].get("file_path") or "").rsplit("/", 1)[-1]
            candidate = PROJECTS_DIR / project_id / filename
            if candidate.is_file():
                final_cut = candidate
    ffprobe = summarize_ffprobe(final_cut)
    write_audit_reports(DEFAULT_OUT, result=data, lineage=lineage, ffprobe=ffprobe, pre_cleanup=pre)
    print(f"INFO: shots={lineage['counts']['shots']} video_assets={lineage['counts']['video_assets']} video_tasks={lineage['counts']['video_tasks']} finals={lineage['counts']['final_videos']}")
    print(f"INFO: live_text={lineage['live_text_call_count']} live_vision={lineage['live_vision_call_count']} live_video={lineage['live_video_call_count']}")
    print(f"INFO: unique_remote_tasks={lineage['counts']['unique_remote_tasks']} duplicate_remote_groups={lineage['counts']['duplicate_remote_groups']}")
    print(f"INFO: MiniMax 新提交={data.get('video_submits_new')} 复用={data.get('video_tasks_reused')} 唯一任务={data.get('unique_remote_tasks')} 中断前已有={data.get('preexisting_remote_tasks')}")
    print(f"INFO: secret_leak={lineage['secret_leak']}")
    print("PASS: lineage and caps" if pre["ok"] else "FAIL: lineage or caps")
    for key, ok in (pre.get("checks") or {}).items():
        print(f"  {key}={'PASS' if ok else 'FAIL'}")
    return 0 if pre["ok"] else 1


def cleanup() -> int:
    data = apply_count_fields(_load_result())
    project_id = data.get("project_id")
    if not project_id or project_id in PROTECTED_PROJECTS or not str(project_id).startswith("project_"):
        print("SKIP: 无本次临时项目可清理")
        return 0
    code = verify()
    if code != 0:
        print("FAIL: 清理前验证未通过，未删除临时项目")
        return code
    delete_project(project_id)
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    print(f"CLEANED: {project_id}")
    after = verify_post_cleanup(project_id)
    print(f"INFO: v1demo_main={after['v1demo_main']} env_exists={after['env_exists']} temp_dir={after['temp_dir_exists']}")
    if not after["ok"]:
        print("FAIL: 清理后校验未通过")
        return 1
    print("PASS: post-cleanup isolation")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if mode == "cleanup":
        raise SystemExit(cleanup())
    if mode == "reconstruct":
        reconstruct_last_live_run(DEFAULT_OUT)
        print("PASS: reconstructed last live-run audit")
        raise SystemExit(0)
    raise SystemExit(verify())
