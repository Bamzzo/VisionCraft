# VisionCraft 优化交付报告

## 交付概览

本次改造在 `production-hardening` 分支完成，未合并 `main`，未改写 `main` 历史。提交按任务拆分：

| commit | 任务 | 内容 |
| --- | --- | --- |
| `b08b4de` | task 3 | per-project 防重入锁、孤儿 job 清理、409 busy 响应 |
| `c6e0f77` | task 1 | Embedding Provider 抽象、SiliconFlow fallback、Chroma collection 隔离 |
| `a0ae1bc` | task 4 | 检索评测 harness、synthetic eval project、评测用例 |
| `b8198e1` | task 4 | memory label dump、检索评测结果、验收日志 |
| `597371b` | task 2 | Pydantic v2 story-plan schema、修复重试、smoke 脚本 |
| `276fb6e` | task 5 | 中文 README、`.env.example` 注释、文档验收 |
| `f19f237` | eval | 检索评测增加按用例类别分组输出 |

本次交付物：

- `docs/production_hardening_log.md`
- `docs/VisionCraft_优化交付报告.md`
- `eval/retrieval_eval_set.json`
- `eval/memory_labels_dump.md`
- `eval/results.md`
- `tools/story_plan_validation_smoke.py`
- `production-hardening` 本地分支

## 基线记录

### 环境与依赖

- 基线分支：`main`
- 工作分支：`production-hardening`
- Pydantic：`2.12.5`
- Python 测试环境：未安装 pytest

基线命令与结论：

```powershell
python -c "import pydantic; print('pydantic', pydantic.__version__)"
python -m pip show pytest
```

```text
pydantic 2.12.5
WARNING: Package(s) not found: pytest
```

因此本次结构化校验使用 Pydantic v2 的 `model_validate`。回归保护采用 Python AST 检查、前端 `node --check` 和任务专项 smoke 脚本。

### 改造前语法检查

```powershell
python -c "import ast; from pathlib import Path; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in Path('backend').rglob('*.py')]; print('backend python syntax ok')"
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
```

```text
backend python syntax ok
node --check 无输出，表示通过
```

### 改造前 hash 检索样例

基线检索可运行，但历史项目文本存在本地显示编码噪声。改造后使用 `eval_project_001` 构造可复现样例，避免用户项目和历史数据影响评测。

## 任务 3：per-project 防重入锁

### 改动内容

- 在 `backend/services/job_service.py` 增加进程内 `{project_id: threading.Lock}`。
- 用 `create_project_job_guarded` 包裹“查找活跃 job + 创建新 job”的临界区。
- `/run` 与 `/retry` 阻塞 `queued/running/paused` 状态，提示用户等待或恢复暂停流程。
- `/resume` 只阻塞 `queued/running`，允许 paused checkpoint 继续执行。
- FastAPI startup 调用 `mark_orphaned_jobs_on_startup`，将重启前残留的 `queued/running` job 标为 failed。
- 前端把 HTTP 409 busy 响应转成清晰中文提示。

### 验收结果

```text
orphan_after job_09566f3992 failed orphaned on restart
run_A_first 200 {'job_id': 'job_95a6da5f09', 'status': 'queued'}
run_A_second 409 {'detail': 'project busy; resume paused workflow or wait for the active job', ...}
run_B_while_A_active 200 {'job_id': 'job_3b6ee760d2', 'status': 'queued'}
C_paused True review_pending
run_C_paused_again 409 {... 'active_job_status': 'paused'}
resume_C 200 {'job_id': 'job_70d388ec06', 'status': 'resuming'}
```

### 边界

该锁适用于单进程 FastAPI BackgroundTasks。多 worker、分布式部署、跨机器运行需要迁移到 Redis lock、数据库行锁或任务队列幂等机制。

## 任务 1：Embedding Provider 抽象

### 改动内容

- 新增 `EmbeddingProvider` 协议。
- 保留本地 `HashEmbeddingProvider`：bigram + blake2b，384 维。
- 新增 `SiliconFlowEmbeddingProvider`：OpenAI-compatible `/embeddings`，默认 `BAAI/bge-m3`，1024 维。
- Chroma collection 按 provider 隔离：
  - `visioncraft_memory_hash`
  - `visioncraft_memory_sf_bge-m3`
- 写入和查询均显式传入 `embeddings=` / `query_embeddings=`，避免 Chroma 默认 embedding 混入。
- SiliconFlow key 缺失、余额不足或网络失败时降级到 hash。
- hybrid rerank 支持环境变量权重。

### 验收结果

hash 模式：

```text
provider hash 384 visioncraft_memory_hash
collection visioncraft_memory_hash
```

SiliconFlow 余额不足 fallback：

```text
resolved_provider siliconflow:BAAI/bge-m3 1024 visioncraft_memory_sf_bge-m3
Falling back to hash embedding provider: SiliconFlow embedding HTTP 403: {"code":30001,"message":"Sorry, your account balance is insufficient","data":null}
used_provider hash 384
fallback_used True
```

### 边界

SiliconFlow live 语义质量已在充值后补跑成功。fallback 记录仍保留在 `eval/results.md` 中，用于说明余额不足时系统会降级到 hash provider。

## 任务 4：检索评测

### 改动内容

- 新建 `eval_project_001` synthetic 项目。
- 新增 `eval/dump_memory_labels.py`，输出真实索引标签。
- 新增 `eval/retrieval_eval_set.json`，共 28 条用例：
  - 10 条直接实体匹配
  - 10 条同义改写
  - 8 条跨镜头追问
- 新增 `eval/run_retrieval_eval.py`，支持 `provider`、`mode`、`k` 参数。
- 每次评测写入时间戳、commit hash、provider、mode、k、用例数、recall@k、MRR。

### memory dump

```powershell
$env:EMBEDDING_PROVIDER='hash'
python eval\dump_memory_labels.py
```

```text
memory_rows 16
```

### 评测结果

| provider | active_provider | mode | k | cases | recall@k | MRR | status |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| hash | hash | hybrid | 2 | 28 | 0.4732 | 0.6071 | OK |
| hash | hash | hybrid | 5 | 28 | 0.8363 | 0.6619 | OK |
| hash | hash | vector_only | 5 | 28 | 0.7381 | 0.6452 | OK |
| hash | hash | lexical_only | 5 | 28 | 0.8363 | 0.6619 | OK |
| siliconflow | hash | vector_only | 2 | 28 | 0.4792 | 0.5893 | PENDING_LIVE_KEY |
| siliconflow | siliconflow:BAAI/bge-m3 | vector_only | 2 | 28 | 0.7738 | 0.8929 | OK |
| siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 2 | 28 | 0.7708 | 0.8750 | OK |
| siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 5 | 28 | 0.9494 | 0.8869 | OK |

充值前的 SiliconFlow 行使用了 fallback hash，状态为 `PENDING_LIVE_KEY`。充值后补跑成功，`active_provider` 为 `siliconflow:BAAI/bge-m3`，status 为 `OK`。

分组评测结果：

| provider | mode | k | group | recall@k | MRR | hit_rate@k |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| hash | hybrid | 5 | direct_match | 0.8000 | 0.5283 | 0.8000 |
| hash | hybrid | 5 | semantic_rewrite | 0.8500 | 0.8333 | 1.0000 |
| hash | hybrid | 5 | cross_shot | 0.8646 | 0.6146 | 1.0000 |
| siliconflow | hybrid | 5 | direct_match | 1.0000 | 0.9333 | 1.0000 |
| siliconflow | hybrid | 5 | semantic_rewrite | 1.0000 | 0.9500 | 1.0000 |
| siliconflow | hybrid | 5 | cross_shot | 0.8229 | 0.7500 | 1.0000 |

关键发现：

- hash hybrid@5 与 hash lexical_only@5 的总分完全相同，均为 recall@5 `0.8363`、MRR `0.6619`，说明 hash 向量在当前权重下没有给排序带来增益。
- hash vector_only@5 只有 recall@5 `0.7381`，低于字面匹配路径。
- SiliconFlow BGE-M3 hybrid@5 将总 recall@5 提升到 `0.9494`，MRR 提升到 `0.8869`。
- 同义改写类从 hash hybrid@5 的 `0.8500` 提升到 SiliconFlow hybrid@5 的 `1.0000`。
- k=2 用例中部分 expected_labels 有 2-4 个，recall@2 存在结构性天花板，因此报告和面试中应同时引用 hit_rate@k。

## 任务 2：Pydantic 校验重试

### 改动内容

- 新增 `StoryPlanModel`、`StoryCharacterModel`、`StorySceneModel`、`StoryShotModel`。
- LLM 返回 JSON 后先走 Pydantic v2 `model_validate`。
- 校验失败时，把错误摘要压缩到 800 字以内，追加给下一轮修复提示。
- 默认 `PLAN_VALIDATION_MAX_RETRIES=2`，总尝试次数为 3。
- 镜头数量必须等于 `shot_count`。
- 多次失败但仍有可解析 JSON 时，走 `_coerce_story_plan` 兜底并记录 `_validation.status=coerced_after_validation_failure`。
- 工作流 job message 记录 schema 状态和尝试次数。

### 验收结果

```powershell
python tools\story_plan_validation_smoke.py
```

```text
retry_then_valid ok attempts=2
coerce_after_validation_failure ok attempts=3
```

## 任务 5：README 与环境变量模板

### 改动内容

- 重写 `README.md` 为中文 GitHub 项目说明。
- 删除课程交付、仓库上传流程、履历化描述等语境。
- 补充架构、工作流、模块、数据模型、关键设计、运行方式、检索评测和边界说明。
- `.env.example` 增加 provider mode、story planning、Pydantic repair attempts、image/video generation 注释。

### 文案检查

已对 README、验收日志和环境变量模板执行本地文案扫描，未发现课程交付语境、仓库上传流程说明和模板化项目词汇。

## 回归记录

每个任务完成后均执行 Python/JS 回归检查。最终状态：

```powershell
python -c "import ast; from pathlib import Path; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for root in ['backend','eval','tools'] for p in Path(root).rglob('*.py')]; print('backend eval tools python syntax ok')"
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
python -m pip show pytest
```

```text
backend eval tools python syntax ok
node --check 无输出，表示通过
WARNING: Package(s) not found: pytest
```

没有新增失败项。

## PENDING 清单

当前没有未完成的 live embedding 评测项。SiliconFlow 补跑已完成，`eval/results.md` 中最新三条 live 记录均为：

```text
active_provider=siliconflow:BAAI/bge-m3
status=OK
```

## 附录调查

### 请求链路

点击“开始生成”后：

1. 前端调用 `POST /api/projects/{project_id}/run`。
2. `main.py` 校验项目是否存在。
3. `create_project_job_guarded` 检查并创建 job。
4. FastAPI `BackgroundTasks` 启动 `run_langgraph_workflow(project_id, job_id)`。
5. API 立即返回 `{"job_id": "...", "status": "queued"}`。
6. 前端通过 SSE `/api/projects/{project_id}/events` 监听项目状态和最新 job。

### API 分组

- 健康检查与 Provider：`/api/health`、`/api/providers/*`
- 项目：`/api/projects`
- 工作流：`/run`、`/resume`、`/retry`
- job 查询：`/api/jobs/{job_id}`
- SSE：`/api/projects/{project_id}/events`
- 反馈与版本：`/feedback`、`/rollback`
- 关键帧：`/keyframes/select`、`/keyframes/redraw`
- 视频：`/video`、`/video/safe-retry`、`/videos`、`/videos/refresh`
- 合成：`/assemble`
- 记忆：`/memory/index`、`/memory/search`
- 导出：`/export/json`、`/export/markdown`

### 静态资源

资产写入 `backend/data/projects/{project_id}`，FastAPI 通过 `/assets` 挂载。数据库保存路径和来源标记，不保存文件二进制。

### checkpoint

当前实现使用自定义 SQLite 表 `workflow_checkpoints`。暂停节点 `_pause_review` 调用 `save_workflow_checkpoint` 保存 state，然后工作流结束。恢复时 `resume_langgraph_workflow` 读取 paused checkpoint，注入 `saved_checkpoint_id`，运行单独的 resume graph，从 `index_memory` 到 `complete`。当前没有使用 LangGraph 官方 `SqliteSaver`。

### routing_mode

`project_service.compute_routing_mode` 在创建项目时写入 `projects.routing_mode`：

- `< 5000`：`direct`
- `< 30000`：`chunk`
- 其他：`rag`

`llm_provider.generate_story_plan` 内部也会根据文本长度计算 route。中长文本使用 `_chunk_source_text(size=2600, overlap=260)`，RAG 记忆索引使用 `_chunk_text(size=900, overlap=120)`。

### quality_gate

`_quality_gate` 查询当前项目所有镜头的当前版本，检查 `first_frame_path` 和 `last_frame_path` 是否为空。若有缺失，抛出 `RuntimeError`。`run_langgraph_workflow` 捕获后最多重试整个工作流 2 次；仍失败则项目标记为 `failed`，job 标记为 `failed`。当前没有对缺失关键帧做局部补生成。

### fallback planner

本地 planner 在 `mock_workflow._build_story_data`，用标题、风格和原文片段生成结构化故事数据。它能保证字段完整，适合作为开发和 Provider 失败兜底；语义拆解能力有限，真实内容质量依赖 live LLM。

### 改造前重复运行保护

`main` 分支的后端 `/run` 直接 `create_job` 并加入 BackgroundTasks，没有后端临界区保护。前端存在按钮状态控制，但不能阻止用户重复请求或直接调用 API。任务 3 已在后端补上原子检查和 409 响应。

## 面试要点摘录

### 任务 3

这个任务解决同一项目重复启动工作流的问题。改造前，前端按钮禁用只能减少误触，后端 API 仍可被重复调用，所以我在 job 创建处加了 per-project 进程内锁，把“检查活跃任务”和“创建任务”放进同一个临界区。这个实现适合本地单进程 FastAPI BackgroundTasks，生产环境会升级到 Redis lock、数据库行锁或队列幂等键。

### 任务 1

这个任务把向量化能力从 ChromaDB 调用里抽出来，形成 `EmbeddingProvider` 协议。hash provider 用于本地零依赖运行，SiliconFlow provider 用于接入语义 embedding；两者维度不同，所以 collection 必须隔离。SiliconFlow 失败时降级到 hash，系统可用性优先，同时在日志和评测结果里明确标注 fallback。

### 任务 4

这个任务给 RAG 检索建立了可复现评测。评测项目是 synthetic 数据，先 dump 真实 memory label，再让用例的 expected label 从 dump 中选取，避免主观编造答案。指标记录 commit hash、provider、mode、k、recall@k、MRR 和分组 breakdown；实测 hash hybrid 与 lexical_only 完全同分，推动了语义 embedding 升级，SiliconFlow BGE-M3 在 hybrid@5 下将总 recall 从 `0.8363` 提升到 `0.9494`，同义改写类从 `0.8500` 提升到 `1.0000`。

### 任务 2

这个任务解决 LLM JSON 输出不稳定的问题。原实现会把模型结果直接 coerce，流程很稳，但难以判断模型是否真的符合 schema；现在先用 Pydantic v2 严格校验，失败后把错误摘要交给模型修复。多次失败后保留 coerce 兜底，保证演示流程可继续，同时 job message 会记录校验状态和尝试次数。

### 任务 5

这个任务把项目说明整理成面向代码仓库的 README。文档重点放在产品定位、架构、模块、数据模型、运行方式和边界，避免课程说明和模板化语言。README 同时写清楚本地单用户版本的边界，给出 PostgreSQL、Redis lock、任务队列、对象存储和前端框架化的升级路径。
