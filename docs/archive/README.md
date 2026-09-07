# 文档归档索引

> 这个目录是历史证据，不是现行开发指南。内部路径、命令、依赖和模块名可能已经失效。

## 目录

| 目录 / 文件 | 归档原因 | 当前替代 |
| --- | --- | --- |
| [`v1/`](v1/) | v1 双轨入口、JSON 文件缓存、OpenAI 优先识别、旧发布清理等描述；与 v2 架构不一致 | `README.md`、`docs/ARCHITECTURE.md`、`docs/DEPLOY.md` |
| [`v2-build/`](v2-build/) | v2 搭建期里程碑计划、验收计划、交付报告与盘点记忆；实现已合并 | `docs/11_WebUI产品化缺口执行计划.md` |
| [`research/`](research/) | 外部项目调研、借鉴映射与许可约束 | 保留背景知识；不承诺当前实现 |
| [`agent-session-2026-09-05/`](agent-session-2026-09-05/) | 一次性旧库修复会话记忆，含本机路径和临时结论 | `README.md` 与当前测试 |

## 设计口径更正

- **存储**：v1 文档描述 JSON 文件缓存；现行 v2 使用 SQLite + Alembic。
- **入口**：v1 文档描述 `AutoAnimeMv.py` / `AutoAnimeMv2.py` 双轨；现行推荐入口是模块化 `autoanime` 包与 `python -m autoanime.api serve`。
- **识别**：v1 文档强调 OpenAI 优先和旧回退链；现行是 L1 规则、L2 SQLite 记忆、L3 可选 LLM 与参考源归一。
- **依赖**：v1 的 `requirements.txt`（`requests` / `zhconv`）已归档；现行依赖以 `pyproject.toml` / `uv.lock` 为准。
- **发布清理**：旧发布清理文档针对当时的单文件工作区；现行公开边界以 README 安全提示和 `.gitignore` 为准。

## 已清理的一次性文件

外层工作区中以下重复或临时文件已删除；可追溯内容已先移动到本目录或 Git 历史：

- `_arch_clean.md`
- `_arch_utf8.md`
- `ARCHITECTURE.md`
- `_tmp_REFERENCE.md`
- `_tmp_M2_M4_第一版Plan.md`
- `PLAN.md`
- `REFERENCE.md`
- `notes/` 下的已完成里程碑计划与第一版交付报告

其中 `_arch_clean.md` 的内容修复后收口为 [`../ARCHITECTURE.md`](../ARCHITECTURE.md)；`ARCHITECTURE.md` 与 `_arch_utf8.md` 中存在损坏 NUL 字节，不再保留。
