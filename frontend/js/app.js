import { api } from "./api.js";
import {
  beginObservation,
  isLiveSession,
  rememberJobEvent,
  shouldWatchProject,
  stopObservation,
} from "./jobObserver.js";
import { renderAll, renderCapabilities, renderFeedbackResult, currentVideoDraftPayload } from "./render.js";
import { selectedShot, state } from "./state.js";

const el = (id) => document.getElementById(id);

async function init() {
  bindEvents();
  await checkHealth();
  state.capabilities = await api.capabilities();
  state.diagnostics = await api.diagnostics();
  renderCapabilities();
  await loadProjects();
  renderAll();
}

function bindEvents() {
  el("projectForm").addEventListener("submit", onCreateProject);
  el("runWorkflowBtn").addEventListener("click", onRunWorkflow);
  el("refreshBtn").addEventListener("click", refreshProject);
  el("deleteProjectBtn").addEventListener("click", onDeleteProject);
  el("cleanupDemoBtn").addEventListener("click", onCleanupDemoData);
  el("sendFeedbackBtn").addEventListener("click", onSendFeedback);
  el("memorySearchBtn").addEventListener("click", onSearchMemory);
  el("shotInspector").addEventListener("click", onInspectorClick);
  el("shotInspector").addEventListener("change", onInspectorChange);
  el("shotInspector").addEventListener("input", onInspectorChange);
  el("generateAllVideosBtn").addEventListener("click", onGenerateAllVideos);
  el("assembleProjectBtn").addEventListener("click", onAssembleProject);
  el("resumeWorkflowBtn").addEventListener("click", onResumeWorkflow);
  el("retryWorkflowBtn").addEventListener("click", onRetryWorkflow);
  el("exportJsonBtn").addEventListener("click", () => exportProject("json"));
  el("exportMdBtn").addEventListener("click", () => exportProject("markdown"));
  el("jobTimelineToggle").addEventListener("click", () => {
    state.timelineOpen = !state.timelineOpen;
    renderAll();
  });
  el("shotModeInput").addEventListener("change", () => {
    el("manualShotField").classList.toggle("hidden", el("shotModeInput").value !== "manual");
  });
  bindTextUpload();
  // 项目列表和分镜列表会反复重绘，事件委托能保持按钮绑定稳定。
  el("projectList").addEventListener("click", async (event) => {
    const item = event.target.closest("[data-project-id]");
    if (!item) return;
    const nextId = item.dataset.projectId;
    if (state.project?.id === nextId) return;
    await flushShotDraft();
    const token = startSessionForProject(nextId);
    try {
      const project = await api.getProject(nextId);
      if (!isLiveSession(state, token, nextId)) return;
      state.project = project;
      state.selectedShotId = state.project.shots?.[0]?.id || null;
      state.memoryResults = [];
      state.videoDraft = null;
      renderFeedbackResult(null);
      restoreJobObservation();
      renderAll();
    } catch (error) {
      if (isLiveSession(state, token, nextId)) showError(`加载项目失败：${error.message}`);
    }
  });
  el("shotGrid").addEventListener("click", async (event) => {
    const item = event.target.closest("[data-shot-id]");
    if (!item) return;
    if (item.dataset.shotId === state.selectedShotId) return;
    await flushShotDraft();
    state.selectedShotId = item.dataset.shotId;
    state.videoDraft = null;
    renderAll();
  });
}

function bindTextUpload() {
  const dropzone = el("textDropzone");
  const fileInput = el("textFileInput");
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) readSourceFile(file);
  });
  ["dragenter", "dragover"].forEach((type) => {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.remove("drag-over");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) readSourceFile(file);
  });
}

function readSourceFile(file) {
  const allowed = /\.(txt|md|markdown|json)$/i.test(file.name) || file.type.startsWith("text/") || file.type === "application/json";
  if (!allowed) {
    showError("请上传 .txt、.md 或 .json 文本文件。");
    return;
  }
  const reader = new FileReader();
  el("uploadStatus").textContent = `读取中：${file.name}`;
  reader.onload = () => {
    const content = String(reader.result || "").trim();
    if (content.length < 5) {
      showError("文件内容太短，至少需要 5 个字符。");
      el("uploadStatus").textContent = "文件内容太短";
      return;
    }
    el("sourceTextInput").value = content;
    if (!el("titleInput").value.trim()) {
      el("titleInput").value = file.name.replace(/\.[^.]+$/, "");
    }
    el("uploadStatus").textContent = `已载入：${file.name} · ${content.length} 字符`;
    renderFeedbackResult(null);
  };
  reader.onerror = () => {
    showError("文件读取失败，请确认文件是 UTF-8 文本。");
    el("uploadStatus").textContent = "读取失败";
  };
  reader.readAsText(file, "utf-8");
}

async function checkHealth() {
  const health = await api.health();
  el("connectionStatus").textContent = health.ok ? "本地服务已连接" : "服务异常";
}

async function loadProjects() {
  state.projects = await api.listProjects();
  if (!state.project && state.projects.length) {
    const token = startSessionForProject(state.projects[0].id);
    state.project = await api.getProject(state.projects[0].id);
    if (!isLiveSession(state, token, state.projects[0].id)) return;
    state.selectedShotId = state.project.shots?.[0]?.id || null;
  }
  restoreJobObservation();
}

async function onCreateProject(event) {
  event.preventDefault();
  const mode = el("shotModeInput").value;
  const sourceText = el("sourceTextInput").value.trim();
  if (sourceText.length < 5) {
    showError("故事文本至少需要 5 个字符。");
    return;
  }
  const payload = {
    title: el("titleInput").value.trim(),
    source_text: sourceText,
    style: el("styleInput").value,
    aspect_ratio: el("ratioInput").value,
    duration_seconds: Number(el("durationInput").value),
    shot_count_mode: mode,
    requested_shot_count: mode === "manual" ? Number(el("shotCountInput").value) : null,
    review_mode: el("reviewModeInput").checked,
  };
  try {
    await flushShotDraft();
    const created = await api.createProject(payload);
    startSessionForProject(created.id);
    state.project = created;
    state.selectedShotId = null;
    await loadProjects();
    renderFeedbackResult(null);
    renderAll();
  } catch (error) {
    showError(`创建失败：${error.message}`);
  }
}

async function onRunWorkflow() {
  if (!state.project) return;
  try {
    const result = await api.runProject(state.project.id);
    attachEvents();
    el("jobMessage").textContent = `任务 ${result.job_id} 已入队`;
    await refreshProject({ preserveObservation: true });
  } catch (error) {
    showError(`启动失败：${error.message}`);
  }
}

function observerTimers() {
  return {
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
  };
}

function stopJobObservation() {
  stopObservation(state, observerTimers());
}

function startSessionForProject(projectId) {
  return beginObservation(state, projectId, observerTimers());
}

async function flushShotDraft() {
  const shot = selectedShot();
  if (!state.project || !shot || !state.videoDraft || state.videoDraft.shotId !== shot.id) return;
  if (!state.videoDraft.dirty && !collectInspectorDraft()) return;
  try {
    const payload = collectInspectorDraft() || state.videoDraft;
    if (typeof api.saveShotDraft === "function") {
      await api.saveShotDraft(state.project.id, shot.id, payload);
    }
  } catch (error) {
    console.warn("自动保存镜头草稿失败", error);
  }
}

function collectInspectorDraft() {
  const description = document.getElementById("shotDescriptionInput");
  if (!description && !document.getElementById("videoModeSelect")) return null;
  return {
    description: description?.value,
    camera_motion: document.getElementById("shotCameraInput")?.value,
    visual_prompt: document.getElementById("shotVisualPromptInput")?.value,
    video_mode: document.getElementById("videoModeSelect")?.value,
    provider: document.getElementById("videoProviderSelect")?.value,
    model: document.getElementById("videoModelSelect")?.value,
    duration_seconds: Number(document.getElementById("videoDurationSelect")?.value || state.project?.duration_seconds || 5),
    first_frame_path: document.getElementById("firstFrameSelect")?.value || null,
    last_frame_path: document.getElementById("lastFrameSelect")?.value || null,
    reference_frame_path: document.getElementById("referenceFrameSelect")?.value || null,
  };
}

function attachEvents() {
  if (!state.project) return;
  startEventStream();
  startRemoteRefreshWatch();
}

function startEventStream() {
  if (!state.project) return;
  const token = state.observerToken;
  const projectId = state.project.id;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  const source = new EventSource(`/api/projects/${projectId}/events?after_id=${state.lastEventId || 0}`);
  state.eventSource = source;
  const onEvent = async (event) => {
    if (!isLiveSession(state, token, projectId) || state.eventSource !== source) return;
    state.sseConnected = true;
    stopEventPolling();
    if (!event.data) return;
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (event.type === "snapshot" || Array.isArray(payload.jobs)) {
      applyJobSnapshot(payload);
      renderAll();
      return;
    }
    await applyJobEvent(payload, { token, projectId });
  };
  ["snapshot", "job.update", "asset.ready", "job.failed", "project.refresh_required"].forEach((type) => {
    source.addEventListener(type, onEvent);
  });
  source.onmessage = onEvent;
  source.onerror = () => {
    if (!isLiveSession(state, token, projectId) || state.eventSource !== source) return;
    state.sseConnected = false;
    source.close();
    if (state.eventSource === source) state.eventSource = null;
    startEventPolling();
  };
}

function startEventPolling() {
  if (state.pollTimer) return;
  state.pollTimer = window.setInterval(pollJobEvents, 4000);
  pollJobEvents();
}

function stopEventPolling() {
  if (!state.pollTimer) return;
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function pollJobEvents() {
  if (!state.project) {
    stopEventPolling();
    return;
  }
  const token = state.observerToken;
  const projectId = state.project.id;
  try {
    const snapshot = await api.jobEvents(projectId, state.lastEventId || 0);
    if (!isLiveSession(state, token, projectId)) return;
    applyJobSnapshot(snapshot);
    for (const event of snapshot.events || []) {
      await applyJobEvent(event, { skipRender: true, token, projectId });
    }
    renderAll();
    if (!shouldWatchProject(state.project).watch) {
      stopEventPolling();
      stopRemoteRefreshWatch();
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }
      state.sseConnected = false;
    }
  } catch (error) {
    if (isLiveSession(state, token, projectId)) console.warn("任务事件轮询失败", error);
  }
}

function applyJobSnapshot(payload) {
  if (!state.project || !payload) return;
  if (payload.project_id && payload.project_id !== state.project.id) return;
  if (payload.jobs) {
    state.project.active_jobs = payload.jobs;
    const byId = new Map((state.project.jobs || []).map((job) => [job.id, job]));
    payload.jobs.forEach((job) => byId.set(job.id, { ...byId.get(job.id), ...job }));
    state.project.jobs = [...byId.values()].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  }
}

async function applyJobEvent(event, options = {}) {
  const token = options.token ?? state.observerToken;
  const projectId = options.projectId || event?.project_id || state.project?.id;
  const result = rememberJobEvent(state, event, token);
  if (!result.applied) return;
  if (state.project && event.job_id) {
    const jobs = [...(state.project.jobs || [])];
    const index = jobs.findIndex((job) => job.id === event.job_id);
    const snapshot = {
      id: event.job_id,
      project_id: event.project_id,
      status: event.status,
      progress: event.progress,
      message: event.message,
      stage: event.stage,
      shot_id: event.shot_id,
      updated_at: event.created_at,
    };
    if (index >= 0) jobs[index] = { ...jobs[index], ...snapshot };
    else jobs.unshift(snapshot);
    state.project.jobs = jobs;
  }
  const shouldRefreshProject = ["asset.ready", "project.refresh_required", "job.failed"].includes(event.event_type);
  if (shouldRefreshProject) {
    await refreshProject({ preserveObservation: true, token, projectId });
    return;
  }
  if (!options.skipRender) renderAll();
}

function startRemoteRefreshWatch() {
  if (state.remoteRefreshTimer) return;
  state.remoteRefreshTimer = window.setInterval(maybeRefreshRemoteTasks, 25000);
}

function stopRemoteRefreshWatch() {
  if (!state.remoteRefreshTimer) return;
  window.clearInterval(state.remoteRefreshTimer);
  state.remoteRefreshTimer = null;
}

async function maybeRefreshRemoteTasks() {
  if (!state.project || state.refreshInFlight || !shouldWatchProject(state.project).waiting) return;
  const token = state.observerToken;
  const projectId = state.project.id;
  const refreshBusy = (state.project.jobs || []).some(
    (job) => job.type === "video_task_refresh" && ["queued", "running"].includes(job.status)
  );
  if (refreshBusy) return;
  try {
    state.refreshInFlight = true;
    await api.refreshVideoTasks(projectId);
  } catch (error) {
    if (isLiveSession(state, token, projectId)) console.warn("云端任务回查失败", error);
  } finally {
    if (isLiveSession(state, token, projectId)) state.refreshInFlight = false;
    else state.refreshInFlight = false;
  }
}

function restoreJobObservation() {
  if (!state.project) {
    stopJobObservation();
    return;
  }
  const events = state.project.job_events || [];
  state.jobEvents = events;
  state.lastEventId = events.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0);
  state.shotProgress = {};
  events.forEach((event) => {
    if (event.shot_id) state.shotProgress[event.shot_id] = event;
  });
  if (shouldWatchProject(state.project).watch) {
    attachEvents();
  } else {
    stopEventPolling();
    stopRemoteRefreshWatch();
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    state.sseConnected = false;
  }
}

async function refreshProject(options = {}) {
  if (!state.project) return;
  const token = options.token ?? state.observerToken;
  const projectId = options.projectId || state.project.id;
  const project = await api.getProject(projectId);
  if (state.project?.id !== projectId || (token != null && Number(state.observerToken) !== Number(token))) {
    return;
  }
  state.project = project;
  state.diagnostics = await api.diagnostics();
  if (state.project?.id !== projectId) return;
  if (!state.selectedShotId) {
    state.selectedShotId = state.project.shots?.[0]?.id || null;
  }
  state.projects = await api.listProjects();
  if (state.project?.id !== projectId) return;
  const incoming = state.project.job_events || [];
  incoming.forEach((event) => rememberJobEvent(state, event, state.observerToken));
  renderAll();
  if (!options.preserveObservation && shouldWatchProject(state.project).watch) {
    attachEvents();
  }
}

async function onSendFeedback() {
  const shot = selectedShot();
  const text = el("feedbackInput").value.trim();
  if (!state.project || !shot || !text) return;
  try {
    const result = await api.sendFeedback(state.project.id, shot.id, text);
    renderFeedbackResult(result);
    el("feedbackInput").value = "";
    await refreshProject();
  } catch (error) {
    showError(`反馈失败：${error.message}`);
  }
}

async function onSearchMemory() {
  const query = el("memoryQueryInput").value.trim();
  if (!state.project || !query) return;
  try {
    const result = await api.searchMemory(state.project.id, query);
    state.memoryResults = result.items || [];
    renderAll();
  } catch (error) {
    showError(`记忆检索失败：${error.message}`);
  }
}

function onInspectorChange(event) {
  const target = event.target;
  const ids = [
    "videoModeSelect",
    "videoProviderSelect",
    "videoModelSelect",
    "videoDurationSelect",
    "shotDescriptionInput",
    "shotCameraInput",
    "shotVisualPromptInput",
    "firstFrameSelect",
    "lastFrameSelect",
    "referenceFrameSelect",
  ];
  if (!target || !ids.includes(target.id)) return;
  const shot = selectedShot();
  if (!shot) return;
  const collected = collectInspectorDraft();
  state.videoDraft = {
    ...(state.videoDraft || {}),
    ...collected,
    shotId: shot.id,
    dirty: true,
  };
  if (target.id === "videoModeSelect" || target.id === "videoProviderSelect") {
    state.videoDraft.model = "";
    renderAll();
  }
}

async function onInspectorClick(event) {
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  const shot = selectedShot();
  if (!state.project || !shot) return;
  if (trigger.dataset.action === "rollback-version") {
    await onRollbackVersion(trigger.dataset.versionId);
    return;
  }
  if (trigger.dataset.action === "refresh-video-tasks") {
    await onRefreshVideoTasks(trigger);
    return;
  }
  if (trigger.dataset.action === "safe-retry-video") {
    await onSafeRetryVideo(trigger);
    return;
  }
  if (trigger.dataset.action === "save-shot-draft") {
    await onSaveShotDraft(trigger);
    return;
  }
  if (trigger.dataset.action === "freeze-shot-version") {
    await onFreezeShotVersion(trigger);
    return;
  }
  if (trigger.dataset.action === "apply-keyframes") {
    await onApplyKeyframes(trigger);
    return;
  }
  if (trigger.dataset.action === "redraw-keyframe") {
    await onRedrawKeyframe(trigger);
    return;
  }
  // 视频模式控件位于会重绘的监制面板中，点击时读取最稳。
  if (trigger.dataset.action !== "generate-video") return;
  try {
    trigger.disabled = true;
    trigger.textContent = "视频任务已提交";
    const result = await api.generateVideo(state.project.id, shot.id, currentVideoDraftPayload());
    attachEvents();
    el("jobMessage").textContent = `镜头 ${shot.title} 视频任务 ${result.job_id} 已入队（版本 ${result.version_id}）`;
    await refreshProject();
  } catch (error) {
    showError(`视频生成失败：${error.message}`);
  }
}

async function onSafeRetryVideo(trigger) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  try {
    trigger.disabled = true;
    trigger.textContent = "安全重试已提交";
    const result = await api.safeRetryVideo(state.project.id, shot.id);
    attachEvents();
    el("jobMessage").textContent = `安全重试任务 ${result.job_id} 已入队`;
    await refreshProject();
  } catch (error) {
    showError(`安全重试失败：${error.message}`);
  }
}

async function onSaveShotDraft(trigger) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  try {
    trigger.disabled = true;
    trigger.textContent = "正在保存草稿";
    await api.saveShotDraft(state.project.id, shot.id, collectInspectorDraft() || currentVideoDraftPayload());
    if (state.videoDraft) state.videoDraft.dirty = false;
    await refreshProject({ preserveObservation: true });
  } catch (error) {
    showError(`保存镜头草稿失败：${error.message}`);
  }
}

async function onFreezeShotVersion(trigger) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  try {
    trigger.disabled = true;
    trigger.textContent = "正在冻结版本";
    const result = await api.freezeShotVersion(state.project.id, shot.id, collectInspectorDraft() || currentVideoDraftPayload());
    if (state.videoDraft) state.videoDraft.dirty = false;
    el("jobMessage").textContent = result.created
      ? `已创建镜头版本 v${result.version_number}`
      : result.reason || "没有实质修改，未创建新版本";
    await refreshProject({ preserveObservation: true });
  } catch (error) {
    showError(`创建镜头版本失败：${error.message}`);
  }
}

async function onApplyKeyframes(trigger) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  const firstFramePath = document.getElementById("firstFrameSelect")?.value || null;
  const lastFramePath = document.getElementById("lastFrameSelect")?.value || null;
  if (!firstFramePath && !lastFramePath) {
    showError("请选择至少一个关键帧资产。");
    return;
  }
  try {
    trigger.disabled = true;
    trigger.textContent = "正在应用关键帧";
    await api.saveShotDraft(state.project.id, shot.id, {
      first_frame_path: firstFramePath || null,
      last_frame_path: lastFramePath || null,
    });
    if (state.videoDraft) state.videoDraft.dirty = false;
    await refreshProject();
  } catch (error) {
    showError(`关键帧选择失败：${error.message}`);
  }
}

async function onRedrawKeyframe(trigger) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  const target = trigger.dataset.target || "both";
  try {
    trigger.disabled = true;
    trigger.textContent = "正在重绘";
    await api.redrawKeyframes(state.project.id, shot.id, target);
    await refreshProject();
  } catch (error) {
    showError(`关键帧重绘失败：${error.message}`);
  }
}

async function onRefreshVideoTasks(trigger) {
  if (!state.project) return;
  try {
    trigger.disabled = true;
    trigger.textContent = "正在回查云端任务";
    const result = await api.refreshVideoTasks(state.project.id);
    attachEvents();
    el("jobMessage").textContent = `Seedance 回查任务 ${result.job_id} 已入队`;
    await refreshProject();
  } catch (error) {
    showError(`Seedance 回查失败：${error.message}`);
  }
}

async function onRollbackVersion(versionId) {
  const shot = selectedShot();
  if (!state.project || !shot || !versionId) return;
  try {
    state.project = await api.rollbackVersion(state.project.id, shot.id, versionId);
    state.selectedShotId = shot.id;
    renderFeedbackResult({ scope: "local", reason: "已回滚到选中的镜头版本。", positive_prompt: versionId });
    renderAll();
  } catch (error) {
    showError(`版本回滚失败：${error.message}`);
  }
}

async function onGenerateAllVideos() {
  if (!state.project) return;
  try {
    el("generateAllVideosBtn").disabled = true;
    const result = await api.generateAllVideos(state.project.id);
    attachEvents();
    el("jobMessage").textContent = `批量视频任务 ${result.job_id} 已入队`;
    await refreshProject();
  } catch (error) {
    showError(`批量视频生成失败：${error.message}`);
  } finally {
    el("generateAllVideosBtn").disabled = false;
  }
}

async function onAssembleProject() {
  if (!state.project) return;
  try {
    el("assembleProjectBtn").disabled = true;
    const result = await api.assembleProject(state.project.id);
    attachEvents();
    el("jobMessage").textContent = `成片合成任务 ${result.job_id} 已入队`;
    await refreshProject();
  } catch (error) {
    showError(`成片合成失败：${error.message}`);
  } finally {
    el("assembleProjectBtn").disabled = false;
  }
}

async function onResumeWorkflow() {
  if (!state.project) return;
  try {
    el("resumeWorkflowBtn").disabled = true;
    const result = await api.resumeProject(state.project.id);
    attachEvents();
    el("jobMessage").textContent = `恢复任务 ${result.job_id} 已继续`;
    await refreshProject();
  } catch (error) {
    showError(`恢复流程失败：${error.message}`);
  } finally {
    el("resumeWorkflowBtn").disabled = false;
  }
}

async function onRetryWorkflow() {
  if (!state.project) return;
  try {
    el("retryWorkflowBtn").disabled = true;
    const result = await api.retryProject(state.project.id);
    attachEvents();
    el("jobMessage").textContent = `重试任务 ${result.job_id} 已入队`;
    await refreshProject();
  } catch (error) {
    showError(`重试流程失败：${error.message}`);
  } finally {
    el("retryWorkflowBtn").disabled = false;
  }
}

function exportProject(kind) {
  if (!state.project) return;
  const path = kind === "json" ? "json" : "markdown";
  window.open(`/api/projects/${state.project.id}/export/${path}`, "_blank");
}

async function onDeleteProject() {
  if (!state.project) return;
  const ok = window.confirm(`确认删除项目「${state.project.title}」及其本地资产吗？`);
  if (!ok) return;
    try {
    await api.deleteProject(state.project.id);
    stopJobObservation();
    startSessionForProject(null);
    state.project = null;
    state.selectedShotId = null;
    state.memoryResults = [];
    await loadProjects();
    renderFeedbackResult(null);
    renderAll();
  } catch (error) {
    showError(`删除失败：${error.message}`);
  }
}

async function onCleanupDemoData() {
  const ok = window.confirm("整理演示数据会移除无效占位视频引用，并归档失败或空项目；当前选中的项目会保留。继续吗？");
  if (!ok) return;
  try {
    el("cleanupDemoBtn").disabled = true;
    const result = await api.cleanupDemoData(state.project?.id || null);
    state.projects = await api.listProjects();
    if (state.project) {
      state.project = await api.getProject(state.project.id);
    } else if (state.projects.length) {
      state.project = await api.getProject(state.projects[0].id);
    }
    renderFeedbackResult({
      scope: "local",
      reason: `已移除 ${result.removed_invalid_video_assets} 个无效视频资产，归档 ${result.archived_projects.length} 个项目。`,
      positive_prompt: "演示数据已整理",
    });
    renderAll();
  } catch (error) {
    showError(`整理演示数据失败：${error.message}`);
  } finally {
    el("cleanupDemoBtn").disabled = false;
  }
}

init().catch((error) => {
  console.error(error);
  el("connectionStatus").textContent = "服务未连接";
  el("connectionStatus").className = "status-pill danger";
});

function showError(message) {
  el("feedbackResult").innerHTML = `<div class="prompt-block"><strong>操作提示</strong><br />${escapeHtml(message)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
