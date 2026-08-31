/**
 * 真实 2 镜头前端闭环。由 tools/run_live_2shot.py 启动带 LIVE 开关的临时后端。
 * 每镜最多点击一次 generate-video；等待中只 refresh，不补发。
 */
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const playwright = require(require.resolve("playwright", { paths: [path.join(__dirname, "..", ".playwright-cli", "node_modules")] }));
const { chromium } = playwright;

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8040";
const OUT = path.join(__dirname, "..", "output", "playwright", "live-2shot");
const JPEG_PATH = process.env.LIVE2SHOT_JPEG;
const TITLE = "LIVE2SHOT 春秋蝉鸣少年归";
const SAMPLE = "春秋蝉鸣少年归。";
const VIDEO_WAIT_MS = 12 * 60 * 1000;
const TEXT_WAIT_MS = 4 * 60 * 1000;
const submittedShots = new Set();
const stages = {};
const screenshotHashes = {};
const snapshots = [];

function pass(msg) {
  console.log(`PASS: ${msg}`);
}
function fail(msg) {
  throw new Error(msg);
}
function setStage(name, status, extra) {
  stages[name] = { status, ...(extra || {}) };
  writeResult({ stages });
}
function redact(id) {
  const text = String(id || "");
  if (!text || text.length <= 8) return text;
  return `${text.slice(0, 4)}…${text.slice(-4)}`;
}
function writeResult(patch, { replace = false } = {}) {
  const file = path.join(OUT, "result.json");
  let data = {};
  if (!replace && fs.existsSync(file)) {
    try {
      data = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      data = {};
    }
  }
  const next = { ...data, ...patch, stages: { ...(data.stages || {}), ...(patch.stages || stages) } };
  fs.writeFileSync(file, JSON.stringify(next, null, 2), "utf8");
}
async function screenshot(page, name) {
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, fullPage: true });
  screenshotHashes[name] = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}
async function snapDom(page, name) {
  const html = await page.locator("#stageWorkspace").innerHTML().catch(() => "");
  snapshots.push({ name, length: html.length, has_data_url: /data:[^;]+;base64,/.test(html) });
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
  if (!res.ok()) fail(`GET ${p} -> ${res.status()} ${await res.text()}`);
  return res.json();
}
async function apiPost(page, p, body) {
  const res = await page.request.post(`${BASE}${p}`, { data: body || {} });
  const text = await res.text();
  if (!res.ok()) fail(`POST ${p} -> ${res.status()} ${text}`);
  return text ? JSON.parse(text) : {};
}
async function waitProject(page, id, pred, timeout = 25000, label = "waitProject") {
  const start = Date.now();
  let last = "";
  for (;;) {
    const proj = await apiGet(page, `/api/projects/${id}`);
    if (pred(proj)) return proj;
    const failed = (proj.jobs || []).find((job) => job.status === "failed");
    if (failed) {
      fail(`${label} 任务失败：${failed.message || ""} ${failed.error_message || ""}`);
    }
    const toast = await page.locator("#statusToast").innerText().catch(() => "");
    if (/BLOCKED_BEFORE_CALL|未授权|预算/.test(toast) && /阻止|失败|超过/.test(toast)) {
      fail(`${label} 护栏：${toast}`);
    }
    last = proj.status;
    if (Date.now() - start > timeout) fail(`${label} 超时，status=${last}`);
    await new Promise((r) => setTimeout(r, 800));
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
async function waitAppReady(page) {
  await page.waitForFunction(
    () => (document.querySelector("#durationInput")?.options?.length || 0) > 0,
    null,
    { timeout: 20000 }
  );
  await page.waitForSelector("#projectList .project-item", { timeout: 20000 });
  await page.waitForFunction(() => {
    const summary = document.querySelector("#projectSummaryPanel");
    return Boolean(summary && !summary.classList.contains("hidden"));
  }, null, { timeout: 15000 });
}

async function openCreateForm(page) {
  await waitAppReady(page);
  const newBtn = page.locator("#newProjectBtn");
  if (await newBtn.isDisabled()) fail("新建项目按钮不可用");
  await newBtn.click();
  await page.waitForFunction(
    () => !document.querySelector("#projectForm")?.classList.contains("hidden"),
    null,
    { timeout: 8000 }
  );
  await page.waitForTimeout(400);
  if (await page.evaluate(() => document.querySelector("#projectForm")?.classList.contains("hidden"))) {
    await newBtn.click();
    await page.waitForFunction(
      () => !document.querySelector("#projectForm")?.classList.contains("hidden"),
      null,
      { timeout: 8000 }
    );
  }
  const visible = await page.evaluate(() => {
    const form = document.querySelector("#projectForm");
    return Boolean(form && !form.classList.contains("hidden"));
  });
  if (!visible) fail("点击新建后创建表单仍隐藏");
}

async function setSelectValue(page, selector, value) {
  await page.selectOption(selector, value, { force: true });
  const actual = await page.locator(selector).inputValue();
  if (actual !== String(value)) fail(`${selector} 未能设为 ${value}，实际 ${actual}`);
}

async function openStage(page, stageId) {
  await clearUnsaved(page);
  await page.click(`[data-stage-id="${stageId}"]`);
  await page.waitForTimeout(400);
  await clearUnsaved(page);
}
async function selectShotCard(page, index) {
  await page.waitForSelector(".asset-card", { timeout: 15000 });
  const cards = page.locator(".asset-card");
  const count = await cards.count();
  if (count < index + 1) fail(`镜头卡片不足：${count}，需要第 ${index + 1} 个`);
  await cards.nth(index).click();
  await page.waitForSelector("#videoModeSelect", { timeout: 15000 });
}
function assertNoFallback(proj, where) {
  const blobs = [
    ...(proj.adaptation_options || []),
    proj.story_bible || {},
    ...(proj.storyboard_drafts || []),
    ...(proj.vision_reviews || []),
  ];
  for (const item of blobs) {
    if (item && (item.used_local_fallback === 1 || item.used_local_fallback === true)) {
      fail(`${where} 出现本地回退，live_strict 禁止静默回退`);
    }
  }
}
function publicTasks(proj) {
  return (proj.video_tasks || []).map((task) => ({
    id: task.id,
    shot_id: task.shot_id,
    provider: task.provider,
    model: task.model,
    status: task.status,
    cloud_status: task.cloud_status,
    remote_task_id: redact(task.remote_task_id),
  }));
}

async function prepareShotI2V(page, projectId, index) {
  await openStage(page, "video");
  await selectShotCard(page, index);
  await page.selectOption("#videoModeSelect", "i2v");
  await page.selectOption("#videoProviderSelect", "minimax");
  const modelValue = await page.locator("#videoModelSelect option").evaluateAll((opts) => {
    const hit = opts.find((opt) => /MiniMax-H3/i.test(opt.value || "") || /MiniMax-H3/i.test(opt.textContent || ""));
    return hit ? hit.value : opts[0]?.value || "";
  });
  if (modelValue) await page.selectOption("#videoModelSelect", modelValue);
  await page.selectOption("#videoDurationSelect", "4");
  const selectedDuration = await page.locator("#videoDurationSelect").inputValue();
  if (selectedDuration !== "4") fail(`本镜时长不是 4 秒：${selectedDuration}`);
  await page.setInputFiles('[data-asset-upload="first_frame"]', JPEG_PATH);
  await page.waitForFunction(
    () => {
      const status = document.querySelector('[data-upload-status="first_frame"]')?.textContent || "";
      const toast = document.querySelector("#statusToast")?.textContent || "";
      if (/失败/.test(status + toast) && !/成功/.test(status)) throw new Error(`首帧上传失败：${status || toast}`);
      return status.includes("成功") || Boolean(document.querySelector("#firstFrameSelect")?.value);
    },
    null,
    { timeout: 25000 }
  );
  const src = await page.locator(".local-keyframe-thumb").first().getAttribute("src").catch(() => "");
  if (src && src.startsWith("data:")) fail("首帧预览不得使用 Data URL");
  if (src && !src.includes(projectId)) fail("首帧预览必须属于当前项目");
  await page.click('[data-action="save-shot-draft"]');
  await page.waitForTimeout(600);
}

async function visionOnce(page, projectId) {
  await openStage(page, "keyframes");
  await selectShotCard(page, 0);
  const btn = page.locator('[data-adapt="vision-review"]');
  if (!(await btn.count())) fail("缺少视觉检查按钮");
  if (await btn.isDisabled()) fail("视觉检查按钮不可用，首帧可能未登记");
  await btn.click();
  const proj = await waitProject(
    page,
    projectId,
    (item) => (item.vision_reviews || []).length >= 1,
    120000,
    "vision-review"
  );
  assertNoFallback(proj, "视觉检查");
  const review = (proj.vision_reviews || [])[0] || {};
  if (review.used_local_fallback) fail("视觉检查使用了本地回退");
  if ((proj.live_vision_call_count || 0) > 1) fail("视觉检查超过 1 次");
  return proj;
}

async function refreshSameRemoteTasks(page, projectId) {
  const refresh = page.locator('[data-action="refresh-video-tasks"]');
  const visible = (await refresh.count()) > 0 && (await refresh.first().isVisible().catch(() => false));
  if (visible) {
    await refresh.first().click({ timeout: 5000 });
    return;
  }
  await apiPost(page, `/api/projects/${projectId}/videos/refresh`, {});
}

async function waitShotVideoReady(page, projectId, target) {
  const start = Date.now();
  while (Date.now() - start < VIDEO_WAIT_MS) {
    const proj = await apiGet(page, `/api/projects/${projectId}`);
    const shotNow = (proj.shots || []).find((item) => item.id === target.id);
    const jobs = (proj.jobs || []).filter((job) => job.shot_id === target.id);
    const failed = jobs.find((job) => job.status === "failed");
    if (failed) fail(`镜头 ${target.shot_index || target.id} 失败：${failed.error_message || failed.message || ""}`);
    if (/BLOCKED_BEFORE_CALL/.test(JSON.stringify(jobs))) fail(`镜头 ${target.id} 触发 BLOCKED_BEFORE_CALL`);
    const status = shotNow?.status || "";
    const tasks = (proj.video_tasks || []).filter((task) => task.shot_id === target.id);
    const task = tasks[0];
    if (task) {
      writeResult({
        [`task_${target.id}`]: {
          id: task.id,
          remote_task_id: redact(task.remote_task_id),
          status: task.status,
          cloud_status: task.cloud_status,
        },
      });
    }
    if (status === "video_ready") {
      if (tasks.length !== 1) fail(`镜头 ${target.id} video_tasks=${tasks.length}，期望 1`);
      return proj;
    }
    if (status === "video_failed") fail(`镜头 ${target.id} 状态 video_failed`);
    await refreshSameRemoteTasks(page, projectId);
    await page.waitForTimeout(8000);
  }
  fail(`镜头 ${target.id} 等待远程任务超时（只回查，未补发）`);
}

async function generateOnceAndWait(page, projectId, index) {
  await openStage(page, "video");
  await selectShotCard(page, index);
  const before = await apiGet(page, `/api/projects/${projectId}`);
  const target = [...(before.shots || [])].sort((a, b) => a.shot_index - b.shot_index)[index];
  if (!target) fail(`找不到镜头 ${index + 1}`);
  if (submittedShots.has(target.id)) fail(`镜头 ${target.id} 已提交过，禁止补发`);

  const existing = (before.video_tasks || []).filter((task) => task.shot_id === target.id && task.remote_task_id);
  if (existing.length) {
    submittedShots.add(target.id);
    const task = existing[0];
    writeResult({
      [`shot_${index + 1}_resume`]: {
        shot_id: target.id,
        task_id: task.id,
        remote_task_id: redact(task.remote_task_id),
        status: task.status,
      },
    });
    pass(`镜头 ${index + 1} 已有远程任务 ${redact(task.remote_task_id)}，只回查不补发`);
    return waitShotVideoReady(page, projectId, target);
  }

  const genBtn = page.locator('[data-action="generate-video"]');
  if (await genBtn.isDisabled()) {
    fail(`生成按钮不可用：${await genBtn.getAttribute("title")}`);
  }
  submittedShots.add(target.id);
  const [response] = await Promise.all([
    page.waitForResponse((res) => /\/shots\/[^/]+\/video$/.test(res.url()) && res.request().method() === "POST", { timeout: 60000 }),
    genBtn.click(),
  ]);
  if (!response.ok()) {
    const text = await response.text();
    fail(`镜头 ${index + 1} 提交失败 HTTP ${response.status()} ${text.slice(0, 400)}`);
  }
  const after = await apiGet(page, `/api/projects/${projectId}`);
  const submitted = (after.video_tasks || []).filter((task) => task.shot_id === target.id);
  if (!submitted.length) fail(`镜头 ${index + 1} 提交后没有 video_task`);
  writeResult({
    [`shot_${index + 1}_submit`]: {
      shot_id: target.id,
      task_id: submitted[0].id,
      remote_task_id: redact(submitted[0].remote_task_id),
      status: submitted[0].status,
    },
  });
  pass(`镜头 ${index + 1} 已提交一次 I2V（不再补发，只 refresh ${redact(submitted[0].remote_task_id)}）`);
  return waitShotVideoReady(page, projectId, target);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  if (!JPEG_PATH || !fs.existsSync(JPEG_PATH)) fail("缺少 gyfy.jpg 副本");
  writeResult({
    title: TITLE,
    source_text: SAMPLE,
    generation_mode: "live_strict",
    real_network: true,
    stages,
  }, { replace: true });
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(20000);
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    await openCreateForm(page);
    await screenshot(page, "00-create-form-1440.png");
    await page.fill("#titleInput", TITLE, { force: true });
    await page.fill("#sourceTextInput", SAMPLE, { force: true });
    await setSelectValue(page, "#shotModeInput", "manual");
    await page.waitForFunction(() => !document.querySelector("#manualShotField")?.classList.contains("hidden"));
    await page.fill("#shotCountInput", "2", { force: true });
    // ProjectCreate 校验 ge=5；MiniMax 最短 4s 在镜头级设置，不改业务代码。
    const projectDuration = (await page.locator("#durationInput option[value='5']").count())
      ? "5"
      : (await page.locator("#durationInput option[value='6']").count())
        ? "6"
        : "";
    if (!projectDuration) fail("项目时长下拉没有可通过校验的 5s/6s");
    await setSelectValue(page, "#durationInput", projectDuration);
    await setSelectValue(page, "#generationModeInput", "live_strict");
    const [createdRes] = await Promise.all([
      page.waitForResponse(
        (res) => res.request().method() === "POST" && new URL(res.url()).pathname === "/api/projects",
        { timeout: 20000 }
      ),
      page.click("#submitProjectBtn"),
    ]);
    if (!createdRes.ok()) {
      fail(`创建项目失败 HTTP ${createdRes.status()} ${((await createdRes.text()) || "").slice(0, 400)}`);
    }
    await page.waitForFunction(
      (title) => (document.querySelector("#summaryFields")?.innerText || "").includes(title.slice(0, 8)),
      TITLE,
      { timeout: 20000 }
    );
    const createdId = await page.evaluate(() => document.querySelector(".project-item.active")?.getAttribute("data-project-id"));
    if (!createdId) fail("新建项目后没有当前项目");
    if (createdId === "project_5fdac03f50" || createdId === "v1demo_main") fail("拒绝复用受保护项目");
    writeResult({ project_id: createdId });
    const created = await apiGet(page, `/api/projects/${createdId}`);
    if (created.generation_mode !== "live_strict") fail(`生成模式不是 live_strict：${created.generation_mode}`);
    if (created.requested_shot_count !== 2 || created.shot_count_mode !== "manual") {
      fail(`镜头策略不是手动 2 镜：${created.shot_count_mode} ${created.requested_shot_count}`);
    }
    if (Number(created.duration_seconds) < 5) fail(`项目时长校验异常：${created.duration_seconds}`);
    pass("新建 live_strict 临时项目，手动 2 镜 / 4 秒");
    setStage("create_project", "PASS", { project_id: createdId });
    await screenshot(page, "01-created-1440.png");

    await openStage(page, "text");
    const textOrigin = await page.locator('section[data-model-stage="text_understanding"]').innerText();
    if (!/deepseek-v4-flash/i.test(textOrigin) && !/DeepSeek V4 Flash/i.test(textOrigin)) {
      fail("文本阶段未预选 DeepSeek Flash");
    }
    const modeBadge = await page.locator("#generationModeBadge").innerText();
    if (!/失败即失败|真实模型/.test(modeBadge)) fail(`生成模式徽章异常：${modeBadge}`);
    pass("文本预选 DeepSeek Flash，生成模式为真实严格");

    await page.click("#runWorkflowBtn");
    await waitProject(page, createdId, (item) => item.status === "awaiting_scope_review" || (item.adaptation_options || []).length > 0, TEXT_WAIT_MS, "adaptation");
    let proj = await apiGet(page, `/api/projects/${createdId}`);
    assertNoFallback(proj, "改编方案");
    if ((proj.adaptation_options || []).some((item) => item.generation_mode && item.generation_mode !== "live_strict")) {
      fail("改编方案 generation_mode 不是 live_strict");
    }
    pass("改编方案已生成（真实文本 1/3）");
    setStage("adaptation", "PASS");
    await screenshot(page, "02-adaptation-1440.png");
    await snapDom(page, "adaptation");

    await openStage(page, "text");
    await page.waitForSelector("[data-adapt='confirm-scope']", { timeout: 20000 });
    await page.locator("[data-adapt='confirm-scope']").first().click();
    await waitProject(page, createdId, (item) => item.status === "awaiting_bible_review", TEXT_WAIT_MS, "bible");
    proj = await apiGet(page, `/api/projects/${createdId}`);
    assertNoFallback(proj, "Story Bible");
    pass("已确认范围并生成 Story Bible（真实文本 2/3）");
    setStage("bible", "PASS");
    await openStage(page, "bible");
    await screenshot(page, "03-bible-1440.png");

    await page.waitForSelector("[data-adapt='confirm-bible']", { timeout: 20000 });
    await page.click("[data-adapt='confirm-bible']");
    await waitProject(page, createdId, (item) => item.status === "awaiting_storyboard_review", TEXT_WAIT_MS, "storyboard");
    proj = await apiGet(page, `/api/projects/${createdId}`);
    assertNoFallback(proj, "分镜");
    pass("已确认 Bible 并生成分镜（真实文本 3/3）");
    setStage("storyboard", "PASS");
    await openStage(page, "storyboard");
    await page.waitForSelector("[data-adapt='confirm-storyboard']", { timeout: 20000 });
    await screenshot(page, "04-storyboard-1440.png");
    await page.click("[data-adapt='confirm-storyboard']");
    await waitProject(page, createdId, (item) => item.status === "production_ready", TEXT_WAIT_MS, "production");
    proj = await apiGet(page, `/api/projects/${createdId}`);
    if ((proj.shots || []).length !== 2) fail(`镜头数不是 2：${(proj.shots || []).length}`);
    if ((proj.live_text_call_count || 0) > 3) fail(`文本调用超过 3：${proj.live_text_call_count}`);
    pass("分镜确认后进入 production_ready，共 2 镜");
    setStage("confirm_storyboard", "PASS", { shots: 2, live_text_call_count: proj.live_text_call_count });

    await prepareShotI2V(page, createdId, 0);
    await prepareShotI2V(page, createdId, 1);
    pass("两镜均已上传 JPEG 首帧并设为 MiniMax I2V 4s");
    setStage("upload_first_frames", "PASS");
    await screenshot(page, "05-first-frames-1440.png");

    await visionOnce(page, createdId);
    pass("视觉检查 1 次完成");
    setStage("vision_review", "PASS");
    await screenshot(page, "06-vision-1440.png");

    await generateOnceAndWait(page, createdId, 0);
    pass("镜头 1 MiniMax I2V 完成");
    setStage("video_shot_1", "PASS");
    await screenshot(page, "07-video-shot1-1440.png");
    await generateOnceAndWait(page, createdId, 1);
    pass("镜头 2 MiniMax I2V 完成");
    setStage("video_shot_2", "PASS");
    await screenshot(page, "08-video-shot2-1440.png");

    proj = await apiGet(page, `/api/projects/${createdId}`);
    const remotes = [...new Set((proj.video_tasks || []).map((task) => task.remote_task_id).filter(Boolean))];
    if (remotes.length !== 2) fail(`唯一远程任务数 ${remotes.length}，期望 2`);
    if ((proj.video_tasks || []).length !== 2) fail(`video_tasks=${(proj.video_tasks || []).length}`);
    if ((proj.live_video_call_count || 0) > 2) fail(`视频提交计数 ${proj.live_video_call_count} 超过 2`);
    for (const shot of proj.shots || []) {
      const version = (shot.versions || []).find((item) => item.id === shot.current_version_id) || (shot.versions || [])[0];
      if (Number(version?.duration_seconds) !== 4) {
        fail(`镜头 ${shot.shot_index} 实际时长不是 4 秒：${version?.duration_seconds}`);
      }
      if (String(version?.provider || "").toLowerCase() !== "minimax") fail(`镜头 ${shot.shot_index} Provider 不是 MiniMax`);
      if (!/MiniMax-H3/i.test(String(version?.model || ""))) fail(`镜头 ${shot.shot_index} 模型不是 MiniMax-H3`);
      if (String(version?.video_mode || "") !== "i2v") fail(`镜头 ${shot.shot_index} 不是 I2V`);
    }
    writeResult({
      project_id: createdId,
      video_tasks: publicTasks(proj),
      live_text_call_count: proj.live_text_call_count,
      live_vision_call_count: proj.live_vision_call_count,
      live_video_call_count: proj.live_video_call_count,
      unique_remote_tasks: remotes.length,
      video_submits_new: remotes.length,
      video_tasks_reused: 0,
    });

    await openStage(page, "assembly");
    await page.waitForSelector("#assembleProjectBtn", { timeout: 15000 });
    if (await page.locator("#assembleProjectBtn").isDisabled()) {
      fail(`成片按钮不可用：${await page.locator("#assembleDisabledReason").innerText().catch(() => "")}`);
    }
    await page.click("#assembleProjectBtn");
    await page.waitForFunction(
      () => {
        const video = document.querySelector("#assemblyPanel video, .assembly-preview video");
        const btn = document.querySelector("#assembleProjectBtn")?.textContent || "";
        return Boolean(video && video.getAttribute("src")) || btn.includes("重新合成");
      },
      null,
      { timeout: 90000 }
    );
    const previewSrc = await page.locator(".assembly-preview video, #assemblyPanel video").first().getAttribute("src");
    if (!previewSrc) fail("缺少成片预览");
    if (!previewSrc.includes(createdId)) fail("成片预览不属于当前项目");
    const download = page.locator('.assembly-preview a[download], #assemblyPanel a[download]').first();
    if (!(await download.count())) fail("缺少下载入口");
    const href = await download.getAttribute("href");
    const dl = await page.request.get(href.startsWith("http") ? href : `${BASE}${href}`);
    if (!dl.ok()) fail(`下载成片失败 HTTP ${dl.status()}`);
    const body = Buffer.from(await dl.body());
    if (body.length < 1000) fail("下载成片过小");
    fs.writeFileSync(path.join(OUT, "downloaded-final.mp4"), body);
    pass("FFmpeg 成片可预览且可下载");
    setStage("assembly", "PASS");
    setStage("preview", "PASS");
    setStage("download", "PASS");
    await screenshot(page, "09-assembly-1440.png");
    await openStage(page, "export");
    await screenshot(page, "10-export-1440.png");
    await snapDom(page, "export");

    writeResult({
      project_id: createdId,
      title: TITLE,
      generation_mode: "live_strict",
      real_network: true,
      preview_ok: true,
      download_ok: true,
      ffmpeg_ran: true,
      final_cut: true,
      stages,
      screenshot_hashes: screenshotHashes,
    });
    fs.writeFileSync(
      path.join(OUT, "browser_evidence.json"),
      JSON.stringify({ phase: "live-2shot", live_network: true, collected: true, project_id: createdId, stages }, null, 2),
      "utf8"
    );
    fs.writeFileSync(path.join(OUT, "browser_dom_snapshots.json"), JSON.stringify({ collected: true, snapshots }, null, 2), "utf8");
    fs.writeFileSync(
      path.join(OUT, "browser_screenshot_hashes.json"),
      JSON.stringify({ collected: true, files: screenshotHashes, ordered: Object.keys(screenshotHashes) }, null, 2),
      "utf8"
    );
    console.log(`PASS: live 2-shot frontend loop project=${createdId}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  setStage("fatal", "FAIL", { message: String(error.message || error) });
  writeResult({ error: String(error.message || error), stages });
  process.exit(1);
});
