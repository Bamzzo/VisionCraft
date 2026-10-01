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

// 页面侧的错误缓冲：前端 init() 是串行 await 链（checkHealth → capabilities →
// diagnostics → renderCapabilities → loadProjects → renderAll），链上任何一处抛错
// 都会让后续渲染整体不发生，表现为「后端只见 GET /api/projects，之后彻底安静」。
// 浏览器控制台报错因此是最有价值的现场，必须收着。
const pageSignals = [];

function attachDiagnostics(page) {
  page.on("pageerror", (err) => {
    pageSignals.push(`pageerror: ${err && err.message ? err.message : String(err)}`);
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") pageSignals.push(`console.error: ${msg.text()}`);
  });
  page.on("requestfailed", (req) => {
    pageSignals.push(`requestfailed: ${req.url()} ${(req.failure() || {}).errorText || ""}`);
  });
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

// 失败时把「现场」打出来：列表里到底有几项、有没有目标项、表单处于什么模式、
// 页面侧有没有报错。这些能把「列表为空」与「列表有项但没渲染到」直接分开，
// 省掉一轮靠猜的排查。
async function dumpScene(page, id) {
  const dom = await page.evaluate((pid) => {
    const items = [...document.querySelectorAll("#projectList .project-item")];
    return {
      itemCount: items.length,
      itemIds: items.map((n) => n.getAttribute("data-project-id")),
      active: document.querySelector(".project-item.active")?.getAttribute("data-project-id") || null,
      hasTarget: Boolean(document.querySelector(`#projectList .project-item[data-project-id="${pid}"]`)),
      targetVisible: (() => {
        const node = document.querySelector(`#projectList .project-item[data-project-id="${pid}"]`);
        if (!node) return null;
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      })(),
      formMode: document.querySelector("#projectForm")?.className || null,
      summaryVisible: !document.querySelector("#projectSummary")?.classList.contains("hidden"),
      listText: (document.querySelector("#projectList")?.textContent || "").trim().slice(0, 100),
    };
  }, id);
  dom.pageSignals = pageSignals.slice(-6);
  return dom;
}

async function selectProject(page, id) {
  // 与姊妹实现（p6c_real_assembly.cjs / local_keyframe_ui.cjs）对齐：先等通用列表项
  // 出现，再等具体项目（本文件原先直接等具体项目、窗口也只有 10000，是工具集里的孤例）。
  // 为什么给到 30s：应用的首次渲染发生在 init() 末尾，而 init() 里那次
  // `GET /api/projects/{id}` 会对**每个就绪镜头起一次 ffprobe**（冷读 ≈ 镜头数秒，
  // 见 tools/test_project_read_budget.py 的「无缓存时约 shot_count 秒」）。机器一忙，
  // 这段冷读可达十几秒，原先 8s/10s 的窗口会把「机器慢」误判成「功能坏」——
  // 2026-10-01 全量 C 轮即如此（后端日志停在 GET /api/projects，随即安静）。
  // 这里等的是**就绪**，不是性能：性能由那个按 ffprobe 次数计量的预算用例守着，
  // 与机器快慢无关，所以放宽窗口不会掩盖真回归。
  await clearUnsaved(page);
  try {
    await page.waitForSelector("#projectList .project-item", { timeout: 30000 });
    await page.waitForSelector(`#projectList .project-item[data-project-id="${id}"]`, { timeout: 30000 });
  } catch (err) {
    const dump = await dumpScene(page, id);
    throw new Error(
      `等待项目卡片 ${id} 出现超时。列表项数=${dump.itemCount}` +
        ` 列表 id=[${dump.itemIds.join(",")}] 表单模式=${dump.formMode}` +
        ` 摘要可见=${dump.summaryVisible} 列表文本="${dump.listText}"` +
        ` 页面信号=[${dump.pageSignals.join(" | ")}]`
    );
  }
  await clearUnsaved(page);
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    await page.locator(`#projectList .project-item[data-project-id="${id}"]`).click();
    await clearUnsaved(page);
    try {
      await page.waitForFunction(
        (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
        id,
        { timeout: 30000 }
      );
      return;
    } catch (err) {
      if (attempt === 2) {
        const dump = await dumpScene(page, id);
        throw new Error(
          `选择项目 ${id} 后 .active 未生效（2 次尝试各 30s）。` +
            `实际 active=${dump.active} 列表=[${dump.itemIds.join(",")}]` +
            ` 页面信号=[${dump.pageSignals.join(" | ")}]`
        );
      }
      console.log(`RETRY: 第 ${attempt} 次选择未落地，重试一次`);
    }
  }
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
  attachDiagnostics(page);
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
