/**
 * Contract test for tools/live_2shot_resume.cjs against a local Mock backend.
 * Zero live network: the Mock server only listens on 127.0.0.1 and serves fixtures.
 *
 * Verifies: wrong-project refusal, refresh-only default, explicit single submit,
 * and stop-on-failure without re-issuing anything.
 */
"use strict";

const assert = require("assert");
const http = require("http");
const os = require("os");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");

const PORT = Number(process.env.RESUME_TEST_PORT || 8055);
const BASE = `http://127.0.0.1:${PORT}`;
const ROOT = path.join(__dirname, "..");
const SHOT1 = "shot_e84099874d";
const SHOT2 = "shot_77c04d65b5";
const PROJECT = "project_a43afde7c5";

let fixture = null;
let requests = [];
let submitBehaviour = "complete";
let assembleRequested = false;
let assemblyCompletes = true;

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

function projectFixture({ title = "LIVE2SHOT 春秋蝉鸣少年归", shot1Status = "video_running", task1Status = "running" } = {}) {
  return {
    id: PROJECT,
    title,
    generation_mode: "live_strict",
    status: "production_ready",
    shots: [
      { id: SHOT1, shot_index: 1, status: shot1Status },
      { id: SHOT2, shot_index: 2, status: "keyframes_ready" },
    ],
    video_tasks:
      shot1Status === "keyframes_ready"
        ? []
        : [
            {
              id: "vt_e0800b9609",
              shot_id: SHOT1,
              version_id: "version_85fb55cedd",
              job_id: "job_b28d841d8f",
              provider: "minimax",
              remote_task_id: "436764667060538",
              status: task1Status,
              cloud_status: "submitted",
            },
          ],
    jobs: [],
  };
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
    });
    req.on("end", () => resolve(data));
  });
}

function json(res, code, payload) {
  const text = JSON.stringify(payload);
  res.writeHead(code, { "content-type": "application/json" });
  res.end(text);
}

function startServer() {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, BASE);
    const p = url.pathname;
    await readBody(req);
    requests.push(`${req.method} ${p}`);
    if (p === "/api/health") return json(res, 200, { ok: true });
    const projectMatch = p.match(/^\/api\/projects\/([^/]+)$/);
    if (req.method === "GET" && projectMatch) {
      if (!fixture || projectMatch[1] !== fixture.id) return json(res, 404, { detail: "Project not found" });
      return json(res, 200, fixture);
    }
    if (req.method === "POST" && p.endsWith("/videos/refresh")) {
      const jobId = `job_refresh_${fixture.jobs.length + 1}`;
      fixture.jobs.push({ id: jobId, type: "video_task_refresh", status: "completed", message: "没有待回查的云端任务" });
      return json(res, 200, { job_id: jobId, status: "completed" });
    }
    const shotVideo = p.match(/^\/api\/projects\/([^/]+)\/shots\/([^/]+)\/video$/);
    if (req.method === "POST" && shotVideo) {
      const shotId = shotVideo[2];
      const jobId = `job_video_${fixture.jobs.length + 1}`;
      fixture.jobs.push({ id: jobId, type: "video_generation", status: "running", shot_id: shotId });
      fixture.video_tasks.push({
        id: `vt_${shotId.slice(-6)}`,
        shot_id: shotId,
        version_id: "version_new",
        job_id: jobId,
        provider: "minimax",
        remote_task_id: "111122223333",
        status: "running",
        cloud_status: "submitted",
      });
      const shot = fixture.shots.find((item) => item.id === shotId);
      shot.status = "video_running";
      if (submitBehaviour === "complete") {
        shot.status = "video_ready";
        const task = fixture.video_tasks.find((item) => item.shot_id === shotId);
        task.status = "completed";
        const job = fixture.jobs.find((item) => item.id === jobId);
        job.status = "completed";
      }
      return json(res, 200, { job_id: jobId, status: "queued", version_id: "version_new", remote_task_id: "111122223333" });
    }
    if (req.method === "GET" && p.endsWith("/assembly")) {
      const ready = (fixture.shots || []).filter((shot) => shot.status === "video_ready").length;
      const done = assembleRequested && assemblyCompletes;
      return json(res, 200, {
        ok: ready === fixture.shots.length,
        can_assemble: ready === fixture.shots.length,
        errors: [],
        shot_count: fixture.shots.length,
        ready_count: ready,
        stale: false,
        current_final: done,
        active_job: null,
      });
    }
    if (req.method === "POST" && p.endsWith("/assemble")) {
      assembleRequested = true;
      return json(res, 200, { job_id: "job_assemble_1", status: "queued" });
    }
    return json(res, 404, { detail: `unhandled ${req.method} ${p}` });
  });
  return new Promise((resolve) => server.listen(PORT, "127.0.0.1", () => resolve(server)));
}

function runDriver(args) {
  const reportPath = path.join(os.tmpdir(), `resume_report_${Date.now()}.json`);
  return new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [path.join(ROOT, "tools", "live_2shot_resume.cjs"), ...args],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          VISIONCRAFT_BASE_URL: BASE,
          VISIONCRAFT_RESUME_REPORT: reportPath,
          RESUME_JOB_POLL_MS: "20",
          RESUME_READY_POLL_MS: "20",
          RESUME_MIN_REFRESH_GAP_MS: "20",
        },
      }
    );
    let out = "";
    child.stdout.on("data", (chunk) => (out += chunk));
    child.stderr.on("data", (chunk) => (out += chunk));
    child.on("close", (code) => resolve({ code, out, reportPath }));
  });
}

function videoPosts() {
  return requests.filter((entry) => /\/shots\/.+\/video$/.test(entry));
}

async function scenarioWrongProject() {
  fixture = projectFixture({ title: "OTHER PROJECT 不是本次测试" });
  requests = [];
  const { code, out } = await runDriver([`--project=${PROJECT}`]);
  assert.strictEqual(code, 3, out);
  assert.strictEqual(videoPosts().length, 0);
  assert.match(out, /拒绝在非本次测试项目上操作/);
  pass("非本次 LIVE2SHOT 项目被拒绝，且没有任何提交");
}

async function scenarioRefreshOnlyDefault() {
  fixture = projectFixture();
  requests = [];
  const { code, out, reportPath } = await runDriver([`--project=${PROJECT}`]);
  assert.strictEqual(code, 1, out);
  assert.strictEqual(videoPosts().length, 0, `默认模式不得提交任何视频：${JSON.stringify(videoPosts())}`);
  assert.strictEqual(requests.filter((entry) => entry.endsWith("/videos/refresh")).length, 1);
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  assert.strictEqual(report.result.submits_issued.length, 0);
  assert.strictEqual(report.steps[0].plan.blocked[0].reason, "not_authorized_by_cli");
  assert.deepStrictEqual(report.result.inflight_remote_tasks, ["4367…0538"]);
  pass("默认只回查一次，未授权时一行提交都没有，报告记录脱敏 remote id");
}

async function scenarioAuthorizedSingleSubmit() {
  fixture = projectFixture();
  requests = [];
  submitBehaviour = "complete";
  const { code, out, reportPath } = await runDriver([`--project=${PROJECT}`, `--allow-submit=${SHOT2}`]);
  assert.strictEqual(code, 1, out);
  const posts = videoPosts();
  assert.strictEqual(posts.length, 1, `只允许一次提交：${JSON.stringify(posts)}`);
  assert.ok(posts[0].includes(SHOT2), posts[0]);
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  assert.strictEqual(report.result.status, "PARTIAL", "镜头 1 仍 inflight 时必须报 PARTIAL");
  assert.deepStrictEqual(report.result.submits_issued, [SHOT2]);
  assert.strictEqual(report.result.final_plan.counts.ready, 1);
  pass("授权后只提交镜头 2 一次，镜头 1 全程零提交，镜头 1 未完成时如实报 PARTIAL");
}

async function scenarioFullClosureWithAssembly() {
  fixture = projectFixture({ shot1Status: "video_ready", task1Status: "completed" });
  requests = [];
  submitBehaviour = "complete";
  assembleRequested = false;
  const { code, out, reportPath } = await runDriver([
    `--project=${PROJECT}`,
    `--allow-submit=${SHOT2}`,
    "--assemble",
  ]);
  assert.strictEqual(code, 0, out);
  const posts = videoPosts();
  assert.strictEqual(posts.length, 1, JSON.stringify(posts));
  assert.ok(posts[0].includes(SHOT2));
  assert.strictEqual(requests.filter((entry) => entry.endsWith("/videos/refresh")).length, 0, "无 inflight 时不应回查");
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  assert.strictEqual(report.result.status, "PASS");
  assert.strictEqual(report.result.assembly.ok, true);
  pass("镜头 1 已完成时零回查，只提交镜头 2，合成成功后整体 PASS");
}

async function scenarioAssemblyPendingIsNotSuccess() {
  fixture = projectFixture({ shot1Status: "video_ready", task1Status: "completed" });
  requests = [];
  submitBehaviour = "complete";
  assembleRequested = false;
  assemblyCompletes = false;
  const { code, out, reportPath } = await runDriver([
    `--project=${PROJECT}`,
    `--allow-submit=${SHOT2}`,
    "--assemble",
  ]);
  assert.strictEqual(code, 1, "合成未产出成片时不得返回成功");
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  assert.strictEqual(report.result.status, "VIDEO_READY_ASSEMBLY_PENDING");
  pass("请求了合成但没有成片时，退出码为失败并标注 PENDING");
}

async function scenarioFailedShotStopsEverything() {
  fixture = projectFixture({ shot1Status: "video_failed", task1Status: "failed" });
  requests = [];
  const { code, out } = await runDriver([`--project=${PROJECT}`, `--allow-submit=${SHOT2}`]);
  assert.strictEqual(code, 2, out);
  assert.strictEqual(videoPosts().length, 0, "失败即停，不得补发");
  assert.strictEqual(requests.filter((entry) => entry.endsWith("/videos/refresh")).length, 0);
  assert.match(out, /STOP: 镜头失败，停止/);
  pass("镜头失败时直接停止：不回查、不提交");
}

async function main() {
  const server = await startServer();
  try {
    await scenarioWrongProject();
    await scenarioRefreshOnlyDefault();
    await scenarioAuthorizedSingleSubmit();
    await scenarioFullClosureWithAssembly();
    await scenarioAssemblyPendingIsNotSuccess();
    await scenarioFailedShotStopsEverything();
    console.log("PASS: live 2-shot resume driver contract (Mock backend, no live network)");
  } finally {
    server.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
