# AutoAnime 识别错误修复方案（修订版 v2）

## 1. 问题归纳

### 1.1 真实数据测试结果
在 `F:\下载` 全量 dry-run（747 个文件）中：
- **未收敛到中文目录：109 条**
- **全链路失败跳过：2 条**
- 高频错误英文目录：
  - `Isekai-Nonbiri-Nouka-2`：12 条 → 应为「异世界悠闲农家」
  - `Maid-san-wa-Taberu-Dake`：12 条 → 应为「女仆小姐的贪吃日常」
  - `Otonari-no-Tenshi-sama-ni-Itsunomanika-Dame-Ningen-ni-Sareteita-Ken-2`：10 条 → 应为「关于邻家的天使大人不知不觉把我惯成了废人这档子事」
  - `Aishiteru-Game-wo-Owarasetai`：7 条 → 应为「想结束这场“我爱你”的游戏」
  - `Ookii-Onnanoko-wa-Suki-Desuka`：7 条 → 应为「你喜欢高大的女孩子吗？」
  - `Ikoku-Nikki`：3 条 → 应为「异国日记」
  - `Ichijyoma-Mankitsu-Gurashi`：3 条 → 应为「一叠间漫画咖啡厅日常」
  - `Otonari-no-Tenshi-sama-2`：3 条 → 应为「关于邻家的天使大人不知不觉把我惯成了废人这档子事」
  - `Arne-no-Jikenbo`：2 条 → 应为「阿涅斯事件簿」
  - `Akane-banashi`：2 条 → 应为「落语朱音」
  - `Chitose-kun-wa-Ramune-Bin-no-Naka`：2 条 → 应为「千岁君在波子汽水瓶中」
  - `Saikyou-no-Ousama--Nidome-no-Jinsei-wa-Nani-o-Suru`：2 条 → 应为「最强王者的第二人生」
- 低频错误英文目录：
  - `VIRGIN-PUNK`、`Odayaka-Kizoku-no-Kyuuka-no-Susume.`、`Kuranika`、`Kirei-ni-Shite-Moraemasu-ka`、`Champignon-no-Majo`、`Bungou-Stray-Dogs-Wan!`、`Otome-Kaijuu-Carameliser`、`Odayaka-Kizoku-no-Kyuuka-no-Susume.` 等。

### 1.2 用户指定格式
以下两种格式应识别为 **"哪里有温柔对待阿宅的辣妹！？"**：
1. `六四位元字幕組★哪裡有溫柔對待阿宅的辣妹！？ Otaku ni Yasashii Gal wa Inai★09★1920x1080★AVC AAC MP4★繁體中文[ 組慶四週年]`
2. `【今晚月色真美】[没有辣妹会对阿宅温柔！？ / オタクに優しいギャルはいない!? / Otaku ni Yasashii Gal wa Inai!?][11][WEBrip][1080P][简日内嵌]`

实测发现：
- 格式 1 经现有清洗后得到 `哪里有温柔对待阿宅的辣妹`（key = `哪里有温柔对待阿宅的辣妹`）。
- 格式 2 经现有清洗后得到 `没有辣妹会对阿宅温柔`（key = `没有辣妹会对阿宅温柔`）。
- 两种格式都会保留英文 romaji，key = `otakuniyasashiigalwainai`。

### 1.3 缓存污染
第一轮 dry-run 中大量错误英文目录已被写入 `titles.json` / `organization.json` 的 canonical/alias 索引，形成"错误自证"。需要先备份并清空这些缓存，再跑新一轮 dry-run。

## 2. 根因分析

1. **API 失效**：OpenAI 计划过期、Bangumi 网络不可达、TMDB token 未配置，识别完全依赖 `local_fallback`。
2. **回退链路兜底不足**：`local_fallback.py` 在 BGM/Bangumi/TMDB 均失败后进入 `local_rules_only`，直接使用文件名截断后的英文/罗马音 RAWName 作为目录名。
3. **手工白名单覆盖不足**：`manual_whitelist.py` 默认只有 10 余条，无法覆盖真实数据中大量英文名和中文别译。
4. **日文假名别名无效**：`Auxiliary_NormalizeAliasKey` 会 stripping 假名，导致 `オタクに優しいギャルはいない` 归一后只剩零星汉字，无法作为稳定别名键。应使用英文 romaji 作为别名键。
5. **同一作品多个中文译名未 canonical 统一**：例如 `没有辣妹会对阿宅温柔` 与 `哪里有温柔对待阿宅的辣妹` 指向同一作品，需要白名单映射到统一主名。

## 3. 修复方案

### 3.1 扩展手工白名单（主要修复）
在 `autoanime/cache/manual_whitelist.py` 的 `DefaultWhitelist` 中新增/调整以下映射：

```python
# 高频英文/罗马音
'isekainonbirinouka': '异世界悠闲农家',
'isekainonbirinouka2': '异世界悠闲农家',
'maidsanwataberudake': '女仆小姐的贪吃日常',
'otonarinotenshisama': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
'otonarinotenshisama2': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
'otonarinotenshisamaniitsunomanikadameningennisareteitaken': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
'otonarinotenshisamaniitsunomanikadameningennisareteitaken2': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
'aishiterugamewoowarasetai': '想结束这场“我爱你”的游戏',
'ookiionnanokowasukidesuka': '你喜欢高大的女孩子吗？',
'ikokunikki': '异国日记',
'ichijyomamankitsugurashi': '一叠间漫画咖啡厅日常',
'ichijyomamankitsugurashi': '一叠间漫画咖啡厅日常',
'arnenojikenbo': '阿涅斯事件簿',
'akanebanashi': '落语朱音',
'chitosekunwaramunebinnonaka': '千岁君在波子汽水瓶中',
'saikyounoousamanidomenojinseiwananiosuru': '最强王者的第二人生',
'saikyounoousamanidomenojinseiwananiwosuru': '最强王者的第二人生',
'终末起点': '最强王者的第二人生',

# 用户指定作品（需要同时覆盖英文、两种中文译名）
'otakuniyasashiigalwainai': '哪里有温柔对待阿宅的辣妹！？',
'哪里有温柔对待阿宅的辣妹': '哪里有温柔对待阿宅的辣妹！？',
'没有辣妹会对阿宅温柔': '哪里有温柔对待阿宅的辣妹！？',

# 低频/剩余
'virginpunk': '处女朋克',
'odayakakizokunokyuukanosusume': '优雅贵族的休假指南',
'kuranika': '和班上第二可爱的女孩子成了朋友',
'kireinishitemoraemasuka': '能帮我弄干净吗',
'champignonnomajo': '蘑菇魔女',
'bungoustraydogswan': '文豪野犬 汪！',
'otomekaijuucarameliser': '乙女怪兽卡列尼策',
'needygirloverdose': '主播女孩重度依赖',
```

> 注：所有 key 均经过 `Auxiliary_NormalizeAliasKey` 处理，因此英文大小写、连接符、空格、季号后缀不影响匹配。新增映射前请先用脚本验证 key。

### 3.2 不修改 `Auxiliary_UniformOTSTR`（审核结论）
原计划保留全角 `？`，但审核发现：保留后格式 1 会产生中文+英文粘连段，反而需要同步增强 `Auxiliary_CleanFallbackTitle` 的边界截断。为避免引入新风险，**本次不改 `UniformOTSTR`**，改为通过白名单中文别名兜底。

### 3.3 清理错误缓存
1. 备份 `.cache/` 目录（复制整个目录到 `.cache.bak/` 或按日期备份）。
2. 删除 `.cache/titles.json` 与 `.cache/organization.json`（清空错误 canonical/organization 记录）。
3. 保留 `manual_title_whitelist.json`（如存在且包含用户自定义条目，执行器应读取并合并）。
4. 跑一轮 dry-run，让白名单重新建立正确的 canonical 记录。

### 3.4 单元测试补充
1. `tests/test_manual_whitelist.py`：新增上述所有 key 到中文的断言。
2. 新增 `tests/test_local_fallback_specific_formats.py`：mock 掉 BGM/Bangumi/TMDB，把用户给的两种完整文件名传给 `Auxiliary_ResolveFileInfoWithFallback`，断言最终 `FinalName == '哪里有温柔对待阿宅的辣妹！？'`。
3. 新增/扩展 `tests/test_local_fallback.py`：验证 API 全失败时，回退链路能命中白名单。

### 3.5 真实数据回归验证
修改后重新执行：
```bash
# 临时禁用 API
# config.ini: USEOPENAIAPI=False, NETERRRECTRYTIMS=0
PYTHONIOENCODING=utf-8 .venv/Scripts/python AutoAnimeMv2.py "F:/下载" --output-path "F:/test" --dry-run > F:/test/dryrun_realdata_round2.log
```

对比指标：
- 未收敛到中文目录数量从 109 降到目标 ≤ 10。
- 用户指定的两种格式均收敛到 `哪里有温柔对待阿宅的辣妹！？`。
- 全链路失败跳过数量从 2 降到 0。

## 4. 实施顺序（TDD 小步）

1. **单元 1**：扩展 `manual_whitelist.py` 默认白名单 + 跑 `tests/test_manual_whitelist.py`。
2. **单元 2**：新增用户指定格式集成测试（mock API 全失败）+ 跑通。
3. **单元 3**：新增/扩展 `local_fallback` 白名单兜底测试 + 跑通。
4. **单元 4**：备份并清空 `.cache/titles.json` / `.cache/organization.json`。
5. **单元 5**：真实数据 dry-run 回归，生成对比报告。
6. **单元 6**：若仍有未收敛条目，补全白名单后重复单元 1/5，直到达标。
7. **单元 7**：最终全量 dry-run 确认，git 提交。

## 5. 验收标准
- `tests/test_manual_whitelist.py` 通过。
- 新增用户指定格式集成测试通过。
- `F:\下载` 全量 dry-run 未收敛中文目录 ≤ 10 条。
- 用户指定的两种格式均识别为 `哪里有温柔对待阿宅的辣妹！？`。
- 没有文件被移动到 `F:\动漫库`。
- 本地 git 提交一条中文 message。

## 6. 审核意见摘要
- 原方案对全角 `？` 的处理会引入中文/英文粘连风险，改为白名单中文别名兜底更安全。
- 必须补全中文别名、短标题变体（如 `otonarinotenshisama`）、低频英文目录，否则无法达到 ≤10 目标。
- 缓存清理建议先备份再删除 `titles.json` / `organization.json`，而不是用 `--rebuild-from-organization`（会加固旧错误）。
- `左撇子艾伦` 已中文命名仍失败，原因待单独排查，不塞进白名单。
