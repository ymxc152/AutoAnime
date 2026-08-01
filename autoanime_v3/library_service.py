"""未来 CLI/WebUI 共用的应用服务边界。

WebUI 不应直接拼 SQL 或移动文件；所有查询、纠正预览和后续执行都通过此层。
当前只开放只读查询与“生成纠正草案”，真正应用纠正留到后续版本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .repository import LibraryRepository
from .normalize import safe_component, same_path


class LibraryService:
    def __init__(self, repository: LibraryRepository, output_root: Path) -> None:
        self.repository = repository
        self.output_root = output_root

    def list_shows(self) -> List[Dict[str, Any]]:
        return self.repository.list_show_progress()

    def get_show(self, show_id: int) -> Optional[Dict[str, Any]]:
        return self.repository.show_detail(show_id)

    def preview_show_title_change(self, show_id: int, new_title: str, reason: str = "") -> Dict[str, Any]:
        if not str(new_title or "").strip():
            raise ValueError("new title must not be empty")
        detail = self.repository.show_detail(show_id)
        if detail is None:
            raise KeyError("show not found: %s" % show_id)
        old_title = str(detail["show"]["canonical_title"])
        clean_title = safe_component(new_title)
        moves = []
        reserved = set()
        for row in detail["episodes"]:
            current = row.get("current_path")
            if not current or row.get("status") != "organized":
                continue
            current_path = Path(current)
            season = int(row["season_number"])
            episode = int(row["episode_number"])
            is_movie = bool(row.get("is_movie"))
            old_stem = current_path.stem
            if is_movie:
                old_prefix = safe_component(old_title)
                tail = old_stem[len(old_prefix):] if old_stem.casefold().startswith(old_prefix.casefold()) else ""
                new_stem = clean_title + tail
                destination = self.output_root / clean_title / (new_stem + current_path.suffix.lower())
            else:
                episode_prefix = "S%02dE%02d - " % (season, episode)
                old_prefix = episode_prefix + safe_component(old_title)
                tail = old_stem[len(old_prefix):] if old_stem.casefold().startswith(old_prefix.casefold()) else ""
                new_stem = episode_prefix + clean_title + tail
                destination = self.output_root / clean_title / ("Season %02d" % season) / (new_stem + current_path.suffix.lower())
            destination_key = str(destination).casefold()
            action = "move"
            move_reason = ""
            if destination_key in reserved:
                action, move_reason = "conflict", "duplicate_destination"
            elif destination.exists() and not same_path(current_path, destination):
                action, move_reason = "conflict", "destination_exists"
            elif same_path(current_path, destination):
                action, move_reason = "skip", "already_in_place"
            reserved.add(destination_key)
            moves.append({
                "source": str(current_path),
                "destination": str(destination),
                "action": action,
                "reason": move_reason,
            })
        correction_id = self.repository.create_correction(
            "show", show_id, "canonical_title", old_title, clean_title, reason, moves
        )
        return {
            "correction_id": correction_id,
            "status": "draft",
            "old_title": old_title,
            "new_title": clean_title,
            "moves": moves,
            "conflicts": sum(1 for move in moves if move["action"] == "conflict"),
        }

    def apply_correction(self, correction_id: int) -> None:
        raise NotImplementedError(
            "v3.1 仅生成可审计迁移计划；WebUI 写入、锁与原子迁移将在后续版本实现。"
        )
