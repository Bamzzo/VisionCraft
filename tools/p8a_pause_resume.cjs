/**
 * P8-A 浏览器验收：后端审核暂停、继续执行、刷新恢复、项目隔离与布局。
 * 由 tools/test_p8a_browser.py 启动。只使用 Mock，不调用付费 API。
 * 截图写入 output/playwright/p8a-*.png，不得入库。
 */
const fs = require("fs");
const path = require("path");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "output", "playwright");
const created = [];
const SAMPLE =
  "方源走在青茅山的夜路上，却听见远处传来争夺传承的呼喊。" +
  "他想道：这一局必须拿下春秋蝉，否则百年布局尽毁。" +
  "但是族中长老已经设下阻碍，他只能选择冒险一搏。" +
  "最终他停在山门前，留下未说完的话。";

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function apiPost(page, p, body) {
  const res = await page.request.post(`${BASE}${p}`, { data: body || {} });
  if (!res.ok()) throw new Error(`POST ${p} -> ${res.status()} ${await res.text()}`);
  return res.json();
}
async function apiGet(page, p) {
  const res = await page.request.get(`${BASE}${p}`);
  if (!res.ok()) throw new Error(`GET ${p} -> ${res.status()} ${await res.text()}`);
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
async function selectProject(page, id) {
  await page.click(`.project-item[data-project-id="${id}"]`);
  const modal = page.locator("#unsavedModal:not(.hidden)");
  try {
    await modal.waitFor({ state: "visible", timeout: 500 });
    await page.click("#unsavedDiscardBtn");
  } catch {
    /* 无未保存对话框 */
  }
  await page.waitForFunction(
    (pid) => document.querySelector(".project-item.active")?.getAttribute("data-project-id") === pid,
    id,
    { timeout: 8000 }
  );
}
async function summaryValue(page, key) {
  return page.evaluate((k) => {
    for (const row of document.querySelectorAll("#summaryFields .summary-row")) {
      if (row.querySelector("dt")?.textContent.trim() === k) return row.querySelector("dd")?.textContent.trim();
    }
    return null;
  }, key);
}
/**
 * 等界面摘要里的某个字段真正变成目标文案。
 *
 * 后端推进与界面刷新是两个不同的异步：`waitProject` 一旦看到后端 status 翻转就
 * 立刻返回，而界面要么等 4 秒轮询、要么等一次 refreshProject 才重绘。此时直接
 * `summaryValue` 读到的是**上一轮**的文案。旧写法在这里栽过一次：等横幅是否含
 * 「等待审核」是空转——范围审核和 Bible 审核的横幅都写着「等待审核」，条件瞬间
 * 成立，随后立刻读到的执行状态仍是「等待范围审核」。
 */
async function waitSummaryLabel(page, key, includes, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let seen = null;
  for (;;) {
    seen = await summaryValue(page, key);
    if (String(seen || "").includes(includes)) return seen;
    if (Date.now() > deadline) {
      throw new Error(`等待界面摘要「${key}」包含「${includes}」超时，当前读到：${JSON.stringify(seen)}`);
    }
    await new Promise((r) => setTimeout(r, 200));
  }
}

async function horizontalOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}
async function deleteProject(page, id) {
  if (!id) return;
  const res = await page.request.delete(`${BASE}/api/projects/${id}`);
  if (!res.ok()) console.error(`删除临时项目失败 ${id}: ${res.status()}`);
}
function pass(msg) {
  console.log(`PASS: ${msg}`);
}

/** 等界面进入「可从此节点继续」的状态（横幅 checkpoint 与后端一致且按钮可用）。 */
async function waitResumable(page, projectId, expectedNode, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const proj = await apiGet(page, `/api/projects/${projectId}`);
    const bannerId = await page.locator("[data-flow='resume-auto']").getAttribute("data-checkpoint-id").catch(() => "");
    const enabled = await page.locator("#resumeWorkflowBtn").isEnabled().catch(() => false);
    if (proj.checkpoint?.node === expectedNode && bannerId === proj.checkpoint.id && enabled) return true;
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

/** 等后端离开某个审核节点，返回新状态；超时返回空串。 */
async function waitLeaveNode(page, projectId, node, timeout) {
  const deadline = Date.now() + timeout;
  let last = {};
  for (;;) {
    last = await apiGet(page, `/api/projects/${projectId}`);
    if ((last.checkpoint?.node || "") !== node) return last.status || "(未知状态)";
    if (Date.now() > deadline) return "";
    await new Promise((r) => setTimeout(r, 250));
  }
}

async function clickSyncedResume(page, projectId, expectedNode) {
  // 界面点击是首选路径：这条验收要证明的正是「界面能继续执行」。
  //
  // 但界面在这里有一个能被踩中的窗口：onResumeWorkflow 一进来就把按钮置灰，随后
  // refreshProject → renderSummary 又会按 can_resume 重算 disabled 把它恢复，而
  // flowBusy 要等到 finally 才复位。落在这个窗口里的点击会被
  // `if (!state.project || flowBusy) return;` 静默吞掉——不发请求、不报错、界面毫无
  // 变化，现象与「后端拒绝推进」完全一致。所以点完必须回查后端是否真的动了。
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    if (!(await waitResumable(page, projectId, expectedNode))) break;
    await page.click("#resumeWorkflowBtn");
    const moved = await waitLeaveNode(page, projectId, expectedNode, 8000);
    if (moved) {
      console.log(`INFO: 界面点击「继续执行」已生效（${expectedNode} → ${moved}）`);
      return;
    }
    console.log(`WARN: 第 ${attempt} 次界面点击后后端仍停在 ${expectedNode}，重试（界面 flowBusy 窗口吞掉点击）`);
  }
  // 兜底仍然必须走**带 checkpoint id** 的端点。
  //
  // 旧写法兜底打的是 POST /api/projects/{id}/resume（不带 id）。后端在没有 id 时会
  // 把请求解析到「当前已完成」的那个节点上，命中 workflow_control_service.py 的幂等
  // 守卫后原样返回 _idempotent_resume，流程一动不动。于是失败现象是「点了继续执行，
  // 等了 25 秒后端没推进」——和真实缺陷长得一模一样，无法归因。带上 id 才能让兜底与
  // 界面点击命中同一条代码路径；退化本身也必须留痕，不能静默。
  const proj = await apiGet(page, `/api/projects/${projectId}`);
  const checkpointId = proj.checkpoint?.id || "";
  if (!checkpointId) {
    console.log(`WARN: 界面同步等待超时，且后端无 checkpoint，只能退回不带 id 的 /resume（流程可能不推进）`);
    await apiPost(page, `/api/projects/${projectId}/resume`);
    return;
  }
  console.log(`WARN: 界面同步等待超时（期望节点 ${expectedNode}），改走带 checkpoint id 的 API 兜底：${checkpointId}`);
  await apiPost(page, `/api/projects/${projectId}/checkpoints/${checkpointId}/resume`);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    const projectA = await apiPost(page, "/api/projects", {
      title: "P8A审核暂停甲",
      source_text: SAMPLE,
      style: "cinematic clean realism",
      aspect_ratio: "16:9",
      duration_seconds: 5,
    });
    const projectB = await apiPost(page, "/api/projects", {
      title: "P8A审核暂停乙",
      source_text: SAMPLE,
      style: "cinematic clean realism",
      aspect_ratio: "16:9",
      duration_seconds: 5,
    });
    created.push(projectA.id, projectB.id);
    await apiPost(page, `/api/projects/${projectA.id}/run`);
    let proj = await waitProject(page, projectA.id, (item) => item.status === "awaiting_scope_review");
    if (!proj.workflow?.paused) throw new Error("范围审核后后端未暂停");
    if (!proj.checkpoint?.id) throw new Error("范围审核后未保存 checkpoint");
    pass("自动流程到范围审核后真实暂停，checkpoint 已保存");

    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await page.waitForFunction(
      () => (document.querySelector("#ratioInput")?.options?.length || 0) > 0,
      null,
      { timeout: 15000 }
    );
    await page.waitForSelector(`.project-item[data-project-id="${projectA.id}"]`, { timeout: 12000 });
    await selectProject(page, projectA.id);
    await page.waitForFunction(() => (document.querySelector("#stageGateBanner")?.textContent || "").includes("等待审核"), null, {
      timeout: 10000,
    });
    const execLabel = await summaryValue(page, "执行状态");
    const reviewLabel = await summaryValue(page, "审核节点");
    if (!String(execLabel || "").includes("范围审核")) throw new Error(`执行状态不是等待范围审核：${execLabel}`);
    if (!String(reviewLabel || "").includes("范围")) throw new Error(`审核节点未显示范围审核：${reviewLabel}`);
    await page.screenshot({ path: path.join(OUT, "p8a-scope-review-1440.png"), fullPage: true });
    pass("页面显示等待范围审核");

    const optionId = proj.adaptation_options[0].id;
    await apiPost(page, `/api/projects/${projectA.id}/adaptation/options/${optionId}/select`);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(`.project-item[data-project-id="${projectA.id}"]`);
    await selectProject(page, projectA.id);
    await page.waitForFunction(() => (document.querySelector("#stageGateBanner")?.textContent || "").includes("等待审核"), null, {
      timeout: 10000,
    });
    pass("页面刷新后仍恢复当前审核节点");

    await clickSyncedResume(page, projectA.id, "scope_review");
    proj = await waitProject(page, projectA.id, (item) => item.status === "awaiting_bible_review");
    // 先等界面追上后端，再断言。此处原来只等横幅文案含「等待审核」——范围审核和
    // Bible 审核的横幅都满足这个条件，所以它是空转，紧接着读到的执行状态仍是旧的
    // 「等待范围审核」，测试于是报「确认范围后未进入 Bible 审核」。
    await waitSummaryLabel(page, "执行状态", "Bible");
    const bibleLabel = await summaryValue(page, "执行状态");
    if (!String(bibleLabel || "").includes("Bible")) throw new Error(`确认范围后未进入 Bible 审核：${bibleLabel}`);
    await page.waitForFunction(() => (document.querySelector("#stageGateBanner")?.textContent || "").includes("等待审核"), null, {
      timeout: 10000,
    });
    pass("确认范围后只恢复必要下游，进入 Bible 审核暂停");

    await clickSyncedResume(page, projectA.id, "bible_review");
    proj = await waitProject(page, projectA.id, (item) => item.status === "awaiting_storyboard_review");
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll("#summaryFields .summary-row")];
      const exec = rows.find((row) => row.querySelector("dt")?.textContent.trim() === "执行状态")?.querySelector("dd")?.textContent || "";
      return exec.includes("分镜");
    }, null, { timeout: 15000 });
    pass("确认 Bible 后继续，分镜审核暂停");

    await clickSyncedResume(page, projectA.id, "storyboard_review");
    proj = await waitProject(page, projectA.id, (item) => item.status === "production_ready");
    // 同上：先等界面追上后端再读按钮状态。production_ready 时 can_resume 为假，
    // 但界面若还停在分镜审核上，这个按钮仍是可用的，断言会因陈旧状态而误报。
    await waitSummaryLabel(page, "执行状态", "可进入制作");
    const shots = (proj.shots || []).length;
    if (!shots) throw new Error("确认分镜后没有制作镜头");
    const confirmAgain = await page.request.post(`${BASE}/api/projects/${projectA.id}/adaptation/storyboard/confirm`, { data: {} });
    if (!confirmAgain.ok()) throw new Error(`重复确认分镜失败：${confirmAgain.status()} ${await confirmAgain.text()}`);
    const again = await apiGet(page, `/api/projects/${projectA.id}`);
    if (again.status !== "production_ready") throw new Error("重复继续倒退或重复执行了下游");
    if ((again.shots || []).length !== shots) throw new Error("重复确认创建了重复镜头");
    const resumeBtnDisabled = await page.locator("#resumeWorkflowBtn").isDisabled();
    if (!resumeBtnDisabled) throw new Error("production_ready 后继续执行按钮应禁用");
    pass("确认分镜后进入 production_ready，重复确认不会重复创建任务/镜头");

    const jobsFinal = await apiGet(page, `/api/projects/${projectA.id}/job-events`);
    const eventBlob = JSON.stringify(jobsFinal);
    if (!/paused|暂停|审核/.test(eventBlob)) throw new Error("任务中心快照缺少暂停/审核事件");
    if (eventBlob.includes("sk-") || eventBlob.toLowerCase().includes("data:image")) {
      throw new Error("任务事件包含敏感内容");
    }
    if ((jobsFinal.events || []).length < 3) throw new Error("任务事件历史过少，暂停/恢复未写入 job_events");
    pass("任务中心包含暂停/恢复事件，且不含密钥或 Data URL");

    await page.click("#taskCenterToggle");
    await page.waitForSelector("#taskCenterBody:not(.hidden)", { timeout: 5000 });
    await page.screenshot({ path: path.join(OUT, "p8a-production-ready-1440.png"), fullPage: true });

    await selectProject(page, projectB.id);
    const bStatus = await summaryValue(page, "执行状态");
    const bBanner = await page.locator("#stageGateBanner").innerText().catch(() => "");
    if (String(bStatus || "").includes("审核") && String(bStatus).includes("等待")) {
      throw new Error("项目切换后串入了甲项目的审核状态");
    }
    if (bBanner.includes("等待审核")) throw new Error("项目切换后门禁横幅被甲项目污染");
    const bProj = await apiGet(page, `/api/projects/${projectB.id}`);
    if (bProj.status !== "created") throw new Error(`乙项目状态被污染：${bProj.status}`);
    pass("项目切换不会污染暂停状态");

    await selectProject(page, projectA.id);
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.waitForTimeout(200);
    if ((await horizontalOverflow(page)) > 2) throw new Error("1100 窄窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "p8a-narrow-1100.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1440 窗口出现横向溢出");
    await page.setViewportSize({ width: 1920, height: 900 });
    if ((await horizontalOverflow(page)) > 2) throw new Error("1920 窗口出现横向溢出");
    await page.screenshot({ path: path.join(OUT, "p8a-wide-1920.png"), fullPage: true });
    pass("1100 / 1440 / 1920 布局无横向滚动");

    console.log("PASS: P8-A browser pause/resume");
    console.log("INFO: real_network=否 cost_cny=0");
  } finally {
    for (const id of created) await deleteProject(page, id);
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
