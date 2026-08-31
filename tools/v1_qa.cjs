/**
 * V1-QA 浏览器验收：界面质量、状态一致性与智能体工作流展示。
 * 由 tools/test_v1_qa_browser.py 通过 npx playwright 调用。不调用付费 API。
 *
 * 截图写入 output/playwright/v1-qa-*.png，不得入库。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const created = [];
const STAGE_IDS = ["text", "storyline", "bible", "storyboard", "keyframes", "video", "assembly", "export"];

function shortText(seed) {
  return (
    `方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊（${seed}）。` +
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
    "最终他停在山门前，留下未说完的话。"
  );
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function apiPost(page, p, body) {
  const res = await page.request.post(`${BASE}${p}`, { data: body });
  if (!res.ok()) throw new Error(`POST ${p} -> ${res.status()} ${await res.text()}`);
  return res.json();
}
async function apiGet(page, p) {
  const res = await page.request.get(`${BASE}${p}`);
  if (!res.ok()) throw new Error(`GET ${p} -> ${res.status()}`);
  return res.json();
}
async function waitProject(page, id, pred, timeout = 25000) {
  const start = Date.now();
  for (;;) {
    const proj = await apiGet(page, `/api/projects/${id}`);
    if (pred(proj)) return proj;
    if (Date.now() - start > timeout) throw new Error("waitProject 超时");
    await new Promise((r) => setTimeout(r, 400));
  }
}
async function driveToStoryboard(page, id) {
  await apiPost(page, `/api/projects/${id}/run`);
  const withOptions = await waitProject(page, id, (p) => (p.adaptation_options || []).length > 0);
  const optId = withOptions.adaptation_options[0].id;
  await apiPost(page, `/api/projects/${id}/adaptation/options/${optId}/select`);
  await apiPost(page, `/api/projects/${id}/adaptation/scope/confirm`, { option_id: optId });
  await waitProject(page, id, (p) => Boolean(p.story_bible));
  await apiPost(page, `/api/projects/${id}/adaptation/bible/confirm`, {});
  await waitProject(page, id, (p) => (p.storyboard_drafts || []).length > 0);
  await apiPost(page, `/api/projects/${id}/adaptation/storyboard/confirm`, {});
  return waitProject(page, id, (p) => (p.shots || []).length > 0);
}

async function uiCreateProject(page, title, text) {
  const formVisible = await page.locator("#projectForm:not(.hidden)").count();
  if (!formVisible) {
    await page.click("#newProjectBtn", { force: true });
  }
  await page.waitForSelector("#projectForm:not(.hidden)", { timeout: 8000 });
  await page.locator("#titleInput").scrollIntoViewIfNeeded();
  await page.fill("#titleInput", title);
  await page.locator("#sourceTextInput").scrollIntoViewIfNeeded();
  await page.waitForSelector("#sourceTextInput:visible", { timeout: 8000 });
  await page.fill("#sourceTextInput", text);
  await page.locator("#submitProjectBtn").scrollIntoViewIfNeeded();
  await page.click("#submitProjectBtn");
  await page.waitForFunction(
    (t) => document.querySelector(".project-item.active strong")?.textContent.includes(t.slice(0, 12)),
    title,
    { timeout: 10000 }
  );
  return page.locator(".project-item.active").getAttribute("data-project-id");
}
async function selectProject(page, id) {
  await page.click(`.project-item[data-project-id="${id}"]`);
  const modal = page.locator("#unsavedModal:not(.hidden)");
  try {
    await modal.waitFor({ state: "visible", timeout: 600 });
    await page.click("#unsavedDiscardBtn");
  } catch {
    /* 无未保存对话框 */
  }
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 8000 }
  );
}
async function openStage(page, stageId) {
  await page.click(`[data-stage-id="${stageId}"]`);
  const modal = page.locator("#unsavedModal:not(.hidden)");
  try {
    await modal.waitFor({ state: "visible", timeout: 500 });
    await page.click("#unsavedDiscardBtn");
  } catch {
    /* 无未保存对话框 */
  }
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-stage-id="${sid}"]`)?.getAttribute("aria-current") === "true",
    stageId,
    { timeout: 8000 }
  );
}
async function summaryValue(page, key) {
  return page.evaluate((k) => {
    for (const row of document.querySelectorAll("#summaryFields .summary-row")) {
      if (row.querySelector("dt")?.textContent.trim() === k) return row.querySelector("dd")?.textContent.trim();
    }
    return null;
  }, key);
}
async function horizontalOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}
async function deleteProject(page, id) {
  if (!id) return;
  const res = await page.request.delete(`${BASE}/api/projects/${id}`);
  if (!res.ok()) console.error(`删除临时项目失败 ${id}: ${res.status()}`);
}
function pass(msg) {
  console.log(`PASS: ${msg}`);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForFunction(
      () => (document.querySelector("#ratioInput")?.options?.length || 0) > 0,
      null,
      { timeout: 15000 }
    );
    await page.waitForFunction(
      () => {
        const list = document.querySelector("#projectList");
        return Boolean(list && (list.querySelector(".project-item") || (list.textContent || "").includes("暂无")));
      },
      null,
      { timeout: 15000 }
    );
    const charset = await page.evaluate(() => document.characterSet);
    if (charset !== "UTF-8") throw new Error(`文档编码不是 UTF-8：${charset}`);

    const titleA = `QA甲-超长中文标题用于检查换行与错位-${"影视创作工作台".repeat(6)}`.slice(0, 120);
    const idA = await uiCreateProject(page, titleA, shortText("A"));
    created.push(idA);

    const nav = await page.evaluate(() => {
      const nodes = [...document.querySelectorAll("#stageNav [data-stage-id]")];
      return nodes.map((node) => ({
        id: node.getAttribute("data-stage-id"),
        label: node.querySelector(".stage-node-label")?.textContent.trim(),
        state: node.querySelector(".stage-state-label")?.textContent.trim(),
        mark: node.querySelector(".stage-state-mark")?.textContent.trim(),
        access: node.querySelector(".stage-node-access")?.textContent.trim(),
        count: node.querySelector(".stage-node-count")?.textContent.trim(),
        hint: node.querySelector(".stage-node-hint")?.textContent.trim(),
        aria: node.getAttribute("aria-label") || "",
        viewable: node.getAttribute("data-viewable"),
        canExecute: node.getAttribute("data-can-execute"),
      }));
    });
    if (nav.map((item) => item.id).join(",") !== STAGE_IDS.join(",")) {
      throw new Error(`右侧 8 阶段不正确：${nav.map((item) => item.id).join(",")}`);
    }
    if (nav.some((item) => !item.state || !item.mark || !item.aria || !item.access || !item.count || !item.hint)) {
      throw new Error("阶段节点缺少中文状态、标记、可访问性、计数或前置提示");
    }
    if (!nav.find((item) => item.id === "storyline" && item.state === "跳过")) {
      throw new Error("短文本故事线选择应显示跳过");
    }
    pass("右侧 8 个阶段均可见，且状态不以颜色为唯一表达");

    const execBefore = await summaryValue(page, "当前阶段");
    const jobsBefore = await page.evaluate(() => document.querySelector("#jobMessage")?.textContent || "");
    await openStage(page, "export");
    const execAfter = await summaryValue(page, "当前阶段");
    if (execBefore !== execAfter) throw new Error("点击导出阶段改变了执行阶段");
    const jobsAfter = await page.evaluate(() => document.querySelector("#jobMessage")?.textContent || "");
    if (jobsBefore !== jobsAfter) throw new Error("点击阶段触发了新任务");
    const subtitle = await page.locator("#stageWorkspaceSubtitle").textContent();
    if (!subtitle.includes("查看：导出与交付") || !subtitle.includes("执行到：文本理解")) {
      throw new Error(`查看/执行阶段未分离：${subtitle}`);
    }
    const exportLocked = await page.locator("#stageWorkspace [data-stage-locked]").count();
    const exportHint = await page.locator("#stageWorkspace").innerText();
    if (!exportLocked && !exportHint.includes("请先合成成片") && !exportHint.includes("前往成片合成")) {
      throw new Error("未开始的导出阶段应给出可执行提示");
    }
    if (await page.locator("#assembleProjectBtn").count()) {
      throw new Error("导出阶段不应直接提供合成按钮");
    }
    if (!exportHint.includes("前往成片合成")) {
      throw new Error("无成片的导出页应显示前往成片合成");
    }
    pass("点击阶段只切换查看；未开始阶段可查看但不能执行非法操作");

    for (const stageId of ["bible", "storyboard", "keyframes", "video", "assembly", "export"]) {
      await openStage(page, stageId);
      const title = await page.locator("#stageWorkspaceTitle").textContent();
      if (!title) throw new Error(`打开 ${stageId} 后工作区标题为空`);
    }
    await page.screenshot({ path: path.join(OUT, "v1-qa-empty-1440.png"), fullPage: true });
    pass("Story Bible、分镜、关键帧、视频、成片、导出工作区可切换");

    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.waitForTimeout(200);
    if ((await horizontalOverflow(page)) > 2) throw new Error("1920 宽屏出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "v1-qa-1920.png"), fullPage: true });
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.waitForTimeout(200);
    if ((await horizontalOverflow(page)) > 2) throw new Error("1100 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "v1-qa-narrow-1100.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1440 工作台出现横向溢出");
    pass("1100 / 1440 / 1920 布局无横向滚动");

    const projA = await driveToStoryboard(page, idA);
    await apiPost(page, `/api/projects/${idA}/shots/${projA.shots[0].id}/keyframes/redraw`, { target: "both" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, idA);
    await openStage(page, "bible");
    const redoBtn = "#stageWorkspace [data-redo-btn]";
    await page.waitForSelector(redoBtn, { timeout: 8000 });
    if (await page.locator(redoBtn).isEnabled()) throw new Error("无修改时重做按钮应禁用");
    await page.fill("#bibleLogline", "QA 修改后的 logline：主角在雨夜做出抉择。");
    await page.waitForFunction(() => {
      const btn = document.querySelector("#stageWorkspace [data-redo-btn]");
      const dirty = document.querySelector("#bibleDirty")?.textContent || "";
      return btn && !btn.disabled && dirty.includes("有未保存修改");
    }, null, { timeout: 5000 });
    pass("无修改时重做按钮禁用，修改后启用");

    await page.click(redoBtn);
    await page.waitForFunction(() => {
      const text = (sid) => document.querySelector(`[data-stage-id="${sid}"] .stage-state-label`)?.textContent.trim();
      return text("storyboard") === "未开始" && text("keyframes") === "已失效";
    }, null, { timeout: 15000 });
    pass("上游重做后下游显示已失效");

    await openStage(page, "video");
    await page.waitForSelector("#stageWorkspace .asset-card", { timeout: 8000 });
    await page.click("#stageWorkspace .asset-card");
    await page.waitForSelector("#assetDetail #shotDescriptionInput", { timeout: 8000 });
    const longModel = await page.evaluate(() => {
      const hint = document.querySelector("#videoCapabilityHint")?.textContent || "";
      const desc = document.querySelector("#shotDescriptionInput");
      if (desc) desc.value = "超长中文错误示例：Provider seedance-pro-ultra-long-model-name-for-wrap 返回内容安全策略拦截，建议改写镜头描述后仅重生成此镜头。".repeat(2);
      desc?.dispatchEvent(new Event("input", { bubbles: true }));
      return {
        wrap: getComputedStyle(document.querySelector("#assetDetail") || document.body).overflowWrap,
        hint,
      };
    });
    if (longModel.wrap !== "anywhere" && longModel.wrap !== "break-word") {
      throw new Error(`详情区未设置安全换行：${longModel.wrap}`);
    }
    await page.screenshot({ path: path.join(OUT, "v1-qa-video-1440.png"), fullPage: true });
    pass("镜头工作区可查看，长文本使用安全换行");

    await openStage(page, "bible");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForFunction(
      (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
      idA,
      { timeout: 10000 }
    );
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspaceTitle")?.textContent || "").includes("Story Bible"),
      null,
      { timeout: 8000 }
    );
    pass("刷新后恢复项目与查看阶段");

    const titleB = `QA乙-${Date.now()}`;
    const idB = await uiCreateProject(page, titleB, shortText("B"));
    created.push(idB);
    await page.click("#taskCenterToggle");
    await page.waitForSelector("#taskCenterBody:not(.hidden)");
    const timelineB = await page.locator("#jobTimeline").innerText();
    if (timelineB.includes("重生成 Story Bible") || timelineB.includes("关键帧")) {
      throw new Error("切换到新项目后仍显示旧项目任务");
    }
    await page.click("#runWorkflowBtn");
    await page.waitForFunction(() => {
      const status = document.querySelector("#jobStatus")?.textContent || "";
      const list = document.querySelector("#jobList")?.innerText || "";
      return list.trim().length > 0 && !/空闲|idle/.test(status);
    }, null, { timeout: 15000 });
    pass("项目切换不污染任务；任务无需刷新即可更新");

    await openStage(page, "assembly");
    const assemblyText = await page.locator("#stageWorkspace").innerText();
    if (!assemblyText.includes("成片") && !assemblyText.includes("合成")) {
      throw new Error("成片工作区未展示合成说明");
    }
    await openStage(page, "export");
    const exportText = await page.locator("#stageWorkspace").innerText();
    if (!exportText.includes("交付检查")) throw new Error("导出工作区缺少交付检查");
    await page.screenshot({ path: path.join(OUT, "v1-qa-export-1440.png"), fullPage: true });
    pass("成片配置与导出交付工作区可查看");

    const garbled = await page.evaluate(() => {
      const navText = document.querySelector("#stageNav")?.innerText || "";
      return /文本理解/.test(navText) && /导出与交付/.test(navText) && !navText.includes("\uFFFD");
    });
    if (!garbled) throw new Error("阶段栏中文未正常显示");
    if ((await horizontalOverflow(page)) > 2) throw new Error("QA 场景结束后仍有横向溢出");
    pass("长中文无乱码、无遮挡、无横向溢出");

    console.log("ALL V1 QA BROWSER TESTS PASSED");
  } finally {
    for (const id of created.filter(Boolean)) {
      try {
        await deleteProject(page, id);
        console.log(`CLEANED: ${id}`);
      } catch (error) {
        console.error(error);
      }
    }
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
