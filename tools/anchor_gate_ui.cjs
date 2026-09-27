/**
 * 走「视觉锚点审核门」的共享浏览器辅助（界面路径）。
 *
 * 抽出来的原因同 ui_project_form.cjs：mock_web_smoke / v1_demo / local_keyframe_ui
 * 都要在确认分镜之后过这道门，各写一份就是第四次踩同一个坑。
 *
 * 门的行为：确认分镜不再直接落 production_ready，项目停在 awaiting_anchor_review，
 * 横幅给出「确认锚点并开始制作 / 去挂锚点 / 无锚点直接进入制作」。跳过一致性机制是
 * 重动作，要点两次（第一次武装、第二次放行）——刻意不用 window.confirm：无头浏览器
 * 默认自动关闭原生对话框，跳过会静默失败，症状和「点不动」一模一样。
 *
 * 只做界面交互与后端状态回查，不碰数据库。
 */
"use strict";

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";

async function projectStatus(page, projectId) {
  const res = await page.request.get(`${BASE}/api/projects/${projectId}`);
  if (!res.ok()) throw new Error(`GET /api/projects/${projectId} -> ${res.status()}`);
  return (await res.json()).status;
}

/** 等后端状态落进给定集合（单个字符串或数组），超时抛出当前状态。 */
async function waitStatus(page, projectId, targets, timeout = 40000) {
  const want = Array.isArray(targets) ? targets : [targets];
  const deadline = Date.now() + timeout;
  let last = "(未读到)";
  for (;;) {
    last = await projectStatus(page, projectId);
    if (want.includes(last)) return last;
    if (Date.now() > deadline) {
      throw new Error(`等待状态 ${want.join("/")} 超时，当前 ${last}`);
    }
    await page.waitForTimeout(250);
  }
}

/**
 * 过门：已挂锚点走「确认锚点并开始制作」；未挂锚点走两次点击的显式跳过。
 * 返回过门后的状态（默认断言为 production_ready）。
 */
async function passAnchorGate(page, projectId, { timeout = 40000, expect = "production_ready" } = {}) {
  const status = await waitStatus(page, projectId, ["awaiting_anchor_review", expect], timeout);
  if (status === expect) return status; // 调用方已经过门，幂等返回

  const confirmBtn = page.locator('#stageGateBanner [data-flow="anchor-confirm"]');
  await confirmBtn.waitFor({ state: "visible", timeout: 20000 });
  if (await confirmBtn.isEnabled()) {
    await confirmBtn.click();
  } else {
    const skipBtn = page.locator('#stageGateBanner [data-flow="anchor-confirm-skip"]');
    await skipBtn.waitFor({ state: "visible", timeout: 20000 });
    await skipBtn.click();
    // 第一次点击只是「武装」；等横幅真的换成第二次的文案再点，否则点到的是同一个旧节点。
    await page.waitForFunction(
      () => {
        const btn = document.querySelector('#stageGateBanner [data-flow="anchor-confirm-skip"]');
        return Boolean(btn && /确认跳过/.test(btn.textContent || ""));
      },
      null,
      { timeout: 10000 }
    );
    await skipBtn.click();
  }
  return waitStatus(page, projectId, expect, timeout);
}

module.exports = { passAnchorGate, waitStatus };
