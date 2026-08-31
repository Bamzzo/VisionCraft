# 工作流暂停与恢复（P8-A）

本切片把前端会话内的“暂停/继续”收成真实后端状态和检查点。不调用真实 API，费用 0 元。

查看阶段与执行阶段仍然分离：

```text
executionStage：后端真实执行到的阶段
viewStage：用户当前查看的阶段
selectedAsset：当前选中素材
```

点击右侧 8 阶段导航只改变 `viewStage`，不会改变项目状态、checkpoint 或任务。

## 1. 项目执行状态

```text
created
running
awaiting_scope_review
awaiting_bible_review
awaiting_storyboard_review
production_ready
failed
```

中等文本额外保留 `awaiting_storyline_review`。旧监制路径保留 `review_pending`。任务状态仍为：

```text
queued → running → paused | completed | failed | waiting_remote
```

## 2. Checkpoint 数据结构

表：`workflow_checkpoints`。同一项目同一审核节点的 `paused` 行会更新，不插入第二行；切到下一审核节点时，旧 `paused` 行变为 `superseded`。

公开字段：

```text
id
project_id
job_id
node            scope_review | bible_review | storyboard_review | storyline_review | quality_gate
status          paused | completed | superseded
stage
option_id / scope_id / storyline_id / version_id
input_summary   不超过 200 字
pause_reason
created_at / updated_at
```

禁止入库：API Key、Authorization、完整 Prompt、Data URL、Base64、签名 URL。保存前会脱敏；若仍像密钥则拒绝写入。

## 3. 审核节点

```text
自动改编 → awaiting_scope_review（暂停）
用户选择并确认范围 → 生成 Story Bible → awaiting_bible_review（暂停）
确认 Bible → 生成分镜 → awaiting_storyboard_review（暂停）
确认分镜 → production_ready
失败 → failed，保留有效 checkpoint
```

暂停只允许发生在上述审核节点。不会中断已经提交给云端的 MiniMax 任务。

## 4. 接口

复用并补齐：

```text
POST /api/projects/{id}/run
POST /api/projects/{id}/pause
POST /api/projects/{id}/resume
GET  /api/projects/{id}/checkpoints
POST /api/projects/{id}/checkpoints/{checkpoint_id}/resume
```

`GET /api/projects/{id}` 附带 `workflow` 与当前公开 `checkpoint`。前端“继续执行”必须调用 resume 接口，不能只改本地变量。

## 5. 幂等

| 操作 | 结果 |
|---|---|
| 审核中再次 `/run` | 复用，不新建改编任务 |
| 重复确认同一已完成节点 | 返回当前状态，不倒退，不重复生成 |
| 用已替代的旧 checkpoint 恢复 | 若下游已生成则 reused，停在当前审核节点 |
| 重复点击继续 | 由后端按当前 node 判断；已完成则 reused |
| 上游重做 | 只失效必要下游；旧版本、旧任务、旧素材、旧成片保留 |
| `waiting_remote` | 只回查原来的 `remote_task_id`；暂停/恢复不得重新提交视频 |

恢复必须校验项目 ID、任务 ID（若提供）、checkpoint ID 和当前状态。错误消息为中文可执行提示，不含密钥、完整路径、请求头或签名 URL。

## 6. waiting_remote

若项目存在 `waiting_remote` 任务或未完成的 `video_tasks`，暂停和恢复都会拒绝，避免误当作“重新提交视频”。继续查询仍走既有视频刷新接口，只使用原来的 `remote_task_id`。

## 7. 前端

三栏工作台与右侧 8 阶段导航显示：当前执行阶段、暂停原因、当前审核节点、可执行确认、继续执行、失败后的检查点恢复。已确认阶段只读，修改请走重做。任务中心通过 SSE / 低频轮询显示暂停、恢复和失败事件，无需手动刷新。项目切换会重置本地查看状态，不会把甲项目的暂停状态带到乙项目。

本阶段没有真实 API 调用。
