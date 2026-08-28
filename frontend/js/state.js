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
};

export const agents = [
  { name: "Narrative", label: "编剧", detail: "故事圣经 / 分镜初稿" },
  { name: "Director", label: "导演", detail: "角色 / 场景 / 风格" },
  { name: "Assets", label: "资产", detail: "基准图 / 关键帧" },
  { name: "Critic", label: "监制", detail: "一致性质检" },
  { name: "Action", label: "执行", detail: "视频片段生成" },
  { name: "Assembly", label: "剪辑", detail: "封装 / 导出" },
];

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
