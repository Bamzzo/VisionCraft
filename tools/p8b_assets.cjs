/**
 * P8-B 浏览器验收：当前项目上传 JPEG/PNG/音频/SRT，项目隔离与刷新恢复。
 * 由 tools/test_p8b_browser.py 启动。只使用本地夹具，不调用付费 API。
 * 截图写入 output/playwright/p8b-assets/，不得入库。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright", "p8b-assets");
const PROJECT_A = process.env.P8B_PROJECT_A;
const PROJECT_B = process.env.P8B_PROJECT_B;
const JPEG_PATH = process.env.P8B_JPEG;
const PNG_PATH = process.env.P8B_PNG;
const WAV_PATH = process.env.P8B_WAV;
const SRT_PATH = process.env.P8B_SRT;

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

async function apiGet(page, p) {
  const res = await page.request.get(`${BASE}${p}`);
  if (!res.ok()) throw new Error(`GET ${p} -> ${res.status()} ${await res.text()}`);
  return res.json();
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
    (id) => document.querySelector(`[data-stage-id="${id}"].active, [data-stage-id="${id}"].current, [data-stage-id="${id}"][aria-current="true"]`),
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

async function horizontalOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  if (!PROJECT_A || !PROJECT_B) throw new Error("缺少 P8B_PROJECT_A / P8B_PROJECT_B");
  for (const [label, filePath] of [
    ["JPEG", JPEG_PATH],
    ["PNG", PNG_PATH],
    ["WAV", WAV_PATH],
    ["SRT", SRT_PATH],
  ]) {
    if (!filePath || !fs.existsSync(filePath)) throw new Error(`缺少 ${label} 夹具`);
  }
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(20000);
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForSelector(`.project-item[data-project-id="${PROJECT_A}"]`, { timeout: 15000 });
    await selectProject(page, PROJECT_A);
    pass("打开临时项目甲");

    await openShotInspector(page, "video");
    await page.selectOption("#videoModeSelect", "i2v");
    await page.waitForFunction(() => {
      const btn = document.querySelector("[data-action='generate-video']");
      return Boolean(btn && btn.disabled);
    });
    pass("未登记首帧时 I2V 按钮不可用");

    await page.setInputFiles('[data-asset-upload="first_frame"]', JPEG_PATH);
    await page.waitForFunction(
      () => (document.querySelector('[data-upload-status="first_frame"]')?.textContent || "").includes("成功"),
      null,
      { timeout: 20000 }
    );
    await page.waitForFunction(
      () => (document.querySelector("[data-first-frame-status]")?.textContent || "").includes("已登记为首帧"),
      null,
      { timeout: 8000 }
    );
    const firstSrc = await page.locator(".local-keyframe-thumb").first().getAttribute("src");
    if (!firstSrc || firstSrc.startsWith("data:")) throw new Error("首帧预览不得使用 Data URL");
    if (!firstSrc.includes(PROJECT_A)) throw new Error("首帧预览必须属于当前项目");
    pass("上传 JPEG 首帧后显示项目内预览");

    await page.setInputFiles('[data-asset-upload="last_frame"]', PNG_PATH);
    await page.waitForFunction(
      () => (document.querySelector('[data-upload-status="last_frame"]')?.textContent || "").includes("成功"),
      null,
      { timeout: 20000 }
    );
    await page.setInputFiles('[data-asset-upload="reference_image"]', JPEG_PATH);
    await page.waitForFunction(
      () => (document.querySelector('[data-upload-status="reference_image"]')?.textContent || "").includes("成功"),
      null,
      { timeout: 20000 }
    );
    pass("上传尾帧与参考图后显示当前项目资产");

    const visionDisabled = await page.locator("[data-adapt='vision-review']").count()
      ? await page.locator("[data-adapt='vision-review']").isDisabled()
      : false;
    await openShotInspector(page, "keyframes");
    const visionAfter = await page.locator("[data-adapt='vision-review']").isDisabled();
    if (visionAfter && visionDisabled) {
      /* keyframes 页才渲染视觉按钮 */
    }
    if (await page.locator("[data-adapt='vision-review']").count()) {
      if (await page.locator("[data-adapt='vision-review']").isDisabled()) {
        throw new Error("登记首帧后视觉检查按钮应可用");
      }
    }
    await page.selectOption("#videoModeSelect", "i2v").catch(() => {});
    const i2vDisabled = await page.locator("[data-action='generate-video']").isDisabled();
    if (i2vDisabled) {
      const reason = await page.locator("[data-action='generate-video']").getAttribute("title");
      throw new Error(`登记后 I2V 应可用，实际：${reason || "仍禁用"}`);
    }
    pass("有效首帧后 Vision/I2V 按钮可用");
    await page.screenshot({ path: path.join(OUT, "01-first-frame-1440.png"), fullPage: true });

    await selectProject(page, PROJECT_B);
    await openStage(page, "video");
    const leaked = await page.evaluate((id) => {
      const imgs = [...document.querySelectorAll("#stageWorkspace img, #assetDetail img, .local-keyframe-thumb")].map((img) => img.getAttribute("src") || "");
      const selects = [...document.querySelectorAll("#firstFrameSelect, #lastFrameSelect, #referenceFrameSelect, #assemblyAudioPath, #assemblySubtitleSrt")].map((node) => node.value || "");
      return imgs.some((src) => src.includes(id)) || selects.some((value) => value.includes(id));
    }, PROJECT_A);
    if (leaked) throw new Error("项目切换后串入了甲项目图片路径");
    pass("项目切换不串图片");

    await selectProject(page, PROJECT_A);
    await openStage(page, "assembly");
    await page.waitForSelector("#assemblySettingsForm");
    await page.setInputFiles('[data-asset-upload="background_audio"]', WAV_PATH);
    await page.waitForFunction(
      () => (document.querySelector('[data-upload-status="background_audio"]')?.textContent || "").includes("成功"),
      null,
      { timeout: 20000 }
    );
    await page.setInputFiles('[data-asset-upload="subtitle"]', SRT_PATH);
    await page.waitForFunction(
      () => (document.querySelector('[data-upload-status="subtitle"]')?.textContent || "").includes("成功"),
      null,
      { timeout: 20000 }
    );
    const audioOptions = await page.locator("#assemblyAudioPath option").count();
    const srtOptions = await page.locator("#assemblySubtitleSrt option").count();
    if (audioOptions < 2) throw new Error("成片工作区未列出当前项目音频");
    if (srtOptions < 2) throw new Error("成片工作区未列出当前项目字幕");
    await page.check("#assemblyAudioEnabled");
    const audioValue = await page.locator("#assemblyAudioPath").inputValue();
    if (!audioValue.includes(PROJECT_A)) throw new Error("背景音频必须属于当前项目");
    await page.selectOption("#assemblySubtitleSrt", { index: 1 }).catch(async () => {
      const value = await page.locator("#assemblySubtitleSrt option").nth(1).getAttribute("value");
      if (value) await page.selectOption("#assemblySubtitleSrt", value);
    });
    const enableSubtitle = await page.evaluate(() => {
      const errors = document.querySelector("#assemblySettingsErrors")?.innerText || "";
      return !errors.includes("FFmpeg") && !errors.includes("字体");
    });
    if (enableSubtitle) {
      await page.check("#assemblySubtitleEnabled");
    } else {
      await page.uncheck("#assemblySubtitleEnabled");
      console.log("SKIP: 本机无法烧录字幕，仅保存 SRT 路径");
    }
    await page.click("[data-action='save-assembly-settings']");
    await page.waitForFunction(
      () => {
        const job = document.querySelector("#jobMessage")?.textContent || "";
        const stale = document.querySelector("#assemblyFreshness")?.textContent || "";
        const body = document.body.innerText || "";
        return job.includes("需要重新合成") || stale.includes("已过期") || body.includes("需要重新合成");
      },
      null,
      { timeout: 15000 }
    );
    pass("上传音频字幕并保存成片配置后显示需要重新合成");
    await page.screenshot({ path: path.join(OUT, "02-assembly-1440.png"), fullPage: true });

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(`.project-item[data-project-id="${PROJECT_A}"]`);
    await selectProject(page, PROJECT_A);
    await openStage(page, "assembly");
    await page.waitForSelector("#assemblyAudioPath");
    const restoredAudio = await page.locator("#assemblyAudioPath").inputValue();
    const restoredSrt = await page.locator("#assemblySubtitleSrt").inputValue();
    if (!restoredAudio.includes(PROJECT_A)) throw new Error("刷新后背景音频配置丢失");
    if (!restoredSrt.includes(PROJECT_A)) throw new Error("刷新后字幕配置丢失");
    pass("刷新页面后成片配置仍存在");

    await selectProject(page, PROJECT_B);
    await openStage(page, "assembly");
    const bAudio = await page.locator("#assemblyAudioPath").inputValue();
    const bSrt = await page.locator("#assemblySubtitleSrt").inputValue();
    if (bAudio.includes(PROJECT_A) || bSrt.includes(PROJECT_A)) throw new Error("项目切换后串入了甲项目成片素材");
    pass("项目切换不串音频和字幕");

    await selectProject(page, PROJECT_A);
    await openShotInspector(page, "keyframes");
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.waitForTimeout(200);
    if ((await horizontalOverflow(page)) > 2) throw new Error("1100 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "03-narrow-1100.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1440 窗口出现横向溢出");
    await page.setViewportSize({ width: 1920, height: 1080 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1920 窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "04-wide-1920.png"), fullPage: true });
    pass("1100 / 1440 / 1920 布局无横向滚动");

    const proj = await apiGet(page, `/api/projects/${PROJECT_A}`);
    const blob = JSON.stringify(proj);
    if (blob.toLowerCase().includes("data:image") || blob.toLowerCase().includes("base64,")) {
      throw new Error("项目 JSON 含 Data URL 或 Base64");
    }
    pass("项目数据不含 Data URL 或 Base64");
    console.log("PASS: P8-B browser asset upload");
    console.log("INFO: real_network=否 cost_cny=0");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
