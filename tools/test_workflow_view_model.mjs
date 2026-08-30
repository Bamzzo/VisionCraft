/**
 * 无费用 fixture 测试：workflowViewModel 的阶段推导、状态分离与素材派生。
 * 运行：node tools/test_workflow_view_model.mjs
 */
import {
  STAGES,
  STAGE_STATE,
  computeWorkflow,
  executionStageId,
  resolveStageId,
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

const STAGE_IDS = ["text", "storyline", "bible", "storyboard", "keyframes", "video", "assembly", "export"];

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
    output_resolution: "1280x720",
    ...overrides,
  };
}

function realShot(overrides = {}) {
  return {
    id: "shot1",
    title: "镜头 1",
    status: "video_ready",
    shot_index: 1,
    current_version_id: "v1",
    versions: [
      {
        id: "v1",
        version_number: 1,
        first_frame_path: "/a.jpg",
        last_frame_path: "/b.jpg",
        video_path: "/v.mp4",
        provider: "seedance",
        model: "doubao-seedance-1-0-pro",
      },
    ],
    ...overrides,
  };
}

{
  assert(STAGES.length === 8, "应始终显示 8 个阶段");
  assert(
    STAGES.map((stage) => stage.id).join(",") === STAGE_IDS.join(","),
    "8 个阶段顺序应为文本理解到导出与交付"
  );
  console.log("PASS: 8 个阶段顺序固定");
}

{
  const workflow = computeWorkflow(baseProject());
  assert(workflow.executionStage === "text", "created 项目执行阶段应为 text");
  assert(stageById(workflow, "storyline").state === STAGE_STATE.SKIPPED, "短文本故事线应跳过");
  assert(stageById(workflow, "text").state === STAGE_STATE.NOT_STARTED, "文本理解应未开始");
  assert(stageById(workflow, "bible").state === STAGE_STATE.NOT_STARTED, "Bible 应未开始");
  assert(stageById(workflow, "export").state === STAGE_STATE.NOT_STARTED, "导出应未开始");
  assert(stageById(workflow, "export").viewable === true, "未开始阶段仍可查看");
  assert(workflow.executionStage !== "viewStage", "视图模型不持有 viewStage");
  console.log("PASS: 新建项目执行阶段与跳过态");
}

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
  assert(stageById(workflow, "storyline").awaitingReview === true, "等待审核标记应为 true");
  assert(stageById(workflow, "storyline").tone === "review", "等待审核用琥珀色调");
  console.log("PASS: 中等文本故事线等待审核");
}

{
  const shortReview = computeWorkflow(
    baseProject({
      status: "awaiting_scope_review",
      adaptation_options: [{ id: "opt1", title: "方案一", selected: 0 }],
    })
  );
  assert(shortReview.executionStage === "text", "短文本等待范围审核时执行阶段为 text");
  assert(stageById(shortReview, "text").state === STAGE_STATE.AWAITING_REVIEW, "文本理解应等待审核");
  assert(stageById(shortReview, "storyline").state === STAGE_STATE.SKIPPED, "短文本故事线仍跳过");
  const mediumReview = computeWorkflow(
    baseProject({
      text_scale: "medium",
      status: "awaiting_scope_review",
      storylines: [{ id: "line1", title: "线一", selected: 1 }],
      adaptation_options: [{ id: "opt1", title: "方案一", selected: 0 }],
    })
  );
  assert(mediumReview.executionStage === "storyline", "中等文本改编方案并入故事线选择");
  assert(stageById(mediumReview, "text").state === STAGE_STATE.COMPLETED, "文本理解应已完成");
  assert(stageById(mediumReview, "storyline").state === STAGE_STATE.AWAITING_REVIEW, "故事线应等待审核");
  console.log("PASS: 改编方案并入文本理解或故事线选择");
}

{
  const staleShot = realShot();
  const workflow = computeWorkflow(
    baseProject({
      status: "awaiting_bible_review",
      story_bible: { review_status: "draft", adaptation_summary: "x" },
      storyboard_drafts: [],
      shots: [staleShot],
      assets: [
        { id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" },
        { id: "fv", type: "final-video", file_path: "/final.mp4", created_at: "2026-08-30T01:00:00Z" },
      ],
    })
  );
  assert(workflow.executionStage === "bible", "Bible 重做后执行阶段回退到 bible");
  assert(stageById(workflow, "keyframes").state === STAGE_STATE.INVALIDATED, "关键帧应标记已失效");
  assert(stageById(workflow, "video").state === STAGE_STATE.INVALIDATED, "镜头视频应标记已失效");
  assert(stageById(workflow, "assembly").state === STAGE_STATE.INVALIDATED, "成片应标记已失效");
  assert(stageById(workflow, "export").state === STAGE_STATE.INVALIDATED, "导出应标记已失效");
  assert(stageById(workflow, "storyboard").state === STAGE_STATE.NOT_STARTED, "分镜被清除后应未开始");
  const historical = stageAssets(
    baseProject({
      shots: [staleShot],
      assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
    }),
    "video"
  );
  assert(historical.length === 1, "历史镜头仍可派生");
  console.log("PASS: 上游重做后下游标记失效且历史保留");
}

{
  const shot = realShot({
    status: "keyframes_ready",
    versions: [{ id: "v1", version_number: 1, first_frame_path: "/a.jpg", last_frame_path: "/b.jpg", video_path: null }],
  });
  const workflow = computeWorkflow(baseProject({ status: "production_ready", shots: [shot] }));
  assert(["keyframes", "video"].includes(workflow.executionStage), "制作期 frontier 应在关键帧或视频");
  assert(stageById(workflow, "storyboard").state === STAGE_STATE.COMPLETED, "分镜确认后应已完成");
  console.log("PASS: 制作期 frontier 推导");
}

{
  const project = baseProject({
    status: "production_ready",
    shots: [realShot({ versions: [{ id: "v1", version_number: 2, video_path: "/v.mp4", provider: "seedance", model: "doubao-seedance-1-0-pro", video_mode: "i2v", first_frame_path: "/a.jpg", last_frame_path: "/b.jpg" }] })],
    assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
  });
  const cards = stageAssets(project, "video");
  assert(cards.length === 1, "视频阶段应有一个镜头卡片");
  assert(cards[0].meta.Provider === "seedance", "卡片应带 Provider");
  assert(cards[0].meta["版本"] === "v2", "卡片应带版本号");
  assert(cards[0].statusLabel === "视频就绪", "真实视频应显示就绪");
  console.log("PASS: 视频阶段素材派生");
}

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

{
  Object.values(STAGE_STATE).forEach((value) => {
    assert(stageStateLabel(value), `状态 ${value} 应有中文标签`);
  });
  const labels = ["未开始", "处理中", "等待审核", "已完成", "已修改", "已失效", "失败", "跳过"];
  labels.forEach((label) => {
    assert(Object.values(STAGE_STATE).some((value) => stageStateLabel(value) === label), `应能显示「${label}」`);
  });
  console.log("PASS: 阶段状态中文标签完整");
}

{
  const processing = computeWorkflow(
    baseProject({
      status: "running",
      jobs: [{ id: "j1", type: "adaptation_workflow", status: "running", progress: 20 }],
      active_jobs: [{ id: "j1", type: "adaptation_workflow", status: "running", progress: 20 }],
    })
  );
  assert(stageById(processing, "text").state === STAGE_STATE.PROCESSING, "运行中应显示处理中");
  assert(stageById(processing, "text").executing === true, "处理中阶段 executing 应为 true");
  assert(stageById(processing, "text").jobCount === 1, "文本阶段应统计相关任务数");

  const failed = computeWorkflow(
    baseProject({
      status: "failed",
      shots: [realShot({ status: "video_failed" })],
      assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
    })
  );
  assert(failed.executionStage === "video", "镜头失败时执行阶段在视频");
  assert(stageById(failed, "video").state === STAGE_STATE.FAILED, "视频阶段应显示失败");

  const storyboardEdited = computeWorkflow(
    baseProject({
      status: "production_ready",
      storyboard_drafts: [{ id: "d1", title: "镜 1", review_status: "edited", source_type: "human_edit" }],
      shots: [realShot({ status: "keyframes_ready", versions: [{ id: "v1", version_number: 1, first_frame_path: "/a.jpg", last_frame_path: "/b.jpg" }] })],
    })
  );
  assert(stageById(storyboardEdited, "storyboard").state === STAGE_STATE.MODIFIED, "人工改过的分镜应显示已修改");
  console.log("PASS: 处理中 / 失败 / 已修改状态可显示");
}

{
  const current = { id: "v2", version_number: 2, video_path: "/v2.mp4", first_frame_path: "/a2.jpg", last_frame_path: "/b2.jpg" };
  const history = { id: "v1", version_number: 1, video_path: "/v1.mp4", first_frame_path: "/a1.jpg", last_frame_path: "/b1.jpg" };
  const project = baseProject({
    status: "video_ready",
    shots: [
      {
        id: "shot1",
        title: "镜头 1",
        status: "video_ready",
        current_version_id: "v2",
        versions: [current, history],
      },
    ],
    assets: [
      { id: "av2", type: "video", file_path: "/v2.mp4", embedding_ref: "provider:seedance" },
      { id: "av1", type: "video", file_path: "/v1.mp4", embedding_ref: "provider:seedance" },
    ],
  });
  const cards = stageAssets(project, "video");
  assert(cards[0].meta["版本"] === "v2", "当前卡片应使用当前版本而不是历史版本");
  assert(executionStageId(project) === "assembly" || executionStageId(project) === "export", "当前有效视频推进 frontier，不受历史版本干扰");
  console.log("PASS: 历史版本不会被误判为当前版本");
}

{
  const ready = computeWorkflow(
    baseProject({
      status: "completed",
      shots: [realShot()],
      assets: [
        { id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" },
        { id: "fv", type: "final-video", file_path: "/final.mp4", created_at: "2026-08-30T02:00:00Z" },
      ],
      assembly_stale: 0,
    })
  );
  assert(ready.executionStage === "export", "有效成片后执行阶段应为导出与交付");
  assert(stageById(ready, "assembly").state === STAGE_STATE.COMPLETED, "成片合成应已完成");
  assert(stageById(ready, "export").state === STAGE_STATE.COMPLETED, "导出应已完成");

  const stale = computeWorkflow(
    baseProject({
      status: "completed",
      shots: [realShot()],
      assets: [
        { id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" },
        { id: "fv", type: "final-video", file_path: "/final.mp4", created_at: "2026-08-30T02:00:00Z" },
      ],
      assembly_stale: 1,
    })
  );
  assert(stale.executionStage === "assembly", "成片过期后执行阶段回到成片合成");
  assert(stageById(stale, "assembly").state === STAGE_STATE.MODIFIED, "过期成片显示已修改");
  console.log("PASS: 导出阶段与成片过期推导");
}

{
  const a = computeWorkflow(baseProject({ id: "project_a", status: "awaiting_bible_review", story_bible: { review_status: "draft" } }));
  const b = computeWorkflow(baseProject({ id: "project_b", status: "created" }));
  assert(a.executionStage === "bible" && b.executionStage === "text", "不同项目各自推导执行阶段");
  assert(resolveStageId("adaptation", baseProject()) === "text", "短文本旧 adaptation 映射到 text");
  assert(resolveStageId("adaptation", baseProject({ text_scale: "medium" })) === "storyline", "中等文本旧 adaptation 映射到 storyline");
  console.log("PASS: 项目切换后状态不串项目，旧阶段 id 可解析");
}

console.log("ALL WORKFLOW VIEW MODEL TESTS PASSED");
