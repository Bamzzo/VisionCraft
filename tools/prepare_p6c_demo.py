"""可重复的 P6-C 本地演示样本。仅在本机有 FFmpeg 时生成真实短视频，不提交到 Git。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from tools.p6c_ffmpeg import INSTALL_HINT, ffmpeg_available, ffmpeg_version
from tools.test_p6c_real_assembly import _seed_project, _seed_real_shots


def main() -> None:
    if not ffmpeg_available():
        print("SKIP: 未准备演示样本，因为本机没有 FFmpeg。")
        print(INSTALL_HINT)
        return
    from backend.config import init_environment
    from backend.database import init_db

    init_environment()
    init_db()
    project_id = _seed_project("P6C 本地演示样本")
    _seed_real_shots(project_id)
    print(f"INFO: ffmpeg {ffmpeg_version()}")
    print(f"INFO: 已创建演示项目 {project_id}")
    print("打开工作台后选择该项目，进入「成片合成」即可真实合成。")
    print("当前 P6 不处理音频。测试结束后请只删除此前缀为 p6c_ 的演示项目。")


if __name__ == "__main__":
    main()
