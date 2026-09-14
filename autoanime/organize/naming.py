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
from typing import Protocol

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
# 媒体库命名开关（批次一）：归档目标路径收敛的唯一入口
# ---------------------------------------------------------------------------

#: 剧场版独立目录名（Plex/Jellyfin 电影库惯例）。
MOVIES_DIR = "Movies"

#: 走 Season 00（Specials）分支的 segment 值（OVA/ONA/SP 类）。
_SPECIAL_SEGMENTS = frozenset({"ova", "special", "ona"})


class NamingSwitchSettings(Protocol):
    """``build_library_dst`` 需要的最小 Settings 切面（Settings-like）。

    ``library_path`` 声明为 ``Path``（与 Settings 一致，Protocol 属性按
    不变形校验）；宽容 str 的调用方先 ``Path(...)`` 包装。
    """

    library_path: Path
    naming_movie_dir: bool
    naming_specials_s00: bool
    naming_year_suffix: bool


def _year_suffix(settings: NamingSwitchSettings, year: str | None) -> str:
    """`` (YYYY)`` 年份后缀：开关开且调用方给了年份才追加（缺省不加）。"""
    if year and settings.naming_year_suffix:
        return f" ({sanitize(str(year))})"
    return ""


def build_library_dst(
    settings: NamingSwitchSettings,
    *,
    title: str,
    year: str | None,
    segment: str,
    season: int | None,
    episode: int | None,
    fansub: str | None,
    extension: str,
    release_title: str | None = None,
) -> Path:
    """归档目标路径（含媒体库根）的唯一计算入口（批次一三开关）。

    各归档路径（RSS 完成回调 / 库外扫描 + 手动导入）此前各自组合
    ``relative_path``，同一部番会因标题来源不同裂成两个目录；本函数把
    「目标位计算」收敛为一个纯函数，标题由调用方先按订阅/解析回退链
    解析好再传入（标题语言回退仍走 :meth:`NamingInput.display_title`）。

    行为（按 ``segment`` 与三开关分流）：

    - ``movie`` 且 ``naming_movie_dir=True`` →
      ``{library}/Movies/{标题}{年份}/{标题}{年份}.{ext}``（无 SxxExx，
      质量段不入名——电影库惯例以目录名为标识）；
    - OVA/ONA/SP 类且 ``naming_specials_s00=True`` →
      ``{library}/{标题}{年份}/Season 00/{标题} - S00E{集序}.{ext}``
      （v1 简化：集序 = episode 号原样、季固定 00）；
    - ``naming_year_suffix=True`` 且 ``year`` 非空 → 标题目录追加
      `` (YYYY)``（year 取 air_date/开工年份由调用方传入，缺省不加；
      v1 调用方暂无系列级年份数据时传 None——按集补年会把同番裂成
      带年份/不带年份两个目录）；
    - 其余 = 现状路径（``relative_path`` 保留为内部实现；质量段由
      ``release_title`` 的分辨率 token 决定，未知 = SD）。

    ``fansub`` v1 未入模板（D17 未定义 {fansub} 槽），保留参数占位；
    ``release_title`` 仅作用于现状分支（新模板按定版格式不带质量段）。
    """
    del fansub  # v1 保留槽位：命名模板未定义 {fansub}
    seg = (segment or "tv").strip().lower()
    clean_title = sanitize(title)
    suffix = _year_suffix(settings, year)
    ext = extension if extension.startswith(".") else f".{extension}"
    library_root = Path(settings.library_path)
    if seg == "movie" and settings.naming_movie_dir:
        named = f"{clean_title}{suffix}"
        return library_root / PurePosixPath(MOVIES_DIR) / named / f"{named}{ext}"
    if seg in _SPECIAL_SEGMENTS and settings.naming_specials_s00:
        code = f"S00{_episode_pad(episode if episode is not None else 0)}"
        rel = (
            PurePosixPath(f"{clean_title}{suffix}")
            / "Season 00"
            / f"{clean_title} - {code}{ext}"
        )
        return library_root / rel
    # 现状路径（relative_path 内部实现）+ 标题目录年份后缀
    # （ona 不在 relative_path 的 movie/ova/special 集内，归并到 special 分支）
    naming = NamingInput(
        title_cn=clean_title,
        title_romaji=clean_title,
        title_jp=clean_title,
        season_number=season if season is not None else 1,
        episode_number=episode if episode is not None else 0,
        media_type="special" if seg == "ona" else seg,
        release_title=release_title,
    )
    rel = relative_path(naming, extension=ext)
    if suffix:
        rel = PurePosixPath(f"{clean_title}{suffix}", *rel.parts[1:])
    return library_root / rel


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
