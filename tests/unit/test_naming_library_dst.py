"""organize.naming.build_library_dst 单测（批次一）：三开关全组合纯函数钉死。

覆盖：3 开关 × {TV 正片 / OVA / 剧场版} 组合矩阵（开关间互不越界：剧场版
开关不动 OVA/TV、S00 开关不动 TV/剧场版）+ 年份后缀开关（TV 目录追加、
剧场版目录与文件名同追加、year 缺省不加）+ 边界（extension 规范化、
无集号 S00E00、无技术词 SD）。全离线纯函数，Settings 直接当 Settings-like。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoanime.config import Settings
from autoanime.organize.naming import build_library_dst

_TOKEN_NAME = "[LoliHouse] 孤独摇滚 - 05 [Baha 1080p HEVC]"


def _settings(
    tmp_path: Path,
    *,
    movie_dir: bool = False,
    specials_s00: bool = False,
    year_suffix: bool = False,
) -> Settings:
    settings = Settings(library_path=tmp_path / "library")
    settings.naming_movie_dir = movie_dir
    settings.naming_specials_s00 = specials_s00
    settings.naming_year_suffix = year_suffix
    return settings


def _dst(
    settings: Settings,
    *,
    segment: str,
    year: str | None = None,
    season: int | None = 1,
    episode: int | None = 5,
    extension: str = ".mkv",
    release_title: str | None = _TOKEN_NAME,
) -> Path:
    return build_library_dst(
        settings,
        title="孤独摇滚",
        year=year,
        segment=segment,
        season=season,
        episode=episode,
        fansub="LoliHouse",
        extension=extension,
        release_title=release_title,
    )


# ------------------------------------------------- 3 开关 × {TV/OVA/剧场版}


@pytest.mark.parametrize(
    ("movie_dir", "specials_s00", "segment", "expected"),
    [
        # TV 正片：剧场版开关/S00 开关都不越界（只有现状路径）
        (True, False, "tv", "孤独摇滚/Season 01/孤独摇滚 - S01E05.1080p.mkv"),
        (False, True, "tv", "孤独摇滚/Season 01/孤独摇滚 - S01E05.1080p.mkv"),
        (True, True, "tv", "孤独摇滚/Season 01/孤独摇滚 - S01E05.1080p.mkv"),
        # OVA：现状 = 电影式模板；S00 开开后入 Season 00；剧场版开关不越界
        (False, False, "ova", "孤独摇滚/孤独摇滚.1080p.mkv"),
        (True, False, "ova", "孤独摇滚/孤独摇滚.1080p.mkv"),
        (False, True, "ova", "孤独摇滚/Season 00/孤独摇滚 - S00E05.mkv"),
        (True, True, "ova", "孤独摇滚/Season 00/孤独摇滚 - S00E05.mkv"),
        # 剧场版：现状 = 电影式模板；独立目录开关开后进 Movies/（无 SxxExx）
        (False, False, "movie", "孤独摇滚/孤独摇滚.1080p.mkv"),
        (False, True, "movie", "孤独摇滚/孤独摇滚.1080p.mkv"),
        (True, False, "movie", "Movies/孤独摇滚/孤独摇滚.mkv"),
        (True, True, "movie", "Movies/孤独摇滚/孤独摇滚.mkv"),
        # special 段与 OVA 同走 S00 分支
        (False, True, "special", "孤独摇滚/Season 00/孤独摇滚 - S00E05.mkv"),
    ],
)
def test_switch_combinations_per_segment(
    tmp_path: Path,
    movie_dir: bool,
    specials_s00: bool,
    segment: str,
    expected: str,
) -> None:
    settings = _settings(tmp_path, movie_dir=movie_dir, specials_s00=specials_s00)
    dst = _dst(settings, segment=segment)
    assert dst == Path(settings.library_path) / Path(*expected.split("/"))
    assert dst.is_relative_to(Path(settings.library_path))


# ------------------------------------------------- 年份开关


def test_year_suffix_on_tv_title_dir_only(tmp_path: Path) -> None:
    """TV：年份只进标题目录，文件名保持现状模板（含质量段）。"""
    settings = _settings(tmp_path, year_suffix=True)
    dst = _dst(settings, segment="tv", year="2026")
    assert dst.as_posix().endswith(
        "孤独摇滚 (2026)/Season 01/孤独摇滚 - S01E05.1080p.mkv"
    )


def test_year_suffix_on_movie_dir_and_file(tmp_path: Path) -> None:
    """剧场版独立目录：目录与文件名同追加 (YYYY)。"""
    settings = _settings(tmp_path, movie_dir=True, year_suffix=True)
    dst = _dst(settings, segment="movie", year="2026")
    assert dst.as_posix().endswith("Movies/孤独摇滚 (2026)/孤独摇滚 (2026).mkv")


def test_year_suffix_on_specials_s00_dir(tmp_path: Path) -> None:
    """S00：年份只进标题目录（文件名按定版格式不带年份/质量段）。"""
    settings = _settings(tmp_path, specials_s00=True, year_suffix=True)
    dst = _dst(settings, segment="ova", year="2026")
    assert dst.as_posix().endswith("孤独摇滚 (2026)/Season 00/孤独摇滚 - S00E05.mkv")


def test_year_suffix_off_ignores_year(tmp_path: Path) -> None:
    """开关关：调用方传了 year 也不追加（现状路径逐字节不变）。"""
    settings = _settings(tmp_path)
    dst = _dst(settings, segment="tv", year="2026")
    assert dst.as_posix().endswith("孤独摇滚/Season 01/孤独摇滚 - S01E05.1080p.mkv")


def test_year_absent_adds_nothing_even_when_enabled(tmp_path: Path) -> None:
    """开关开但 year 缺省（None）：缺省不加，不产生空括号目录。"""
    settings = _settings(tmp_path, movie_dir=True, year_suffix=True)
    dst = _dst(settings, segment="movie", year=None)
    assert dst.as_posix().endswith("Movies/孤独摇滚/孤独摇滚.mkv")


# ------------------------------------------------- 边界


def test_extension_normalized_without_leading_dot(tmp_path: Path) -> None:
    settings = _settings(tmp_path, specials_s00=True)
    dst = _dst(settings, segment="ova", extension="mp4")
    assert dst.name == "孤独摇滚 - S00E05.mp4"


def test_specials_without_episode_number_is_s00e00(tmp_path: Path) -> None:
    settings = _settings(tmp_path, specials_s00=True)
    dst = _dst(settings, segment="special", episode=None)
    assert dst.name == "孤独摇滚 - S00E00.mkv"


def test_legacy_branch_without_quality_token_is_sd(tmp_path: Path) -> None:
    """现状分支：release_title 无分辨率 token → SD（与旧行为一致）。"""
    settings = _settings(tmp_path)
    dst = _dst(settings, segment="tv", release_title="无技术词")
    assert dst.name == "孤独摇滚 - S01E05.SD.mkv"
