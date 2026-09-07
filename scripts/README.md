# scripts

本目录只保留与仓库相关的校验和维护工具。

## v2 校验工具

| 脚本 | 用途 |
| --- | --- |
| `validate_l1_corpus.py` | L1 真实语料验证 |
| `validate_l2_corpus.py` | L2 记忆/别名语料验证 |
| `validate_l3_corpus.py` | L3 元数据与仲裁语料验证 |
| `validate_pr7_corpus.py` | 参考源归一化回归验证 |
| `validate_metrics.py` | 指标报表快照验证 |
| `verify_refactor_with_real_data.py` | 重构后真实数据端到端验收 |

## 旧版维护工具

`cache_doctor.py`、`fix_otaku_gal_cache.py`、`normalize_api_cache_cn_punct.py`、`repair_confirmed_organize_errors.py`、`test_opencode_api.py` 属于旧版维护或一次性诊断工具。它们不是 v2 架构基线；删除前先确认没有未迁移的库修复流程依赖。
