#!/usr/bin/env node
/**
 * 本地自检（零外发、零费用）：证明 2 镜 harness 拿到的"本轮新建项目 id"来自
 * POST /api/projects 的服务端应答，并且前端确实把该项目渲染成当前项目。
 *
 * 背景：曾两次在错误的项目上跑工作流——页面加载时前端自动选中 projects[0]，
 * 创建请求返回后侧栏还停留在上一次渲染，此时读 .project-item.active 会拿到
 * "上一个项目"；旧标题前缀又能直接满足原先"等摘要出现"的等待条件。
 *
 * 本脚本自己起一个 LOCAL 后端（LIVE 三个开关强制为 0），在真实 UI 上复现
 * 创建步骤，断言：
 *   1. 响应体顶层带 id，且该 id 不在本轮开始前的项目集合里；
 *   2. 该 id 会（在超时内）成为侧栏 active 项；
 *   3. 历史同名项目 a43afde7c5 不会被采纳，且会被守卫判为不合法。
 * 结束后打印新建项目 id，交由 tools/cleanup_temp_project.py 清理。
 */
"use strict";

const { spawn } = require("child_process");
const http = require("http");
const net = require("net");
const path = require("path");
const fs = require("fs");

const ROOT = path.join(__dirname, "..");
const { assertFreshCreatedId } = require("./live_2shot_helpers");
const { openCreateForm, fillProjectForm } = require("./ui_project_form.cjs");
const { chromium } = require(path.join(ROOT, ".playwright-cli", "node_modules", "playwright"));

const STAMP = new Date().toISOString().slice(11, 19).replace(/:/g, "");
const TITLE = `LIVE2SHOT-GUARD${STAMP} 春秋蝉鸣少年归`;
const SAMPLE = "春秋蝉鸣少年归。";
const OLD_PROJECT = "project_a43afde7c5";
// 输出目录。
//
// output/playwright/live-2shot 放的是**真实付费运行**的归档证据（报告、后端日志、
// 截图），本脚本过去无论谁跑都往那里写。于是一次无费用回归就会把真实运行的
// create_guard_report.json / create_guard_backend.log 覆盖掉，而覆盖前后文件名完全
// 相同，从产物上看不出证据已经被替换 —— 归档里留下的其实是回归产物。
//
// 判据用「有没有隔离数据目录」：回归驱动器（run_no_cost_regression.py）一定会导出
// VISIONCRAFT_DATA_DIR，人工单独跑不会。这样回归写进运行目录，人工跑仍写归档。
const DATA_DIR = (process.env.VISIONCRAFT_DATA_DIR || "").trim();
const OUT = DATA_DIR
  ? path.join(DATA_DIR, "live-2shot")
  : path.join(ROOT, "output", "playwright", "live-2shot");
const REPORT = path.join(OUT, "create_guard_report.json");

const observations = [];
let failures = 0;
let createdId = "";

function check(ok, label, detail) {
  if (ok) {
    console.log(`PASS: ${label}`);
  } else {
    failures += 1;
    console.error(`FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
  }
  observations.push({ ok: !!ok, label, detail: detail || "" });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function getJson(base, p) {
  return new Promise((resolve, reject) => {
    const req = http.get(`${base}${p}`, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on("error", reject);
    req.setTimeout(4000, () => req.destroy(new Error("timeout")));
  });
}

async function waitHealth(base, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await getJson(base, "/api/health");
      return true;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  }
  return false;
}

function localOnlyEnv() {
  return {
    ...process.env,
    VISIONCRAFT_ALLOW_LIVE_LLM: "0",
    VISIONCRAFT_ALLOW_LIVE_VISION: "0",
    VISIONCRAFT_ALLOW_LIVE_VIDEO: "0",
    PYTHONUNBUFFERED: "1",
  };
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const python = path.join(ROOT, ".venv", "Scripts", "python.exe");
  const exe = fs.existsSync(python) ? python : process.execPath;
  const logPath = path.join(OUT, "create_guard_backend.log");
  const logHandle = fs.openSync(logPath, "w");
  const server = spawn(
    exe,
    ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", String(port)],
    { cwd: ROOT, env: localOnlyEnv(), stdio: ["ignore", logHandle, logHandle] }
  );

  let browser = null;
  try {
    const up = await waitHealth(base);
    if (!up) throw new Error(`本地后端未起来，见 ${logPath}`);
    console.log(`INFO: local backend ${base} (LIVE 开关全 0)`);

    const health = await getJson(base, "/api/health");
    const caps = await getJson(base, "/api/providers/capabilities");
    const live = caps.live_access || {};
    const armed = Object.entries(live).filter(([, value]) => value === true).map(([key]) => key);
    check(armed.length === 0, "本地后端未 armed：live_access 无 true 项", JSON.stringify(live));
    console.log(`INFO: health=${JSON.stringify(health).slice(0, 120)}`);

    try {
      browser = await chromium.launch({ channel: "chrome", headless: true });
    } catch {
      browser = await chromium.launch({ headless: true });
    }
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(20000);

    await page.goto(base, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#newProjectBtn");
    // app.js 的 init() 是异步的，收尾才把表单模式定为「有项目则摘要态」。若在它收尾前就点
    // 「新建」，表单会先显示、随即被隐藏，之后的 force 填入在隐藏元素上静默失效，提交校验
    // 挡下请求 —— 现象是等不到 POST（而不是收到错误响应），极难归因。故先等状态稳定。
    await openCreateForm(page);

    // 1) 记录本轮开始前已存在的项目 id（事故形态的判定依据）
    const preexistingIds = await page.evaluate(() =>
      Array.from(document.querySelectorAll("#projectList .project-item")).map((el) =>
        el.getAttribute("data-project-id")
      )
    );
    check(preexistingIds.length > 0, "读到本轮开始前的项目列表", `${preexistingIds.length} 个`);
    check(
      preexistingIds.includes(OLD_PROJECT),
      `历史同名项目 ${OLD_PROJECT} 在列表中（复现事故前提）`
    );

    // 2) 原版的等待条件必须被判定为"不可靠"：title.slice(0, 8) 会被旧项目满足
    const legacyPredicateSatisfiedByOld = await page.evaluate(
      (needle) => (document.querySelector("#summaryFields")?.innerText || "").includes(needle),
      TITLE.slice(0, 8)
    );
    check(
      legacyPredicateSatisfiedByOld,
      "复现旧缺陷条件：title.slice(0,8) 在创建前就已被旧同名项目满足"
    );

    // 3) 创建项目
    await fillProjectForm(page, { "#titleInput": TITLE, "#sourceTextInput": SAMPLE });
    await page.selectOption("#shotModeInput", "manual", { force: true });
    await page.waitForFunction(
      () => !document.querySelector("#manualShotField")?.classList.contains("hidden")
    );
    await page.fill("#shotCountInput", "2", { force: true });
    await page.selectOption("#durationInput", "5", { force: true });
    await page.selectOption("#generationModeInput", "live_strict", { force: true });

    const [response] = await Promise.all([
      page.waitForResponse(
        (res) => res.request().method() === "POST" && new URL(res.url()).pathname === "/api/projects",
        { timeout: 20000 }
      ),
      page.click("#submitProjectBtn"),
    ]);
    if (!response.ok()) {
      throw new Error(`创建项目失败 HTTP ${response.status()} ${(await response.text()).slice(0, 300)}`);
    }

    // 4) id 必须来自服务端应答
    const body = await response.json().catch(() => null);
    createdId = String((body && (body.id || body.project_id)) || "");
    check(!!createdId, "POST /api/projects 应答体顶层带 id", JSON.stringify(body).slice(0, 160));
    check(
      createdId !== OLD_PROJECT && !preexistingIds.includes(createdId),
      "服务端应答的 id 确实是本轮新建的（不在本轮开始前的集合里）",
      createdId
    );

    // 5) 守卫对两者分别判定：新 id 放行，旧 id 拒绝
    let guardError = "";
    try {
      assertFreshCreatedId(preexistingIds, createdId);
    } catch (error) {
      guardError = error.message;
    }
    check(!guardError, "守卫放行本轮新建 id", guardError);
    let oldRejected = false;
    try {
      assertFreshCreatedId(preexistingIds, OLD_PROJECT);
    } catch {
      oldRejected = true;
    }
    check(oldRejected, `守卫拒绝历史同名项目 ${OLD_PROJECT}`);

    // 6) 前端必须真的把新项目渲染成当前项目
    let becameActive = true;
    try {
      await page.waitForFunction(
        (id) => {
          const item = document.querySelector(`#projectList .project-item[data-project-id="${id}"]`);
          return !!item && item.classList.contains("active");
        },
        createdId,
        { timeout: 20000 }
      );
    } catch {
      becameActive = false;
    }
    check(becameActive, "新建项目在超时内成为侧栏 active 项", createdId);

    // 7) 旧同名项目不得处于 active
    const activeIds = await page.evaluate(() =>
      Array.from(document.querySelectorAll("#projectList .project-item.active")).map((el) =>
        el.getAttribute("data-project-id")
      )
    );
    check(
      !activeIds.includes(OLD_PROJECT) && activeIds.length === 1 && activeIds[0] === createdId,
      "唯一 active 项就是本轮新建项目，历史同名项目未被采纳",
      JSON.stringify(activeIds)
    );

    await page.screenshot({ path: path.join(OUT, "create-guard-1440.png"), fullPage: true });
  } finally {
    if (browser) await browser.close().catch(() => {});
    try {
      if (process.platform === "win32") {
        spawn("taskkill", ["/PID", String(server.pid), "/T", "/F"], { stdio: "ignore" });
      } else {
        server.kill();
      }
    } catch {
      /* 忽略清理异常 */
    }
    fs.closeSync(logHandle);
    const report = {
      schema: "visioncraft.live_2shot_create_guard.v1",
      real_network: false,
      live_flags_forced_off: true,
      created_project_id: createdId,
      title: TITLE,
      finished_at: new Date().toISOString(),
      failures,
      observations,
    };
    fs.writeFileSync(REPORT, JSON.stringify(report, null, 2), "utf8");
    console.log(`INFO: report -> ${REPORT}`);
  }

  console.log(failures === 0 ? "ALL PASS: create-guard self check" : `FAILURES: ${failures}`);
  return failures === 0 ? 0 : 1;
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(`FAIL: ${error && error.message}`);
    process.exitCode = 1;
  });
