/**
 * V1 演示收口全链路浏览器验收。由 tools/test_v1_demo_browser.py 调用。
 * 截图只写入 output/playwright/v1-*.png，不得入库。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const READY_ID = process.env.V1_READY_ID;
const OTHER_ID = process.env.V1_OTHER_ID;
const HAS_FFMPEG = process.env.V1_HAS_FFMPEG === "1";
const UI_TITLE = "V1E2E 全链路验收项目 · 青茅山夜路超长中文标题用于检查换行省略与错位";
const SAMPLE = (
  "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。" +
  "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
  "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
  "最终他停在山门前，留下未说完的话。"
);

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

async function openStage(page, stageId) {
  await page.click(`[data-stage-id="${stageId}"]`);
  await page.waitForFunction(
    (id) => document.querySelector(`[data-stage-id="${id}"].active, [data-stage-id="${id}"].current`),
    stageId,
    { timeout: 8000 }
  ).catch(() => {});
}

async function waitAdapt(page, action, timeout = 25000) {
  await page.waitForSelector(`[data-adapt="${action}"]`, { timeout });
}

async function main() {
  if (!READY_ID || !OTHER_ID) throw new Error("缺少 V1 E2E 项目 ID");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForFunction(() => (document.querySelector("#ratioInput")?.options?.length || 0) > 0, null, { timeout: 10000 });
    await page.waitForFunction(
      () => {
        const list = document.querySelector("#projectList");
        return Boolean(list && (list.querySelector(".project-item") || (list.textContent || "").includes("暂无")));
      },
      null,
      { timeout: 15000 }
    );
    if (await page.locator("#projectForm.hidden").count()) {
      await page.click("#newProjectBtn");
    }
    await page.waitForSelector("#projectForm:not(.hidden)", { timeout: 8000 });
    await page.waitForSelector("#submitProjectBtn:visible");
    await page.fill("#titleInput", UI_TITLE);
    await page.fill("#sourceTextInput", SAMPLE);
    if (await page.locator("#resolutionInput option[value='1280x720']").count()) {
      await page.selectOption("#resolutionInput", "1280x720");
    }
    await page.screenshot({ path: path.join(OUT, "v1-create-form-1440.png"), fullPage: true });
    await page.click("#submitProjectBtn");
    await page.waitForFunction(
      (title) => (document.querySelector("#summaryFields")?.innerText || "").includes(title.slice(0, 10)),
      UI_TITLE,
      { timeout: 15000 }
    );
    const createdId = await page.evaluate(() => document.querySelector(".project-item.active")?.getAttribute("data-project-id"));
    console.log("INFO: UI_PROJECT_ID=" + createdId);
    await page.screenshot({ path: path.join(OUT, "v1-created-summary-1440.png"), fullPage: true });
    pass("新建项目并填写配置后创建成功");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    if (createdId) await selectProject(page, createdId);
    await page.waitForFunction(
      (title) => {
        const text = document.querySelector("#summaryFields")?.innerText || "";
        return text.includes(title.slice(0, 10)) && text.includes("1280x720");
      },
      UI_TITLE,
      { timeout: 15000 }
    );
    await page.screenshot({ path: path.join(OUT, "v1-reload-summary-1440.png"), fullPage: true });
    pass("刷新页面后项目摘要和配置仍存在");

    await page.click("#runWorkflowBtn");
    await openStage(page, "text");
    await waitAdapt(page, "confirm-scope", 30000);
    await page.screenshot({ path: path.join(OUT, "v1-adaptation-options-1440.png"), fullPage: true });
    pass("短文本改编方案可见");
    await page.click("[data-adapt='confirm-scope']");
    await openStage(page, "bible");
    await waitAdapt(page, "confirm-bible", 30000);
    await page.screenshot({ path: path.join(OUT, "v1-story-bible-1440.png"), fullPage: true });
    pass("Story Bible 可见");
    await page.click("[data-adapt='confirm-bible']");
    await openStage(page, "storyboard");
    await waitAdapt(page, "confirm-storyboard", 30000);
    await page.screenshot({ path: path.join(OUT, "v1-storyboard-1440.png"), fullPage: true });
    pass("分镜草案可见");
    await page.click("[data-adapt='confirm-storyboard']");
    await page.waitForFunction(
      () => (document.querySelector("#summaryFields")?.innerText || "").includes("production_ready"),
      null,
      { timeout: 20000 }
    );
    await openStage(page, "keyframes");
    await page.screenshot({ path: path.join(OUT, "v1-keyframes-1440.png"), fullPage: true });
    await openStage(page, "video");
    await page.screenshot({ path: path.join(OUT, "v1-shot-versions-1440.png"), fullPage: true });
    pass("确认分镜后进入镜头制作，可查看版本与关键帧状态");

    if (!HAS_FFMPEG) {
      skip("本地视频夹具任务中心 / 字幕音频配置 / 真实成片合成");
      skip("旧成片过期与新成片生成");
    } else {
      await selectProject(page, READY_ID);
      await openStage(page, "assembly");
      await page.waitForSelector("#assemblyPanel", { timeout: 8000 });
      const bodyHidden = await page.locator("#taskCenterBody.hidden").count();
      if (bodyHidden) await page.click("#taskCenterToggle");
      await page.screenshot({ path: path.join(OUT, "v1-assembly-ready-1440.png"), fullPage: true });
      pass("使用本地视频夹具进入成片工作区");

      await page.click("#assembleProjectBtn");
      await page.waitForFunction(
        () => /成片合成|排队|处理/.test(document.querySelector("#jobMessage")?.textContent || "") ||
          /排队|处理|完成/.test(document.querySelector("#jobTimeline")?.innerText || ""),
        null,
        { timeout: 10000 }
      );
      await page.screenshot({ path: path.join(OUT, "v1-assembly-running-1440.png"), fullPage: true });
      pass("无需手动刷新即可看到合成排队或处理中");
      await page.waitForFunction(
        () => {
          const video = document.querySelector("#assemblyPanel video");
          const btn = document.querySelector("#assembleProjectBtn")?.textContent || "";
          const fresh = document.querySelector("#assemblyFreshness")?.textContent || "";
          return video && video.readyState >= 2 && btn.includes("重新合成") && fresh.includes("当前有效");
        },
        null,
        { timeout: 60000 }
      );
      if (!(await page.locator('#assemblyPanel a[download]').count())) throw new Error("缺少下载入口");
      await page.screenshot({ path: path.join(OUT, "v1-assembly-complete-1440.png"), fullPage: true });
      pass("成片可以预览和下载真实 MP4");

      if (await page.locator("#assemblyKeepSourceAudio").count()) await page.check("#assemblyKeepSourceAudio");
      if (await page.locator("#assemblyAudioEnabled").count()) await page.check("#assemblyAudioEnabled");
      const audioSelect = page.locator("#assemblyAudioPath");
      if ((await audioSelect.locator("option").count()) >= 2) await audioSelect.selectOption({ index: 1 });
      if (await page.locator("#assemblySubtitleEnabled").count()) await page.check("#assemblySubtitleEnabled");
      await page.fill("#assemblySubtitleText", "青茅山夜路，传承将起。超长中文字幕用于检查换行。");
      await page.click("#saveAssemblySettingsBtn");
      await page.waitForFunction(
        () => (document.querySelector("#assemblyFreshness")?.textContent || "").includes("已过期"),
        null,
        { timeout: 12000 }
      );
      await page.screenshot({ path: path.join(OUT, "v1-assembly-stale-1440.png"), fullPage: true });
      pass("配置字幕、背景音和原声并保存后显示需要重新合成");

      await page.click("#assembleProjectBtn");
      await page.waitForFunction(
        () => {
          const fresh = document.querySelector("#assemblyFreshness")?.textContent || "";
          return fresh.includes("当前有效") && document.querySelectorAll(".assembly-history-item").length >= 1;
        },
        null,
        { timeout: 60000 }
      );
      pass("重新合成后生成新成片，历史仍在");

      await page.click("#editProjectBtn");
      await page.waitForSelector("#projectForm:not(.hidden)");
      await page.fill("#titleInput", "V1E2E 设置已修改 · 超长中文标题第二次检查错位");
      if (await page.locator("#resolutionInput option[value='1920x1080']").count()) {
        await page.selectOption("#resolutionInput", "1920x1080");
      }
      await page.click("#submitProjectBtn");
      await page.waitForFunction(
        () => (document.querySelector("#summaryFields")?.innerText || "").includes("设置已修改"),
        null,
        { timeout: 12000 }
      );
      await openStage(page, "assembly");
      await page.waitForFunction(
        () => (document.querySelector("#assemblyFreshness")?.textContent || "").includes("已过期"),
        null,
        { timeout: 12000 }
      );
      await page.screenshot({ path: path.join(OUT, "v1-settings-stale-1440.png"), fullPage: true });
      pass("修改项目设置后旧成片显示过期，工作区无需手动刷新");
      await openStage(page, "export");
      const staleExport = await page.locator("#stageWorkspace").innerText();
      if (!staleExport.includes("返回成片合成")) throw new Error("过期成片的导出页应显示返回成片合成");
      if (await page.locator("#assembleProjectBtn").count()) {
        throw new Error("导出页不得直接提供合成按钮");
      }
      await page.click("[data-action='goto-assembly']");
      await page.waitForFunction(
        () => (document.querySelector("#stageWorkspaceTitle")?.textContent || "").includes("成片合成"),
        null,
        { timeout: 8000 }
      );
      pass("过期成片可从导出页返回成片合成");
      await page.click("#assembleProjectBtn");
      await page.waitForFunction(
        () => (document.querySelector("#assemblyFreshness")?.textContent || "").includes("当前有效"),
        null,
        { timeout: 60000 }
      );
      pass("按新设置重新合成后生成新成片");
    }

    await page.setViewportSize({ width: 1100, height: 900 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2
    );
    if (overflow) throw new Error("1100px 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "v1-narrow-1100.png"), fullPage: true });
    pass("1100px 窄窗口无横向溢出");

    await selectProject(page, OTHER_ID);
    const otherSummary = await page.locator("#summaryFields").innerText();
    if (otherSummary.includes("全链路验收") || otherSummary.includes("设置已修改")) {
      throw new Error("切换项目后仍显示上一项目配置");
    }
    const timeline = await page.locator("#jobTimeline").innerText().catch(() => "");
    if (timeline.includes("成片合成已排队") || timeline.includes("成片已生成") || timeline.includes("项目设置已更新")) {
      throw new Error("切换项目后仍显示上一项目任务");
    }
    await page.screenshot({ path: path.join(OUT, "v1-project-switch-1440.png"), fullPage: true });
    pass("切换到另一项目后数据、任务和配置不串项目");

    if (createdId) {
      await selectProject(page, createdId);
      await page.click("#editProjectBtn");
      await page.fill("#titleInput", "");
      await page.click("#submitProjectBtn");
      await page.waitForFunction(
        () => /不能为空|保存项目设置失败/.test(document.querySelector("#feedbackResult")?.innerText || ""),
        null,
        { timeout: 8000 }
      );
      const failText = await page.locator("#feedbackResult").innerText();
      if (/ffmpeg|ffprobe|api_key|sk-/i.test(failText)) throw new Error("错误提示包含敏感信息");
      await page.screenshot({ path: path.join(OUT, "v1-long-error-1440.png"), fullPage: true });
      pass("长中文错误提示无乱码，且不含密钥或命令");
    }
    console.log("ALL V1 BROWSER CHECKS PASSED");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
