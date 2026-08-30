/**
 * P6-E 原声配置恢复与混音浏览器闭环。
 * 无 FFmpeg 时不得调用本脚本，也不得写出 p6e-*.png。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const READY_ID = process.env.P6E_READY_ID;
const OTHER_ID = process.env.P6E_OTHER_ID;

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

async function waitFresh(page, label) {
  await page.waitForFunction(
    (text) => (document.querySelector("#assemblyFreshness")?.textContent || "").includes(text),
    label,
    { timeout: 45000 }
  );
}

async function main() {
  if (!READY_ID || !OTHER_ID) throw new Error("缺少 P6E 项目 ID");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, READY_ID);
    await openAssembly(page);
    if (!(await page.locator("#assemblyKeepSourceAudio").count())) {
      throw new Error("缺少保留原视频音频开关");
    }
    await page.check("#assemblyKeepSourceAudio");
    await page.check("#assemblyAudioEnabled");
    const audioSelect = page.locator("#assemblyAudioPath");
    if ((await audioSelect.locator("option").count()) < 2) throw new Error("没有可选背景音频");
    await audioSelect.selectOption({ index: 1 });
    await page.waitForFunction(
      () => (document.querySelector("#assemblySettingsDirty")?.textContent || "").includes("未保存"),
      null,
      { timeout: 5000 }
    );
    pass("修改配置但未保存时显示未保存状态");
    await page.click("#saveAssemblySettingsBtn");
    await page.waitForFunction(
      () => (document.querySelector("#jobMessage")?.textContent || "").includes("成片配置已保存"),
      null,
      { timeout: 8000 }
    );

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, READY_ID);
    await openAssembly(page);
    if (!(await page.locator("#assemblyKeepSourceAudio").isChecked())) {
      throw new Error("刷新后未恢复原声开关");
    }
    if (!(await page.locator("#assemblyAudioEnabled").isChecked())) {
      throw new Error("刷新后未恢复背景音频开关");
    }
    const summary = await page.locator("#assemblyAudioSummary").innerText();
    if (!summary.includes("原声开") || !summary.includes("背景音开")) {
      throw new Error(`音频配置摘要不正确：${summary}`);
    }
    await page.screenshot({ path: path.join(OUT, "p6e-assembly-restored-1440.png"), fullPage: true });
    pass("页面能读取并显示已保存的原声和背景音配置");

    const bodyHidden = await page.locator("#taskCenterBody.hidden").count();
    if (bodyHidden) await page.click("#taskCenterToggle");
    await page.click("#assembleProjectBtn");
    await page.waitForFunction(
      () => /成片合成|排队|处理/.test(document.querySelector("#jobMessage")?.textContent || "") ||
        /排队|处理|完成/.test(document.querySelector("#jobTimeline")?.innerText || ""),
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "p6e-assembly-running-1440.png"), fullPage: true });
    pass("任务中心无需手动刷新即可看到合成进度");

    await page.waitForFunction(
      () => {
        const video = document.querySelector("#assemblyPanel video");
        const btn = document.querySelector("#assembleProjectBtn")?.textContent || "";
        return video && video.readyState >= 2 && btn.includes("重新合成");
      },
      null,
      { timeout: 60000 }
    );
    if (!(await page.locator('#assemblyPanel a[download]').count())) throw new Error("缺少下载入口");
    await page.screenshot({ path: path.join(OUT, "p6e-assembly-complete-1440.png"), fullPage: true });
    pass("成片可以预览和下载");

    await page.fill("#assemblySubtitleText", "未保存不应立刻过期");
    await page.waitForFunction(
      () => (document.querySelector("#assemblySettingsDirty")?.textContent || "").includes("未保存"),
      null,
      { timeout: 5000 }
    );
    await page.click("#saveAssemblySettingsBtn");
    await waitFresh(page, "已过期");
    await page.screenshot({ path: path.join(OUT, "p6e-assembly-stale-1440.png"), fullPage: true });
    pass("修改配置并保存后旧成片显示过期");

    await page.click("#assembleProjectBtn");
    await waitFresh(page, "当前有效");
    if (!(await page.locator(".assembly-history-item").count())) {
      throw new Error("重新合成后历史成片未保留");
    }
    pass("重新合成后显示新成片，历史仍在");

    await page.setViewportSize({ width: 1100, height: 900 });
    await openAssembly(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2
    );
    if (overflow) throw new Error("1100px 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "p6e-assembly-narrow-1100.png"), fullPage: true });
    pass("1100px 窄窗口无横向溢出");

    await selectProject(page, OTHER_ID);
    await openAssembly(page);
    if (await page.locator("#assemblyKeepSourceAudio").isChecked()) {
      throw new Error("切换项目后串用了上一项目的原声配置");
    }
    const timeline = await page.locator("#jobTimeline").innerText().catch(() => "");
    if (timeline.includes("成片合成已排队") || timeline.includes("成片已生成")) {
      throw new Error("切换项目后仍显示上一项目的合成事件");
    }
    pass("切换项目后配置和任务不会串项目");
    console.log("ALL P6-E BROWSER CHECKS PASSED");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
