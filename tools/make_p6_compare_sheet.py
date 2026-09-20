"""把三家视频模型对同一镜头的输出拼成一张对比图。

演示用素材：三家在**同一提示词、同一首帧**下各生成一次，抽同一时刻的一帧
横向拼接，一眼能看出构图、人物比例与元素位置的差异。

源视频是 2026-08-28 的真实付费产物，住在工作库里；本工具只读取与拼接，
不发任何请求。无 FFmpeg 时明确失败，不产出占位图。

用法::

    .venv\\Scripts\\python.exe tools\\make_p6_compare_sheet.py
    .venv\\Scripts\\python.exe tools\\make_p6_compare_sheet.py --at 1.5
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from tools.p6c_ffmpeg import ensure_process_path, ffmpeg_bin, probe_video  # noqa: E402

DEFAULT_SOURCE_DIR = ROOT / "backend" / "data" / "projects"
DEFAULT_OUT = ROOT / "output" / "playwright" / "p6demo" / "compare-three-providers.png"

#: 顺序即图上从左到右的顺序，与 prepare_p6_demo_samples.py 的 COMPARE_SOURCES 对齐。
SOURCES = (
    ("i2v_ark_b97ea96a", "asset_0fab834ac2.mp4", "ark"),
    ("i2v_dashscope_612ff237", "asset_89afa38ec3.mp4", "dashscope"),
    ("i2v_minimax_f12c30c7", "asset_edb963f99f.mp4", "minimax"),
)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font_file() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _label_filter(index: int, text: str, font: str | None) -> str:
    base = f"[{index}:v]scale=-2:480,setpts=PTS-STARTPTS"
    if not font:
        return f"{base}[v{index}]"
    escaped = font.replace(":", "\\:")
    return (
        f"{base},drawtext=fontfile='{escaped}':text='{text}':x=12:y=12:"
        f"fontsize=26:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8[v{index}]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成三家同镜头对比图")
    parser.add_argument("--at", type=float, default=1.0, help="抽取第几秒的帧（默认 1.0）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 PNG 路径")
    parser.add_argument("--source-projects-dir", default=str(DEFAULT_SOURCE_DIR),
                        help="真实产物所在的项目目录")
    args = parser.parse_args()

    ensure_process_path()
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        print("FAIL: 找不到 FFmpeg，无法拼接对比图。")
        return 1

    source_dir = Path(args.source_projects_dir).resolve()
    font = _font_file()
    inputs: list[str] = []
    wanted: list[str] = []
    for index, (project_id, filename, label) in enumerate(SOURCES):
        path = source_dir / project_id / filename
        if not path.is_file():
            print(f"FAIL: 缺少源视频 {path}")
            print("      这是既有的真实付费产物，不能凭空生成。")
            return 1
        info = probe_video(path)
        caption = f"{label} {info.get('width')}x{info.get('height')}"
        wanted.append(caption)
        inputs += ["-ss", str(args.at), "-i", str(path)]

    chain = ";".join(
        _label_filter(i, wanted[i], font) for i in range(len(SOURCES))
    ) + ";" + "".join(f"[v{i}]" for i in range(len(SOURCES))) + \
        f"hstack=inputs={len(SOURCES)}[out]"

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", *inputs,
         "-filter_complex", chain, "-map", "[out]", "-frames:v", "1", str(target)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"FAIL: ffmpeg 退出码 {proc.returncode}")
        print(proc.stderr[-800:])
        return 1

    print(f"INFO: 对比图 {target}  {target.stat().st_size / 1024:.0f} KB")
    for caption in wanted:
        print(f"       {caption}")
    if not font:
        print("INFO: 未找到字体，图上没有文字标签。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
