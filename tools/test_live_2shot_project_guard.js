/**
 * No-network tests for the run-provenance guards.
 * These exist because a live run adopted a pre-existing project with the same title and then
 * deleted it as if it were this run's temp project.
 */
"use strict";

const assert = require("assert");
const {
  jobCreatedAfter,
  findFailureFromThisRun,
  assertFreshCreatedId,
  cleanupTarget,
} = require("./live_2shot_helpers");

const RUN_START = "2026-09-20T04:13:00.000Z";

function pass(msg) {
  console.log(`PASS: ${msg}`);
}

function testJobProvenance() {
  assert.strictEqual(jobCreatedAfter({ created_at: "2026-09-20T04:09:18.060123+00:00" }, RUN_START), false);
  assert.strictEqual(jobCreatedAfter({ created_at: "2026-09-20T04:13:30.000000+00:00" }, RUN_START), true);
  assert.strictEqual(jobCreatedAfter({ created_at: "2026-09-20T04:13:00.000000+00:00" }, RUN_START), true);
  assert.strictEqual(jobCreatedAfter({}, RUN_START), false, "缺少 created_at 视为来源不明，不据此中断");
  assert.strictEqual(jobCreatedAfter({ created_at: "2026-09-20T04:13:30Z" }, ""), false);
  pass("失败 job 只认本轮开始之后的 created_at，来源不明不触发中断");
}

function testStaleFailureIsIgnored() {
  const project = {
    jobs: [
      { id: "job_old", status: "failed", created_at: "2026-09-20T04:09:20+00:00", message: "invalid task_id" },
      { id: "job_new", status: "completed", created_at: "2026-09-20T04:13:05+00:00" },
    ],
  };
  assert.strictEqual(findFailureFromThisRun(project, null, RUN_START), undefined);
  const withFreshFailure = {
    jobs: [...project.jobs, { id: "job_fresh", status: "failed", created_at: "2026-09-20T04:14:00+00:00" }],
  };
  assert.strictEqual(findFailureFromThisRun(withFreshFailure, null, RUN_START).id, "job_fresh");
  pass("上午遗留的失败 job 不再误判为本轮失败；本轮真失败仍能识别");
}

function testAssertFreshCreatedId() {
  const preexisting = ["project_a43afde7c5", "v1demo_main"];
  assert.strictEqual(assertFreshCreatedId(preexisting, "project_fc30dd39a9"), true);
  let threw = false;
  try {
    assertFreshCreatedId(preexisting, "project_a43afde7c5");
  } catch (error) {
    threw = true;
    assert.match(String(error.message), /在本轮开始前就已存在/);
  }
  assert.ok(threw, "采纳已存在项目必须抛错");
  threw = false;
  try {
    assertFreshCreatedId(preexisting, "");
  } catch (error) {
    threw = true;
  }
  assert.ok(threw, "缺少 id 必须抛错");
  pass("新建后断言 createdId 必须是本轮新 id，同名旧项目会被拒绝");
}

function testCleanupTarget() {
  const protectedIds = ["v1demo_main", "project_5fdac03f50"];
  const preexisting = ["project_a43afde7c5"];
  assert.deepStrictEqual(
    cleanupTarget({ createdProjectId: "project_new01", preexistingIds: preexisting, protectedIds }),
    { ok: true, project_id: "project_new01" }
  );
  assert.strictEqual(
    cleanupTarget({ createdProjectId: "project_a43afde7c5", preexistingIds: preexisting, protectedIds }).reason,
    "not_created_this_run"
  );
  assert.strictEqual(
    cleanupTarget({ createdProjectId: "v1demo_main", preexistingIds: preexisting, protectedIds }).reason,
    "protected_project"
  );
  assert.strictEqual(cleanupTarget({ createdProjectId: "", preexistingIds: [], protectedIds }).reason, "no_created_project_id");
  pass("清理目标只允许本轮新建且非受保护的项目");
}

function testRealIncidentShape() {
  // 本轮开始前的项目集合里含保留项目；新建项目与它同名 → 必须被拒绝
  const preexisting = ["project_a43afde7c5"];
  const adopted = "project_a43afde7c5";
  assert.throws(() => assertFreshCreatedId(preexisting, adopted));
  assert.strictEqual(
    cleanupTarget({ createdProjectId: adopted, preexistingIds: preexisting, protectedIds: [] }).ok,
    false,
    "事后清理也必须拒绝非本轮项目"
  );
  pass("复现事故形态：同名旧项目既不能续跑，也不能被清理");
}

function main() {
  testJobProvenance();
  testStaleFailureIsIgnored();
  testAssertFreshCreatedId();
  testCleanupTarget();
  testRealIncidentShape();
  console.log("PASS: live 2-shot run provenance guards (no network)");
}

main();
