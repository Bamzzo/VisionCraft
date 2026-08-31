/**
 * Live 2-shot test helpers. Pure logic for persist polling, resume, and assembly gates.
 * No Playwright, no network. Required by tools/live_2shot.cjs and Mock tests.
 */
"use strict";

const INFLIGHT_TASK_STATUSES = new Set(["running", "submitted", "pending_remote", "waiting_remote"]);
const INFLIGHT_SHOT_STATUSES = new Set(["video_running", "video_waiting_remote"]);
const FAILED_TASK_STATUSES = new Set(["failed", "error"]);
const FAILED_SHOT_STATUSES = new Set(["video_failed"]);
const TASK_PERSIST_TIMEOUT_MS = 90 * 1000;
const TASK_PERSIST_INTERVAL_MS = 500;
const VIDEO_WAIT_USES_MODE_SELECT = false;

function redact(id) {
  const text = String(id || "");
  if (!text || text.length <= 8) return text;
  return `${text.slice(0, 4)}…${text.slice(-4)}`;
}

function persistTimeoutMessage(shotLabel) {
  return `镜头 ${shotLabel} 任务落库超时`;
}

function extractSubmitHint(body) {
  const payload = body && typeof body === "object" ? body : {};
  const nested = payload.video_task && typeof payload.video_task === "object" ? payload.video_task : {};
  return {
    job_id: payload.job_id || payload.jobId || nested.job_id || "",
    task_id: payload.task_id || payload.video_task_id || nested.id || "",
    remote_task_id: payload.remote_task_id || nested.remote_task_id || "",
    status: payload.status || nested.status || "",
    version_id: payload.version_id || nested.version_id || "",
  };
}

function tasksForShot(project, shotId) {
  return (project.video_tasks || []).filter((task) => task && task.shot_id === shotId);
}

function findShotTask(project, shotId, hint) {
  const tasks = tasksForShot(project, shotId);
  const info = hint || {};
  if (info.task_id) {
    const hit = tasks.find((task) => task.id === info.task_id);
    if (hit) return hit;
  }
  if (info.remote_task_id) {
    const hit = tasks.find((task) => task.remote_task_id === info.remote_task_id);
    if (hit) return hit;
  }
  if (info.job_id) {
    const hit = tasks.find((task) => task.job_id === info.job_id);
    if (hit) return hit;
  }
  if (info.version_id) {
    const hit = tasks.find((task) => task.version_id === info.version_id);
    if (hit) return hit;
  }
  return tasks[0] || null;
}

function videoWaitDecision(project, shotId) {
  const tasks = tasksForShot(project, shotId);
  const withRemote = tasks.find((task) => task.remote_task_id);
  const shot = (project.shots || []).find((item) => item.id === shotId);
  const shotInflight = Boolean(shot && INFLIGHT_SHOT_STATUSES.has(String(shot.status || "")));
  if (withRemote) {
    return { action: "refresh_only", allow_post_video: false, task: withRemote, reason: "existing_remote_task_id" };
  }
  if (tasks.length) {
    return { action: "refresh_only", allow_post_video: false, task: tasks[0], reason: "existing_video_task" };
  }
  if (shotInflight) {
    return { action: "refresh_only", allow_post_video: false, task: null, reason: "shot_inflight" };
  }
  return { action: "submit_once", allow_post_video: true, task: null, reason: "no_task" };
}

function failedJobForShot(project, shotId) {
  return (project.jobs || []).find((job) => job.shot_id === shotId && job.status === "failed");
}

function shotVideoVerdict(project, target) {
  const shotId = target.id;
  const shotNow = (project.shots || []).find((item) => item.id === shotId);
  const status = shotNow?.status || "";
  const tasks = tasksForShot(project, shotId);
  const task = tasks[0];
  const failed = failedJobForShot(project, shotId);
  if (failed) {
    return {
      ready: false,
      failed: true,
      inflight: false,
      task,
      message: `镜头 ${target.shot_index || shotId} 失败：${failed.error_message || failed.message || ""}`,
    };
  }
  if (FAILED_SHOT_STATUSES.has(status) || (task && FAILED_TASK_STATUSES.has(String(task.status || "")))) {
    return {
      ready: false,
      failed: true,
      inflight: false,
      task,
      message: `镜头 ${shotId} 状态 ${status || task.status}`,
    };
  }
  if (status === "video_ready") {
    if (tasks.length !== 1) {
      return { ready: false, failed: true, inflight: false, task, message: `镜头 ${shotId} video_tasks=${tasks.length}，期望 1` };
    }
    if (String(task.status || "") !== "completed") {
      return { ready: false, failed: true, inflight: false, task, message: `镜头 ${shotId} 未完成却标记 video_ready` };
    }
    return { ready: true, failed: false, inflight: false, task };
  }
  const inflight =
    INFLIGHT_SHOT_STATUSES.has(status) || (task && INFLIGHT_TASK_STATUSES.has(String(task.status || "")));
  return { ready: false, failed: false, inflight, task };
}

function canEnterAssembly(project) {
  const shots = project.shots || [];
  if (!shots.length) return false;
  return shots.every((shot) => {
    const verdict = shotVideoVerdict(project, shot);
    return verdict.ready && !verdict.failed && !verdict.inflight;
  });
}

function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForVideoTaskPersist(options) {
  const {
    getProject,
    shotId,
    shotLabel,
    hint,
    timeoutMs = TASK_PERSIST_TIMEOUT_MS,
    intervalMs = TASK_PERSIST_INTERVAL_MS,
    sleep = defaultSleep,
    onPoll,
  } = options;
  const label = shotLabel || shotId;
  const start = Date.now();
  let last = null;
  while (Date.now() - start < timeoutMs) {
    const project = await getProject();
    last = project;
    if (typeof onPoll === "function") onPoll({ project, generate: false });
    const failed = failedJobForShot(project, shotId);
    if (failed) {
      throw new Error(`镜头 ${label} 任务失败：${failed.error_message || failed.message || ""}`);
    }
    const task = findShotTask(project, shotId, hint);
    if (task) return { project, task };
    await sleep(intervalMs);
  }
  const hintBits = hint && (hint.task_id || hint.remote_task_id || hint.job_id)
    ? ` hint=${redact(hint.task_id || hint.remote_task_id || hint.job_id)}`
    : "";
  const lastStatus = ((last && last.shots) || []).find((item) => item.id === shotId)?.status || "";
  throw new Error(`${persistTimeoutMessage(label)}${hintBits} status=${lastStatus}`);
}

async function pollShotVideoReady(options) {
  const {
    getProject,
    refresh,
    target,
    timeoutMs,
    intervalMs = 8000,
    sleep = defaultSleep,
  } = options;
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const project = await getProject();
    const verdict = shotVideoVerdict(project, target);
    if (verdict.failed) throw new Error(verdict.message);
    if (verdict.ready) return project;
    if (typeof refresh === "function") await refresh(project);
    await sleep(intervalMs);
  }
  throw new Error(`镜头 ${target.id} 等待远程任务超时（只回查，未补发）`);
}

module.exports = {
  INFLIGHT_TASK_STATUSES,
  INFLIGHT_SHOT_STATUSES,
  TASK_PERSIST_TIMEOUT_MS,
  TASK_PERSIST_INTERVAL_MS,
  VIDEO_WAIT_USES_MODE_SELECT,
  redact,
  persistTimeoutMessage,
  extractSubmitHint,
  findShotTask,
  tasksForShot,
  videoWaitDecision,
  shotVideoVerdict,
  canEnterAssembly,
  waitForVideoTaskPersist,
  pollShotVideoReady,
};
