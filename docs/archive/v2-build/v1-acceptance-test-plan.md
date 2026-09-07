> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# 第一版验收测试 Plan（E4 合并后执行）

> 生成：2026-09-06。前置：E1-E4 全部合并进 v2，D10 验收 + 转正包之后执行。
> 目标：真实数据全链路验证，交付「功能可用、无逻辑 bug」的第一代产品。

## 0. 环境与配置

- 代理：`HTTPS_PROXY=http://127.0.0.1:7890`（TMDB/Bangumi/ark 均已实测 200）
- LLM（火山方舟，OpenAI 兼容）：base_url `https://ark.cn-beijing.volces.com/api/v3`
  - 模型链（额度耗尽依序降级，均已实测 200）：
    1. `deepseek-v4-flash-ga-260731`（主）
    2. `doubao-seed-2-1-turbo-260628`
    3. `glm-5-2-260617`
    4. `doubao-seed-evolving`
  - env：`AUTOANIME_LLM_ENABLED=true`、`AUTOANIME_LLM_BASE_URL`、`AUTOANIME_LLM_API_KEY`、`AUTOANIME_LLM_MODEL`
  - 注意：ark 为推理模型，若 10s 默认超时不足，测试时调 `AUTOANIME_LLM_TIMEOUT_S`
- TMDB：`AUTOANIME_TMDB_API_KEY`（AutoAnime/.env 已有）
- 测试区：`E:\AutoAnimeTest\`（downloads=下载区；library=归档库；**同盘保证 hardlink 生效路径**）
- qBittorrent（远程 qb.ymxc152.top，凭据见 AnimeAgent/.env）：只做只读连通冒烟；
  真实添加种子属外发动作，执行前单独向用户确认（Plan §7.4 / D5 L2）

## 1. 测试数据（E:\AutoAnimeTest\downloads，已复制 5.1GB）

| 目录/文件 | 特征 | 覆盖点 |
|---|---|---|
| Anime.AzurLane.Slow.Ahead.S02...(Baha)-MWeb ×2集 | 整季包命名、Baha 来源 | L1 season pack、L3 Bangumi/TMDB |
| Bleach.S04...(DSNP)-MWeb ×1集 | 长青番 S04 | 高季数识别 |
| Bleach.S04...(LINETV)-MWeb ×1集 | 同番不同来源 | L2 记忆命中、洗版升级判定 |
| BLEACH.Si.Shen.Qian.Nian...（拼音命名）×1集 | 中文拼音罗马化 | PR7 前置消歧/别名 |
| Bungo.Stray.Dogs.Wan.S02...(Baha) ×2集 | 外传「Wan」后缀 | 短名/外传保护 |
| Clevatess.S02...(friDay)-MWeb ×1集 | friDay 来源 | 来源评分维度 |
| Chainsmoker.Cat.S01E01...(NF)-VARYG.mkv | 单文件、NF 流媒体 | 单文件快路径 |

## 2. 白盒测试（代码级）

1. `uv run pytest` 全量（含 E1-E4 新增）全绿；`ruff`/`pyright` 零错。
2. 真实库联测：临时 SQLite 库指向 E:\AutoAnimeTest，跑 parse→confirm→organize 全链，断言库内
   parse_memory/title_aliases/audit/release_record/episode 状态机转移正确。
3. 合批：同目录同字幕组 ≥5 凑批、<5 单文件、订阅路径不凑批（E1 单测 + 快照指标复核）。
4. 洗版决策表：DSNP vs LINETV 同集评分对比 → 升级/保留判定与 audit 记录。
5. 迁移链：alembic 0001→head upgrade/downgrade 往返。

## 3. 黑盒测试（CLI 端到端，真实网络+真实 LLM）

1. **识别**：对 7 组素材逐个 `autoanime parse`（或对应命令），记录：L1 分级、L2 命中、L3 路由、
   canonical 归一（拼音/外传用例必须归一到中文权威名）、耗时。
2. **学习闭环**：对识别结果 confirm → 复查 parse_memory 两级键 + title_aliases 行；二次 parse 同风格
   文件应 L2/alias 命中零外呼；错误字段 correct → bypass 生效。
3. **归档**：organize 到 E:\AutoAnimeTest\library：
   - D17 命名模板逐字段核对（title_cn/Season SS/SxxExx/quality）
   - **硬链接实证**：同盘 E:→E: 用 `fsutil hardlink list` 验证归档文件与下载原件同一 MFT 记录；
     原件保留可做种（D21）
   - D18 字幕跟随（如素材含字幕）
   - 跨盘降级：library 指到 C: 小样本验证 copy 降级与 >20GB 跳过（用大文件 Clevatess 集）
4. **回滚**：rollback 后归档文件消失/还原，audit reverse 记录完整。
5. **报表**：`autoanime report --json` 人工介入率/命中率与实际操作数一致。
6. **LLM 模型链**：主模型跑通后，用错误 key 模拟 401/额度耗尽 → 人工按链切换模型重跑，
   记录各模型识别质量差异（幻觉/字段错误率）。

## 4. WebUI 黑盒（browser-use 走真实页面）

1. uvicorn 起服（token 空）+ `npm run preview`（真 API 模式），8 页逐一走查：
   Dashboard 指标与 CLI report 一致；Library 树与库内一致；Pending diff 视图 evidence 标注；
   Pipeline SSE 流动（触发一次 parse 观察实时事件）；Logs 分组+回滚按钮；Settings PUT 生效；
   Subscriptions/RSSSources CRUD。
2. SSE：断线重连（杀服重启）后 Last-Event-ID 补发。
3. 认证：设 AUTOANIME_API_TOKEN 后页面带 token 访问正常、无 token 401。

## 5. D10 验收剧本（适配后）

| 剧本条目 | 适配口径 |
|---|---|
| docker-compose 一键起 | docker 未装：compose 文件结构校验（yaml/字段）+ 提供 `uv run serve + npm preview` 一键脚本；真实 docker 验证留用户环境 |
| 放番剧订阅全自动闭环 1h | 本地静态 RSS fixture HTTP 服务 + 真实调度轮询走通 拉取→匹配→（下载网关 fake/或 qB 只读）→归档 全链；真实 Mikan+qB 添加种子前单独确认 |
| 2608/2606 库存导入出指标 | validate_metrics.py 快照报告 + report --json |
| xyflow 页实时流动 | §4.1 Pipeline 页实测 |

## 5.1 多轮轮换纪律（用户要求）

- 每轮黑盒测试后**更换一批样本**再跑下一轮（`bash E:\AutoAnimeTest\rotate_samples.sh <1|2|3>`）：
  - 第 1 轮：初始 7 组（整季包/同番双来源/拼音/外传/单文件）
  - 第 2 轮：全新番剧（Bookworm 三源混搭/Daemons NF/BLACK TORCH/BanG Dream friDay）
  - 第 3 轮：回归轮——同番不同集（验证 L2 记忆与 title_aliases 跨集命中 + 洗版升级）
- 每轮换库（新 SQLite 库文件），上一轮发现的 bug 修复后必须在下一轮回归确认。
- 全部轮次通过才算验收；任一轮发现逻辑 bug：立即修复 → 该用例进固定回归集 → 重跑本轮。

## 6. 交付物

- 测试报告（白盒/黑盒/WWW 三节 + 缺陷清单与修复记录）
- PR7+M2-M4 指标对比表（报告 JSON 汇总）
- 转正包：LICENSE(MIT)、README（v2 指向）、demo 素材、docs/DEPLOY.md
