/**
 * 无人版演示录制：用 Playwright 自带的录屏能力走一遍真实界面，产出可交付的视频。
 *
 * 与 v1_demo.cjs 的分工不同：那个是**验收**（断言、截图、失败即红），这个是**演示**
 * （有停顿、有解说式标题、不为断言而等待）。所以刻意分开写——
 * 把断言塞进演示脚本会让两者都变脆：演示的停顿会拖慢验收，验收的等待会让演示卡顿。
 *
 * 素材全部来自磁盘上已存在的真实产物（prepare_v1_demo.py 用 ffmpeg 造的本地夹具、
 * 以及长文本项目的真实章节树），**不调用任何付费接口**，可反复录制。
 *
 * 环境变量：
 *   VISIONCRAFT_BASE_URL  后端地址
 *   DEMO_PROJECT_ID       全流程演示项目（应有成片）
 *   DEMO_LONG_PROJECT_ID  可选：长文本演示项目（章节树 + 范围勾选 + 全文检索）
 *   DEMO_VIDEO_DIR        录屏输出目录
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const PROJECT_ID = process.env.DEMO_PROJECT_ID;
const LONG_ID = process.env.DEMO_LONG_PROJECT_ID || "";
const VIDEO_DIR = process.env.DEMO_VIDEO_DIR || path.join(__dirname, "..", "output", "demo", "raw");
const VIEWPORT = { width: 1440, height: 900 };
// 每一步的停顿：太短看不清，太长会拖成一部纪录片。经验值 1.4s。
const BEAT = 1400;

function narrate(msg) {
  console.log(`SCENE: ${msg}`);
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
  await page.waitForSelector(`#projectList .project-item[data-project-id="${id}"]`, { timeout: 20000 });
  await page.click(`#projectList .project-item[data-project-id="${id}"]`);
  await clearUnsaved(page);
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 20000 }
  );
  await page.waitForTimeout(BEAT);
}

async function openStage(page, stageId, beat = BEAT) {
  await clearUnsaved(page);
  const nav = page.locator(`[data-stage-id="${stageId}"]`);
  if (!(await nav.count())) return false;
  await nav.first().click();
  await clearUnsaved(page);
  await page.waitForFunction(
    (id) => document.querySelector(`[data-stage-id="${id}"].active, [data-stage-id="${id}"].current`),
    stageId,
    { timeout: 10000 }
  ).catch(() => {});
  await page.waitForTimeout(beat);
  return true;
}

/** 缓慢滚动，让长内容在视频里"过一遍"而不是瞬移。 */
async function glide(page, distance = 420, steps = 12) {
  for (let index = 0; index < steps; index += 1) {
    await page.mouse.wheel(0, distance / steps);
    await page.waitForTimeout(60);
  }
}

async function scrollTop(page) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(300);
}

async function main() {
  if (!PROJECT_ID) throw new Error("缺少 DEMO_PROJECT_ID");
  fs.mkdirSync(VIDEO_DIR, { recursive: true });
  const browser = await launchBrowser();
  const context = await browser.newContext({
    viewport: VIEWPORT,
    recordVideo: { dir: VIDEO_DIR, size: VIEWPORT },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(25000);
  let videoPath = "";
  try {
    narrate("打开工作台");
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForFunction(() => (document.querySelector("#projectList")?.querySelectorAll(".project-item")?.length || 0) > 0, null, {
      timeout: 20000,
    });
    await page.waitForTimeout(BEAT);

    narrate("选中演示项目，看项目摘要");
    await selectProject(page, PROJECT_ID);
    await page.waitForTimeout(BEAT);

    narrate("八道阶段的导航：从改编一路走到成片");
    await scrollTop(page);
    for (const stage of ["text", "storyline", "bible", "storyboard", "keyframes", "video"]) {
      const opened = await openStage(page, stage);
      if (!opened) continue;
      await scrollTop(page);
      await page.waitForTimeout(400);
      await glide(page, 380);
      await scrollTop(page);
    }

    narrate("镜头视频：同一镜头保留多个历史版本");
    if (await openStage(page, "video")) {
      const cards = page.locator(".asset-card");
      if (await cards.count()) {
        await cards.first().click();
        await page.waitForTimeout(BEAT);
      }
      await glide(page, 420);
      await scrollTop(page);
    }

    narrate("成片合成：预览并下载真实 MP4");
    if (await openStage(page, "assembly", 2200)) {
      const video = page.locator("#assemblyPanel video").first();
      if (await video.count()) {
        await video.scrollIntoViewIfNeeded();
        await page.waitForTimeout(2600);
      }
      await glide(page, 300);
      await scrollTop(page);
    }

    narrate("导出与交付");
    if (await openStage(page, "export")) {
      await glide(page, 320);
      await scrollTop(page);
    }

    if (LONG_ID) {
      narrate("长文本：章节树按节圈定改编范围");
      await selectProject(page, LONG_ID);
      if (await openStage(page, "storyline", 1800)) {
        await scrollTop(page);
        // 勾一节，展示"未保存改动"的提示（这正是本轮修掉的那类界面缺陷）。
        const boxes = page.locator("[data-chapter-check]");
        const total = await boxes.count();
        if (total > 3) {
          await boxes.nth(Math.floor(total / 2)).click();
          await page.waitForTimeout(1600);
        }
        await glide(page, 500, 14);
        await scrollTop(page);
      }

      narrate("全文检索：按字面片段在原文里找块");
      const searchBox = page.locator("#memoryQueryInput");
      if (await searchBox.count()) {
        await searchBox.first().fill("蛊虫");
        await page.waitForTimeout(400);
        const button = page.locator("#memorySearchBtn");
        if (await button.count()) {
          await button.first().click();
          await page.waitForTimeout(2600);
        }
        await glide(page, 360);
        await scrollTop(page);
      } else {
        console.log("WARN: 未找到记忆检索输入框，跳过这一段");
      }
    }

    await page.waitForTimeout(BEAT);
  } finally {
    videoPath = await page.video()?.path() ?? "";
    await context.close();
    await browser.close();
  }
  if (!videoPath || !fs.existsSync(videoPath)) {
    throw new Error(`录屏文件未生成：${videoPath || "(空)"}`);
  }
  console.log(`VIDEO_PATH=${videoPath}`);
  console.log("DEMO RECORDING FINISHED");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
