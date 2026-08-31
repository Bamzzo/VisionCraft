/**
 * P7-C 浏览器证据：阶段状态 DOM 断言 + 截图 SHA-256。
 * 由 tools/test_p7c_ui_state_browser.py 启动本地 Mock 后端后调用。不发送真实 API。
 *
 * 产物（gitignored）：
 *   output/playwright/p7c-ui-state/browser_evidence.json
 *   output/playwright/p7c-ui-state/browser_dom_snapshots.json
 *   output/playwright/p7c-ui-state/browser_screenshot_hashes.json
 *   output/playwright/p7c-ui-state/*.png
 */
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright", "p7c-ui-state");
const IDS = JSON.parse(process.env.P7C_IDS || "{}");
const STAGE_IDS = ["text", "storyline", "bible", "storyboard", "keyframes", "video", "assembly", "export"];
const STAGE_LABELS = {
  text: "文本理解",
  storyline: "故事线选择",
  bible: "Story Bible",
  storyboard: "分镜设计",
  keyframes: "关键帧",
  video: "镜头视频",
  assembly: "成片合成",
  export: "导出与交付",
};

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

function sha256file(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
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
    // 无未保存守卫
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
    // 无未保存对话框
  }
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-stage-id="${sid}"]`)?.getAttribute("aria-current") === "true",
    stageId,
    { timeout: 8000 }
  );
}

async function readNav(page) {
  return page.evaluate((ids) => {
    const nodes = [...document.querySelectorAll("#stageNav [data-stage-id]")];
    return nodes.map((node) => ({
      id: node.getAttribute("data-stage-id"),
      label: node.querySelector(".stage-node-label")?.textContent.trim() || "",
      state: node.querySelector(".stage-state-label")?.textContent.trim() || "",
      mark: node.querySelector(".stage-state-mark")?.textContent.trim() || "",
      summary: node.querySelector(".stage-node-summary")?.textContent.trim() || "",
      access: node.querySelector(".stage-node-access")?.textContent.trim() || "",
      count: node.querySelector(".stage-node-count")?.textContent.trim() || "",
      hint: node.querySelector(".stage-node-hint")?.textContent.trim() || "",
      aria: node.getAttribute("aria-label") || "",
      viewable: node.getAttribute("data-viewable"),
      canExecute: node.getAttribute("data-can-execute"),
      executing: node.getAttribute("data-executing"),
      current: node.getAttribute("data-current"),
      viewing: node.getAttribute("aria-current"),
    })).filter((item) => ids.includes(item.id));
  }, STAGE_IDS);
}

async function snapshotDom(page) {
  return page.evaluate(() => ({
    projectId: document.querySelector(".project-item.active")?.getAttribute("data-project-id") || "",
    workspaceTitle: document.querySelector("#stageWorkspaceTitle")?.textContent.trim() || "",
    workspaceSubtitle: document.querySelector("#stageWorkspaceSubtitle")?.textContent.trim() || "",
    executionStage: document.querySelector("#stageNav")?.getAttribute("data-execution-stage") || "",
    summary: document.querySelector("#summaryFields")?.innerText || "",
    workspaceText: (document.querySelector("#stageWorkspace")?.innerText || "").slice(0, 1200),
    jobText: document.querySelector("#jobMessage")?.textContent.trim() || "",
    buttons: [...document.querySelectorAll("#stageWorkspace button, #stageWorkspace a.secondary-btn, #stageWorkspace a.primary-btn")]
      .slice(0, 12)
      .map((node) => (node.textContent || "").trim())
      .filter(Boolean),
  }));
}

async function waitDom(page, pred, timeout = 15000) {
  await page.waitForFunction(pred, null, { timeout });
}

async function assertNoOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 1) throw new Error(`出现横向溢出 ${overflow}px`);
}

async function captureStep(page, ctx, spec) {
  await selectProject(page, spec.projectId);
  await openStage(page, spec.viewStage);
  await waitDom(page, spec.wait);
  const nav = await readNav(page);
  const snap = await snapshotDom(page);
  if (nav.length !== 8) throw new Error(`${spec.id}: 右侧阶段数不是 8`);
  if (nav.some((item) => !item.state || !item.access || !item.count || !item.hint || !item.aria)) {
    throw new Error(`${spec.id}: 阶段缺少中文状态/可访问性/计数/前置提示`);
  }
  if (nav.map((item) => item.id).join(",") !== STAGE_IDS.join(",")) {
    throw new Error(`${spec.id}: 阶段顺序不正确`);
  }
  if (snap.workspaceTitle !== STAGE_LABELS[spec.viewStage]) {
    throw new Error(`${spec.id}: 工作区标题应为 ${STAGE_LABELS[spec.viewStage]}，实际 ${snap.workspaceTitle}`);
  }
  if (snap.projectId !== spec.projectId) {
    throw new Error(`${spec.id}: 项目 ID 不匹配 ${snap.projectId}`);
  }
  const node = nav.find((item) => item.id === spec.assertStage);
  if (!node) throw new Error(`${spec.id}: 找不到阶段 ${spec.assertStage}`);
  if (spec.state && node.state !== spec.state) {
    throw new Error(`${spec.id}: ${spec.assertStage} 状态应为「${spec.state}」，实际「${node.state}」`);
  }
  if (spec.forbiddenStates) {
    for (const item of spec.forbiddenStates) {
      const target = nav.find((row) => row.id === item.id);
      if (target && target.state === item.state) {
        throw new Error(`${spec.id}: ${item.id} 不得显示「${item.state}」`);
      }
    }
  }
  if (spec.summaryIncludes) {
    const hay = `${node.summary} ${snap.workspaceText} ${snap.workspaceSubtitle}`;
    for (const token of spec.summaryIncludes) {
      if (!hay.includes(token)) throw new Error(`${spec.id}: 缺少文本「${token}」`);
    }
  }
  if (spec.buttonIncludes) {
    for (const token of spec.buttonIncludes) {
      if (!snap.buttons.some((text) => text.includes(token)) && !snap.workspaceText.includes(token)) {
        throw new Error(`${spec.id}: 缺少按钮或关键文案「${token}」`);
      }
    }
  }
  if (spec.canExecute === false && node.canExecute === "true" && spec.assertStage !== spec.viewStage) {
    throw new Error(`${spec.id}: ${spec.assertStage} 不应可执行`);
  }
  const file = path.join(OUT, spec.file);
  await page.screenshot({ path: file, fullPage: true });
  const hash = sha256file(file);
  const prev = ctx.steps[ctx.steps.length - 1];
  if (prev && prev.sha256 === hash && !spec.allowSame) {
    throw new Error(`FAIL: 相邻截图哈希相同 ${prev.id} / ${spec.id} (${hash})`);
  }
  const step = {
    id: spec.id,
    file: spec.file,
    sha256: hash,
    projectId: spec.projectId,
    viewStage: spec.viewStage,
    workspaceTitle: snap.workspaceTitle,
    executionStage: snap.executionStage,
    stageState: node.state,
    jobCount: node.count,
    allowSame: Boolean(spec.allowSame),
    sameAsPreviousReason: spec.allowSame ? spec.sameReason || "同一阶段 UI 无变化" : "",
    assertions: {
      workspaceTitle: snap.workspaceTitle,
      executionStage: snap.executionStage,
      stageState: node.state,
      projectId: snap.projectId,
      buttonCount: snap.buttons.length,
    },
  };
  ctx.steps.push(step);
  ctx.dom[spec.id] = { nav, ...snap, sha256: hash };
  ctx.hashes.files[spec.file] = hash;
  ctx.hashes.ordered.push({ id: spec.id, file: spec.file, sha256: hash, allow_same: Boolean(spec.allowSame) });
  pass(`${spec.id} 截图 ${spec.file} sha256=${hash.slice(0, 12)}`);
  return step;
}

async function main() {
  if (!IDS.created) throw new Error("缺少 P7C_IDS");
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const ctx = { steps: [], dom: {}, hashes: { files: {}, ordered: [] } };
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForSelector("#projectList .project-item", { timeout: 15000 });
    const charset = await page.evaluate(() => document.characterSet);
    if (charset !== "UTF-8") throw new Error(`文档编码不是 UTF-8：${charset}`);

    await captureStep(page, ctx, {
      id: "created",
      file: "01-created.png",
      projectId: IDS.created,
      viewStage: "text",
      assertStage: "keyframes",
      state: "未开始",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("文本理解") &&
        document.querySelector('[data-stage-id="keyframes"] .stage-state-label')?.textContent === "未开始" &&
        document.querySelector('[data-stage-id="keyframes"]')?.getAttribute("data-executing") === "false",
      forbiddenStates: [
        { id: "keyframes", state: "处理中" },
        { id: "video", state: "已失效" },
      ],
      summaryIncludes: ["文本理解"],
    });

    await captureStep(page, ctx, {
      id: "adaptation",
      file: "02-adaptation.png",
      projectId: IDS.adaptation,
      viewStage: "text",
      assertStage: "text",
      state: "等待审核",
      wait: () =>
        (document.querySelector("#stageWorkspace")?.innerText || "").includes("春秋蝉归乡") &&
        document.querySelector('[data-stage-id="text"] .stage-state-label')?.textContent === "等待审核",
      buttonIncludes: ["确认范围"],
      summaryIncludes: ["等待审核"],
    });

    await captureStep(page, ctx, {
      id: "story_bible",
      file: "03-story-bible.png",
      projectId: IDS.story_bible,
      viewStage: "bible",
      assertStage: "bible",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("Story Bible") &&
        (document.querySelector("#stageWorkspace")?.innerText || "").includes("Story Bible"),
      buttonIncludes: ["确认 Story Bible"],
      summaryIncludes: ["Story Bible"],
    });

    await captureStep(page, ctx, {
      id: "storyboard",
      file: "04-storyboard.png",
      projectId: IDS.storyboard,
      viewStage: "storyboard",
      assertStage: "storyboard",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("分镜设计") &&
        (document.querySelector("#stageWorkspace")?.innerText || "").includes("分镜"),
      summaryIncludes: ["分镜"],
    });

    await captureStep(page, ctx, {
      id: "first_frame",
      file: "05-first-frame.png",
      projectId: IDS.first_frame,
      viewStage: "keyframes",
      assertStage: "keyframes",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("关键帧") &&
        document.querySelector('[data-stage-id="keyframes"] .stage-state-label')?.textContent !== "处理中",
      forbiddenStates: [{ id: "keyframes", state: "处理中" }],
      summaryIncludes: ["关键帧"],
    });

    await captureStep(page, ctx, {
      id: "vision_review",
      file: "06-vision-review.png",
      projectId: IDS.vision_review,
      viewStage: "keyframes",
      assertStage: "keyframes",
      wait: () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("最近视觉检查"),
      summaryIncludes: ["最近视觉检查", "未调用远程视觉模型"],
    });

    await captureStep(page, ctx, {
      id: "video_partial",
      file: "07-video-partial.png",
      projectId: IDS.video_partial,
      viewStage: "video",
      assertStage: "video",
      state: "处理中",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("镜头视频") &&
        (document.querySelector('[data-stage-id="video"] .stage-node-summary')?.textContent || "").includes("2/5"),
      summaryIncludes: ["2/5"],
    });

    await captureStep(page, ctx, {
      id: "video_complete",
      file: "08-video-complete.png",
      projectId: IDS.video_complete,
      viewStage: "video",
      assertStage: "video",
      state: "已完成",
      wait: () =>
        document.querySelector('[data-stage-id="video"] .stage-state-label')?.textContent === "已完成" &&
        (document.querySelector('[data-stage-id="video"] .stage-node-summary')?.textContent || "").includes("5/5"),
      forbiddenStates: [
        { id: "video", state: "已失效" },
        { id: "keyframes", state: "处理中" },
      ],
      summaryIncludes: ["5/5"],
    });

    await captureStep(page, ctx, {
      id: "assembly_running",
      file: "09-assembly-running.png",
      projectId: IDS.assembly_running,
      viewStage: "assembly",
      assertStage: "assembly",
      state: "处理中",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("成片合成") &&
        document.querySelector('[data-stage-id="assembly"] .stage-state-label')?.textContent === "处理中",
      summaryIncludes: ["成片合成"],
    });
    await openStage(page, "export");
    await waitDom(
      page,
      () =>
        (document.querySelector("#exportPanel")?.getAttribute("data-export-state") === "running") &&
        (document.querySelector("#stageWorkspace")?.innerText || "").includes("继续查看合成进度")
    );
    pass("合成中的导出页显示继续查看合成进度，不另建合成按钮");

    await captureStep(page, ctx, {
      id: "assembly_complete",
      file: "10-assembly-complete.png",
      projectId: IDS.assembly_complete,
      viewStage: "assembly",
      assertStage: "assembly",
      state: "已完成",
      wait: () =>
        document.querySelector('[data-stage-id="assembly"] .stage-state-label')?.textContent === "已完成" &&
        document.querySelector('[data-stage-id="export"] .stage-state-label')?.textContent === "已完成",
      forbiddenStates: [
        { id: "keyframes", state: "处理中" },
        { id: "video", state: "已失效" },
      ],
    });

    await captureStep(page, ctx, {
      id: "download_ready",
      file: "11-download-ready.png",
      projectId: IDS.download_ready,
      viewStage: "export",
      assertStage: "export",
      state: "已完成",
      wait: () =>
        document.querySelector("#stageWorkspaceTitle")?.textContent.includes("导出与交付") &&
        (document.querySelector("#stageWorkspace")?.innerText || "").includes("下载当前成片"),
      buttonIncludes: ["下载当前成片", "预览当前成片"],
      forbiddenStates: [
        { id: "keyframes", state: "处理中" },
        { id: "video", state: "已失效" },
      ],
    });

    const execBefore = await page.evaluate(() => document.querySelector("#stageNav")?.getAttribute("data-execution-stage"));
    await openStage(page, "keyframes");
    const execAfterView = await page.evaluate(() => document.querySelector("#stageNav")?.getAttribute("data-execution-stage"));
    if (execBefore !== execAfterView) throw new Error("只读查看关键帧改变了执行阶段");
    pass("只读查看不改变执行状态");

    await selectProject(page, IDS.other);
    await waitDom(
      page,
      () => document.querySelector("#stageNav")?.getAttribute("data-execution-stage") === "text"
    );
    const otherNav = await readNav(page);
    if (otherNav.find((item) => item.id === "video")?.state === "已完成") {
      throw new Error("项目切换后继承了旧项目视频完成态");
    }
    pass("项目切换隔离");

    await selectProject(page, IDS.assembly_complete);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await selectProject(page, IDS.assembly_complete);
    await waitDom(
      page,
      () =>
        document.querySelector('[data-stage-id="export"] .stage-state-label')?.textContent === "已完成" &&
        document.querySelector('[data-stage-id="keyframes"] .stage-state-label')?.textContent !== "处理中"
    );
    pass("刷新后从后端恢复正确阶段状态");

    await selectProject(page, IDS.created);
    await openStage(page, "export");
    await waitDom(page, () => document.querySelector("#stageWorkspaceTitle")?.textContent.includes("导出与交付"));
    if (await page.locator("#assembleProjectBtn").count()) {
      throw new Error("未开始的导出阶段不应出现合成按钮");
    }
    const emptyExport = await page.locator("#stageWorkspace").innerText();
    if (!emptyExport.includes("前往成片合成")) throw new Error("无成片时应给出前往成片合成入口");
    const exportNode = (await readNav(page)).find((item) => item.id === "export");
    if (exportNode.canExecute === "true") throw new Error("未开始的导出阶段不能执行");
    pass("未开始阶段不能执行非法操作");

    if (!IDS.assembly_stale) throw new Error("缺少 assembly_stale 夹具");
    await selectProject(page, IDS.assembly_stale);
    await openStage(page, "export");
    await waitDom(
      page,
      () =>
        document.querySelector("#exportPanel")?.getAttribute("data-export-state") === "stale" &&
        (document.querySelector("#stageWorkspace")?.innerText || "").includes("返回成片合成")
    );
    await page.click("[data-action='goto-assembly']");
    await waitDom(page, () => (document.querySelector("#stageWorkspaceTitle")?.textContent || "").includes("成片合成"));
    if (!(await page.locator("#assembleProjectBtn").count())) {
      throw new Error("过期成片跳转后应回到成片合成页");
    }
    pass("过期成片可从导出页返回成片合成");

    for (const width of [1100, 1440, 1920]) {
      const height = width === 1920 ? 1080 : 900;
      await page.setViewportSize({ width, height });
      await selectProject(page, IDS.video_complete);
      await openStage(page, "video");
      await waitDom(page, () => document.querySelector("#stageWorkspaceTitle")?.textContent.includes("镜头视频"));
      await assertNoOverflow(page);
      const file = path.join(OUT, `viewport-${width}.png`);
      await page.screenshot({ path: file, fullPage: true });
      ctx.hashes.files[`viewport-${width}.png`] = sha256file(file);
      pass(`${width}px 无横向溢出`);
    }

    const garbled = await page.evaluate(() => {
      const text = `${document.body.innerText} ${document.querySelector("#stageWorkspace")?.innerText || ""}`;
      return text.includes("\uFFFD") || text.includes("锟") || text.includes("烫烫");
    });
    if (garbled) throw new Error("页面出现乱码替换符");
    const longBits = await page.evaluate(() => document.body.innerText);
    if (!longBits.includes("超长中文标题") && !longBits.includes("超长模型")) {
      // 当前项目是五镜完成项，模型名应在镜头卡片中
      await selectProject(page, IDS.created);
      await waitDom(page, () => (document.body.innerText || "").includes("超长中文标题"));
    }
    pass("长中文与长模型名可见且未乱码");

    const evidence = {
      phase: "P7-C",
      live_network: false,
      cost_cny: 0,
      generated_at: new Date().toISOString(),
      output_dir: "output/playwright/p7c-ui-state",
      steps: ctx.steps,
      notes: [
        "每个截图前等待对应 DOM 条件，不使用固定 sleep 作为唯一同步。",
        "相邻截图 SHA-256 相同且未声明 allow_same 时立即 FAIL。",
        "本阶段未点击视觉检查按钮，未调用真实图片/视频/语音/音乐 API。",
        "未修改 output/playwright/live-multishot/ 历史截图。",
      ],
    };
    fs.writeFileSync(path.join(OUT, "browser_evidence.json"), JSON.stringify(evidence, null, 2), "utf8");
    fs.writeFileSync(path.join(OUT, "browser_dom_snapshots.json"), JSON.stringify(ctx.dom, null, 2), "utf8");
    fs.writeFileSync(path.join(OUT, "browser_screenshot_hashes.json"), JSON.stringify(ctx.hashes, null, 2), "utf8");
    pass("已写入 browser_evidence.json / browser_dom_snapshots.json / browser_screenshot_hashes.json");
    console.log("INFO: live_network=否");
    console.log("INFO: cost_cny=0");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
