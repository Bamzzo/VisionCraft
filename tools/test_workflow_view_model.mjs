/**
 * 无费用 fixture 测试：workflowViewModel 的阶段推导、状态分离与素材派生。
 * 运行：node tools/test_workflow_view_model.mjs
 */
import {
  STAGES,
  STAGE_STATE,
  computeWorkflow,
  executionStageId,
  stageAssets,
  stageStateLabel,
  jobCenterRows,
  jobStatusLabel,
} from "../frontend/js/workflowViewModel.js";

function assert(condition, message) {
  if (!condition) throw new Error(`断言失败：${message}`);
}

function stageById(workflow, id) {
  return workflow.stages.find((stage) => stage.id === id);
}

/* ---------- 基础项目 fixture（字段与后端 get_project 对齐） ---------- */
function baseProject(overrides = {}) {
  return {
    id: "project_fixture",
    title: "样例",
    status: "created",
    source_text: "方源走在青茅山的夜路上。",
    text_scale: "short",
    text_scale_label: "短文本：直接改编",
    review_mode: 0,
    adaptation_options: [],
    storylines: [],
    story_events: [],
    story_bible: null,
    storyboard_drafts: [],
    shots: [],
    assets: [],
    jobs: [],
    active_jobs: [],
    job_events: [],
    ...overrides,
  };
}

/* 1. 新建项目：执行阶段在文本理解，其余未开始，故事线对短文本跳过。 */
{
  const workflow = computeWorkflow(baseProject());
  assert(workflow.executionStage === "text", "created 项目执行阶段应为 text");
  assert(stageById(workflow, "storyline").state === STAGE_STATE.SKIPPED, "短文本故事线应跳过");
  assert(stageById(workflow, "adaptation").state === STAGE_STATE.NOT_STARTED, "改编方案应未开始");
  assert(stageById(workflow, "assembly").state === STAGE_STATE.NOT_STARTED, "成片应未开始");
  console.log("PASS: 新建项目执行阶段与跳过态");
}

/* 2. 中等文本：故事线阶段等待审核。 */
{
  const workflow = computeWorkflow(
    baseProject({
      text_scale: "medium",
      text_scale_label: "中等文本：先选择故事线，再进行改编",
      status: "awaiting_storyline_review",
      storylines: [{ id: "line1", title: "线一", selected: 1 }],
    })
  );
  assert(workflow.executionStage === "storyline", "中等文本等待故事线审核");
  assert(stageById(workflow, "storyline").state === STAGE_STATE.AWAITING_REVIEW, "故事线应等待审核");
  assert(stageById(workflow, "storyline").tone === "review", "等待审核用琥珀色调");
  console.log("PASS: 中等文本故事线等待审核");
}

/* 3. 改编方案等待审核；上游已完成。 */
{
  const workflow = computeWorkflow(
    baseProject({
      status: "awaiting_scope_review",
      adaptation_options: [{ id: "opt1", title: "方案一", selected: 0 }],
    })
  );
  assert(workflow.executionStage === "adaptation", "等待范围审核时执行阶段为 adaptation");
  assert(stageById(workflow, "text").state === STAGE_STATE.COMPLETED, "文本理解应已完成");
  assert(stageById(workflow, "adaptation").state === STAGE_STATE.AWAITING_REVIEW, "改编方案应等待审核");
  console.log("PASS: 改编方案等待审核且上游完成");
}

/* 4. 上游重做后下游失效：Bible 回到等待审核，但分镜/镜头/视频仍有历史数据。 */
{
  const staleShot = {
    id: "shot1",
    title: "镜头 1",
    status: "video_ready",
    current_version_id: "v1",
    versions: [{ id: "v1", version_number: 1, first_frame_path: "/a.jpg", last_frame_path: "/b.jpg", video_path: "/v.mp4" }],
  };
  const workflow = computeWorkflow(
    baseProject({
      status: "awaiting_bible_review",
      story_bible: { review_status: "draft", adaptation_summary: "x" },
      storyboard_drafts: [],
      shots: [staleShot],
      assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
    })
  );
  assert(workflow.executionStage === "bible", "Bible 重做后执行阶段回退到 bible");
  assert(stageById(workflow, "keyframes").state === STAGE_STATE.INVALIDATED, "关键帧应标记已失效");
  assert(stageById(workflow, "video").state === STAGE_STATE.INVALIDATED, "镜头视频应标记已失效");
  assert(stageById(workflow, "storyboard").state === STAGE_STATE.NOT_STARTED, "分镜被清除后应未开始");
  console.log("PASS: 上游重做后下游标记失效且历史保留");
}

/* 5. 制作期 frontier：分镜确认后进入关键帧/视频。 */
{
  const shot = {
    id: "shot1",
    title: "镜头 1",
    status: "keyframes_ready",
    current_version_id: "v1",
    versions: [{ id: "v1", version_number: 1, first_frame_path: "/a.jpg", last_frame_path: "/b.jpg", video_path: null }],
  };
  const workflow = computeWorkflow(baseProject({ status: "production_ready", shots: [shot] }));
  assert(["keyframes", "video"].includes(workflow.executionStage), "制作期 frontier 应在关键帧或视频");
  assert(stageById(workflow, "storyboard").state === STAGE_STATE.COMPLETED, "分镜确认后应已完成");
  console.log("PASS: 制作期 frontier 推导");
}

/* 6. 素材派生：视频阶段卡片带 Provider/模型/版本。 */
{
  const shot = {
    id: "shot1",
    title: "镜头 1",
    status: "video_ready",
    current_version_id: "v1",
    versions: [
      { id: "v1", version_number: 2, video_path: "/v.mp4", provider: "seedance", model: "doubao-seedance-1-0-pro", video_mode: "i2v" },
    ],
  };
  const project = baseProject({
    status: "production_ready",
    shots: [shot],
    assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
  });
  const cards = stageAssets(project, "video");
  assert(cards.length === 1, "视频阶段应有一个镜头卡片");
  assert(cards[0].meta.Provider === "seedance", "卡片应带 Provider");
  assert(cards[0].meta["版本"] === "v2", "卡片应带版本号");
  assert(cards[0].statusLabel === "视频就绪", "真实视频应显示就绪");
  console.log("PASS: 视频阶段素材派生");
}

/* 7. 任务中心中文状态与 Provider/模型派生。 */
{
  const project = baseProject({
    jobs: [
      { id: "job1", type: "video_generation", status: "waiting_remote", progress: 40, message: "等待云端", shot_id: "shot1", stage: "queued", updated_at: "2026-08-30T01:00:00Z" },
    ],
    job_events: [
      { id: 1, job_id: "job1", event_type: "job.update", stage: "queued", status: "waiting_remote", progress: 40, message: "等待云端", detail: { provider: "seedance", model: "doubao-seedance" }, created_at: "2026-08-30T01:00:00Z" },
    ],
  });
  const rows = jobCenterRows(project, { shot1: "镜头 1" });
  assert(rows[0].statusLabel === "等待远端返回", "waiting_remote 应映射为等待远端返回");
  assert(rows[0].provider === "seedance", "任务行应带 Provider");
  assert(rows[0].shotTitle === "镜头 1", "任务行应带镜头名");
  assert(jobStatusLabel("running") === "处理中", "running 中文标签");
  console.log("PASS: 任务中心中文状态与详情派生");
}

/* 8. 阶段状态中文标签完整。 */
{
  Object.values(STAGE_STATE).forEach((value) => {
    assert(stageStateLabel(value), `状态 ${value} 应有中文标签`);
  });
  assert(STAGES.length === 8, "应始终显示 8 个阶段");
  console.log("PASS: 阶段状态中文标签完整");
}

console.log("ALL WORKFLOW VIEW MODEL TESTS PASSED");
