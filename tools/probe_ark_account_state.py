"""Ark 账号状态诊断：按「错误码在第几层」定序，零费用、零副作用。

为什么需要这个工具（2026-09-29 的教训）：
    我曾用「不存在的模型名」打创建接口，拿到 404 就判「账号闸门已放行」——**错的**。
    实测内部顺序是

        模型解析  ->  账号闸门  ->  参数校验

    非法模型名在**模型解析层**就短路成 404，**根本走不到账号闸门**，那条 404 与账号状态无关。

本工具同时跑两条互补的探针，用它们的**差异**把闸门逼出来：
    A) 非法模型名 + 有 content      -> 命中模型解析层（应 404）
    B) **合法模型** + `content: []` -> 命中账号闸门（403 欠费 / 400 参数）

读法：
    A=404 且 B=403 AccountOverdue*  -> **闸门仍在拦生成**（换 key 无用；去找客服/核现金余额）
    A=404 且 B=404 ModelNotOpen     -> **模型未开通**：控制台「开通管理」里开通即可（免费动作）。
                                       ⚠️ 这一层也**可能排在账号闸门之前**，所以它**不构成
                                       "账号已正常"的证明** —— 开通后复跑，看 B 变成 400 才算穿透。
    A=404 且 B=400 参数类            -> **闸门已放行**，可以进入真实付费验收
    A=401 或 B=401                   -> 凭据层问题（此时才值得重发 key）

附带：方舟在错误体里会写 `Your account <数字>`，本工具把它抠出来打印。
    它 = **key 所属账号**的 id（不是控制台登录账号），可与控制台右上角的账号 id 直接比对。

**一个必须一起读的前提（2026-09-29 21:4x 补）**：上面这张"读法"表**默认我们的 key 属于
费用中心里看到的那个账号**。若费用中心显示"零消费、零欠费、零券"，而这里仍报 `AccountOverdueError`，
那么**先怀疑"key 与账号不是同一个"**——此时重发 key 其实是**可能有效的**。为了能人工比对，
本工具打印一把 **key 指纹**（前 6 / 后 4 / sha256 前缀，**不打印完整 key**），拿去方舟控制台的
「API Key 管理」里找同款即可判定。方舟自 2026-09-17 12:00 起改用新明文格式（`ark-` 前缀）。

零费用保证：两次 POST 都不可能创建任务——A 的模型名不存在；B 的 content 为空（无提示词）。
并且**提交前后各查一次任务总数**，断言 `total` 不变 —— 零副作用是**取证**出来的，不是声明的。

用法：
    .venv/Scripts/python.exe tools/probe_ark_account_state.py
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")

from backend.config import init_environment  # noqa: E402

init_environment()

from backend.providers.video_provider import _ark_api_key  # noqa: E402

BASE = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
TASKS = BASE + "/contents/generations/tasks"
MODELS = BASE + "/models"

BAD_MODEL = "probe-definitely-not-a-real-model-0000"


def _post(key: str, payload: dict) -> tuple[int, str, str]:
    """返回 (http_status, error.code, body[:400])。"""
    req = urllib.request.Request(
        TASKS,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, "", resp.read().decode("utf-8", errors="replace")[:400]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        code = ""
        try:
            err = (json.loads(body).get("error") or {})
            code = str(err.get("code") or "")
        except Exception:  # noqa: BLE001
            pass
        return exc.code, code, body[:400]


def _tasks_total(key: str):
    req = urllib.request.Request(
        TASKS, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("total")
    except Exception as exc:  # noqa: BLE001
        return f"ERR {type(exc).__name__}"


def _models(key: str):
    req = urllib.request.Request(
        MODELS, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data") or []
        want = os.getenv("VOLC_VIDEO_MODEL") or "doubao-seedance-2-0-260128"
        by_id = {m.get("id"): m for m in items}
        m = by_id.get(want)
        # 注意：可用模型**可能没有 status 字段**（字段缺失 ≠ 未列出）。
        # 早先把这两者混为一谈，会把"在列且可用"误报成"不在列"——已在 §14.28 记录。
        if m is None:
            return len(items), False, None, False
        return len(items), True, m.get("status"), ("status" in m)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"ERR {type(exc).__name__}", None


def main() -> int:
    key = _ark_api_key()
    if not key:
        print("NO_KEY：加载链里读不到 ark 密钥（VOLC_VIDEO_API_KEY / VOLC_API_KEY 均为空）")
        return 2
    model = os.getenv("VOLC_VIDEO_MODEL") or "doubao-seedance-2-0-260128"
    print(f"key source : {[n for n in ('VOLC_VIDEO_API_KEY', 'VOLC_API_KEY', 'ARK_API_KEY') if os.getenv(n)]}")
    print(f"key length : {len(key)}  (只报长度，不打印值)")
    print(f"key 指纹   : head={key[:6]}…  tail=…{key[-4:]}  sha256={hashlib.sha256(key.encode()).hexdigest()[:10]}")
    print("             ↑ 拿去方舟控制台「API Key 管理」比对：找得到=同账号；找不到=这是**另一个账号**的 key")
    print(f"endpoint   : {BASE}")
    print("-" * 70)

    n_models, listed, status, has_status = _models(key)
    print(f"[models]   可见 {n_models} 个；目标模型 {model!r} -> listed={listed} "
          f"status={status!r} (has_status_field={has_status})")

    before = _tasks_total(key)
    print(f"[tasks]    提交前 total = {before}")
    print("-" * 70)

    print("A) 非法模型名 + 有 content   （命中：模型解析层）")
    sa, ca, ba = _post(key, {"model": BAD_MODEL, "content": [{"type": "text", "text": "probe"}],
                             "duration": 5, "ratio": "16:9", "resolution": "720p"})
    print(f"   -> HTTP {sa}  code={ca or '(none)'}")
    print(f"   -> {ba[:200]}")

    print()
    print(f"B) 合法模型 {model!r} + content:[]  （命中：账号闸门）  <-- 决定性")
    sb, cb, bb = _post(key, {"model": model, "content": [], "duration": 5,
                             "ratio": "16:9", "resolution": "720p"})
    print(f"   -> HTTP {sb}  code={cb or '(none)'}")
    print(f"   -> {bb[:260]}")
    acct = ""
    m = re.search(r"Your account (\d+)", bb) or re.search(r"Your account (\d+)", ba)
    if m:
        acct = m.group(1)
        print(f"   -> key 所属账号 id = {acct}（拿去控制台右上角比对；不是登录账号就是换账号了）")
    print("-" * 70)

    after = _tasks_total(key)
    print(f"[tasks]    提交后 total = {after}")
    if isinstance(before, int) and isinstance(after, int):
        print("safety:     " + ("任务总数未变 -> 零副作用、零费用 ✅" if before == after
                                else f"⚠️ 总数变化 {before}->{after}，需立刻核对计费"))
    print("-" * 70)

    print("结论：")
    if sb == 401 or sa == 401:
        print("  凭据层失败（401）-> 这把 key 本身有问题，重发 key 才**有意义**。")
    elif str(cb).startswith("AccountOverdue") or sb == 403:
        print("  账号闸门**仍在拦生成**（B 命中账号层 403）。模型解析排在闸门之前，")
        print("  所以 A 的 404 是**空转**、与账号状态无关。")
        print("  下一步分两种世界，别混：")
        print("    · 费用中心能看到欠费/负数现金  -> 补现金并结清账单（换 key 无用）")
        print("    · 费用中心显示零消费零欠费零券 -> **先怀疑 key 属于另一个账号**：")
        print("      拿上面的 key 指纹去方舟控制台「API Key 管理」比对；找不到就换本账号新建的 key 再跑本工具。")
    elif cb == "ModelNotOpen":
        print(f"  凭据层 OK，且**不再报欠费**：B 停在「模型未开通」（账号 {acct or '?'} 未开通 {model}）。")
        print("  这是控制台里的**免费动作**：方舟控制台 ->「开通管理」-> 开通该模型（按量计费，开通本身不收费）。")
        print("  ⚠️ 但这**还不是「账号已正常」的证明**：若开通检查排在账号闸门之前，这一层同样是短路。")
        print("  → 开通后复跑本工具；B 变成 400 参数类才算真正穿透到闸门之后。")
    elif sb == 400 or cb in ("MissingParameter", "InvalidParameter"):
        print("  账号闸门**已放行**（B 停在参数校验层 400）。可进入真实付费验收（需逐次授权）。")
    else:
        print(f"  未归类：A={sa}/{ca or '-'}  B={sb}/{cb or '-'} —— 人工核对 response body。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
