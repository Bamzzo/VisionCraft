# VisionCraft Production Hardening Log

本日志随任务推进记录验收命令与关键输出。所有命令均在 `production-hardening` 分支执行。

## 基线记录

### Git 与环境

```text
branch before work: main
created branch: production-hardening
latest commit: 4a30ccb Initial VisionCraft workspace
tracked files: 39
```

### Pydantic 版本

命令：

```powershell
Get-Content -Raw -Encoding UTF8 requirements.txt
@'
try:
    import pydantic
    print('pydantic', pydantic.__version__)
except Exception as exc:
    print('pydantic import failed', exc)
'@ | python -
```

关键输出：

```text
pydantic>=2.8.0
pydantic 2.12.5
```

结论：后续结构化校验使用 Pydantic v2 的 `model_validate`。

### 改造前语法检查

命令：

```powershell
@'
import ast
from pathlib import Path
for path in Path('backend').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('backend python syntax ok')
'@ | python -
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
```

关键输出：

```text
backend python syntax ok
```

说明：`node --check` 无输出表示检查通过。

### 测试套件基线

命令：

```powershell
pytest -q
python -m pip show pytest
```

关键输出：

```text
pytest : The term 'pytest' is not recognized as the name of a cmdlet...
WARNING: Package(s) not found: pytest
```

结论：当前环境未安装 pytest，仓库也没有现成 tests 目录。后续每个任务以 Python AST 语法检查、前端 JS `node --check`、任务专项脚本作为回归保护。

### 改造前 hash 检索样例

命令：

```powershell
@'
from backend.services.project_service import list_projects
from backend.services.memory_service import search_project_memory, index_project_memory
projects = list_projects(include_archived=True)
print('project_count', len(projects))
if projects:
    p = projects[0]
    print('project', p['id'], p['title'], p['status'])
    indexed = index_project_memory(p['id'])
    print('indexed', indexed)
    result = search_project_memory(p['id'], '角色 场景 关键线索', 5)
    for item in result:
        md = item.get('metadata') or {}
        print(md.get('label'), md.get('kind'), item.get('score'), (item.get('document') or '')[:80].replace('\n',' '))
'@ | python -
```

关键输出：

```text
project_count 12
project project_3b379f2246 ... ready_for_review
indexed 29
... scene 0.0043 ...
... asset:scene 0.0041 ...
... scene 0.0 ...
```

说明：本地历史项目标题存在编码显示问题，但 hash 检索链路可运行。后续任务 1 和任务 4 会使用新建评测项目构造可复现样例。

## 任务 3：per-project 防重入锁

### 改动文件

- `backend/services/job_service.py`
- `backend/main.py`
- `frontend/js/api.js`

### 设计记录

- 新增进程内 `{project_id: threading.Lock}`，将“检查活跃 job + 创建新 job”包进临界区。
- 活跃 job 定义为 `status IN ('queued', 'running')`。
- full workflow 的 `run/retry` 额外阻塞 `paused`，提示用户恢复暂停流程。
- `resume` 只检查 queued/running，不创建新 job。
- FastAPI startup 将历史 `queued/running` job 标记为 failed，并写入 `orphaned on restart`。
- 前端把 HTTP 409 busy 响应转成中文提示。
- 该锁只适用于单进程 FastAPI BackgroundTasks；多 worker 或分布式部署应替换为 Redis lock、数据库行锁或任务队列。

### 语法回归

命令：

```powershell
@'
import ast
from pathlib import Path
for path in Path('backend').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('backend python syntax ok')
'@ | python -
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
```

关键输出：

```text
backend python syntax ok
```

### 专项验收

命令：使用临时端口 `8123` 启动后端，并在环境变量中置空 live provider key，避免触发真实模型调用。

关键输出：

```text
orphan_before job_09566f3992 queued
orphan_after job_09566f3992 failed orphaned on restart
create_A 200 project_beb5a77210
run_A_first 200 {'job_id': 'job_95a6da5f09', 'status': 'queued'}
run_A_second 409 {'detail': 'project busy; resume paused workflow or wait for the active job', 'active_job_id': 'job_95a6da5f09', 'active_job_type': 'full_workflow', 'active_job_status': 'queued'}
run_B_while_A_active 200 {'job_id': 'job_3b6ee760d2', 'status': 'queued'}
run_C_review 200 {'job_id': 'job_70d388ec06', 'status': 'queued'}
C_paused True review_pending
run_C_paused_again 409 {'detail': 'project busy; resume paused workflow or wait for the active job', 'active_job_id': 'job_70d388ec06', 'active_job_type': 'full_workflow', 'active_job_status': 'paused'}
resume_C 200 {'job_id': 'job_70d388ec06', 'status': 'resuming'}
```

验收结论：

- 同一项目连续两次 `/run`：第一次 200，第二次 409。
- 两个不同项目同时 `/run`：互不阻塞。
- paused workflow 阻塞新的 full run，并提示走 resume。
- `/resume` 在 paused 状态下正常返回 200。
- startup orphan cleanup 生效。

### 遗留问题

- 进程内锁不适用于多 worker 或多机部署。
- 当前 startup cleanup 会将所有历史 queued/running job 置为 failed，符合单进程重启语义；如果未来引入持久任务队列，需要由队列系统恢复或认领任务。

## 任务 1：Embedding Provider 抽象与 SiliconFlow 降级

### 改动文件

- `backend/providers/embedding_provider.py`
- `backend/services/memory_service.py`
- `.env.example`
- `docs/production_hardening_log.md`

### 设计记录

- 新增 `EmbeddingProvider` 协议，统一 `embed_texts(texts)`、`dimension`、`name`。
- `HashEmbeddingProvider` 封装原有 bigram + blake2b hash embedding，维度保持 384。
- `SiliconFlowEmbeddingProvider` 调用 OpenAI-compatible `/embeddings`，默认模型 `BAAI/bge-m3`，维度 1024，批量上限 32，超时 30s，失败重试 1 次。
- Chroma collection 按 provider 隔离：
  - hash: `visioncraft_memory_hash`
  - siliconflow bge-m3: `visioncraft_memory_sf_bge-m3`
- 写入与查询改为显式传 `embeddings=` / `query_embeddings=`，避免远程失败后 1024 维与 384 维混入同一 collection。
- `siliconflow` 模式 key 缺失或调用失败时，进程内降级到 hash provider，并记录 warning。
- hybrid rerank 保留，权重由 `HYBRID_LEXICAL_WEIGHT` / `HYBRID_VECTOR_WEIGHT` 控制；未配置时 hash 默认 0.8/0.2，siliconflow 默认 0.3/0.7。

### 新增配置

```text
EMBEDDING_PROVIDER=hash
EMBEDDING_MODEL=BAAI/bge-m3
HYBRID_LEXICAL_WEIGHT=0.8
HYBRID_VECTOR_WEIGHT=0.2
```

### 语法回归

命令：

```powershell
@'
import ast
from pathlib import Path
for path in Path('backend').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('backend python syntax ok')
'@ | python -
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
```

关键输出：

```text
backend python syntax ok
```

### hash 模式验收

命令：

```powershell
$env:EMBEDDING_PROVIDER='hash'
@'
from backend.providers.embedding_provider import get_embedding_provider, collection_name_for_provider
from backend.services.project_service import list_projects
from backend.services.memory_service import index_project_memory, search_project_memory, get_collection
provider = get_embedding_provider()
print('provider', provider.name, provider.dimension, collection_name_for_provider(provider))
projects = list_projects(include_archived=True)
p = projects[0]
print('project', p['id'], p['title'], p['status'])
indexed = index_project_memory(p['id'])
print('indexed', indexed)
collection = get_collection(provider)
print('collection', collection.name)
items = search_project_memory(p['id'], '角色 场景 关键线索', 5)
for item in items:
    md = item.get('metadata') or {}
    print(md.get('label'), md.get('kind'), item.get('score'))
'@ | python -
```

关键输出：

```text
provider hash 384 visioncraft_memory_hash
indexed 28
collection visioncraft_memory_hash
paused project source_text 0.9741
故事圣经 story_bible 0.9641
Shot 1 First Frame asset:first-frame 0.8524
```

### 改造前同项目对照

命令：

```powershell
$env:EMBEDDING_PROVIDER='hash'
@'
from backend.services.memory_service import index_project_memory, search_project_memory
pid='project_3b379f2246'
print('project', pid)
print('indexed', index_project_memory(pid))
items = search_project_memory(pid, '角色 场景 关键线索', 5)
for item in items:
    md=item.get('metadata') or {}
    print(md.get('label'), md.get('kind'), item.get('score'))
'@ | python -
```

关键输出：

```text
project project_3b379f2246
indexed 29
... scene 0.0043
... asset:scene 0.0041
... scene 0.0
... scene 0.0
... asset:scene 0.0
```

结论：同一项目、同一 query 的 hash top label 与基线一致。

### siliconflow 缺 key 降级验收

命令：

```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
$env:SILICONFLOW_API_KEY=''
@'
from backend.providers.embedding_provider import get_embedding_provider, collection_name_for_provider
from backend.services.project_service import list_projects
from backend.services.memory_service import index_project_memory, search_project_memory
provider = get_embedding_provider()
print('provider_after_missing_key', provider.name, provider.dimension, collection_name_for_provider(provider))
p = list_projects(include_archived=True)[0]
print('indexed', index_project_memory(p['id']))
items = search_project_memory(p['id'], '角色 场景 关键线索', 3)
for item in items:
    md = item.get('metadata') or {}
    print(md.get('label'), md.get('kind'), item.get('score'))
'@ | python -
```

关键输出：

```text
Falling back to hash embedding provider: SILICONFLOW_API_KEY is missing
provider_after_missing_key hash 384 visioncraft_memory_hash
indexed 28
paused project source_text 0.9741
故事圣经 story_bible 0.9641
Shot 1 First Frame asset:first-frame 0.8524
```

### siliconflow live smoke

命令：

```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
Remove-Item Env:SILICONFLOW_API_KEY -ErrorAction SilentlyContinue
@'
import os
from backend.config import init_environment
init_environment()
os.environ['EMBEDDING_PROVIDER'] = 'siliconflow'
from backend.providers.embedding_provider import get_embedding_provider, embed_texts_with_fallback, collection_name_for_provider
provider = get_embedding_provider()
print('resolved_provider', provider.name, provider.dimension, collection_name_for_provider(provider))
provider2, vectors = embed_texts_with_fallback(provider, ['皇帝在御书房审阅奏章', '君主站在宫殿窗前'])
print('used_provider', provider2.name, provider2.dimension, len(vectors), len(vectors[0]) if vectors else 0)
print('fallback_used', provider2.name == 'hash')
'@ | python -
```

关键输出：

```text
resolved_provider siliconflow:BAAI/bge-m3 1024 visioncraft_memory_sf_bge-m3
used_provider hash 384 2 384
fallback_used True
Falling back to hash embedding provider: SiliconFlow embedding HTTP 403: {"code":30001,"message":"Sorry, your account balance is insufficient","data":null}
```

结论：SiliconFlow embedding 接口因余额不足返回 403，任务 1 live semantic 验收标记为 `PENDING_LIVE_KEY`。代码已验证会降级 hash，工作流不会因 embedding 失败中断。

### PENDING_LIVE_KEY

待 SiliconFlow 余额/key 可用后执行：

```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
$env:HYBRID_LEXICAL_WEIGHT='0.3'
$env:HYBRID_VECTOR_WEIGHT='0.7'
python eval\run_retrieval_eval.py --provider siliconflow --mode vector_only --k 2
python eval\run_retrieval_eval.py --provider siliconflow --mode hybrid --k 2
python eval\run_retrieval_eval.py --provider siliconflow --mode hybrid --k 5
```

## 任务 2：Pydantic 结构化校验与修复重试

### 改动文件

- `backend/providers/llm_provider.py`
- `backend/workflow/langgraph_workflow.py`
- `backend/workflow/mock_workflow.py`
- `tools/story_plan_validation_smoke.py`
- `.env.example`

### 设计记录

- 当前环境 Pydantic 版本为 `2.12.5`，结构化校验使用 v2 的 `model_validate`。
- 新增 `StoryPlanModel`、`StoryCharacterModel`、`StorySceneModel`、`StoryShotModel`，覆盖编剧 Agent 输出的故事圣经、角色、场景和镜头字段。
- `extra=ignore`：模型多返回字段不会失败；缺少必要字段、空字符串、空列表、镜头数量不等于 `shot_count` 会触发校验失败。
- 新增 `PLAN_VALIDATION_MAX_RETRIES=2`，默认最多修复 2 次，即总共 3 次 LLM JSON 尝试。
- 校验失败后，把 Pydantic 错误摘要压缩到 800 字以内，追加到下一轮修复提示中，要求模型只返回 JSON。
- 如果多次修复仍失败，但最后一次至少返回了可解析 JSON，则走 `_coerce_story_plan` 兜底并标记 `_validation.status=coerced_after_validation_failure`。如果连 JSON 都无法解析，则抛出 `ProviderError`，工作流回退本地 planner。
- LangGraph workflow 和 mock workflow 会把 schema 状态写入 job message，例如 `Story plan schema validated after 2 attempt(s)`。

### 专项验收

命令：
```powershell
python tools\story_plan_validation_smoke.py
python -c "import pydantic; print('pydantic', pydantic.__version__)"
```

关键输出：
```text
retry_then_valid ok attempts=2
coerce_after_validation_failure ok attempts=3
pydantic 2.12.5
```

结论：第一次 schema 不合格、第二次修复成功的路径通过；连续失败后 coerce 兜底的路径通过。

### 回归检查

命令：
```powershell
python -c "import ast; from pathlib import Path; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for root in ['backend','eval','tools'] for p in Path(root).rglob('*.py')]; print('backend eval tools python syntax ok')"
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
python -m pip show pytest
```

关键输出：
```text
backend eval tools python syntax ok
node --check 无输出，表示通过
WARNING: Package(s) not found: pytest
```

结论：Python/JS 语法回归通过，pytest 仍未安装，与基线一致，没有新增失败。

## 任务 5：README 与环境变量模板

### 改动文件

- `README.md`
- `.env.example`
- `docs/production_hardening_log.md`

### 设计记录

- README 改为面向 GitHub 项目的中文说明，不包含作业、上传须知、简历描述等内容。
- README 保留产品定位、功能表、架构图、LangGraph 工作流、后端模块、前端模块、数据模型、关键设计、运行方式、环境变量、检索评测、常见问题和边界说明。
- 关键设计中补充了本次生产化改造：per-project 防重入锁、Embedding Provider 和 collection 隔离、检索评测、Pydantic 校验重试、关键帧连续性、远程视频任务回查、成片来源校验。
- `.env.example` 增加 provider mode、story planning、Pydantic repair attempts、image/video generation 的注释，保留空 key，不包含任何真实密钥。

### 文案检查

命令：
```powershell
rg "作业|不是|而是|不仅|更是|赋能|闭环|生态|全链路|AI味|简历" README.md docs\production_hardening_log.md .env.example
```

关键输出：
```text
无输出，表示未命中
```

### 回归检查

命令：
```powershell
python -c "import ast; from pathlib import Path; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for root in ['backend','eval','tools'] for p in Path(root).rglob('*.py')]; print('backend eval tools python syntax ok')"
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
python -m pip show pytest
```

关键输出：
```text
backend eval tools python syntax ok
node --check 无输出，表示通过
WARNING: Package(s) not found: pytest
```

结论：README 和 `.env.example` 更新没有影响代码回归，pytest 仍未安装，与基线一致。

### 遗留问题

- 当前 live semantic recall 尚未验证，原因是 SiliconFlow 账户余额不足。
- embedding collection 会按 provider 分离；切换 provider 后需要手动调用 `/memory/index` 或运行评测脚本重建目标项目索引。

## 任务 4：检索评测集与评测脚本

### 改动文件

- `eval/eval_support.py`
- `eval/dump_memory_labels.py`
- `eval/run_retrieval_eval.py`
- `eval/retrieval_eval_set.json`
- `eval/memory_labels_dump.md`
- `eval/results.md`

### 设计记录

- 新建固定 synthetic 项目 `eval_project_001`，只用于检索评测，不触碰用户已有项目。
- 评测项目包含故事圣经、3 个角色、4 个场景、2 个视觉资产、5 个镜头，共 16 条 memory 文档。
- `dump_memory_labels.py` 会先重建评测项目，再把 ChromaDB 中实际写入的 `label / kind / 摘要前 50 字` 写入 `eval/memory_labels_dump.md`。
- `retrieval_eval_set.json` 共 28 条用例：前 10 条直接实体匹配，中 10 条同义改写，后 8 条跨镜头追问；每条用例都带 `note`。
- `run_retrieval_eval.py` 支持 `--provider hash|siliconflow`、`--mode vector_only|lexical_only|hybrid`、`--k`，并在运行前校验 `expected_labels` 必须来自当前 memory dump。
- 评测脚本会重建同一个 synthetic 项目，因此应串行执行。一次并行尝试出现了共享评测项目重建竞争，随后改为串行验收。

### 代码提交

```text
a0ae1bc [task-4] add retrieval evaluation harness
```

### memory label dump

命令：
```powershell
$env:EMBEDDING_PROVIDER='hash'
python eval\dump_memory_labels.py
```

关键输出：
```text
wrote D:\...\visioncraft\eval\memory_labels_dump.md
memory_rows 16
```

### hash 评测

命令：
```powershell
$env:EMBEDDING_PROVIDER='hash'
python eval\run_retrieval_eval.py --provider hash --mode hybrid --k 2
python eval\run_retrieval_eval.py --provider hash --mode hybrid --k 5
python eval\run_retrieval_eval.py --provider hash --mode vector_only --k 5
python eval\run_retrieval_eval.py --provider hash --mode lexical_only --k 5
```

关键输出：
```text
provider=hash active_provider=hash mode=hybrid k=2 cases=28 recall@k=0.4732 mrr=0.6071 status=OK
provider=hash active_provider=hash mode=hybrid k=5 cases=28 recall@k=0.8363 mrr=0.6619 status=OK
provider=hash active_provider=hash mode=vector_only k=5 cases=28 recall@k=0.7381 mrr=0.6452 status=OK
provider=hash active_provider=hash mode=lexical_only k=5 cases=28 recall@k=0.8363 mrr=0.6619 status=OK
```

### SiliconFlow live 评测

命令：
```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
python eval\run_retrieval_eval.py --provider siliconflow --mode vector_only --k 2
```

关键输出：
```text
Falling back to hash embedding provider: SiliconFlow embedding HTTP 403: {"code":30001,"message":"Sorry, your account balance is insufficient","data":null}
provider=siliconflow active_provider=hash mode=vector_only k=2 cases=28 recall@k=0.4792 mrr=0.5893 status=PENDING_LIVE_KEY
```

结论：SiliconFlow embedding 仍因账户余额不足返回 403。本次 live 语义评测标记为 `PENDING_LIVE_KEY`，hash 模式和 fallback 路径可运行。

### 回归检查

命令：
```powershell
python -c "import ast; from pathlib import Path; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for root in ['backend','eval'] for p in Path(root).rglob('*.py')]; print('backend and eval python syntax ok')"
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\render.js
node --check frontend\js\state.js
python -m pip show pytest
```

关键输出：
```text
backend and eval python syntax ok
node --check 无输出，表示通过
WARNING: Package(s) not found: pytest
```

结论：Python/JS 语法回归通过，pytest 仍未安装，与基线一致，没有新增失败。

### PENDING_LIVE_KEY

SiliconFlow key/余额可用后执行：

```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
$env:HYBRID_LEXICAL_WEIGHT='0'
$env:HYBRID_VECTOR_WEIGHT='1'
python eval\run_retrieval_eval.py --provider siliconflow --mode vector_only --k 2

$env:HYBRID_LEXICAL_WEIGHT='0.3'
$env:HYBRID_VECTOR_WEIGHT='0.7'
python eval\run_retrieval_eval.py --provider siliconflow --mode hybrid --k 2
python eval\run_retrieval_eval.py --provider siliconflow --mode hybrid --k 5
```
