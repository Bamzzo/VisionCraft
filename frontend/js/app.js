import { api } from "./api.js";
import {
  beginObservation,
  isLiveSession,
  rememberJobEvent,
  shouldWatchProject,
  stopObservation,
} from "./jobObserver.js";
import { renderAll, renderCapabilities, renderFeedbackResult, currentVideoDraftPayload } from "./render.js";
import { selectedShot, state, resetViewState } from "./state.js";
import { computeWorkflow, resolveStageId } from "./workflowViewModel.js";

const el = (id) => document.getElementById(id);

const LAST_PROJECT_KEY = "vc:lastProjectId";
const VIEW_STORAGE_PREFIX = "vc:view:";
let flowBusy = false;

function persistLastProject(projectId) {
  try {
    if (projectId) sessionStorage.setItem(LAST_PROJECT_KEY, projectId);
    else sessionStorage.removeItem(LAST_PROJECT_KEY);
  } catch {
    /* sessionStorage 不可用时忽略 */
  }
}

function persistViewState() {
  const projectId = state.project?.id;
  if (!projectId) return;
  try {
    sessionStorage.setItem(
      VIEW_STORAGE_PREFIX + projectId,
      JSON.stringify({ viewStage: state.viewStage, selectedAsset: state.selectedAsset })
    );
    persistLastProject(projectId);
  } catch {
    /* sessionStorage 不可用时忽略 */
  }
}

function restoreViewState(project) {
  if (!project?.id) return;
  try {
    const raw = sessionStorage.getItem(VIEW_STORAGE_PREFIX + project.id);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved?.viewStage) state.viewStage = resolveStageId(saved.viewStage, project);
    if (saved?.selectedAsset) state.selectedAsset = saved.selectedAsset;
  } catch {
    /* 损坏的缓存不影响加载 */
  }
}

async function init() {
  bindEvents();
  await checkHealth();
  state.capabilities = await api.capabilities();
  state.diagnostics = await api.diagnostics();
  renderCapabilities();
  await loadProjects();
  // 无项目时进入新建表单态；有项目时展示摘要态。
  state.projectFormMode = state.project ? "summary" : "create";
  renderAll();
}

/* ================================================================== */
/* 事件绑定                                                             */
/* ================================================================== */

function bindEvents() {
  // 项目表单：新建 / 创建 / 编辑 / 取消
  el("newProjectBtn").addEventListener("click", onNewProject);
  el("projectForm").addEventListener("submit", onSubmitProjectForm);
  el("cancelFormBtn").addEventListener("click", onCancelForm);
  el("editProjectBtn").addEventListener("click", onEditProject);
  el("projectForm").addEventListener("input", () => {
    if (state.projectFormMode === "create" || state.projectFormMode === "edit") state.formTouched = true;
  });

  // 工作流控制
  el("runWorkflowBtn").addEventListener("click", onRunWorkflow);
  el("resumeWorkflowBtn").addEventListener("click", onResumeWorkflow);
  el("retryWorkflowBtn").addEventListener("click", onRetryWorkflow);

  // 顶栏
  el("refreshBtn").addEventListener("click", refreshProject);
  el("deleteProjectBtn").addEventListener("click", onDeleteProject);
  el("cleanupDemoBtn").addEventListener("click", onCleanupDemoData);
  el("exportJsonBtn").addEventListener("click", () => exportProject("json"));
  el("exportMdBtn").addEventListener("click", () => exportProject("markdown"));

  // 全局工具
  el("sendFeedbackBtn").addEventListener("click", onSendFeedback);
  el("memorySearchBtn").addEventListener("click", onSearchMemory);

  // 任务中心
  el("taskCenterToggle").addEventListener("click", () => {
    state.timelineOpen = !state.timelineOpen;
    renderAll();
  });

  // 新建项目镜头策略
  el("shotModeInput").addEventListener("change", () => {
    el("manualShotField").classList.toggle("hidden", el("shotModeInput").value !== "manual");
  });
  bindTextUpload();

  // 右侧阶段导航：只切换查看阶段，不触发任务。
  el("stageNav").addEventListener("click", (event) => {
    const node = event.target.closest("[data-stage-id]");
    if (!node) return;
    setViewStage(node.dataset.stageId);
  });

  // 中间工作区：视图切换 + 素材选择 + 阶段操作
  el("stageViewToggle").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-view]");
    if (!btn) return;
    state.assetViewMode[state.viewStage] = btn.dataset.view;
    renderAll();
  });
  el("stageWorkspace").addEventListener("click", onWorkspaceClick);
  el("stageWorkspace").addEventListener("input", onWorkspaceInput);
  el("stageWorkspace").addEventListener("change", onWorkspaceInput);

  // 单素材详情 / 镜头编辑器
  el("assetDetail").addEventListener("click", onInspectorClick);
  el("assetDetail").addEventListener("change", onInspectorChange);
  el("assetDetail").addEventListener("input", onInspectorChange);

  // 阶段门禁（自动/监制）
  el("stageGateBanner").addEventListener("click", onFlowAction);

  // 项目列表切换（含未保存守卫）
  el("projectList").addEventListener("click", onProjectListClick);

  // 未保存修改对话框
  el("unsavedSaveBtn").addEventListener("click", () => resolveUnsaved("save"));
  el("unsavedDiscardBtn").addEventListener("click", () => resolveUnsaved("discard"));
  el("unsavedCancelBtn").addEventListener("click", () => resolveUnsaved("cancel"));
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
    state.formTouched = true;
    el("uploadStatus").textContent = `已载入：${file.name} · ${content.length} 字符`;
    renderFeedbackResult(null);
  };
  reader.onerror = () => {
    showError("文件读取失败，请确认文件是 UTF-8 文本。");
    el("uploadStatus").textContent = "读取失败";
  };
  reader.readAsText(file, "utf-8");
}

/* ================================================================== */
/* 项目：新建 / 创建 / 编辑 / 查看分离                                   */
/* ================================================================== */

function onNewProject() {
  // 新建项目：只清空表单进入空白态，不创建数据库项目，不影响当前项目。
  state.projectFormMode = "create";
  state.formTouched = false;
  resetProjectForm();
  renderAll();
}

function resetProjectForm() {
  el("titleInput").value = "";
  el("sourceTextInput").value = "";
  el("uploadStatus").textContent = "未选择文件";
  el("styleInput").selectedIndex = 0;
  el("shotModeInput").value = "auto";
  el("manualShotField").classList.add("hidden");
  el("shotCountInput").value = 4;
  el("reviewModeInput").checked = false;
  if (el("ratioInput").options.length) el("ratioInput").selectedIndex = 0;
  if (el("durationInput").options.length) el("durationInput").selectedIndex = 0;
  if (el("resolutionInput")?.options.length) setSelectValue(el("resolutionInput"), "1280x720");
}

function onEditProject() {
  if (!state.project) return;
  state.projectFormMode = "edit";
  state.formTouched = false;
  populateFormFromProject(state.project);
  renderAll();
}

function populateFormFromProject(project) {
  el("titleInput").value = project.title || "";
  el("sourceTextInput").value = project.source_text || "";
  el("styleInput").value = project.style || el("styleInput").value;
  el("shotModeInput").value = project.shot_count_mode || "auto";
  el("manualShotField").classList.toggle("hidden", project.shot_count_mode !== "manual");
  el("shotCountInput").value = project.requested_shot_count || 4;
  el("reviewModeInput").checked = Boolean(project.review_mode);
  setSelectValue(el("generationModeInput"), project.generation_mode || "mock");
  setSelectValue(el("ratioInput"), project.aspect_ratio);
  setSelectValue(el("durationInput"), project.duration_seconds);
  setSelectValue(el("resolutionInput"), project.output_resolution || "1280x720");
  el("uploadStatus").textContent = "已载入当前项目原文";
}

function setSelectValue(select, value) {
  if (value === undefined || value === null) return;
  const match = [...select.options].find((opt) => opt.value === String(value) || opt.value === String(Number(value)));
  if (match) select.value = match.value;
}

function onCancelForm() {
  // 取消新建/编辑：有项目则回到摘要态，丢弃未提交输入。
  state.formTouched = false;
  state.projectFormMode = state.project ? "summary" : "create";
  renderAll();
}

async function onSubmitProjectForm(event) {
  event.preventDefault();
  if (state.projectFormMode === "edit") {
    await onSaveProjectSettings();
    return;
  }
  await onCreateProject();
}

function collectProjectSettingsPayload() {
  return {
    title: el("titleInput").value.trim(),
    aspect_ratio: el("ratioInput").value,
    duration_seconds: Number(el("durationInput").value),
    output_resolution: el("resolutionInput")?.value || "1280x720",
  };
}

async function onSaveProjectSettings() {
  if (!state.project) return;
  const payload = collectProjectSettingsPayload();
  if (!payload.title) {
    showError("项目名称不能为空。");
    return;
  }
  try {
    await flushShotDraft();
    const updated = await api.patchProject(state.project.id, payload);
    state.project = updated;
    state.projectFormMode = "summary";
    state.formTouched = false;
    await loadProjects();
    renderFeedbackResult(null);
    renderAll();
    showSuccess("项目设置已保存");
  } catch (error) {
    showError(`保存项目设置失败：${error.message}`);
  }
}

async function onCreateProject() {
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
    output_resolution: el("resolutionInput")?.value || "1280x720",
    shot_count_mode: mode,
    requested_shot_count: mode === "manual" ? Number(el("shotCountInput").value) : null,
    review_mode: el("reviewModeInput").checked,
    generation_mode: el("generationModeInput")?.value || "mock",
  };
  try {
    await flushShotDraft();
    const created = await api.createProject(payload);
    // 创建成功：自动选中新项目并恢复项目摘要。
    startSessionForProject(created.id);
    resetViewState();
    state.project = created;
    state.selectedShotId = null;
    state.projectFormMode = "summary";
    state.formTouched = false;
    persistViewState();
    await loadProjects();
    renderFeedbackResult(null);
    renderAll();
    showSuccess("项目已创建");
  } catch (error) {
    showError(`创建失败：${error.message}`);
  }
}

async function onProjectListClick(event) {
  const item = event.target.closest("[data-project-id]");
  if (!item) return;
  const nextId = item.dataset.projectId;
  if (state.project?.id === nextId) return;
  // 未保存守卫：新建表单草稿或镜头草稿未保存时不静默覆盖。
  const guard = await guardUnsavedChanges();
  if (guard === "cancel") return;
  await switchProject(nextId);
}

async function switchProject(nextId) {
  await flushShotDraft();
  const token = startSessionForProject(nextId);
  try {
    const project = await api.getProject(nextId);
    if (!isLiveSession(state, token, nextId)) return;
    state.project = project;
    resetViewState();
    restoreViewState(project);
    state.selectedShotId = state.project.shots?.[0]?.id || null;
    state.memoryResults = [];
    state.videoDraft = null;
    state.projectFormMode = "summary";
    state.formTouched = false;
    persistViewState();
    renderFeedbackResult(null);
    restoreJobObservation();
    renderAll();
  } catch (error) {
    if (isLiveSession(state, token, nextId)) showError(`加载项目失败：${error.message}`);
  }
}

/* ---- 未保存修改守卫 ---- */
let unsavedResolver = null;

function guardUnsavedChanges() {
  const newProjectDraft = (state.projectFormMode === "create" || state.projectFormMode === "edit") && state.formTouched;
  const shotDirty = hasUnsavedShotDraft();
  if (!newProjectDraft && !shotDirty) return Promise.resolve("proceed");
  const message = state.projectFormMode === "edit" && state.formTouched
    ? "当前有未保存的项目设置。切换会丢弃这些修改；镜头版本和成片不会被删除。"
    : newProjectDraft && state.projectFormMode === "create"
    ? "当前有未提交的新项目草稿。切换项目会丢弃该草稿；当前项目不受影响。"
    : "当前镜头有未保存的修改。";
  return openUnsavedModal(message, { canSave: shotDirty && !newProjectDraft });
}

function openUnsavedModal(message, { canSave }) {
  el("unsavedModalMessage").textContent = message;
  el("unsavedSaveBtn").classList.toggle("hidden", !canSave);
  el("unsavedModal").classList.remove("hidden");
  return new Promise((resolve) => {
    unsavedResolver = resolve;
  });
}

async function resolveUnsaved(choice) {
  el("unsavedModal").classList.add("hidden");
  const resolve = unsavedResolver;
  unsavedResolver = null;
  if (choice === "save") {
    await flushShotDraft(true);
    state.formTouched = false;
    resolve?.("proceed");
  } else if (choice === "discard") {
    state.formTouched = false;
    state.videoDraft = null;
    resolve?.("proceed");
  } else {
    resolve?.("cancel");
  }
}

function hasUnsavedShotDraft() {
  const shot = selectedShot();
  if (!state.project || !shot) return false;
  return Boolean(state.videoDraft?.dirty && state.videoDraft?.shotId === shot.id) || shotFormDirty(shot);
}

/* ================================================================== */
/* 阶段导航 / 查看状态                                                   */
/* ================================================================== */

async function setViewStage(stageId) {
  const resolved = resolveStageId(stageId, state.project);
  if (state.viewStage === resolved) return;
  // 离开有未保存修改的阶段前给出守卫。
  const dirty = (state.viewStage === "video" || state.viewStage === "keyframes") && hasUnsavedShotDraft();
  if (dirty) {
    const choice = await openUnsavedModal("当前镜头有未保存的修改。", { canSave: true });
    if (choice === "cancel") return;
    if (choice === "discard") state.videoDraft = null;
  }
  state.viewStage = resolved;
  state.selectedAsset = null;
  persistViewState();
  renderAll();
}

/* ================================================================== */
/* 中间工作区：素材选择与阶段操作                                        */
/* ================================================================== */

function onWorkspaceClick(event) {
  // 素材卡片选择
  const card = event.target.closest("[data-asset-key]");
  if (card) {
    selectAsset(card.dataset.stage, card.dataset.assetKey);
    return;
  }
  const exportKind = event.target.closest("[data-action='export-json'], [data-action='export-md']");
  if (exportKind) {
    exportProject(exportKind.dataset.action === "export-json" ? "json" : "markdown");
    return;
  }
  const gotoAssembly = event.target.closest("[data-action='goto-assembly']");
  if (gotoAssembly) {
    setViewStage("assembly");
    return;
  }
  const assemble = event.target.closest("[data-action='assemble-project']");
  if (assemble && !assemble.disabled) {
    onAssembleProject();
    return;
  }
  const saveAssembly = event.target.closest("[data-action='save-assembly-settings']");
  if (saveAssembly && !saveAssembly.disabled) {
    onSaveAssemblySettings();
    return;
  }
  // 阶段操作（改编 / 故事线 / Bible / 分镜）
  const trigger = event.target.closest("[data-adapt]");
  if (trigger) {
    onAdaptationAction(trigger);
  }
}

function selectAsset(stage, key) {
  if (state.selectedAsset?.stage === stage && state.selectedAsset?.key === key) {
    state.selectedAsset = null; // 再次点击取消选中
    } else {
    state.selectedAsset = { stage, key };
  }
  persistViewState();
  renderAll();
}

async function onAdaptationAction(trigger) {
  if (!state.project) return;
  const action = trigger.dataset.adapt;
  try {
    trigger.disabled = true;
    if (action === "select-option") {
      state.project = await api.selectAdaptationOption(state.project.id, trigger.dataset.optionId);
    } else if (action === "select-storyline") {
      state.project = await api.selectStoryline(state.project.id, trigger.dataset.storylineId);
    } else if (action === "save-medium-scope") {
      state.project = await api.saveMediumScope(state.project.id, collectMediumScopePayload());
    } else if (action === "recommend-scope") {
      state.project = await api.recommendMediumScope(state.project.id);
    } else if (action === "confirm-medium-scope") {
      state.project = await api.confirmMediumScope(state.project.id, collectMediumScopePayload());
    } else if (action === "regen-medium") {
      state.project = await api.regenerateMedium(state.project.id, trigger.dataset.stage);
    } else if (action === "confirm-scope") {
      state.project = await api.confirmScope(state.project.id, trigger.dataset.optionId);
    } else if (action === "save-bible") {
      state.project = await api.saveBible(state.project.id, collectBiblePayload());
    } else if (action === "confirm-bible") {
      state.project = await api.confirmBible(state.project.id, collectBiblePayload());
    } else if (action === "discard-bible") {
      renderAll();
      return;
    } else if (action === "save-storyboard") {
      state.project = await api.saveStoryboard(state.project.id, collectStoryboardPayload());
    } else if (action === "confirm-storyboard") {
      state.project = await api.confirmStoryboard(state.project.id, collectStoryboardPayload());
    } else if (action === "discard-storyboard") {
      renderAll();
      return;
    } else if (action === "save-model-config") {
      await saveStageModelConfig(trigger.dataset.modelStage);
    } else if (action === "save-generation-mode") {
      const mode = document.getElementById("generationModeSelect")?.value || "mock";
      state.project = await api.saveGenerationMode(state.project.id, mode);
    } else if (action === "vision-review") {
      const path = trigger.dataset.assetPath;
      if (!path || !/\.(png|jpe?g|webp)$/i.test(path)) {
        showError("请先登记当前项目的 JPEG 或 PNG 首帧，再进行视觉检查。");
        return;
      }
      state.project = await api.visionReview(state.project.id, { asset_path: path, role: "first_frame" });
      showSuccess("视觉检查已完成。");
    } else if (action === "regen") {
      // 重做：先保存当前阶段草稿，再从该阶段重新执行（后端会失效必要下游）。
      await redoStage(state.project, trigger.dataset.stage);
    }
    attachEvents();
    renderAll();
    if (["save-bible", "save-storyboard", "save-medium-scope"].includes(action)) {
      showSuccess("草稿已保存");
    }
  } catch (error) {
    showError(error.message);
  } finally {
    trigger.disabled = false;
  }
}

/** 从某阶段重做并继续：先保存草稿，再调用后端重生成（失效必要下游，保留历史版本）。 */
async function redoStage(project, stage) {
  if (stage === "bible") {
    await api.saveBible(project.id, collectBiblePayload());
    state.project = await api.regenerateAdaptation(project.id, "bible");
  } else if (stage === "storyboard") {
    await api.saveStoryboard(project.id, collectStoryboardPayload());
    state.project = await api.regenerateAdaptation(project.id, "storyboard");
  } else {
    state.project = await api.regenerateAdaptation(project.id, stage);
  }
}

/* ================================================================== */
/* 阶段门禁：自动 / 监制模式                                             */
/* ================================================================== */

async function onFlowAction(event) {
  const trigger = event.target.closest("[data-flow]");
  if (!trigger || !state.project || flowBusy) return;
  const action = trigger.dataset.flow;
  const projectId = state.project.id;
  const workflow = computeWorkflow(state.project);
  const frontier = workflow.executionStage;
  try {
    flowBusy = true;
    trigger.disabled = true;
    if (action === "pause") {
      const result = await api.pauseProject(projectId);
      state.project = result.checkpoint ? await api.getProject(projectId) : state.project;
      if (result.message) el("jobMessage").textContent = result.message;
      await refreshProject();
      return;
    }
    if (action === "resume-auto") {
      const checkpointId = trigger.dataset.checkpointId || state.project.checkpoint?.id;
      const result = checkpointId
        ? await api.resumeCheckpoint(projectId, checkpointId)
        : await api.resumeProject(projectId);
      el("jobMessage").textContent = result.message || `已从检查点继续`;
      if (result.project) state.project = result.project;
      await refreshProject();
      return;
    }
    if (action === "adopt") {
      await adoptFrontier(projectId, frontier);
    } else if (action === "redo") {
      await redoFrontier(projectId, frontier);
    }
    attachEvents();
    renderAll();
  } catch (error) {
    showError(error.message);
  } finally {
    flowBusy = false;
    trigger.disabled = false;
  }
}

/** 采用并继续：确认当前执行阶段（使用已保存内容），进入下一阶段。 */
async function adoptFrontier(projectId, frontier) {
  if (frontier === "storyline") {
    state.project = await api.confirmMediumScope(projectId);
  } else if (frontier === "text" || frontier === "adaptation") {
    state.project = await api.confirmScope(projectId);
  } else if (frontier === "bible") {
    state.project = await api.confirmBible(projectId);
  } else if (frontier === "storyboard") {
    state.project = await api.confirmStoryboard(projectId);
  } else if (frontier === "video" || frontier === "assembly") {
    const result = await api.assembleProject(projectId);
    el("jobMessage").textContent = `成片合成任务 ${result.job_id} 已入队`;
    await refreshProject();
  }
}

/** 重做当前执行阶段（使用已保存内容，不含未保存表单编辑）。 */
async function redoFrontier(projectId, frontier) {
  if (frontier === "storyline") {
    state.project = await api.regenerateMedium(projectId, "analysis");
  } else if (frontier === "text" || frontier === "adaptation") {
    state.project = await api.regenerateAdaptation(projectId, "scope");
  } else if (frontier === "bible") {
    state.project = await api.regenerateAdaptation(projectId, "bible");
  } else if (frontier === "storyboard") {
    state.project = await api.regenerateAdaptation(projectId, "storyboard");
  }
}

/* ================================================================== */
/* 镜头编辑器（视频 / 关键帧阶段）                                       */
/* ================================================================== */

function onInspectorChange(event) {
  const target = event.target;
  if (!target || !target.closest("#assetDetail")) return;
  if (target.id === "localFirstFrameInput") {
    onRegisterLocalKeyframe(target);
    return;
  }
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
  if (!ids.includes(target.id)) return;
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
    return;
  }
  // 文本输入不整页重绘（保留焦点），只更新脏状态与重做按钮。
  updateShotDirtyUI(shot);
}

async function onInspectorClick(event) {
  const adapt = event.target.closest("[data-adapt]");
  if (adapt) {
    await onAdaptationAction(adapt);
    return;
  }
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  const shot = selectedShot();
  if (!state.project || !shot) return;
  const action = trigger.dataset.action;
  if (action === "rollback-version") return onRollbackVersion(trigger.dataset.versionId);
  if (action === "refresh-video-tasks") return onRefreshVideoTasks(trigger);
  if (action === "safe-retry-video") return onSafeRetryVideo(trigger);
  if (action === "save-shot-draft") return onSaveShotDraft(trigger);
  if (action === "freeze-shot-version") return onFreezeShotVersion(trigger);
  if (action === "apply-keyframes") return onApplyKeyframes(trigger);
  if (action === "redraw-keyframe") return onRedrawKeyframe(trigger);
  if (action === "discard-shot") return onDiscardShot();
  if (action === "redo-shot") return onRedoShot(trigger);
  if (action === "assemble-project") return onAssembleProject();
  if (action === "save-assembly-settings") return onSaveAssemblySettings();
  if (action === "generate-video") return onGenerateVideo(trigger, shot);
}

async function onGenerateVideo(trigger, shot) {
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

/** 从此处重做并继续（镜头）：保存草稿 → 冻结新版本 → 重新生成该镜头视频。 */
async function onRedoShot(trigger) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  try {
    trigger.disabled = true;
    trigger.textContent = "正在重做";
    const payload = collectInspectorDraft() || currentVideoDraftPayload();
    await api.saveShotDraft(state.project.id, shot.id, payload);
    const frozen = await api.freezeShotVersion(state.project.id, shot.id, payload);
    if (state.videoDraft) state.videoDraft.dirty = false;
    state.stageEdit = null;
    const result = await api.generateVideo(state.project.id, shot.id, { ...payload, version_id: frozen.version_id });
    attachEvents();
    el("jobMessage").textContent = `已从镜头 ${shot.title} 重做：新版本 v${frozen.version_number || "?"}，视频任务 ${result.job_id} 已入队。下游成片将标记为需重新合成。`;
    await refreshProject();
  } catch (error) {
    showError(`重做失败：${error.message}`);
  }
}

function onDiscardShot() {
  // 放弃修改：丢弃内存草稿与编辑基线，重新从已保存草稿/版本渲染。
  state.videoDraft = null;
  state.stageEdit = null;
  renderAll();
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
    state.stageEdit = null;
    await refreshProject({ preserveObservation: true });
    showSuccess("镜头草稿已保存");
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
    state.stageEdit = null;
    el("jobMessage").textContent = result.created ? `已创建镜头版本 v${result.version_number}` : result.reason || "没有实质修改，未创建新版本";
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

async function onRegisterLocalKeyframe(input) {
  const shot = selectedShot();
  const file = input.files && input.files[0];
  input.value = "";
  if (!state.project || !shot) {
    showError("请先选择一个镜头，再登记首帧。");
    return;
  }
  if (!file) return;
  const name = (file.name || "").toLowerCase();
  if (!/\.(png|jpe?g)$/.test(name) && !["image/png", "image/jpeg"].includes(file.type)) {
    showError("只接受 JPEG 或 PNG。请重新选择本地图片。");
    return;
  }
  try {
    const registered = await api.registerLocalKeyframe(state.project.id, shot.id, file);
    if (state.videoDraft && state.videoDraft.shotId === shot.id && registered?.file_path) {
      state.videoDraft.first_frame_path = registered.file_path;
    }
    await refreshProject();
    showSuccess("已登记为首帧。");
  } catch (error) {
    showError(`登记首帧失败：${error.message}`);
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
    el("jobMessage").textContent = `云端回查任务 ${result.job_id} 已入队`;
    await refreshProject();
  } catch (error) {
    showError(`云端回查失败：${error.message}`);
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

/* ================================================================== */
/* 工作流控制 / 合成 / 反馈 / 检索                                      */
/* ================================================================== */

async function onRunWorkflow() {
  if (!state.project) return;
  const projectId = state.project.id;
  const token = state.observerToken;
  try {
    const result = await api.runProject(projectId);
    if (!isLiveSession(state, token, projectId)) return;
    el("jobMessage").textContent = result.reused
      ? (result.message || "已复用当前改编任务")
      : `改编任务 ${result.job_id} 已入队`;
    renderAll();
    await refreshProject({ token, projectId, preserveObservation: true });
    await waitForAdaptationSurface({ token, projectId });
    if (!isLiveSession(state, token, projectId)) return;
    restoreJobObservation();
    renderAll();
  } catch (error) {
    if (isLiveSession(state, token, projectId)) showError(`启动失败：${error.message}`);
  }
}

function adaptationSurfaceReady(project) {
  if (!project) return false;
  if (project.status === "failed") return true;
  if ((project.adaptation_options || []).length > 0) return true;
  if ((project.storylines || []).length > 0) return true;
  return Boolean(project.status && !["created", "draft"].includes(project.status));
}

async function waitForAdaptationSurface({ token, projectId, timeoutMs = 10000 }) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!isLiveSession(state, token, projectId)) return;
    if (adaptationSurfaceReady(state.project) && state.project?.id === projectId) return;
    await new Promise((resolve) => window.setTimeout(resolve, 280));
    if (!isLiveSession(state, token, projectId)) return;
    await refreshProject({ token, projectId, preserveObservation: true });
  }
}

async function onResumeWorkflow() {
  if (!state.project || flowBusy) return;
  try {
    flowBusy = true;
    el("resumeWorkflowBtn").disabled = true;
    const checkpointId = el("resumeWorkflowBtn").dataset.checkpointId || state.project.checkpoint?.id;
    const result = checkpointId
      ? await api.resumeCheckpoint(state.project.id, checkpointId)
      : await api.resumeProject(state.project.id);
    attachEvents();
    el("jobMessage").textContent = result.message || (result.reused ? "已复用当前检查点，未重复执行。" : `已从检查点继续`);
    if (result.project) state.project = result.project;
    await refreshProject();
  } catch (error) {
    showError(`恢复流程失败：${error.message}`);
  } finally {
    flowBusy = false;
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

async function onAssembleProject() {
  if (!state.project) return;
  try {
    const result = await api.assembleProject(state.project.id);
    attachEvents();
    el("jobMessage").textContent = result.reused
      ? result.message || "成片合成任务已在进行中"
      : result.message || `成片合成任务 ${result.job_id} 已入队`;
    state.viewStage = "assembly";
    persistViewState();
    await refreshProject();
  } catch (error) {
    showError(`成片合成失败：${error.message}`);
  }
}

function collectAssemblySettings() {
  const checked = (id) => Boolean(el(id)?.checked);
  const valueOf = (id) => (el(id)?.value || "").trim();
  return {
    subtitle_enabled: checked("assemblySubtitleEnabled"),
    subtitle_text: el("assemblySubtitleText")?.value || "",
    subtitle_srt_path: valueOf("assemblySubtitleSrt"),
    audio_enabled: checked("assemblyAudioEnabled"),
    audio_asset_path: valueOf("assemblyAudioPath"),
    audio_volume: Number(el("assemblyAudioVolume")?.value || 0.4),
    keep_source_audio: checked("assemblyKeepSourceAudio"),
    subtitle_font_size: Number(el("assemblySubtitleSize")?.value || 28),
    subtitle_position: valueOf("assemblySubtitlePosition") || "bottom",
  };
}

async function onSaveAssemblySettings() {
  if (!state.project) return;
  try {
    const saved = await api.saveAssemblySettings(state.project.id, collectAssemblySettings());
    state.assemblyDraft = { projectId: state.project.id, dirty: false, values: saved.settings || collectAssemblySettings() };
    attachEvents();
    el("jobMessage").textContent = saved.stale
      ? "成片配置已保存。当前成片已过期，需要重新合成。"
      : "成片配置已保存。";
    await refreshProject();
    state.statusNotice = saved.stale
      ? "成片配置已保存。当前成片已过期，需要重新合成。"
      : "成片配置已保存。";
    el("jobMessage").textContent = state.statusNotice;
    showSuccess("成片配置已保存");
    window.setTimeout(() => {
      if (state.statusNotice && state.statusNotice.includes("成片配置已保存")) {
        state.statusNotice = null;
      }
    }, 6000);
  } catch (error) {
    showError(`保存成片配置失败：${error.message}`);
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
    resetViewState();
    state.selectedShotId = null;
    state.memoryResults = [];
    state.projectFormMode = "create";
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

/* ================================================================== */
/* 表单数据收集                                                         */
/* ================================================================== */

function collectMediumScopePayload() {
  const eventIds = [...document.querySelectorAll("[data-event-check]:checked")].map((input) => input.value);
  return {
    event_ids: eventIds,
    user_note: document.getElementById("scopeUserNote")?.value || "",
  };
}

function collectBiblePayload() {
  const cards = (kind) => {
    const map = {};
    document.querySelectorAll(`[data-bible-card="${kind}"]`).forEach((input) => {
      const index = Number(input.dataset.index);
      map[index] = map[index] || {};
      map[index][input.dataset.field] = input.value;
    });
    return Object.keys(map)
      .sort((a, b) => Number(a) - Number(b))
      .map((key) => map[key]);
  };
  return {
    logline: document.getElementById("bibleLogline")?.value,
    adaptation_summary: document.getElementById("bibleSummary")?.value,
    emotion_curve: document.getElementById("bibleEmotion")?.value,
    protagonist: document.getElementById("bibleHero")?.value,
    protagonist_goal: document.getElementById("bibleGoal")?.value,
    obstacle: document.getElementById("bibleObstacle")?.value,
    visual_style: document.getElementById("bibleStyle")?.value,
    consistency_constraints: document.getElementById("bibleConstraints")?.value,
    character_cards: cards("character"),
    scene_cards: cards("scene"),
  };
}

function collectStoryboardPayload() {
  const grouped = {};
  document.querySelectorAll("[data-board-id]").forEach((input) => {
    const id = input.dataset.boardId;
    grouped[id] = grouped[id] || { id };
    const field = input.dataset.boardField;
    grouped[id][field] = field === "characters" ? input.value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean) : field === "duration_seconds" ? Number(input.value) : input.value;
  });
  return Object.values(grouped);
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

/* ================================================================== */
/* 脏状态追踪（驱动「从此处重做并继续」按钮）                            */
/* ================================================================== */

const SHOT_TRACK_FIELDS = ["description", "camera_motion", "visual_prompt", "video_mode", "provider", "model", "duration_seconds", "first_frame_path", "last_frame_path", "reference_frame_path"];

function shotFormDirty(shot) {
  const form = readShotFormFields();
  if (!form) {
    return Boolean(state.videoDraft?.dirty && state.videoDraft?.shotId === shot.id) || Boolean(shot.has_unsaved_changes);
  }
  // 与渲染时捕获的归一化基线对比，避免 Provider 归一化造成“未修改却判脏”。
  const baseline = state.stageEdit?.shotId === shot.id ? state.stageEdit.baseline : null;
  if (!baseline) return Boolean(state.videoDraft?.dirty);
  return SHOT_TRACK_FIELDS.some((field) => String(form[field] ?? "") !== String(baseline[field] ?? ""));
}

function readShotFormFields() {
  if (!document.getElementById("shotDescriptionInput") && !document.getElementById("videoModeSelect")) return null;
  const collected = collectInspectorDraft();
  if (!collected) return null;
  return {
    description: collected.description ?? "",
    camera_motion: collected.camera_motion ?? "",
    visual_prompt: collected.visual_prompt ?? "",
    video_mode: collected.video_mode ?? "t2v",
    provider: collected.provider ?? "",
    model: collected.model ?? "",
    duration_seconds: Number(collected.duration_seconds ?? 0),
    first_frame_path: collected.first_frame_path ?? "",
    last_frame_path: collected.last_frame_path ?? "",
    reference_frame_path: collected.reference_frame_path ?? "",
  };
}

function updateShotDirtyUI(shot) {
  const dirty = shotFormDirty(shot);
  const flag = document.getElementById("shotDirty");
  if (flag) {
    flag.className = dirty ? "dirty-flag" : "clean-flag";
    flag.textContent = dirty ? "有未保存修改" : "草稿已同步";
  }
  document.querySelectorAll('#assetDetail [data-action="redo-shot"]').forEach((btn) => {
    btn.disabled = !dirty;
  });
}

function onWorkspaceInput(event) {
  const target = event.target;
  if (!target) return;
  if (target.closest("#assemblySettingsForm")) {
    updateAssemblySettingsDirtyUI();
  } else if (target.closest("[data-model-stage]")) {
    onStageModelDraftInput(target);
  } else if (target.closest("[data-bible-track]") || target.closest("[data-bible-card]")) {
    updateFormDirtyUI("bible");
  } else if (target.closest("[data-board-track]") || target.closest("[data-board-id]")) {
    updateFormDirtyUI("storyboard");
  }
}

function onStageModelDraftInput(target) {
  const panel = target.closest("[data-model-stage]");
  if (!panel || !state.project) return;
  const stage = panel.dataset.modelStage;
  const provider = panel.querySelector('[data-model-field="provider"]')?.value;
  const model = panel.querySelector('[data-model-field="model"]')?.value;
  const current = state.project.model_configs?.[stage] || {};
  const dirty = provider !== current.provider || model !== current.model;
  state.stageModelDraft = {
    ...(state.stageModelDraft || {}),
    [stage]: { provider, model, dirty },
  };
  renderAll();
}

async function saveStageModelConfig(stage) {
  if (!state.project || !stage) return;
  const panel = document.querySelector(`[data-model-stage="${stage}"]`);
  const current = state.project.model_configs?.[stage] || {};
  const provider = panel?.querySelector('[data-model-field="provider"]')?.value || state.stageModelDraft?.[stage]?.provider || current.provider;
  const model = panel?.querySelector('[data-model-field="model"]')?.value || state.stageModelDraft?.[stage]?.model || current.model;
  if (!provider || !model) {
    showError("请选择 Provider 和模型。");
    return;
  }
  state.project = await api.saveModelConfig(state.project.id, stage, { provider, model });
  if (state.stageModelDraft) delete state.stageModelDraft[stage];
  const labels = { text: "文本理解", storyline: "故事线选择", bible: "Story Bible", storyboard: "分镜设计" };
  const affected = (state.project.invalidated_stages || []).map((id) => labels[id] || id).join("、");
  showSuccess(affected ? `已保存模型配置。可能受影响：${affected}` : "已保存模型配置。");
}

function assemblySettingsEqual(left, right) {
  const keys = [
    "subtitle_enabled",
    "subtitle_text",
    "subtitle_srt_path",
    "audio_enabled",
    "audio_asset_path",
    "audio_volume",
    "keep_source_audio",
    "subtitle_font_size",
    "subtitle_position",
  ];
  return keys.every((key) => String(left?.[key] ?? "") === String(right?.[key] ?? ""));
}

function savedAssemblySettings() {
  return state.project?.assembly?.settings || {};
}

function updateAssemblySettingsDirtyUI() {
  if (!state.project || !el("assemblySettingsForm")) return;
  const values = collectAssemblySettings();
  const dirty = !assemblySettingsEqual(values, savedAssemblySettings());
  state.assemblyDraft = { projectId: state.project.id, dirty, values };
  const flag = el("assemblySettingsDirty");
  if (flag) {
    flag.className = dirty ? "dirty-flag" : "clean-flag";
    flag.textContent = dirty ? "有未保存修改" : "配置已同步";
  }
}

function updateFormDirtyUI(kind) {
  const dirty = kind === "bible" ? bibleFormDirty() : storyboardFormDirty();
  const flagId = kind === "bible" ? "bibleDirty" : "boardDirty";
  const flag = document.getElementById(flagId);
  if (flag) {
    flag.className = dirty ? "dirty-flag" : "clean-flag";
    flag.textContent = dirty ? "有未保存修改" : "草稿已同步";
  }
  // 重做按钮只在有实际修改时可用。
  const container = el("stageWorkspace");
  container.querySelectorAll("[data-redo-btn]").forEach((btn) => {
    btn.disabled = !dirty;
  });
}

function bibleFormDirty() {
  const bible = state.project?.story_bible;
  if (!bible || !document.getElementById("bibleLogline")) return false;
  const val = (id) => document.getElementById(id)?.value ?? "";
  const scalarDiff =
    val("bibleLogline") !== (bible.logline || "") ||
    val("bibleSummary") !== (bible.adaptation_summary || bible.summary || "") ||
    val("bibleEmotion") !== (bible.emotion_curve || "") ||
    val("bibleHero") !== (bible.protagonist || "") ||
    val("bibleGoal") !== (bible.protagonist_goal || "") ||
    val("bibleObstacle") !== (bible.obstacle || "") ||
    val("bibleStyle") !== (bible.visual_style || "") ||
    val("bibleConstraints") !== (bible.consistency_constraints || "");
  if (scalarDiff) return true;
  return cardsDirty("character", bible.character_cards || []) || cardsDirty("scene", bible.scene_cards || []);
}

function cardsDirty(kind, savedCards) {
  const inputs = [...document.querySelectorAll(`[data-bible-card="${kind}"]`)];
  return inputs.some((input) => {
    const card = savedCards[Number(input.dataset.index)] || {};
    const field = input.dataset.field;
    const saved = field === "identity" ? card.identity || card.role || "" : card[field] || "";
    return input.value !== saved;
  });
}

function storyboardFormDirty() {
  const drafts = state.project?.storyboard_drafts || [];
  if (!drafts.length) return false;
  const byId = Object.fromEntries(drafts.map((item) => [item.id, item]));
  return [...document.querySelectorAll("[data-board-id]")].some((input) => {
    const saved = byId[input.dataset.boardId];
    if (!saved) return false;
    const field = input.dataset.boardField;
    const savedValue = field === "characters" ? (saved.characters || []).join("、") : field === "duration_seconds" ? String(saved.duration_seconds || 5) : saved[field] || "";
    return input.value !== savedValue;
  });
}

/* ================================================================== */
/* 实时监听（SSE + 轮询 + 云端回查）：保持既有 P2 逻辑                   */
/* ================================================================== */

async function checkHealth() {
  const health = await api.health();
  el("connectionStatus").textContent = health.ok ? "本地服务已连接" : "服务异常";
}

async function loadProjects() {
  state.projects = await api.listProjects();
  if (!state.project && state.projects.length) {
    let preferred = "";
    try {
      preferred = sessionStorage.getItem(LAST_PROJECT_KEY) || "";
    } catch {
      preferred = "";
    }
    const match = state.projects.find((item) => item.id === preferred) || state.projects[0];
    const token = startSessionForProject(match.id);
    state.project = await api.getProject(match.id);
    if (!isLiveSession(state, token, match.id)) return;
    restoreViewState(state.project);
    persistViewState();
    state.selectedShotId = state.project.shots?.[0]?.id || null;
  }
  restoreJobObservation();
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
  persistLastProject(projectId);
  return beginObservation(state, projectId, observerTimers());
}

async function flushShotDraft(force = false) {
  const shot = selectedShot();
  if (!state.project || !shot) return;
  const draft = collectInspectorDraft() || (state.videoDraft?.shotId === shot.id ? state.videoDraft : null);
  if (!draft) return;
  if (!force && !state.videoDraft?.dirty && !shotFormDirty(shot)) return;
  try {
    if (typeof api.saveShotDraft === "function") {
      await api.saveShotDraft(state.project.id, shot.id, draft);
    }
    if (state.videoDraft) state.videoDraft.dirty = false;
  } catch (error) {
    console.warn("自动保存镜头草稿失败", error);
  }
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
    if (event.type === "snapshot" || (payload && Array.isArray(payload.jobs) && !payload.event_type)) {
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
  const shouldRefreshProject =
    ["asset.ready", "project.refresh_required", "job.failed"].includes(event.event_type) ||
    (event.event_type === "job.update" && ["paused", "completed"].includes(event.status)) ||
    String(event.stage || "").startsWith("awaiting_");
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
  const refreshBusy = (state.project.jobs || []).some((job) => job.type === "video_task_refresh" && ["queued", "running"].includes(job.status));
  if (refreshBusy) return;
  try {
    state.refreshInFlight = true;
    await api.refreshVideoTasks(projectId);
  } catch (error) {
    if (isLiveSession(state, token, projectId)) console.warn("云端任务回查失败", error);
  } finally {
    state.refreshInFlight = false;
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
  if (!isLiveSession(state, token, projectId)) return;
  const project = await api.getProject(projectId);
  if (!isLiveSession(state, token, projectId) || state.project?.id !== projectId) return;
  state.project = project;
  if (!state.selectedShotId) {
    state.selectedShotId = state.project.shots?.[0]?.id || null;
  }
  const incoming = state.project.job_events || [];
  incoming.forEach((event) => rememberJobEvent(state, event, token));
  renderAll();
  try {
    const diagnostics = await api.diagnostics();
    if (isLiveSession(state, token, projectId)) state.diagnostics = diagnostics;
    const projects = await api.listProjects();
    if (isLiveSession(state, token, projectId) && state.project?.id === projectId) {
      state.projects = projects;
      renderAll();
    }
  } catch (error) {
    if (isLiveSession(state, token, projectId)) console.warn("刷新附属状态失败", error);
  }
  if (!isLiveSession(state, token, projectId) || state.project?.id !== projectId) return;
  const watch = shouldWatchProject(state.project).watch;
  if (!options.preserveObservation && watch) {
    attachEvents();
  } else if (options.preserveObservation && watch && !state.eventSource) {
    attachEvents();
  }
}

/* ================================================================== */
/* 工具                                                                 */
/* ================================================================== */

init().catch((error) => {
  console.error(error);
  el("connectionStatus").textContent = "服务未连接";
  el("connectionStatus").className = "status-pill danger";
});

function showError(message) {
  el("feedbackResult").innerHTML = `<div class="prompt-block"><strong>操作提示</strong><br />${escapeHtml(message)}</div>`;
  showToast(message, "error");
}

function showSuccess(message) {
  showToast(message, "ok");
}

let toastTimer = 0;
function showToast(message, kind = "ok") {
  const node = el("statusToast");
  if (!node) return;
  node.hidden = false;
  node.className = `status-toast ${kind}`;
  node.textContent = String(message || "");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    node.hidden = true;
  }, 2400);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
