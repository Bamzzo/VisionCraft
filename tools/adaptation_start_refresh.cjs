/**
 * 浏览器验收：启动改编后不整页刷新也应出现候选方案（适配 UI-0～UI-1 工作台布局）。
 * 由 tools/test_adaptation_start_refresh.py 通过 npx playwright 调用。
 *
 * 新布局要点：
 * - 查看已有项目时新建表单默认隐藏，需先点击「新建项目」展开空白表单；
 * - 改编方案 / 故事线内容渲染在中间「阶段工作区」#stageWorkspace，
 *   需先点击右侧阶段导航 [data-stage-id] 切换 viewStage 才会显示；
 * - 文本规模说明（短文本/中等文本）显示在左侧项目摘要 #summaryFields。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");

function shortText() {
  return (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。" +
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
    "最终他停在山门前，留下未说完的话。"
  );
}

function mediumText() {
  const unit = shortText();
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

async function createProject(page, title, text) {
  // 查看已有项目时表单处于摘要态，先点击「新建项目」进入空白表单。
  await page.click("#newProjectBtn");
  await page.waitForSelector("#projectForm:not(.hidden)", { timeout: 5000 });
  await page.fill("#titleInput", title);
  await page.fill("#sourceTextInput", text);
  await page.click("#submitProjectBtn");
  await page.waitForFunction(
    (expected) => {
      const active = document.querySelector(".project-item.active strong");
      return active && active.textContent.includes(expected);
    },
    title,
    { timeout: 10000 }
  );
}

async function currentProjectId(page) {
  return page.locator(".project-item.active").getAttribute("data-project-id");
}

async function deleteProject(page, projectId) {
  if (!projectId) return;
  const response = await page.request.delete(`${BASE}/api/projects/${projectId}`);
  if (!response.ok()) {
    throw new Error(`删除临时项目失败：${response.status()} ${await response.text()}`);
  }
}

async function runWithoutReload(page, title, text) {
  await createProject(page, title, text);
  const projectId = await currentProjectId(page);
  await page.click("#runWorkflowBtn");
  // mock 流程推进极快，瞬时“已入队”消息可能被后续事件覆盖；
  // 以“项目状态离开 created 或任务中心出现活动”作为已启动的可靠信号。
  await page.waitForFunction(
    () => {
      const summary = document.querySelector("#summaryFields")?.innerText || "";
      const msg = document.querySelector("#jobMessage")?.textContent || "";
      const status = document.querySelector("#jobStatus")?.textContent || "";
      const left = summary.length > 0 && !/项目状态\ncreated/.test(summary);
      const busy = status.length > 0 && !/空闲|idle/.test(status);
      return left || busy || msg.includes("已入队") || msg.includes("改编") || msg.includes("排队") || msg.includes("就绪");
    },
    null,
    { timeout: 15000 }
  );
  return projectId;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage();
  const created = [];
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");

    // ---- 短文本：直接改编，出现候选方案 ----
    const shortTitle = `ui-short-${Date.now()}`;
    const shortId = await runWithoutReload(page, shortTitle, shortText());
    created.push(shortId);
    // 切换到「改编方案」查看阶段，等待候选方案在不刷新页面的情况下出现。
    await page.click('[data-stage-id="adaptation"]');
    await page.waitForFunction(
      () => {
        const ws = document.querySelector("#stageWorkspace")?.innerText || "";
        return ws.includes("选择此方案") && ws.includes("确认范围并生成 Bible");
      },
      null,
      { timeout: 15000 }
    );
    const summary = await page.locator("#summaryFields").innerText();
    if (!summary.includes("直接改编")) throw new Error("短文本未显示规模说明");
    await page.screenshot({ path: path.join(OUT, "short-after-run.png"), fullPage: true });
    console.log("PASS: 短文本启动后未刷新即出现候选方案");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");
    await page.click('[data-stage-id="adaptation"]');
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("选择此方案"),
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "short-after-reload.png"), fullPage: true });
    console.log("PASS: 刷新后短文本候选方案与审核状态仍在");

    // ---- 中等文本：先选择故事线 ----
    const mediumTitle = `ui-medium-${Date.now()}`;
    const mediumId = await runWithoutReload(page, mediumTitle, mediumText());
    created.push(mediumId);
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => {
        const ws = document.querySelector("#stageWorkspace")?.innerText || "";
        return ws.includes("选择此故事线");
      },
      null,
      { timeout: 15000 }
    );
    const mediumSummary = await page.locator("#summaryFields").innerText();
    if (!mediumSummary.includes("先选择故事线")) throw new Error("中等文本未显示规模说明");
    await page.screenshot({ path: path.join(OUT, "medium-after-run.png"), fullPage: true });
    console.log("PASS: 中等文本启动后未刷新即出现故事线选择");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("选择此故事线"),
      null,
      { timeout: 10000 }
    );
    console.log("PASS: 刷新后中等文本故事线仍在");
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
