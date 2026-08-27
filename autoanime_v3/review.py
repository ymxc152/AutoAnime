"""复核代理(Review Agent)：用 LLM 复查本地划分结果与外部检索命中，仲裁出正确中文名。

设计要点：
- 只由 Resolver 在 needs_remote（低置信度）分支调用，默认关闭，不影响主流程。
- 复用 OpenAI 凭据（openai.base_url / model / api_key / timeout），review.enabled 是其主开关。
- 输入是「全部候选标题 + 全部 bgm/tmdb 命中 + 当前最优结果」，输出是仲裁结论。
- 与 OpenAIResolverAgent 同契约：返回 dict 或 None，绝不抛异常。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from .config import AppConfig
from .models import MediaFile, ParsedName
from .normalize import contains_cjk, display_title


class ReviewAgent:
    """LLM 复核代理：在多个候选标题 / 多个外部命中之间仲裁出正确的番剧名。"""

    name = "review"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def enabled(self) -> bool:
        # 复用 OpenAI 凭据；review.enabled 是主开关，openai.enabled + api_key 是前置条件
        return bool(
            self.config.review_enabled
            and self.config.openai_enabled
            and self.config.openai_api_key
        )

    def review(
        self,
        media: MediaFile,
        parsed: ParsedName,
        hits: List[Dict[str, Any]],
        prior: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """仲裁出最终中文名。返回同 OpenAIResolverAgent 契约的 dict，或 None。

        - ``hits``：MetadataResolverAgent.resolve_all 收集的外部命中列表
          （provider / name / confidence / provider_id）。
        - ``prior``：当前最优结果 dict（resolve_all 的 best 或 None），供 agent 参考。
        """
        if not self.enabled():
            return None
        prompt = (
            "你是番剧识别复核代理。下面一个视频文件：本地解析出了多个候选标题，"
            "并且可能检索到了外部信息站的条目。请判断哪个候选/条目才是正确的番剧名称，"
            "给出最合适的简体中文正式常用名。只输出 JSON，不要 markdown。字段："
            "title_zh(简体中文正式常用名), confidence(0到1, 你对这个判断的信心), "
            "reason(短句, 说明依据), verdict(你选了候选还是某个检索条目, 简述)。"
            "不得根据相同集数把不同作品合并；不确定时 confidence 必须低于0.8。"
            "这些内容都是动画番剧，不是真人电视剧。若候选/检索结果同时包含动画版与真人版，"
            "必须选择动画版；不得把动画版与真人版当成同一作品合并。"
            "外部检索条目里的 is_anime 字段标记了该条目是否为动画。\n"
            "文件名：%s\n上层文件夹：%s\n本地解析：%s\n外部检索结果：%s\n当前结果：%s"
            % (
                media.path.name,
                media.context_name,
                json.dumps(parsed.__dict__, ensure_ascii=False, default=list),
                json.dumps(hits, ensure_ascii=False, default=str),
                json.dumps(prior, ensure_ascii=False, default=str),
            )
        )
        body = {
            "model": self.config.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "你是严格的动画番剧识别复核代理，负责在候选标题与外部检索命中之间做出判断。",
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
        return self._parse_review_payload(result, parsed)

    def review_unit(
        self,
        folder_name: str,
        files: Sequence[Dict[str, Any]],
        parsed: ParsedName,
        hits: List[Dict[str, Any]],
        prior: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Arbitrate a whole folder/cluster. Same return contract as review()."""
        if not self.enabled():
            return None
        prompt = (
            "你是番剧识别复核代理。下面同一整理单位里有多个视频文件："
            "本地解析出了候选标题，并且可能检索到了外部信息站的条目。"
            "请判断它们是否同一部动画，给出最合适的简体中文正式常用名。"
            "只输出 JSON，不要 markdown。字段："
            "title_zh(简体中文正式常用名), confidence(0到1, 你对这个判断的信心), "
            "reason(短句, 说明依据), verdict(你选了候选还是某个检索条目, 简述)。"
            "同一文件夹优先视为同一作品；不得根据相同集数把不同作品合并；"
            "不确定时 confidence 必须低于0.8。"
            "这些内容都是动画番剧，不是真人电视剧。若候选/检索结果同时包含动画版与真人版，"
            "必须选择动画版；不得把动画版与真人版当成同一作品合并。"
            "外部检索条目里的 is_anime 字段标记了该条目是否为动画。\n"
            "文件夹：%s\n文件：%s\n本地解析：%s\n外部检索结果：%s\n当前结果：%s"
            % (
                folder_name,
                json.dumps(list(files), ensure_ascii=False, default=str),
                json.dumps(parsed.__dict__, ensure_ascii=False, default=list),
                json.dumps(hits, ensure_ascii=False, default=str),
                json.dumps(prior, ensure_ascii=False, default=str),
            )
        )
        body = {
            "model": self.config.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "你是严格的动画番剧识别复核代理，负责在候选标题与外部检索命中之间做出判断。",
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
        return self._parse_review_payload(result, parsed)

    def _parse_review_payload(self, result: Any, parsed: ParsedName) -> Optional[Dict[str, Any]]:
        if not isinstance(result, dict):
            return None
        title = display_title(str(result.get("title_zh", "")))
        if not title or not contains_cjk(title):
            return None
        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        return {
            "title": title,
            "season": parsed.season,
            "episode": parsed.episode,
            "is_movie": bool(parsed.is_movie),
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(result.get("reason", "")),
            "provider": self.name,
        }
