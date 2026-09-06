# FAQ（常见问题）

简短速查。部署与配置全集见 [DEPLOY.md](DEPLOY.md) 与 `.env.example`。

## LLM key / 额度用尽 / 换模型？

L3 是**单模型配置**，没有多模型回退链：`AUTOANIME_LLM_ENABLED` +
`AUTOANIME_LLM_BASE_URL` / `AUTOANIME_LLM_API_KEY` / `AUTOANIME_LLM_MODEL`。
额度用尽或要换模型时，改 `AUTOANIME_LLM_MODEL`（跨供应商则连
`BASE_URL` / `API_KEY` 一起改）后重启进程即可。

额度耗尽不阻塞主流程：L3 段传输失败会把该轮标记 `degraded`，L1/L2 结果
原样保留、归档照常，低置信度照常进待确认队列。把
`AUTOANIME_LLM_ENABLED=false` 则 L3 整段关闭——这是设计好的优雅降级，
不是故障。

**传输失败的日志会写明具体原因**（超时 / HTTP 状态 / 网络错误，已脱敏），
可据此区分「额度尽」和「超时」。**推理型模型（如 deepseek-v4-flash）默认
响应慢**，`AUTOANIME_LLM_TIMEOUT_S=60` 可能不够（实测同一 prompt 19s～
60s+ 波动）：日志出现 `ReadTimeout` 时把它调大到 180 再试，不必急着换模型。

## 为什么 MEDIUM 要人工确认？

仲裁器只自动归档「完全可信」的结果（如 L1 HIGH 直通，或 title+season
三方一致升档到 HIGH）。终审仍是 MEDIUM 意味着证据只对上了一部分
（例如只有 LLM 的结果而 L1 缺席）——自动归档写错会污染媒体库，而且
错误结果还会被识别记忆学习放大。宁可入待确认队列。

在 Pending 页（或 `autoanime confirm`）确认一次后，该命名模式写入
parse_memory，下次同类命名走 L2 直接命中，不会再问第二遍。

## hardlink 的前提？

**库目录与下载目录必须在同一盘/同一卷**（同一文件系统）。hardlink
做不了跨盘：跨盘（或文件系统不支持）时按 D9 默认降级 copy
（大文件翻倍 IO；`AUTOANIME_UPGRADE_COPY_POLICY=strict` 则直接跳过
并记审计）。docker compose 里两者同在 `/data` 下即同盘。

## 代理怎么配？

标准环境变量即可，进程内所有出网请求（Mikan RSS / LLM / Bangumi /
TMDB）都走 httpx 的 `trust_env`：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890   # 按需：HTTP_PROXY / ALL_PROXY / NO_PROXY 同理
```

Mikan 主站部分地区被墙，设 `HTTPS_PROXY` 是实操对策；备用域名
`mikanime.tv` 也可。Windows PowerShell 用
`$env:HTTPS_PROXY="http://127.0.0.1:7890"`。

## API token 怎么用？

`.env` 里设置 `AUTOANIME_API_TOKEN`（空串 = 关闭认证）后：

- 所有 `/api` 请求带请求头 `X-API-Token: <token>`；
- SSE（`/api/events`）额外接受同值查询参数 `?token=<token>`
  （EventSource 发不了自定义头）；
- WebUI：Settings 页填入 token，前端存 localStorage，后续请求自动携带。

```bash
curl -H "X-API-Token: <token>" http://127.0.0.1:8000/api/health
```

再次提醒：**勿暴露公网**——简单 token 只是 LAN 内的最低门槛，不是
公网防线。
