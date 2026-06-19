import { agents, latestVersion, selectedShot, state } from "./state.js";

const el = (id) => document.getElementById(id);

function statusClass(status) {
  if (["completed", "ready_for_review", "keyframes_ready", "passed", "video_ready"].includes(status)) return "success";
  if (["running", "queued", "needs_regeneration", "paused", "review_pending", "waiting_remote"].includes(status)) return "warning";
  if (["video_running", "video_waiting_remote"].includes(status)) return "warning";
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
  // 单镜头视频必须来自模型 Provider，FFmpeg 结果只作为最终成片。
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
    return {
      title: "内容安全 / 版权策略拦截",
      detail: "该镜头可以先做原创化安全改写，再用 T2V 重试，避免命名作品、文字复现或过强风格指向。",
      canSafeRetry: true,
    };
  }
  if (code.includes("SetLimitExceeded") || message.includes("SetLimitExceeded")) {
    return {
      title: "平台推理额度或并发限制",
      detail: "这类错误通常需要等待额度恢复，或在火山方舟控制台调整模型推理限制。",
      canSafeRetry: false,
    };
  }
  if (code.includes("AccessDenied") || message.includes("AccessDenied")) {
    return {
      title: "模型或接入点权限不足",
      detail: "请检查 Seedance 模型、接入点和 API Key 是否开通了对应权限。",
      canSafeRetry: false,
    };
  }
  return {
    title: "视频任务失败",
    detail: "可以先回查云端任务；如果错误来自 Prompt 内容，再尝试安全改写重试。",
    canSafeRetry: true,
  };
}

function renderMediaBadge(assetOrPath) {
  return `<span class="media-badge">${escapeHtml(mediaSource(assetOrPath))}</span>`;
}

function renderAssetMedia(path, label, asset) {
  if (!path) return "";
  const safePath = escapeHtml(path);
  const safeLabel = escapeHtml(label);
  const suffix = path.split(".").pop()?.toLowerCase();
  if (suffix === "mp4" && isInvalidVideoAsset(asset)) {
    return `<div class="invalid-media-tile">无效占位视频</div>${renderMediaBadge(asset || path)}`;
  }
  const media =
    suffix === "mp4"
      ? `<video src="${safePath}" muted playsinline preload="metadata"></video>`
      : `<img src="${safePath}" alt="${safeLabel}" />`;
  return `${media}${renderMediaBadge(asset || path)}`;
}

export function renderAll() {
  // 所有界面都从同一份 state 重绘，避免异步任务结束后出现半更新面板。
  renderProjects();
  renderProjectStatus();
  renderAgentFlow();
  renderAssetSummary();
  renderWorkflowControls();
  renderStoryBible();
  renderProviderDiagnostics();
  renderShots();
  renderInspector();
  renderConstraints();
  renderMemoryResults();
  renderAssetLibrary();
  renderJob();
}

export function renderCapabilities() {
  const video = state.capabilities?.video?.[0];
  const ratioInput = el("ratioInput");
  const durationInput = el("durationInput");
  ratioInput.innerHTML = "";
  durationInput.innerHTML = "";
  (video?.supported_ratios || ["16:9", "9:16", "1:1"]).forEach((ratio) => {
    ratioInput.append(new Option(ratio, ratio));
  });
  (video?.supported_durations || [5]).forEach((duration) => {
    durationInput.append(new Option(`${duration}s`, duration));
  });
}

function renderProjects() {
  el("projectCount").textContent = String(state.projects.length);
  if (state.projects.length === 0) {
    el("projectList").innerHTML = `<div class="empty-state">暂无项目。</div>`;
    return;
  }
  el("projectList").innerHTML = state.projects
    .map(
      (project) => `
      <button class="project-item ${state.project?.id === project.id ? "active" : ""}" data-project-id="${project.id}">
        <div class="shot-meta">
          <strong>${escapeHtml(project.title)}</strong>
          <span class="status-pill ${statusClass(project.status)}">${escapeHtml(project.status)}</span>
        </div>
        <p class="muted-text">${escapeHtml(project.style)} · ${escapeHtml(project.aspect_ratio)}</p>
      </button>`
    )
    .join("");
}

function renderProjectStatus() {
  const project = state.project;
  const status = project?.status || "未创建";
  el("projectStatus").textContent = status;
  el("projectStatus").className = `status-pill ${statusClass(status)}`;
  el("routingBadge").textContent = project?.routing_mode || "Direct";
  el("selectedShotBadge").textContent = selectedShot()?.title || "未选择";
}

function renderAgentFlow() {
  const status = state.project?.status || "draft";
  const doneCount = status === "ready_for_review" ? agents.length : status === "review_pending" ? 4 : status === "running" ? 3 : 0;
  el("agentFlow").innerHTML = agents
    .map((agent, index) => {
      const className = index < doneCount ? "done" : status === "running" && index === doneCount ? "running" : "";
      return `<div class="agent-node ${className}">
        <strong>${agent.label}</strong>
        <small>${agent.detail}</small>
      </div>`;
    })
    .join("");
}

function renderWorkflowControls() {
  const project = state.project;
  const canResume = project?.status === "review_pending" && !!project?.checkpoint;
  const canRetry = !!project && !["running"].includes(project.status);
  el("resumeWorkflowBtn").disabled = !canResume;
  el("retryWorkflowBtn").disabled = !canRetry;
  el("resumeWorkflowBtn").title = canResume ? "从监制检查点继续执行" : "只有监制模式暂停后才能恢复";
  el("retryWorkflowBtn").title = canRetry ? "重新运行完整 LangGraph 工作流" : "任务运行中";
}

function renderAssetSummary() {
  const project = state.project;
  if (!project || !(project.assets || []).length) {
    el("assetSummaryBar").className = "asset-summary-bar empty-state";
    el("assetSummaryBar").textContent = "视觉资产摘要会在项目运行后显示。";
    return;
  }
  const assets = project.assets || [];
  const firstFrames = assets.filter((asset) => asset.type === "first-frame").length;
  const lastFrames = assets.filter((asset) => asset.type === "last-frame").length;
  const videos = assets.filter((asset) => asset.type === "video" && isRealShotVideo(asset)).length;
  const placeholderVideos = assets.filter((asset) => asset.type === "video" && !isRealShotVideo(asset)).length;
  const finalVideos = assets.filter((asset) => asset.type === "final-video").length;
  const mockAssets = assets.filter((asset) => mediaSource(asset) === "Mock").length;
  const continuityAssets = assets.filter((asset) => mediaSource(asset) === "Continuity").length;
  el("assetSummaryBar").className = "asset-summary-bar";
  el("assetSummaryBar").innerHTML = `
    <span><strong>${project.characters?.length || 0}</strong> 角色</span>
    <span><strong>${project.scenes?.length || 0}</strong> 场景</span>
    <span><strong>${firstFrames}</strong> 首帧</span>
    <span><strong>${lastFrames}</strong> 尾帧</span>
    <span><strong>${videos}</strong> 真实视频</span>
    <span><strong>${placeholderVideos}</strong> 无效占位</span>
    <span><strong>${finalVideos}</strong> 成片</span>
    <span><strong>${continuityAssets}</strong> 连续</span>
    <span><strong>${mockAssets}</strong> 占位</span>
    <span><strong>${assets.length}</strong> 总资产</span>
    <span><strong>${project.review_mode ? "开" : "关"}</strong> 监制模式</span>
  `;
}

function renderStoryBible() {
  const bible = state.project?.story_bible;
  if (!bible) {
    el("storyBible").className = "story-bible empty-state";
    el("storyBible").textContent = "创建并运行项目后显示故事摘要、世界观和风格标签。";
    return;
  }
  el("storyBible").className = "story-bible";
  el("storyBible").innerHTML = `
    <p>${escapeHtml(bible.summary)}</p>
    <p>${escapeHtml(bible.worldview)}</p>
    <div class="tag-row">${(bible.style_tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
  `;
}

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
      <div>
        <strong>FFmpeg</strong>
        <span class="muted-text">${diagnostics.tools?.ffmpeg ? "可用" : "未检测到"}</span>
      </div>
    </div>
  `;
}

function renderShots() {
  const shots = state.project?.shots || [];
  const videoReady = shots.filter((shot) => {
    const version = latestVersion(shot);
    return version?.video_path && isRealShotVideo(assetForPath(version.video_path));
  }).length;
  el("shotSummary").textContent = shots.length ? `${shots.length} 个镜头，${videoReady} 个真实视频已就绪。` : "等待生成分镜。";
  if (!shots.length) {
    el("shotGrid").className = "shot-grid empty-state";
    el("shotGrid").textContent = "分镜卡片会出现在这里。";
    return;
  }
  el("shotGrid").className = "shot-grid";
  el("shotGrid").innerHTML = shots
    .map((shot) => {
      const version = latestVersion(shot);
      const preview = version?.first_frame_path || "";
      const asset = assetForPath(preview);
      const videoAsset = assetForPath(version?.video_path);
      const videoStatus = version?.video_path ? (isRealShotVideo(videoAsset) ? "video_ready" : "video_invalid") : shot.status;
      return `<button class="shot-card ${shot.id === selectedShot()?.id ? "active" : ""}" data-shot-id="${shot.id}">
        <div class="shot-preview">${renderAssetMedia(preview, shot.title, asset)}</div>
        <div class="shot-meta">
          <strong>${escapeHtml(shot.title)}</strong>
          <span class="status-pill ${statusClass(videoStatus)}">${escapeHtml(videoStatus)}</span>
        </div>
        <p class="shot-description">${escapeHtml(shot.description)}</p>
      </button>`;
    })
    .join("");
}

function renderInspector() {
  const shot = selectedShot();
  if (!shot) {
    el("shotInspector").className = "shot-inspector empty-state";
    el("shotInspector").textContent = "选择一个镜头查看关键帧、Prompt 和修改历史。";
    return;
  }
  const version = latestVersion(shot);
  el("shotInspector").className = "shot-inspector";
  const videoAsset = assetForPath(version?.video_path);
  const realVideo = isRealShotVideo(videoAsset);
  const task = activeVideoTask(shot);
  const waitingRemote = ["video_waiting_remote", "video_running"].includes(shot.status) || ["running", "pending_remote"].includes(task?.status);
  const diagnosis = videoFailureDiagnosis(task, shot);
  const candidates = keyframeCandidates();
  // 关键帧候选只来自项目资产，后端才能校验归属并记录版本。
  const optionHtml = (currentPath) =>
    [`<option value="">不修改</option>`]
      .concat(
        candidates.map((asset) => {
          const selected = asset.file_path === currentPath ? "selected" : "";
          return `<option value="${escapeHtml(asset.file_path)}" ${selected}>${escapeHtml(asset.name)} · ${escapeHtml(mediaSource(asset))}</option>`;
        })
      )
      .join("");
  const videoMode = version?.video_mode || "t2v";
  const taskBlock = task && ["running", "pending_remote", "failed"].includes(task.status)
    ? `<div class="prompt-block video-task-block">
        <strong>Seedance 云端任务</strong><br />
        <span class="tag">${escapeHtml(task.status)}</span>
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
  const keyframeControl = `<div class="prompt-block keyframe-control">
      <strong>关键帧与视频模式</strong>
      <label>
        生成模式
        <select id="videoModeSelect">
          <option value="t2v" ${videoMode === "t2v" ? "selected" : ""}>T2V 文本生成</option>
          <option value="i2v" ${videoMode === "i2v" ? "selected" : ""}>I2V 首帧驱动</option>
          <option value="keyframes" ${videoMode === "keyframes" ? "selected" : ""}>首尾帧约束</option>
        </select>
      </label>
      <label>
        首帧资产
        <select id="firstFrameSelect">${optionHtml(version?.first_frame_path)}</select>
      </label>
      <label>
        尾帧资产
        <select id="lastFrameSelect">${optionHtml(version?.last_frame_path)}</select>
      </label>
      <div class="button-row compact-row">
        <button class="secondary-btn mini-btn" data-action="apply-keyframes">应用</button>
        <button class="secondary-btn mini-btn" data-action="redraw-keyframe" data-target="first">重绘首帧</button>
        <button class="secondary-btn mini-btn" data-action="redraw-keyframe" data-target="last">重绘尾帧</button>
      </div>
    </div>`;
  const videoPreview = version?.video_path
    ? realVideo
      ? `<div class="video-shell"><video class="video-preview" src="${escapeHtml(version.video_path)}" controls playsinline></video>${renderMediaBadge(videoAsset || version.video_path)}</div>`
      : `<div class="video-placeholder danger-placeholder">该片段是静帧占位视频，不能作为正式视频或成片素材。请重新生成真实视频。</div><button class="secondary-btn full-width" data-action="generate-video">重新生成真实视频</button>`
    : waitingRemote
    ? `<div class="video-placeholder">Seedance 云端仍在生成，稍后可回查结果。</div><button class="secondary-btn full-width" data-action="refresh-video-tasks">回查 Seedance 任务</button>`
    : `<button class="secondary-btn full-width" data-action="generate-video">生成视频片段</button>`;
  const evidence = shot.rag_evidence || [];
  const evidenceBlock = evidence.length
    ? `<div class="prompt-block"><strong>RAG 证据</strong>${evidence
        .map(
          (item) =>
            `<div class="evidence-item"><span class="tag">${escapeHtml(item.kind)} · ${Number(item.score || 0).toFixed(2)}</span><p>${escapeHtml(item.label)}：${escapeHtml(item.excerpt)}</p></div>`
        )
        .join("")}</div>`
    : `<div class="prompt-block"><strong>RAG 证据</strong><br />暂无证据挂载，可能是旧项目或尚未完成索引。</div>`;
  const versions = (shot.versions || [])
    .map(
      (item) => `
      <div class="version-item ${item.id === shot.current_version_id ? "active" : ""}">
        <div>
          <strong>v${item.version_number}</strong>
          <span class="muted-text">${escapeHtml(item.created_by)} · ${escapeHtml(formatTime(item.created_at))}</span>
        </div>
        <span class="tag">${item.video_path ? "含视频" : item.video_mode || "关键帧"}</span>
        <button class="secondary-btn mini-btn" data-action="rollback-version" data-version-id="${escapeHtml(item.id)}" ${item.id === shot.current_version_id ? "disabled" : ""}>回滚</button>
      </div>`
    )
    .join("");
  el("shotInspector").innerHTML = `
    <div class="inspector-preview-grid">
      <div class="shot-preview">${renderAssetMedia(version?.first_frame_path, "first frame", assetForPath(version?.first_frame_path))}</div>
      <div class="shot-preview">${renderAssetMedia(version?.last_frame_path, "last frame", assetForPath(version?.last_frame_path))}</div>
    </div>
    ${keyframeControl}
    ${videoPreview}
    ${taskBlock}
    ${diagnosisBlock}
    <div>
      <h3>${escapeHtml(shot.title)}</h3>
      <p class="shot-description">${escapeHtml(shot.description)}</p>
    </div>
    <div class="prompt-block"><strong>Visual Prompt</strong><br />${escapeHtml(shot.visual_prompt)}</div>
    <div class="prompt-block"><strong>Audio Prompt</strong><br />${escapeHtml(shot.audio_prompt)}</div>
    ${evidenceBlock}
    <div class="prompt-block version-list"><strong>版本历史</strong>${versions}</div>
  `;
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
        <div class="shot-meta">
          <strong>${escapeHtml(metadata.label || item.id)}</strong>
          <span class="tag">${escapeHtml(metadata.kind || "memory")} · ${Number(item.score || 0).toFixed(2)}</span>
        </div>
        <p>${escapeHtml(item.document || "").slice(0, 180)}</p>
      </article>`;
    })
    .join("");
}

function renderAssetLibrary() {
  const project = state.project;
  const assets = project?.assets || [];
  el("assetCount").textContent = String(assets.length);
  if (!assets.length) {
    el("assetLibrary").className = "asset-library empty-state";
    el("assetLibrary").textContent = "运行项目后显示角色、场景、关键帧与视频资产。";
    return;
  }

  const groups = [
    {
      title: "角色",
      items: (project.characters || []).map((character) => {
        const asset = assets.find((item) => item.id === character.asset_id);
        return {
          label: character.name,
          meta: character.role,
          description: character.description,
          prompt: character.visual_prompt,
          path: asset?.file_path,
          asset,
        };
      }),
    },
    {
      title: "场景",
      items: (project.scenes || []).map((scene) => {
        const asset = assets.find((item) => item.id === scene.asset_id);
        return {
          label: scene.name,
          meta: "scene",
          description: scene.description,
          prompt: scene.visual_prompt,
          path: asset?.file_path,
          asset,
        };
      }),
    },
    {
      title: "关键帧",
      items: assets
        .filter((asset) => ["first-frame", "last-frame"].includes(asset.type))
        .map((asset) => ({
          label: asset.name,
          meta: asset.type,
          description: asset.description,
          prompt: asset.prompt,
          path: asset.file_path,
          asset,
        })),
    },
    {
      title: "视频片段",
      items: assets
        .filter((asset) => asset.type === "video")
        .map((asset) => ({
          label: asset.name,
          meta: mediaSource(asset),
          description: asset.description,
          prompt: asset.prompt,
          path: asset.file_path,
          asset,
        })),
    },
    {
      title: "最终成片",
      items: assets
        .filter((asset) => asset.type === "final-video")
        .map((asset) => ({
          label: asset.name,
          meta: "final cut",
          description: asset.description,
          prompt: asset.prompt,
          path: asset.file_path,
          asset,
        })),
    },
  ].filter((group) => group.items.length);

  el("assetLibrary").className = "asset-library";
  el("assetLibrary").innerHTML = groups
    .map(
      (group) => `
      <div class="asset-group">
        <div class="asset-group-title">${escapeHtml(group.title)}</div>
        <div class="asset-grid">
          ${group.items.map(renderAssetCard).join("")}
        </div>
      </div>`
    )
    .join("");
}

function renderAssetCard(item) {
  return `
    <article class="asset-card">
      <div class="asset-thumb">${renderAssetMedia(item.path, item.label, item.asset)}</div>
      <div class="asset-content">
        <div class="shot-meta">
          <strong>${escapeHtml(item.label)}</strong>
          <span class="tag">${escapeHtml(item.meta)}</span>
        </div>
        <p>${escapeHtml(item.description)}</p>
        <details>
          <summary>Prompt</summary>
          <div>${escapeHtml(item.prompt)}</div>
        </details>
        ${item.path ? `<div class="asset-actions"><a href="${escapeHtml(item.path)}" target="_blank">打开</a><a href="${escapeHtml(item.path)}" download>下载</a></div>` : ""}
      </div>
    </article>`;
}

function renderJob() {
  const job = state.project?.jobs?.[0];
  const error = job?.error_message ? `｜${job.error_message}` : "";
  el("jobMessage").textContent = `${job?.message || "等待任务。"}${error}`;
  el("jobStatus").textContent = job?.status || "Idle";
  el("jobStatus").className = `status-pill ${statusClass(job?.status || "idle")}`;
  el("jobProgress").style.width = `${job?.progress || 0}%`;
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
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}
