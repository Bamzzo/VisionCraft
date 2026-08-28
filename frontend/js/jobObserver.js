/**
 * 当前选中项目的实时监听会话。
 * 用递增 observerToken + observedProjectId 丢弃过期的 SSE / 轮询 / 回查结果。
 */

export function createObserverContext(extras = {}) {
  return {
    observerToken: 0,
    observedProjectId: null,
    eventSource: null,
    pollTimer: null,
    remoteRefreshTimer: null,
    sseConnected: false,
    refreshInFlight: false,
    lastEventId: 0,
    jobEvents: [],
    shotProgress: {},
    project: extras.project || null,
  };
}

export function stopObservation(ctx, timers = globalTimers()) {
  if (ctx.eventSource) {
    ctx.eventSource.close?.();
    ctx.eventSource = null;
  }
  if (ctx.pollTimer) {
    timers.clearInterval(ctx.pollTimer);
    ctx.pollTimer = null;
  }
  if (ctx.remoteRefreshTimer) {
    timers.clearInterval(ctx.remoteRefreshTimer);
    ctx.remoteRefreshTimer = null;
  }
  ctx.sseConnected = false;
  ctx.refreshInFlight = false;
}

export function beginObservation(ctx, projectId, timers = globalTimers()) {
  stopObservation(ctx, timers);
  ctx.observerToken = Number(ctx.observerToken || 0) + 1;
  ctx.observedProjectId = projectId || null;
  ctx.lastEventId = 0;
  ctx.jobEvents = [];
  ctx.shotProgress = {};
  return ctx.observerToken;
}

export function isLiveSession(ctx, token, projectId) {
  if (!projectId) return false;
  return Number(ctx.observerToken) === Number(token) && ctx.observedProjectId === projectId;
}

export function rememberJobEvent(ctx, event, token) {
  if (!event) return { applied: false, reason: "empty" };
  const projectId = event.project_id || ctx.observedProjectId;
  if (!isLiveSession(ctx, token, projectId)) {
    return { applied: false, reason: "stale-session" };
  }
  if (event.project_id && event.project_id !== ctx.observedProjectId) {
    return { applied: false, reason: "project-mismatch" };
  }
  const eventId = Number(event.id);
  if (Number.isFinite(eventId) && ctx.jobEvents.some((item) => Number(item.id) === eventId)) {
    return { applied: false, reason: "duplicate" };
  }
  ctx.jobEvents = [...ctx.jobEvents, event].sort((a, b) => Number(a.id) - Number(b.id));
  if (Number.isFinite(eventId)) ctx.lastEventId = Math.max(Number(ctx.lastEventId) || 0, eventId);
  if (event.shot_id) ctx.shotProgress[event.shot_id] = event;
  return { applied: true, reason: "ok" };
}

export function shouldWatchProject(project) {
  const jobs = project?.active_jobs?.length ? project.active_jobs : project?.jobs || [];
  const active = jobs.some((job) => ["queued", "running", "waiting_remote", "paused"].includes(job.status));
  const waiting =
    jobs.some((job) => job.status === "waiting_remote") ||
    (project?.shots || []).some(
      (shot) =>
        ["video_waiting_remote", "video_running"].includes(shot.status) ||
        ["running", "pending_remote"].includes(shot.active_video_task?.status)
    );
  return { active, waiting, watch: active || waiting };
}

function globalTimers() {
  return {
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
  };
}
