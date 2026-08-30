import { currentVersion, latestVersion, selectedShot, state } from "./state.js";
import {
  STAGES,
  STAGE_STATE,
  computeWorkflow,
  jobCenterRows,
  jobStatusLabel,
  stageAssets,
  stageStateLabel,
} from "./workflowViewModel.js";

const el = (id) => document.getElementById(id);

/* ================================================================== */
/* 基础 helpers                                                         */
/* ================================================================== */

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusClass(status) {
  if (["completed", "ready_for_review", "keyframes_ready", "passed", "video_ready", "production_ready"].includes(status)) return "success";
  if (["running", "queued", "needs_regeneration", "paused", "review_pending", "waiting_remote", "awaiting_storyline_review", "awaiting_scope_review", "awaiting_bible_review", "awaiting_storyboard_review", "adaptation_options_ready", "story_bible_ready", "storyboard_draft_ready"].includes(status)) return "warning";
  if (["video_running", "video_waiting_remote"].includes(status)) return "active";
  if (["failed", "video_failed", "video_invalid"].includes(status)) return "danger";
  return "neutral";
}

function mediaSource(assetOrPath) {
  const filePath = typeof assetOrPath === "string" ? assetOrPath : assetOrPath?.file_path || "";
  const ref = typeof assetOrPath === "string" ? "" : assetOrPath?.embedding_ref || "";
  const type = typeof assetOrPath === "string" ? "" : assetOrPath?.type || "";
  const suffix = filePath.split(".").pop()?.toLowerCase();
  if (ref.startsWith("continuity:")) return "Continuity";
  if (ref.startsWith("fallback:ffmpeg")) return "占位视频";
  if (ref.startsWith("fallback:image") || suffix === "svg") return "Mock";
  if (type === "final-video") return "成片";
  if (suffix === "mp4") return "Video";
  if (["png", "jpg", "jpeg", "webp"].includes(suffix)) return "AI Image";
  return "Asset";
}

function assetForPath(path) {
  return (state.project?.assets || []).find((asset) => asset.file_path === path);
}

function isRealShotVideo(asset) {
  const ref = asset?.embedding_ref || "";
  return asset?.type === "video" && ref.startsWith("provider:") && !ref.startsWith("provider:ffmpeg");
}

function isInvalidVideoAsset(asset) {
  return asset?.type === "video" && !isRealShotVideo(asset);
}

function activeVideoTask(shot) {
  return shot?.active_video_task || (shot?.video_tasks || [])[0] || null;
}

function keyframeCandidates() {
  return (state.project?.assets || []).filter((asset) => {
    const suffix = asset.file_path?.split(".").pop()?.toLowerCase();
    return ["character", "scene", "first-frame", "last-frame"].includes(asset.type) && ["png", "jpg", "jpeg", "webp", "svg"].includes(suffix);
  });
}

function videoFailureDiagnosis(task, shot) {
  const code = task?.error_code || "";
  const message = task?.error_message || "";
  const taskFailed = !!task && task.status === "failed";
  const shotFailed = ["video_failed", "video_invalid"].includes(shot?.status);
  if (!taskFailed && !shotFailed) return null;
  if (code.includes("PolicyViolation") || code.includes("SensitiveContent") || message.includes("敏感") || message.includes("版权")) {
    return { title: "内容安全 / 版权策略拦截", detail: "该镜头可以先做原创化安全改写，再用 T2V 重试，避免命名作品、文字复现或过强风格指向。", canSafeRetry: true };
  }
  if (code.includes("SetLimitExceeded") || message.includes("SetLimitExceeded")) {
    return { title: "平台推理额度或并发限制", detail: "这类错误通常需要等待额度恢复，或在火山方舟控制台调整模型推理限制。", canSafeRetry: false };
  }
  if (code.includes("AccessDenied") || message.includes("AccessDenied")) {
    return { title: "模型或接入点权限不足", detail: "请检查 Seedance 模型、接入点和 API Key 是否开通了对应权限。", canSafeRetry: false };
  }
  return { title: "视频任务失败", detail: "可以先回查云端任务；如果错误来自 Prompt 内容，再尝试安全改写重试。", canSafeRetry: true };
}

function renderMediaBadge(assetOrPath) {
  return `<span class="media-badge">${escapeHtml(mediaSource(assetOrPath))}</span>`;
}

function renderAssetMedia(path, label, asset) {
  if (!path) return `<div class="asset-thumb-empty">无预览</div>`;
  const safePath = escapeHtml(path);
  const safeLabel = escapeHtml(label);
  const suffix = path.split(".").pop()?.toLowerCase();
  if (suffix === "mp4" && isInvalidVideoAsset(asset)) {
    return `<div class="asset-thumb-empty">无效占位视频</div>${renderMediaBadge(asset || path)}`;
  }
  const media =
    suffix === "mp4"
      ? `<video src="${safePath}" muted playsinline preload="metadata"></video>`
      : `<img src="${safePath}" alt="${safeLabel}" />`;
  return `${media}${renderMediaBadge(asset || path)}`;
}

function formatTime(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function uniqueValues(items) {
  return [...new Set(items.filter((item) => item !== undefined && item !== null && item !== ""))];
}

/* ================================================================== */
/* 主入口                                                               */
/* ================================================================== */

export function renderAll() {
  renderTopbar();
  renderProjectSection();
  renderProjects();
  renderProviderDiagnostics();
  renderConstraints();
  renderMemoryResults();
  renderStageNav();
  renderStageWorkspace();
  renderJob();
}

/* ================================================================== */
/* 顶栏                                                                 */
/* ================================================================== */

function renderTopbar() {
  const project = state.project;
  const badge = el("projectModeBadge");
  const review = Boolean(project?.review_mode);
  badge.textContent = review ? "监制模式" : "自动模式";
  badge.className = `status-pill ${review ? "warning" : "active"}`;
  badge.title = review
    ? "监制模式：关键阶段完成后等待审核，需点击「采用并继续」。"
    : "自动模式：阶段完成后将自动继续，可随时暂停。";
}

/* ================================================================== */
/* 左侧：项目区（新建 / 创建 / 摘要分离）                                 */
/* ================================================================== */

function renderProjectSection() {
  const mode = state.projectFormMode;
  const hasProject = Boolean(state.project);
  const form = el("projectForm");
  const summary = el("projectSummaryPanel");
  const title = el("projectSectionTitle");
  const note = el("formModeNote");
  const submit = el("submitProjectBtn");
  const cancel = el("cancelFormBtn");
  const newBtn = el("newProjectBtn");

  // 新建项目按钮：查看或编辑已有项目时可用；纯新建表单态下禁用（避免误以为是另一个创建入口）。
  newBtn.disabled = mode === "create" && !hasProject;

  if (mode === "summary" && hasProject) {
    form.classList.add("hidden");
    summary.classList.remove("hidden");
    title.textContent = "项目配置";
    renderSummaryFields();
  } else {
    summary.classList.add("hidden");
    form.classList.remove("hidden");
    if (mode === "edit") {
      title.textContent = "编辑项目设置";
      note.className = "form-mode-note warning";
      note.textContent = "后端暂不支持修改已创建项目的设置（缺口 GAP-1 已记录）。可查看当前配置；如需变更，请新建项目。";
      submit.textContent = "保存设置（暂不支持）";
      submit.disabled = true;
      cancel.classList.remove("hidden");
    } else {
      title.textContent = "新建项目";
      note.className = "form-mode-note";
      note.textContent = hasProject
        ? "正在起草新项目：填写后点击「创建项目」才会真正创建；不会影响当前项目。"
        : "填写表单并点击「创建项目」以创建第一个项目。";
      submit.textContent = "创建项目";
      submit.disabled = false;
      cancel.classList.toggle("hidden", !hasProject);
    }
  }
}

function renderSummaryFields() {
  const project = state.project;
  if (!project) {
    el("summaryFields").innerHTML = "选择或创建一个项目。";
    return;
  }
  const workflow = computeWorkflow(project);
  const execLabel = STAGES.find((s) => s.id === workflow.executionStage)?.label || "文本理解";
  const rows = [
    ["项目名称", project.title],
    ["当前阶段", execLabel],
    ["项目状态", project.status || "created"],
    ["视觉风格", project.style],
    ["视频比例", project.aspect_ratio],
    ["单镜时长", `${project.duration_seconds}s`],
    ["镜头策略", project.shot_count_mode === "manual" ? `手动 · ${project.requested_shot_count || "-"} 镜` : "自动"],
    ["运行模式", project.review_mode ? "监制模式" : "自动模式"],
    ["原文规模", `${project.text_scale_label || ""} · ${(project.source_text || "").length} 字`],
    ["更新时间", formatTime(project.updated_at)],
  ];
  el("summaryFields").className = "summary-fields";
  el("summaryFields").innerHTML = rows
    .map(([key, value]) => `<div class="summary-row"><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
  // 工作流控制按钮可用性
  const canResume = project?.status === "review_pending" && project?.checkpoint?.node === "quality_gate";
  const canRetry = !!project && !["running"].includes(project.status);
  el("resumeWorkflowBtn").disabled = !canResume;
  el("retryWorkflowBtn").disabled = !canRetry;
  el("resumeWorkflowBtn").title = canResume ? "从旧版监制检查点继续执行" : "P4 审核请在中间工作区确认当前步骤";
  el("retryWorkflowBtn").title = canRetry ? "重生成改编范围（保留已有镜头版本与资产）" : "任务运行中";
}

function renderProjects() {
  el("projectCount").textContent = String(state.projects.length);
  if (state.projects.length === 0) {
    el("projectList").innerHTML = `<div class="empty-state">暂无项目。</div>`;
    return;
  }
  el("projectList").innerHTML = state.projects
    .map((project) => {
      const active = state.project?.id === project.id;
      const failed = ["failed", "video_failed"].includes(project.status);
      const progress = projectProgress(project);
      return `
      <button class="project-item ${active ? "active" : ""}" data-project-id="${escapeHtml(project.id)}">
        <div class="project-item-head">
          <strong>${escapeHtml(project.title)}</strong>
          <span class="status-pill ${statusClass(project.status)}">${escapeHtml(project.status)}</span>
        </div>
        <div class="project-progress" title="制作进度"><span style="width:${progress}%"></span></div>
        <div class="project-item-meta">
          <span>${escapeHtml(projectStageLabel(project))}${failed ? " · 异常" : ""}</span>
          <span>${escapeHtml(formatTime(project.updated_at))}</span>
        </div>
      </button>`;
    })
    .join("");
}

// 项目列表项的阶段/进度：列表项只有摘要字段，用 status 近似推导。
function projectStageLabel(project) {
  const map = {
    created: "文本理解",
    draft: "文本理解",
    running: "文本理解",
    awaiting_storyline_review: "故事线选择",
    adaptation_options_ready: "改编方案",
    awaiting_scope_review: "改编方案",
    story_bible_ready: "Story Bible",
    awaiting_bible_review: "Story Bible",
    storyboard_draft_ready: "分镜设计",
    awaiting_storyboard_review: "分镜设计",
    production_ready: "镜头制作",
    ready_for_review: "镜头制作",
    review_pending: "镜头制作",
    video_ready: "镜头视频",
    completed: "成片合成",
    failed: "失败",
  };
  return map[project.status] || "文本理解";
}

function projectProgress(project) {
  const order = ["created", "awaiting_storyline_review", "awaiting_scope_review", "awaiting_bible_review", "awaiting_storyboard_review", "production_ready", "video_ready", "completed"];
  const index = order.indexOf(project.status);
  if (project.status === "failed") return 100;
  if (index < 0) return 8;
  return Math.round(((index + 1) / order.length) * 100);
}

/* ================================================================== */
/* 右侧：阶段导航                                                       */
/* ================================================================== */

function renderStageNav() {
  const nav = el("stageNav");
  const project = state.project;
  if (!project) {
    nav.innerHTML = `<div class="empty-state">创建项目后显示完整制作流程。</div>`;
    return;
  }
  const workflow = computeWorkflow(project);
  nav.innerHTML = workflow.stages
    .map((stage) => {
      const viewing = state.viewStage === stage.id;
      const executing = stage.current && !["skipped"].includes(stage.state);
      const classes = ["stage-node", `tone-${stage.tone}`];
      if (viewing) classes.push("viewing");
      if (executing) classes.push("executing");
      if (stage.state === STAGE_STATE.SKIPPED) classes.push("skipped");
      if (stage.state === STAGE_STATE.INVALIDATED) classes.push("invalidated");
      return `
      <button type="button" class="${classes.join(" ")}" data-stage-id="${stage.id}" title="${escapeHtml(stage.skippedReason || stage.stateLabel)}">
        <span class="stage-index">${stage.index + 1}</span>
        <span class="stage-node-body">
          <span class="stage-node-label">${escapeHtml(stage.label)}</span>
          <span class="stage-node-summary">${escapeHtml(stage.summary)}</span>
        </span>
        <span class="stage-node-state">
          <span class="stage-state-label tone-${stage.tone}">${escapeHtml(stage.stateLabel)}</span>
          <span class="stage-state-dot tone-${stage.tone}"></span>
        </span>
      </button>`;
    })
    .join("");
}

/* ================================================================== */
/* 中间：阶段工作区                                                     */
/* ================================================================== */

function renderStageWorkspace() {
  const project = state.project;
  const stage = state.viewStage;
  const stageDef = STAGES.find((s) => s.id === stage) || STAGES[0];
  el("stageWorkspaceTitle").textContent = stageDef.label;

  if (!project) {
    el("stageWorkspaceSubtitle").textContent = "当前查看阶段";
    el("stageGateBanner").innerHTML = "";
    el("stageWorkspace").className = "stage-workspace empty-state";
    el("stageWorkspace").textContent = "创建项目并启动改编流程后，这里显示当前阶段的素材。";
    el("assetDetail").innerHTML = "";
    updateViewToggle();
    return;
  }

  const workflow = computeWorkflow(project);
  const stageVm = workflow.stages.find((s) => s.id === stage);
  const execLabel = STAGES.find((s) => s.id === workflow.executionStage)?.label || "";
  el("stageWorkspaceSubtitle").textContent = `查看：${stageDef.label} · 执行到：${execLabel}`;

  renderGateBanner(project, workflow);
  updateViewToggle();

  const workspace = el("stageWorkspace");
  workspace.className = "stage-workspace";

  // 无效阶段提示仍允许查看说明与历史。
  const invalidatedNotice =
    stageVm?.state === STAGE_STATE.INVALIDATED
      ? `<div class="prompt-block"><strong>本阶段结果已失效。</strong>上游阶段已重做，以下内容仅作历史参考，不会被下游任务使用。请先在左侧重新执行上游阶段。</div>`
      : "";
  const skippedNotice =
    stageVm?.state === STAGE_STATE.SKIPPED
      ? `<div class="prompt-block"><strong>当前文本无需此步骤。</strong>${escapeHtml(stageVm.skippedReason || "")}</div>`
      : "";

  const body = stageBodyHtml(project, stage, stageVm);
  workspace.innerHTML = `${invalidatedNotice}${skippedNotice}${body}`;

  renderAssetDetail(project, stage);
}

function updateViewToggle() {
  const mode = state.assetViewMode[state.viewStage] || "grid";
  el("stageViewToggle").querySelectorAll(".toggle-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === mode);
  });
}

/* ---- 阶段门禁横幅：自动 / 监制模式差异 ---- */
function renderGateBanner(project, workflow) {
  const banner = el("stageGateBanner");
  const frontier = workflow.stages.find((s) => s.current);
  if (!frontier) {
    banner.innerHTML = "";
    return;
  }
  const review = Boolean(project.review_mode);
  const paused = state.workflowControl.paused;
  const stageLabel = frontier.label;

  if (paused) {
    banner.innerHTML = `
      <div class="gate-card paused">
        <span class="gate-title">流程已暂停（原型）</span>
        <span>自动编排引擎尚未在后端实现，暂停状态仅在本页会话内生效。当前停在「${escapeHtml(stageLabel)}」。</span>
        <div class="button-row compact-row">
          <button class="primary-btn mini-btn" data-flow="resume-auto">恢复自动执行</button>
        </div>
      </div>`;
    return;
  }

  if (frontier.state === STAGE_STATE.AWAITING_REVIEW) {
    if (review) {
      banner.innerHTML = `
        <div class="gate-card review">
          <span class="gate-title">「${escapeHtml(stageLabel)}」已完成，等待审核</span>
          <span>监制模式：确认后才会进入下一阶段。可查看生成依据、修改后重做，或采用并继续。</span>
          <div class="button-row compact-row">
            <button class="primary-btn mini-btn" data-flow="adopt">采用并继续</button>
            <button class="secondary-btn mini-btn" data-flow="redo">修改后重做</button>
          </div>
        </div>`;
    } else {
      banner.innerHTML = `
        <div class="gate-card auto">
          <span class="gate-title">「${escapeHtml(stageLabel)}」已完成，自动模式将继续</span>
          <span>自动模式：确认后进入下一阶段。可随时暂停流程进行人工干预。</span>
          <div class="button-row compact-row">
            <button class="primary-btn mini-btn" data-flow="adopt">采用并继续</button>
            <button class="secondary-btn mini-btn" data-flow="pause">暂停流程</button>
          </div>
        </div>`;
    }
    return;
  }

  if (frontier.state === STAGE_STATE.PROCESSING) {
    banner.innerHTML = `
      <div class="gate-card auto">
        <span class="gate-title">「${escapeHtml(stageLabel)}」处理中</span>
        <span>任务进度见底部任务中心，无需刷新页面。</span>
        <div class="button-row compact-row">
          <button class="secondary-btn mini-btn" data-flow="pause">暂停流程</button>
        </div>
      </div>`;
    return;
  }

  banner.innerHTML = "";
}

/* ---- 阶段工作区主体 ---- */
function stageBodyHtml(project, stage, stageVm) {
  switch (stage) {
    case "text":
      return textStageHtml(project);
    case "storyline":
      return storylineStageHtml(project);
    case "adaptation":
      return adaptationStageHtml(project);
    case "bible":
      return bibleStageHtml(project);
    case "storyboard":
      return storyboardStageHtml(project);
    case "keyframes":
    case "video":
    case "assembly":
      return assetGridHtml(project, stage);
    default:
      return `<div class="empty-state">该阶段暂无内容。</div>`;
  }
}

function assetGridHtml(project, stage) {
  const cards = stageAssets(project, stage);
  if (!cards.length) {
    return `<div class="empty-state">${escapeHtml(stageEmptyText(stage))}</div>`;
  }
  const mode = state.assetViewMode[stage] || "grid";
  const workflow = computeWorkflow(project);
  const invalidated = workflow.stages.find((s) => s.id === stage)?.state === STAGE_STATE.INVALIDATED;
  return `<div class="asset-grid ${mode === "single" ? "single" : ""}">
    ${cards.map((card) => assetCardHtml(card, stage, invalidated)).join("")}
  </div>`;
}

function assetCardHtml(card, stage, invalidated = false) {
  const selected = state.selectedAsset && state.selectedAsset.stage === stage && state.selectedAsset.key === card.key;
  const classes = ["asset-card"];
  if (selected) classes.push("selected");
  if (invalidated) classes.push("invalidated");
  const thumb = card.preview
    ? `<div class="asset-thumb">${renderAssetMedia(card.preview, card.title, assetForPath(card.preview))}</div>`
    : "";
  const meta = Object.entries(card.meta || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `<span class="tag">${escapeHtml(key)}：${escapeHtml(String(value))}</span>`)
    .join("");
  return `
  <button type="button" class="${classes.join(" ")}" data-asset-key="${escapeHtml(card.key)}" data-stage="${stage}">
    ${thumb}
    <div class="asset-head">
      <strong>${escapeHtml(card.title)}</strong>
      ${card.statusLabel ? `<span class="status-pill ${toneToPill(card.tone)}">${escapeHtml(card.statusLabel)}</span>` : ""}
    </div>
    ${card.summary ? `<p class="asset-summary">${escapeHtml(card.summary)}</p>` : ""}
    ${meta ? `<div class="asset-meta">${meta}</div>` : ""}
  </button>`;
}

function toneToPill(tone) {
  return { active: "active", review: "warning", done: "success", failed: "danger", modified: "warning", invalidated: "neutral", skipped: "neutral", idle: "neutral" }[tone] || "neutral";
}

function stageEmptyText(stage) {
  return {
    text: "启动改编流程后显示文本理解结果（摘要、人物、场景、事件与引用）。",
    storyline: "中等文本启动改编后，这里出现 2～3 条候选故事线。",
    adaptation: "启动改编流程后，这里出现候选改编方案。",
    bible: "选定改编方案并确认范围后，这里生成 Story Bible。",
    storyboard: "确认 Story Bible 后，这里生成分镜草案。",
    keyframes: "确认分镜后，这里显示每个镜头的首帧、尾帧与参考图。",
    video: "确认分镜后，这里显示每个镜头的视频、Provider、模型与任务进度。",
    assembly: "各镜头视频就绪后，这里合成并预览成片。",
  }[stage] || "该阶段暂无内容。";
}

/* ---- 文本理解 ---- */
function textStageHtml(project) {
  const cards = stageAssets(project, "text");
  const scaleLine = `<div class="prompt-block"><strong>原文规模</strong><br />${escapeHtml(project.text_scale_label || "")} · 共 ${escapeHtml(String((project.source_text || "").length))} 字</div>`;
  if (!cards.length) return `${scaleLine}<div class="empty-state">启动改编流程后显示文本理解结果。</div>`;
  return `${scaleLine}${assetGridHtml(project, "text")}`;
}

/* ---- 故事线选择（中等文本） ---- */
function storylineStageHtml(project) {
  const storylines = project.storylines || [];
  if (!storylines.length) {
    return `<div class="empty-state">启动改编流程后，这里出现候选故事线。当前状态：${escapeHtml(project.status || "")}</div>`;
  }
  const scope = project.adaptation_scope;
  const selectedLine = storylines.find((item) => item.selected);
  const selectedEventIds = new Set(scope?.event_ids || selectedLine?.event_ids || []);
  const cards = storylines
    .map(
      (item) => `
      <article class="option-card ${item.selected ? "active" : ""}">
        <div class="section-title">
          <h3>${escapeHtml(item.title)}</h3>
          ${item.selected ? `<span class="tag">已选</span>` : ""}
        </div>
        <p><strong>主角：</strong>${escapeHtml(item.protagonist || "")}</p>
        <p><strong>目标：</strong>${escapeHtml(item.protagonist_goal || "")}</p>
        <p><strong>冲突：</strong>${escapeHtml(item.conflict || "")}</p>
        <p><strong>覆盖：</strong>${escapeHtml(String((item.chunk_ids || []).length))} 块 · ${escapeHtml(String((item.event_ids || []).length))} 事件 · ${escapeHtml(String(item.suggested_duration_seconds))}s · ${escapeHtml(String(item.suggested_shot_count))} 镜</p>
        <p><strong>推荐理由：</strong>${escapeHtml(item.rationale || "")}</p>
        <blockquote>引用：「${escapeHtml(item.source_excerpt || "")}」</blockquote>
        <button class="secondary-btn mini-btn" data-adapt="select-storyline" data-storyline-id="${escapeHtml(item.id)}">选择此故事线</button>
      </article>`
    )
    .join("");
  const eventChecks = selectedLine
    ? dedupe(
        (project.story_events || []).filter((event) => (selectedLine.event_ids || []).includes(event.id) || selectedEventIds.has(event.id))
      )
        .map(
          (event) => `
          <label class="prompt-block">
            <input type="checkbox" data-event-check value="${escapeHtml(event.id)}" ${selectedEventIds.has(event.id) ? "checked" : ""} />
            <strong>${escapeHtml(event.title)}</strong>
            <p>${escapeHtml(event.summary || "")}</p>
            <p class="muted-text">原文：「${escapeHtml(event.source_excerpt || "")}」 · 偏移 ${escapeHtml(String(event.source_start))}–${escapeHtml(String(event.source_end))}</p>
          </label>`
        )
        .join("")
    : "";
  const scopedPreview = scope?.scoped_text
    ? `<blockquote>系统将把以下选中范围交给后续改编（共 ${escapeHtml(String(scope.scoped_text.length))} 字）：「${escapeHtml(scope.scoped_text.slice(0, 280))}${scope.scoped_text.length > 280 ? "…" : ""}」</blockquote>`
    : `<p class="muted-text">选择故事线并勾选事件后，这里会显示实际交给改编的原文。</p>`;
  const scopePanel = selectedLine
    ? `<div class="prompt-block">
        <strong>组成事件（可勾选/取消少量事件）</strong>
        ${eventChecks}
        ${scopedPreview}
        <label>修改说明<input id="scopeUserNote" value="${escapeHtml(scope?.user_note || "")}" /></label>
        <div class="button-row compact-row">
          <button class="secondary-btn mini-btn" data-adapt="recommend-scope">按推荐范围继续</button>
          <button class="secondary-btn mini-btn" data-adapt="save-medium-scope">保存范围</button>
          <button class="primary-btn mini-btn" data-adapt="confirm-medium-scope">确认范围并进入改编</button>
          <button class="secondary-btn mini-btn" data-adapt="regen-medium" data-stage="analysis">修改后重生成分析</button>
        </div>
      </div>`
    : `<p class="muted-text">请先选择一条故事线。</p>`;
  return `<div class="asset-grid single">${cards}</div>${scopePanel}`;
}

/* ---- 改编方案 ---- */
function adaptationStageHtml(project) {
  const options = project.adaptation_options || [];
  if (!options.length) {
    return `<div class="empty-state">启动改编流程后，这里出现候选改编方案。当前状态：${escapeHtml(project.status || "")}</div>`;
  }
  const cards = options
    .map(
      (item) => `
      <article class="option-card ${item.selected ? "active" : ""}">
        <div class="section-title">
          <h3>${escapeHtml(item.title)}</h3>
          ${item.selected ? `<span class="tag">已选</span>` : ""}
        </div>
        <p><strong>冲突：</strong>${escapeHtml(item.conflict)}</p>
        <p><strong>时长建议：</strong>${escapeHtml(String(item.suggested_duration_seconds))}s · ${escapeHtml(String(item.suggested_shot_count))} 镜</p>
        <p><strong>推荐理由：</strong>${escapeHtml(item.rationale)}</p>
        <blockquote>引用：「${escapeHtml(item.source_excerpt)}」</blockquote>
        <div class="button-row compact-row">
          <button class="secondary-btn mini-btn" data-adapt="select-option" data-option-id="${escapeHtml(item.id)}">选择此方案</button>
          <button class="primary-btn mini-btn" data-adapt="confirm-scope" data-option-id="${escapeHtml(item.id)}">确认范围并生成 Bible</button>
        </div>
      </article>`
    )
    .join("");
  return `<div class="asset-grid single">${cards}</div>
    <div class="button-row compact-row">
      <button class="secondary-btn mini-btn" data-adapt="regen" data-stage="scope">修改后重生成改编方案</button>
    </div>`;
}

/* ---- Story Bible ---- */
function bibleStageHtml(project) {
  const bible = project.story_bible;
  if (!bible) {
    return `<div class="empty-state">选定方案并确认范围后，这里生成可编辑的 Story Bible。</div>`;
  }
  const cards = (bible?.character_cards || [])
    .map(
      (card, index) => `
      <div class="prompt-block">
        <strong>角色 ${index + 1}</strong>
        <label>名称<input data-bible-card="character" data-index="${index}" data-field="name" value="${escapeHtml(card.name || "")}" /></label>
        <label>身份/关系<input data-bible-card="character" data-index="${index}" data-field="identity" value="${escapeHtml(card.identity || card.role || "")}" /></label>
        <label>外观<input data-bible-card="character" data-index="${index}" data-field="appearance" value="${escapeHtml(card.appearance || "")}" /></label>
        <label>动机<input data-bible-card="character" data-index="${index}" data-field="motivation" value="${escapeHtml(card.motivation || "")}" /></label>
        <label>不可改变特征<input data-bible-card="character" data-index="${index}" data-field="invariant" value="${escapeHtml(card.invariant || "")}" /></label>
      </div>`
    )
    .join("");
  const sceneCards = (bible?.scene_cards || [])
    .map(
      (card, index) => `
      <div class="prompt-block">
        <strong>场景 ${index + 1}</strong>
        <label>名称<input data-bible-card="scene" data-index="${index}" data-field="name" value="${escapeHtml(card.name || "")}" /></label>
        <label>环境<input data-bible-card="scene" data-index="${index}" data-field="environment" value="${escapeHtml(card.environment || "")}" /></label>
        <label>时间<input data-bible-card="scene" data-index="${index}" data-field="time" value="${escapeHtml(card.time || "")}" /></label>
        <label>视觉元素<input data-bible-card="scene" data-index="${index}" data-field="visuals" value="${escapeHtml(card.visuals || "")}" /></label>
        <label>不可改变特征<input data-bible-card="scene" data-index="${index}" data-field="invariant" value="${escapeHtml(card.invariant || "")}" /></label>
      </div>`
    )
    .join("");
  return `
    <div class="prompt-block" id="bibleForm">
      <strong>Story Bible${bible.review_status === "confirmed" ? " · 已确认" : " · 可编辑"}</strong>
      <span class="muted-text">${bible.review_status === "confirmed" ? "已确认。再次修改需重新确认并会使下游分镜失效。" : "编辑后请保存或确认。"}</span>
      <label>Logline<textarea id="bibleLogline" rows="2" data-bible-track>${escapeHtml(bible.logline || "")}</textarea></label>
      <label>改编摘要<textarea id="bibleSummary" rows="3" data-bible-track>${escapeHtml(bible.adaptation_summary || bible.summary || "")}</textarea></label>
      <label>主题与情绪曲线<input id="bibleEmotion" data-bible-track value="${escapeHtml(bible.emotion_curve || "")}" /></label>
      <label>主角<input id="bibleHero" data-bible-track value="${escapeHtml(bible.protagonist || "")}" /></label>
      <label>目标<input id="bibleGoal" data-bible-track value="${escapeHtml(bible.protagonist_goal || "")}" /></label>
      <label>阻碍<input id="bibleObstacle" data-bible-track value="${escapeHtml(bible.obstacle || "")}" /></label>
      <label>全局视觉风格<input id="bibleStyle" data-bible-track value="${escapeHtml(bible.visual_style || "")}" /></label>
      <label>一致性约束<textarea id="bibleConstraints" rows="2" data-bible-track>${escapeHtml(bible.consistency_constraints || "")}</textarea></label>
      ${cards}${sceneCards}
      <div class="button-row compact-row">
        <span id="bibleDirty" class="clean-flag">草稿已同步</span>
      </div>
      <div class="button-row compact-row">
        <button class="secondary-btn mini-btn" data-adapt="save-bible">保存草稿</button>
        <button class="primary-btn mini-btn" data-adapt="confirm-bible">采用并继续（确认并生成分镜）</button>
        <button class="secondary-btn mini-btn" data-adapt="regen" data-stage="bible" data-redo-btn disabled>从此处重做并继续</button>
        <button class="secondary-btn mini-btn" data-adapt="discard-bible">放弃修改</button>
      </div>
    </div>`;
}

/* ---- 分镜设计 ---- */
function storyboardStageHtml(project) {
  const drafts = project.storyboard_drafts || [];
  if (!drafts.length) {
    // 已确认分镜后展示制作镜头结果。
    const shots = project.shots || [];
    if (shots.length) {
      return `<div class="prompt-block"><strong>分镜已确认，已进入镜头制作。</strong>关键帧与视频请在对应阶段查看。</div>${assetGridHtml(project, "storyboard")}`;
    }
    return `<div class="empty-state">确认 Story Bible 后，这里生成分镜草案。</div>`;
  }
  const rows = drafts
    .map(
      (item) => `
      <article class="option-card">
        <div class="section-title">
          <strong>${escapeHtml(item.title)}</strong>
          <span class="tag">镜 ${escapeHtml(String(item.shot_index))}</span>
        </div>
        <label>叙事目的<input data-board-id="${escapeHtml(item.id)}" data-board-field="narrative_purpose" data-board-track value="${escapeHtml(item.narrative_purpose || "")}" /></label>
        <label>角色<input data-board-id="${escapeHtml(item.id)}" data-board-field="characters" data-board-track value="${escapeHtml((item.characters || []).join("、"))}" /></label>
        <label>场景<input data-board-id="${escapeHtml(item.id)}" data-board-field="scene" data-board-track value="${escapeHtml(item.scene || "")}" /></label>
        <label>动作<textarea data-board-id="${escapeHtml(item.id)}" data-board-field="action_text" data-board-track rows="2">${escapeHtml(item.action_text || "")}</textarea></label>
        <label>运镜<input data-board-id="${escapeHtml(item.id)}" data-board-field="camera_motion" data-board-track value="${escapeHtml(item.camera_motion || "")}" /></label>
        <label>时长（秒）<input data-board-id="${escapeHtml(item.id)}" data-board-field="duration_seconds" data-board-track type="number" min="1" max="15" value="${escapeHtml(String(item.duration_seconds || 5))}" /></label>
        <p class="muted-text">原文依据：「${escapeHtml(item.source_excerpt || "")}」</p>
      </article>`
    )
    .join("");
  return `
    <div class="prompt-block">
      <strong>分镜审核</strong>
      <span id="boardDirty" class="clean-flag">草稿已同步</span>
    </div>
    <div class="asset-grid single">${rows}</div>
    <div class="button-row compact-row">
      <button class="secondary-btn mini-btn" data-adapt="save-storyboard">保存草稿</button>
      <button class="primary-btn mini-btn" data-adapt="confirm-storyboard">采用并继续（确认分镜）</button>
      <button class="secondary-btn mini-btn" data-adapt="regen" data-stage="storyboard" data-redo-btn disabled>从此处重做并继续</button>
      <button class="secondary-btn mini-btn" data-adapt="discard-storyboard">放弃修改</button>
    </div>`;
}

/* ================================================================== */
/* 单素材详情 / 编辑区                                                   */
/* ================================================================== */

function renderAssetDetail(project, stage) {
  const box = el("assetDetail");
  const sel = state.selectedAsset;
  if (!sel || sel.stage !== stage) {
    box.innerHTML = "";
    return;
  }
  if (stage === "video" || stage === "keyframes") {
    const shot = findShotByAssetKey(project, sel.key);
    if (shot) {
      state.selectedShotId = shot.id;
      box.innerHTML = `<div class="asset-detail-inner">${shotEditorHtml(project, shot, stage)}</div>`;
      // 捕获编辑基线（归一化后的草稿快照）：仅在切换到不同镜头时重置，
      // 避免任务事件重绘把用户未保存的修改误判为“已同步”。
      if (state.stageEdit?.shotId !== shot.id) {
        state.stageEdit = { shotId: shot.id, baseline: { ...(state.videoDraft || {}) } };
      }
      return;
    }
  }
  if (stage === "assembly") {
    box.innerHTML = `<div class="asset-detail-inner">${assemblyDetailHtml(project)}</div>`;
    return;
  }
  // 其它阶段：展示选中素材的只读详情。
  const card = stageAssets(project, stage).find((item) => item.key === sel.key);
  if (!card) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<div class="asset-detail-inner">${genericDetailHtml(card, stage)}</div>`;
}

function findShotByAssetKey(project, key) {
  // keyframe key 形如 frame:shotId:first；video key 形如 video:shotId。
  const shots = project?.shots || [];
  const byShot = key.match(/:(shot_[^:]+)/);
  if (byShot) return shots.find((shot) => shot.id === byShot[1]) || null;
  const videoMatch = key.match(/^video:(.+)$/);
  if (videoMatch) return shots.find((shot) => shot.id === videoMatch[1]) || null;
  return null;
}

function genericDetailHtml(card, stage) {
  const stageLabel = STAGES.find((s) => s.id === stage)?.label || stage;
  const meta = Object.entries(card.meta || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `<div class="meta-item"><span class="meta-key">${escapeHtml(key)}</span><span class="meta-value">${escapeHtml(String(value))}</span></div>`)
    .join("");
  const preview = card.preview
    ? `<div class="asset-detail-preview">${renderAssetMedia(card.preview, card.title, assetForPath(card.preview))}</div>`
    : "";
  return `
    <div class="asset-detail-head">
      <div>
        <h3>${escapeHtml(card.title)}</h3>
        <p class="muted-text">阶段：${escapeHtml(stageLabel)} · 类型：${escapeHtml(card.kind)}</p>
      </div>
      ${card.statusLabel ? `<span class="status-pill ${toneToPill(card.tone)}">${escapeHtml(card.statusLabel)}</span>` : ""}
    </div>
    <div class="asset-detail-grid">
      ${preview}
      <div class="asset-detail-fields">
        ${card.summary ? `<p>${escapeHtml(card.summary)}</p>` : ""}
        <div class="detail-meta-grid">${meta}</div>
      </div>
    </div>`;
}

function assemblyDetailHtml(project) {
  const finalAsset = (project.assets || []).find((asset) => asset.type === "final-video");
  const preview = finalAsset
    ? `<div class="asset-detail-preview"><video src="${escapeHtml(finalAsset.file_path)}" controls playsinline></video>${renderMediaBadge(finalAsset)}</div>`
    : `<div class="asset-detail-preview"><div class="asset-thumb-empty">尚未生成成片</div></div>`;
  return `
    <div class="asset-detail-head">
      <div>
        <h3>成片合成</h3>
        <p class="muted-text">${project.assembly_stale ? "成片已过期：镜头版本变化后需要重新合成（不会自动执行）。" : "将各镜头视频合成为可播放短片。"}</p>
      </div>
      ${finalAsset ? `<span class="status-pill ${project.assembly_stale ? "warning" : "success"}">${project.assembly_stale ? "已过期" : "已生成"}</span>` : ""}
    </div>
    <div class="asset-detail-grid">
      ${preview}
      <div class="asset-detail-fields">
        <p class="muted-text">共 ${(project.shots || []).length} 个镜头参与合成。</p>
        <div class="button-row compact-row">
          <button class="primary-btn mini-btn" data-action="assemble-project">合成成片</button>
          ${finalAsset ? `<a class="secondary-btn mini-btn" href="${escapeHtml(finalAsset.file_path)}" target="_blank" rel="noopener">打开成片</a>` : ""}
        </div>
      </div>
    </div>`;
}

/* ---- 镜头编辑器（视频 / 关键帧阶段共用） ---- */
function shotEditorHtml(project, shot, stage) {
  const version = currentVersion(shot) || latestVersion(shot);
  const videoAsset = assetForPath(version?.video_path);
  const realVideo = isRealShotVideo(videoAsset);
  const task = activeVideoTask(shot);
  const waitingRemote = ["video_waiting_remote", "video_running"].includes(shot.status) || ["running", "pending_remote"].includes(task?.status);
  const diagnosis = videoFailureDiagnosis(task, shot);
  const candidates = keyframeCandidates();
  const draft = syncVideoDraft(shot, version);
  const constraint = evaluateVideoDraft(shot, version, draft);
  const providerOptions = videoProviderOptions(draft.video_mode);
  const modelOptions = videoModelOptions(draft.provider, draft.video_mode);
  const stageLabel = STAGES.find((s) => s.id === stage)?.label || stage;
  const currentLabel = version ? `当前版本 v${version.version_number}` : "尚未冻结版本";
  const videoStatusLabelText = version?.video_path ? (realVideo ? "当前版本视频可预览" : "当前版本视频不可用") : "此版本尚未生成视频";

  const optionHtml = (currentPath) =>
    [`<option value="">未选择</option>`]
      .concat(
        candidates.map((asset) => {
          const selected = asset.file_path === currentPath ? "selected" : "";
          return `<option value="${escapeHtml(asset.file_path)}" ${selected}>${escapeHtml(asset.name)} · ${escapeHtml(mediaSource(asset))}</option>`;
        })
      )
      .join("");

  const taskBlock = task && ["running", "pending_remote", "failed"].includes(task.status)
    ? `<div class="prompt-block video-task-block">
        <strong>${escapeHtml(task.provider || "云端")} 任务</strong><br />
        <span class="tag">${escapeHtml(task.status)}</span>
        <span class="tag">${escapeHtml(task.model || "unknown")}</span>
        <span class="tag">${escapeHtml(task.cloud_status || "unknown")}</span>
        ${task.error_code ? `<span class="tag">${escapeHtml(task.error_code)}</span>` : ""}
        <p class="muted-text">Task ID：${escapeHtml(task.remote_task_id)}</p>
        ${task.error_message ? `<p>${escapeHtml(task.error_message)}</p>` : ""}
      </div>`
    : "";
  const diagnosisBlock = diagnosis
    ? `<div class="prompt-block failure-diagnosis">
        <strong>${escapeHtml(diagnosis.title)}</strong>
        <p>${escapeHtml(diagnosis.detail)}</p>
        ${diagnosis.canSafeRetry ? `<button class="secondary-btn full-width" data-action="safe-retry-video">安全改写并重试</button>` : ""}
      </div>`
    : "";

  const videoPreview = version?.video_path
    ? realVideo
      ? `<div class="asset-detail-preview"><video src="${escapeHtml(version.video_path)}" controls playsinline></video>${renderMediaBadge(videoAsset || version.video_path)}</div>`
      : `<div class="asset-detail-preview"><div class="asset-thumb-empty">静帧占位视频，不能作为正式素材。请重新生成真实视频。</div></div>`
    : waitingRemote
    ? `<div class="asset-detail-preview"><div class="asset-thumb-empty">云端仍在生成，正在回查同一任务，不会重复提交或重复计费。</div></div>`
    : `<div class="asset-detail-preview"><div class="asset-thumb-empty">${escapeHtml(videoStatusLabelText)}</div></div>`;

  const keyframePreview = `
    <div class="asset-detail-preview">${renderAssetMedia(draft.first_frame_path || version?.first_frame_path, "首帧", assetForPath(draft.first_frame_path || version?.first_frame_path))}</div>`;

  const versions = (shot.versions || [])
    .map(
      (item) => `
      <div class="version-item ${item.id === shot.current_version_id ? "active" : ""}">
        <div>
          <strong>v${item.version_number}${item.id === shot.current_version_id ? " · 当前" : ""}</strong>
          <span class="muted-text">${escapeHtml(formatTime(item.created_at))}</span>
          <p class="muted-text">${escapeHtml(item.change_summary || item.provider || item.video_mode || "历史版本")}</p>
          <span class="muted-text">${escapeHtml(item.provider || "未记录")} · ${escapeHtml(item.model || "未记录")} · ${escapeHtml(item.video_mode || "t2v")} · ${escapeHtml(String(item.duration_seconds || "-"))}s</span>
          <p class="muted-text">关键帧：${escapeHtml(frameStatusLabel(item))}</p>
        </div>
        <button class="secondary-btn mini-btn" data-action="rollback-version" data-version-id="${escapeHtml(item.id)}" ${item.id === shot.current_version_id ? "disabled" : ""}>回滚至此版本</button>
      </div>`
    )
    .join("");

  const evidence = shot.rag_evidence || [];
  const evidenceBlock = evidence.length
    ? `<div class="prompt-block"><strong>原文 / RAG 依据</strong>${evidence
        .map((item) => `<div class="evidence-item"><span class="tag">${escapeHtml(item.kind)} · ${Number(item.score || 0).toFixed(2)}</span><p>${escapeHtml(item.label)}：${escapeHtml(item.excerpt)}</p></div>`)
        .join("")}</div>`
    : "";

  const preview = stage === "video" ? videoPreview : keyframePreview;

  return `
    <div class="asset-detail-head">
      <div>
        <h3>${escapeHtml(shot.title)}</h3>
        <p class="muted-text">阶段：${escapeHtml(stageLabel)} · 镜头 #${escapeHtml(String(shot.shot_index))} · 素材类型：${stage === "video" ? "镜头视频" : "关键帧"}</p>
      </div>
      <span id="shotDirty" class="${shot.has_unsaved_changes ? "dirty-flag" : "clean-flag"}">${shot.has_unsaved_changes ? "有未保存修改" : "草稿已同步"}</span>
    </div>
    <div class="asset-detail-grid">
      ${preview}
      <div class="asset-detail-fields">
        <div class="detail-meta-grid">
          <div class="meta-item"><span class="meta-key">当前版本</span><span class="meta-value">${escapeHtml(currentLabel)}</span></div>
          <div class="meta-item"><span class="meta-key">Provider</span><span class="meta-value">${escapeHtml(draft.provider || "未选择")}</span></div>
          <div class="meta-item"><span class="meta-key">模型</span><span class="meta-value">${escapeHtml(draft.model || "未选择")}</span></div>
          <div class="meta-item"><span class="meta-key">模式 / 时长</span><span class="meta-value">${escapeHtml(draft.video_mode || "t2v")} · ${escapeHtml(String(draft.duration_seconds || project?.duration_seconds || 5))}s</span></div>
          <div class="meta-item"><span class="meta-key">输入素材</span><span class="meta-value">${escapeHtml(frameStatusLabel(draft))}</span></div>
          <div class="meta-item"><span class="meta-key">视频状态</span><span class="meta-value">${escapeHtml(videoStatusLabelText)}</span></div>
        </div>
        ${project?.assembly_stale ? `<p class="muted-text">成片已过期，需要重新合成（不会自动执行）。</p>` : ""}
        ${shotProgressHtml(shot)}
      </div>
    </div>

    <div class="prompt-block">
      <strong>镜头编辑草稿</strong>
      <span class="muted-text">编辑只写入草稿。点击「从此处重做并继续」后才会冻结新版本并重新生成；历史版本不会被覆盖。</span>
      <label>自然语言描述<textarea id="shotDescriptionInput" rows="3" data-shot-track>${escapeHtml(draft.description || "")}</textarea></label>
      <label>动作 / 运镜<input id="shotCameraInput" data-shot-track value="${escapeHtml(draft.camera_motion || "")}" /></label>
      <label>视觉提示词<textarea id="shotVisualPromptInput" rows="3" data-shot-track>${escapeHtml(draft.visual_prompt || "")}</textarea></label>
      <div class="form-grid">
        <label>生成模式
          <select id="videoModeSelect" data-shot-track>
            <option value="t2v" ${draft.video_mode === "t2v" ? "selected" : ""}>T2V 文本生成</option>
            <option value="i2v" ${draft.video_mode === "i2v" ? "selected" : ""}>I2V 首帧驱动</option>
            <option value="keyframes" ${draft.video_mode === "keyframes" ? "selected" : ""}>首尾帧约束</option>
          </select>
        </label>
        <label>Provider
          <select id="videoProviderSelect" data-shot-track>
            ${providerOptions.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === draft.provider ? "selected" : ""} ${item.disabled ? "disabled" : ""}>${escapeHtml(item.label)}</option>`).join("")}
          </select>
        </label>
        <label>模型
          <select id="videoModelSelect" data-shot-track>
            ${modelOptions.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === draft.model ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
          </select>
        </label>
        <label>本镜时长
          <select id="videoDurationSelect" data-shot-track>
            ${(constraint.durations || [draft.duration_seconds]).map((item) => `<option value="${item}" ${Number(item) === Number(draft.duration_seconds) ? "selected" : ""}>${item}s</option>`).join("")}
          </select>
        </label>
      </div>
      <p class="muted-text" id="videoCapabilityHint">${escapeHtml(constraint.hint)}</p>
      <label>首帧资产 ${draft.first_frame_path ? "· 已选" : "· 未选"}<select id="firstFrameSelect" data-shot-track>${optionHtml(draft.first_frame_path)}</select></label>
      <label>尾帧资产 ${draft.last_frame_path ? "· 已选" : "· 未选"}<select id="lastFrameSelect" data-shot-track>${optionHtml(draft.last_frame_path)}</select></label>
      <label>参考图 ${draft.reference_frame_path ? "· 已选" : "· 未选"}<select id="referenceFrameSelect" data-shot-track>${optionHtml(draft.reference_frame_path)}</select></label>
      <div class="button-row compact-row">
        <button class="secondary-btn mini-btn" data-action="save-shot-draft">保存草稿</button>
        <button class="secondary-btn mini-btn" data-action="redo-shot" data-redo-btn disabled>从此处重做并继续</button>
        <button class="secondary-btn mini-btn" data-action="discard-shot">放弃修改</button>
        <button class="secondary-btn mini-btn" data-action="freeze-shot-version">基于草稿冻结新版本</button>
      </div>
      <div class="button-row compact-row">
        <button class="secondary-btn mini-btn" data-action="apply-keyframes">写入关键帧到草稿</button>
        <button class="secondary-btn mini-btn" data-action="redraw-keyframe" data-target="first">重绘首帧</button>
        <button class="secondary-btn mini-btn" data-action="redraw-keyframe" data-target="last">重绘尾帧</button>
        ${waitingRemote ? `<button class="secondary-btn mini-btn" data-action="refresh-video-tasks">立即刷新云端任务</button>` : ""}
      </div>
      <button class="primary-btn full-width" data-action="generate-video" ${constraint.ok ? "" : "disabled"} title="${escapeHtml(constraint.reason || "")}">${constraint.ok ? "仅重生成此镜头视频" : escapeHtml(constraint.reason)}</button>
    </div>

    ${taskBlock}
    ${diagnosisBlock}
    ${evidenceBlock}
    <div class="prompt-block version-list"><strong>版本历史</strong>${versions || "<p class='muted-text'>暂无版本</p>"}</div>`;
}

/* ================================================================== */
/* 左侧面板其余部分                                                      */
/* ================================================================== */

function renderProviderDiagnostics() {
  const diagnostics = state.diagnostics;
  const container = el("providerDiagnostics");
  if (!diagnostics) {
    container.className = "provider-diagnostics empty-state";
    container.textContent = "等待服务状态。";
    return;
  }
  const rows = [
    ["LLM", diagnostics.llm],
    ["Image", diagnostics.image],
    ["Video", diagnostics.video],
  ];
  container.className = "provider-diagnostics";
  container.innerHTML = `
    ${rows
      .map(([label, item]) => {
        const ok = item?.configured;
        const status = ok ? "已配置" : "降级可用";
        return `<div class="provider-row">
          <div>
            <strong>${label}</strong>
            <span class="muted-text">${escapeHtml(item?.provider || "unknown")}</span>
          </div>
          <span class="status-pill ${ok ? "success" : "warning"}">${status}</span>
          <p>${escapeHtml(item?.model || item?.fallback || "N/A")}</p>
        </div>`;
      })
      .join("")}
    <div class="provider-row compact">
      <div><strong>FFmpeg</strong> <span class="muted-text">${diagnostics.tools?.ffmpeg ? "可用" : "未检测到"}</span></div>
    </div>`;
}

function renderConstraints() {
  const constraints = state.project?.global_constraints || [];
  if (!constraints.length) {
    el("constraintList").className = "constraint-list empty-state";
    el("constraintList").textContent = "暂无全局约束。";
    return;
  }
  el("constraintList").className = "constraint-list";
  el("constraintList").innerHTML = constraints
    .map(
      (item) => `<div class="constraint-item">
        <strong>${escapeHtml(item.target)}</strong>
        <p>${escapeHtml(item.positive_prompt)}</p>
      </div>`
    )
    .join("");
}

function renderMemoryResults() {
  const results = state.memoryResults || [];
  const container = el("memoryResults");
  if (!results.length) {
    container.className = "memory-results empty-state";
    container.textContent = "等待检索。";
    return;
  }
  container.className = "memory-results";
  container.innerHTML = results
    .map((item) => {
      const metadata = item.metadata || {};
      return `<article class="memory-item">
        <div class="asset-head">
          <strong>${escapeHtml(metadata.label || item.id)}</strong>
          <span class="tag">${escapeHtml(metadata.kind || "memory")} · ${Number(item.score || 0).toFixed(2)}</span>
        </div>
        <p>${escapeHtml(item.document || "").slice(0, 180)}</p>
      </article>`;
    })
    .join("");
}

/* ================================================================== */
/* 底部：任务中心                                                       */
/* ================================================================== */

function renderJob() {
  const project = state.project;
  const job = (project?.active_jobs || [])[0] || project?.jobs?.[0];
  const latest = state.jobEvents[state.jobEvents.length - 1];
  const message = latest?.message || job?.message || "等待任务。";
  const status = latest?.status || job?.status || "idle";
  const progress = latest?.progress ?? job?.progress ?? 0;
  const waiting = status === "waiting_remote";
  el("jobMessage").textContent = message;
  el("jobHint").textContent = waiting
    ? "正在回查同一云端任务，不会重复提交或重复计费。"
    : state.sseConnected
      ? "实时更新已连接。"
      : ["queued", "running", "waiting_remote", "paused"].includes(status)
        ? "实时通道断开，正在用低频轮询恢复任务状态。"
        : "";
  el("jobStatus").textContent = jobStatusLabel(status);
  el("jobStatus").className = `status-pill ${statusClass(status)}`;
  el("jobProgress").style.width = `${progress || 0}%`;

  // 展开 / 收起
  const open = state.timelineOpen;
  el("taskCenterBody").classList.toggle("hidden", !open);
  el("taskCenterToggle").setAttribute("aria-expanded", String(open));
  el("taskCenterChevron").textContent = open ? "收起" : "展开";
  if (!open) return;

  // 任务列表
  const shotTitles = {};
  (project?.shots || []).forEach((shot) => {
    shotTitles[shot.id] = shot.title;
  });
  const rows = jobCenterRows(project, shotTitles);
  el("jobList").innerHTML = rows.length
    ? rows
        .map(
          (row) => `
        <div class="job-row ${row.status === "failed" ? "failed" : ""}">
          <div class="job-row-head">
            <strong>${escapeHtml(row.name)}${row.shotTitle ? ` · ${escapeHtml(row.shotTitle)}` : ""}</strong>
            <span class="status-pill ${statusClass(row.status)}">${escapeHtml(row.statusLabel)}</span>
          </div>
          <div class="job-row-progress"><span style="width:${row.progress}%"></span></div>
          <div class="job-row-meta">
            ${row.provider ? `<span>Provider：${escapeHtml(row.provider)}</span>` : ""}
            ${row.model ? `<span>模型：${escapeHtml(row.model)}</span>` : ""}
            <span>进度 ${row.progress}%</span>
            <span>${escapeHtml(formatTime(row.updatedAt))}</span>
          </div>
          ${row.message ? `<div class="muted-text">${escapeHtml(row.message)}</div>` : ""}
          ${row.error ? `<div class="job-row-error">失败原因：${escapeHtml(row.error)}</div>` : ""}
        </div>`
        )
        .join("")
    : `<div class="muted-text">暂无任务。</div>`;

  // 最近事件时间线
  const events = [...state.jobEvents].slice(-20).reverse();
  el("jobTimeline").innerHTML = events.length
    ? events
        .map(
          (item) => `<div class="job-timeline-item">
            <span class="tag">${escapeHtml(item.stage || item.status)}</span>
            <span>${escapeHtml(item.message)}</span>
            <span class="muted-text">${escapeHtml(item.progress ?? 0)}%</span>
          </div>`
        )
        .join("")
    : `<div class="muted-text">暂无任务事件。</div>`;
}

function shotProgressHtml(shot) {
  const event = state.shotProgress[shot.id];
  if (!event) return "";
  const provider = event.detail?.provider || "";
  return `<p class="muted-text">${escapeHtml(event.message || "")}${provider ? ` · ${escapeHtml(provider)}` : ""} · ${escapeHtml(event.progress ?? 0)}%</p>`;
}

/* ================================================================== */
/* 能力 / 反馈 / 视频草稿（供 app.js 使用）                              */
/* ================================================================== */

export function renderCapabilities() {
  const videos = state.capabilities?.video || [];
  const ratioInput = el("ratioInput");
  const durationInput = el("durationInput");
  ratioInput.innerHTML = "";
  durationInput.innerHTML = "";
  uniqueValues(videos.flatMap((item) => item.supported_ratios || [])).forEach((ratio) => {
    ratioInput.append(new Option(ratio, ratio));
  });
  uniqueValues(videos.flatMap((item) => item.supported_durations || [])).forEach((duration) => {
    durationInput.append(new Option(`${duration}s`, duration));
  });
  if (!ratioInput.options.length) {
    ["16:9", "9:16", "1:1"].forEach((ratio) => ratioInput.append(new Option(ratio, ratio)));
  }
  if (!durationInput.options.length) {
    [5].forEach((duration) => durationInput.append(new Option(`${duration}s`, duration)));
  }
}

export function renderFeedbackResult(result) {
  if (!result) {
    el("feedbackResult").innerHTML = "";
    return;
  }
  el("feedbackResult").innerHTML = `
    <div class="prompt-block">
      <strong>${result.scope === "global" ? "全局约束" : "局部修改"}</strong><br />
      ${escapeHtml(result.reason)}<br />
      <span class="muted-text">${escapeHtml(result.positive_prompt)}</span>
    </div>`;
}

export function currentVideoDraftPayload() {
  const collected = typeof document !== "undefined" ? {
    description: document.getElementById("shotDescriptionInput")?.value,
    camera_motion: document.getElementById("shotCameraInput")?.value,
    visual_prompt: document.getElementById("shotVisualPromptInput")?.value,
    video_mode: document.getElementById("videoModeSelect")?.value,
    provider: document.getElementById("videoProviderSelect")?.value,
    model: document.getElementById("videoModelSelect")?.value,
    duration_seconds: Number(document.getElementById("videoDurationSelect")?.value || state.videoDraft?.duration_seconds || state.project?.duration_seconds || 5),
    first_frame_path: document.getElementById("firstFrameSelect")?.value || null,
    last_frame_path: document.getElementById("lastFrameSelect")?.value || null,
    reference_frame_path: document.getElementById("referenceFrameSelect")?.value || null,
  } : {};
  const draft = state.videoDraft || {};
  return {
    description: collected.description ?? draft.description,
    camera_motion: collected.camera_motion ?? draft.camera_motion,
    visual_prompt: collected.visual_prompt ?? draft.visual_prompt,
    video_mode: collected.video_mode || draft.video_mode || "t2v",
    provider: collected.provider || draft.provider,
    model: collected.model || draft.model,
    duration_seconds: Number(collected.duration_seconds || draft.duration_seconds || 5),
    first_frame_path: collected.first_frame_path || draft.first_frame_path || null,
    last_frame_path: collected.last_frame_path || draft.last_frame_path || null,
    reference_frame_path: collected.reference_frame_path || draft.reference_frame_path || null,
  };
}

/* ---- 视频草稿内部 helpers（供镜头编辑器使用） ---- */

function videoProviders() {
  return state.capabilities?.video || [];
}

function findVideoProvider(providerId) {
  return videoProviders().find((item) => item.id === providerId) || null;
}

function videoProviderOptions(videoMode) {
  return videoProviders().map((item) => {
    const supportsMode = (item.supported_modes || []).includes(videoMode);
    const configured = item.mode === "live-ready";
    const disabled = !supportsMode;
    const suffix = !supportsMode ? "（不支持该模式）" : configured ? "" : "（未配置密钥）";
    return { id: item.id, label: `${item.label}${suffix}`, disabled };
  });
}

function videoModelOptions(providerId, videoMode) {
  const provider = findVideoProvider(providerId);
  return (provider?.models || []).filter((item) => (item.supported_modes || []).includes(videoMode));
}

function frameStatusLabel(source) {
  const first = source?.first_frame_path ? "首帧已选" : "无首帧";
  const last = source?.last_frame_path ? "尾帧已选" : "无尾帧";
  const reference = source?.reference_frame_path ? "参考图已选" : "无参考图";
  return `${first} · ${last} · ${reference}`;
}

function syncVideoDraft(shot, version) {
  const persisted = shot.draft || {};
  const defaultProvider = state.capabilities?.default_video_provider || videoProviders().find((item) => item.mode === "live-ready")?.id || videoProviders()[0]?.id;
  const existing = state.videoDraft?.shotId === shot.id ? state.videoDraft : null;
  const videoMode = existing?.video_mode || persisted.video_mode || version?.video_mode || "t2v";
  let provider = existing?.provider || persisted.provider || version?.provider || defaultProvider;
  const providers = videoProviderOptions(videoMode).filter((item) => !item.disabled);
  if (!providers.some((item) => item.id === provider)) {
    provider = providers[0]?.id || defaultProvider;
  }
  const models = videoModelOptions(provider, videoMode);
  let model = existing?.model || persisted.model || version?.model || findVideoProvider(provider)?.default_model;
  if (!models.some((item) => item.id === model)) {
    model = models[0]?.id || "";
  }
  const durations = findVideoProvider(provider)?.supported_durations || [state.project?.duration_seconds || 5];
  let duration = Number(existing?.duration_seconds || persisted.duration_seconds || version?.duration_seconds || state.project?.duration_seconds || durations[0]);
  if (!durations.map(Number).includes(duration)) {
    duration = durations[0];
  }
  state.videoDraft = {
    shotId: shot.id,
    dirty: Boolean(existing?.dirty),
    description: existing?.description ?? persisted.description ?? version?.description ?? shot.description ?? "",
    camera_motion: existing?.camera_motion ?? persisted.camera_motion ?? version?.camera_motion ?? shot.camera_motion ?? "",
    visual_prompt: existing?.visual_prompt ?? persisted.visual_prompt ?? version?.visual_prompt ?? shot.visual_prompt ?? "",
    video_mode: videoMode,
    provider,
    model,
    duration_seconds: duration,
    first_frame_path: existing?.first_frame_path ?? persisted.first_frame_path ?? version?.first_frame_path ?? null,
    last_frame_path: existing?.last_frame_path ?? persisted.last_frame_path ?? version?.last_frame_path ?? null,
    reference_frame_path: existing?.reference_frame_path ?? persisted.reference_frame_path ?? version?.reference_frame_path ?? null,
  };
  return state.videoDraft;
}

function evaluateVideoDraft(shot, version, draft) {
  const provider = findVideoProvider(draft.provider);
  const requirements = state.capabilities?.mode_requirements?.[draft.video_mode] || {};
  const models = videoModelOptions(draft.provider, draft.video_mode);
  const durations = provider?.supported_durations || [];
  const ratios = provider?.supported_ratios || [];
  const projectRatio = state.project?.aspect_ratio;
  const firstReady = Boolean(draft.first_frame_path || version?.first_frame_path);
  const lastReady = Boolean(draft.last_frame_path || version?.last_frame_path);
  const resolution = models.find((item) => item.id === draft.model)?.default_resolution || provider?.default_resolution || "未声明";
  const hint = [
    provider ? `${provider.label}` : "未选择 Provider",
    draft.model || "未选择模型",
    `分辨率 ${resolution}`,
    `支持时长 ${(durations || []).join("/") || "?"}s`,
    `比例 ${(ratios || []).join("/") || "?"}`,
    frameStatusLabel(draft),
  ].join(" · ");
  if (!provider) return { ok: false, reason: "请选择视频 Provider", hint, durations };
  if (!(provider.supported_modes || []).includes(draft.video_mode)) return { ok: false, reason: "该 Provider 不支持当前生成模式", hint, durations };
  if (!draft.model || !models.some((item) => item.id === draft.model)) return { ok: false, reason: "该模型不支持当前生成模式", hint, durations };
  if (projectRatio && ratios.length && !ratios.includes(projectRatio)) return { ok: false, reason: `该 Provider 不支持比例 ${projectRatio}`, hint, durations };
  if (durations.length && !durations.map(Number).includes(Number(draft.duration_seconds))) return { ok: false, reason: `该 Provider 不支持 ${draft.duration_seconds}s`, hint, durations };
  if (requirements.requires_first_frame && !firstReady) return { ok: false, reason: "缺少首帧，无法提交 I2V", hint, durations };
  if (requirements.requires_last_frame && !lastReady) return { ok: false, reason: "缺少尾帧，无法提交首尾帧模式", hint, durations };
  return { ok: true, reason: "", hint, durations };
}
