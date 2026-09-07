> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# AutoAnime v2 升级计划：RSS 订阅匹配与 DownloadIntent

> 状态：Deferred / Draft
> 记录时间：2026-09-05
> 触发前提：等外部文件识别的整条 `L1 -> L2 -> L3 -> Arbiter` 管线完成并稳定后，再启动本改造。
> 当前基线：PR3 L1 本地解析已完成。

---

## 1. 背景

当前 v2 的识别主线是面向“外部未知文件”：

```text
Unknown File
  -> L1 本地解析
  -> L2 记忆 / alias
  -> L3 LLM 兜底
  -> Arbiter
  -> Organize / Pending
```

但 RSS 订阅场景不能简单理解为“下载完成后再次识别”。

如果 RSS item 已经在下载前匹配到了系统内的订阅番剧，那么系统在发送下载器之前就已经知道：

- 属于哪部番剧
- 第几季
- 第几集或是否是季包
- 字幕组
- 来源
- 画质候选
- 预期目标

因此，这类 Managed RSS 资源在下载完成后不应该再走一次完整的 L1/L2/L3 识别，而应该只做确定性核验后进入 Organize。

真正需要完整识别的是另一类资源：

```text
qBittorrent 等外部工具已经下载完成，但 AutoAnime 没有参与下载决策的文件
```

所以本计划的目标是把识别能力抽成可复用的 RecognitionCore，并让它同时服务两个入口：

```text
1. RSS 订阅匹配入口：Feed Item -> DownloadIntent
2. 外部文件识别入口：Unknown File -> Organize / Pending
```

---

## 2. 目标架构

### 2.1 RSS 订阅匹配链路

```text
Feed Item
  -> L1 本地解析
  -> L2 记忆 / 订阅 / alias 匹配
  -> L3 LLM 兜底
  -> Arbiter
  -> DownloadIntent
  -> Downloader
  -> Download Completed
  -> Managed Verify
  -> Organize
```

### 2.2 外部文件识别链路

```text
Unknown File / Torrent
  -> L1 本地解析
  -> L2 记忆 / alias
  -> L3 LLM 兜底
  -> Arbiter
  -> Organize / Pending
```

### 2.3 关键原则

```text
底层能力复用，编排流程不复用。
L1/L2/L3 是 RecognitionCore 的能力，不是只属于文件导入的固定流程。
RSS 阶段和文件阶段使用不同 context、不同验收阈值和不同后续动作。
Managed RSS 下载完成后不做复杂识别，只做确定性核验。
```

---

## 3. 复用范围

RSS 匹配和外部文件识别应复用以下能力：

- 标题归一化
- 季 / 集 / 剧场版解析
- 字幕组解析
- 分辨率、来源、编码解析
- alias 匹配
- parse_memory
- metadata provider
- LLM client
- JSON schema 校验
- confidence / level
- evidence / missing_fields
- Arbiter
- metrics / parse_events

不建议复用的是整条 Pipeline 编排，因为两者入口不同：

| 场景 | 输入 | 输出 | 后续动作 |
|---|---|---|---|
| RSS 匹配 | Feed Item | MatchDecision / DownloadIntent | 下载 |
| 外部文件识别 | Unknown File | ParsedRelease / MatchResult | Organize / Pending |

---

## 4. RSS 阶段的三层定义

### L1：Feed 本地解析

输入示例：

```text
[ANi] 葬送的芙莉莲 第二季 - 10 [1080P][Baha][WEB-DL][AAC AVC][CHT].mp4
```

输出：

```text
title_candidates
season
episode
segment
fansub
resolution
source
confidence
missing_fields
```

L1 只做本地、零成本解析。

### L2：订阅 / 记忆匹配

L2 在 RSS 阶段不只是文件记忆，还包括：

- 当前订阅列表
- series alias
- parse_memory
- 历史 feed pattern
- 番剧元数据
- 期望下一集
- 字幕组偏好

如果 RSS item 能精确匹配当前订阅，例如：

```text
订阅：Sousou no Frieren
alias：葬送的芙莉莲 / Frieren
expected season：2
next episode：10
```

那么应直接得到高置信度匹配，不需要 LLM。

### L3：LLM 兜底

L3 只处理模糊情况，例如：

- 中文、英文、罗马音别名差异
- 简称与全称不一致
- 第二季 / S2 / 2nd Season 表达差异
- 同名或系列作品混淆
- 新番标题与订阅标题不完全一致

LLM 输出的只是候选和证据，最终是否下载必须由 Arbiter 与规则决定。

---

## 5. DownloadIntent

新增核心概念：`DownloadIntent`。

它表示：

```text
系统主动决定下载某个 RSS item，并且已经知道它应归属到哪里。
```

建议字段：

```text
id
subscription_id
feed_item_id
release_record_id
torrent_hash
series_id
season_id
episode_id            # 单集时有值
is_season_pack        # 季包时有值
fansub
quality_score
decision
state
matched_at
created_at
updated_at
```

状态建议：

```text
pending
matched
downloading
completed
verified
imported
failed
cancelled
needs_review
```

---

## 6. release_record 补充字段

`release_record` 需要区分资源来源：

```text
ingest_mode:
- managed_rss       系统内 RSS 订阅下载
- external          外部已有下载
- manual_import     手动导入本地文件

match_state:
- prematched        下载前已由 AutoAnime 匹配
- inferred          通过 qBittorrent category / tag 弱推断
- unknown           未知来源，需要完整识别
- needs_review      有候选但存在冲突
```

注意：

- 只有 AutoAnime 自己生成的 DownloadIntent 才能是 `prematched`
- qBittorrent 已有标签最多只能是 `inferred`
- 外部未知资源必须走完整识别

---

## 7. qBittorrent 标签策略

qBittorrent 标签只作为索引，不作为事实源。

建议：

```text
category: autoanime
tag: aa:managed
tag: aa:r:<release_record_id>
```

系统内部仍以 SQLite 中的 `DownloadIntent + release_record` 为准。

原因是：

- 用户可能手动修改 qBittorrent 标签
- qBittorrent 标签表达能力有限
- 季包无法简单映射成单个 episode
- 外部资源本来没有 AutoAnime 标签

---

## 8. 下载完成后的 Managed Verify

Managed RSS 资源下载完成后，不应再调用 L1/L2/L3。

只做确定性核验：

1. torrent_hash 是否存在 DownloadIntent
2. release_record 是否是 accepted
3. expected series / season / episode 是否仍有效
4. 是否已有同集文件
5. 是否存在 file_hash 冲突
6. 目标路径是否已存在
7. 文件数量是否合理
8. 扩展名是否是视频 / 字幕
9. 是否存在 sample / extras / spam 文件
10. 季包是否能确定性映射集数

核验通过后：

```text
rename / move / hardlink
episode.state = ORGANIZED
release_record.decision = accepted
audit_log 写入 operation_id
```

核验失败时不自动调用 LLM，而是进入：

```text
pending_queue
reason = managed_verification_failed
```

---

## 9. 季包处理

Managed RSS 中大量资源是季包：

```text
Anime.Title.S02.1080p.Baha.WEB-DL-MWeb
```

因此 DownloadIntent 必须支持：

```text
episode_id
```

和：

```text
is_season_pack = true
```

下载完成后系统需要把多个文件确定性映射到集数：

- `S02E01`
- ` - 01 `
- `[01]`
- `第 01 集`
- `01.mkv`

如果规则全部失败，不调用 LLM，而是进入人工确认：

```text
这个季包含 N 个文件，请确认集数映射
```

---

## 10. Memory 扩展

alias 必须全局共享，因为同一个别名应同时帮助：

- RSS 订阅匹配
- 外部文件识别
- 元数据搜索

`parse_memory` 需要增加 scope：

```text
scope = feed | file
```

唯一键建议调整为：

```text
UNIQUE(scope, key_level, key_hash)
```

原因：

- feed 标题和文件名结构相似但不完全相同
- feed 记忆可能保存 `matched_series_id`
- file 记忆更多保存 title / season / episode 解析结果

---

## 11. 实施阶段

本计划暂缓执行。等以下条件满足后再启动：

```text
外部文件识别 L1 已完成
L2 记忆匹配已完成
L3 LLM 兜底已完成
Arbiter 稳定
外部文件识别测试集全绿
Pending / Confirm 基本闭环
```

然后按以下阶段改造。

### Phase 0：冻结外部识别管线

- 外部文件识别 L1/L2/L3 测试全绿
- 识别结果有稳定 `RecognitionResult`
- evidence / missing_fields 结构稳定
- Arbiter 决策路径有测试覆盖

### Phase 1：抽取 RecognitionCore

把现有能力从文件导入流程中抽成共享核心：

```text
normalizer
episode_parser
fansub_parser
alias_matcher
memory_matcher
metadata_matcher
llm_disambiguator
arbiter
metrics
```

约束：

- 不改变外部文件识别行为
- 现有测试必须继续通过
- 只做重构，不引入 RSS 匹配功能

### Phase 2：新增 Feed 数据模型

新增或完善：

```text
subscription
feed_source
feed_item
download_intent
```

并为 `release_record` 增加：

```text
ingest_mode
match_state
```

### Phase 3：Feed L1

实现 Feed Item 的本地解析。

要求：

- 复用现有 L1 解析能力
- 不调用 LLM
- 不产生下载动作
- 只生成结构化候选

### Phase 4：Feed L2

实现订阅与记忆匹配。

要求：

- 优先匹配当前订阅
- 使用 alias
- 使用 parse_memory
- 支持字幕组偏好
- 支持期望下一集
- 高置信度直接生成候选

### Phase 5：Arbiter 与 DownloadIntent

实现：

- 阈值判断
- 字幕组 / 画质规则
- 重复资源判断
- 放送进度校验
- 季包 / 单集判断
- 生成 DownloadIntent

低置信度不下载，进入：

```text
pending_queue
pending_type = download_match
```

### Phase 6：Feed L3

只在 L1/L2 无法命中时调用 LLM。

要求：

- 复用现有 LLM client
- 复用槽位轮换 / 熔断 / retry
- 使用独立 prompt
- 输出严格 JSON
- 记录 cost / latency / confidence
- LLM 只给候选，不直接触发下载

### Phase 7：下载器与 Managed Verify

实现：

- qBittorrent / aria2 发送下载
- 自动打 category / tag
- 下载完成事件监听
- 根据 torrent_hash 找回 DownloadIntent
- Managed Verify
- 确定性导入
- 审计与回滚

### Phase 8：外部资源兼容

处理 qBittorrent 中已经存在的资源：

```text
完全未知 -> 外部识别 L1/L2/L3
有 category 可推断 -> inferred / 轻量识别
有旧标签 -> 只作为 inferred，不当作 prematched
```

### Phase 9：UI 收敛

WebUI 一级导航收敛为：

```text
工作台
番剧
下载与导入
处理中心
设置
```

处理中心内部包含：

```text
待确认下载
待确认导入
冲突
季包映射失败
记忆
诊断 / 识别轨迹
```

### Phase 10：指标与回归

新增指标：

- feed item 总数
- feed L1 命中率
- feed L2 命中率
- feed L3 调用率
- 自动下载率
- 待确认下载率
- 错误下载率
- managed verify 成功率
- managed verify 失败率

所有指标应基于 `parse_events` / `download_intent` / `release_record` 可复算。

---

## 12. 验收标准

改造完成时必须满足：

- RSS item 能通过 L1/L2/L3 匹配到订阅番剧
- 高置信度 item 能生成 DownloadIntent
- DownloadIntent 能发送到下载器
- qBittorrent tag / category 能与 SQLite 记录互相索引
- 下载完成后能根据 hash 找回 DownloadIntent
- Managed RSS 资源下载完成后不进入完整识别
- Managed Verify 只执行确定性核验
- 外部未知资源仍走完整识别
- alias 在 RSS 和文件识别中共享
- parse_memory 支持 `feed | file` scope
- 所有自动/人工决策都有 audit_log
- 所有测试、类型检查、lint 全绿

---

## 13. 风险与边界

### 风险 1：RSS 匹配错误导致错误下载

对策：

- RSS 阶段阈值高于文件识别阶段
- LLM 只给候选，不直接触发下载
- 中低置信度进入待确认下载

### 风险 2：重复实现两套识别

对策：

- 抽取 RecognitionCore
- Feed 和 File 只保留不同编排器

### 风险 3：Managed Verify 过度设计

对策：

- 第一版只做确定性规则
- 不做 AI 判断
- 失败一律进入人工队列

### 风险 4：季包集数映射错误

对策：

- 只使用确定性规则
- 无法映射时进入人工确认
- 不用 LLM 猜测

### 风险 5：qBittorrent 标签被用户修改

对策：

- qBittorrent 标签只作为索引
- SQLite 记录是事实源
- 标签丢失时可人工重新绑定，不自动猜测

---

## 14. 当前决定

本计划只作为升级记录。

当前不立即实施。
先完成并稳定外部文件识别的：

```text
L1 -> L2 -> L3 -> Arbiter
```

之后再将 RSS 订阅匹配改造成：

```text
Feed Item -> L1 -> L2 -> L3 -> Arbiter -> DownloadIntent
```

并将下载完成后 managed 资源的处理收缩为确定性核验。
