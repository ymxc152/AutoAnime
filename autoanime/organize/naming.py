"""归档命名（E4b，拍板 D17/D18）。

Sonarr 兼容模板::

    {title_cn}/Season {SS}/{title_cn} - S{SS}E{EE}.{quality}.mkv

- 标题语言可配（``settings.naming_title_language`` = ``title_cn`` 或
  ``title_romaji``），缺什么回退什么（title_cn → romaji → jp， Jellyfin/
  Plex 零配置识别的形态本身不变）；
- 剧场版/OVA（media_type 分支）不套 Season/E 模板：
  ``{title}/{title}.{quality}.mkv``（v1 不做年份数据，backlog）；
- 质量段 ``{quality}`` 用分辨率 token（1080p/720p/576p/480p），未知 = ``SD``；
- Windows/跨平台非法字符统一清洗；字幕跟随（D18）在 mover 里做，
  本模块提供 ``with_subtitle`` 的改名对应关系。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from autoanime.organize.upgrade import parse_quality_tokens

_UNSAFE = re.compile(r'[\\/:*?"<>|]+')
_SPACES = re.compile(r"\s+")


def sanitize(name: str) -> str:
    """文件/目录名清洗：去非法字符与首尾空白/点。"""
    cleaned = _UNSAFE.sub(" ", name)
    cleaned = _SPACES.sub(" ", cleaned).strip(" .")
    return cleaned or "Unknown"


def _season_pad(season: int) -> str:
    return f"S{season:02d}"


def _episode_pad(episode: int) -> str:
    return f"E{episode:02d}"


@dataclass(frozen=True)
class NamingInput:
    """命名的最小输入（series/episode 已知事实 + 候选技术词）。"""

    title_cn: str | None
    title_romaji: str | None
    title_jp: str | None
    season_number: int
    episode_number: int
    media_type: str = "tv"  # tv | movie | ova | special
    release_title: str | None = None  # 候选标题（抽分辨率 token 用）

    def display_title(self, language: str = "title_cn") -> str:
        """标题语言可配 + 回退链（D17）。"""
        candidates = {
            "title_cn": (self.title_cn, self.title_romaji, self.title_jp),
            "title_romaji": (self.title_romaji, self.title_cn, self.title_jp),
            "title_jp": (self.title_jp, self.title_romaji, self.title_cn),
        }
        for candidate in candidates.get(language, candidates["title_cn"]):
            if candidate and candidate.strip():
                return candidate.strip()
        return "Unknown"


def quality_label(release_title: str | None) -> str:
    """{quality} 段：分辨率 token；未知（含 2160p 未定义档）= SD。"""
    if not release_title:
        return "SD"
    tokens = parse_quality_tokens(release_title)
    return tokens.resolution or "SD"


def episode_relative_path(
    naming: NamingInput, *, language: str = "title_cn", extension: str = ".mkv"
) -> PurePosixPath:
    """相对媒体库根的归档路径（tv 分支，D17 模板）。"""
    title = sanitize(naming.display_title(language))
    quality = quality_label(naming.release_title)
    season = _season_pad(naming.season_number)
    code = f"{season}{_episode_pad(naming.episode_number)}"
    return PurePosixPath(title) / f"Season {naming.season_number:02d}" / (
        f"{title} - {code}.{quality}{extension}"
    )


def movie_relative_path(
    naming: NamingInput, *, language: str = "title_cn", extension: str = ".mkv"
) -> PurePosixPath:
    """剧场版/OVA 分支：不套 Season/E 模板（Plan §6 第 10 项）。"""
    title = sanitize(naming.display_title(language))
    quality = quality_label(naming.release_title)
    return PurePosixPath(title) / f"{title}.{quality}{extension}"


def relative_path(
    naming: NamingInput, *, language: str = "title_cn", extension: str = ".mkv"
) -> PurePosixPath:
    """media_type 分支入口。"""
    if naming.media_type in ("movie", "ova", "special"):
        return movie_relative_path(naming, language=language, extension=extension)
    return episode_relative_path(naming, language=language, extension=extension)


# ---------------------------------------------------------------------------
# 字幕跟随（D18）
# ---------------------------------------------------------------------------

VIDEO_SUFFIXES = frozenset({".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".webm"})
SUBTITLE_SUFFIXES = frozenset({".ass", ".srt", ".ssa", ".sub", ".vtt"})


def subtitle_targets(video_src: Path, video_dst_name: str, siblings: list[Path]) -> list[tuple[Path, str]]:
    """同包同名字幕跟随（D18）：``<stem>.<lang后缀>.<字幕扩展名>`` 保后缀改名。

    ``Show - S01E01.mkv`` ↔ ``Show - S01E01.zh.ass`` → 目标
    ``Show - S01E01.zh.ass`` 对应视频目标名的同名形态。只跟随同目录
    （同包语义）、只认字幕扩展名；不做字幕站下载、不提取内封字幕。
    """
    video_stem = video_src.name[: -len(video_src.suffix)]
    dst_stem = video_dst_name[: video_dst_name.rfind(".")] if "." in video_dst_name else video_dst_name
    pairs: list[tuple[Path, str]] = []
    for sibling in sorted(siblings):
        if sibling == video_src or sibling.suffix.lower() not in SUBTITLE_SUFFIXES:
            continue
        sibling_stem = sibling.name[: -len(sibling.suffix)]
        if sibling_stem == video_stem:
            pairs.append((sibling, f"{dst_stem}{sibling.suffix}"))
        elif sibling_stem.startswith(f"{video_stem}."):
            lang_suffix = sibling_stem[len(video_stem):]  # ".zh" / ".chi.简体" 等
            pairs.append((sibling, f"{dst_stem}{lang_suffix}{sibling.suffix}"))
    return pairs
