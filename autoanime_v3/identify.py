"""Batch identify agent: one LLM call per folder/cluster, never invents destinations."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from .config import AppConfig
from .identify_units import IdentifyUnit
from .models import MediaFile, ParsedName
from .normalize import contains_cjk, display_title


class IdentifyAgent:
    """LLM identify agent that sees every file in a work unit as working memory."""

    name = "identify_batch"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def enabled(self) -> bool:
        return bool(
            self.config.parse_agent_mode != "off"
            and self.config.openai_enabled
            and self.config.openai_api_key
        )

    def identify(
        self,
        unit: IdentifyUnit,
        files: Sequence[Dict[str, Any]],
        catalog_hints: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled():
            return None
        prompt = (
            "你是番剧识别代理。下面同一整理单位（同一文件夹或同一作品聚类）里的多个视频文件。"
            "请判断它们是否同一部动画，给出最合适的简体中文正式常用名。"
            "只输出 JSON，不要 markdown。字段："
            "title_zh(简体中文正式常用名，整组同一部时填写),"
            "aliases(数组，文件名/文件夹里出现的其它叫法),"
            "confidence(0到1),"
            "reason(短句),"
            "split(布尔，若混了多部作品则为 true),"
            "shows(split 为 true 时填写，数组，每项 {\"title_zh\":\"...\",\"files\":[文件名...]}),"
            "files(可选，数组，仅当需要补 season/episode/media_type 时填写，"
            "每项 {\"name\":\"文件名\",\"season\":整数,\"episode\":整数或字符串,\"media_type\":\"episode|movie|special\"})。"
            "同一文件夹优先视为同一作品；只有明确证据才 split。"
            "不得根据相同集数把不同作品合并；不确定时 confidence 必须低于 0.8。"
            "这些内容都是动画番剧，不是真人电视剧。若同时包含动画版与真人版，必须选择动画版。"
            "不要输出整理目标路径。\n"
            "文件夹：%s\n已有提示标题：%s\n已知别名：%s\n文件：%s"
            % (
                unit.folder.name,
                unit.hint_title or "",
                json.dumps(list(catalog_hints or []), ensure_ascii=False),
                json.dumps(list(files), ensure_ascii=False, default=str),
            )
        )
        body = {
            "model": self.config.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "你是严格的动画番剧批量识别代理，一次处理一个文件夹或作品聚类。",
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
        return _normalize_identify_result(result, [item.path.name for item in unit.files])


def _normalize_identify_result(result: Any, filenames: List[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    split = bool(result.get("split"))
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    aliases = []
    for value in result.get("aliases") or []:
        text = display_title(str(value))
        if text:
            aliases.append(text)
    shows: List[Dict[str, Any]] = []
    if split:
        for entry in result.get("shows") or []:
            if not isinstance(entry, dict):
                continue
            title = display_title(str(entry.get("title_zh", "")))
            if not title or not contains_cjk(title):
                continue
            names = [str(name) for name in (entry.get("files") or []) if str(name)]
            shows.append({"title_zh": title, "files": names})
        if not shows:
            return None
    title = display_title(str(result.get("title_zh", "")))
    if not split and (not title or not contains_cjk(title)):
        return None
    files_out: Dict[str, Dict[str, Any]] = {}
    for entry in result.get("files") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        item: Dict[str, Any] = {}
        if "season" in entry and entry["season"] not in (None, ""):
            try:
                item["season"] = int(entry["season"])
            except (TypeError, ValueError):
                pass
        if "episode" in entry and entry["episode"] not in (None, ""):
            item["episode"] = entry["episode"]
        media_type = str(entry.get("media_type") or "").strip()
        if media_type in {"episode", "movie", "special"}:
            item["media_type"] = media_type
        if item:
            files_out[name] = item
    return {
        "title": title,
        "aliases": aliases,
        "confidence": confidence,
        "reason": str(result.get("reason", "")),
        "split": split,
        "shows": shows,
        "files": files_out,
        "filenames": filenames,
        "provider": IdentifyAgent.name,
    }
