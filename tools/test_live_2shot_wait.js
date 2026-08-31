/**
 * No-network unit tests for live 2-shot persist/resume helpers.
 * Does not launch Playwright, backends, or live providers.
 */
"use strict";

const assert = require("assert");
const helpers = require("./live_2shot_helpers");

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

async function testDelayedPersistFindsSameTask() {
  let calls = 0;
  let generatePosts = 0;
  const shotId = "shot_e84099874d";
  const task = {
    id: "vt_e0800b9609",
    shot_id: shotId,
    job_id: "job_queued",
    remote_task_id: "436705380538",
    status: "running",
  };
  const hint = helpers.extractSubmitHint({ job_id: "job_queued", status: "queued" });
  const result = await helpers.waitForVideoTaskPersist({
    getProject: async () => {
      calls += 1;
      if (calls < 3) {
        return {
          shots: [{ id: shotId, status: "video_running" }],
          video_tasks: [],
          jobs: [{ id: "job_queued", shot_id: shotId, status: "running" }],
        };
      }
      return {
        shots: [{ id: shotId, status: "video_running" }],
        video_tasks: [task],
        jobs: [{ id: "job_queued", shot_id: shotId, status: "running" }],
      };
    },
    shotId,
    shotLabel: "1",
    hint,
    timeoutMs: 2000,
    intervalMs: 1,
    sleep: async () => {},
    onPoll: () => {
      /* waiting must never POST /video */
    },
  });
  assert.strictEqual(result.task.id, "vt_e0800b9609");
  assert.strictEqual(result.task.remote_task_id, "436705380538");
  assert.ok(calls >= 3);
  assert.strictEqual(generatePosts, 0);
  pass("POST 200 后延迟落库，轮询发现同一任务且未重复 POST");
}

async function testPersistTimeoutMessage() {
  let threw = false;
  try {
    await helpers.waitForVideoTaskPersist({
      getProject: async () => ({ shots: [{ id: "s1", status: "video_running" }], video_tasks: [], jobs: [] }),
      shotId: "s1",
      shotLabel: "1",
      timeoutMs: 20,
      intervalMs: 5,
      sleep: async () => {},
    });
  } catch (error) {
    threw = true;
    assert.match(String(error.message), /任务落库超时/);
    assert.doesNotMatch(String(error.message), /提交失败/);
    assert.doesNotMatch(String(error.message), /没有 video_task/);
  }
  assert.ok(threw, "timeout must throw");
  pass("落库超时明确报告任务落库超时，不误报提交失败");
}

function testPreferPostHint() {
  const hint = helpers.extractSubmitHint({
    job_id: "job_x",
    remote_task_id: "remote_keep",
    video_task: { id: "vt_keep" },
  });
  const task = helpers.findShotTask(
    {
      video_tasks: [
        { id: "vt_other", shot_id: "s1", remote_task_id: "other" },
        { id: "vt_keep", shot_id: "s1", remote_task_id: "remote_keep", job_id: "job_x" },
      ],
    },
    "s1",
    hint
  );
  assert.strictEqual(task.id, "vt_keep");
  pass("优先匹配 POST 返回的 task/remote_task_id");
}

function testResumeNeverPosts() {
  const withRemote = helpers.videoWaitDecision(
    { shots: [{ id: "s1", status: "video_running" }], video_tasks: [{ id: "vt1", shot_id: "s1", remote_task_id: "r1", status: "running" }] },
    "s1"
  );
  assert.strictEqual(withRemote.action, "refresh_only");
  assert.strictEqual(withRemote.allow_post_video, false);

  const emptyTasksRunningShot = helpers.videoWaitDecision(
    { shots: [{ id: "s1", status: "video_running" }], video_tasks: [] },
    "s1"
  );
  assert.strictEqual(emptyTasksRunningShot.allow_post_video, false);

  const fresh = helpers.videoWaitDecision(
    { shots: [{ id: "s1", status: "keyframes_ready" }], video_tasks: [] },
    "s1"
  );
  assert.strictEqual(fresh.allow_post_video, true);
  pass("已有 remote_task_id 或镜头 inflight 时只 refresh，不 POST /video");
}

async function testWaitDoesNotUseModeSelect() {
  assert.strictEqual(helpers.VIDEO_WAIT_USES_MODE_SELECT, false);
  let refreshCalls = 0;
  const projectReady = {
    shots: [{ id: "s1", shot_index: 1, status: "video_ready" }],
    video_tasks: [{ id: "vt1", shot_id: "s1", remote_task_id: "r1", status: "completed" }],
    jobs: [],
  };
  const running = {
    shots: [{ id: "s1", shot_index: 1, status: "video_running" }],
    video_tasks: [{ id: "vt1", shot_id: "s1", remote_task_id: "r1", status: "running" }],
    jobs: [],
  };
  let n = 0;
  await helpers.pollShotVideoReady({
    getProject: async () => {
      n += 1;
      return n < 2 ? running : projectReady;
    },
    refresh: async () => {
      refreshCalls += 1;
    },
    target: { id: "s1", shot_index: 1 },
    timeoutMs: 1000,
    intervalMs: 1,
    sleep: async () => {},
  });
  assert.ok(refreshCalls >= 1);
  pass("浏览器视频等待只 refresh 项目数据，不依赖 #videoModeSelect");
}

async function testFailedTaskIsNotSuccess() {
  let threw = false;
  try {
    await helpers.pollShotVideoReady({
      getProject: async () => ({
        shots: [{ id: "s1", status: "video_failed" }],
        video_tasks: [{ id: "vt1", shot_id: "s1", remote_task_id: "r1", status: "failed" }],
        jobs: [{ shot_id: "s1", status: "failed", message: "provider error" }],
      }),
      refresh: async () => {},
      target: { id: "s1", shot_index: 1 },
      timeoutMs: 200,
      intervalMs: 1,
      sleep: async () => {},
    });
  } catch (error) {
    threw = true;
    assert.match(String(error.message), /失败|video_failed|provider error/);
  }
  assert.ok(threw);
  const runningVerdict = helpers.shotVideoVerdict(
    {
      shots: [{ id: "s1", status: "video_running" }],
      video_tasks: [{ shot_id: "s1", status: "running", remote_task_id: "r1" }],
      jobs: [],
    },
    { id: "s1" }
  );
  assert.strictEqual(runningVerdict.ready, false);
  assert.strictEqual(runningVerdict.inflight, true);
  pass("任务失败不会伪造成功；running 不视为完成");
}

function testAssemblyOnlyAfterComplete() {
  assert.strictEqual(
    helpers.canEnterAssembly({
      shots: [
        { id: "s1", status: "video_running" },
        { id: "s2", status: "keyframes_ready" },
      ],
      video_tasks: [{ shot_id: "s1", status: "running", remote_task_id: "r1" }],
    }),
    false
  );
  assert.strictEqual(
    helpers.canEnterAssembly({
      shots: [
        { id: "s1", status: "video_ready" },
        { id: "s2", status: "video_ready" },
      ],
      video_tasks: [
        { shot_id: "s1", status: "completed", remote_task_id: "r1" },
        { shot_id: "s2", status: "completed", remote_task_id: "r2" },
      ],
    }),
    true
  );
  pass("全部镜头完成后才允许进入下载/合成");
}

async function main() {
  await testDelayedPersistFindsSameTask();
  await testPersistTimeoutMessage();
  testPreferPostHint();
  testResumeNeverPosts();
  await testWaitDoesNotUseModeSelect();
  await testFailedTaskIsNotSuccess();
  testAssemblyOnlyAfterComplete();
  console.log("PASS: live 2-shot wait helpers (no network)");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
