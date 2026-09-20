"""Seed the historical fixture projects that the acceptance scripts assume exist.

Several acceptance scripts do not test the code under test so much as a *state*:
they reproduce an incident that happened on a database which already contained
certain projects. Run against a brand new isolated database they fail for a
reason that has nothing to do with the code, which makes them useless as
regression checks.

This tool builds that state explicitly, fully offline:

* ``v1demo_main`` — built by ``tools/prepare_v1_demo.py`` (mock adaptation plus
  local lavfi clips; no provider is called). ``test_cleanup_temp_project.py``
  needs it present to assert that a protected project survives the cleanup tool,
  and two more of its cases need *some* project with shots.
* ``project_a43afde7c5`` — the historical same-title project that
  ``test_live_2shot_create_guard.cjs`` reproduces the accident with. The id
  cannot be produced through the API (``project_service.create_project`` always
  generates ``project_<uuid10>``), so it is inserted directly, which is exactly
  what reproduces the incident premise.
* ``project_c0ffee0001`` — a non-empty project that is *not* in the cleanup
  tool's ``PROTECTED`` set, plus one historical (non-inflight) ``video_tasks``
  row on it. Two cases in ``test_retire_remote_video_task.py`` select any
  non-inflight row and print SKIP when the table is empty, and the real working
  database is not empty; without this fixture the isolated run silently tests
  less than the real one does.

Safety: the target data directory is taken from ``VISIONCRAFT_DATA_DIR`` and the
tool refuses to run when that resolves to the real application data directory.
Nothing outside the target directory is touched, and no project other than the
fixtures is modified.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# The historical project the create-guard script reproduces the accident with.
GUARD_ID = "project_a43afde7c5"
GUARD_TITLE = "LIVE2SHOT 历史同名项目（回归夹具）"
GUARD_SAMPLE = "春秋蝉鸣少年归。历史夹具文本，仅用于让旧缺陷条件成立，不参与任何真实调用。"
DEMO_ID = "v1demo_main"
# A non-empty project that is deliberately *not* in the cleanup tool's PROTECTED
# set: test_cleanup_temp_project.py needs one to exercise its "non-empty project
# is refused" and "explicit allow still requires --apply" cases. Without it those
# two cases silently skip, and picking the protected demo project instead would
# make them pass for the wrong reason.
PLAIN_ID = "project_c0ffee0001"
PLAIN_TITLE = "回归夹具：带镜头的普通项目"
PLAIN_SAMPLE = "回归夹具文本：给清理工具守卫一个非空、且不受保护的普通项目。"
# One historical, **non-inflight** video task. test_retire_remote_video_task.py
# selects "any row that is not submitted/running/pending_remote/waiting_remote"
# (and, in its other case, any row at all) and prints SKIP when the table is
# empty. A fresh isolated database is empty, so both cases silently stop testing
# anything; the real working database has eight such rows, where they do run.
VTASK_ID = "vt_fixture_history_0001"
VTASK_PROVIDER = "fixture"
VTASK_MODEL = "fixture-history"
VTASK_REMOTE_ID = "fixture-history-0001"
VTASK_STATUS = "completed"


def resolve_data_dir(explicit: str) -> Path:
    raw = explicit or os.getenv("VISIONCRAFT_DATA_DIR", "")
    if not raw:
        raise SystemExit(
            "拒绝执行：既没有 --data-dir，环境里也没有 VISIONCRAFT_DATA_DIR。\n"
            "本工具只往隔离数据目录写夹具，请先指定目标目录。"
        )
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    real = (ROOT / "backend" / "data").resolve()
    if candidate == real:
        raise SystemExit(f"拒绝执行：{candidate} 是应用真实数据目录，夹具只应写入隔离目录。")
    return candidate


def touch_guard_fixture(data_dir: Path) -> dict:
    """Make the guard fixture the most recently updated project.

    The frontend auto-selects ``projects[0]``, and ``list_projects`` orders by
    ``updated_at DESC``. ``test_live_2shot_create_guard.cjs`` asserts that the
    legacy wait predicate (a title prefix showing up in the summary panel) is
    already satisfied *before* it creates anything, which can only hold while
    the historical project is the selected one. Checks that run earlier may have
    created newer projects, so the premise is restored right before that check.
    """
    os.environ["VISIONCRAFT_DATA_DIR"] = str(data_dir)
    from backend.database import connect, init_db, utc_now

    init_db()
    with connect() as conn:
        row = conn.execute("SELECT id, title FROM projects WHERE id = ?", (GUARD_ID,)).fetchone()
        if not row:
            return {"touched": False, "reason": "guard fixture missing"}
        now = utc_now()
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, GUARD_ID))
    return {"touched": True, "updated_at": now}


def insert_guard_fixture(data_dir: Path) -> dict:
    os.environ["VISIONCRAFT_DATA_DIR"] = str(data_dir)
    from backend.database import connect, init_db, utc_now

    init_db()
    now = utc_now()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (GUARD_ID,)).fetchone()
        if existing:
            return {"created": False, "skipped": "already present", "project_id": GUARD_ID}
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (GUARD_ID, GUARD_TITLE, GUARD_SAMPLE, "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "created", "direct", 0, now, now),
        )
    return {"created": True, "project_id": GUARD_ID, "title": GUARD_TITLE}


def insert_plain_fixture(data_dir: Path) -> dict:
    """Create a non-empty, non-protected project offline.

    The mock adaptation workflow runs entirely locally, so this costs nothing and
    calls no provider. Shots (not media) are all the cleanup guards look at.
    """
    os.environ["VISIONCRAFT_DATA_DIR"] = str(data_dir)
    from backend.database import connect, init_db, utc_now
    from backend.services.adaptation_service import (
        confirm_bible,
        confirm_scope,
        confirm_storyboard,
        list_adaptation_options,
        start_adaptation_workflow,
    )
    from backend.services.project_service import get_project

    init_db()
    now = utc_now()
    with connect() as conn:
        if conn.execute("SELECT id FROM projects WHERE id = ?", (PLAIN_ID,)).fetchone():
            existing = get_project(PLAIN_ID)
            return {"created": False, "skipped": "already present", "project_id": PLAIN_ID,
                    "shots": len(existing.get("shots") or [])}
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, output_resolution,
             shot_count_mode, status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (PLAIN_ID, PLAIN_TITLE, PLAIN_SAMPLE, "cinematic clean realism", "16:9", 5, "1280x720",
             "auto", "created", "direct", 0, now, now),
        )

    start_adaptation_workflow(PLAIN_ID)
    options = list_adaptation_options(PLAIN_ID)
    if not options:
        raise RuntimeError("普通夹具项目的 mock 改编方案为空")
    confirm_scope(PLAIN_ID, options[0]["id"])
    confirm_bible(PLAIN_ID)
    confirm_storyboard(PLAIN_ID)
    shots = len(get_project(PLAIN_ID).get("shots") or [])
    return {"created": True, "project_id": PLAIN_ID, "title": PLAIN_TITLE, "shots": shots}


def ensure_video_task_fixture(data_dir: Path) -> dict:
    """Give the plain fixture one historical (non-inflight) video task.

    Idempotent, and safe to re-run while the backend is up: it inserts one row
    only when that row is missing.

    Why this container. ``video_tasks`` has three ``NOT NULL REFERENCES``
    columns and the app turns SQLite foreign keys on (``database.py`` runs
    ``PRAGMA foreign_keys = ON``), so a floating row pointing at a non-existent
    project cannot be inserted — the anchor has to be real. The plain fixture is
    the right host because nothing runs real generation against it: it only ever
    meets the cleanup tool's *refusal* paths (every case in
    ``test_cleanup_temp_project.py`` is a refusal or a dry-run, none deletes a
    non-empty project). By contrast the demo project is driven by the v1-demo and
    workbench checks, and ``video_service.prepare_version_for_generation`` counts
    rows by ``version_id`` to decide "this version already has a video task, so
    clone a fresh version instead of reusing it". A fixture row on a version that
    real flows touch would silently change that decision.

    Why ``completed``. Every guard that could plausibly trip filters on an
    *active* status: ``has_waiting_remote_video`` and
    ``refresh_project_video_tasks`` accept only running/pending_remote (plus
    submitted for the former), the paid-run preflight and the cleanup tool count
    the four inflight states, and the safe-retry path looks for ``failed``. A
    completed row is invisible to all of them while still being exactly what the
    two retire cases select.

    ``provider`` is deliberately not a real vendor name: it cannot collide with
    the ``UNIQUE(provider, remote_task_id)`` constraint, and no code path looks
    up a task by this name.
    """
    os.environ["VISIONCRAFT_DATA_DIR"] = str(data_dir)
    from backend.database import connect, init_db, utc_now

    init_db()
    now = utc_now()
    with connect() as conn:
        if conn.execute("SELECT id FROM video_tasks WHERE id = ?", (VTASK_ID,)).fetchone():
            return {"created": False, "skipped": "already present", "video_task_id": VTASK_ID}
        anchor = conn.execute(
            """SELECT s.id AS shot_id, s.current_version_id AS version_id
               FROM shots s
               WHERE s.project_id = ? AND s.current_version_id IS NOT NULL
               ORDER BY s.created_at, s.id
               LIMIT 1""",
            (PLAIN_ID,),
        ).fetchone()
        if not anchor:
            return {"created": False, "video_task_id": VTASK_ID,
                    "skipped": "plain fixture has no shot with a current version"}
        conn.execute(
            """INSERT INTO video_tasks
            (id, project_id, shot_id, version_id, provider, model, remote_task_id,
             status, cloud_status, prompt, submit_payload, status_payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (VTASK_ID, PLAIN_ID, anchor["shot_id"], anchor["version_id"],
             VTASK_PROVIDER, VTASK_MODEL, VTASK_REMOTE_ID,
             VTASK_STATUS, "", "", "{}", "{}", now, now),
        )
    return {"created": True, "video_task_id": VTASK_ID, "project_id": PLAIN_ID,
            "shot_id": anchor["shot_id"], "version_id": anchor["version_id"],
            "status": VTASK_STATUS}


def seed(data_dir: Path, *, with_demo: bool = True, touch: bool = True) -> dict:
    result: dict = {"data_dir": str(data_dir)}
    if with_demo:
        # Imported here so VISIONCRAFT_DATA_DIR is already in the environment when
        # backend.config reads it at import time.
        from tools.prepare_v1_demo import prepare as prepare_demo

        demo = prepare_demo()
        result["v1demo"] = {"project_id": demo["project_id"], "shot_count": demo["shot_count"]}
    result["guard"] = insert_guard_fixture(data_dir)
    result["plain"] = insert_plain_fixture(data_dir)
    result["video_task"] = ensure_video_task_fixture(data_dir)
    # Last, so the historical project is the most recently updated one the
    # frontend auto-selects.
    if touch:
        result["guard_touch"] = touch_guard_fixture(data_dir)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the historical regression fixtures")
    parser.add_argument("--data-dir", default="", help="Target data dir; defaults to VISIONCRAFT_DATA_DIR")
    parser.add_argument("--touch-only", action="store_true",
                        help="Only restore the guard fixture's updated_at (no demo rebuild)")
    parser.add_argument("--ensure-video-task", action="store_true",
                        help="Only ensure the historical video task fixture exists (no rebuild)")
    parser.add_argument("--no-touch", action="store_true", help="Skip the updated_at restore")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.touch_only:
        outcome = touch_guard_fixture(data_dir)
    elif args.ensure_video_task:
        outcome = ensure_video_task_fixture(data_dir)
    else:
        outcome = seed(data_dir, touch=not args.no_touch)

    print(json.dumps({"schema": "visioncraft.regression_fixtures.v1",
                      "seeded_at": datetime.now(timezone.utc).isoformat(),
                      **outcome}, ensure_ascii=False))
    if not (args.touch_only or args.ensure_video_task) and not outcome.get("v1demo"):
        print("FAIL: v1demo_main 未建成", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
