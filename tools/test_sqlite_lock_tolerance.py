"""并发写不能把读变成 500：SQLite 的 busy 等待必须留足余量。

背景（2026-09-27 实测）：`POST /api/projects/{id}/run` 用 BackgroundTasks 在响应
**之后**才开始跑改编流程，而前端与验收脚本同时在轮询 `GET /api/projects/{id}`。
实测正常竞争窗口约 1.5s（见 output/repro_lock.txt），机器拥挤时更久。
SQLite 默认的 busy 超时只有 5 秒（Python `sqlite3.connect` 的隐式 timeout），
等不到就把 `sqlite3.OperationalError: database is locked` 抛成 500 ——
这正是 `test_ui_workbench.py` 在整轮回归里偶发失败的原因。

本用例用**确定性**手法复现，不靠偶发：另起一个连接 `BEGIN EXCLUSIVE` 持写锁
`HOLD_SECONDS` 秒，在持锁窗口内发一次 GET，断言它**不是 500**，
而是等到锁释放后拿到 200。

`HOLD_SECONDS` 取 10s 的理由（按本机实测，不按标称值推）：
  - 标称 5000ms 的 `busy_timeout` 在这台机器上的**真实放弃时刻约 7.4s**：
    SQLite 的重试序列每轮都要做一次文件锁系统调用，实际 elapsed 被放大到标称值的
    ~1.5 倍（可复核：output/lock_control_ab5000.txt 的 7.447s，以及本用例红轮实测的 7.3s）。
    所以持锁时长必须**大于 7.4s**，才区分得出「5000」与「20000」两种设置；
    第一版取 6.5s 就落进了「两种设置都能等到」的区间，用例因此测不出差别。
  - 又不能太大，否则用例变成慢测试：10s 相对修复后的真实上限（~29s）仍有 3 倍裕量。

顺带钉住配置本身：`BUSY_TIMEOUT_MS` 必须显著大于 `HOLD_SECONDS`，
否则将来有人把它调小，这里会直接指出来。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import DB_PATH, PROJECTS_DIR, init_environment
from backend.database import BUSY_TIMEOUT_MS, connect, init_db, utc_now

HOLD_SECONDS = 10.0
CREATED: list[str] = []


def _seed_project(title: str) -> str:
    project_id = f"locktol_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO projects
            (id, title, source_text, style, aspect_ratio, duration_seconds, shot_count_mode,
             status, routing_mode, assembly_stale, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, "锁容错用例", "cinematic clean realism", "16:9", 5, "auto",
             "production_ready", "direct", 0, now, now),
        )
    CREATED.append(project_id)
    return project_id


def _cleanup() -> None:
    for project_id in CREATED:
        if not str(project_id).startswith("locktol_"):
            continue
        with connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


class WriteLockHolder:
    """用一个独立连接持有 EXCLUSIVE 写锁，制造确定性的锁竞争。

    全程在**工作线程**里操作该连接，并显式 `check_same_thread=False`：
    sqlite3 默认禁止跨线程复用连接，而释放动作必须发生在持锁线程之外
    （否则主线程被 sleep 占住，就没法在窗口内发请求了）。
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.acquired = threading.Event()
        self.done = threading.Event()
        self.error: Exception | None = None
        self._conn: sqlite3.Connection | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "WriteLockHolder":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self.acquired.wait(timeout=15):
            detail = f"（{self.error!r}）" if self.error else ""
            raise SystemExit(f"FAIL: 独立连接 15 秒内没拿到 EXCLUSIVE 写锁{detail}")
        return self

    def _run(self) -> None:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
            conn.isolation_level = None
            conn.execute("BEGIN EXCLUSIVE")
            self._conn = conn
            self.acquired.set()
            time.sleep(self.seconds)
            conn.execute("ROLLBACK")
        except Exception as exc:  # noqa: BLE001
            self.error = exc
        finally:
            self.done.set()

    def stop(self) -> None:
        self.done.wait(timeout=self.seconds + 20)
        if self.error is not None:
            print(f"  注意：持锁线程报错 {self.error!r}")
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        if self._thread is not None:
            self._thread.join(timeout=10)


def main() -> None:
    # 配置本身的护栏：等待上限必须显著大于本用例制造的持锁时长。
    if BUSY_TIMEOUT_MS <= HOLD_SECONDS * 1000 * 1.5:
        print(
            f"FAIL: BUSY_TIMEOUT_MS={BUSY_TIMEOUT_MS} 太短，"
            f"不足以覆盖 {HOLD_SECONDS:.1f}s 的写锁窗口（应为 {HOLD_SECONDS*1500:.0f} 以上）"
        )
        raise SystemExit(1)
    print(f"PASS: busy 等待上限 {BUSY_TIMEOUT_MS}ms 显著大于用例持锁 {HOLD_SECONDS:.1f}s")

    init_environment()
    init_db()

    # 诊断：读路径连接上**真实生效**的等待上限（不是常量值）。常量写对、
    # PRAGMA 却被漏掉时，这两个数会分叉，这里能立刻看出来。
    with connect() as probe_conn:
        effective_busy = probe_conn.execute("PRAGMA busy_timeout").fetchone()[0]
    print(f"诊断：读路径连接上的 busy_timeout = {effective_busy}ms")

    from fastapi.testclient import TestClient

    from backend.main import app

    holder: WriteLockHolder | None = None
    try:
        project_id = _seed_project("锁容错")
        # raise_server_exceptions=False：本用例要**看见** 500，而不是让它变成异常。
        client = TestClient(app, raise_server_exceptions=False)

        baseline = client.get(f"/api/projects/{project_id}")
        if baseline.status_code != 200:
            raise SystemExit(f"FAIL: 基线读取返回 {baseline.status_code} {baseline.text[:160]}")
        print("PASS: 无竞争时项目读取返回 200")

        holder = WriteLockHolder(HOLD_SECONDS).start()
        print(f"PASS: 独立连接已持有 EXCLUSIVE 写锁，窗口 {HOLD_SECONDS:.1f}s")

        started = time.time()
        during = client.get(f"/api/projects/{project_id}")
        elapsed = time.time() - started

        if during.status_code != 200:
            print(
                f"FAIL: 并发写把读变成了 {during.status_code}（用时 {elapsed:.1f}s）——"
                f"busy 等待不足，真实回归里会表现为偶发 500。"
            )
            print(f"      正文：{' '.join(during.text.split())[:160]}")
            raise SystemExit(1)
        print(f"PASS: 持锁窗口内的读取返回 200（用时 {elapsed:.1f}s，没有变成 500）")

        # 防「假通过」：如果读根本没撞上锁，这条用例就没有证明力。
        if elapsed < HOLD_SECONDS * 0.7:
            print(
                f"FAIL: 读取只用了 {elapsed:.1f}s，说明它没撞上写锁，用例失去意义"
                f"（应至少等待 {HOLD_SECONDS*0.7:.1f}s）"
            )
            raise SystemExit(1)
        print(f"PASS: 读取确实等到了锁释放（{elapsed:.1f}s），用例的证明力成立")

        after = client.get(f"/api/projects/{project_id}")
        if after.status_code != 200:
            raise SystemExit(f"FAIL: 释放后读取返回 {after.status_code}")
        print("PASS: 锁释放后读取恢复正常 200")
    finally:
        if holder is not None:
            holder.stop()
        _cleanup()


if __name__ == "__main__":
    main()
