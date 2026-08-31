/**
 * Mock 浏览器回归：视频提交后不依赖 #videoModeSelect，显示 waiting/running，
 * 本地刷新恢复任务状态，完成后才进入合成/下载。不发真实 Provider 请求。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8050";
const OUT = path.join(__dirname, "..", "output", "playwright", "mock-video-refresh");
const IDS = JSON.parse(process.env.MVREF_IDS || "{}");

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
  await page.waitForSelector(`#projectList .project-item[data-project-id="${id}"]`, { timeout: 15000 });
  await page.evaluate((pid) => {
    const node = document.querySelector(`#projectList .project-item[data-project-id="${pid}"]`);
    if (!node) throw new Error("项目不在列表中");
    node.scrollIntoView({ block: "nearest" });
    node.click();
  }, id);
  const modal = page.locator("#unsavedModal:not(.hidden)");
  try {
    await modal.waitFor({ state: "visible", timeout: 800 });
    await page.click("#unsavedDiscardBtn");
  } catch {
    /* no modal */
  }
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 15000 }
  );
}

async function openStage(page, stageId) {
  await page.click(`[data-stage-id="${stageId}"]`);
  const modal = page.locator("#unsavedModal:not(.hidden)");
  try {
    await modal.waitFor({ state: "visible", timeout: 500 });
    await page.click("#unsavedDiscardBtn");
  } catch {
    /* no modal */
  }
  await page.waitForTimeout(300);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(20000);
  let generatePosts = 0;
  page.on("request", (req) => {
    if (req.method() === "POST" && /\/shots\/[^/]+\/video$/.test(req.url())) generatePosts += 1;
  });
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, IDS.waiting);
    await openStage(page, "video");
    await page.waitForSelector(".asset-card", { timeout: 15000 });
    await page.locator(".asset-card").first().click();
    await page.waitForFunction(
      () => {
        const detail = document.querySelector("#assetDetail")?.innerText || "";
        const workspace = document.querySelector("#stageWorkspace")?.innerText || "";
        const blob = `${detail}\n${workspace}`;
        return /云端仍在生成|running|pending_remote|等待云端/.test(blob);
      },
      null,
      { timeout: 15000 }
    );
    const usedModeSelect = await page.locator("#videoModeSelect").isVisible().catch(() => false);
    if (usedModeSelect) {
      /* 下拉可以存在，但本回归不得依赖它才能看到 waiting 状态 */
    }
    const waitingText = await page.locator("#assetDetail").innerText();
    if (!/云端仍在生成|running|pending_remote/.test(waitingText)) {
      throw new Error("等待态镜头详情未显示 running/waiting");
    }
    pass("提交后无需 #videoModeSelect 即可看到 waiting/running");
    await page.screenshot({ path: path.join(OUT, "01-waiting-no-modeselect-1440.png"), fullPage: true });

    await page.click("#refreshBtn");
    await page.waitForTimeout(800);
    const modal = page.locator("#unsavedModal:not(.hidden)");
    try {
      await modal.waitFor({ state: "visible", timeout: 800 });
      await page.click("#unsavedDiscardBtn");
    } catch {
      /* no modal */
    }
    await openStage(page, "video");
    await page.waitForSelector(".asset-card", { timeout: 15000 });
    await page.locator(".asset-card").first().click();
    await page.waitForFunction(
      () => {
        const blob = `${document.querySelector("#assetDetail")?.innerText || ""}\n${document.querySelector("#stageWorkspace")?.innerText || ""}`;
        return /云端仍在生成|running|pending_remote|等待云端|回查同一任务/.test(blob);
      },
      null,
      { timeout: 15000 }
    );
    pass("本地刷新后仍恢复同一等待任务状态");
    await page.screenshot({ path: path.join(OUT, "02-refresh-restored-1440.png"), fullPage: true });

    await openStage(page, "assembly");
    await page.waitForSelector("#assembleProjectBtn", { timeout: 15000 });
    if (!(await page.locator("#assembleProjectBtn").isDisabled())) {
      throw new Error("镜头未完成时成片按钮应禁用");
    }
    pass("未完成视频前不能合成/下载成片");

    await selectProject(page, IDS.ready);
    await openStage(page, "video");
    await page.waitForFunction(
      () => (document.querySelector('[data-stage-id="video"] .stage-state-label')?.textContent || "").includes("已完成")
        || (document.querySelector("#stageWorkspace")?.innerText || "").includes("可预览"),
      null,
      { timeout: 15000 }
    );
    await openStage(page, "assembly");
    await page.waitForSelector("#assembleProjectBtn", { timeout: 15000 });
    const reason = await page.locator("#assembleDisabledReason").innerText().catch(() => "");
    const assembleDisabled = await page.locator("#assembleProjectBtn").isDisabled();
    const download = page.locator('#assetDetail a[download], a[href*="final"]').first();
    const hasFinal = (await page.locator("video").count()) > 0 || (await download.count()) > 0;
    if (assembleDisabled && !hasFinal) {
      throw new Error(`完成后仍不能合成或下载：${reason}`);
    }
    pass("任务完成后才进入合成/下载");
    await page.screenshot({ path: path.join(OUT, "03-ready-assembly-1440.png"), fullPage: true });

    if (generatePosts !== 0) {
      throw new Error(`禁止补发：检测到 ${generatePosts} 次 generate-video POST`);
    }
    pass("全程未调用 generate-video");
    fs.writeFileSync(
      path.join(OUT, "result.json"),
      JSON.stringify({ ok: true, generate_video_posts: generatePosts, waiting: IDS.waiting, ready: IDS.ready }, null, 2),
      "utf8"
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
