from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .config import AppConfig
from .models import MediaFile, ParsedName
from .normalize import contains_cjk, display_title


class OpenAIResolverAgent:
    """仅处理本地无法安全收敛的条目；返回结果仍需本地策略校验。"""

    name = "openai"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def enabled(self) -> bool:
        return bool(self.config.openai_enabled and self.config.openai_api_key)

    def resolve(self, media: MediaFile, parsed: ParsedName) -> Optional[Dict[str, Any]]:
        if not self.enabled():
            return None
        prompt = (
            "识别这个动画视频。只输出 JSON，不要 markdown。字段："
            "title_zh(简体中文正式常用名), season(整数), episode(整数), "
            "is_movie(布尔), confidence(0到1), reason(短句)。"
            "不得根据相同集数把不同作品合并；不确定时 confidence 必须低于0.8。\n"
            "文件名：%s\n上层文件夹：%s\n本地解析：%s"
            % (media.path.name, media.context_name, json.dumps(parsed.__dict__, ensure_ascii=False, default=list))
        )
        body = {
            "model": self.config.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你是严格的动画文件名元数据识别器。"},
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
        title = display_title(str(result.get("title_zh", "")))
        if not title or not contains_cjk(title):
            return None
        try:
            season = int(result.get("season"))
            episode = int(result.get("episode"))
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if season < 1 or episode < 1:
            return None
        movie_flag = result.get("is_movie", False)
        if not isinstance(movie_flag, bool):
            return None
        return {
            "title": title,
            "season": season,
            "episode": episode,
            "is_movie": movie_flag,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(result.get("reason", "")),
        }
