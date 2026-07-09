# VisionCraft Retrieval Evaluation Results

| timestamp | commit | provider | active_provider | mode | k | cases | recall@k | MRR | status |
|---|---|---|---|---|---:|---:|---:|---:|---|
| 2026-07-09T06:40:19+00:00 | a0ae1bc | hash | hash | hybrid | 2 | 28 | 0.4732 | 0.6071 | OK |
| 2026-07-09T06:40:37+00:00 | a0ae1bc | hash | hash | hybrid | 5 | 28 | 0.8363 | 0.6619 | OK |
| 2026-07-09T06:40:50+00:00 | a0ae1bc | hash | hash | vector_only | 5 | 28 | 0.7381 | 0.6452 | OK |
| 2026-07-09T06:41:03+00:00 | a0ae1bc | hash | hash | lexical_only | 5 | 28 | 0.8363 | 0.6619 | OK |
| 2026-07-09T06:41:23+00:00 | a0ae1bc | siliconflow | hash | vector_only | 2 | 28 | 0.4792 | 0.5893 | PENDING_LIVE_KEY |
| 2026-07-09T08:08:50+00:00 | f19f237 | hash | hash | hybrid | 5 | 28 | 0.8363 | 0.6619 | OK |
| 2026-07-09T08:09:09+00:00 | f19f237 | hash | hash | lexical_only | 5 | 28 | 0.8363 | 0.6619 | OK |
| 2026-07-09T08:09:28+00:00 | f19f237 | hash | hash | vector_only | 5 | 28 | 0.7381 | 0.6452 | OK |
| 2026-07-09T08:10:32+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | vector_only | 2 | 28 | 0.7738 | 0.8929 | OK |
| 2026-07-09T08:11:03+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 2 | 28 | 0.7708 | 0.8750 | OK |
| 2026-07-09T08:11:37+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 5 | 28 | 0.9494 | 0.8869 | OK |

## Breakdown By Case Category

| timestamp | commit | provider | active_provider | mode | k | group | cases | expected_labels | recall@k | MRR | hit_rate@k | status |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| 2026-07-09T08:08:50+00:00 | f19f237 | hash | hash | hybrid | 5 | direct_match | 10 | 10 | 0.8000 | 0.5283 | 0.8000 | OK |
| 2026-07-09T08:08:50+00:00 | f19f237 | hash | hash | hybrid | 5 | semantic_rewrite | 10 | 19 | 0.8500 | 0.8333 | 1.0000 | OK |
| 2026-07-09T08:08:50+00:00 | f19f237 | hash | hash | hybrid | 5 | cross_shot | 8 | 21 | 0.8646 | 0.6146 | 1.0000 | OK |
| 2026-07-09T08:09:09+00:00 | f19f237 | hash | hash | lexical_only | 5 | direct_match | 10 | 10 | 0.8000 | 0.5283 | 0.8000 | OK |
| 2026-07-09T08:09:09+00:00 | f19f237 | hash | hash | lexical_only | 5 | semantic_rewrite | 10 | 19 | 0.8500 | 0.8333 | 1.0000 | OK |
| 2026-07-09T08:09:09+00:00 | f19f237 | hash | hash | lexical_only | 5 | cross_shot | 8 | 21 | 0.8646 | 0.6146 | 1.0000 | OK |
| 2026-07-09T08:09:28+00:00 | f19f237 | hash | hash | vector_only | 5 | direct_match | 10 | 10 | 0.8000 | 0.5283 | 0.8000 | OK |
| 2026-07-09T08:09:28+00:00 | f19f237 | hash | hash | vector_only | 5 | semantic_rewrite | 10 | 19 | 0.7000 | 0.7333 | 0.9000 | OK |
| 2026-07-09T08:09:28+00:00 | f19f237 | hash | hash | vector_only | 5 | cross_shot | 8 | 21 | 0.7083 | 0.6813 | 1.0000 | OK |
| 2026-07-09T08:10:32+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | vector_only | 2 | direct_match | 10 | 10 | 0.9000 | 0.9000 | 0.9000 | OK |
| 2026-07-09T08:10:32+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | vector_only | 2 | semantic_rewrite | 10 | 19 | 0.8500 | 1.0000 | 1.0000 | OK |
| 2026-07-09T08:10:32+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | vector_only | 2 | cross_shot | 8 | 21 | 0.5208 | 0.7500 | 0.7500 | OK |
| 2026-07-09T08:11:03+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 2 | direct_match | 10 | 10 | 0.9000 | 0.9000 | 0.9000 | OK |
| 2026-07-09T08:11:03+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 2 | semantic_rewrite | 10 | 19 | 0.8500 | 0.9500 | 1.0000 | OK |
| 2026-07-09T08:11:03+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 2 | cross_shot | 8 | 21 | 0.5104 | 0.7500 | 1.0000 | OK |
| 2026-07-09T08:11:37+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 5 | direct_match | 10 | 10 | 1.0000 | 0.9333 | 1.0000 | OK |
| 2026-07-09T08:11:37+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 5 | semantic_rewrite | 10 | 19 | 1.0000 | 0.9500 | 1.0000 | OK |
| 2026-07-09T08:11:37+00:00 | f19f237 | siliconflow | siliconflow:BAAI/bge-m3 | hybrid | 5 | cross_shot | 8 | 21 | 0.8229 | 0.7500 | 1.0000 | OK |
