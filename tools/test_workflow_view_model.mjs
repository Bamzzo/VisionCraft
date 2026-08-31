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
      shots: [
        realShot({
          status: "video_failed",
          versions: [{ id: "v1", version_number: 1, first_frame_path: "/a.jpg", last_frame_path: "/b.jpg", video_path: null }],
        }),
      ],
    })
  );
  assert(failed.executionStage === "video", "当前版本视频失败时执行阶段在视频");
  assert(stageById(failed, "video").state === STAGE_STATE.FAILED, "无有效当前视频时应显示失败");

  const failedButCurrentReady = computeWorkflow(
    baseProject({
      status: "failed",
      shots: [realShot({ status: "video_invalid" })],
      assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
    })
  );
  assert(failedButCurrentReady.executionStage !== "video" || stageById(failedButCurrentReady, "video").state !== STAGE_STATE.FAILED, "当前版本已有真实视频时，video_invalid 不得覆盖完成态");

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

{
  const workflow = computeWorkflow(
    baseProject({
      status: "production_ready",
      stale_stages: ["bible", "storyboard"],
      story_bible: { review_status: "stale" },
      storyboard_drafts: [{ id: "sb1", shot_index: 1, title: "镜 1" }],
      shots: [realShot()],
      assets: [{ id: "av", type: "video", file_path: "/v.mp4", embedding_ref: "provider:seedance" }],
    })
  );
  assert(stageById(workflow, "bible").state === STAGE_STATE.INVALIDATED, "阶段模型修改后 Story Bible 应显示失效");
  assert(stageById(workflow, "storyboard").state === STAGE_STATE.INVALIDATED, "阶段模型修改后分镜应显示失效");
  assert(stageById(workflow, "video").state !== STAGE_STATE.INVALIDATED, "镜头视频不应因文本模型修改而失效");
  console.log("PASS: 阶段模型修改只失效必要下游");
}

function i2vShot(index, extras = {}) {
  const hasVideo = Boolean(extras.video_path);
  return {
    id: extras.id || `shot${index}`,
    title: `镜头 ${index}`,
    status: extras.status || (hasVideo ? "video_ready" : "production_ready"),
    shot_index: index,
    current_version_id: extras.current_version_id || `v${index}`,
    versions: extras.versions || [
      {
        id: extras.current_version_id || `v${index}`,
        version_number: extras.version_number || 1,
        video_mode: "i2v",
        first_frame_path: extras.first_frame_path !== undefined ? extras.first_frame_path : `/a${index}.jpg`,
        last_frame_path: extras.last_frame_path !== undefined ? extras.last_frame_path : null,
        video_path: extras.video_path !== undefined ? extras.video_path : null,
        provider: extras.provider || "minimax",
        model: extras.model || "MiniMax-Hailuo-02-超长模型名用于检查换行",
      },
    ],
  };
}

function videoAsset(index, path) {
  return { id: `av${index}`, type: "video", file_path: path || `/v${index}.mp4`, embedding_ref: "provider:minimax:local-fixture" };
}

{
  const empty = computeWorkflow(baseProject({ source_text: "", shots: [], jobs: [] }));
  assert(empty.executionStage === "text", "空项目执行阶段为文本理解");
  assert(stageById(empty, "keyframes").state !== STAGE_STATE.PROCESSING, "空项目关键帧不得显示处理中");
  assert(stageById(empty, "video").state === STAGE_STATE.NOT_STARTED, "空项目镜头视频未开始");
  assert(stageById(empty, "export").canExecute === false, "空项目导出不可执行");
  assert(stageById(empty, "export").accessLabel.includes("不可执行"), "空项目导出应标明不可执行");
  console.log("PASS: 空项目阶段状态");
}

{
  const workflow = computeWorkflow(
    baseProject({
      status: "awaiting_scope_review",
      adaptation_options: [{ id: "opt1", title: "方案一", selected: 0 }],
    })
  );
  assert(workflow.executionStage === "text", "改编等待审核时执行阶段在文本理解");
  assert(stageById(workflow, "text").state === STAGE_STATE.AWAITING_REVIEW, "改编等待审核");
  assert(stageById(workflow, "bible").state === STAGE_STATE.NOT_STARTED, "Bible 仍未开始");
  console.log("PASS: 改编等待审核");
}

{
  const workflow = computeWorkflow(
    baseProject({
      status: "awaiting_storyboard_review",
      story_bible: { review_status: "confirmed", adaptation_summary: "已确认" },
      storyboard_drafts: [{ id: "d1", title: "镜 1", review_status: "draft" }],
    })
  );
  assert(stageById(workflow, "bible").state === STAGE_STATE.COMPLETED, "Story Bible 已确认应已完成");
  assert(workflow.executionStage === "storyboard", "确认 Bible 后执行阶段在分镜");
  console.log("PASS: Story Bible 已确认");
}

{
  const workflow = computeWorkflow(
    baseProject({
      status: "production_ready",
      story_bible: { review_status: "confirmed" },
      storyboard_drafts: [],
      shots: [i2vShot(1, { first_frame_path: null })],
    })
  );
  assert(stageById(workflow, "storyboard").state === STAGE_STATE.COMPLETED, "分镜已确认应已完成");
  assert(workflow.executionStage === "keyframes", "分镜确认后若无关键帧则停在关键帧");
  console.log("PASS: 分镜已确认");
}

{
  const workflow = computeWorkflow(
    baseProject({
      status: "production_ready",
      shots: [1, 2, 3, 4, 5].map((index) => i2vShot(index, { first_frame_path: null })),
    })
  );
  assert(workflow.executionStage === "keyframes", "仅有分镜、无关键帧任务时执行阶段在关键帧");
  assert(stageById(workflow, "keyframes").state === STAGE_STATE.NOT_STARTED, "没有关键帧任务时不得显示处理中");
  assert(stageById(workflow, "keyframes").stateLabel === "未开始", "关键帧中文状态应为未开始");
  assert(stageById(workflow, "video").state === STAGE_STATE.NOT_STARTED, "下游视频应为未开始而不是处理中");
  console.log("PASS: 关键帧未开始（无任务不得显示处理中）");
}

{
  const processing = computeWorkflow(
    baseProject({
      status: "production_ready",
      shots: [i2vShot(1, { first_frame_path: null })],
      jobs: [{ id: "j1", type: "keyframe_redraw", status: "running", progress: 30 }],
      active_jobs: [{ id: "j1", type: "keyframe_redraw", status: "running", progress: 30 }],
    })
  );
  assert(stageById(processing, "keyframes").state === STAGE_STATE.PROCESSING, "有关键帧任务时才显示处理中");
  assert(stageById(processing, "keyframes").jobCount === 1, "关键帧应统计任务数");
  console.log("PASS: 关键帧处理中");
}

{
  const shots = [1, 2, 3, 4, 5].map((index) =>
    i2vShot(index, {
      status: index <= 2 ? "video_running" : "production_ready",
      video_path: index <= 2 ? `/v${index}.mp4` : null,
    })
  );
  shots[0].status = "video_running";
  shots[1].status = "video_waiting_remote";
  const workflow = computeWorkflow(
    baseProject({
      status: "production_ready",
      shots,
      assets: [videoAsset(1), videoAsset(2)],
      jobs: [{ id: "j1", type: "video_generation", status: "waiting_remote", progress: 40, shot_id: "shot2" }],
      active_jobs: [{ id: "j1", type: "video_generation", status: "waiting_remote", progress: 40, shot_id: "shot2" }],
    })
  );
  assert(workflow.executionStage === "video", "部分镜头完成后仍停在镜头视频");
  assert(stageById(workflow, "video").state === STAGE_STATE.PROCESSING, "未完成镜头等待远程时应处理中");
  assert(stageById(workflow, "video").summary.includes("2/5"), "部分完成应显示 2/5");
  assert(stageById(workflow, "keyframes").state === STAGE_STATE.COMPLETED, "I2V 仅首帧且已挂帧后关键帧应已完成");
  console.log("PASS: 镜头视频处理中 / 等待远程");
}

{
  const current = {
    id: "v2",
    version_number: 2,
    video_mode: "i2v",
    first_frame_path: "/a2.jpg",
    last_frame_path: null,
    video_path: "/v2.mp4",
    provider: "minimax",
    model: "MiniMax-Hailuo-02",
  };
  const history = {
    id: "v1",
    version_number: 1,
    video_mode: "i2v",
    first_frame_path: "/a1.jpg",
    video_path: "/v1.mp4",
    provider: "minimax",
    model: "old-model",
  };
  const project = baseProject({
    status: "production_ready",
    shots: [
      {
        id: "shot1",
        title: "镜头 1",
        status: "video_invalid",
        current_version_id: "v2",
        versions: [current, history],
      },
    ],
    assets: [
      { id: "av2", type: "video", file_path: "/v2.mp4", embedding_ref: "provider:minimax" },
      { id: "av1", type: "video", file_path: "/v1.mp4", embedding_ref: "provider:minimax" },
    ],
  });
  const workflow = computeWorkflow(project);
  assert(stageById(workflow, "video").state !== STAGE_STATE.INVALIDATED, "历史版本失效不能覆盖当前版本已完成");
  assert(stageById(workflow, "video").stateLabel !== "已失效", "当前有效视频不得显示镜头视频已失效");
  const cards = stageAssets(project, "video");
  assert(cards[0].statusLabel === "视频就绪", "当前版本真实视频应显示就绪");
  assert(cards[0].meta["版本"] === "v2", "卡片使用当前版本");
  console.log("PASS: 历史版本已失效但当前版本有效");
}

{
  const shots = [1, 2, 3, 4, 5].map((index) => i2vShot(index, { video_path: `/v${index}.mp4` }));
  const assets = [1, 2, 3, 4, 5].map((index) => videoAsset(index));
  const workflow = computeWorkflow(baseProject({ status: "video_ready", shots, assets }));
  assert(workflow.executionStage === "assembly", "五镜全部完成后执行阶段到成片合成");
  assert(stageById(workflow, "keyframes").state === STAGE_STATE.COMPLETED, "I2V 仅首帧完成后关键帧已完成");
  assert(stageById(workflow, "keyframes").state !== STAGE_STATE.PROCESSING, "成片前关键帧不得处理中");
  assert(stageById(workflow, "video").state === STAGE_STATE.COMPLETED, "五镜视频已完成");
  assert(stageById(workflow, "video").state !== STAGE_STATE.INVALIDATED, "当前版本有效时视频不得已失效");
  assert(stageById(workflow, "video").summary.includes("5/5"), "应显示 5/5 镜头视频");
  console.log("PASS: 5 个镜头全部完成");
}

{
  const shots = [1, 2, 3, 4, 5].map((index) => i2vShot(index, { video_path: `/v${index}.mp4` }));
  const assets = [1, 2, 3, 4, 5].map((index) => videoAsset(index));
  const running = computeWorkflow(
    baseProject({
      status: "production_ready",
      shots,
      assets,
      jobs: [{ id: "j1", type: "sequence_assembly", status: "running", progress: 55 }],
      active_jobs: [{ id: "j1", type: "sequence_assembly", status: "running", progress: 55 }],
    })
  );
  assert(running.executionStage === "assembly", "成片合成中执行阶段在成片");
  assert(stageById(running, "assembly").state === STAGE_STATE.PROCESSING, "成片合成中");
  assert(stageById(running, "export").state === STAGE_STATE.NOT_STARTED, "合成进行中导出不得处理中");
  assert(stageById(running, "keyframes").state === STAGE_STATE.COMPLETED, "合成中关键帧仍为已完成");
  console.log("PASS: 成片合成中");
}

{
  const shots = [1, 2, 3, 4, 5].map((index) => i2vShot(index, { video_path: `/v${index}.mp4` }));
  const assets = [
    ...[1, 2, 3, 4, 5].map((index) => videoAsset(index)),
    { id: "fv", type: "final-video", file_path: "/final.mp4", created_at: "2026-08-31T02:00:00Z" },
  ];
  const complete = computeWorkflow(baseProject({ status: "completed", shots, assets, assembly_stale: 0 }));
  assert(complete.executionStage === "export", "成片完成后执行阶段为导出与交付");
  assert(stageById(complete, "keyframes").state === STAGE_STATE.COMPLETED, "成片完成后关键帧已完成");
  assert(stageById(complete, "keyframes").stateLabel !== "处理中", "成片完成后不得显示关键帧处理中");
  assert(stageById(complete, "video").state === STAGE_STATE.COMPLETED, "成片完成后镜头视频已完成");
  assert(stageById(complete, "video").stateLabel !== "已失效", "成片完成后不得显示镜头视频已失效");
  assert(stageById(complete, "assembly").state === STAGE_STATE.COMPLETED, "成片合成已完成");
  assert(stageById(complete, "export").state === STAGE_STATE.COMPLETED, "导出已完成");
  complete.stages.forEach((stage) => {
    assert(stage.viewable === true, `${stage.label} 应可查看`);
    assert(stage.accessLabel, `${stage.label} 应标明可否执行`);
    assert(typeof stage.assetCount === "number", `${stage.label} 应有素材计数`);
    assert(typeof stage.jobCount === "number", `${stage.label} 应有任务计数`);
    assert(stage.prerequisite, `${stage.label} 应有前置条件提示`);
    assert(stage.stateLabel, `${stage.label} 应有中文状态`);
  });

  const stale = computeWorkflow(baseProject({ status: "completed", shots, assets, assembly_stale: 1 }));
  assert(stale.executionStage === "assembly", "成片过期后回到成片合成");
  assert(stageById(stale, "assembly").state === STAGE_STATE.MODIFIED, "过期成片显示已修改");
  assert(stageById(stale, "export").state !== STAGE_STATE.COMPLETED, "过期后导出不再是已完成");
  console.log("PASS: 成片合成完成 / 过期");
}

{
  const a = computeWorkflow(baseProject({ id: "project_a", status: "completed", shots: [i2vShot(1, { video_path: "/v1.mp4" })], assets: [videoAsset(1), { id: "fv", type: "final-video", file_path: "/final.mp4" }] }));
  const b = computeWorkflow(baseProject({ id: "project_b" }));
  assert(a.executionStage === "export" && b.executionStage === "text", "项目切换后各自恢复执行阶段");
  assert(stageById(b, "video").state === STAGE_STATE.NOT_STARTED, "新项目不得继承旧项目视频完成态");
  console.log("PASS: 项目切换隔离（视图模型纯函数）");
}

{
  const snapshot = baseProject({
    status: "completed",
    shots: [1, 2, 3, 4, 5].map((index) => i2vShot(index, { video_path: `/v${index}.mp4` })),
    assets: [...[1, 2, 3, 4, 5].map((index) => videoAsset(index)), { id: "fv", type: "final-video", file_path: "/final.mp4" }],
  });
  const first = computeWorkflow(snapshot);
  const refreshed = computeWorkflow(JSON.parse(JSON.stringify(snapshot)));
  assert(first.executionStage === refreshed.executionStage, "刷新后执行阶段应能从后端快照恢复");
  assert(stageById(first, "video").state === stageById(refreshed, "video").state, "刷新后视频状态一致");
  console.log("PASS: 刷新恢复");
}

{
  const workflow = computeWorkflow(
    baseProject({
      status: "created",
      shots: [],
    })
  );
  assert(stageById(workflow, "assembly").canExecute === false, "未开始的成片阶段不可执行");
  assert(stageById(workflow, "export").canExecute === false, "未开始的导出阶段不可执行");
  assert(stageById(workflow, "video").canExecute === false, "未开始的视频阶段不可执行");
  assert(stageById(workflow, "text").canExecute === true, "当前执行阶段可执行");
  console.log("PASS: 未开始阶段不能执行非法操作");
}

{
  const longName = computeWorkflow(
    baseProject({
      title: "QA甲-超长中文标题用于检查换行与错位-影视创作工作台".repeat(3),
      status: "video_ready",
      shots: [i2vShot(1, { video_path: "/v1.mp4", model: "doubao-seedance-1-0-pro-fast-超长模型标识" })],
      assets: [videoAsset(1)],
    })
  );
  assert(longName.stages.length === 8, "长标题项目仍显示 8 阶段");
  const cards = stageAssets(
    baseProject({
      shots: [i2vShot(1, { video_path: "/v1.mp4", model: "doubao-seedance-1-0-pro-fast-超长模型标识" })],
      assets: [videoAsset(1)],
    }),
    "video"
  );
  assert(cards[0].meta["模型"].includes("超长模型"), "长模型名应完整进入卡片而不是被丢弃");
  console.log("PASS: 长中文与长模型名进入视图模型");
}

console.log("ALL WORKFLOW VIEW MODEL TESTS PASSED");
