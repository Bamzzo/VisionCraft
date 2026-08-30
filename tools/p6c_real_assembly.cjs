/**
 * P6-C 真实 FFmpeg 浏览器闭环（仅在 PATH 有 ffmpeg 时由 Python 包装启动）。
 * 无 FFmpeg 时不得调用本脚本，也不得写出 p6c-real-*.png。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const READY_ID = process.env.P6C_READY_ID;
const OTHER_ID = process.env.P6C_OTHER_ID;
const SHOT_ID = process.env.P6C_SHOT_ID;
const ALT_VERSION = process.env.P6C_ALT_VERSION;

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

async function selectProject(page, id) {
  await page.waitForSelector(`#projectList .project-item[data-project-id="${id}"]`, { timeout: 10000 });
  await page.locator(`#projectList .project-item[data-project-id="${id}"]`).click();
  const modal = page.locator("#unsavedModal:not(.hidden)");
  if (await modal.count()) await page.click("#unsavedDiscardBtn");
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 8000 }
  );
}

async function openAssembly(page) {
  await page.click('[data-stage-id="assembly"]');
  await page.waitForSelector("#assemblyPanel", { timeout: 8000 });
}

async function main() {
  if (!READY_ID || !OTHER_ID) throw new Error("缺少 P6C 项目 ID");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, READY_ID);
    await openAssembly(page);
    const order = await page.evaluate(() =>
      [...document.querySelectorAll(".assembly-shot-row .assembly-shot-index")].map((node) => node.textContent.trim())
    );
    if (JSON.stringify(order) !== JSON.stringify(["01", "02", "03", "04"])) {
      throw new Error(`镜头顺序不正确：${order.join(",")}`);
    }
    await page.screenshot({ path: path.join(OUT, "p6c-real-assembly-ready-1440.png"), fullPage: true });
    pass("真实镜头按顺序显示，合成按钮可用");

    const bodyHidden = await page.locator("#taskCenterBody.hidden").count();
    if (bodyHidden) await page.click("#taskCenterToggle");
    await page.click("#assembleProjectBtn");
    await page.waitForFunction(
      () => /成片合成/.test(document.querySelector("#jobMessage")?.textContent || ""),
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "p6c-real-assembly-running-1440.png"), fullPage: true });
    pass("任务中心在不刷新页面的情况下显示合成进度");

    await page.waitForFunction(
      () => {
        const video = document.querySelector("#assemblyPanel video");
        const btn = document.querySelector("#assembleProjectBtn")?.textContent || "";
        return video && video.readyState >= 2 && btn.includes("重新合成");
      },
      null,
      { timeout: 45000 }
    );
    await page.screenshot({ path: path.join(OUT, "p6c-real-assembly-complete-1440.png"), fullPage: true });
    if (!(await page.locator('#assemblyPanel a[download]').count())) throw new Error("缺少下载入口");
    pass("真实成片预览可播放，并出现下载/打开入口");

    if (SHOT_ID && ALT_VERSION) {
      const rolled = await page.request.post(`${BASE}/api/projects/${READY_ID}/shots/${SHOT_ID}/versions/${ALT_VERSION}/rollback`);
      if (!rolled.ok()) throw new Error(`替换镜头失败 ${rolled.status()}`);
      await page.click("#refreshBtn");
      await page.waitForFunction(
        () => document.querySelector("#assemblyFreshness")?.textContent.includes("已过期"),
        null,
        { timeout: 10000 }
      );
      await openAssembly(page);
      await page.screenshot({ path: path.join(OUT, "p6c-real-assembly-stale-1440.png"), fullPage: true });
      pass("替换镜头后旧成片显示已过期");
      await page.click("#assembleProjectBtn");
      await page.waitForFunction(
        () => document.querySelector("#assemblyFreshness")?.textContent.includes("当前有效"),
        null,
        { timeout: 45000 }
      );
      pass("重新合成后出现新的当前成片");
    }

    await selectProject(page, OTHER_ID);
    const timeline = await page.locator("#jobTimeline").innerText().catch(() => "");
    if (timeline.includes("成片合成已排队") || timeline.includes("成片已生成")) {
      throw new Error("切换项目后仍显示上一项目的合成事件");
    }
    pass("切换项目后旧合成事件不污染当前页面");
    console.log("ALL P6-C REAL BROWSER CHECKS PASSED");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
