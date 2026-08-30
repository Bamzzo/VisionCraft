/**
 * UI-0～UI-1 工作台交互原型验收（真实浏览器，mock 后端，无付费 API）。
 * 由 tools/test_ui_workbench.py 通过 npx playwright 调用。
 *
 * 本脚本只验收当前四区工作台（左项目 / 中阶段工作区 / 右阶段导航 / 底任务中心）。
 * output/playwright 下旧文件（workbench-*.png、short-*.png、medium-*.png、
 * browser-short-review.png）是历史产物，不作为本轮主验收证据。
 *
 * 覆盖：
 *  1. 新建项目不改变当前项目；创建项目后自动切换并恢复摘要；
 *  2. 新建草稿未提交时切换项目的未保存守卫（取消/放弃）；
 *  3. 点击右侧阶段只改变 viewStage，不改变 executionStage、不启动任务；
 *  4. 素材缩略图 / 单素材视图切换与素材选择；
 *  5. 重做按钮脏状态门控（无修改禁用、修改后启用）；
 *  6. 上游重做后下游阶段显示失效；
 *  7. 任务进度更新无需刷新；
 *  8. 切换项目后旧任务事件不污染新项目；
 *  9. 长中文标题最多 2 行省略，完整标题可访问，窗口无横向溢出。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const created = [];

function shortText(seed) {
  return (
    `方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊（${seed}）。` +
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
    "最终他停在山门前，留下未说完的话。"
  );
}

function mediumText() {
  const unit = shortText("中等");
  let text = "";
  let i = 0;
  while (text.length < 2200) {
    i += 1;
    text += `第${i}段。${unit}`;
  }
  return text;
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

/* ---------- API 辅助（仅用于布置复杂项目状态） ---------- */
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

/* ---------- UI 辅助 ---------- */
async function uiCreateProject(page, title, text) {
  await page.click("#newProjectBtn");
  await page.waitForSelector("#projectForm:not(.hidden)", { timeout: 5000 });
  await page.fill("#titleInput", title);
  await page.fill("#sourceTextInput", text);
  await page.click("#submitProjectBtn");
  await page.waitForFunction(
    (t) => document.querySelector(".project-item.active strong")?.textContent.includes(t),
    title,
    { timeout: 10000 }
  );
  return page.locator(".project-item.active").getAttribute("data-project-id");
}
async function activeProjectId(page) {
  return page.locator(".project-item.active").getAttribute("data-project-id");
}
async function selectProject(page, id) {
  await page.click(`.project-item[data-project-id="${id}"]`);
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
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
async function stageState(page, stageId) {
  return page.evaluate((sid) => {
    const node = document.querySelector(`[data-stage-id="${sid}"] .stage-state-label`);
    return node ? node.textContent.trim() : null;
  }, stageId);
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

    const layoutOk = await page.evaluate(() => ({
      left: Boolean(document.querySelector(".left-panel #projectSection")),
      center: Boolean(document.querySelector(".center-panel #stageWorkspace")),
      right: Boolean(document.querySelector(".right-panel #stageNav")),
      task: Boolean(document.querySelector("#taskDrawer, #taskCenterBody")),
      oldReview: Boolean(document.querySelector("#adaptationReview")),
      oldConsole: Boolean(document.querySelector("#supervisorConsole")),
    }));
    if (!layoutOk.left || !layoutOk.center || !layoutOk.right || !layoutOk.task) {
      throw new Error(`未检测到四区工作台布局：${JSON.stringify(layoutOk)}`);
    }
    if (layoutOk.oldReview || layoutOk.oldConsole) {
      throw new Error("页面仍包含旧版改编审核 / 监制控制台主区域");
    }
    pass("浏览器加载的是当前四区工作台，而非旧版中间审核 + 右侧监制控制台");

    /* ===== 1 & 2：创建自动切换 + 新建不改变当前项目 + 未保存守卫 ===== */
    const titleA = `工作台甲-${Date.now()}`;
    const idA = await uiCreateProject(page, titleA, shortText("A"));
    created.push(idA);
    if ((await activeProjectId(page)) !== idA) throw new Error("创建项目 A 后未自动切换");
    if ((await summaryValue(page, "项目名称")) !== titleA) throw new Error("创建后未恢复项目摘要");
    await page.screenshot({ path: path.join(OUT, "ui-01-empty-project-1440.png"), fullPage: true });
    pass("创建项目后自动选中新项目并恢复项目摘要");

    const titleB = `工作台乙-${Date.now()}`;
    const idB = await uiCreateProject(page, titleB, shortText("B"));
    created.push(idB);
    if ((await activeProjectId(page)) !== idB) throw new Error("创建项目 B 后未自动切换");

    // 新建项目：只清空表单，不改变当前项目 B。
    await page.click("#newProjectBtn");
    await page.waitForSelector("#projectForm:not(.hidden)");
    if ((await activeProjectId(page)) !== idB) throw new Error("点击新建项目后当前项目被改变");
    if (await page.locator("#projectSummaryPanel").isVisible()) throw new Error("新建态不应显示项目摘要");
    pass("新建项目仅进入空白表单，不改变当前项目");

    // 未保存守卫：填写草稿后切换项目。
    await page.fill("#titleInput", "未提交的新项目草稿");
    await page.click(`.project-item[data-project-id="${idA}"]`);
    await page.waitForSelector("#unsavedModal:not(.hidden)", { timeout: 5000 });
    if (await page.locator("#unsavedSaveBtn").isVisible()) throw new Error("新项目草稿守卫不应提供保存按钮");
    // 取消：保留草稿且不切换。
    await page.click("#unsavedCancelBtn");
    await page.waitForSelector("#unsavedModal.hidden", { state: "attached" }).catch(() => {});
    if ((await activeProjectId(page)) !== idB) throw new Error("取消守卫后不应切换项目");
    if ((await page.locator("#titleInput").inputValue()) !== "未提交的新项目草稿") throw new Error("取消守卫后草稿被清空");
    // 放弃：切换到 A 并恢复摘要。
    await page.click(`.project-item[data-project-id="${idA}"]`);
    await page.waitForSelector("#unsavedModal:not(.hidden)", { timeout: 5000 });
    await page.click("#unsavedDiscardBtn");
    await page.waitForFunction(
      (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
      idA,
      { timeout: 8000 }
    );
    if (await page.locator("#projectForm").isVisible()) throw new Error("放弃修改后应恢复项目摘要态");
    pass("新建草稿未提交时切换项目触发守卫（取消保留 / 放弃切换）");

    /* ===== 故事线审核场景截图（中等文本，真实 API） ===== */
    const titleM = `工作台故事线-${Date.now()}`.slice(0, 120);
    const mediumCreated = await apiPost(page, "/api/projects", {
      title: titleM,
      source_text: mediumText(),
      style: "cinematic clean realism",
      aspect_ratio: "16:9",
      duration_seconds: 5,
    });
    const idM = mediumCreated.id;
    created.push(idM);
    await apiPost(page, `/api/projects/${idM}/run`);
    await waitProject(page, idM, (p) => p.status === "awaiting_storyline_review" || (p.storylines || []).length > 0);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, idM);
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => document.querySelector("#stageWorkspaceTitle")?.textContent.includes("故事线"),
      null,
      { timeout: 5000 }
    );
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(OUT, "ui-02-storyline-review-1440.png"), fullPage: true });
    pass("中等文本故事线审核场景已用新版工作台截图");

    /* ===== 3：点击阶段只改变查看阶段 ===== */
    const projA = await driveToStoryboard(page, idA);
    // 为首个镜头生成关键帧（同步 mock），制造下游产物，用于验证重做后的「已失效」标记。
    await apiPost(page, `/api/projects/${idA}/shots/${projA.shots[0].id}/keyframes/redraw`, { target: "both" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, idA);
    const execBefore = await summaryValue(page, "当前阶段");
    const jobsBefore = await page.evaluate(() => (document.querySelector("#jobMessage")?.textContent || ""));
    await page.click('[data-stage-id="bible"]');
    await page.waitForFunction(() => document.querySelector("#stageWorkspaceTitle")?.textContent.includes("Story Bible"), null, { timeout: 5000 });
    const execAfter = await summaryValue(page, "当前阶段");
    if (execBefore !== execAfter) throw new Error(`点击阶段改变了执行阶段：${execBefore} -> ${execAfter}`);
    const jobsAfter = await page.evaluate(() => (document.querySelector("#jobMessage")?.textContent || ""));
    if (jobsBefore !== jobsAfter) throw new Error("点击阶段触发了新任务");
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.join(OUT, "ui-03-story-bible-1920.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    pass("点击右侧阶段只切换查看内容，不改变执行阶段、不启动任务");

    /* ===== 5 & 6：脏状态门控 + 上游重做下游失效 ===== */
    const redoBtn = '#stageWorkspace [data-redo-btn]';
    await page.waitForSelector(redoBtn, { timeout: 5000 });
    if (await page.locator(redoBtn).isEnabled()) throw new Error("无修改时重做按钮应禁用");
    await page.fill("#bibleLogline", "修改后的 logline：主角在雨夜做出抉择。");
    await page.waitForFunction(() => {
      const btn = document.querySelector("#stageWorkspace [data-redo-btn]");
      const dirty = document.querySelector("#bibleDirty")?.textContent || "";
      return btn && !btn.disabled && dirty.includes("有未保存修改");
    }, null, { timeout: 5000 });
    pass("无修改时重做按钮禁用，修改后启用并显示脏标记");

    await page.click(redoBtn);
    // 语义：分镜 drafts 被清空→未开始；首镜已有关键帧产物→已失效；视频从未生成→未开始。
    await page.waitForFunction(
      () => {
        const text = (sid) => document.querySelector(`[data-stage-id="${sid}"] .stage-state-label`)?.textContent.trim();
        return text("storyboard") === "未开始" && text("keyframes") === "已失效" && text("video") === "未开始";
      },
      null,
      { timeout: 15000 }
    );
    const execAfterRedo = await summaryValue(page, "当前阶段");
    if (execAfterRedo !== "Story Bible") throw new Error(`重做后执行阶段应回退到 Story Bible，实际为 ${execAfterRedo}`);
    await page.screenshot({ path: path.join(OUT, "workbench-after-redo.png"), fullPage: true });
    pass("上游 Story Bible 重做后执行阶段回退，分镜未开始、关键帧已失效、视频未开始");

    /* ===== 4：视图切换 + 素材选择 ===== */
    await page.click('[data-stage-id="video"]');
    await page.waitForSelector("#stageWorkspace .asset-card", { timeout: 8000 });
    await page.click('#stageViewToggle [data-view="single"]');
    await page.waitForFunction(() => document.querySelector("#stageWorkspace .asset-grid")?.classList.contains("single"), null, { timeout: 5000 });
    await page.click('#stageViewToggle [data-view="grid"]');
    await page.waitForFunction(() => !document.querySelector("#stageWorkspace .asset-grid")?.classList.contains("single"), null, { timeout: 5000 });
    pass("素材缩略图与单素材视图可切换");
    await page.click("#stageWorkspace .asset-card");
    await page.waitForSelector("#assetDetail #shotDescriptionInput", { timeout: 8000 });
    pass("点击素材后在工作区内打开单素材编辑区");

    /* ===== 8：切换项目后旧任务事件不污染新项目 ===== */
    await page.click("#taskCenterToggle"); // 展开任务中心（timelineOpen 跨项目保持）
    await page.waitForSelector("#taskCenterBody:not(.hidden)");
    const timelineA = await page.locator("#jobTimeline").innerText();
    if (!timelineA.trim() || timelineA.includes("暂无任务事件")) throw new Error("项目 A 应有任务事件");
    await selectProject(page, idB); // 切换后任务中心保持展开，直接读取 B 的事件
    await page.waitForSelector("#taskCenterBody:not(.hidden)");
    const timelineB = await page.locator("#jobTimeline").innerText();
    if (timelineB.includes("改编流程") || timelineB.includes("重生成") || timelineB.includes("已排队")) {
      throw new Error("切换到项目 B 后仍显示项目 A 的任务事件");
    }
    pass("切换项目后旧任务事件不污染新项目");

    /* ===== 7：任务进度更新无需刷新 ===== */
    await page.click("#runWorkflowBtn");
    await page.waitForFunction(
      () => {
        const list = document.querySelector("#jobList")?.innerText || "";
        const status = document.querySelector("#jobStatus")?.textContent || "";
        return list.trim().length > 0 && !/空闲|idle/.test(status);
      },
      null,
      { timeout: 15000 }
    );
    // 硬断言：刚启动的 B 只产生改编事件，时间线不得出现视频生成/重生成等他项目事件。
    const timelineRun = await page.locator("#jobTimeline").innerText();
    if (timelineRun.includes("镜头生成参数") || timelineRun.includes("提交至") || timelineRun.includes("重生成 Story Bible")) {
      throw new Error(`任务中心时间线出现非本项目事件，存在串项目污染：${timelineRun.slice(0, 120)}`);
    }
    await page.screenshot({ path: path.join(OUT, "ui-04-task-center-1440.png"), fullPage: true });
    await page.screenshot({ path: path.join(OUT, "workbench-task-center.png"), fullPage: true });
    pass("任务中心在不刷新页面的情况下显示任务与进度，且时间线无串项目污染");

    /* ===== 9：长中文标题 2 行省略 + 无横向溢出 ===== */
    const longTitle = (`长标题-${"影视创作工作台长中文标题溢出验证".repeat(4)}-${Date.now()}`).slice(0, 120);
    const idD = await uiCreateProject(page, longTitle, shortText("D"));
    created.push(idD);
    for (const width of [1440, 1920, 1100]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(300);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overflow > 2) throw new Error(`宽度 ${width} 下出现横向溢出 ${overflow}px`);
    }
    await page.setViewportSize({ width: 1100, height: 900 });
    const titleInfo = await page.evaluate(() => {
      const item = document.querySelector(".project-item.active");
      const node = item?.querySelector(".project-item-title");
      const meta = item?.querySelector(".project-item-meta");
      const idNode = item?.querySelector(".project-item-id");
      if (!item || !node) return null;
      const style = getComputedStyle(node);
      return {
        cardHeight: item.getBoundingClientRect().height,
        lineClamp: style.webkitLineClamp,
        titleAttr: item.getAttribute("title") || "",
        fullText: node.textContent || "",
        clamped: node.scrollHeight > node.clientHeight + 1,
        metaVisible: Boolean(meta && meta.getBoundingClientRect().height > 8),
        metaText: meta?.innerText || "",
        idVisible: Boolean(idNode && idNode.getBoundingClientRect().height > 6),
        idText: idNode?.textContent || "",
      };
    });
    if (!titleInfo) throw new Error("未找到项目列表活动项标题");
    if (titleInfo.lineClamp !== "2") throw new Error(`长标题未限制为 2 行，实际 line-clamp=${titleInfo.lineClamp}`);
    if (titleInfo.cardHeight > 150) throw new Error(`长标题项目卡过高：${titleInfo.cardHeight}px`);
    if (titleInfo.titleAttr !== longTitle) throw new Error("项目卡 title 未保留完整标题");
    if (titleInfo.fullText !== longTitle) throw new Error("项目真实名称被改写，仅允许视觉截断");
    if (!titleInfo.clamped) throw new Error("超长标题未被截断，仍占据完整高度");
    if (!titleInfo.metaVisible || !/文本理解|Story Bible|分镜|改编|故事线/.test(titleInfo.metaText)) {
      throw new Error("项目卡阶段或更新时间被长标题挤出");
    }
    if (!titleInfo.idVisible || titleInfo.idText !== idD) throw new Error("项目卡未清晰显示项目 ID");
    await page.screenshot({ path: path.join(OUT, "ui-05-long-title-1100.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(OUT, "workbench-long-text.png"), fullPage: true });
    pass("1440×900 / 1920×1080 / 1100 下无横向溢出，长标题最多 2 行且完整标题可访问");

    console.log("ALL UI WORKBENCH TESTS PASSED");
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
