/**
 * 本地 JPEG 首帧登记浏览器验收。禁止真实付费 API。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const OTHER_ID = process.env.V1_OTHER_ID;
const UI_TITLE = "护栏E2E 本地首帧登记";
const SAMPLE = "春秋蝉鸣少年归";
const JPEG_PATH = process.env.LOCAL_JPEG_PATH;

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function clearUnsaved(page) {
  const visible = await page.evaluate(() => {
    const modal = document.querySelector("#unsavedModal");
    return Boolean(modal && !modal.classList.contains("hidden"));
  });
  if (!visible) return;
  await page.locator("#unsavedDiscardBtn").click({ force: true });
  await page.waitForFunction(
    () => document.querySelector("#unsavedModal")?.classList.contains("hidden"),
    null,
    { timeout: 8000 }
  );
}

async function selectProject(page, id) {
  await clearUnsaved(page);
  await page.waitForSelector(`#projectList .project-item[data-project-id="${id}"]`, { timeout: 15000 });
  await page.click(`#projectList .project-item[data-project-id="${id}"]`);
  await clearUnsaved(page);
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 15000 }
  );
}

async function openStage(page, stageId) {
  await clearUnsaved(page);
  await page.click(`[data-stage-id="${stageId}"]`);
  await clearUnsaved(page);
  await page.waitForFunction(
    (id) => document.querySelector(`[data-stage-id="${id}"].active, [data-stage-id="${id}"].current`),
    stageId,
    { timeout: 8000 }
  ).catch(() => {});
}

async function openShotInspector(page, stage) {
  await openStage(page, stage);
  await page.waitForSelector(".asset-card", { timeout: 10000 });
  if (!(await page.locator(".asset-card.selected").count())) {
    await page.click(".asset-card");
  }
  await page.waitForSelector("#videoModeSelect");
}

async function waitAdapt(page, action, timeout = 30000) {
  await page.waitForSelector(`[data-adapt="${action}"]`, { timeout });
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  if (!JPEG_PATH || !fs.existsSync(JPEG_PATH)) {
    throw new Error("缺少 LOCAL_JPEG_PATH");
  }
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(20000);
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    if (await page.locator("#projectForm.hidden").count()) {
      await page.click("#newProjectBtn");
    }
    await page.waitForSelector("#projectForm:not(.hidden)");
    await page.fill("#titleInput", UI_TITLE);
    await page.fill("#sourceTextInput", SAMPLE);
    await page.selectOption("#generationModeInput", "mock");
    await page.click("#submitProjectBtn");
    await page.waitForFunction(
      (title) => (document.querySelector("#summaryFields")?.innerText || "").includes(title.slice(0, 8)),
      UI_TITLE,
      { timeout: 15000 }
    );
    const createdId = await page.evaluate(() => document.querySelector(".project-item.active")?.getAttribute("data-project-id"));
    pass("新建临时项目");

    await page.click("#runWorkflowBtn");
    await page.waitForSelector('[data-stage-id="text"]', { timeout: 15000 });
    await openStage(page, "text");
    await waitAdapt(page, "confirm-scope", 30000);
    await page.click("[data-adapt='confirm-scope']");
    await openStage(page, "bible");
    await waitAdapt(page, "confirm-bible", 30000);
    await page.click("[data-adapt='confirm-bible']");
    await openStage(page, "storyboard");
    await waitAdapt(page, "confirm-storyboard", 30000);
    await page.click("[data-adapt='confirm-storyboard']");
    await page.waitForFunction(
      () => (document.querySelector("#summaryFields")?.innerText || "").includes("production_ready"),
      null,
      { timeout: 20000 }
    );

    await openShotInspector(page, "video");
    await page.selectOption("#videoModeSelect", "i2v");
    await page.waitForFunction(() => {
      const btn = document.querySelector("[data-action='generate-video']");
      return Boolean(btn && btn.disabled && /首帧/.test(`${btn.getAttribute("title") || ""}${btn.textContent || ""}`));
    }, null, { timeout: 8000 });
    pass("未登记首帧时 I2V 按钮不可用");

    await page.setInputFiles("#localFirstFrameInput", JPEG_PATH);
    await page.waitForFunction(
      () => (document.querySelector("[data-first-frame-status]")?.textContent || "").includes("已登记为首帧"),
      null,
      { timeout: 15000 }
    );
    pass("登记后显示已登记为首帧");

    const previewSel = ".asset-detail-preview img, .local-keyframe-thumb";
    await page.waitForSelector(previewSel, { timeout: 8000 });
    const src = await page.locator(previewSel).first().getAttribute("src");
    if (!src || src.startsWith("data:")) throw new Error("预览不得使用 Data URL");
    if (createdId && !src.includes(createdId)) throw new Error("预览路径必须属于当前项目");
    pass("登记本地 JPEG 后显示首帧预览");

    if (await page.locator("[data-action='save-shot-draft']").count()) {
      await page.click("[data-action='save-shot-draft']");
    }
    await openShotInspector(page, "keyframes");
    const visionDisabled = await page.locator("[data-adapt='vision-review']").isDisabled();
    if (visionDisabled) throw new Error("登记后视觉检查按钮应可用");
    pass("登记后视觉检查按钮可用");

    await openShotInspector(page, "video");
    await page.selectOption("#videoModeSelect", "i2v");
    const disabledAfter = await page.locator("[data-action='generate-video']").isDisabled();
    if (disabledAfter) {
      const reason = await page.locator("[data-action='generate-video']").getAttribute("title");
      throw new Error(`登记后 I2V 前置条件应满足，实际：${reason || "按钮仍禁用"}`);
    }
    pass("登记后 I2V 前置条件满足");
    await page.screenshot({ path: path.join(OUT, "p7-local-keyframe-1440.png"), fullPage: true });

    if (OTHER_ID) {
      await selectProject(page, OTHER_ID);
      await openStage(page, "video");
      const leaked = await page.evaluate((id) => {
        const img = document.querySelector(".asset-detail-preview img, .asset-thumb img");
        return Boolean(img && (img.getAttribute("src") || "").includes(id));
      }, createdId);
      if (leaked) throw new Error("项目切换后串用了另一项目图片");
      pass("项目切换不串图");
    }

    await page.setViewportSize({ width: 1100, height: 900 });
    if (createdId) await selectProject(page, createdId);
    await openShotInspector(page, "video");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    if (overflow) throw new Error("1100px 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "p7-local-keyframe-1100.png"), fullPage: true });
    pass("1100px 窄窗口无横向溢出");
    console.log("ALL LOCAL KEYFRAME UI CHECKS PASSED");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
