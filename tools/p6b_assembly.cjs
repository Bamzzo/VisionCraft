/**
 * P6-B 成片工作台浏览器验收（1440×900）。
 * 由 tools/test_p6b_assembly.py 注入项目 ID。不调用付费 API。
 * 旧 output/playwright 截图保留为历史产物，本轮只新增 p6b-*.png。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const READY_ID = process.env.P6B_READY_ID;
const OTHER_ID = process.env.P6B_OTHER_ID;
const COMPLETE_ID = process.env.P6B_COMPLETE_ID;
const STALE_ID = process.env.P6B_STALE_ID;
const FFMPEG = process.env.P6B_FFMPEG === "1";

function pass(msg) {
  console.log(`PASS: ${msg}`);
}
function skip(msg) {
  console.log(`SKIP: ${msg}`);
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
  await page.waitForFunction(() => document.querySelector("#stageWorkspaceTitle")?.textContent.includes("成片"), null, {
    timeout: 5000,
  });
  await page.waitForSelector("#assemblyPanel", { timeout: 5000 });
}

async function main() {
  if (!READY_ID || !OTHER_ID || !COMPLETE_ID || !STALE_ID) {
    throw new Error("缺少 P6B_* 项目 ID 环境变量");
  }
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
    if (JSON.stringify(order) !== JSON.stringify(["01", "02", "03"])) {
      throw new Error(`镜头顺序不正确：${order.join(",")}`);
    }
    const readyCount = await page.locator(".assembly-shot-row.ready").count();
    if (readyCount !== 3) throw new Error(`就绪镜头应为 3，实际 ${readyCount}`);
    const btn = page.locator("#assembleProjectBtn");
    if (!(await btn.isEnabled())) throw new Error("条件满足时合成按钮应可用");
    if ((await btn.innerText()).trim() !== "合成成片") throw new Error("就绪态按钮文案应为「合成成片」");
    await page.screenshot({ path: path.join(OUT, "p6b-assembly-ready-1440.png"), fullPage: true });
    pass("成片阶段按镜头顺序展示，合成按钮可用");

    const bodyHidden = await page.locator("#taskCenterBody.hidden").count();
    if (bodyHidden) await page.click("#taskCenterToggle");
    await page.waitForSelector("#taskCenterBody:not(.hidden)", { timeout: 3000 }).catch(() => {});
    await btn.click();
    await page.waitForFunction(
      () => {
        const msg = document.querySelector("#jobMessage")?.textContent || "";
        const list = document.querySelector("#jobList")?.innerText || "";
        return msg.includes("成片合成") || list.includes("成片合成");
      },
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "p6b-assembly-running-1440.png"), fullPage: true });
    pass("提交后任务中心显示成片合成已排队/进行中，无需刷新");

    if (FFMPEG) {
      await page.waitForFunction(
        () => {
          const btn = document.querySelector("#assembleProjectBtn");
          const pill = document.querySelector("#assemblyFreshness")?.textContent || "";
          return btn && btn.textContent.includes("重新合成") && pill.includes("当前有效");
        },
        null,
        { timeout: 20000 }
      );
      if (!(await page.locator("#assemblyPanel video").count())) throw new Error("合成完成后未出现预览");
      if (!(await page.locator('#assemblyPanel a[download]').count())) throw new Error("合成完成后未出现下载入口");
      await page.screenshot({ path: path.join(OUT, "p6b-assembly-complete-1440.png"), fullPage: true });
      pass("成片完成后自动出现预览和下载入口");
    } else {
      await selectProject(page, COMPLETE_ID);
      await openAssembly(page);
      if (!(await page.locator("#assemblyPanel video").count())) throw new Error("完整成片夹具未出现预览");
      await page.screenshot({ path: path.join(OUT, "p6b-assembly-complete-1440.png"), fullPage: true });
      skip("本机没有 FFmpeg，完成态截图来自已登记成片夹具，不报告为真实 concat 通过");
    }

    await selectProject(page, STALE_ID);
    await openAssembly(page);
    const stale = await page.locator("#assemblyFreshness").innerText();
    if (!stale.includes("已过期")) throw new Error("替换镜头后成片应显示已过期");
    const staleBtn = await page.locator("#assembleProjectBtn").innerText();
    if (!staleBtn.includes("重新合成") && !staleBtn.includes("合成成片")) {
      throw new Error("过期成片应允许重新合成");
    }
    await page.screenshot({ path: path.join(OUT, "p6b-assembly-stale-1440.png"), fullPage: true });
    pass("替换镜头后旧成片显示已过期");

    if (FFMPEG) {
      await page.click("#assembleProjectBtn");
      await page.waitForFunction(
        () => document.querySelector("#assemblyFreshness")?.textContent.includes("当前有效"),
        null,
        { timeout: 20000 }
      );
      pass("重新合成后出现新成片");
    } else {
      skip("本机没有 FFmpeg，跳过真实重新合成");
    }

    const timelineBefore = await page.locator("#jobTimeline").innerText().catch(() => "");
    await selectProject(page, OTHER_ID);
    await page.waitForTimeout(400);
    const timelineAfter = await page.locator("#jobTimeline").innerText().catch(() => "");
    if (timelineAfter.includes("成片合成") && timelineBefore.includes("成片合成") && timelineAfter === timelineBefore) {
      throw new Error("切换项目后仍显示上一项目的成片任务事件");
    }
    pass("切换项目后旧合成任务事件不污染当前项目");
    console.log("ALL P6-B ASSEMBLY BROWSER CHECKS DONE");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
