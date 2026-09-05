"""organize.naming / mover / rollback 单测（E4b）：D17 命名 + D18 字幕跟随 +
D9 hardlink/copy 降级决策表 + D21 原件不动 + audit reverse 执行（tmp_path 真文件系统）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoanime.organize import mover
from autoanime.organize.naming import (
    NamingInput,
    quality_label,
    relative_path,
    sanitize,
    subtitle_targets,
)
from autoanime.organize.rollback import execute_reverse, split_reverse


def _naming(media_type: str = "tv") -> NamingInput:
    return NamingInput(
        title_cn="孤独摇滚",
        title_romaji="Bocchi the Rock",
        title_jp="ぼっち・ざ・ろっく!",
        season_number=1,
        episode_number=5,
        media_type=media_type,
        release_title="[LoliHouse] 孤独摇滚 - 05 [Baha 1080p HEVC]",
    )


# ---------------------------------------------------------------------------
# D17 命名
# ---------------------------------------------------------------------------


def test_episode_template_sonarr_compatible() -> None:
    path = relative_path(_naming())
    assert path.as_posix() == "孤独摇滚/Season 01/孤独摇滚 - S01E05.1080p.mkv"


def test_title_language_switch_and_fallback() -> None:
    romaji = relative_path(_naming(), language="title_romaji")
    assert romaji.as_posix().startswith("Bocchi the Rock/Season 01/Bocchi the Rock - S01E05")
    jp = relative_path(_naming(), language="title_jp")
    assert "ぼっち・ざ・ろっく!" in jp.as_posix()
    # 回退链：缺主语言标题时按 cn → romaji → jp 兜底
    sparse = NamingInput(title_cn=None, title_romaji="Bocchi", title_jp=None,
                         season_number=1, episode_number=1)
    assert sparse.display_title("title_cn") == "Bocchi"


def test_movie_branch_no_season_template() -> None:
    path = relative_path(_naming(media_type="movie"))
    assert path.as_posix() == "孤独摇滚/孤独摇滚.1080p.mkv"
    ova = relative_path(_naming(media_type="ova"))
    assert "Season" not in ova.as_posix()


def test_quality_label_unknown_is_sd() -> None:
    assert quality_label("[Sub] 无技术词") == "SD"
    assert quality_label(None) == "SD"


def test_sanitize_strips_illegal_chars() -> None:
    assert sanitize('a<b>c:d"e|f?g*h') == "a b c d e f g h"
    assert sanitize("  孤独摇滚。 ") == "孤独摇滚。"


# ---------------------------------------------------------------------------
# D18 字幕跟随
# ---------------------------------------------------------------------------


def test_subtitle_targets_follow_with_language_suffix() -> None:
    video = Path("/dl/Show - S01E01.mkv")
    siblings = [
        Path("/dl/Show - S01E01.zh.ass"),
        Path("/dl/Show - S01E01.chs.srt"),
        Path("/dl/other.txt"),
        Path("/dl/Show - S01E01.ass"),
    ]
    pairs = subtitle_targets(video, "Show - S01E01.1080p.mkv", siblings)
    assert (Path("/dl/Show - S01E01.zh.ass"), "Show - S01E01.1080p.zh.ass") in pairs
    assert (Path("/dl/Show - S01E01.ass"), "Show - S01E01.1080p.ass") in pairs
    assert len(pairs) == 3  # other.txt 不跟随


# ---------------------------------------------------------------------------
# D9 降级决策表（plan 纯决策）
# ---------------------------------------------------------------------------


@pytest.fixture
def fs_pair(tmp_path: Path) -> tuple[Path, Path]:
    downloads = tmp_path / "downloads"
    library = tmp_path / "library"
    downloads.mkdir()
    library.mkdir()
    return downloads, library


def _video(downloads: Path, name: str = "Show - S01E01.mkv", size: int = 10) -> Path:
    path = downloads / name
    path.write_bytes(b"x" * size)
    return path


@pytest.mark.parametrize(
    ("setup", "copy_policy", "expected_strategy", "expected_reason"),
    [
        ("same_fs", "allow", "hardlink", None),
        ("same_fs", "strict", "hardlink", None),  # strict 只禁 copy，同盘照常 hardlink
        ("cross_fs", "allow", "copy", None),
        ("cross_fs", "strict", "skip", "cross_fs_copy_disabled"),
    ],
)
def test_plan_transfer_degradation_table(
    fs_pair: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    setup: str,
    copy_policy: str,
    expected_strategy: str,
    expected_reason: str | None,
) -> None:
    downloads, library = fs_pair
    video = _video(downloads)
    if setup == "cross_fs":
        monkeypatch.setattr(mover, "_same_filesystem", lambda a, b: False)
    plan = mover.plan_transfer(
        video,
        library_root=library,
        dst_dir=library / "Show" / "Season 01",
        dst_name="Show - S01E01.1080p.mkv",
        copy_policy=copy_policy,  # type: ignore[arg-type]
    )
    assert plan.strategy == expected_strategy
    assert plan.skip_reason == expected_reason


def test_plan_transfer_skips_oversize(fs_pair: tuple[Path, Path]) -> None:
    downloads, library = fs_pair
    video = _video(downloads, size=30)
    plan = mover.plan_transfer(
        video, library_root=library, dst_dir=library, dst_name="x.mkv",
        skip_over_bytes=20,
    )
    assert plan.strategy == "skip"
    assert plan.skip_reason == "size_over_limit:30"


def test_plan_transfer_skips_missing_source(fs_pair: tuple[Path, Path]) -> None:
    downloads, library = fs_pair
    plan = mover.plan_transfer(
        downloads / "ghost.mkv", library_root=library, dst_dir=library, dst_name="x.mkv",
    )
    assert plan.strategy == "skip"
    assert plan.skip_reason == "missing_source"


def test_execute_transfer_hardlink_keeps_source_seed(
    fs_pair: tuple[Path, Path], tmp_path: Path
) -> None:
    """D21：hardlink 归档后，下载原件不动（同 inode、链接数不减）。"""
    downloads, library = fs_pair
    video = _video(downloads)
    subtitle_src = downloads / "Show - S01E01.zh.ass"
    subtitle_src.write_text("subs")
    dst_dir = library / "Show" / "Season 01"
    plan = mover.plan_transfer(
        video, library_root=library, dst_dir=dst_dir,
        dst_name="Show - S01E01.1080p.mkv",
        siblings=[subtitle_src],
    )
    result = mover.execute_transfer(plan)
    assert result.error is None
    archived = result.dst_paths[0]
    assert archived.exists()
    assert archived.samefile(video)  # hardlink：同一 inode
    assert video.exists()  # 下载原件保留做种（D21）
    assert plan.strategy == "hardlink"
    # 字幕跟随
    subtitle = dst_dir / "Show - S01E01.1080p.zh.ass"
    assert subtitle.exists()


def test_execute_transfer_atomic_replace_semantics(fs_pair: tuple[Path, Path]) -> None:
    downloads, library = fs_pair
    video = _video(downloads, "New.mkv")
    dst_dir = library
    plan = mover.plan_transfer(video, library_root=library, dst_dir=dst_dir, dst_name="New.mkv")
    result = mover.execute_transfer(plan)
    assert result.dst_paths == (dst_dir / "New.mkv",)
    # 重复执行覆盖不炸（os.replace 语义）
    again = mover.execute_transfer(plan)
    assert again.error is None


def test_replace_archive_file_keeps_old_on_failure(
    fs_pair: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """洗版失败回滚：新文件清理、旧文件原样保留。"""
    downloads, library = fs_pair
    old = library / "Show - S01E01.720p.mkv"
    old.write_bytes(b"old")
    new_video = _video(downloads, "New.mkv")
    plan = mover.plan_transfer(
        new_video, library_root=library, dst_dir=library, dst_name="Show - S01E01.1080p.mkv",
    )
    # 注入执行期失败：os.replace 前的 copy/link 阶段抛错
    def boom(_src: object, _dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mover.os, "link", boom)
    monkeypatch.setattr(mover.shutil, "copy2", boom)
    result = mover.replace_archive_file(old, plan)
    assert result.error == "OSError"
    assert old.read_bytes() == b"old"  # 旧文件保留


# ---------------------------------------------------------------------------
# audit reverse（5.4）
# ---------------------------------------------------------------------------


def test_split_reverse_keys() -> None:
    executable, skipped = split_reverse({"moves": [], "status": "active", "extra": 1})
    assert set(executable) == {"moves"}
    assert set(skipped) == {"status", "extra"}


def test_execute_reverse_undoes_hardlink_keeps_seed(
    fs_pair: tuple[Path, Path], tmp_path: Path
) -> None:
    downloads, library = fs_pair
    video = _video(downloads)
    dst_dir = library / "Show"
    plan = mover.plan_transfer(video, library_root=library, dst_dir=dst_dir, dst_name="a.mkv")
    mover.execute_transfer(plan)
    reverse: dict[str, object] = {"moves": list(plan.reverse_moves)}
    applied, skipped = execute_reverse(reverse)
    assert applied and not skipped
    assert not (dst_dir / "a.mkv").exists()  # 归档侧链接已撤销
    assert video.exists()  # 做种原件不受影响（D21）


def test_execute_reverse_reports_already_gone(fs_pair: tuple[Path, Path]) -> None:
    applied, skipped = execute_reverse(
        {"moves": [{"src": "/nowhere/x.mkv", "dst": "/nowhere/y.mkv", "kind": "copy"}]}
    )
    assert not applied
    assert skipped[0]["reason"] == "already gone"
