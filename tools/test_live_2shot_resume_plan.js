/**
 * No-network unit tests for the retained-project resume planner.
 * Does not launch Playwright, backends, or live providers.
 */
"use strict";

const assert = require("assert");
const { planResume, assertSingleSubmitPerShot, describePlan } = require("./live_2shot_resume_plan");

const SHOT1 = "shot_e84099874d";
const SHOT2 = "shot_77c04d65b5";

function taskRunning() {
  return {
    id: "vt_e0800b9609",
    shot_id: SHOT1,
    job_id: "job_b28d841d8f",
    remote_task_id: "436764667060538",
    status: "running",
    cloud_status: "submitted",
  };
}

function retainedProject() {
  return {
    id: "project_a43afde7c5",
    shots: [
      { id: SHOT1, shot_index: 1, status: "video_running" },
      { id: SHOT2, shot_index: 2, status: "keyframes_ready" },
    ],
    video_tasks: [taskRunning()],
    jobs: [],
  };
}

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

function testRetainedProjectRefreshesOnly() {
  const plan = planResume(retainedProject());
  assert.strictEqual(plan.stop, false);
  assert.strictEqual(plan.refresh.length, 1);
  assert.strictEqual(plan.refresh[0].shot_id, SHOT1);
  assert.strictEqual(plan.refresh[0].remote_task_id, "4367…0538");
  assert.strictEqual(plan.submits.length, 0);
  assert.strictEqual(plan.counts.blocked, 1, "镜头 2 未授权时必须被挡");
  assert.strictEqual(plan.blocked[0].reason, "not_authorized_by_cli");
  assert.strictEqual(plan.can_assemble, false);
  pass("残留现场默认只 refresh 镜头 1，镜头 2 需显式授权");
}

function testAuthorizedShotTwoSubmitsExactlyOnce() {
  const plan = planResume(retainedProject(), { allowedSubmitShotIds: [SHOT2] });
  assert.strictEqual(plan.submits.length, 1);
  assert.strictEqual(plan.submits[0].shot_id, SHOT2);
  assert.strictEqual(plan.refresh.length, 1);
  assert.strictEqual(plan.counts.blocked, 0);
  assert.ok(assertSingleSubmitPerShot(plan));
  pass("授权后仅提交镜头 2 一次，镜头 1 仍只回查");
}

function testShotWithRemoteNeverSubmits() {
  const project = retainedProject();
  project.shots[0].status = "keyframes_ready";
  const plan = planResume(project, { allowedSubmitShotIds: [SHOT1, SHOT2] });
  const shot1Submits = plan.submits.filter((item) => item.shot_id === SHOT1);
  assert.strictEqual(shot1Submits.length, 0, "已有 remote_task_id 的镜头绝不重新提交");
  assert.strictEqual(plan.refresh.filter((item) => item.shot_id === SHOT1).length, 1);
  pass("镜头状态回退到 keyframes_ready 但已有 remote_task_id 时仍只 refresh");
}

function testFailedShotStopsRun() {
  const project = retainedProject();
  project.video_tasks[0].status = "failed";
  project.shots[0].status = "video_failed";
  const plan = planResume(project, { allowedSubmitShotIds: [SHOT1, SHOT2] });
  assert.strictEqual(plan.stop, true);
  assert.strictEqual(plan.stop_reason, "shot_failed_do_not_resubmit");
  assert.strictEqual(plan.submits.length, 0, "失败即停，不补发任何镜头");
  assert.strictEqual(plan.refresh.length, 0);
  assert.strictEqual(plan.can_assemble, false);
  pass("镜头失败时立即停止，不自动补发");
}

function testBothReadyAllowsAssembly() {
  const project = {
    id: "project_a43afde7c5",
    shots: [
      { id: SHOT1, shot_index: 1, status: "video_ready" },
      { id: SHOT2, shot_index: 2, status: "video_ready" },
    ],
    video_tasks: [
      { id: "vt1", shot_id: SHOT1, remote_task_id: "r1", status: "completed" },
      { id: "vt2", shot_id: SHOT2, remote_task_id: "r2", status: "completed" },
    ],
    jobs: [],
  };
  const plan = planResume(project, { allowedSubmitShotIds: [SHOT1, SHOT2] });
  assert.strictEqual(plan.can_assemble, true);
  assert.strictEqual(plan.submits.length, 0);
  assert.strictEqual(plan.refresh.length, 0);
  assert.strictEqual(plan.counts.ready, 2);
  pass("两镜都完成后才允许进入合成，且不再提交任何任务");
}

function testBothInflightRefreshesBoth() {
  const project = retainedProject();
  project.shots[1].status = "video_running";
  project.video_tasks.push({
    id: "vt_second",
    shot_id: SHOT2,
    remote_task_id: "999900001111",
    status: "running",
  });
  const plan = planResume(project, { allowedSubmitShotIds: [SHOT1, SHOT2] });
  assert.strictEqual(plan.refresh.length, 2);
  assert.strictEqual(plan.submits.length, 0);
  assert.strictEqual(plan.can_assemble, false);
  pass("两镜都 inflight 时全部只回查，一次都不重新提交");
}

function testInflightShotWithoutTaskNotSubmitted() {
  const project = {
    id: "project_a43afde7c5",
    shots: [{ id: SHOT2, shot_index: 2, status: "video_running" }],
    video_tasks: [],
    jobs: [{ id: "job_x", shot_id: SHOT2, status: "running" }],
  };
  const plan = planResume(project, { allowedSubmitShotIds: [SHOT2] });
  assert.strictEqual(plan.submits.length, 0, "落库间隙不得判定为可提交");
  assert.strictEqual(plan.refresh.length, 1);
  assert.strictEqual(plan.refresh[0].reason, "shot_inflight");
  pass("落库间隙（镜头 inflight 但任务未落库）只 refresh，绝不补发");
}

function testDescribePlanIsReadable() {
  const plan = planResume(retainedProject(), { allowedSubmitShotIds: [SHOT2] });
  const text = describePlan(plan);
  assert.match(text, /待回查 1/);
  assert.match(text, /待提交 1/);
  assert.match(text, /can_assemble=false/);
  pass("计划文本可读且包含关键计数");
}

function main() {
  testRetainedProjectRefreshesOnly();
  testAuthorizedShotTwoSubmitsExactlyOnce();
  testShotWithRemoteNeverSubmits();
  testFailedShotStopsRun();
  testBothReadyAllowsAssembly();
  testBothInflightRefreshesBoth();
  testInflightShotWithoutTaskNotSubmitted();
  testDescribePlanIsReadable();
  console.log("PASS: live 2-shot resume planner (no network)");
}

main();
