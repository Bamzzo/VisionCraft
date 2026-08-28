import { agents, currentVersion, latestVersion, selectedShot, state } from "./state.js";

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
      const version = currentVersion(shot) || latestVersion(shot);
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
        ${shotProgressHtml(shot)}
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
  const version = currentVersion(shot) || latestVersion(shot);
  el("shotInspector").className = "shot-inspector";
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
  const unsaved = Boolean(shot.has_unsaved_changes || draft.dirty);
  const currentLabel = version ? `当前版本 v${version.version_number}` : "尚未冻结版本";
  const videoStatusLabel = version?.video_path
    ? realVideo
      ? "当前版本视频可预览"
      : "当前版本视频不可用"
    : "此版本尚未生成视频";
  // 关键帧候选只来自项目资产，后端才能校验归属并记录版本。
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
  const resultCard = `<div class="prompt-block result-card">
        <strong>镜头版本状态</strong>
        <div class="tag-row">
          <span class="tag">${escapeHtml(currentLabel)}</span>
          <span class="tag">${unsaved ? "有未保存修改" : "草稿已同步"}</span>
          <span class="tag">${escapeHtml(draft.provider || "未选择 Provider")}</span>
          <span class="tag">${escapeHtml(draft.model || "未选择模型")}</span>
          <span class="tag">${escapeHtml(draft.video_mode || "t2v")}</span>
          <span class="tag">${escapeHtml(String(draft.duration_seconds || state.project?.duration_seconds || 5))}s</span>
        </div>
        <p class="muted-text">首帧/尾帧/参考图：${escapeHtml(frameStatusLabel(draft))}</p>
        <p class="muted-text">${escapeHtml(videoStatusLabel)}</p>
        ${state.project?.assembly_stale ? `<p class="muted-text">成片已过期，需要重新合成（不会自动执行）。</p>` : ""}
        ${shotProgressHtml(shot)}
      </div>`;
  const keyframeControl = `<div class="prompt-block keyframe-control">
      <strong>镜头编辑草稿</strong>
      <p class="muted-text">编辑只写入草稿。点击「基于当前草稿生成新版本」后才会冻结不可变版本；历史版本不会被覆盖。</p>
      <label>
        自然语言描述
        <textarea id="shotDescriptionInput" rows="3">${escapeHtml(draft.description || "")}</textarea>
      </label>
      <label>
        动作 / 运镜
        <input id="shotCameraInput" value="${escapeHtml(draft.camera_motion || "")}" />
      </label>
      <label>
        视觉提示词
        <textarea id="shotVisualPromptInput" rows="3">${escapeHtml(draft.visual_prompt || "")}</textarea>
      </label>
      <div class="form-grid">
        <label>
          生成模式
          <select id="videoModeSelect">
            <option value="t2v" ${draft.video_mode === "t2v" ? "selected" : ""}>T2V 文本生成</option>
            <option value="i2v" ${draft.video_mode === "i2v" ? "selected" : ""}>I2V 首帧驱动</option>
            <option value="keyframes" ${draft.video_mode === "keyframes" ? "selected" : ""}>首尾帧约束</option>
          </select>
        </label>
        <label>
          Provider
          <select id="videoProviderSelect">
            ${providerOptions
              .map(
                (item) =>
                  `<option value="${escapeHtml(item.id)}" ${item.id === draft.provider ? "selected" : ""} ${item.disabled ? "disabled" : ""}>${escapeHtml(item.label)}</option>`
              )
              .join("")}
          </select>
        </label>
        <label>
          模型
          <select id="videoModelSelect">
            ${modelOptions
              .map(
                (item) =>
                  `<option value="${escapeHtml(item.id)}" ${item.id === draft.model ? "selected" : ""}>${escapeHtml(item.label)}</option>`
              )
              .join("")}
          </select>
        </label>
        <label>
          本镜时长
          <select id="videoDurationSelect">
            ${(constraint.durations || [draft.duration_seconds]).map((item) => `<option value="${item}" ${Number(item) === Number(draft.duration_seconds) ? "selected" : ""}>${item}s</option>`).join("")}
          </select>
        </label>
      </div>
      <p class="muted-text" id="videoCapabilityHint">${escapeHtml(constraint.hint)}</p>
      <label>
        首帧资产 ${draft.first_frame_path ? "· 已选" : "· 未选"}
        <select id="firstFrameSelect">${optionHtml(draft.first_frame_path)}</select>
      </label>
      <label>
        尾帧资产 ${draft.last_frame_path ? "· 已选" : "· 未选"}
        <select id="lastFrameSelect">${optionHtml(draft.last_frame_path)}</select>
      </label>
      <label>
        参考图 ${draft.reference_frame_path ? "· 已选" : "· 未选"}
        <select id="referenceFrameSelect">${optionHtml(draft.reference_frame_path)}</select>
      </label>
      <div class="button-row compact-row">
        <button class="secondary-btn mini-btn" data-action="save-shot-draft">保存镜头草稿</button>
        <button class="secondary-btn mini-btn" data-action="freeze-shot-version">基于当前草稿生成新版本</button>
        <button class="secondary-btn mini-btn" data-action="apply-keyframes">写入关键帧到草稿</button>
        <button class="secondary-btn mini-btn" data-action="redraw-keyframe" data-target="first">重绘首帧</button>
        <button class="secondary-btn mini-btn" data-action="redraw-keyframe" data-target="last">重绘尾帧</button>
      </div>
    </div>`;
  const generateButton = `<button class="secondary-btn full-width" data-action="generate-video" ${constraint.ok ? "" : "disabled"} title="${escapeHtml(constraint.reason || "")}">${constraint.ok ? "仅重生成此镜头" : constraint.reason}</button>`;
  const videoPreview = version?.video_path
    ? realVideo
      ? `<div class="video-shell"><video class="video-preview" src="${escapeHtml(version.video_path)}" controls playsinline></video>${renderMediaBadge(videoAsset || version.video_path)}</div>${generateButton}`
      : `<div class="video-placeholder danger-placeholder">该片段是静帧占位视频，不能作为正式视频或成片素材。请重新生成真实视频。</div>${generateButton}`
    : waitingRemote
    ? `<div class="video-placeholder">云端仍在生成。正在回查同一云端任务，不会重复提交或重复计费。</div><button class="secondary-btn full-width" data-action="refresh-video-tasks">立即刷新云端任务</button>${generateButton}`
    : `<div class="video-placeholder">${escapeHtml(videoStatusLabel)}</div>${generateButton}`;
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
          <strong>v${item.version_number}${item.id === shot.current_version_id ? " · 当前" : ""}</strong>
          <span class="muted-text">${escapeHtml(item.created_at || "")}</span>
          <p class="muted-text">${escapeHtml(item.change_summary || item.provider || item.video_mode || "历史版本")}</p>
          <span class="muted-text">${escapeHtml(item.provider || "未记录")} · ${escapeHtml(item.model || "未记录")} · ${escapeHtml(item.video_mode || "t2v")} · ${escapeHtml(String(item.duration_seconds || "-"))}s</span>
          <p class="muted-text">关键帧：${escapeHtml(frameStatusLabel(item))}</p>
        </div>
        <span class="tag">${item.video_path ? "含视频" : "此版本尚未生成视频"}</span>
        <button class="secondary-btn mini-btn" data-action="rollback-version" data-version-id="${escapeHtml(item.id)}" ${item.id === shot.current_version_id ? "disabled" : ""}>切换/回滚至此版本</button>
      </div>`
    )
    .join("");
  el("shotInspector").innerHTML = `
    <div class="inspector-preview-grid">
      <div class="shot-preview">${renderAssetMedia(draft.first_frame_path || version?.first_frame_path, "first frame", assetForPath(draft.first_frame_path || version?.first_frame_path))}</div>
      <div class="shot-preview">${renderAssetMedia(draft.last_frame_path || version?.last_frame_path, "last frame", assetForPath(draft.last_frame_path || version?.last_frame_path))}</div>
    </div>
    ${resultCard}
    ${keyframeControl}
    ${videoPreview}
    ${taskBlock}
    ${diagnosisBlock}
    <div>
      <h3>${escapeHtml(shot.title)}</h3>
    </div>
    ${evidenceBlock}
    <div class="prompt-block version-list"><strong>版本历史</strong>${versions || "<p class='muted-text'>暂无版本</p>"}</div>
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
  const job = (state.project?.active_jobs || [])[0] || state.project?.jobs?.[0];
  const latest = state.jobEvents[state.jobEvents.length - 1];
  const message = latest?.message || job?.message || "等待任务。";
  const status = latest?.status || job?.status || "idle";
  const progress = latest?.progress ?? job?.progress ?? 0;
  const waiting = status === "waiting_remote" || hasWaitingHint(status);
  el("jobMessage").textContent = message;
  el("jobHint").textContent = waiting
    ? "正在回查同一云端任务，不会重复提交或重复计费。"
    : state.sseConnected
      ? "实时更新已连接。"
      : hasActiveHint(status)
        ? "实时通道断开，正在用低频轮询恢复任务状态。"
        : "";
  el("jobStatus").textContent = statusLabel(status);
  el("jobStatus").className = `status-pill ${statusClass(status)}`;
  el("jobProgress").style.width = `${progress || 0}%`;
  const timeline = el("jobTimeline");
  const toggle = el("jobTimelineToggle");
  if (!timeline || !toggle) return;
  toggle.textContent = state.timelineOpen ? "收起时间线" : "时间线";
  timeline.classList.toggle("hidden", !state.timelineOpen);
  const events = [...state.jobEvents].slice(-20).reverse();
  timeline.innerHTML = events.length
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
  return `<p class="shot-job-status">${escapeHtml(event.message || "")}${provider ? ` · ${escapeHtml(provider)}` : ""} · ${escapeHtml(event.progress ?? 0)}%</p>`;
}

function statusLabel(status) {
  return (
    {
      idle: "空闲",
      queued: "已排队",
      running: "处理中",
      waiting_remote: "等待云端",
      paused: "已暂停",
      completed: "已完成",
      failed: "失败",
    }[status] || status
  );
}

function hasWaitingHint(status) {
  return status === "waiting_remote";
}

function hasActiveHint(status) {
  return ["queued", "running", "waiting_remote", "paused"].includes(status);
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

function uniqueValues(items) {
  return [...new Set(items.filter((item) => item !== undefined && item !== null && item !== ""))];
}

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
  const hintParts = [
    provider ? `${provider.label}` : "未选择 Provider",
    draft.model || "未选择模型",
    `分辨率 ${resolution}`,
    `支持时长 ${(durations || []).join("/") || "?"}s`,
    `比例 ${(ratios || []).join("/") || "?"}`,
    frameStatusLabel(draft),
  ];
  const hint = hintParts.join(" · ");
  if (!provider) {
    return { ok: false, reason: "请选择视频 Provider", hint, durations };
  }
  if (!(provider.supported_modes || []).includes(draft.video_mode)) {
    return { ok: false, reason: "该 Provider 不支持当前生成模式", hint, durations };
  }
  if (!draft.model || !models.some((item) => item.id === draft.model)) {
    return { ok: false, reason: "该模型不支持当前生成模式", hint, durations };
  }
  if (projectRatio && ratios.length && !ratios.includes(projectRatio)) {
    return { ok: false, reason: `该 Provider 不支持比例 ${projectRatio}`, hint, durations };
  }
  if (durations.length && !durations.map(Number).includes(Number(draft.duration_seconds))) {
    return { ok: false, reason: `该 Provider 不支持 ${draft.duration_seconds}s`, hint, durations };
  }
  if (requirements.requires_first_frame && !firstReady) {
    return { ok: false, reason: "缺少首帧，无法提交 I2V", hint, durations };
  }
  if (requirements.requires_last_frame && !lastReady) {
    return { ok: false, reason: "缺少尾帧，无法提交首尾帧模式", hint, durations };
  }
  return { ok: true, reason: "", hint, durations };
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
