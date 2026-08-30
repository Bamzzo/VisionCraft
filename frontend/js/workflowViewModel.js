/**
 * 工作流视图模型：把后端项目状态映射为页面阶段导航与工作区状态。
 *
 * 职责边界（见 docs/ui-layout-interaction-design.md 第 10 节）：
 *   - 只做「后端状态 → 页面状态」的只读推导，不发起请求、不修改服务端数据。
 *   - 三种状态分离：executionStage（实际执行到的阶段）由本模块推导；
 *     viewStage（用户正在查看的阶段）与 selectedAsset（选中的素材）存放在 state.js。
 *
 * 阶段状态枚举与中文标签在本模块统一维护，渲染层只消费推导结果。
 */

export const STAGES = [
  { id: "text", label: "文本理解", index: 0 },
  { id: "storyline", label: "故事线选择", index: 1 },
  { id: "bible", label: "Story Bible", index: 2 },
  { id: "storyboard", label: "分镜设计", index: 3 },
  { id: "keyframes", label: "关键帧", index: 4 },
  { id: "video", label: "镜头视频", index: 5 },
  { id: "assembly", label: "成片合成", index: 6 },
  { id: "export", label: "导出与交付", index: 7 },
];

/** 旧导航 id 兼容：改编方案已并入文本理解（短文本）或故事线选择（中等文本）。 */
export const LEGACY_STAGE_ALIAS = {
  adaptation: "text",
  keyframes_legacy: "keyframes",
};

export const STAGE_STATE_MARKS = {
  not_started: "○",
  processing: "…",
  awaiting_review: "!",
  completed: "✓",
  modified: "✎",
  invalidated: "↩",
  failed: "×",
  skipped: "–",
};

export const STAGE_STATE = {
  NOT_STARTED: "not_started",
  PROCESSING: "processing",
  AWAITING_REVIEW: "awaiting_review",
  COMPLETED: "completed",
  MODIFIED: "modified",
  INVALIDATED: "invalidated",
  FAILED: "failed",
  SKIPPED: "skipped",
};

const STAGE_STATE_LABELS = {
  [STAGE_STATE.NOT_STARTED]: "未开始",
  [STAGE_STATE.PROCESSING]: "处理中",
  [STAGE_STATE.AWAITING_REVIEW]: "等待审核",
  [STAGE_STATE.COMPLETED]: "已完成",
  [STAGE_STATE.MODIFIED]: "已修改",
  [STAGE_STATE.INVALIDATED]: "已失效",
  [STAGE_STATE.FAILED]: "失败",
  [STAGE_STATE.SKIPPED]: "跳过",
};

export function stageStateLabel(state) {
  return STAGE_STATE_LABELS[state] || "未开始";
}

export function stageStateMark(state) {
  return STAGE_STATE_MARKS[state] || STAGE_STATE_MARKS.not_started;
}

export function resolveStageId(stageId, project) {
  if (stageId === "adaptation") return isMedium(project) ? "storyline" : "text";
  if (STAGES.some((stage) => stage.id === stageId)) return stageId;
  return LEGACY_STAGE_ALIAS[stageId] || "text";
}

/** 阶段状态对应的视觉色调：进行/选中用蓝绿，等待审核用琥珀，失败/风险用红。 */
export function stageStateTone(state) {
  switch (state) {
    case STAGE_STATE.PROCESSING:
      return "active";
    case STAGE_STATE.AWAITING_REVIEW:
      return "review";
    case STAGE_STATE.COMPLETED:
      return "done";
    case STAGE_STATE.MODIFIED:
      return "modified";
    case STAGE_STATE.FAILED:
      return "failed";
    case STAGE_STATE.INVALIDATED:
      return "invalidated";
    case STAGE_STATE.SKIPPED:
      return "skipped";
    default:
      return "idle";
  }
}

const ADAPTATION_STATUS_STAGE = {
  awaiting_storyline_review: "storyline",
  story_bible_ready: "bible",
  awaiting_bible_review: "bible",
  storyboard_draft_ready: "storyboard",
  awaiting_storyboard_review: "storyboard",
};

const STAGE_JOB_TYPES = {
  text: ["adaptation_workflow", "adaptation_regen_scope"],
  storyline: ["adaptation_workflow", "medium_analysis"],
  bible: ["adaptation_bible", "adaptation_regen_bible"],
  storyboard: ["adaptation_storyboard", "adaptation_regen_storyboard", "adaptation_production"],
  keyframes: ["keyframe_redraw"],
  video: ["video_generation", "batch_video_generation", "video_safety_retry", "video_task_refresh"],
  assembly: ["sequence_assembly"],
  export: ["project_settings"],
};

const STAGE_GOALS = {
  text: "理解原文规模、人物与冲突，并产出可审核的改编依据。",
  storyline: "从候选故事线或改编方案中确认本次短片范围。",
  bible: "锁定角色、场景与视觉规则，作为后续镜头的锚点。",
  storyboard: "确认镜头顺序、动作与原文依据，再进入制作。",
  keyframes: "为每个镜头准备首帧与尾帧，供视频生成引用。",
  video: "按镜头选择 Provider/模型并生成或回滚视频版本。",
  assembly: "按镜头顺序合成成片，并配置字幕、背景音与原声。",
  export: "检查当前有效成片规格并下载交付文件。",
};

const STAGE_INPUTS = {
  text: "项目原文与规模判断。",
  storyline: "文本理解结果与候选故事线。",
  bible: "已确认的改编范围。",
  storyboard: "已确认的 Story Bible。",
  keyframes: "已确认的分镜镜头。",
  video: "镜头草稿、关键帧与模型选择。",
  assembly: "各镜头当前有效视频与成片配置。",
  export: "当前有效成片与项目规格。",
};

const STAGE_OUTPUTS = {
  text: "摘要、人物、场景、事件，以及短文本改编方案。",
  storyline: "已选故事线、事件范围，以及中等文本改编方案。",
  bible: "角色卡、场景卡与视觉规则。",
  storyboard: "可审核的分镜草案或已入制作镜头。",
  keyframes: "每镜首帧与尾帧。",
  video: "镜头视频版本、Provider 与模型。",
  assembly: "成片文件、字幕/音频配置与合成历史。",
  export: "可下载成片与交付检查结果。",
};

const STAGE_NEXT = {
  text: "启动改编流程，或确认改编范围。",
  storyline: "选择故事线并确认范围。",
  bible: "确认 Story Bible 并生成分镜。",
  storyboard: "确认分镜并进入制作。",
  keyframes: "补齐或重绘关键帧。",
  video: "生成、回滚或仅重生成此镜头。",
  assembly: "保存成片配置并合成成片。",
  export: "下载当前有效成片，或回到成片合成重新导出。",
};

const STAGE_LOCKED_HINT = {
  text: "请先创建项目并启动改编流程。",
  storyline: "请先完成文本理解。",
  bible: "请先确认改编范围。",
  storyboard: "请先确认 Story Bible。",
  keyframes: "请先确认分镜并进入制作。",
  video: "请先确认分镜并进入制作。",
  assembly: "请先完成各镜头视频。",
  export: "请先合成成片。",
};

const PRODUCTION_STATUSES = new Set([
  "production_ready",
  "ready_for_review",
  "review_pending",
  "keyframes_ready",
  "video_ready",
  "completed",
]);

function isMedium(project) {
  return project?.text_scale === "medium";
}

function realVideoPath(shot) {
  const versions = shot?.versions || [];
  const current = versions.find((item) => item.id === shot?.current_version_id) || versions[0];
  return current?.video_path || "";
}

function assetByPath(project, path) {
  if (!path) return null;
  return (project?.assets || []).find((asset) => asset.file_path === path) || null;
}

/** 与 render.js 一致的「真实视频」判定：必须来自模型 Provider，FFmpeg 结果只算成片。 */
function isRealShotVideo(asset) {
  const ref = asset?.embedding_ref || "";
  return asset?.type === "video" && ref.startsWith("provider:") && !ref.startsWith("provider:ffmpeg");
}

function shotHasRealVideo(project, shot) {
  return isRealShotVideo(assetByPath(project, realVideoPath(shot)));
}

function shotHasKeyframes(shot) {
  const versions = shot?.versions || [];
  const current = versions.find((item) => item.id === shot?.current_version_id) || versions[0];
  return Boolean(current?.first_frame_path && current?.last_frame_path);
}

function activeJobs(project) {
  return project?.active_jobs?.length ? project.active_jobs : project?.jobs || [];
}

function hasRunningJob(project, types) {
  return activeJobs(project).some(
    (job) => types.includes(job.type) && ["queued", "running", "waiting_remote", "paused"].includes(job.status)
  );
}

/**
 * 计算项目实际执行到的阶段（frontier）。
 * 适配期由 project.status 直接定位；制作期由镜头/素材数据定位最早未完成阶段。
 */
export function executionStageId(project) {
  if (!project) return "text";
  const status = project.status || "created";
  if (status === "awaiting_scope_review" || status === "adaptation_options_ready") {
    return isMedium(project) ? "storyline" : "text";
  }
  if (ADAPTATION_STATUS_STAGE[status]) return ADAPTATION_STATUS_STAGE[status];
  if (PRODUCTION_STATUSES.has(status) || status === "failed") return productionFrontier(project);
  // created / draft / running / paused 等：仍处于文本理解。
  return "text";
}

function productionFrontier(project) {
  const shots = project?.shots || [];
  const assets = project?.assets || [];
  const finalAsset = assets.find((asset) => asset.type === "final-video");
  if (!shots.length) return "keyframes";
  const allKeyframes = shots.every(shotHasKeyframes);
  if (!allKeyframes) return "keyframes";
  const allVideos = shots.every((shot) => shotHasRealVideo(project, shot));
  if (!allVideos) return "video";
  if (finalAsset && !project?.assembly_stale) return "export";
  return "assembly";
}

function failedStage(project) {
  // 失败定位到最早出现问题的阶段；制作期失败优先看镜头视频。
  const shots = project?.shots || [];
  if (shots.some((shot) => ["video_failed", "video_invalid"].includes(shot.status))) return "video";
  return executionStageId({ ...project, status: fallbackStatusBeforeFailure(project) });
}

function fallbackStatusBeforeFailure(project) {
  if ((project?.storyboard_drafts || []).length) return "awaiting_storyboard_review";
  if (project?.story_bible) return "awaiting_bible_review";
  if ((project?.adaptation_options || []).length) return "awaiting_scope_review";
  if ((project?.storylines || []).length) return "awaiting_storyline_review";
  return "created";
}

function stageHasData(project, stageId) {
  const shots = project?.shots || [];
  const assets = project?.assets || [];
  switch (stageId) {
    case "text":
      return Boolean(project?.source_text) || (project?.adaptation_options || []).length > 0;
    case "storyline":
      return (project?.storylines || []).length > 0 || (isMedium(project) && (project?.adaptation_options || []).length > 0);
    case "bible":
      return Boolean(project?.story_bible);
    case "storyboard":
      // 分镜阶段的「当前有效产物」是可审核的 storyboard_drafts；
      // 上游重做会清空 drafts，此时即便残留下游 shots，分镜也应视为未开始（shots 由关键帧/视频阶段标记失效）。
      return (project?.storyboard_drafts || []).length > 0;
    case "keyframes":
      return shots.some((shot) => (shot.versions || []).some((v) => v.first_frame_path || v.last_frame_path));
    case "video":
      return shots.some((shot) => (shot.versions || []).some((v) => v.video_path)) ||
        assets.some((asset) => asset.type === "video");
    case "assembly":
    case "export":
      return assets.some((asset) => asset.type === "final-video");
    default:
      return false;
  }
}

/**  frontier 阶段的实时子状态。 */
function frontierState(project, stageId) {
  const status = project?.status || "created";
  if (status === "failed") return STAGE_STATE.FAILED;
  switch (stageId) {
    case "text":
      if (status === "running" || hasRunningJob(project, ["adaptation_workflow", "adaptation_regen_scope"])) {
        return STAGE_STATE.PROCESSING;
      }
      if (status === "awaiting_scope_review" || status === "adaptation_options_ready") {
        return STAGE_STATE.AWAITING_REVIEW;
      }
      return STAGE_STATE.NOT_STARTED;
    case "storyline":
      if (hasRunningJob(project, ["adaptation_workflow", "medium_analysis", "adaptation_regen_scope"])) {
        return STAGE_STATE.PROCESSING;
      }
      return STAGE_STATE.AWAITING_REVIEW;
    case "bible":
      return STAGE_STATE.AWAITING_REVIEW;
    case "storyboard":
      return STAGE_STATE.AWAITING_REVIEW;
    case "keyframes": {
      const shots = project?.shots || [];
      if (!shots.length) {
        return hasRunningJob(project, ["adaptation_production"]) ? STAGE_STATE.PROCESSING : STAGE_STATE.NOT_STARTED;
      }
      return shots.every(shotHasKeyframes) ? STAGE_STATE.COMPLETED : STAGE_STATE.PROCESSING;
    }
    case "video": {
      const shots = project?.shots || [];
      if (!shots.length) return STAGE_STATE.NOT_STARTED;
      if (shots.some((shot) => ["video_failed", "video_invalid"].includes(shot.status))) return STAGE_STATE.FAILED;
      if (
        shots.some((shot) => ["video_running", "video_waiting_remote"].includes(shot.status)) ||
        hasRunningJob(project, ["video_generation", "batch_video_generation", "video_safety_retry", "video_task_refresh"])
      ) {
        return STAGE_STATE.PROCESSING;
      }
      if (shots.every((shot) => shotHasRealVideo(project, shot))) {
        return project?.review_mode ? STAGE_STATE.AWAITING_REVIEW : STAGE_STATE.COMPLETED;
      }
      return STAGE_STATE.NOT_STARTED;
    }
    case "assembly": {
      const finalAsset = (project?.assets || []).find((asset) => asset.type === "final-video");
      if (hasRunningJob(project, ["sequence_assembly"])) return STAGE_STATE.PROCESSING;
      if (finalAsset && project?.assembly_stale) return STAGE_STATE.MODIFIED;
      if (finalAsset) return project?.review_mode ? STAGE_STATE.AWAITING_REVIEW : STAGE_STATE.COMPLETED;
      return STAGE_STATE.NOT_STARTED;
    }
    case "export": {
      const finalAsset = (project?.assets || []).find((asset) => asset.type === "final-video");
      if (hasRunningJob(project, ["sequence_assembly"])) return STAGE_STATE.PROCESSING;
      if (finalAsset && project?.assembly_stale) return STAGE_STATE.MODIFIED;
      if (finalAsset) return STAGE_STATE.COMPLETED;
      return STAGE_STATE.NOT_STARTED;
    }
    default:
      return STAGE_STATE.NOT_STARTED;
  }
}

/** 已完成阶段是否被改过的痕迹（用于「已修改」标签）。 */
function completedStageModified(project, stageId) {
  switch (stageId) {
    case "bible":
      return (project?.story_bible?.review_status === "confirmed") && false; // 编辑后仍 confirmed，无法从后端区分，交由草稿脏状态表达
    case "storyboard":
      return (project?.storyboard_drafts || []).some(
        (item) => item.review_status === "edited" || item.source_type === "human_edit"
      );
    case "video":
    case "keyframes":
      return (project?.shots || []).some((shot) => shot.has_unsaved_changes);
    default:
      return false;
  }
}

/**
 * 计算完整阶段导航视图模型。
 * 返回 { executionStage, stages: [{ id, label, index, state, stateLabel, tone, summary, skippedReason, current, ... }] }。
 */
export function computeWorkflow(project) {
  const medium = isMedium(project);
  let frontierId = executionStageId(project);
  if (project?.status === "failed") frontierId = failedStage(project);
  const frontierIndex = stageIndex(frontierId);

  const stages = STAGES.map((stage) => {
    if (stage.id === "storyline" && !medium) {
      return decorateStage(project, stage, {
        state: STAGE_STATE.SKIPPED,
        summary: "当前文本无需此步骤",
        skippedReason: "短文本直接进入改编方案，无需故事线选择。",
        current: false,
      });
    }
    let state;
    if (stage.index < frontierIndex) {
      state = completedStageModified(project, stage.id) ? STAGE_STATE.MODIFIED : STAGE_STATE.COMPLETED;
      if (
        ["text", "storyline", "bible", "storyboard"].includes(stage.id) &&
        (project?.stale_stages || []).includes(stage.id) &&
        stageHasData(project, stage.id)
      ) {
        state = STAGE_STATE.INVALIDATED;
      }
    } else if (stage.index === frontierIndex) {
      state = frontierState(project, stage.id);
    } else {
      state = stageHasData(project, stage.id) ? STAGE_STATE.INVALIDATED : STAGE_STATE.NOT_STARTED;
    }
    return decorateStage(project, stage, {
      state,
      summary: stageSummary(project, stage.id, state),
      skippedReason: "",
      current: stage.index === frontierIndex,
    });
  });

  return { executionStage: frontierId, stages };
}

function decorateStage(project, stage, extra) {
  const state = extra.state;
  const assetCount = stageAssetCount(project, stage.id);
  const jobCount = stageJobCount(project, stage.id);
  const executing = Boolean(extra.current && state === STAGE_STATE.PROCESSING);
  const awaitingReview = state === STAGE_STATE.AWAITING_REVIEW;
  const flags = [];
  if (extra.current) flags.push("系统执行到此阶段");
  if (executing) flags.push("正在执行");
  if (awaitingReview) flags.push("等待用户审核");
  if (state === STAGE_STATE.COMPLETED) flags.push("已完成");
  if (state === STAGE_STATE.MODIFIED) flags.push("已修改");
  if (state === STAGE_STATE.INVALIDATED) flags.push("因上游修改而失效");
  if (state === STAGE_STATE.FAILED) flags.push("失败");
  if (state === STAGE_STATE.SKIPPED) flags.push("跳过");
  flags.push("可点击查看");
  const ariaLabel = `${stage.label}，${stageStateLabel(state)}。素材 ${assetCount}，任务 ${jobCount}。${flags.join("，")}。`;
  return {
    ...stage,
    ...extra,
    state,
    stateLabel: stageStateLabel(state),
    tone: stageStateTone(state),
    mark: stageStateMark(state),
    viewable: true,
    executing,
    awaitingReview,
    canExecute: Boolean(extra.current && state !== STAGE_STATE.NOT_STARTED && state !== STAGE_STATE.SKIPPED),
    assetCount,
    jobCount,
    goal: STAGE_GOALS[stage.id] || "",
    lockedHint: !extra.current && state === STAGE_STATE.NOT_STARTED ? stageLockedHint(stage.id) : "",
    ariaLabel,
  };
}

export function stageLockedHint(stageId) {
  return STAGE_LOCKED_HINT[stageId] || "请先完成前置阶段。";
}

export function stageBriefModel(project, stageId, stageVm) {
  return {
    goal: STAGE_GOALS[stageId] || "",
    input: STAGE_INPUTS[stageId] || "",
    output: STAGE_OUTPUTS[stageId] || "",
    nextAction: STAGE_NEXT[stageId] || "",
    stateLabel: stageVm?.stateLabel || "",
    lockedHint: stageVm?.lockedHint || "",
  };
}

function stageJobCount(project, stageId) {
  const types = STAGE_JOB_TYPES[stageId] || [];
  return (project?.jobs || []).filter((job) => types.includes(job.type)).length;
}

function stageAssetCount(project, stageId) {
  if (!project) return 0;
  if (stageId === "text" && !isMedium(project)) {
    return textAssets(project).length + (project?.adaptation_options || []).length;
  }
  if (stageId === "storyline" && isMedium(project)) {
    return storylineAssets(project).length + (project?.adaptation_options || []).length;
  }
  return stageAssets(project, stageId).length;
}

function stageIndex(stageId) {
  return STAGES.find((stage) => stage.id === stageId)?.index ?? 0;
}

function stageSummary(project, stageId, state) {
  if (state === STAGE_STATE.INVALIDATED) return "上游已重做，结果失效";
  if (state === STAGE_STATE.SKIPPED) return "当前文本无需此步骤";
  const shots = project?.shots || [];
  switch (stageId) {
    case "text": {
      const len = (project?.source_text || "").length;
      const options = isMedium(project) ? 0 : (project?.adaptation_options || []).length;
      const scale = project?.text_scale_label || "文本";
      if (options) return `${scale} · ${len} 字 · ${options} 个方案`;
      return len ? `${scale} · ${len} 字` : "等待原文";
    }
    case "storyline": {
      const count = (project?.storylines || []).length;
      const options = isMedium(project) ? (project?.adaptation_options || []).length : 0;
      if (options) return `${count} 条故事线 · ${options} 个改编方案`;
      return count ? `${count} 条候选故事线` : "等待故事线";
    }
    case "bible":
      return project?.story_bible ? "角色/场景/风格已就绪" : "等待 Story Bible";
    case "storyboard": {
      const count = (project?.storyboard_drafts || []).length || shots.length;
      return count ? `${count} 个分镜` : "等待分镜";
    }
    case "keyframes": {
      const ready = shots.filter(shotHasKeyframes).length;
      return shots.length ? `${ready}/${shots.length} 镜头关键帧` : "等待关键帧";
    }
    case "video": {
      const ready = shots.filter((shot) => shotHasRealVideo(project, shot)).length;
      return shots.length ? `${ready}/${shots.length} 镜头视频` : "等待视频";
    }
    case "assembly":
      return (project?.assets || []).some((asset) => asset.type === "final-video")
        ? project?.assembly_stale
          ? "成片已过期"
          : "成片已生成"
        : "等待合成";
    case "export":
      return (project?.assets || []).some((asset) => asset.type === "final-video")
        ? project?.assembly_stale
          ? "成片已过期，需重新合成"
          : `${project?.output_resolution || "1280x720"} · 可下载`
        : "等待可交付成片";
    default:
      return "";
  }
}

/* ------------------------------------------------------------------ */
/* 阶段工作区素材派生：统一为缩略图/单素材视图可消费的 AssetCard 形状。    */
/* ------------------------------------------------------------------ */

/**
 * 归一化素材卡片：
 * { key, kind, title, status, statusLabel, tone, preview, summary, meta, ref }
 * ref 保留原始对象（option/storyline/bible/shot/asset/event），供详情与操作使用。
 */
export function stageAssets(project, stageId) {
  if (!project) return [];
  switch (stageId) {
    case "text":
      return textAssets(project);
    case "storyline":
      return storylineAssets(project);
    case "adaptation":
      return adaptationAssets(project);
    case "bible":
      return bibleAssets(project);
    case "storyboard":
      return storyboardAssets(project);
    case "keyframes":
      return keyframeAssets(project);
    case "video":
      return videoAssets(project);
    case "assembly":
      return assemblyAssets(project);
    case "export":
      return exportAssets(project);
    default:
      return [];
  }
}

function exportAssets(project) {
  return assemblyAssets(project).filter((card) => card.kind === "final");
}

function baseCard(kind, title, ref) {
  return { key: `${kind}:${ref?.id || title}`, kind, title, status: "", statusLabel: "", tone: "idle", preview: "", summary: "", meta: {}, ref };
}

function textAssets(project) {
  const cards = [];
  const source = baseCard("text", "原文与规模", { id: "source" });
  source.summary = (project.source_text || "").slice(0, 400);
  source.statusLabel = project.text_scale_label || "";
  source.meta = { 字数: (project.source_text || "").length, 规模: project.text_scale || "short" };
  cards.push(source);
  (project.story_events || []).forEach((event) => {
    const card = baseCard("event", event.title || "事件", event);
    card.summary = event.summary || "";
    card.meta = { 引用: event.source_excerpt || "", 偏移: `${event.source_start}–${event.source_end}` };
    cards.push(card);
  });
  (project.characters || []).forEach((item) => {
    const card = baseCard("character", item.name || "角色", item);
    card.summary = item.description || "";
    card.preview = assetPathById(project, item.asset_id);
    card.meta = { 身份: item.role || "" };
    cards.push(card);
  });
  (project.scenes || []).forEach((item) => {
    const card = baseCard("scene", item.name || "场景", item);
    card.summary = item.description || "";
    card.preview = assetPathById(project, item.asset_id);
    cards.push(card);
  });
  return cards;
}

function storylineAssets(project) {
  return (project.storylines || []).map((line) => {
    const card = baseCard("storyline", line.title || "故事线", line);
    card.summary = line.rationale || "";
    card.status = line.selected ? "selected" : "";
    card.statusLabel = line.selected ? "已选" : "";
    card.tone = line.selected ? "active" : "idle";
    card.meta = {
      主角: line.protagonist || "",
      冲突: line.conflict || "",
      引用: line.source_excerpt || "",
      建议: `${line.suggested_duration_seconds}s · ${line.suggested_shot_count} 镜`,
    };
    return card;
  });
}

function adaptationAssets(project) {
  return (project.adaptation_options || []).map((option) => {
    const card = baseCard("option", option.title || "改编方案", option);
    card.summary = option.rationale || "";
    card.status = option.selected ? "selected" : "";
    card.statusLabel = option.selected ? "已选" : "";
    card.tone = option.selected ? "active" : "idle";
    card.meta = {
      冲突: option.conflict || "",
      引用: option.source_excerpt || "",
      建议: `${option.suggested_duration_seconds}s · ${option.suggested_shot_count} 镜`,
    };
    return card;
  });
}

function bibleAssets(project) {
  const bible = project.story_bible;
  if (!bible) return [];
  const cards = [];
  const overview = baseCard("bible", "Story Bible 总览", { id: "bible" });
  overview.summary = bible.adaptation_summary || bible.summary || "";
  overview.status = bible.review_status || "draft";
  overview.statusLabel = bible.review_status === "confirmed" ? "已确认" : "草稿";
  overview.tone = bible.review_status === "confirmed" ? "done" : "review";
  overview.meta = { Logline: bible.logline || "", 风格: bible.visual_style || "", 主角: bible.protagonist || "" };
  cards.push(overview);
  (bible.character_cards || []).forEach((item, index) => {
    const card = baseCard("character-card", item.name || `角色 ${index + 1}`, { ...item, id: `char-${index}`, cardIndex: index, cardKind: "character" });
    card.summary = item.appearance || item.identity || "";
    card.meta = { 动机: item.motivation || "", 不可改变: item.invariant || "" };
    cards.push(card);
  });
  (bible.scene_cards || []).forEach((item, index) => {
    const card = baseCard("scene-card", item.name || `场景 ${index + 1}`, { ...item, id: `scene-${index}`, cardIndex: index, cardKind: "scene" });
    card.summary = item.environment || "";
    card.meta = { 时间: item.time || "", 视觉: item.visuals || "" };
    cards.push(card);
  });
  return cards;
}

function storyboardAssets(project) {
  const drafts = project.storyboard_drafts || [];
  if (drafts.length) {
    return drafts.map((item) => {
      const card = baseCard("storyboard", item.title || `镜头 ${item.shot_index}`, item);
      card.summary = item.action_text || item.narrative_purpose || "";
      card.status = item.review_status || "draft";
      card.statusLabel = item.review_status === "confirmed" ? "已确认" : item.review_status === "edited" ? "已修改" : "草稿";
      card.tone = item.review_status === "confirmed" ? "done" : item.review_status === "edited" ? "modified" : "idle";
      card.meta = { 场景: item.scene || "", 运镜: item.camera_motion || "", 时长: `${item.duration_seconds || 5}s`, 依据: item.source_excerpt || "" };
      return card;
    });
  }
  // 已确认分镜后，分镜阶段展示制作镜头作为结果。
  return (project.shots || []).map((shot) => {
    const card = baseCard("shot", shot.title || "镜头", shot);
    card.summary = shot.description || "";
    card.status = shot.status || "";
    card.statusLabel = "已入制作";
    card.tone = "done";
    card.preview = currentVersionPreview(shot);
    card.meta = { 场景: shot.scene || "", 运镜: shot.camera_motion || "" };
    return card;
  });
}

function keyframeAssets(project) {
  const cards = [];
  (project.shots || []).forEach((shot) => {
    const version = currentVersionOf(shot);
    const first = baseCard("frame", `${shot.title} · 首帧`, { id: `${shot.id}:first`, shot, version, frameRole: "first" });
    first.preview = version?.first_frame_path || "";
    first.summary = shot.description || "";
    first.status = shot.status || "";
    first.statusLabel = version?.first_frame_path ? "已生成" : "缺首帧";
    first.tone = version?.first_frame_path ? "done" : "review";
    first.meta = { 镜头: shot.title, 角色: "首帧", 版本: version ? `v${version.version_number}` : "-" };
    cards.push(first);
    const last = baseCard("frame", `${shot.title} · 尾帧`, { id: `${shot.id}:last`, shot, version, frameRole: "last" });
    last.preview = version?.last_frame_path || "";
    last.summary = shot.description || "";
    last.status = shot.status || "";
    last.statusLabel = version?.last_frame_path ? "已生成" : "缺尾帧";
    last.tone = version?.last_frame_path ? "done" : "review";
    last.meta = { 镜头: shot.title, 角色: "尾帧", 版本: version ? `v${version.version_number}` : "-" };
    cards.push(last);
  });
  return cards;
}

function videoAssets(project) {
  return (project.shots || []).map((shot) => {
    const version = currentVersionOf(shot);
    const videoPath = version?.video_path || "";
    const asset = assetByPath(project, videoPath);
    const real = isRealShotVideo(asset);
    const card = baseCard("video", shot.title || "镜头", { id: shot.id, shot, version });
    card.preview = real ? videoPath : "";
    card.summary = shot.description || "";
    card.status = shot.status || "";
    card.statusLabel = videoStatusLabel(shot, version, real);
    card.tone = videoTone(shot, real);
    card.meta = {
      版本: version ? `v${version.version_number}` : "-",
      Provider: version?.provider || version?.generation?.provider || "",
      模型: version?.model || version?.generation?.model || "",
      模式: version?.video_mode || "t2v",
    };
    return card;
  });
}

function assemblyAssets(project) {
  const assets = project.assets || [];
  const finals = assets
    .filter((asset) => asset.type === "final-video")
    .slice()
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  const cards = finals.map((asset, index) => {
    const latest = index === 0;
    const card = baseCard("final", latest ? "当前成片" : "历史成片", asset);
    card.preview = latest ? asset.file_path || "" : "";
    card.summary = asset.description || "";
    card.status = latest && project.assembly_stale ? "stale" : latest ? "ready" : "history";
    card.statusLabel = latest ? (project.assembly_stale ? "已过期" : "当前有效") : "历史";
    card.tone = latest ? (project.assembly_stale ? "modified" : "done") : "idle";
    card.meta = { 时间: asset.created_at || "", 镜头数: asset.description || "" };
    return card;
  });
  (project.shots || [])
    .slice()
    .sort((a, b) => Number(a.shot_index || 0) - Number(b.shot_index || 0))
    .forEach((shot) => {
      const version = currentVersionOf(shot);
      const real = shotHasRealVideo(project, shot);
      const card = baseCard("shot-order", `${String(shot.shot_index).padStart(2, "0")}. ${shot.title}`, {
        id: `order-${shot.id}`,
        shot,
        version,
      });
      card.preview = real ? version?.video_path || "" : version?.first_frame_path || "";
      card.summary = shot.description || "";
      card.statusLabel = real ? "视频就绪" : version?.video_path ? "占位/失效" : "缺视频";
      card.tone = real ? "done" : "review";
      cards.push(card);
    });
  return cards;
}

function currentVersionOf(shot) {
  const versions = shot?.versions || [];
  return versions.find((item) => item.id === shot?.current_version_id) || versions[0] || null;
}

function currentVersionPreview(shot) {
  const version = currentVersionOf(shot);
  return version?.first_frame_path || "";
}

function assetPathById(project, assetId) {
  if (!assetId) return "";
  return (project?.assets || []).find((asset) => asset.id === assetId)?.file_path || "";
}

function videoStatusLabel(shot, version, real) {
  if (["video_running"].includes(shot.status)) return "生成中";
  if (["video_waiting_remote"].includes(shot.status)) return "等待云端";
  if (["video_failed", "video_invalid"].includes(shot.status)) return "失败";
  if (version?.video_path && real) return "视频就绪";
  if (version?.video_path && !real) return "占位无效";
  return "未生成";
}

function videoTone(shot, real) {
  if (["video_failed", "video_invalid"].includes(shot.status)) return "failed";
  if (["video_running", "video_waiting_remote"].includes(shot.status)) return "active";
  if (real) return "done";
  return "idle";
}

/* ------------------------------------------------------------------ */
/* 任务中心：把 job / job_events 映射为中文状态行。                        */
/* ------------------------------------------------------------------ */

const JOB_STATUS_LABELS = {
  queued: "已排队",
  running: "处理中",
  waiting_remote: "等待远端返回",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  idle: "空闲",
};

export function jobStatusLabel(status) {
  return JOB_STATUS_LABELS[status] || status || "空闲";
}

const JOB_TYPE_LABELS = {
  adaptation_workflow: "改编流程",
  adaptation_bible: "Story Bible 生成",
  adaptation_storyboard: "分镜草案生成",
  adaptation_production: "进入镜头制作",
  adaptation_regen_scope: "重生成改编方案",
  adaptation_regen_bible: "重生成 Story Bible",
  adaptation_regen_storyboard: "重生成分镜",
  video_generation: "镜头视频生成",
  batch_video_generation: "批量视频生成",
  video_safety_retry: "视频安全重试",
  video_task_refresh: "云端任务回查",
  sequence_assembly: "成片合成",
};

export function jobTypeLabel(type) {
  return JOB_TYPE_LABELS[type] || type || "任务";
}

/** 任务中心行：{ id, name, shotTitle, stageLabel, status, statusLabel, progress, provider, model, message, error, updatedAt } */
export function jobCenterRows(project, shotTitles = {}) {
  const jobs = (project?.jobs || []).slice().sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  const events = project?.job_events || [];
  // Provider/模型记录在 job_events.detail 中，按任务取最近一条带详情的补充。
  const latestDetailByJob = {};
  events.forEach((event) => {
    if (event.job_id && event.detail && (event.detail.provider || event.detail.model)) {
      latestDetailByJob[event.job_id] = event.detail;
    }
  });
  return jobs.map((job) => {
    const detail = latestDetailByJob[job.id] || {};
    return {
      id: job.id,
      name: jobTypeLabel(job.type),
      shotId: job.shot_id || "",
      shotTitle: job.shot_id ? shotTitles[job.shot_id] || job.shot_id : "",
      stage: job.stage || "",
      status: job.status || "idle",
      statusLabel: jobStatusLabel(job.status),
      progress: Number(job.progress ?? 0),
      provider: detail.provider || "",
      model: detail.model || "",
      message: job.message || "",
      error: job.error_message || "",
      updatedAt: job.updated_at || "",
    };
  });
}
