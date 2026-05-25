# -*- coding: utf-8 -*-
"""
根据 organization 单条 `episode_last_dst` 生成与 `Sorting_Mv` 一致的目标路径，用于重命名/迁移已整理资源。

- 不依赖 `state` / dry-run 全局：由调用方传入命名参数。
- 与 `autoanime.sorting` 包内模块解耦，避免经 `sorting` 包 `__init__` 时触发循环导入（`file_ops` → `autoanime.pipeline`）。

默认由 `scripts/cache_doctor.py` 在「计划」阶段打印 move，显式 `--apply-rename` 时执行 `shutil.move`。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from os import path
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .naming import (
    Auxiliary_ASSFileCA,
    Auxiliary_FormatSEEPToken,
    Auxiliary_SanitizePathComponent,
    Auxiliary_SubtitleLanguageSuffixForEmby,
)
from .text_utils import Auxiliary_NormalizeChinesePunctuation

_TAG_SXEY = re.compile(r"^S(\d+)E(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class EpisodeDstRenameParams:
    """与 `sorting/pipeline.py::Sorting_Mv` 一致的命名相关参数。"""

    naming_style: str = "default"
    use_title_to_ep: bool = True
    max_filename_length: int = 180


@dataclass(frozen=True)
class EpisodeDstMove:
    tag: str
    src: Path
    dst: Path


def Auxiliary_ParseOrganizedTag(tag: str) -> Optional[Tuple[str, str]]:
    """解析 `S05E01` 风格 tag，返回 (se, ep) 字符串，与季集整理链路中 SE/EP 形式一致。"""
    if tag in [None, ""]:
        return None
    m = _TAG_SXEY.match(str(tag).strip())
    if m is None:
        return None
    return m.group(1), m.group(2)


def _pc_sanitize(component: str, max_len: int) -> str:
    return Auxiliary_SanitizePathComponent(
        Auxiliary_NormalizeChinesePunctuation(str(component or "")), max_len
    )


def BuildSortingDestPath(
    source_abs: Path,
    se: str,
    ep: str,
    new_api_name: str,
    params: EpisodeDstRenameParams,
) -> Path:
    """
    对单文件计算与 `Sorting_Mv` 相同规则的目标绝对路径；不检查源是否存在。

    `source_abs` 需为已整理落盘后的绝对路径，形如
    `.../剧名/SeasonXX/文件名.ext`（`Sorting_Mv` 的 BaseDir/Show/Season/file）。
    """
    naming_style = str(params.naming_style or "default").strip().lower()
    if naming_style not in ("default", "emby"):
        naming_style = "default"
    ml = int(params.max_filename_length or 180)
    safe_name = _pc_sanitize(new_api_name, ml)
    se_pad = Auxiliary_FormatSEEPToken(se)
    ep_pad = Auxiliary_FormatSEEPToken(ep)
    if naming_style == "emby":
        season_dir_name = f"Season {se_pad}"
    else:
        season_dir_name = f"Season{se}"
    season_san = _pc_sanitize(season_dir_name, ml)
    try:
        base_dir = source_abs.parent.parent.parent
    except Exception:
        base_dir = Path(".")

    new_dir = base_dir / safe_name / season_san

    if naming_style == "emby":
        episode_base = f"{safe_name} - S{se_pad}E{ep_pad}"
    else:
        if params.use_title_to_ep is True:
            episode_base = f"S{se}E{ep}.{safe_name}"
        else:
            episode_base = f"S{se}E{ep}"
    episode_base = _pc_sanitize(episode_base, ml)

    file_name = path.basename(str(source_abs))
    ext = path.splitext(file_name)[1].lower()
    if ext in [".ass", ".srt"]:
        if naming_style == "emby":
            new_stem = _pc_sanitize(
                f"{safe_name} - S{se_pad}E{ep_pad}{Auxiliary_SubtitleLanguageSuffixForEmby(file_name)}",
                ml,
            )
        else:
            new_stem = _pc_sanitize(episode_base + Auxiliary_ASSFileCA(file_name), ml)
    else:
        new_stem = episode_base
    return new_dir / f"{new_stem}{ext}"


def PlanEpisodeDstRenames(
    org_record: Dict[str, Any],
    new_title_zh: str,
    params: Optional[EpisodeDstRenameParams] = None,
) -> Tuple[List[EpisodeDstMove], List[str]]:
    """
    对一条 organization `records[...]` 的 `episode_last_dst` 生成 move 列表。

    返回 (moves, errors)。errors 非空时 moves 可能仍部分可用；调用方应视情况中止。
    """
    p = params or EpisodeDstRenameParams()
    new_zh = str(new_title_zh or "").strip()
    if new_zh == "":
        return [], ["剧名为空"]
    if type(org_record) is not dict:
        return [], ["organization 记录不是 dict"]
    last_map = org_record.get("episode_last_dst", {})
    if type(last_map) is not dict or not last_map:
        return [], []
    err: List[str] = []
    show_roots: List[Path] = []
    moves: List[EpisodeDstMove] = []
    dups: Dict[str, str] = {}
    for tag, raw in last_map.items():
        if tag in [None, ""] or raw in [None, ""]:
            continue
        parsed = Auxiliary_ParseOrganizedTag(str(tag))
        if parsed is None:
            err.append(f"无法解析集标签: {tag!r}")
            continue
        se, ep = parsed
        src = Path(str(raw))
        try:
            if not src.is_absolute():
                src = src.resolve()
        except Exception:
            pass
        if not src.is_file():
            err.append(f"源文件不存在: {src}")
        try:
            show_roots.append(src.parent.parent.resolve())
        except Exception as ex:
            err.append(f"{tag} 父路径错误: {ex}")
            continue
        try:
            dst = BuildSortingDestPath(src, se, ep, new_zh, p)
        except Exception as ex:
            err.append(f"{tag} 目标路径计算失败: {ex}")
            continue
        dkey = str(dst)
        if dkey in dups and dups[dkey] != str(src):
            err.append(f"目标冲突: {dkey!r} 已对应 {dups[dkey]}，又与 {src} 冲突")
        dups[dkey] = str(src)
        moves.append(EpisodeDstMove(tag=tag, src=src, dst=dst))
    if len(show_roots) > 1:
        u = {str(x) for x in show_roots}
        if len(u) > 1:
            err.append(
                "同一 canonical 的 episode_last_dst 指向不同剧集根目录: "
                + ", ".join(sorted(u)[:5])
            )
    return moves, err


def _path_equal_or_samefile(a: Path, b: Path) -> bool:
    try:
        if a == b:
            return True
        if a.is_file() and b.is_file() and a.samefile(b):
            return True
    except Exception:
        return False
    return False


def ApplyEpisodeDstRenames(
    moves: Sequence[EpisodeDstMove], *, apply: bool
) -> Tuple[bool, List[str]]:
    """
    执行重命名。`apply` 为 False 时只返回将要执行的动作描述，不写磁盘。

    返回 (ok, 日志行)。
    """
    lines: List[str] = []
    if not moves:
        lines.append("无需要移动的条目。")
        return True, lines
    for m in moves:
        lines.append(f"[{m.tag}] {m.src} -> {m.dst}")
    if not apply:
        return True, lines
    for m in moves:
        m.dst.parent.mkdir(parents=True, exist_ok=True)
        if m.dst.exists() and not _path_equal_or_samefile(m.src, m.dst):
            return False, lines + [f"目标已存在且与源非同一文件: {m.dst}"]
        if not m.src.is_file():
            return False, lines + [f"源已不存在: {m.src}，已中止，请检查缓存与磁盘是否一致。"]
    for m in moves:
        if _path_equal_or_samefile(m.src, m.dst):
            continue
        shutil.move(str(m.src), str(m.dst))
    return True, lines + ["已完成 shutil.move。"]


def PatchOrganizationRecordPaths(
    org_record: Dict[str, Any],
    moves: Sequence[EpisodeDstMove],
) -> None:
    """在内存中把 `episode_last_dst[tag]` 更新为与 moves 的 dst 一致（原地改 dict）。"""
    d = org_record.get("episode_last_dst", {})
    if type(d) is not dict:
        d = {}
    new_map = {str(k): str(v) for k, v in d.items()}
    for m in moves:
        new_map[m.tag] = str(m.dst)
    org_record["episode_last_dst"] = new_map
