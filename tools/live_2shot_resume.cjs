/**
 * Resume the retained LIVE2SHOT project without creating a new one.
 *
 * Default behaviour is refresh-only: POST /api/projects/{id}/videos/refresh, which re-queries the
 * SAME remote task and never re-submits. Generation for a shot requires an explicit
 * --allow-submit=<shot_id> flag, and each shot is submitted at most once per run.
 *
 * Usage:
 *   node tools/live_2shot_resume.cjs --project=project_a43afde7c5
 *   node tools/live_2shot_resume.cjs --project=project_a43afde7c5 --allow-submit=shot_77c04d65b5
 *   node tools/live_2shot_resume.cjs --project=project_a43afde7c5 --assemble
 *   node tools/live_2shot_resume.cjs --project=project_a43afde7c5 --dry-run
 */
"use strict";

const fs = require("fs");
const path = require("path");
const {
  redact,
  extractSubmitHint,
  shotVideoVerdict,
  waitForVideoTaskPersist,
} = require("./live_2shot_helpers");
const { planResume, assertSingleSubmitPerShot, describePlan } = require("./live_2shot_resume_plan");

const BASE = process.env.VISIONCRAFT_BASE_URL || "http://127.0.0.1:8040";
const OUT = path.join(__dirname, "..", "output", "playwright", "live-2shot");
const REPORT = process.env.VISIONCRAFT_RESUME_REPORT || path.join(OUT, "resume_report.json");
const TITLE_PREFIX = "LIVE2SHOT";
const HTTP_TIMEOUT_MS = 30000;
const REFRESH_JOB_STATUSES = new Set(["completed", "failed", "waiting_remote"]);
const INFLIGHT_JOB_STATUSES = new Set(["queued", "running"]);
const JOB_POLL_MS = Number(process.env.RESUME_JOB_POLL_MS || 3000);
const READY_POLL_MS = Number(process.env.RESUME_READY_POLL_MS || 8000);
const MIN_REFRESH_GAP_MS = Number(process.env.RESUME_MIN_REFRESH_GAP_MS || 20000);

function parseArgs(argv) {
  const args = {
    project: "project_a43afde7c5",
    allowSubmit: [],
    assemble: false,
    dryRun: false,
    waitMin: 15,
  };
  for (const raw of argv) {
    if (raw.startsWith("--project=")) args.project = raw.split("=")[1];
    else if (raw.startsWith("--allow-submit=")) args.allowSubmit.push(raw.split("=")[1]);
    else if (raw === "--assemble") args.assemble = true;
    else if (raw === "--dry-run") args.dryRun = true;
    else if (raw.startsWith("--wait-min=")) args.waitMin = Number(raw.split("=")[1]) || 15;
  }
  return args;
}

function info(msg) {
  console.log(`INFO: ${msg}`);
}
function step(msg) {
  console.log(`STEP: ${msg}`);
}
function fail(msg) {
  console.log(`STOP: ${msg}`);
}

async function apiGet(p) {
  const res = await fetch(`${BASE}${p}`, { signal: AbortSignal.timeout(HTTP_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`GET ${p} -> ${res.status} ${await res.text()}`);
  return res.json();
}

async function apiPost(p, body) {
  const res = await fetch(`${BASE}${p}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
    signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`POST ${p} -> ${res.status} ${text}`);
  return text ? JSON.parse(text) : {};
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function findActiveRefreshJob(project) {
  return (project.jobs || []).find(
    (job) => String(job.type || job.kind || "").includes("video_task_refresh") && INFLIGHT_JOB_STATUSES.has(String(job.status || ""))
  );
}

function findJob(project, jobId) {
  return (project.jobs || []).find((job) => job.id === jobId);
}

async function waitJob(jobId, timeoutMs) {
  const start = Date.now();
  for (;;) {
    const project = await apiGet(`/api/projects/${state.projectId}`);
    const job = findJob(project, jobId);
    if (job && REFRESH_JOB_STATUSES.has(String(job.status || ""))) return job;
    if (Date.now() - start > timeoutMs) {
      return { id: jobId, status: "timeout", message: "回查任务等待超时" };
    }
    await sleep(JOB_POLL_MS);
  }
}

const state = { projectId: "", allowSubmit: [], dryRun: false, waitMin: 15, submitsIssued: [], refreshCount: 0 };

async function refreshOnce(reasonText) {
  if (state.dryRun) {
    info(`dry-run：跳过回查（${reasonText}）`);
    return { status: "dry_run" };
  }
  const project = await apiGet(`/api/projects/${state.projectId}`);
  const active = findActiveRefreshJob(project);
  if (active) {
    info(`已有回查任务 ${active.id} 在运行，本次不重复发起`);
    return { status: "already_running", job_id: active.id };
  }
  step(`回查同一云端任务：${reasonText}（只 GET，不重复提交、不重复计费）`);
  const body = await apiPost(`/api/projects/${state.projectId}/videos/refresh`);
  state.refreshCount += 1;
  info(`回查任务已排队 ${body.job_id}`);
  return waitJob(body.job_id, 6 * 60 * 1000);
}

async function waitShotReady(shotId, shotIndex) {
  const deadline = Date.now() + state.waitMin * 60 * 1000;
  let lastRefreshAt = 0;
  for (;;) {
    const project = await apiGet(`/api/projects/${state.projectId}`);
    const shot = (project.shots || []).find((item) => item.id === shotId);
    if (!shot) throw new Error(`镜头 ${shotId} 不在项目中`);
    const verdict = shotVideoVerdict(project, shot);
    if (verdict.failed) throw new Error(verdict.message || `镜头 ${shotIndex} 失败`);
    if (verdict.ready) return project;
    if (Date.now() > deadline) throw new Error(`镜头 ${shotIndex} 等待超时（只回查，未补发）`);
    const active = findActiveRefreshJob(project);
    if (!active && Date.now() - lastRefreshAt > MIN_REFRESH_GAP_MS) {
      lastRefreshAt = Date.now();
      await refreshOnce(`镜头 ${shotIndex} 等待中`);
    }
    await sleep(READY_POLL_MS);
  }
}

async function submitShotOnce(shotId, shotIndex) {
  if (state.submitsIssued.includes(shotId)) {
    throw new Error(`镜头 ${shotIndex} 本次运行已提交过，拒绝重复提交`);
  }
  step(`提交镜头 ${shotIndex} 一次（唯一一次 POST /video）`);
  const body = await apiPost(`/api/projects/${state.projectId}/shots/${shotId}/video`, {});
  state.submitsIssued.push(shotId);
  info(`提交返回 job=${body.job_id || "-"} version=${body.version_id || "-"}`);
  const hint = extractSubmitHint(body);
  const { task } = await waitForVideoTaskPersist({
    getProject: () => apiGet(`/api/projects/${state.projectId}`),
    shotId,
    shotLabel: String(shotIndex),
    hint,
    onPoll: ({ generate }) => {
      if (generate) throw new Error("等待落库期间禁止再次提交");
    },
  });
  info(`任务已落库 ${task.id} remote=${redact(task.remote_task_id || "")}`);
  return task;
}

async function snapshot(project) {
  const shots = (project.shots || []).slice().sort((a, b) => (a.shot_index || 0) - (b.shot_index || 0));
  info(`项目 ${project.id} / ${project.title} / mode=${project.generation_mode || "-"}`);
  for (const shot of shots) {
    const verdict = shotVideoVerdict(project, shot);
    info(
      `  镜头 ${shot.shot_index} ${shot.id} status=${shot.status} ready=${verdict.ready} inflight=${verdict.inflight} failed=${verdict.failed}`
    );
  }
  for (const task of project.video_tasks || []) {
    info(`  task ${task.id} shot=${task.shot_id} status=${task.status} remote=${redact(task.remote_task_id || "")}`);
  }
}

async function runAssembly() {
  const assembly = await apiGet(`/api/projects/${state.projectId}/assembly`);
  info(`合成前置：can_assemble=${assembly.can_assemble} ready=${assembly.ready_count}/${assembly.shot_count}`);
  if (!assembly.can_assemble) {
    return { attempted: false, reason: "can_assemble_false", errors: assembly.errors || [] };
  }
  step("发起 FFmpeg 合成");
  const body = await apiPost(`/api/projects/${state.projectId}/assemble`);
  info(`合成任务 ${body.job_id || "-"} 已排队`);
  const deadline = Date.now() + 10 * 60 * 1000;
  for (;;) {
    const status = await apiGet(`/api/projects/${state.projectId}/assembly`);
    if (!status.active_job && status.ready_count === status.shot_count) {
      return {
        attempted: true,
        job_id: body.job_id || "",
        ok: Boolean(status.current_final),
        current_final: Boolean(status.current_final),
        stale: Boolean(status.stale),
      };
    }
    if (Date.now() > deadline) return { attempted: true, job_id: body.job_id || "", ok: false, reason: "assembly_timeout" };
    await sleep(5000);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  state.projectId = args.project;
  state.allowSubmit = args.allowSubmit;
  state.dryRun = args.dryRun;
  state.waitMin = args.waitMin;
  fs.mkdirSync(OUT, { recursive: true });

  const report = {
    schema: "visioncraft.live_2shot_resume.v1",
    started_at: new Date().toISOString(),
    base_url: BASE,
    project_id: args.project,
    dry_run: args.dryRun,
    allow_submit: args.allowSubmit.slice(),
    assemble_requested: args.assemble,
    steps: [],
    result: {},
  };
  const write = () => fs.writeFileSync(REPORT, JSON.stringify(report, null, 2), "utf8");

  let project = await apiGet(`/api/projects/${args.project}`);
  if (!String(project.title || "").startsWith(TITLE_PREFIX)) {
    fail(`项目标题不以 ${TITLE_PREFIX} 开头，拒绝在非本次测试项目上操作`);
    report.result = { status: "refused_wrong_project", title: project.title || "" };
    write();
    return 3;
  }
  await snapshot(project);

  let plan = planResume(project, { allowedSubmitShotIds: args.allowSubmit });
  assertSingleSubmitPerShot(plan);
  console.log(describePlan(plan));
  report.steps.push({ name: "initial_plan", plan: summarizePlan(plan) });

  if (plan.stop) {
    fail(`镜头失败，停止：不允许自动补发`);
    report.result = {
      status: "stopped_on_failure",
      failures: plan.failures,
      submits_issued: state.submitsIssued.slice(),
      refresh_count: state.refreshCount,
    };
    write();
    return 2;
  }

  if (plan.refresh.length) {
    const job = await refreshOnce(`残留任务 ${plan.refresh.length} 个`);
    report.steps.push({ name: "refresh", job_status: job.status, job_message: job.message || "" });
    info(`回查结束 status=${job.status} ${job.message || ""}`);
    project = await apiGet(`/api/projects/${args.project}`);
    plan = planResume(project, { allowedSubmitShotIds: args.allowSubmit });
    assertSingleSubmitPerShot(plan);
    console.log(describePlan(plan));
    report.steps.push({ name: "plan_after_refresh", plan: summarizePlan(plan) });
    if (plan.stop) {
      fail("回查后发现镜头失败，停止：不补发");
      report.result = {
        status: "stopped_after_refresh",
        failures: plan.failures,
        submits_issued: state.submitsIssued.slice(),
        refresh_count: state.refreshCount,
        cost_visibility: "无法确认",
        platform_cost: "无法确认",
      };
      write();
      return 2;
    }
  }

  for (const item of plan.submits) {
    await submitShotOnce(item.shot_id, item.shot_index);
    report.steps.push({ name: "submit_once", shot_id: item.shot_id, shot_index: item.shot_index });
    await waitShotReady(item.shot_id, item.shot_index);
    info(`镜头 ${item.shot_index} 视频就绪`);
    report.steps.push({ name: "video_ready", shot_id: item.shot_id, shot_index: item.shot_index });
  }

  project = await apiGet(`/api/projects/${args.project}`);
  plan = planResume(project, { allowedSubmitShotIds: args.allowSubmit });
  console.log(describePlan(plan));

  let assembly = { attempted: false, reason: "not_requested" };
  if (args.assemble && plan.can_assemble) {
    assembly = await runAssembly();
    report.steps.push({ name: "assembly", ...assembly });
  } else if (args.assemble) {
    info("尚未满足合成条件，跳过合成");
  }

  const allReady = plan.counts.ready === plan.counts.shots && plan.counts.shots > 0;
  const assembleOk = !args.assemble || assembly.ok === true;
  report.result = {
    status: allReady
      ? args.assemble
        ? assembly.ok
          ? "PASS"
          : "VIDEO_READY_ASSEMBLY_PENDING"
        : "VIDEO_READY"
      : "PARTIAL",
    final_plan: summarizePlan(plan),
    submits_issued: state.submitsIssued.slice(),
    refresh_count: state.refreshCount,
    assembly,
    inflight_remote_tasks: plan.refresh
      .filter((item) => item.reason === "existing_remote_task_id")
      .map((item) => item.remote_task_id),
    cost_visibility: "无法确认",
    platform_cost: "无法确认",
  };
  report.finished_at = new Date().toISOString();
  write();
  info(`报告写入 ${REPORT}`);
  return allReady && assembleOk ? 0 : 1;
}

function summarizePlan(plan) {
  return {
    counts: plan.counts,
    stop: plan.stop,
    can_assemble: plan.can_assemble,
    refresh: plan.refresh.map((item) => ({ shot_id: item.shot_id, reason: item.reason, remote_task_id: item.remote_task_id })),
    submits: plan.submits.map((item) => ({ shot_id: item.shot_id, reason: item.reason })),
    blocked: plan.blocked.map((item) => ({ shot_id: item.shot_id, reason: item.reason })),
    failures: plan.failures,
  };
}

main()
  .then((code) => {
    process.exitCode = Number(code) || 0;
  })
  .catch((error) => {
    console.error(`STOP: ${error.message}`);
    process.exitCode = 1;
  });
