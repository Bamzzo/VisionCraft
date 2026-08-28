/**
 * 浏览器验收：启动改编后不整页刷新也应出现候选方案。
 * 由 tools/test_adaptation_start_refresh.py 通过 npx playwright 调用。
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
  await page.fill("#titleInput", title);
  await page.fill("#sourceTextInput", text);
  await page.click('button.primary-btn[type="submit"]');
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
  await page.waitForFunction(
    () => {
      const msg = document.querySelector("#jobMessage")?.textContent || "";
      return msg.includes("已入队") || msg.includes("改编");
    },
    null,
    { timeout: 8000 }
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

    const shortTitle = `ui-short-${Date.now()}`;
    const shortId = await runWithoutReload(page, shortTitle, shortText());
    created.push(shortId);
    await page.waitForFunction(
      () => {
        const status = document.querySelector("#projectStatus")?.textContent?.trim();
        const review = document.querySelector("#adaptationReview")?.innerText || "";
        return status && status !== "created" && status !== "未创建" && review.includes("选择此方案");
      },
      null,
      { timeout: 10000 }
    );
    const shortBody = await page.locator("#adaptationReview").innerText();
    if (!shortBody.includes("短文本：直接改编")) throw new Error("短文本未显示规模说明");
    if (!shortBody.includes("确认范围并生成 Bible")) throw new Error("短文本未显示确认范围");
    await page.screenshot({ path: path.join(OUT, "short-after-run.png"), fullPage: true });
    console.log("PASS: 短文本启动后未刷新即出现候选方案");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => {
        const status = document.querySelector("#projectStatus")?.textContent?.trim();
        const review = document.querySelector("#adaptationReview")?.innerText || "";
        return status && status !== "created" && review.includes("选择此方案");
      },
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "short-after-reload.png"), fullPage: true });
    console.log("PASS: 刷新后短文本候选方案与审核状态仍在");

    const mediumTitle = `ui-medium-${Date.now()}`;
    const mediumId = await runWithoutReload(page, mediumTitle, mediumText());
    created.push(mediumId);
    await page.waitForFunction(
      () => {
        const status = document.querySelector("#projectStatus")?.textContent?.trim();
        const review = document.querySelector("#adaptationReview")?.innerText || "";
        return (
          status === "awaiting_storyline_review" &&
          review.includes("选择故事线") &&
          (review.includes("中等文本：先选择故事线") || review.includes("选择故事线"))
        );
      },
      null,
      { timeout: 10000 }
    );
    const mediumBody = await page.locator("#adaptationReview").innerText();
    if (!mediumBody.includes("中等文本：先选择故事线，再进行改编")) {
      throw new Error("中等文本未显示规模说明");
    }
    await page.screenshot({ path: path.join(OUT, "medium-after-run.png"), fullPage: true });
    console.log("PASS: 中等文本启动后未刷新即出现故事线选择");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => {
        const status = document.querySelector("#projectStatus")?.textContent?.trim();
        const review = document.querySelector("#adaptationReview")?.innerText || "";
        return status === "awaiting_storyline_review" && review.includes("选择故事线");
      },
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
