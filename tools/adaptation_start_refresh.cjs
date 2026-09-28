/**
 * 浏览器验收：启动改编后不整页刷新也应出现候选方案（适配 UI-0～UI-1 工作台布局）。
 * 由 tools/test_adaptation_start_refresh.py 通过 npx playwright 调用。
 *
 * 新布局要点：
 * - 查看已有项目时新建表单默认隐藏，需先点击「新建项目」展开空白表单；
 * - 改编方案 / 故事线内容渲染在中间「阶段工作区」#stageWorkspace，
 *   需先点击右侧阶段导航 [data-stage-id] 切换 viewStage 才会显示；
 * - 文本规模说明（短文本/中等文本）显示在左侧项目摘要 #summaryFields。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;
const { openCreateForm, fillProjectForm } = require("./ui_project_form.cjs");

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");

function shortText() {
  return (
    "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。" +
    "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
    "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
    "最终他停在山门前，留下未说完的话。"
  );
}

function mediumText() {
  const unit = shortText();
  let text = "";
  let i = 0;
  while (text.length < 2200) {
    i += 1;
    text += `第${i}段。${unit}`;
  }
  return text;
}

// 长文本必须**带真实章节标记**：只有标记才验证得了"按章选范围"这条链路，
// 用无标记文本会退化成硬切分章，测不到章节树本身。
const LONG_SECTIONS = 12;

function longText() {
  const unit = shortText();
  let text = "";
  for (let section = 1; section <= LONG_SECTIONS; section += 1) {
    let body = "";
    while (body.length < 1100) body += unit;
    text += `第${section}节：第${section}节标题\n${body}\n`;
  }
  return text;
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function createProject(page, title, text) {
  // 查看已有项目时表单处于摘要态，需要先进入空白表单。原来这里是「点一次新建 +
  // 一次性等表单可见（5s）」——正好落在 app.js 异步 init 收尾把表单模式重置回摘要态的
  // 窗口里就会超时（实测在 run-20260920-153453 复现）。改走共享的稳定等待。
  await openCreateForm(page);
  await fillProjectForm(page, { "#titleInput": title, "#sourceTextInput": text });
  await page.click("#submitProjectBtn");
  await page.waitForFunction(
    (expected) => {
      const active = document.querySelector(".project-item.active strong");
      return active && active.textContent.includes(expected);
    },
    title,
    { timeout: 10000 }
  );
}

async function currentProjectId(page) {
  return page.locator(".project-item.active").getAttribute("data-project-id");
}

/**
 * 等「阶段工作区」停止重渲染。
 *
 * 为什么需要：改编流程跑完前后，后台任务事件会把 `#stageWorkspace` 整块重绘
 * （实测一次长文本分析期间数到 **13 次**）。勾选框的勾选状态只存在于 DOM 里，
 * 重绘会把它抹掉——于是"勾 3 节却存下 2 节"这种失败看起来像产品缺陷，
 * 其实是**用例撞上了重绘窗口**。这里不是在掩盖问题：重绘期间的丢勾选是已知的
 * 界面隐患（未保存的勾选不跨重绘），已单列在文档里；这里只是让用例不再依赖时序。
 */
async function waitForWorkspaceQuiet(page, quietMs = 1500) {
  await page.evaluate(() => {
    const host = document.querySelector("#stageWorkspace");
    if (window.__wsObserver) window.__wsObserver.disconnect();
    window.__wsObservedFrom = Date.now();
    window.__wsLastMutation = 0;
    window.__wsObserver = new MutationObserver(() => {
      window.__wsLastMutation = Date.now();
    });
    window.__wsObserver.observe(host, { childList: true, subtree: true });
  });
  await page.waitForFunction(
    (quiet) => {
      // 注意两种都算"静止"：观察期内没有发生过变化（last === 0，界面本来就稳），
      // 或最后一次变化已经过去 quiet 毫秒。写成"必须观察到过变化"会在本来就没有重绘时
      // 永远等不到——本用例第一版就是这么在驱动器里超时的（手跑时有任务事件掩盖了它）。
      const last = window.__wsLastMutation || 0;
      const baseline = last || window.__wsObservedFrom || Date.now();
      return Date.now() - baseline > quiet;
    },
    quietMs,
    { timeout: 30000 }
  );
}

async function deleteProject(page, projectId) {
  if (!projectId) return;
  const response = await page.request.delete(`${BASE}/api/projects/${projectId}`);
  if (!response.ok()) {
    throw new Error(`删除临时项目失败：${response.status()} ${await response.text()}`);
  }
}

async function runWithoutReload(page, title, text) {
  await createProject(page, title, text);
  const projectId = await currentProjectId(page);
  await page.click("#runWorkflowBtn");
  // mock 流程推进极快，瞬时“已入队”消息可能被后续事件覆盖；
  // 以“项目状态离开 created 或任务中心出现活动”作为已启动的可靠信号。
  await page.waitForFunction(
    () => {
      const summary = document.querySelector("#summaryFields")?.innerText || "";
      const msg = document.querySelector("#jobMessage")?.textContent || "";
      const status = document.querySelector("#jobStatus")?.textContent || "";
      const left = summary.length > 0 && !/项目状态\ncreated/.test(summary);
      const busy = status.length > 0 && !/空闲|idle/.test(status);
      return left || busy || msg.includes("已入队") || msg.includes("改编") || msg.includes("排队") || msg.includes("就绪");
    },
    null,
    { timeout: 15000 }
  );
  return projectId;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage();
  const created = [];
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");

    // ---- 短文本：直接改编，出现候选方案 ----
    const shortTitle = `ui-short-${Date.now()}`;
    const shortId = await runWithoutReload(page, shortTitle, shortText());
    created.push(shortId);
    // 切换到「改编方案」查看阶段，等待候选方案在不刷新页面的情况下出现。
    await page.click('[data-stage-id="text"]');
    await page.waitForFunction(
      () => {
        const ws = document.querySelector("#stageWorkspace")?.innerText || "";
        return ws.includes("选择此方案") && ws.includes("确认范围并生成 Story Bible");
      },
      null,
      { timeout: 15000 }
    );
    const summary = await page.locator("#summaryFields").innerText();
    if (!summary.includes("直接改编")) throw new Error("短文本未显示规模说明");
    await page.screenshot({ path: path.join(OUT, "short-after-run.png"), fullPage: true });
    console.log("PASS: 短文本启动后未刷新即出现候选方案");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");
    await page.click('[data-stage-id="text"]');
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("选择此方案"),
      null,
      { timeout: 10000 }
    );
    await page.screenshot({ path: path.join(OUT, "short-after-reload.png"), fullPage: true });
    console.log("PASS: 刷新后短文本候选方案与审核状态仍在");

    // ---- 中等文本：先选择故事线 ----
    const mediumTitle = `ui-medium-${Date.now()}`;
    const mediumId = await runWithoutReload(page, mediumTitle, mediumText());
    created.push(mediumId);
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => {
        const ws = document.querySelector("#stageWorkspace")?.innerText || "";
        return ws.includes("选择此故事线");
      },
      null,
      { timeout: 15000 }
    );
    const mediumSummary = await page.locator("#summaryFields").innerText();
    if (!mediumSummary.includes("先选择故事线")) throw new Error("中等文本未显示规模说明");
    await page.screenshot({ path: path.join(OUT, "medium-after-run.png"), fullPage: true });
    console.log("PASS: 中等文本启动后未刷新即出现故事线选择");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("选择此故事线"),
      null,
      { timeout: 10000 }
    );
    console.log("PASS: 刷新后中等文本故事线仍在");

    // ---- 长文本：章节树 + 按章选范围 ----
    const longSource = longText();
    const longTitle = `ui-long-${Date.now()}`;
    const longId = await runWithoutReload(page, longTitle, longSource);
    created.push(longId);
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("章节树"),
      null,
      { timeout: 20000 }
    );
    const boxes = page.locator("[data-chapter-check]");
    await waitForWorkspaceQuiet(page);
    const boxCount = await boxes.count();
    if (boxCount !== LONG_SECTIONS) {
      throw new Error(`长文本应渲染 ${LONG_SECTIONS} 个章节勾选框，实际 ${boxCount}`);
    }
    const longSummary = await page.locator("#summaryFields").innerText();
    if (!longSummary.includes("按章节选择改编范围")) throw new Error("长文本未显示按章节的规模说明");
    console.log("PASS: 长文本启动后未刷新即出现章节树与逐章勾选框");

    // 只勾中间三节：从第 1 节开始会让"范围"恰好等于前缀，测不出越界。
    const picked = [4, 5, 6];
    for (const index of picked) await boxes.nth(index).check();
    const checkedBeforeSave = await page.locator("[data-chapter-check]:checked").count();
    if (checkedBeforeSave !== picked.length) {
      throw new Error(`点完勾选后应勾着 ${picked.length} 节，实际 ${checkedBeforeSave}（界面重绘抹掉了勾选？）`);
    }
    await page.click('[data-adapt="save-medium-scope"]');
    await page.waitForFunction(
      () => (document.querySelector("#stageWorkspace")?.innerText || "").includes("系统将把以下选中范围交给后续改编"),
      null,
      { timeout: 20000 }
    );
    const previewText = await page.locator("#stageWorkspace").innerText();
    // 只从「交给后续改编」那句往后取数：章节树头部也写着「共 N 字」（那是全文），
    // 直接全文匹配会抓到全文长度，把"范围没生效"误判成通过的反面——口径错了，
    // 报出来的永远是错的数。见 PITFALLS「计量点选错」。
    const previewSlice = previewText.slice(previewText.indexOf("系统将把以下选中范围交给后续改编"));
    const matched = previewSlice.match(/共\s*([\d,]+)\s*字/);
    if (!matched) throw new Error("范围预览里没有可读的字数");
    const scopedChars = Number(matched[1].replace(/,/g, ""));
    if (!(scopedChars > 0 && scopedChars < longSource.length * 0.5)) {
      throw new Error(`只勾 3 节却带进 ${scopedChars} 字（全文 ${longSource.length} 字）`);
    }
    await page.screenshot({ path: path.join(OUT, "long-chapters.png"), fullPage: true });
    console.log(`PASS: 长文本按章选范围后 scope 为 ${scopedChars} 字（全文 ${longSource.length} 字）`);

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#runWorkflowBtn");
    await page.click('[data-stage-id="storyline"]');
    await page.waitForFunction(
      () => document.querySelectorAll("[data-chapter-check]").length > 0,
      null,
      { timeout: 15000 }
    );
    const stillChecked = await page.locator("[data-chapter-check]:checked").count();
    if (stillChecked !== picked.length) {
      throw new Error(`刷新后应仍勾着 ${picked.length} 节，实际 ${stillChecked}`);
    }
    console.log("PASS: 刷新后长文本章节范围仍保留");
  } finally {
    for (const id of created.filter(Boolean)) {
      try {
        await deleteProject(page, id);
        console.log(`CLEANED: ${id}`);
      } catch (error) {
        console.error(error);
      }
    }
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
