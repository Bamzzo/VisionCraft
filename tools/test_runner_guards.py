"""无费用验收：无费用回归驱动器自身的两处守卫。

这个文件测的不是产品，而是**让"全量回归的结论可信"这件事本身成立**的两个前提。两条都
来自实测事故，不是假想：

1. **单实例锁**（`acquire_run_lock`）。两个全量回归并行会互相抢 8070-8109 端口与 CPU，
   失败项既不是产品缺陷也不是用例缺陷，纯粹是自己踩自己。实测踩过一次：一个被中断的
   实例没死透，与新一轮并行跑了十几分钟，那一项失败看起来像真的。锁让这种失败**不可能
   被误读**——要么只有一个实例，要么第二个直接说出来。

2. **绝不把陌生后端当成自己的**（`wait_for_backend`）。端口被残留 uvicorn 占着时，我们
   自己的子进程会因 EADDRINUSE 退出，而 `/api/health` 照样有人回 200；用例于是跑在别人
   的数据目录上，跑到一半对方消失，表现为"后端中途没了"。判据必须是**子进程自己宣布
   开始服务**（日志里的 `Uvicorn running on http://127.0.0.1:<port>`），不是健康检查能通。
   注意不能用 `Application startup complete` 代替：实测 uvicorn 先跑 lifespan 启动、之后
   才 bind 端口，**端口被占时这句照样会打印**。

本用例用临时锁路径与假进程，不碰驱动器此刻持有的真锁，也不占任何端口。

覆盖：
  1. 无锁时可获取，锁里记的是自己的 pid 与启动时间；
  2. 同进程重复获取不会自我拒绝；
  3. 持有者 pid 不存在时，过期锁可被接管；损坏的锁文件按过期处理；
  4. 释放后锁文件消失，重复释放不报错；
  5. `pid_alive` 对自己为真、对不存在的 pid 为假；
  6. `wait_for_backend` 在"日志只有 startup complete、子进程已死（端口被占）"时**判失败**；
  7. `wait_for_backend` 在"日志有 Uvicorn running on <本端口>"且健康检查通过时判成功；
  8. `wait_for_backend` 承认的端口必须与我们请求的一致（别的端口的启动行不算数）。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_no_cost_regression as R  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"PASS: {label}")
    else:
        FAILED += 1
        print(f"FAIL: {label}" + (f"  [{detail}]" if detail else ""))


class FakeProc:
    """够用的 Popen 替身：wait_for_backend 只用到 poll()。"""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


def capture(fn, *args, **kwargs) -> tuple[bool, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def test_run_lock(tmp_dir: Path) -> None:
    lock = tmp_dir / "runner.lock"
    check("初始状态没有锁文件", not lock.exists())

    got = R.acquire_run_lock(lock)
    check("无锁时可以获取", got is True)
    info = json.loads(lock.read_text(encoding="utf-8"))
    check("锁里记的是自己的 pid", int(info.get("pid") or 0) == os.getpid(), str(info))
    check("锁里记了启动时间", bool(info.get("started_at")), str(info.get("started_at")))

    # 同进程重复获取：持有者是自己，应当放行而不是把自己判成"另一个实例"。
    check("同进程重复获取不会自我拒绝", R.acquire_run_lock(lock) is True)

    R.release_run_lock()
    check("释放后锁文件被删除", not lock.exists())

    lock.write_text(json.dumps({"pid": 999999, "started_at": "stale"}), encoding="utf-8")
    took = R.acquire_run_lock(lock)
    check("持有者 pid 不存在时，过期锁可被接管", took is True)
    if took:
        check("接管后锁里换成新 pid", int(json.loads(lock.read_text(encoding="utf-8")).get("pid") or 0) == os.getpid())
    R.release_run_lock()
    check("接管后再释放，无残留", not lock.exists())

    lock.write_text("{ not json", encoding="utf-8")
    check("锁文件损坏时按过期处理并可接管", R.acquire_run_lock(lock) is True)
    R.release_run_lock()
    check("重复释放不报错", R.release_run_lock() is None)

    check("pid_alive(自己) 为真", R.pid_alive(os.getpid()) is True)
    check("pid_alive(不存在的 pid) 为假", R.pid_alive(999999) is False)


def test_backend_ownership(tmp_dir: Path) -> None:
    port = 8097
    other = 8098
    log = tmp_dir / "fake_backend.log"

    # 端口被占的真实日志长这样：lifespan 先成功（打印 startup complete），然后 bind 失败。
    # 只看 startup complete 会把这种情况误判成"我们的后端起来了"。
    log.write_text(
        "INFO:     Started server process [1234]\n"
        "INFO:     Waiting for application startup.\n"
        "INFO:     Application startup complete.\n"
        f"ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', {port}):\n"
        "INFO:     Application shutdown complete.\n",
        encoding="utf-8",
    )
    original = R.health_ok
    R.health_ok = lambda *a, **k: True  # 端口上确实有人应答，这正是陷阱所在
    try:
        result, out = capture(R.wait_for_backend, FakeProc(alive=False), port, log, 0)
        check("端口被占（startup complete + bind 失败 + 子进程已死）判失败", result is False, f"returned {result}")
        check("失败信息点明是端口被残留服务占用", "已被残留服务占用" in out, out.strip().replace("\n", " | ")[:110])

        # 子进程还活着但从未 bind：健康检查会通（那是别人的后端），必须继续等而不是放行。
        result, _ = capture(R.wait_for_backend, FakeProc(alive=True), port, log, 0, 2)
        check("只有 startup complete、没有 Uvicorn running on 时，不因健康检查放行", result is False, f"returned {result}")
    finally:
        R.health_ok = original

    # 真的起来了：日志里有本端口的启动行，健康检查也通。
    log.write_text(
        "INFO:     Started server process [1234]\n"
        "INFO:     Application startup complete.\n"
        f"INFO:     Uvicorn running on http://127.0.0.1:{port} (Press CTRL+C to quit)\n",
        encoding="utf-8",
    )
    R.health_ok = lambda *a, **k: True
    try:
        result, _ = capture(R.wait_for_backend, FakeProc(alive=True), port, log, 0, 3)
        check("日志里有本端口启动行且健康检查通过时判成功", result is True, f"returned {result}")

        # 启动行是**别的**端口：不能算数（否则"任何后端起来了"都会让我们放行）。
        result, _ = capture(R.wait_for_backend, FakeProc(alive=True), other, log, 0, 2)
        check("启动行端口与我们请求的不一致时判失败", result is False, f"returned {result}")
    finally:
        R.health_ok = original

    # 日志按水位截断：换端口重试后不能把上一轮的 bind 失败算到这一轮头上。
    log.write_text(
        f"ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', {port}):\n",
        encoding="utf-8",
    )
    offset = log.stat().st_size
    check("水位之前的 bind 失败不算进本轮", R.log_shows_bind_failure(log, offset) is False)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8098):\n")
    check("水位之后的 bind 失败会被识别", R.log_shows_bind_failure(log, offset) is True)


def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="vc_runner_guards_"))
    test_run_lock(tmp_dir)
    test_backend_ownership(tmp_dir)
    print()
    # 收尾行刻意避开独立的 "pass"/"fail" token：驱动器按 token 粗计断言数，
    # 一句 "N pass / M fail" 会被多算成一条断言（其余脚本都只打印 PASS: 行）。
    print(f"runner guards: {PASSED} ok / {FAILED} failing")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
