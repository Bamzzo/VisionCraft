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
