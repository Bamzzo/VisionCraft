"""P7-D 无费用可用性测试：默认预选、生成模式文案、审计口径、导出合同。

真实网络请求：否。费用：0 元。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.config import init_environment
from backend.database import init_db
from backend.providers.capabilities import get_provider_capabilities, get_provider_diagnostics
from backend.providers.llm_catalog import DEEPSEEK_FLASH, DEEPSEEK_VISION
from tools.live_run_audit import LAST_LIVE_RUN, COUNT_LABELS, has_secret_leak, write_audit_reports


def pass_(msg: str) -> None:
    print(f"PASS: {msg}")


@contextmanager
def _without_env(*keys: str):
    saved = {key: os.environ.pop(key, None) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_default_preselects() -> None:
    with _without_env("VISIONCRAFT_VIDEO_PROVIDER"):
        payload = get_provider_capabilities()
        assert payload["stages"]["text_understanding"]["default_provider"] == "deepseek"
        assert payload["stages"]["text_understanding"]["default_model"] == DEEPSEEK_FLASH
        assert payload["stages"]["vision_review"]["default_provider"] == "deepseek"
        assert payload["stages"]["vision_review"]["default_model"] == DEEPSEEK_VISION
        assert payload["stages"]["video_generation"]["default_provider"] == "minimax"
        assert payload["default_video_provider"] == "minimax"
    pass_("文本 / 视觉 / 视频默认仅为预选值")


def test_generation_mode_labels_and_live_access() -> None:
    payload = get_provider_capabilities()
    modes = {item["id"]: item["label"] for item in payload["generation_modes"]}
    assert "不调用真实模型" in modes["mock"]
    assert "失败即失败" in modes["live_strict"]
    assert "允许本地回退" in modes["live_with_local_fallback"]
    access = payload["live_access"]
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "SILICONFLOW_API_KEY", "VISIONCRAFT_ALLOW_LIVE"):
        assert forbidden not in blob
        assert forbidden not in access["hint"]
    diagnostics = get_provider_diagnostics()
    assert diagnostics["llm"]["provider"] == "deepseek"
    assert diagnostics["llm"]["model"] == DEEPSEEK_FLASH
    assert diagnostics["llm"]["status_label"] in {"已配置", "未配置"}
    pass_("Mock / live_strict / live_with_local_fallback 文案正确且无环境变量名")


def test_last_live_run_four_plus_one() -> None:
    assert LAST_LIVE_RUN["video_submits_new"] == 4
    assert LAST_LIVE_RUN["video_tasks_reused"] == 1
    assert LAST_LIVE_RUN["preexisting_remote_tasks"] == 1
    assert LAST_LIVE_RUN["unique_remote_tasks"] == 5
    assert LAST_LIVE_RUN["duplicate_submits"] == 0
    assert LAST_LIVE_RUN["duplicate_assets"] == 0
    assert COUNT_LABELS["video_submits_new"] == "本次新提交任务"
    assert COUNT_LABELS["preexisting_remote_tasks"] == "中断前已有任务"
    assert COUNT_LABELS["video_tasks_reused"] == "复用任务"
    assert COUNT_LABELS["unique_remote_tasks"] == "唯一远程任务"
    pass_("4 新提交 + 1 复用 = 5 唯一远程任务，复用不算新调用")


def test_audit_files_have_no_secrets() -> None:
    out = ROOT / "output" / "playwright" / "_p7d_usability_audit"
    shutil.rmtree(out, ignore_errors=True)
    try:
        paths = write_audit_reports(out, result=dict(LAST_LIVE_RUN), reconstructed=False)
        blob = "".join(path.read_text(encoding="utf-8") for path in paths.values())
        assert has_secret_leak(blob) is False
        lowered = blob.lower()
        assert "sk-" not in lowered
        assert "authorization" not in lowered
        assert "data:image" not in lowered
        assert "base64," not in lowered
        audit = json.loads(paths["audit"].read_text(encoding="utf-8"))
        for key in (
            "project_id",
            "generation_mode",
            "text_calls_total",
            "vision_calls_total",
            "video_submits_new",
            "video_tasks_reused",
            "unique_remote_tasks",
            "ffmpeg_ran",
            "final_cut",
            "preview_ok",
            "download_ok",
            "cleanup_verified",
        ):
            assert key in audit
        pass_("审计文件不含 Key、Data URL 或 Base64")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def main() -> None:
    init_environment()
    init_db()
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_LLM", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VISION", None)
    os.environ.pop("VISIONCRAFT_ALLOW_LIVE_VIDEO", None)
    test_default_preselects()
    test_generation_mode_labels_and_live_access()
    test_last_live_run_four_plus_one()
    test_audit_files_have_no_secrets()
    print("PASS: P7-D usability (no live network, cost 0)")
    print("INFO: real_network=否 cost_cny=0")


if __name__ == "__main__":
    main()
