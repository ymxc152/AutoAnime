"""AI 名称划分代理(AI Parse Agent)：让 LLM 从文件名划分出各语言的作品名候选。

设计要点：
- 只由 Resolver 在 parse.agent_mode != "off" 时调用（uncertain 仅低置信文件、all 所有文件）。
- 复用 OpenAI 凭据（openai.base_url / model / api_key / timeout），parse.agent_mode 是其主开关。
- 输出语言标签候选（lang ∈ romaji / ja / en / zh-cn / zh-tw），供 bgm/tmdb 逐个探测 + 复核代理仲裁。
- 与 OpenAIResolverAgent 同契约：返回 dict 或 None，绝不抛异常。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .config import AppConfig
from .models import MediaFile, ParsedName

# 允许的语言标签白名单。
ALLOWED_LANGS = {"romaji", "ja", "en", "zh-cn", "zh-tw"}
MAX_AI_CANDIDATES = 8


class AIParseAgent:
    """从文件名划分出各语言作品名的 LLM 代理。"""

    name = "aiparse"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def enabled(self) -> bool:
        # parse.agent_mode != "off" 是主开关；openai_enabled + api_key 是前置条件
        return bool(
            self.config.parse_agent_mode != "off"
            and self.config.openai_enabled
            and self.config.openai_api_key
        )

    def parse(self, media: MediaFile, parsed: ParsedName) -> Optional[Dict[str, Any]]:
        """把文件名划分成各语言候选。返回 ``{"candidates": [(lang, name), ...], "reason": ...}`` 或 None。

        candidates 已按 lang 白名单过滤、去重、非空校验，最多 ``MAX_AI_CANDIDATES`` 条。
        """
        if not self.enabled():
            return None
        prompt = (
            "你是番剧文件名划分代理。把下面这个视频文件名按语言划分出作品名称。"
            "只输出 JSON，不要 markdown。字段："
            "candidates(数组，每项 {\"lang\":\"...\", \"name\":\"...\"}), reason(短句)。"
            "lang 只允许：romaji(罗马音), ja(日文), en(英文), zh-cn(简体中文), zh-tw(繁体中文)。"
            "只填能从文件名/上层文件夹可靠推断出的名称；推断不出就不填该条目，不得编造。"
            "这是动画番剧，划分出的名称应指向动画作品本身，不要指向真人改编电视剧。\n"
            "文件名：%s\n上层文件夹：%s\n本地解析：%s"
            % (
                media.path.name,
                media.context_name,
                json.dumps(parsed.__dict__, ensure_ascii=False, default=list),
            )
        )
        body = {
            "model": self.config.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "你是严格的动画番剧文件名语言划分器。",
                },
                {"role": "user", "content": prompt},
            ],
        }
        endpoint = self.config.openai_base_url.rstrip("/")
        if not endpoint.endswith("/v1/chat/completions"):
            endpoint += "/v1/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.config.openai_api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.openai_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (OSError, KeyError, IndexError, ValueError, json.JSONDecodeError, urllib.error.URLError):
            return None
        if not isinstance(result, dict):
            return None
        candidates: List[Tuple[str, str]] = []
        seen: set = set()
        for entry in result.get("candidates") or []:
            if not isinstance(entry, dict):
                continue
            lang = str(entry.get("lang", "")).strip().lower()
            name = str(entry.get("name", "")).strip()
            if lang not in ALLOWED_LANGS or not name:
                continue
            key = (lang, name)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
            if len(candidates) >= MAX_AI_CANDIDATES:
                break
        if not candidates:
            return None
        return {
            "candidates": candidates,
            "reason": str(result.get("reason", "")),
        }
