# Agent 协作记忆（AutoAnimeMv2 回退链路修复）

## 项目目标
修复 AutoAnimeMv2 在关闭 OpenAI API 后走回退链路（本地正则 → Bangumi → TMDB）时的识别错误，通过真实数据 `F:/下载` 整理到 `F:/test` 验证。

## 已完成的修复单元

### 单元 1：Windows 日志编码崩溃
- **问题**：`Auxiliary_Log` 在 Windows 默认 GBK 终端输出 `\uff65` 等字符时崩溃。
- **修改**：
  - `autoanime/cli.py`：入口设置 `stdout/stderr` 为 UTF-8。
  - `autoanime/logging_utils.py`：`print` 分支增加 `UnicodeEncodeError` 防御回退。
- **验证**：1388 文件 dry-run 跑完无崩溃。

### 单元 2：剧名清洗过度（保留 `!`）
- **问题**：`Auxiliary_UniformOTSTR` 把 `!` 替换成 `=`，导致 `Ganbare! Nakamura-kun!!` 搜不到。
- **修改**：`autoanime/naming.py:78` 白名单增加 `!` / `！`。
- **验证**：日志中不再出现 `Ganbare=-Nakamura-kun`。

### 单元 3：剧名漂移保护误判
- **问题**：`autoanime/pipeline/main.py` 中 `Auxiliary_ShowFindCrossCanonicalEpisode` 仅按 `(SE, EP)` 匹配，导致 `MAO`、`Fate Strange Fake` 等无关番剧被合并到已有 CanonicalID。
- **修改**：复用 `CrossCID` 前增加 `_CurrentAliasesMatchCanonicalID` 校验，验证当前剧名与目标 CanonicalID 的别名是否相关。
- **验证**：漂移保护复用从 60 次降到 0 次，拒绝 623 次无关合并。

### 单元 4.1：Bangumi max_results 放宽 + 季号剥离
- **问题**：
  - `Bangumi` 只取 `max_results=1`，错失英文条目的中文名。
  - `RAWNameLocal` 含 `2nd Season`/`S2` 等后缀，Bangumi 搜不到。
- **修改**：
  - `autoanime/apis/bangumi.py`：`max_results=5`，遍历取首个含中文标题。
  - `autoanime/identification/local_fallback.py`：新增 `_StripSeasonSuffixes` / `_GenerateQueryNameCandidates`，查询前剥离季号。
- **验证**：`Medalist 2nd Season` 收敛到 `金牌得主`。

### 单元 4.2：扩展 manual whitelist 兜底
- **问题**：Bangumi/TMDB 都失败时，英文/罗马音剧名无中文兜底。
- **修改**：
  - `autoanime/cache/manual_whitelist.py`：扩展默认白名单（Fate、GANSO BanG Dream Chan、Medalist、Nakamura-kun、MAO、勇者之屑、魔都精兵的奴隶、冰之城墙、数码宝贝 觉醒节拍等）。
  - `autoanime/identification/local_fallback.py`：API 链路失败后调用 `Auxiliary_GetManualWhitelistedTitle`。
- **验证**：大量英文剧名收敛到中文目录。

### 单元 5：绝对集数映射
- **问题**：`Sousou no Frieren - 38` 被识别为 `S01E38`（应为 S02E10）。
- **修改**：
  - `autoanime/identification/episode_rules.py`：新增通用 `Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode` 和内置 `_MANUAL_SEASON_LAYOUT`。
  - `autoanime/identification/local_fallback.py` / `autoanime/identification/title_chain.py`：调用通用映射。
- **验证**：芙莉莲 38 → S02E10，咒术回战 58 → S03E11，地狱乐 25 越界警告。

### 单元 7：支持两种新文件名格式 + 修复错误缓存
- **问题**：
  - 文件名含字幕组前缀 + 多语言标题（`六四位元字幕組★哪裡有溫柔對待阿宅的辣妹！？ Otaku ni Yasashii Gal wa Inai★09...`）。
  - 文件名含 `[中 / 日 / 英]` 多语言分段（`【今晚月色真美】[没有辣妹会对阿宅温柔！？ / オタクに優しいギャルはいない!? / Otaku ni Yasashii Gal wa Inai!?][11]...`）。
  - 缓存中 `Otaku ni Yasashii Gal wa Inai` 的 canonical 标题为旧译名 `没有辣妹会对阿宅温柔！？`，需统一为目标译名 `哪里有温柔对待阿宅的辣妹！？`。
- **修改**：
  - `autoanime/text_utils.py`：新增 `Auxiliary_CleanFallbackTitle`，清洗字幕组前缀、提取中文段、去除尾部非中文后缀。
  - `autoanime/identification/local_fallback.py`：`Auxiliary_FallbackLocalRules` 中对 `RAWName` 先调用 `Auxiliary_CleanFallbackTitle`。
  - `scripts/fix_otaku_gal_cache.py`：一次性脚本，备份并修复 `.cache/titles.json` / `.cache/organization.json`，将 canonical 与 organization 记录迁移到 `哪里有温柔对待阿宅的辣妹！？`，同步所有 alias 与 episode_last_dst 路径。
- **验证**：
  - `python -m pytest tests/test_text_utils.py tests/test_fallback_identification.py -v` → 8 passed。
  - `python -m pytest tests/ -q` → 85 passed。
  - dry-run 在 `F:/test/unit7` 上识别两文件为 `哪里有温柔对待阿宅的辣妹！？\Season01\S01E09/E11`。

## 关键文件变更
- `autoanime/cli.py`
- `autoanime/logging_utils.py`
- `autoanime/naming.py`
- `autoanime/pipeline/main.py`
- `autoanime/apis/bangumi.py`
- `autoanime/identification/local_fallback.py`
- `autoanime/identification/episode_rules.py`
- `autoanime/identification/title_chain.py`
- `autoanime/cache/manual_whitelist.py`
- `tests/test_logging_utils.py`（新增）
- `tests/test_naming.py`（新增）
- `tests/test_fallback_identification.py`（新增）
- `tests/test_bangumi.py`（新增）
- `tests/test_manual_whitelist.py`（新增）
- `tests/test_episode_rules.py`（新增）
- `tests/test_text_utils.py`（新增）
- `scripts/fix_otaku_gal_cache.py`（新增）

## 待办 / 下一步
- [x] 全量 dry-run 回归验证（使用最终配置，临时禁用 OpenAI 避免超时）。
- [x] 清理工作区 diff（已本地提交）。
- [ ] 配置 TMDB token 后验证 TMDB 路径兼容性。
- [ ] 继续补充 `_MANUAL_SEASON_LAYOUT` 其他长篇番剧。
- [ ] 处理 DRY_RUN 模式下 ShowIndex「自愈」反复触发的问题（review_report 遗漏 1）。
- [ ] 建议用户将 `config.ini` 中 `USEOPENAIAPI` 设为 `False`（当前 API 已过期）。

## 最终回归验证结果（第 7 单元）
- **测试**：`python -m pytest tests/ -q` → **85 passed**
- **dry-run**：`python AutoAnimeMv2.py "F:/test/unit7" --output-path "F:/test_out" --dry-run`（OpenAI 临时禁用）
  - 2 个目标文件均收敛到 `哪里有温柔对待阿宅的辣妹！？\Season01\S01E09/E11`
  - 完整跑完，末尾 `一切工作已经完成,用时1.31s`

## 最终回归验证结果（第 6 单元）
- **测试**：`python -m pytest tests/ -q` → **81 passed**
- **dry-run**：`python AutoAnimeMv2.py "F:/下载" --output-path "F:/test" --dry-run`（OpenAI 临时禁用）
  - 完整跑完，末尾 `一切工作已经完成,用时33.47s`
  - `UnicodeEncodeError`：0 次
  - `gbk codec`：0 次
  - `剧名漂移保护：复用已有 CanonicalID`：0 次
  - `剧名漂移保护拒绝`：170 次
  - `BangumiApi查询失败`：250 次（网络/代理问题，非代码问题）
  - `剧名未收敛到中文`：124 次（Bangumi 网络不可达 + TMDB token 未配置）
  - `OpenAI 识别 + 本地回退 + 传统 API 全部失败`：7 次（剧场版/SP）
  - `绝对集数映射`：10 次

## git 提交
- commit: `e7fe0a2` "修复 AutoAnimeMv2 回退链路识别错误"
- 39 files changed, 1388 insertions(+), 1159 deletions(-)

## 已知限制
- 当前环境 Bangumi API 网络不可达，dry-run 中 `BangumiApi查询失败` 为网络错误，非代码问题。
- `config.ini` 中 `TMDB_BEARER_TOKEN` 为空，TMDB 链路被跳过。
- 部分剧场版/SP 文件仍全链路失败，需后续处理。

## 测试数据
- 源目录：`F:/下载`（1388 个视频文件）
- 目标目录：`F:/test`
- 日志参考：`F:/test/dryrun_after_unit*.log`
