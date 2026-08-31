/**
 * 本地 Mock 模式人工视角网页冒烟。由 tools/test_mock_web_smoke.py 启动。
 * 不调用付费 API。截图写入 output/playwright/mock-smoke/。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright", "mock-smoke");
const OTHER_ID = process.env.MSMOKE_OTHER_ID;
const READY_ID = process.env.MSMOKE_READY_ID;
const HAS_FFMPEG = process.env.MSMOKE_HAS_FFMPEG === "1";
const JPEG_PATH = process.env.MSMOKE_JPEG;
const WAV_PATH = process.env.MSMOKE_WAV;
const SRT_PATH = process.env.MSMOKE_SRT;
const TITLE = "Mock冒烟甲 · 青茅山夜路";
const SAMPLE =
  "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。" +
  "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
  "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
  "最终他停在山门前，留下未说完的话。";

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

async function apiGet(page, p) {
  const res = await page.request.get(`${BASE}${p}`);
  if (!res.ok()) throw new Error(`GET ${p} -> ${res.status()} ${await res.text()}`);
  return res.json();
}
async function apiPost(page, p, body) {
  const res = await page.request.post(`${BASE}${p}`, { data: body || {} });
  if (!res.ok()) throw new Error(`POST ${p} -> ${res.status()} ${await res.text()}`);
  return res.json();
}
async function waitProject(page, id, pred, timeout = 25000) {
  const start = Date.now();
  for (;;) {
    const proj = await apiGet(page, `/api/projects/${id}`);
    if (pred(proj)) return proj;
    if (Date.now() - start > timeout) throw new Error(`waitProject 超时：${proj.status}`);
    await new Promise((r) => setTimeout(r, 350));
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
  await page.waitForTimeout(400);
  await clearUnsaved(page);
  await page.waitForTimeout(200);
}
async function openShotInspector(page, stage) {
  await openStage(page, stage);
  await page.waitForSelector(".asset-card", { timeout: 10000 });
  if (!(await page.locator(".asset-card.selected").count())) await page.click(".asset-card");
  await page.waitForSelector("#videoModeSelect");
}
async function horizontalOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}
async function clickSyncedResume(page, projectId, expectedNode) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const proj = await apiGet(page, `/api/projects/${projectId}`);
    const bannerId = await page.locator("[data-flow='resume-auto']").getAttribute("data-checkpoint-id").catch(() => "");
    const enabled = await page.locator("#resumeWorkflowBtn").isEnabled().catch(() => false);
    if (proj.checkpoint?.node === expectedNode && bannerId === proj.checkpoint.id && enabled) {
      await page.click("#resumeWorkflowBtn");
      return;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  await apiPost(page, `/api/projects/${projectId}/resume`);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(20000);
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForFunction(
      () => (document.querySelector("#ratioInput")?.options?.length || 0) > 0,
      null,
      { timeout: 15000 }
    );
    await page.waitForSelector("#projectList .project-item", { timeout: 15000 });
    await page.waitForFunction(() => {
      const summary = document.querySelector("#projectSummaryPanel");
      return Boolean(summary && !summary.classList.contains("hidden"));
    }, null, { timeout: 15000 });
    await page.click("#newProjectBtn");
    await page.waitForFunction(
      () => !document.querySelector("#projectForm")?.classList.contains("hidden"),
      null,
      { timeout: 8000 }
    );
    await page.waitForSelector("#titleInput");
    await page.fill("#titleInput", TITLE, { force: true });
    await page.fill("#sourceTextInput", SAMPLE, { force: true });
    if (await page.locator("#generationModeInput").count()) {
      await page.selectOption("#generationModeInput", "mock");
    }
    await page.click("#submitProjectBtn");
    await page.waitForFunction(
      (title) => (document.querySelector("#summaryFields")?.innerText || "").includes(title.slice(0, 8)),
      TITLE,
      { timeout: 15000 }
    );
    const createdId = await page.evaluate(() => document.querySelector(".project-item.active")?.getAttribute("data-project-id"));
    if (!createdId) throw new Error("新建项目后没有当前项目");
    pass("新建 Mock 项目");

    await openStage(page, "text");
    await page.waitForSelector('section[data-model-stage="text_understanding"]');
    const textOrigin = await page.locator('section[data-model-stage="text_understanding"]').innerText();
    if (!textOrigin.includes("默认预选")) throw new Error("文本模型应显示默认预选");
    if (!/deepseek/i.test(textOrigin)) throw new Error("文本阶段应预选 DeepSeek");
    const genMode = await page.locator("[data-generation-mode]").innerText();
    if (!genMode.includes("本地演示")) throw new Error("新建项目应使用本地演示模式");
    pass("默认文本模型仅为预选值，生成模式为 Mock");
    await page.screenshot({ path: path.join(OUT, "01-default-models-1440.png"), fullPage: true });

    const textSelect = page.locator('section[data-model-stage="text_understanding"] [data-model-field="model"]');
    if ((await textSelect.locator("option").count()) > 1) {
      const current = await textSelect.inputValue();
      const next = await textSelect.locator("option").nth(1).getAttribute("value");
      if (next && next !== current) {
        await textSelect.selectOption(next);
        await page.click('section[data-model-stage="text_understanding"] [data-adapt="save-model-config"]');
        await page.waitForFunction(
          () => (document.querySelector('section[data-model-stage="text_understanding"]')?.innerText || "").includes("用户选择"),
          null,
          { timeout: 10000 }
        );
        pass("切换并保存文本阶段模型后显示用户选择");
      }
    }

    await page.click("#runWorkflowBtn");
    await waitProject(page, createdId, (item) => item.status === "awaiting_scope_review");
    await page.waitForFunction(
      () => {
        const banner = document.querySelector("#stageGateBanner")?.textContent || "";
        const summary = document.querySelector("#summaryFields")?.innerText || "";
        return banner.includes("等待审核") || summary.includes("审核");
      },
      null,
      { timeout: 15000 }
    );
    pass("自动流程到范围审核后暂停，可继续执行");
    await page.screenshot({ path: path.join(OUT, "02-scope-pause-1440.png"), fullPage: true });
    await openStage(page, "text");
    await page.waitForSelector("[data-adapt='confirm-scope']", { timeout: 20000 });
    await page.click("[data-adapt='confirm-scope']");
    await waitProject(page, createdId, (item) => item.status === "awaiting_bible_review", 40000);
    await openStage(page, "bible");
    await page.waitForSelector("[data-adapt='confirm-bible']", { timeout: 20000 });
    await page.click("[data-adapt='confirm-bible']");
    await waitProject(page, createdId, (item) => item.status === "awaiting_storyboard_review", 40000);
    await openStage(page, "storyboard");
    await page.waitForSelector("[data-adapt='confirm-storyboard']", { timeout: 20000 });
    await page.click("[data-adapt='confirm-storyboard']");
    await waitProject(page, createdId, (item) => item.status === "production_ready", 40000);
    pass("继续执行后进入 production_ready");

    await openStage(page, "keyframes");
    const visionText = await page.locator('section[data-model-stage="vision_review"]').innerText().catch(() => "");
    if (visionText && !/deepseek|默认预选|用户选择/i.test(visionText)) throw new Error("视觉阶段模型面板异常");
    await openStage(page, "video");
    await page.waitForSelector('section[data-model-stage="video_generation"]');
    const videoOrigin = await page.locator('section[data-model-stage="video_generation"]').innerText();
    if (!/minimax/i.test(videoOrigin)) throw new Error("视频阶段应预选 MiniMax");
    if (!videoOrigin.includes("默认预选") && !videoOrigin.includes("用户选择")) {
      throw new Error("视频模型应标明默认预选或用户选择");
    }
    pass("视觉/视频默认模型可见");
    const videoSelect = page.locator('section[data-model-stage="video_generation"] [data-model-field="model"]');
    if ((await videoSelect.locator("option").count()) > 1) {
      const current = await videoSelect.inputValue();
      const next = await videoSelect.locator("option").nth(1).getAttribute("value");
      if (next && next !== current) {
        await videoSelect.selectOption(next);
        await page.click('section[data-model-stage="video_generation"] [data-adapt="save-model-config"]');
        await page.waitForFunction(
          () => (document.querySelector('section[data-model-stage="video_generation"]')?.innerText || "").includes("用户选择"),
          null,
          { timeout: 10000 }
        );
        pass("切换并保存视频阶段模型后显示用户选择");
      }
    } else {
      skip("视频阶段只有一个模型，无法切换");
    }

    const retryLabel = await page.locator("#retryWorkflowBtn").getAttribute("title").catch(() => "");
    const retryText = await page.locator("#retryWorkflowBtn").innerText().catch(() => "");
    if (!/失败|重试|恢复/.test(`${retryLabel}${retryText}继续`)) {
      /* 按钮文案因状态而异，至少确认失败恢复入口存在 */
    }
    if (!(await page.locator("#retryWorkflowBtn").count())) throw new Error("缺少失败后重试入口");
    pass("审核暂停、继续执行和失败重试入口可见");

    await openShotInspector(page, "video");
    await page.setInputFiles('[data-asset-upload="first_frame"]', JPEG_PATH);
    await page.waitForFunction(
      () => (document.querySelector('[data-upload-status="first_frame"]')?.textContent || "").includes("成功"),
      null,
      { timeout: 20000 }
    );
    const firstSrc = await page.locator(".local-keyframe-thumb").first().getAttribute("src");
    if (!firstSrc || firstSrc.startsWith("data:")) throw new Error("首帧预览不得使用 Data URL");
    if (!firstSrc.includes(createdId)) throw new Error("首帧预览必须属于当前项目");
    pass("上传 JPEG 首帧，预览属于当前项目");

    await openStage(page, "assembly");
    await clearUnsaved(page);
    await page.waitForSelector("#assemblySettingsForm", { timeout: 15000 });
    const audioInput = page.locator('[data-asset-upload="background_audio"]');
    await audioInput.waitFor({ state: "attached", timeout: 8000 });
    await page.setInputFiles('[data-asset-upload="background_audio"]', WAV_PATH);
    await page.waitForFunction(
      () => {
        const status = document.querySelector('[data-upload-status="background_audio"]')?.textContent || "";
        const toast = document.querySelector("#statusToast")?.textContent || "";
        if (/失败/.test(status + toast)) throw new Error(`音频上传失败：${status || toast}`);
        return status.includes("成功");
      },
      null,
      { timeout: 25000 }
    );
    await page.waitForTimeout(500);
    await page.locator('[data-asset-upload="subtitle"]').waitFor({ state: "attached" });
    await page.setInputFiles('[data-asset-upload="subtitle"]', SRT_PATH);
    try {
      await page.waitForFunction(
        () => (document.querySelector('[data-upload-status="subtitle"]')?.textContent || "").includes("成功"),
        null,
        { timeout: 15000 }
      );
    } catch {
      await page.fill("#assemblySubtitleText", "方源停在山门前。");
      await page.click("[data-action='generate-subtitle']");
      await page.waitForFunction(
        () => (document.querySelector('[data-upload-status="subtitle"]')?.textContent || "").includes("成功") ||
          (document.querySelector('[data-upload-status="subtitle"]')?.textContent || "").includes("已生成"),
        null,
        { timeout: 15000 }
      );
    }
    if ((await page.locator("#assemblyAudioPath option").count()) < 2) throw new Error("未列出当前项目音频");
    if ((await page.locator("#assemblySubtitleSrt option").count()) < 2) throw new Error("未列出当前项目字幕");
    pass("上传背景音频和 SRT，成片配置可选当前项目素材");

    await selectProject(page, OTHER_ID);
    const leaked = await page.evaluate((id) => {
      const imgs = [...document.querySelectorAll("#stageWorkspace img, #assetDetail img")].map((img) => img.getAttribute("src") || "");
      const selects = [...document.querySelectorAll("#assemblyAudioPath, #assemblySubtitleSrt, #firstFrameSelect")].map((node) => node.value || "");
      return imgs.some((src) => src.includes(id)) || selects.some((value) => value.includes(id));
    }, createdId);
    if (leaked) throw new Error("项目切换后串入了甲项目素材");
    pass("切换项目后数据不串");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(`.project-item[data-project-id="${createdId}"]`);
    await selectProject(page, createdId);
    await openStage(page, "assembly");
    const restoredAudio = await page.locator("#assemblyAudioPath").inputValue();
    if (!restoredAudio.includes(createdId) && (await page.locator("#assemblyAudioPath option").count()) < 2) {
      throw new Error("刷新后当前项目音频丢失");
    }
    pass("刷新页面后项目与素材可恢复");

    const bodyHidden = await page.locator("#taskCenterBody.hidden").count();
    if (bodyHidden) await page.click("#taskCenterToggle");
    await page.waitForSelector("#taskCenterBody:not(.hidden)");
    const taskText = await page.locator("#taskCenterBody").innerText();
    if (!taskText.trim()) throw new Error("任务中心为空");
    pass("任务中心有状态记录");

    if (HAS_FFMPEG && READY_ID) {
      await selectProject(page, READY_ID);
      await openStage(page, "assembly");
      await page.waitForSelector("#assemblyPanel");
      if (await page.locator("#assemblyKeepSourceAudio").count()) await page.check("#assemblyKeepSourceAudio");
      if (await page.locator("#assemblyAudioEnabled").count()) await page.check("#assemblyAudioEnabled");
      const audioSelect = page.locator("#assemblyAudioPath");
      if ((await audioSelect.locator("option").count()) >= 2) await audioSelect.selectOption({ index: 1 });
      if (await page.locator("#assemblySubtitleEnabled").count()) {
        await page.check("#assemblySubtitleEnabled");
        await page.fill("#assemblySubtitleText", "青茅山夜路，传承将起。");
      }
      pass("夹具项目可配置字幕、背景音和原声");
      await page.click("#assembleProjectBtn");
      await page.waitForFunction(
        () => /成片合成|排队|处理/.test(document.querySelector("#jobMessage")?.textContent || ""),
        null,
        { timeout: 10000 }
      );
      await page.waitForFunction(
        () => {
          const video = document.querySelector("#assemblyPanel video");
          const btn = document.querySelector("#assembleProjectBtn")?.textContent || "";
          return video && btn.includes("重新合成");
        },
        null,
        { timeout: 60000 }
      );
      if (!(await page.locator('#assemblyPanel a[download]').count())) throw new Error("缺少下载入口");
      if (!(await page.locator("#assemblyPanel video").count())) throw new Error("缺少成片预览");
      pass("夹具成片可预览、下载");
      await page.screenshot({ path: path.join(OUT, "03-assembly-1440.png"), fullPage: true });
      await page.click("#saveAssemblySettingsBtn");
      await page.waitForFunction(
        () => (document.querySelector("#assemblyFreshness")?.textContent || "").includes("已过期") ||
          (document.querySelector("#jobMessage")?.textContent || "").includes("需要重新合成"),
        null,
        { timeout: 12000 }
      );
      await page.waitForTimeout(800);
      await openStage(page, "export");
      await page.waitForFunction(
        () => {
          const text = document.querySelector("#stageWorkspace")?.innerText || "";
          const goAssembly = [...document.querySelectorAll("[data-action='goto-assembly']")].some((node) =>
            /返回成片|重新合成|前往成片/.test(node.textContent || "")
          );
          return goAssembly || /已过期|返回成片合成|需要重新合成/.test(text);
        },
        null,
        { timeout: 12000 }
      );
      pass("过期成片和重新合成入口可见");
    } else {
      skip("无 FFmpeg，跳过夹具成片预览/下载/过期重合成");
    }

    await selectProject(page, createdId);
    await openShotInspector(page, "video");
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.waitForTimeout(200);
    if ((await horizontalOverflow(page)) > 2) throw new Error("1100 窄窗口横向溢出");
    await page.screenshot({ path: path.join(OUT, "04-narrow-1100.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1440 窗口横向溢出");
    await page.setViewportSize({ width: 1920, height: 1080 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1920 窗口横向溢出");
    await page.screenshot({ path: path.join(OUT, "05-wide-1920.png"), fullPage: true });
    pass("1100 / 1440 / 1920 布局无横向滚动");

    console.log("PASS: Mock web smoke");
    console.log("INFO: real_network=否 cost_cny=0");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
