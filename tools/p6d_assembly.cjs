/**
 * P6-D 本地音频/字幕成片配置浏览器闭环。
 * 无 FFmpeg 时不得调用本脚本，也不得写出 p6d-*.png。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const READY_ID = process.env.P6D_READY_ID;
const OTHER_ID = process.env.P6D_OTHER_ID;

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
  const item = page.locator(`#projectList .project-item[data-project-id="${id}"]`);
  await item.waitFor({ timeout: 15000 });
  await item.scrollIntoViewIfNeeded();
  await item.click();
  const modal = page.locator("#unsavedModal:not(.hidden)");
  try {
    await modal.waitFor({ state: "visible", timeout: 800 });
    await page.click("#unsavedDiscardBtn");
  } catch {
    // 无未保存守卫时继续。
  }
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 15000 }
  );
}

async function openAssembly(page) {
  await page.click('[data-stage-id="assembly"]');
  await page.waitForSelector("#assemblyPanel", { timeout: 8000 });
}

async function waitAssemblyComplete(page) {
  await page.waitForFunction(
    () => {
      const video = document.querySelector("#assemblyPanel video");
      const btn = document.querySelector("#assembleProjectBtn")?.textContent || "";
      return video && video.readyState >= 2 && btn.includes("重新合成");
    },
    null,
    { timeout: 45000 }
  );
}

async function main() {
  if (!READY_ID || !OTHER_ID) throw new Error("缺少 P6D 项目 ID");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, READY_ID);
    await openAssembly(page);
    if (!(await page.locator("#assemblySettingsForm").count())) {
      throw new Error("成片工作区缺少字幕/音频配置区");
    }
    if (!(await page.locator("#assemblySubtitleEnabled").count()) || !(await page.locator("#assemblyAudioEnabled").count())) {
      throw new Error("缺少字幕或背景音频开关");
    }
    await page.screenshot({ path: path.join(OUT, "p6d-assembly-settings-1440.png"), fullPage: true });
    pass("页面显示字幕/音频配置");

    const bodyHidden = await page.locator("#taskCenterBody.hidden").count();
    if (bodyHidden) await page.click("#taskCenterToggle");
    await page.click("#assembleProjectBtn");
    await page.waitForFunction(
      () => /成片合成|排队|处理/.test(document.querySelector("#jobMessage")?.textContent || "") ||
        /排队|处理|完成/.test(document.querySelector("#jobTimeline")?.innerText || ""),
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "p6d-assembly-running-1440.png"), fullPage: true });
    pass("任务中心显示合成排队或处理中");

    await waitAssemblyComplete(page);
    await page.screenshot({ path: path.join(OUT, "p6d-assembly-complete-1440.png"), fullPage: true });
    if (!(await page.locator('#assemblyPanel a[download]').count())) throw new Error("缺少下载入口");
    pass("成片可以预览和下载");

    await page.check("#assemblyAudioEnabled");
    const audioSelect = page.locator("#assemblyAudioPath");
    const optionCount = await audioSelect.locator("option").count();
    if (optionCount < 2) throw new Error("没有可选的项目音频资产");
    await audioSelect.selectOption({ index: 1 });
    await page.click("#saveAssemblySettingsBtn");
    await page.waitForFunction(
      () => (document.querySelector("#assemblyFreshness")?.textContent || "").includes("已过期"),
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "p6d-assembly-stale-1440.png"), fullPage: true });
    pass("保存配置后无需手动刷新即可显示待重新合成");

    await page.click("#assembleProjectBtn");
    await page.waitForFunction(
      () => {
        const fresh = document.querySelector("#assemblyFreshness")?.textContent || "";
        return fresh.includes("当前有效") && document.querySelectorAll(".assembly-history-item").length >= 1;
      },
      null,
      { timeout: 45000 }
    );
    pass("重新合成后当前成片有效，旧成片仍在历史中并曾显示过期");

    await page.check("#assemblySubtitleEnabled");
    await page.fill("#assemblySubtitleText", "");
    await page.click("#saveAssemblySettingsBtn");
    await page.waitForFunction(
      () => /保存成片配置失败|请填写字幕|字幕/.test(document.querySelector("#feedbackResult")?.innerText || ""),
      null,
      { timeout: 8000 }
    );
    const failText = await page.locator("#feedbackResult").innerText();
    if (/ffmpeg|ffprobe| -i |api_key/i.test(failText)) {
      throw new Error("失败提示包含敏感命令或密钥");
    }
    pass("失败状态有中文提示且不含 FFmpeg 命令");

    await page.setViewportSize({ width: 1100, height: 900 });
    await openAssembly(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2
    );
    if (overflow) throw new Error("1100px 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "p6d-assembly-narrow-1100.png"), fullPage: true });
    pass("1100px 窄窗口无横向溢出");

    await selectProject(page, OTHER_ID);
    const timeline = await page.locator("#jobTimeline").innerText().catch(() => "");
    if (timeline.includes("成片合成已排队") || timeline.includes("成片已生成")) {
      throw new Error("切换项目后仍显示上一项目的合成事件");
    }
    pass("切换项目后旧合成事件不污染当前页面");
    console.log("ALL P6-D BROWSER CHECKS PASSED");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
