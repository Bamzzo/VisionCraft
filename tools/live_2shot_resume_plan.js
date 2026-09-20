/**
 * Pure resume planning for the retained LIVE2SHOT project.
 * No Playwright, no network. Required by tools/live_2shot_resume.cjs and its Mock test.
 *
 * Rules:
 * - A shot with an existing remote task is only ever refreshed, never re-submitted.
 * - A shot with no task is only submitted when its id is explicitly authorized by CLI.
 * - A failed shot stops the whole run; nothing is auto-reissued.
 */
"use strict";

const { videoWaitDecision, shotVideoVerdict, canEnterAssembly, redact } = require("./live_2shot_helpers");

const SUBMITTABLE_SHOT_STATUSES = new Set(["keyframes_ready"]);

function shotLabel(shot) {
  return `shot_index=${shot.shot_index || "?"} ${shot.id}`;
}

function planResume(project, options = {}) {
  const allowedSubmitShotIds = new Set(options.allowedSubmitShotIds || []);
  const shots = (project.shots || []).slice().sort((a, b) => (a.shot_index || 0) - (b.shot_index || 0));
  const refresh = [];
  const submits = [];
  const blocked = [];
  const failures = [];
  const ready = [];

  // First pass: any failed shot stops the whole run before any action is planned.
  for (const shot of shots) {
    const verdict = shotVideoVerdict(project, shot);
    if (!verdict.failed) continue;
    failures.push({
      shot_id: shot.id,
      shot_index: shot.shot_index || null,
      status: shot.status || "",
      message: verdict.message || "镜头失败",
      remote_task_id: redact((verdict.task || {}).remote_task_id || ""),
    });
  }

  if (failures.length) {
    for (const shot of shots) {
      blocked.push({
        shot_id: shot.id,
        shot_index: shot.shot_index || null,
        reason: "stopped_by_failed_shot",
        status: shot.status || "",
      });
    }
    return {
      project_id: project.id || "",
      stop: true,
      stop_reason: "shot_failed_do_not_resubmit",
      refresh,
      submits,
      blocked,
      failures,
      ready,
      can_assemble: false,
      counts: {
        shots: shots.length,
        ready: ready.length,
        refresh: refresh.length,
        submits: submits.length,
        blocked: blocked.length,
        failures: failures.length,
      },
    };
  }

  for (const shot of shots) {
    const verdict = shotVideoVerdict(project, shot);
    if (verdict.ready) {
      ready.push({
        shot_id: shot.id,
        shot_index: shot.shot_index || null,
        remote_task_id: redact((verdict.task || {}).remote_task_id || ""),
      });
      continue;
    }
    const decision = videoWaitDecision(project, shot.id);
    if (decision.action === "refresh_only") {
      refresh.push({
        shot_id: shot.id,
        shot_index: shot.shot_index || null,
        label: shotLabel(shot),
        reason: decision.reason,
        status: shot.status || "",
        remote_task_id: redact((decision.task || {}).remote_task_id || ""),
      });
      continue;
    }
    if (decision.allow_post_video !== true) {
      blocked.push({
        shot_id: shot.id,
        shot_index: shot.shot_index || null,
        reason: "allow_post_video_false",
        status: shot.status || "",
      });
      continue;
    }
    if (!SUBMITTABLE_SHOT_STATUSES.has(String(shot.status || ""))) {
      blocked.push({
        shot_id: shot.id,
        shot_index: shot.shot_index || null,
        reason: `status_not_submittable:${shot.status || "empty"}`,
        status: shot.status || "",
      });
      continue;
    }
    if (!allowedSubmitShotIds.has(shot.id)) {
      blocked.push({
        shot_id: shot.id,
        shot_index: shot.shot_index || null,
        reason: "not_authorized_by_cli",
        status: shot.status || "",
      });
      continue;
    }
    submits.push({
      shot_id: shot.id,
      shot_index: shot.shot_index || null,
      label: shotLabel(shot),
      reason: "no_task_submit_once",
      status: shot.status || "",
    });
  }

  return {
    project_id: project.id || "",
    stop: false,
    stop_reason: "",
    refresh,
    submits,
    blocked,
    failures,
    ready,
    can_assemble: canEnterAssembly(project),
    counts: {
      shots: shots.length,
      ready: ready.length,
      refresh: refresh.length,
      submits: submits.length,
      blocked: blocked.length,
      failures: failures.length,
    },
  };
}

function assertSingleSubmitPerShot(plan) {
  const seen = new Set();
  for (const item of plan.submits || []) {
    if (seen.has(item.shot_id)) {
      throw new Error(`镜头 ${item.shot_id} 在一次计划中出现多次提交，已拒绝`);
    }
    seen.add(item.shot_id);
  }
  return true;
}

function describePlan(plan) {
  const lines = [];
  lines.push(
    `计划：镜头 ${plan.counts.shots} 个，已完成 ${plan.counts.ready}，待回查 ${plan.counts.refresh}，待提交 ${plan.counts.submits}，被挡 ${plan.counts.blocked}，失败 ${plan.counts.failures}`
  );
  for (const item of plan.refresh) {
    lines.push(`  refresh  镜头 ${item.shot_index}（${item.reason} remote=${item.remote_task_id || "-"}）`);
  }
  for (const item of plan.submits) {
    lines.push(`  submit   镜头 ${item.shot_index}（仅一次，${item.reason}）`);
  }
  for (const item of plan.blocked) {
    lines.push(`  blocked  镜头 ${item.shot_index}（${item.reason}）`);
  }
  for (const item of plan.failures) {
    lines.push(`  failure  镜头 ${item.shot_index}（${item.message}）`);
  }
  lines.push(`  can_assemble=${plan.can_assemble}`);
  if (plan.stop) lines.push(`  STOP: ${plan.stop_reason}`);
  return lines.join("\n");
}

module.exports = {
  SUBMITTABLE_SHOT_STATUSES,
  planResume,
  assertSingleSubmitPerShot,
  describePlan,
};
