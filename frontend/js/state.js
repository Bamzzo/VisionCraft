export const state = {
  capabilities: null,
  diagnostics: null,
  projects: [],
  project: null,
  selectedShotId: null,
  eventSource: null,
  pollTimer: null,
  memoryResults: [],
  videoDraft: null,
  jobEvents: [],
  lastEventId: 0,
  shotProgress: {},
  sseConnected: false,
  timelineOpen: false,
  remoteRefreshTimer: null,
  refreshInFlight: false,
  // 「流程控制正在执行」（暂停/继续/采用/重做）。放在共享 state 而不是 app.js 的
  // 模块级变量，是因为 render.js 需要据此禁用按钮：刷新会在 finally 复位之前先按
  // can_resume 把按钮解禁，那个窗口里的点击会被入口守卫静默吞掉。
  flowBusy: false,
  observerToken: 0,
  observedProjectId: null,

  /* ---- 查看状态（与执行状态分离，见 ui-layout-interaction-design 第 3 节） ---- */
  // 用户当前查看的阶段；点击右侧导航只改变它，不触发任何任务。
  viewStage: "text",
  // 当前选中的阶段素材：{ stage, key } 或 null。
  selectedAsset: null,
  // 每个阶段的素材视图模式：grid（缩略图）或 single（单素材）。
  assetViewMode: {},

  /* ---- 项目表单状态（新建/创建/查看分离） ---- */
  // summary：查看已有项目配置摘要；create：空白新建表单；edit：编辑已保存的项目设置。
  projectFormMode: "summary",
  // 新建表单是否有未提交输入（用于切换项目时的未保存守卫）。
  formTouched: false,

  /* ---- 素材编辑状态（脏状态驱动重做按钮） ---- */
  // { stage, key, baseline, draft, dirty }：baseline 为进入编辑时的快照。
  stageEdit: null,

  /* ---- 流程控制由后端 checkpoint / project.status 决定，切换项目时清空本地提示 ---- */
  workflowControl: { paused: false },
  // 视觉锚点门：跳过一致性机制需要点两次，第一次只把按钮武装成确认态。
  // 放在共享 state 而不是 app.js 的模块级变量，是因为 render.js 要据此渲染按钮文案。
  // anchorSkipArmedSig 记录武装时的就绪度快照（anchorSkipSignature），就绪度一变即失效——
  // 否则"先武装、去挂锚点、再回来"就能一次点击放行，二次确认会被绕过。
  anchorSkipArmed: false,
  anchorSkipArmedSig: "",
  assetUpload: { role: "", status: "idle", message: "" },

  stageModelDraft: {},
  /* ---- 成片配置草稿（未保存时刷新/任务更新不丢，切换项目时清空） ---- */
  assemblyDraft: null,
  statusNotice: null,
};

export function selectedShot() {
  if (!state.project) return null;
  return state.project.shots.find((shot) => shot.id === state.selectedShotId) || state.project.shots[0] || null;
}

export function latestVersion(shot) {
  if (!shot || !shot.versions || shot.versions.length === 0) return null;
  return shot.versions[0];
}

export function currentVersion(shot) {
  if (!shot || !shot.versions || shot.versions.length === 0) return null;
  return shot.versions.find((item) => item.id === shot.current_version_id) || latestVersion(shot);
}

/**
 * 撤销视觉锚点门「跳过」的武装状态。
 *
 * 凡是让锚点集合发生变化、或让用户离开这道门的动作，都要调用它：二次确认的前提是
 * 两次点击针对同一件事，跨动作保留武装会把"两次点击"降级成"一次点击"。
 */
export function disarmAnchorSkip() {
  state.anchorSkipArmed = false;
  state.anchorSkipArmedSig = "";
}

/** 重置查看/编辑状态：切换项目或进入无项目状态时调用，避免串项目污染。 */
export function resetViewState() {
  state.viewStage = "text";
  state.selectedAsset = null;
  state.assetViewMode = {};
  state.stageEdit = null;
  state.stageModelDraft = {};
  state.workflowControl = { paused: false };
  disarmAnchorSkip();
  state.assetUpload = { role: "", status: "idle", message: "" };
  state.assemblyDraft = null;
  state.statusNotice = null;
}
