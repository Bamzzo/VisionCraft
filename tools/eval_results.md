# VisionCraft 检索评测结果

> 口径：`tools/eval_retrieval.py`。语料 `output/test_texts/蛊真人100000字.txt`（95,618 字 / **131 个章节块**），
> 16 例标注（6 字面 / 8 同义改写 / 2 抽象），本次切分下全部可答（无 UNANSWERABLE）。
> **产品路径 `build_shot_evidence` 走 `limit=2`，所以 `k=2` 才是关键区间**，`k=5` 只作参考。
> `note` 列用来区分召回逻辑的 A/B —— 没有它，改动前后的数字会混在一张表里看不出区别。
> 三种 mode 的作用：`vector_only` 看"语义"本身的贡献，`lexical_only` 看字面贡献，`hybrid` 是产品实际口径。

| timestamp | commit | provider | active_provider | mode | k | cases | recall@k | MRR | note |
|---|---|---|---|---|---:|---:|---:|---:|---|
| 2026-10-02T11:21:49+0800 | fcb6c3d | hash | hash | hybrid | 2 | 16 | 0.4375 | 0.4375 | 候选池修复前 |
| 2026-10-02T11:21:49+0800 | fcb6c3d | hash | hash | vector_only | 2 | 16 | 0.0625 | 0.0625 | 候选池修复前 |
| 2026-10-02T11:21:49+0800 | fcb6c3d | hash | hash | lexical_only | 2 | 16 | 0.8750 | 0.8438 | 候选池修复前 |
| 2026-10-02T11:22:19+0800 | fcb6c3d | hash | hash | hybrid | 5 | 16 | 0.5000 | 0.5000 | 候选池修复前 |
| 2026-10-02T11:22:19+0800 | fcb6c3d | hash | hash | vector_only | 5 | 16 | 0.1875 | 0.0958 | 候选池修复前 |
| 2026-10-02T11:22:19+0800 | fcb6c3d | hash | hash | lexical_only | 5 | 16 | 0.9375 | 0.8594 | 候选池修复前 |
| 2026-10-02T11:29:31+0800 | fcb6c3d | hash | hash | hybrid | 2 | 16 | 0.8750 | 0.8438 | 候选池修复后 hash |
| 2026-10-02T11:29:31+0800 | fcb6c3d | hash | hash | vector_only | 2 | 16 | 0.0625 | 0.0625 | 候选池修复后 hash |
| 2026-10-02T11:29:31+0800 | fcb6c3d | hash | hash | lexical_only | 2 | 16 | 0.8750 | 0.8438 | 候选池修复后 hash |
| 2026-10-02T11:29:31+0800 | fcb6c3d | hash | hash | hybrid | 5 | 16 | 0.9375 | 0.8562 | 候选池修复后 hash |
| 2026-10-02T11:29:31+0800 | fcb6c3d | hash | hash | vector_only | 5 | 16 | 0.1875 | 0.0958 | 候选池修复后 hash |
| 2026-10-02T11:29:31+0800 | fcb6c3d | hash | hash | lexical_only | 5 | 16 | 0.9375 | 0.8594 | 候选池修复后 hash |
| 2026-10-02T11:30:48+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | hybrid | 2 | 16 | 0.8125 | 0.7812 | 候选池修复后 dashscope |
| 2026-10-02T11:30:48+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | vector_only | 2 | 16 | 0.6875 | 0.6562 | 候选池修复后 dashscope |
| 2026-10-02T11:30:48+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | lexical_only | 2 | 16 | 0.8750 | 0.8438 | 候选池修复后 dashscope |
| 2026-10-02T11:30:48+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | hybrid | 5 | 16 | 0.9375 | 0.9062 | 候选池修复后 dashscope |
| 2026-10-02T11:30:48+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | vector_only | 5 | 16 | 0.7500 | 0.6687 | 候选池修复后 dashscope |
| 2026-10-02T11:30:48+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | lexical_only | 5 | 16 | 0.9375 | 0.8594 | 候选池修复后 dashscope |
| 2026-10-02T11:33:39+0800 | fcb6c3d | hash | hash | hybrid | 2 | 16 | 0.8750 | 0.8438 | 最终权重0.8/0.2 hash |
| 2026-10-02T11:33:39+0800 | fcb6c3d | hash | hash | vector_only | 2 | 16 | 0.0625 | 0.0625 | 最终权重0.8/0.2 hash |
| 2026-10-02T11:33:39+0800 | fcb6c3d | hash | hash | lexical_only | 2 | 16 | 0.8750 | 0.8438 | 最终权重0.8/0.2 hash |
| 2026-10-02T11:33:39+0800 | fcb6c3d | hash | hash | hybrid | 5 | 16 | 0.9375 | 0.8562 | 最终权重0.8/0.2 hash |
| 2026-10-02T11:33:39+0800 | fcb6c3d | hash | hash | vector_only | 5 | 16 | 0.1875 | 0.0958 | 最终权重0.8/0.2 hash |
| 2026-10-02T11:33:39+0800 | fcb6c3d | hash | hash | lexical_only | 5 | 16 | 0.9375 | 0.8594 | 最终权重0.8/0.2 hash |
| 2026-10-02T11:34:18+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | hybrid | 2 | 16 | 0.9375 | 0.8750 | 最终权重0.8/0.2 dashscope |
| 2026-10-02T11:34:18+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | vector_only | 2 | 16 | 0.6875 | 0.6562 | 最终权重0.8/0.2 dashscope |
| 2026-10-02T11:34:18+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | lexical_only | 2 | 16 | 0.8750 | 0.8438 | 最终权重0.8/0.2 dashscope |
| 2026-10-02T11:34:18+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | hybrid | 5 | 16 | 0.9375 | 0.9062 | 最终权重0.8/0.2 dashscope |
| 2026-10-02T11:34:18+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | vector_only | 5 | 16 | 0.7500 | 0.6687 | 最终权重0.8/0.2 dashscope |
| 2026-10-02T11:34:18+0800 | fcb6c3d | dashscope | dashscope:text-embedding-v4 | lexical_only | 5 | 16 | 0.9375 | 0.8594 | 最终权重0.8/0.2 dashscope |
