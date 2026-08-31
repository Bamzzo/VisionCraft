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

/** 重置查看/编辑状态：切换项目或进入无项目状态时调用，避免串项目污染。 */
export function resetViewState() {
  state.viewStage = "text";
  state.selectedAsset = null;
  state.assetViewMode = {};
  state.stageEdit = null;
  state.stageModelDraft = {};
  state.workflowControl = { paused: false };
  state.assemblyDraft = null;
  state.statusNotice = null;
}
