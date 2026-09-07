> 归档状态：2026-09-07 归档。这是调研期参考资料；只记录设计思想和协议边界，不代表当前仓库实现承诺。

# REFERENCE - 开源案例逐项调研(2026-09-05)

> 2026-09-05 二次调研:元数据(星数/协议/活跃度)经 GitHub API 实测刷新,扫描脚本 `scripts/scan_reference_repos*.py` 可复跑。M4 专项(洗版/硬链接/订阅)新增 §7-§8,协议提示见 §9。

> 结论先行:AutoBangumi 3.3 已内置多供应商 LLM 解析器(OpenAI/Claude/Gemini, primary/fallback)。
> "LLM 兜底"本身不是差异化;**本地方言解析优先 + 置信度路由 + LLM 只做最后兜底(省 token/低延迟/中文场景)** 才是。

## 1. Auto_Bangumi — 8.2k⭐,Python,最直接竞品

- 仓库布局:`backend/ + webui/ + plugins/ + e2e/ + DESIGN.md`
- **学实现**:
  - RSS 解析器 → 自动生成下载规则;季中追番自动补全遗漏集
  - 多供应商 LLM 解析器,primary/fallback 模式(⚠️ 我们必须避开它的 LLM-primary 路线)
  - SSE 事件流驱动 WebUI(替代轮询);全异步后端;程序内更新 + sha256/ed25519 签名校验
  - 首次运行 7 步设置向导
- **学 UI**:`webui/` = Vue3 + Naive UI + Pinia + Vite + vue-i18n + Storybook + vitest + PWA;
  DESIGN.md 定义 "Soft Ink" 视觉系统(CSS 变量 token、明暗双主题),**反模式清单明确拒绝 AI 味设计**:
  粉彩 pill 徽章、圆角方块图标空状态、发光状态点、彩色大底警报框
- **对我们**:M1 抄仓库布局;M2 定位差异 = local-first vs 它的 LLM-primary;M3 抄 Naive UI 栈 + SSE + DESIGN.md 模式

## 2. ani-rss — 3.5k⭐,Java,**GPL-2.0**

- 功能闭环最全:RSS→订阅→下载→刮削→**洗版**(同集更高质量自动重下)
- **学实现**:洗版机制(本项目的硬需求,见 ARCHITECTURE.md 洗版引擎);多模块工程(application + ui)
- **学运营**:独立文档站(docs.wushuo.top)是获客利器;README 放真实截图;TG 社区
- 文化注意:**明确不接受纯 AI 生成的 PR**——本项目提交信息必须认真写,展示人在主导

## 3. MoviePilot — 11.7k⭐,Python + Vue3,**GPL-3.0**,活跃(2026-09-05 仍在推)

- **学实现**:插件市场架构(官方仓库 + 第三方生态);严格 commit 规范(带 suppressions 的 eslint 体系)
- **M4 洗版/整理核心模块(2026-09-05 实测目录)**:
  - `app/chain/transfer/` 按职责切 16 个文件:`plan`(迁移计划)/`execution`(执行)/`checkpoint`(断点)/`retry`(重试)/`settlement`(结算)/`history`(历史)/`records`(记录)/`format`(命名格式)/`filter`/`scrape`/`queue`/`contract`/`facade`/`workflow` —— **正对位我们 organize/ 的 naming/mover/upgrade/rollback 四件套**,读它学职责切分,不抄代码(GPL)
  - `app/modules/filemanager/` = 存储抽象(`storages/` 子目录,本地/云盘统一)+ `transhandler.py` 传输处理;**硬链接整理的现成答案**(link→改名,做种原件不动,跨盘降级 copy)
  - `app/chain/storage.py` 存储链
- **学 UI**:Vuetify + Pinia + **@vue-flow**(节点流图)→ M3 用 vue-flow 把三级识别管线画成可视化流程图,
  每级显示命中率统计,是最强的 demo 画面;fullcalendar(追番日历,可入 backlog)
- 远期 backlog:插件市场

## 4. Overseerr — 5k⭐,TypeScript

- UI 打磨标杆:暗色主题、卡片网格、状态徽章、分组设置页、请求流转 UX
- 用途:M3 前端视觉参照(抄布局语言,不抄代码,它是 React/Next 栈)

## 5. Sonarr — 15.4k⭐,C#,**GPL-3.0**,活跃

- 领域模型教科书:Series / Season / Episode 三层 + 状态机 + 质量画像 + monitoring(追更开关)
- **M4 洗版思想(读设计不读代码)**:
  - **Custom Formats 评分制**:每个 release 按规则(分辨率/编码/字幕组/语言)打分,升级判定 = 新 release 分数 > 现有 + 阈值 —— 我们洗版引擎评分器的成熟参照
  - **Upgrade Eligible 判定**:episode file 状态机(HardlinkCutoff/Upgradable/CutoffNotMet),缺集回补(Missing Episodes Search)对照预生成集表找缺口 —— 正是我们"订阅预生成全季集表 + 每周校准"的已有实现
- 用途:M1 定义内部 schema 直接对标,比自造模型专业,面试讲数据建模有出处

## 6. anitopy — 77⭐,Python,**MPL-2.0**(igorcmoura/anitopy)

- anitomy 的 Python 移植;对中文剧名/字幕组命名覆盖弱
- 用途:M2 的 L1 解析器;短板即 L2/L3 存在的理由,用真实中文文件名测试集量化它

## 7. BGmi — 1.0k⭐,Python,**MIT**,活跃(uv 工程,在找 maintainer)

- 轻量 Bangumi RSS 订阅闭环:订阅 → RSS 拉取 → 过滤 → 下载,CLI + Web UI;仓库极小(`bgmi/` 单包),半天可读完
- **MIT 可代码级借鉴**,是 M4 RSS 订阅调度快速起步的最佳样本
- 风险提示:官方在招 maintainer,引用时注意别学它停更的坑

## 8. FlexGet — 2.0k⭐,Python,**MIT**,活跃

- RSS 条目处理管线组件库(`flexget/components/`):`series`(剧集解析+quality 过滤)/`parsing`/`backlog`(未匹配条目重试)/`seen`(去重)/`rejected`(拒绝记录)/`estimate_release`(放送时间预估)/`history`
- accept/reject 语义与我们三档置信度路由同构;`estimate_release` 对应"无 air_date 不拦截只降级"
- MIT:需要小段代码级借鉴时的安全选项

### n8n(补充到 §3 的 M3 备注)

- n8n-io/n8n 203k⭐ 但是 **fair-code 非开源协议**,只看 UI 交互模式不抄任何代码;管线画布参照它的 React Flow 节点卡片布局与运行状态流。Langflow(MIT, 154k⭐)与 xyflow 官方 examples(MIT)是更安全的画布参照

## 9. 协议提示(引用纪律)

| 项目 | 协议 | 可否代码级借鉴 |
|------|------|---------------|
| BGmi / FlexGet / Auto_Bangumi / Langflow / xyflow | MIT | ✅ 可 |
| anitopy | MPL-2.0 | ✅ 文件级(改动的文件需开源该文件) |
| MoviePilot / Sonarr / Radarr | GPL-3.0 | ⚠️ 只学思想与数据模型,不逐行移植 |
| ani-rss | GPL-2.0 | ⚠️ 同上 |
| n8n | fair-code | ❌ 只看交互,不抄代码 |

- 面试展示 + 攒真实用户定位:借**数据模型、状态机、策略思想**,代码自写;确需移植从 MIT 表内选。

## 定位声明(写进 README/简历)

> 对比 AutoBangumi(8.2k⭐)的 LLM-primary 方案:AutoAnime 采用 local-first 三级管线,
> anitopy 本地解析 + 规则记忆沉淀 + LLM 仅兜底,LLM 调用降低 X%,token 成本趋近于零,
> 且 LLM 结果自动沉淀为规则记忆,越用越省。
