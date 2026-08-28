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
};

export function workflowSteps(project) {
  const production = { name: "production", label: "制作镜头", statuses: ["production_ready", "ready_for_review", "video_ready"] };
  if (project?.text_scale === "medium") {
    return [
      { name: "storyline", label: "1. 选择故事线", statuses: ["awaiting_storyline_review"] },
      { name: "scope", label: "2. 选择故事范围", statuses: ["adaptation_options_ready", "awaiting_scope_review"] },
      { name: "bible", label: "3. 确认 Story Bible", statuses: ["story_bible_ready", "awaiting_bible_review"] },
      { name: "storyboard", label: "4. 审核分镜", statuses: ["storyboard_draft_ready", "awaiting_storyboard_review"] },
      { ...production, label: "5. 制作镜头" },
    ];
  }
  return [
    { name: "scope", label: "1. 选择故事范围", statuses: ["adaptation_options_ready", "awaiting_scope_review"] },
    { name: "bible", label: "2. 确认 Story Bible", statuses: ["story_bible_ready", "awaiting_bible_review"] },
    { name: "storyboard", label: "3. 审核分镜", statuses: ["storyboard_draft_ready", "awaiting_storyboard_review"] },
    { ...production, label: "4. 制作镜头" },
  ];
}

export const agents = workflowSteps(null);

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
