"""免费预检：真实静帧能不能挂上去、挂上之后 i2v 校验能不能过、图像链会不会外发。

三项发现（本脚本用实测把它们钉住，而不是靠读代码推断）
------------------------------------------------------------------
【A】分镜阶段的首/末帧是 **SVG 占位图**
    _insert_shots 无条件调用 generate_image_asset；本机图像链
    （VISIONCRAFT_IMAGE_PROVIDER 默认 siliconflow，.env 无其 key → 退 ark）
    最终落 create_placeholder_svg。media_transfer_service 明确拒绝 SVG。

【B】register-local 只换首帧，**末帧原样保留**
    于是挂完真 JPEG 后：首帧 = JPEG、末帧 = SVG。
    ⇒ 用 keyframes 模式：闸门先放行并**自增 live_video_call_count**，
      之后媒体准备才抛 SVG_NOT_ALLOWED —— 钱没花，但名额白吃一个。
    ⇒ 必须用 **i2v**（只发首帧）。

【C】图像链会真的向 ark 发包
    只要 ARK_API_KEY 在环境里，generate_image_asset 就会 POST
    https://ark.cn-beijing.volces.com/api/v3/images/generations（账号已不可用）。
    ⇒ 分镜阶段每个关键帧一次，纯浪费 + 对第三方产生真实流量。
    ⇒ 进程级清掉 ARK_API_KEY，图像链直接落本地占位图，**零外发**。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

TS = time.strftime("%Y%m%d-%H%M%S")
TMP_DATA = ROOT / "output" / f"tmp-kfcheck-{TS}"
REPORT_DIR = Path(os.getenv("VISIONCRAFT_CHECK_OUT")
                  or (ROOT / "output" / "playwright" / "live-4shot-20261002"))
REPORT_DIR.mkdir(parents=True, exist_ok=True)
ASSETS = ROOT / "output" / "live-assets"

LIVE_KEYS = (
    "VISIONCRAFT_ALLOW_LIVE_LLM",
    "VISIONCRAFT_ALLOW_LIVE_VIDEO",
    "VISIONCRAFT_ALLOW_LIVE_VISION",
    "VISIONCRAFT_LIVE_MAX_VIDEO_CALLS",
    "VISIONCRAFT_LIVE_BUDGET_CNY",
)
os.environ["VISIONCRAFT_DATA_DIR"] = str(TMP_DATA)
for _k in LIVE_KEYS:
    os.environ.pop(_k, None)

ATTEMPTS: list[str] = []
_real_urlopen = urllib.request.urlopen


def _tripwire(req, *a, **k):  # noqa: ANN001
    url = getattr(req, "full_url", None) or str(req)
    ATTEMPTS.append(url)
    raise AssertionError(f"NETWORK TRIPWIRE：{url}")


urllib.request.urlopen = _tripwire  # type: ignore[assignment]

RESULTS: list[dict] = []
EVIDENCE: dict = {}


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))
    return bool(ok)


def probe_image_chain(pid: str, label: str) -> tuple[str, list[str]]:
    """调一次图像链，返回 (落盘路径, 本次外发 URL 列表)。"""
    from backend.providers.image_provider import ImageAssetRequest, generate_image_asset

    ATTEMPTS.clear()
    asset_id = generate_image_asset(ImageAssetRequest(
        project_id=pid, asset_type="first-frame", name=f"probe-{label}",
        description="probe", prompt="probe", accent="#2563eb",
    ))
    from backend.database import connect

    with connect() as conn:
        row = conn.execute("SELECT file_path, source FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return (row["file_path"], row["source"], list(ATTEMPTS))  # type: ignore[return-value]


def main() -> int:
    from backend.config import init_environment  # noqa: E402
    from backend.database import connect, init_db, utc_now  # noqa: E402

    init_environment()
    init_db()

    source = ("方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。"
              "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。"
              "但是族中长老已经设下阻碍，他只能选择冒险一搏。")

    from fastapi.testclient import TestClient  # noqa: E402
    from backend.main import app  # noqa: E402

    client = TestClient(app)
    resp = client.post("/api/projects", json={
        "title": f"挂帧预检-{TS}", "source_text": source,
        "aspect_ratio": "16:9", "duration_seconds": 5,
        "output_resolution": "1280x720", "shot_count_mode": "auto",
        "review_mode": False, "generation_mode": "mock",
    })
    pid = resp.json()["id"]
    check("创建项目", resp.status_code == 200, pid)

    # ---- A. 图像链现状：外发 + 落 SVG -------------------------------------
    svg_path, source_tag, net1 = probe_image_chain(pid, "with-ark-key")
    EVIDENCE["image_chain_with_ark_key"] = {"file_path": svg_path, "source": source_tag, "outbound": net1}
    check("A1 图像链落盘为 SVG 占位图",
          str(svg_path).lower().endswith(".svg"), f"{svg_path} | source={source_tag}")
    check("A2 ARK_API_KEY 在环境里时，图像链**真的外发**到 ark",
          len(net1) >= 1, f"{net1}")

    # ---- C. 抑制 ARK_API_KEY 后：不再外发，仍落 SVG -------------------------
    saved_ark = os.environ.pop("ARK_API_KEY", None)
    saved_volc = os.environ.pop("VOLC_API_KEY", None)
    saved_volc_img = os.environ.pop("VOLC_IMAGE_API_KEY", None)
    try:
        svg_path2, source_tag2, net2 = probe_image_chain(pid, "no-ark-key")
    finally:
        if saved_ark is not None:
            os.environ["ARK_API_KEY"] = saved_ark
        if saved_volc is not None:
            os.environ["VOLC_API_KEY"] = saved_volc
        if saved_volc_img is not None:
            os.environ["VOLC_IMAGE_API_KEY"] = saved_volc_img
    EVIDENCE["image_chain_without_ark_key"] = {"file_path": svg_path2, "source": source_tag2, "outbound": net2}
    check("C1 清掉 ARK_API_KEY 后图像链**零外发**", len(net2) == 0, f"{net2}")
    check("C2 清掉 ARK_API_KEY 后仍然落本地占位图（行为等价）",
          str(svg_path2).lower().endswith(".svg"), f"{svg_path2} | source={source_tag2}")

    # ---- B. 造镜头：首/末帧都指向 SVG（复刻分镜阶段产物） ------------------
    shot_id = f"shot_{uuid.uuid4().hex[:10]}"
    version_id = f"version_{uuid.uuid4().hex[:10]}"
    now = utc_now()
    with connect() as conn:
        conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                     ("production_ready", now, pid))
        conn.execute(
            """INSERT INTO shots
            (id, project_id, shot_index, title, description, characters, scene, camera_motion,
             visual_prompt, negative_prompt, audio_prompt, status, retry_count, current_version_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shot_id, pid, 1, "镜01", "动作", "[]", "场景", "固定",
             "prompt", "", "", "keyframes_ready", 0, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO shot_versions
            (id, shot_id, version_number, description, visual_prompt, negative_prompt, audio_prompt,
             first_frame_path, last_frame_path, video_path, video_mode, provider, model, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, shot_id, 1, "动作", "prompt", "", "",
             svg_path, svg_path, None, "t2v", None, None, "probe", now),
        )

    jpg = ASSETS / "kf1-mountain-figure-1280x720.jpg"
    check("真实静帧素材存在", jpg.is_file(), str(jpg))
    if not jpg.is_file():
        return 1

    ATTEMPTS.clear()  # 只统计"挂帧 + 生成"这一段的外发
    up = client.post(
        f"/api/projects/{pid}/shots/{shot_id}/keyframes/register-local",
        files={"file": ("kf1.jpg", jpg.read_bytes(), "image/jpeg")},
    )
    check("B1 register-local 接受真实 JPEG", up.status_code == 200,
          f"HTTP {up.status_code} {up.text[:150]}")

    with connect() as conn:
        cur = conn.execute("SELECT current_version_id FROM shots WHERE id = ?", (shot_id,)).fetchone()["current_version_id"]
        ver = conn.execute(
            "SELECT id, first_frame_path, last_frame_path, video_mode FROM shot_versions WHERE id = ?",
            (cur,),
        ).fetchone()
    first_suffix = (ver["first_frame_path"] or "").rsplit(".", 1)[-1].lower()
    last_suffix = (ver["last_frame_path"] or "").rsplit(".", 1)[-1].lower()
    EVIDENCE["after_attach"] = {
        "first_frame_path": ver["first_frame_path"],
        "last_frame_path": ver["last_frame_path"],
        "video_mode": ver["video_mode"],
    }
    check("B2 挂载后首帧变成 JPEG", first_suffix in {"jpg", "jpeg"}, str(ver["first_frame_path"]))
    check("B3 挂载后末帧**仍是 SVG**（只换首帧 → keyframes 模式的陷阱）",
          last_suffix == "svg", str(ver["last_frame_path"]))

    r_i2v = client.post(f"/api/projects/{pid}/shots/{shot_id}/video",
                        json={"video_mode": "i2v", "provider": "dashscope"})
    check("B4 i2v 端点接受排队（首帧校验通过、没卡在缺帧）",
          r_i2v.status_code == 200, f"HTTP {r_i2v.status_code} {r_i2v.text[:150]}")
    job = (r_i2v.json() or {}).get("job_id")
    job_body = client.get(f"/api/jobs/{job}").json() if job else {}
    blob = json.dumps(job_body, ensure_ascii=False)
    check("B5 i2v 被付费闸门拦下（尚未授权）",
          ("尚未授权" in blob) and job_body.get("status") == "failed", blob[:230])

    check("B6 挂帧 + 生成这一段零外发", len(ATTEMPTS) == 0, f"{ATTEMPTS[:3]}")

    report = {
        "status": "done", "generated_at": TS, "temp_data_dir": str(TMP_DATA),
        "evidence": EVIDENCE,
        "selected_video_mode": "i2v",
        "selected_provider": (r_i2v.json() or {}).get("provider") if r_i2v.status_code == 200 else None,
        "selected_model": (r_i2v.json() or {}).get("model") if r_i2v.status_code == 200 else None,
        "checks": RESULTS,
        "passed": sum(1 for r in RESULTS if r["ok"]), "total": len(RESULTS),
    }
    out = REPORT_DIR / "keyframe_attach_precheck.json"
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
