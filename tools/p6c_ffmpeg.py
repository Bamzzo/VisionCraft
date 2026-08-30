"""P6-C 真实 FFmpeg 探测与夹具。不修改系统 PATH，不安装软件。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

INSTALL_HINT = """
环境阻塞：本机没有可用的 ffmpeg / ffprobe，因此 P6-C 真实合成、真实预览和 p6c-real-*.png 均未执行，也不得报成通过。

本任务不会改系统 PATH。可把 ffmpeg/ffprobe 放到以下任一位置，或加入当前终端 PATH：

1. D:\\Agent\\summercompetition\\StoryCraft\\.tools\\ffmpeg\\bin\\
2. 设置 VISIONCRAFT_FFMPEG_DIR 指向同时包含 ffmpeg 与 ffprobe 的目录
3. 打开 https://www.gyan.dev/ffmpeg/builds/ 下载 ffmpeg-release-essentials.zip
4. 关闭并重新打开终端后执行：
   Get-Command ffmpeg
   ffmpeg -version
   ffprobe -version
5. 再运行：
   .venv\\Scripts\\python.exe tools\\test_p6c_real_assembly.py
   .venv\\Scripts\\python.exe tools\\test_p6c_real_assembly_browser.py

当前 P6 只拼接视频流并统一到 1280x720 yuv420p，使用 -an，不处理音频、旁白或配乐。
""".strip()


def _exe_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _candidate_dirs() -> list[Path]:
    env_dir = os.environ.get("VISIONCRAFT_FFMPEG_DIR", "").strip()
    dirs = []
    if env_dir:
        dirs.append(Path(env_dir))
    dirs.extend(
        [
            REPO_ROOT.parent / ".tools" / "ffmpeg" / "bin",
            REPO_ROOT.parent / ".tools" / "ffmpeg",
            REPO_ROOT / ".tools" / "ffmpeg" / "bin",
            REPO_ROOT.parent / "tools" / "ffmpeg" / "bin",
            Path(r"C:\ffmpeg\bin"),
        ]
    )
    return dirs


def _from_dir(directory: Path, name: str) -> Path | None:
    for folder in (directory, directory / "bin"):
        candidate = folder / _exe_name(name)
        if candidate.is_file():
            return candidate
    return None


def ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for directory in _candidate_dirs():
        hit = _from_dir(directory, "ffmpeg")
        if hit:
            return str(hit)
    return None


def ffprobe_bin() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    for directory in _candidate_dirs():
        hit = _from_dir(directory, "ffprobe")
        if hit:
            return str(hit)
    return None


def ensure_process_path() -> str | None:
    """把包含 ffmpeg/ffprobe 的目录加入当前进程 PATH，不改用户或系统 PATH。"""
    ffmpeg = ffmpeg_bin()
    ffprobe = ffprobe_bin()
    if not ffmpeg or not ffprobe:
        return None
    bin_dir = str(Path(ffmpeg).resolve().parent)
    probe_dir = str(Path(ffprobe).resolve().parent)
    if bin_dir.lower() != probe_dir.lower():
        return None
    current = os.environ.get("PATH", "")
    parts = [item for item in current.split(os.pathsep) if item]
    if all(item.lower() != bin_dir.lower() for item in parts):
        os.environ["PATH"] = bin_dir + os.pathsep + current
    return bin_dir


def ffmpeg_available() -> bool:
    ensure_process_path()
    return bool(ffmpeg_bin() and ffprobe_bin())


def ffmpeg_version() -> str:
    exe = ffmpeg_bin()
    if not exe:
        return ""
    completed = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False)
    line = (completed.stdout or completed.stderr or "").splitlines()
    return line[0] if line else ""


def ffprobe_version() -> str:
    exe = ffprobe_bin()
    if not exe:
        return ""
    completed = subprocess.run([exe, "-version"], capture_output=True, text=True, check=False)
    line = (completed.stdout or completed.stderr or "").splitlines()
    return line[0] if line else ""


def make_color_clip(path: Path, *, color: str, size: str, duration: float) -> None:
    exe = ffmpeg_bin()
    if not exe:
        raise RuntimeError("ffmpeg 不可用")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:d={duration}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-an",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("生成测试视频夹具失败")


def probe_video(path: Path) -> dict:
    exe = ffprobe_bin()
    if not exe:
        raise RuntimeError("ffprobe 不可用")
    command = [
        exe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,codec_name,pix_fmt,nb_frames",
        "-show_entries",
        "format=duration,size",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("ffprobe 无法识别视频")
    payload = json.loads(completed.stdout or "{}")
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec": stream.get("codec_name") or "",
        "pix_fmt": stream.get("pix_fmt") or "",
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "has_video": bool(stream.get("codec_name")),
    }


def make_sine_wav(path: Path, *, duration: float, frequency: int = 440) -> None:
    exe = ffmpeg_bin()
    if not exe:
        raise RuntimeError("ffmpeg 不可用")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("生成测试音频夹具失败")


def probe_media(path: Path) -> dict:
    info = probe_video(path)
    exe = ffprobe_bin()
    completed = subprocess.run(
        [exe, "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout or "{}") if completed.returncode == 0 else {}
    audio = [item for item in payload.get("streams") or [] if item.get("codec_type") == "audio"]
    info["has_audio"] = bool(audio)
    info["audio_codec"] = (audio[0].get("codec_name") if audio else "") or ""
    info["audio_sample_rate"] = int((audio[0].get("sample_rate") if audio else 0) or 0)
    info["audio_channels"] = int((audio[0].get("channels") if audio else 0) or 0)
    info["audio_streams"] = len(audio)
    return info


def make_color_clip_with_sine(
    path: Path, *, color: str, size: str, duration: float, frequency: int = 440
) -> None:
    exe = ffmpeg_bin()
    if not exe:
        raise RuntimeError("ffmpeg 不可用")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-shortest",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("生成带音频测试视频夹具失败")


def audio_mean_volume(path: Path, *, start: float = 0.0, duration: float = 0.4) -> float:
    exe = ffmpeg_bin()
    if not exe:
        raise RuntimeError("ffmpeg 不可用")
    command = [
        exe,
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    blob = (completed.stderr or "") + (completed.stdout or "")
    for line in blob.splitlines():
        if "mean_volume" in line:
            try:
                return float(line.rsplit(":", 1)[-1].replace("dB", "").strip())
            except ValueError:
                return -120.0
    return -120.0
