> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# AutoAnime 第一代产品 · 最终交付报告

> 交付日期：2026-09-06 · 基线：v2 @ `be2e37a` · 主会话出具
> 结论：**第一版产品功能完整、七轮真实数据测试通过、WebUI 浏览器实测通过，达到交付标准。**

---

## 1. 产品是什么

AutoAnime 是**本地优先（local-first）的番剧库自动化工具**：

```
订阅(Mikan RSS) / 手动导入
      ↓
L1 本地规则解析(anitopy 方言) ── HIGH ──────────────┐
      ↓ MEDIUM                                      ↓
L2 记忆命中(parse_memory 两级键 + title_aliases 别名) │ 硬链接归档
      ↓ miss                                        │ (D17 命名,D18 字幕跟随)
前置消歧(alias 表 → Bangumi/TMDB 参考链→canonical)   │ 做种原件保留(D21)
      ↓ miss                                        ↓
L3 LLM 兜底(机会主义合批,省 84.1% 调用) ──→ 仲裁 ──→ 洗版评分闸门 → 错配 A/B/C 恢复
      ↓
WebUI(8 页面,React 19 + xyflow 管线可视化 + SSE 实时事件)
```

技术栈：Python 3.12 + uv + FastAPI + async SQLAlchemy 2.0 + SQLite 单库；React 19 + Tailwind 4 + TypeScript strict + @xyflow/react。**零外部服务依赖**（无 Redis/Postgres），单进程（FastAPI + APScheduler，D16）。

## 2. 交付范围与完成度

| 模块 | 内容 | 验证方式 |
|---|---|---|
| L1/L2/L3 三级识别管线 | PR1-PR5 | 1039+ 离线单测 + 快照回归 |
| 参考源归一化（PR7） | 别名窄表 + 前置消歧 + confirm 回填 | 罗马音/拼音/别名实测归一 |
| 机会主义合批（E1） | 同目录+同字幕组 ≥5 凑批 | 快照实测 LLM 调用 **-84.1%** |
| FastAPI + SSE（E2） | 22 端点 + 事件流/回放/认证 | 集成冒烟 + 浏览器实测 |
| WebUI 8 页面（E3） | Dashboard/Pipeline/Library/Subscriptions/RSS/Pending/Logs/Settings | **浏览器逐页走查**（本报告 §5） |
| M4 闭环（E4） | RSS 调度 + qB/aria2 网关 + 洗版 + 错配恢复 + 通知 + compose | 假 qB 全链路 + 真 qB 只读冒烟 |
| CLI | init-db/parse/import/queue/confirm/subscribe/rerun/report | 真实文件端到端 |

代码规模：后端 1058 tests、前端 67 tests，全绿；ruff/pyright/tsc/eslint 四门禁零错；迁移链 0001→0006 可往返。

## 3. 三轮真实数据测试（核心验证）

测试环境：E 盘测试区（与下载区同盘保证 hardlink），7 组特征样本（整季包/同番双来源/拼音命名/外传后缀/单文件），真实 LLM（deepseek-v4-flash-ga-260731，火山方舟）+ 真实 TMDB/Bangumi（经代理）。

### 3.1 端到端能力实证（全部通过）

| 能力 | 实证 |
|---|---|
| 识别→中文名归一 | 拼音名(死神)、罗马音(葬送的芙莉莲)、外传(Wan)全部归一；Bleach DSNP 与 LINETV 双来源归到同一权威名 |
| 硬链接 | `fsutil hardlink list` + `samefile` 双实证，nlink=2，做种原件 100% 保留 |
| 归档命名 | `碧蓝航线：微速前行！/Season 02/碧蓝航线：微速前行！ - S02E06.1080p.mkv`（D17 逐字段） |
| 字幕跟随 | 同包 .ass 跟随改名（.zh 后缀保留）入归档目录（D18） |
| 学习闭环 | confirm 后同番文件重导 **route=memory 零 LLM 外呼**（冷启 ~50s/条 → 0.76s）；跨集命中（L1 草稿形状作 user-asserted alias） |
| 订阅闭环 | RSS 拉取→季级对齐→择优→PICKED→(完成)→fast_path 归档全链路；错标条目拒绝落库 |
| 洗版升级 | 真实触发：quality 9→10，upgraded_count+1，threshold_not_met/allowed 决策链完整，做种原件保留（D21） |
| 错配 A/B/C | A 改挂零重下+原集回 MISSING；B 隔离+pending+证据链；C 回补预算 2 次超限转人工+hash 拉黑 |
| 通知 | webhook 真实 HTTP 外发（白名单过滤验证） |
| qBittorrent | 远程真连只读冒烟通过（分类隔离，零写操作） |
| 优雅降级 | LLM 401 → L1 结果保留 + degraded 标记不 crash；下载器不可达 → note 不 crash；参考源 miss → 负缓存 |

### 3.2 测试揪出的缺陷（19 项，全部修复+回归测试）

**只有真实测试才能暴露的**：
1. 下载完成早于首次采样 → 状态机卡死（任务永不上架）
2. organize 把集号当 episode id 用 → **多季订阅全错位**（单番测试侥幸掩盖）
3. import 目标位不查占用 → 同番三源互踩、**绕过洗版评分闸门**
4. parse_events 无生产写入路径 → 指标恒 0
5. 回滚学习旁路死代码（字段名漂移）
6. 网关 ping() 方法名漂移（单测 fake 同名掩盖）
7. A 改挂后原集谎报 DOWNLOADED（挡死回补）
8. pending 人工操作 audit actor 恒 auto → 人工介入率恒 0
9. 确认学习未绑定 L1 草稿形状 → 跨集无法命中

**WebUI 浏览器实测揪出的**（离线单测因注入 fake 完全掩盖）：
10. EventStreamProvider 未接线 api 层工厂 → **生产装配下 SSE 永不连接**
11. 后端命名事件(event: parse/…)用 onmessage 接收 → **连接成功但零事件到达**

**产品化补齐**：
12. CLI run/import/queue 空壳接线（手动导入是头号验收场景）
13. import 重跑幂等（already-archived/pending 跳过，防 IO/audit 膨胀）
14. StorageMemoryStore 透传 find_alias_key（PR7 生产装配缺口）

### 3.3 第 4-5 轮真实数据测试（2026-09-06 补充，全新命名风格 + 学习回归）

**第 4 轮**（r4c/r4d 库，180s LLM 超时）：从 Z:\下载 换入前三轮未覆盖的命名形态——中文方括号字幕组整季包（乡下大叔成为剑圣/骸骨骑士大人异世界冒险中/摩绪）、LoliHouse 散文件（Rakudai 06/08）、简繁内嵌同番双版本（非人学生 E12 简体+繁体）、中文+罗马音混合+招募标记（云光芙莉莲 S2E10），共 8 项 5.7G。

- 识别实证：中文方括号包名、LoliHouse fansub（smzase&LoliHouse）、简繁字形全部正确解析（fansub 不再出现括号残渣）；MAO（摩绪）L1 HIGH 直通归档并 hardlink（fsutil 实证 nlink=2，做种原件保留）
- 传输失败日志可观测性：deepseek-v4-flash 为推理模型，60s 超时边缘超时（实测 19s～60s+ 波动），调 180s 后全量成功；日志修复后可直接区分 ReadTimeout/额度尽

**揪出并修复 4 项（15-18，全部带回归测试）**：
15. L1 fansub 括号残渣：结构锚点在方括号内部（`[H264 8bit 1080P]`）时 tail=`]` 被当字幕组名
16. L3 transport 失败日志只打异常类名——超时/额度尽无法区分（运维轮换模型的关键依据）
17. import 计数口径：目标位占用跳过（幂等）被计入 failed 虚报失败
18. CLI confirm 只学习不 resolve pending 行（与 WebUI 确认语义不一致，确认后队列永不减少）

**第 5 轮**（同批重导 r4d 库，学习回归）：7/8 走 memory 路由**零 LLM 外呼**；season 从记忆补齐（Rakudai L1 None→S1）；failed=0（17 项修复实证）；7/7 confirm 后队列全部 resolved（18 项修复实证）。

**模型切换实证**（额度轮换预案，同一火山方舟 key）：doubao-seed-2-1-turbo-260628（29s，直接输出中文权威名）、glm-5-2-260617（13s，罗马音名走参考源归一）均可用。

**第 6 轮**（r6 库，A1'/B1 拍板落地验证）：第 4 轮同番兄弟集 + 全新番（海贼王 One Piece S1E1170，L1 HIGH 直通归档零 LLM）。
- 拍板 A1'（确认名精确覆盖）+ B1（confirm 学习键参考源归一）+ D1（README HIGH 语义如实化）已实现合入 v2
- 实测：乡下大叔重导 title `.Katainaka...2026.` → **乡下大叔成为剑圣**；骸骨骑士同理；Bangumi 别名（乡里别剑圣/日文/英文名）全部归一；简繁键因 Bangumi miss 如实回退（非 bug）；重导 llm_cache 恒 5 行=零新外呼
- 揪出并修复 1 项（19）：确认名覆盖被 arbiter 逐字段仲裁打回（memory 证据 rank 低于 name）——EVIDENCE_PRIORITY 新增 confirmed 置顶
- **确认归档通路落地**（第 7 轮实测，§6.1 首要补齐项闭环）：CLI confirm 与 WebUI confirm/correct 共用 `organize/confirm_archive` 单一实现，确认即 hardlink 入库；r6 实测 4 项确认全部落库（`乡下大叔成为剑圣/Season 02/... - S02E01.1080p.mkv`，nlink=2）；重导被 already-archived 幂等桶放行

**第 7 轮**（r7 库，纯点分隔命名全新番）：Dara-san of Reiwa / Elegy for the Henchmen / GROW UP SHOW -Sunflower Circus- / Jaadugar A Witch in Mongolia / KAMUI Hes Behind You / Love Unseen Beneath the Clear Night Sky / Mushoku Tensei S03 / My Stepmother / KAIJU GIRL CARAMELISE（全大写）共 9 项。
- **8/9 L1 HIGH 直通归档**（零 LLM）：S03 季号、长名多词、连字符括号变体全部正确解析归档
- 全大写 KAIJU 按设计降 MEDIUM → L3 → confirm → **确认即归档**（`怪兽女孩卡梅丽斯/Season 01/`，nlink=2）+ manual alias（kaiju girl caramelise→怪兽女孩卡梅丽斯）；parse 兄弟集 E05 实证 **title 归一为确认名（evidence=confirmed）、fansub 记忆补出、零外呼**——A1'+B1+确认归档三条新通路的闭环实证
- 第 4-6 轮修复全部在位（无新缺陷）：fansub 无残渣、failed=0、队列 resolve 正常

**确认为设计而非 bug**：memory 融合只补缺不覆盖（confirmed 例外：用户确认过的事实）；简繁双键在参考源 miss 时回退为两次 confirm（B1 行为）。

## 4. 指标汇总

| 指标 | 数值 |
|---|---|
| 离线测试 | 1071 backend + 67 frontend，全绿（第 4-6 轮累计新增 13 项回归测试） |
| 2606 条快照回归 | l3 fallback 2233→1；memory 路由 0→2232；archive 恒等 |
| 合批收益 | LLM 调用 -84.1%（1878 次/2606 条） |
| 学习闭环收益 | 二次导入 0 外呼（-100%） |
| 单条管线延迟 | p50 6.4ms / p95 9.0ms（离线口径） |
| D10 自检 | compose 结构 6/6 ✅；订阅闭环全链路 ✅（离线等价）；库存指标 ✅；Pipeline 实时流动 ✅（浏览器实证） |

## 5. WebUI 浏览器实测（主会话亲自执行）

- 八页全部走查：真实数据渲染逐页核对（Dashboard 指标=库内实况；Library 中文权威名+质量分+缺集徽标；Logs 完整审计历史分组；Settings 运行时开关+密钥不回显）
- **交互实测**：Pending 页纠正表单提交 → 学习三件套落库 → 队列 4→3 → **SSE 事件 1 秒内实时到达 Pipeline 页**（修复后）
- 深色主题切换正常（Soft Ink 双主题）
- 认证四态：无 token 401 / 错 token 401 / X-API-Token 200 / ?token= 200（EventSource 兼容）
- 截图存档：notes/screenshots/01-dashboard.png ~ 06-settings-dark.png

## 6. 已知边界（如实告知，均不影响核心闭环）

1. **~~confirm/correct 只学习不归档~~ 已补齐（2026-09-06 第 7 轮实测）**：confirm/correct（CLI 与 WebUI 共用 confirm_archive 通路）确认即 hardlink 入库——r6 实测乡下大叔/骸骨骑士/非人学生确认后直接落库（确认名命名、nlink=2、原件保留）；文件不在位时审计如实记原因
2. rollback 文件反操作 v1 记 skipped（parse_memory 恢复正常工作；organize 域反操作引擎已可用未接 API）
3. settings 无持久化表（PUT 进程内生效，重启回 env/toml）
4. LLM HIGH 不自动归档（l3:high 入队人工）——契约行为，产品可议
5. "Anime." 前缀季包名致参考源首查 miss（confirm 一次后由记忆/别名兜底）
6. docker compose 结构校验通过，实机 docker 验证待用户环境
7. 真实 Mikan 订阅 + 真 qB 添加种子未实测（属外发动作，需用户当次确认；网关真连形态已只读冒烟）

## 7. 交付物清单

- 代码：v2 分支（github.com/ymxc152/AutoAnime），全部任务分支已推送
- 测试：三轮真实测试报告（会话记录）+ 本报告 + notes/第一版验收测试Plan.md
- 文档：README/README_en/LICENSE(MIT，GPL-3.0 已替换)/docs/DEPLOY.md/docs/FAQ.md——全部已合入 v2 @ ea73604
- 截图：notes/screenshots/（6 张）
- 测试基础设施：E:\AutoAnimeTest（rotate_samples.sh 三轮样本轮换、env.sh、三份测试库）

## 8. v2 转正建议（D8）

第一版验收通过，建议执行转正包：v2 合并 README/docs → main 归档 → 指向 v2 → 发布。剩余 backlog（主动搜索 indexer、LLM HIGH 自动归档开关、settings 持久化、4K 评分档）建议记入 v2 迭代计划；confirm 归档通路已落地（§6.1）。
