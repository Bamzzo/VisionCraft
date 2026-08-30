const jsonHeaders = {
  "Content-Type": "application/json",
};

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    let message = text || `Request failed: ${response.status}`;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed.detail)) {
        message = parsed.detail.map((item) => `${item.loc?.join(".")}: ${item.msg}`).join("; ");
      } else if (parsed.detail) {
        message = parsed.detail;
      }
    } catch {
      // Keep raw text.
    }
    throw new Error(message);
  }
  return response.json();
}

export const api = {
  health: () => request("/api/health"),
  capabilities: () => request("/api/providers/capabilities"),
  getJob: (jobId) => request(`/api/jobs/${jobId}`),
  jobEvents: (projectId, afterId = 0) => request(`/api/projects/${projectId}/job-events?after_id=${afterId}`),
  diagnostics: () => request("/api/providers/diagnostics"),
  listProjects: () => request("/api/projects"),
  getProject: (id) => request(`/api/projects/${id}`),
  deleteProject: (id) =>
    request(`/api/projects/${id}`, {
      method: "DELETE",
    }),
  createProject: (payload) =>
    request("/api/projects", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    }),
  runProject: (id) =>
    request(`/api/projects/${id}/run`, {
      method: "POST",
    }),
  resumeProject: (id) =>
    request(`/api/projects/${id}/resume`, {
      method: "POST",
    }),
  retryProject: (id) =>
    request(`/api/projects/${id}/retry`, {
      method: "POST",
    }),
  sendFeedback: (projectId, shotId, userText) =>
    request(`/api/projects/${projectId}/shots/${shotId}/feedback`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ user_text: userText }),
    }),
  selectKeyframes: (projectId, shotId, payload) =>
    request(`/api/projects/${projectId}/shots/${shotId}/keyframes/select`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    }),
  redrawKeyframes: (projectId, shotId, target) =>
    request(`/api/projects/${projectId}/shots/${shotId}/keyframes/redraw`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ target }),
    }),
  generateVideo: (projectId, shotId, payload = {}) =>
    request(`/api/projects/${projectId}/shots/${shotId}/video`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(typeof payload === "string" ? { video_mode: payload } : payload),
    }),
  getShotEditor: (projectId, shotId) => request(`/api/projects/${projectId}/shots/${shotId}/editor`),
  saveShotDraft: (projectId, shotId, payload) =>
    request(`/api/projects/${projectId}/shots/${shotId}/draft`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  freezeShotVersion: (projectId, shotId, payload = {}) =>
    request(`/api/projects/${projectId}/shots/${shotId}/versions`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  safeRetryVideo: (projectId, shotId) =>
    request(`/api/projects/${projectId}/shots/${shotId}/video/safe-retry`, {
      method: "POST",
    }),
  rollbackVersion: (projectId, shotId, versionId) =>
    request(`/api/projects/${projectId}/shots/${shotId}/versions/${versionId}/rollback`, {
      method: "POST",
    }),
  generateAllVideos: (projectId) =>
    request(`/api/projects/${projectId}/videos`, {
      method: "POST",
    }),
  refreshVideoTasks: (projectId) =>
    request(`/api/projects/${projectId}/videos/refresh`, {
      method: "POST",
    }),
  getAssembly: (projectId) => request(`/api/projects/${projectId}/assembly`),
  getAssemblySettings: (projectId) => request(`/api/projects/${projectId}/assembly-settings`),
  saveAssemblySettings: (projectId, payload) =>
    request(`/api/projects/${projectId}/assembly-settings`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  assembleProject: (projectId) =>
    request(`/api/projects/${projectId}/assemble`, {
      method: "POST",
    }),
  cleanupDemoData: (keepProjectId) =>
    request("/api/projects/demo/cleanup", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ keep_project_id: keepProjectId }),
    }),
  searchMemory: (projectId, query) => request(`/api/projects/${projectId}/memory/search?q=${encodeURIComponent(query)}`),
  getAdaptation: (projectId) => request(`/api/projects/${projectId}/adaptation`),
  selectAdaptationOption: (projectId, optionId) =>
    request(`/api/projects/${projectId}/adaptation/options/${optionId}/select`, { method: "POST" }),
  confirmScope: (projectId, optionId) =>
    request(`/api/projects/${projectId}/adaptation/scope/confirm`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ option_id: optionId }),
    }),
  saveBible: (projectId, payload) =>
    request(`/api/projects/${projectId}/adaptation/bible`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  confirmBible: (projectId, payload = {}) =>
    request(`/api/projects/${projectId}/adaptation/bible/confirm`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  generateStoryboard: (projectId) =>
    request(`/api/projects/${projectId}/adaptation/storyboard`, { method: "POST" }),
  saveStoryboard: (projectId, shots) =>
    request(`/api/projects/${projectId}/adaptation/storyboard`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify({ shots }),
    }),
  confirmStoryboard: (projectId, shots) =>
    request(`/api/projects/${projectId}/adaptation/storyboard/confirm`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(shots ? { shots } : {}),
    }),
  regenerateAdaptation: (projectId, stage) =>
    request(`/api/projects/${projectId}/adaptation/regenerate`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ stage }),
    }),
  getMediumText: (projectId) => request(`/api/projects/${projectId}/medium-text`),
  analyzeMediumText: (projectId) =>
    request(`/api/projects/${projectId}/medium-text/analyze`, { method: "POST" }),
  selectStoryline: (projectId, storylineId) =>
    request(`/api/projects/${projectId}/medium-text/storylines/${storylineId}/select`, { method: "POST" }),
  saveMediumScope: (projectId, payload) =>
    request(`/api/projects/${projectId}/medium-text/scope`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  recommendMediumScope: (projectId) =>
    request(`/api/projects/${projectId}/medium-text/scope/recommend`, { method: "POST" }),
  confirmMediumScope: (projectId, payload = {}) =>
    request(`/api/projects/${projectId}/medium-text/scope/confirm`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload || {}),
    }),
  regenerateMedium: (projectId, stage) =>
    request(`/api/projects/${projectId}/medium-text/regenerate`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ stage }),
    }),
};
