# -*- coding: utf-8 -*-
"""
缓存诊断与灾难恢复：inspect / 审计导出 / 按 audit 撤销别名 / 从 organization 重建 titles /
手工白名单写入 / 修改识别用中文名 / 按 episode_last_dst 计划或执行重命名（与 Sorting 命名一致）。

用法见 `autoanime/cache/README.md` §8、`autoanime/cache/cache_doctor_重命名与剧名纠偏_使用说明.md` 与 `docs/10_缓存Schema_v2设计.md`。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _cache_base(path: Optional[str]) -> Path:
    if path and str(path).strip():
        p = Path(path)
        return p if p.is_absolute() else (_ROOT / p)
    return _ROOT / ".cache"


def _read_json(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            o = json.load(f)
        return o if type(o) is dict else {}
    except Exception:
        return {}


def _iter_audit_lines(audit_path: Path) -> Iterator[dict]:
    if not audit_path.is_file():
        return
    with open(audit_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                if type(o) is dict:
                    yield o
            except Exception:
                continue


def cmd_inspect(cache_dir: Path) -> int:
    from autoanime.cache.trust import ALIAS_KEY_MAX_LEN
    from autoanime.cache.v2_data import Auxiliary_Sha256File, V2_VERSION

    meta = _read_json(cache_dir / "cache_meta.json")
    if not meta.get("schema_version"):
        print("未找到有效的 cache_meta.json（可能仍为旧版单文件 api_cache.json 布局）。")
        mono = cache_dir / "api_cache.json"
        if mono.is_file():
            print(f"检测到单文件缓存: {mono}  大小={mono.stat().st_size} 字节")
        return 1

    subfiles = [
        ("organization.json", "整理进度"),
        ("titles.json", "主名与别名"),
        ("api_responses.json", "API 响应（TTL）"),
        ("pollution_audit.jsonl", "审计（仅追加）"),
    ]
    print(f"schema_version={meta.get('schema_version')}  cache_dir={cache_dir}")
    if meta.get("legacy_archive"):
        print(f"legacy_archive={meta.get('legacy_archive')}")
    for name, desc in subfiles:
        p = cache_dir / name
        if not p.is_file():
            print(f"  [{desc}] {name}: 缺失")
            continue
        st = p.stat()
        h = Auxiliary_Sha256File(p)
        extra = ""
        if name == "titles.json":
            tj = _read_json(p)
            als = tj.get("aliases") or {}
            cans = tj.get("canonicals") or {}
            long_keys = [k for k in als if len(str(k)) > ALIAS_KEY_MAX_LEN]
            low_trust = sum(
                1
                for v in als.values()
                if type(v) is dict and int(v.get("trust_level", 0) or 0) < 50
            )
            extra = f"  canonicals={len(cans)} aliases={len(als)} len>{ALIAS_KEY_MAX_LEN}别名键={len(long_keys)} trust<50={low_trust}"
        elif name == "organization.json":
            oj = _read_json(p)
            recs = oj.get("records") or {}
            extra = f"  records={len(recs)}"
        elif name == "api_responses.json":
            aj = _read_json(p)
            n = 0
            tmdb = aj.get("tmdb") or {}
            if type(tmdb) is dict:
                for _k, bkt in tmdb.items():
                    if type(bkt) is dict:
                        n += len(bkt)
            bg = (aj.get("bangumi") or {}).get("titles") or {}
            if type(bg) is dict:
                n += len(bg)
            oa = ((aj.get("openai_identify") or {}).get("file_info")) or {}
            if type(oa) is dict:
                n += len(oa)
            ext = aj.get("ext") or {}
            if type(ext) is dict:
                for bkt in ext.values():
                    if type(bkt) is dict:
                        n += len(bkt)
            extra = f"  约 {n} 条缓存条目（估算）"
        elif name.endswith(".jsonl"):
            try:
                lines = sum(1 for _ in open(p, "r", encoding="utf-8"))
            except Exception:
                lines = -1
            extra = f"  行数≈{lines}"
        print(f"  [{desc}] {name}: {st.st_size} bytes  sha256={h[:16]}...{extra}")
    _ = V2_VERSION
    return 0


def _parse_since(s: str) -> float:
    dt = datetime.strptime(s[:10], "%Y-%m-%d")
    return dt.timestamp()


def cmd_export_audit(cache_dir: Path, since: str) -> int:
    ap = cache_dir / "pollution_audit.jsonl"
    t0 = _parse_since(since)
    n = 0
    for ev in _iter_audit_lines(ap):
        ts = float(ev.get("ts", 0) or 0)
        if ts >= t0:
            print(json.dumps(ev, ensure_ascii=False))
            n += 1
    print(f"# exported {n} events since {since}", file=sys.stderr)
    return 0


def cmd_revert(cache_dir: Path, audit_id: str) -> int:
    from autoanime.cache.v2_data import Auxiliary_AtomicWriteJson, Auxiliary_Sha256File, Auxiliary_WriteV2CacheMeta
    from autoanime.cache.v2_data import EMPTY_TITLES

    ap = cache_dir / "pollution_audit.jsonl"
    target: Optional[dict] = None
    for ev in _iter_audit_lines(ap):
        if str(ev.get("audit_id", "")) == str(audit_id):
            target = ev
            break
    if target is None:
        print(f"未在 {ap} 中找到 audit_id={audit_id}", file=sys.stderr)
        return 1
    et = str(target.get("type", ""))
    if et != "alias_written":
        print(f"该事件 type={et} 不可撤销（仅支持 alias_written）", file=sys.stderr)
        return 1
    ak = str(target.get("alias_key", "") or "")
    if not ak:
        print("记录缺少 alias_key", file=sys.stderr)
        return 1
    tp = cache_dir / "titles.json"
    data = _read_json(tp)
    if not data:
        data = json.loads(json.dumps(EMPTY_TITLES))
    als = data.get("aliases")
    if type(als) is not dict or ak not in als:
        print(f"titles.json 中不存在别名键: {ak!r}（可能已删除）", file=sys.stderr)
        return 0
    del als[ak]
    data["aliases"] = als
    meta = data.get("__meta__")
    if type(meta) is dict:
        meta["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
    Auxiliary_AtomicWriteJson(tp, data)
    nc = len((data.get("canonicals") or {}))
    na = len((data.get("aliases") or {}))
    Auxiliary_WriteV2CacheMeta(
        {
            "titles.json": {
                "sha256": Auxiliary_Sha256File(tp),
                "canonicals": nc,
                "aliases": na,
                "updated_at": datetime.now().replace(microsecond=0).isoformat(),
            }
        }
    )
    print(f"已移除别名 {ak!r}，请重启主程序或自行重载内存缓存。")
    return 0


def cmd_rebuild(cache_dir: Path) -> int:
    from autoanime.cache.trust import ALIAS_KEY_MAX_LEN
    from autoanime.cache.v2_data import Auxiliary_AtomicWriteJson, Auxiliary_Sha256File, Auxiliary_WriteV2CacheMeta, V2_VERSION
    from autoanime.text_utils import Auxiliary_NormalizeAliasKey, Auxiliary_NormalizeApiTitle, Auxiliary_NormalizeDisplayTitle

    op = cache_dir / "organization.json"
    org = _read_json(op)
    recs = org.get("records") or {}
    if type(recs) is not dict or not recs:
        print("organization.json 无 records，放弃重建。", file=sys.stderr)
        return 1
    root: Dict[str, Any] = {
        "__meta__": {
            "schema_version": V2_VERSION,
            "updated_at": datetime.now().replace(microsecond=0).isoformat(),
        },
        "canonicals": {},
        "aliases": {},
    }
    for cid, rec in recs.items():
        if type(rec) is not dict:
            continue
        c = str(rec.get("canonical_id") or cid)
        zh = Auxiliary_NormalizeApiTitle(str(rec.get("title_zh", "")))
        en = Auxiliary_NormalizeDisplayTitle(str(rec.get("title_en", "")))
        rj = Auxiliary_NormalizeDisplayTitle(str(rec.get("title_romaji", "")))
        root["canonicals"][c] = {
            "zh": zh,
            "en": en,
            "romaji": rj,
            "source": "rebuild_from_organization",
            "confidence": 90,
            "locked": False,
            "created_at": str(rec.get("first_organized_at", "")),
            "last_updated": datetime.now().replace(microsecond=0).isoformat(),
        }
        for piece, src in ((zh, "zh"), (en, "en"), (rj, "romaji")):
            if piece in [None, ""]:
                continue
            ak = Auxiliary_NormalizeAliasKey(piece)
            if not ak or len(ak) > ALIAS_KEY_MAX_LEN:
                continue
            root["aliases"][ak] = {
                "canonical_id": c,
                "trust_level": 85,
                "source": f"rebuild:{src}",
                "added_at": datetime.now().replace(microsecond=0).isoformat(),
            }
    tp = cache_dir / "titles.json"
    Auxiliary_AtomicWriteJson(tp, root)
    nc = len(root["canonicals"])
    na = len(root["aliases"])
    Auxiliary_WriteV2CacheMeta(
        {
            "titles.json": {
                "sha256": Auxiliary_Sha256File(tp),
                "canonicals": nc,
                "aliases": na,
                "updated_at": datetime.now().replace(microsecond=0).isoformat(),
            }
        }
    )
    print(f"已写入 {tp}：canonicals={nc} aliases={na}（请备份后使用；会覆盖现有 titles.json）")
    return 0


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _find_org_record(
    org: Dict[str, Any], canonical_id: Optional[str], old_title_zh: Optional[str]
) -> Tuple[Optional[str], Optional[dict]]:
    from autoanime.text_utils import Auxiliary_NormalizeApiTitle

    recs = org.get("records") or {}
    if type(recs) is not dict:
        return None, None
    cid = str(canonical_id or "").strip()
    if cid != "":
        if cid in recs and type(recs[cid]) is dict:
            return cid, recs[cid]
        for k, v in recs.items():
            if type(v) is dict and str(v.get("canonical_id", "") or "") == cid:
                return str(k), v
        return None, None
    o = Auxiliary_NormalizeApiTitle(str(old_title_zh or ""))
    if o in [None, ""]:
        return None, None
    matches: List[Tuple[str, dict]] = []
    for k, v in recs.items():
        if type(v) is not dict:
            continue
        if Auxiliary_NormalizeApiTitle(str(v.get("title_zh", ""))) == o:
            matches.append((str(k), v))
    if len(matches) == 1:
        return matches[0]
    return None, None


def _write_org_titles_meta(
    cache_dir: Path,
    org_data: dict,
    titles_data: dict,
) -> None:
    from autoanime.cache.v2_data import (
        Auxiliary_AtomicWriteJson,
        Auxiliary_Sha256File,
        Auxiliary_WriteV2CacheMeta,
    )

    op = cache_dir / "organization.json"
    tp = cache_dir / "titles.json"
    meta_o = org_data.get("__meta__")
    if type(meta_o) is dict:
        meta_o["updated_at"] = _now_iso()
    meta_t = titles_data.get("__meta__")
    if type(meta_t) is dict:
        meta_t["updated_at"] = _now_iso()
    Auxiliary_AtomicWriteJson(op, org_data)
    Auxiliary_AtomicWriteJson(tp, titles_data)
    nr = len((org_data.get("records") or {})) if type(org_data.get("records")) is dict else 0
    nc = len((titles_data.get("canonicals") or {})) if type(titles_data.get("canonicals")) is dict else 0
    na = len((titles_data.get("aliases") or {})) if type(titles_data.get("aliases")) is dict else 0
    Auxiliary_WriteV2CacheMeta(
        {
            "organization.json": {
                "sha256": Auxiliary_Sha256File(op),
                "records": nr,
                "updated_at": _now_iso(),
            },
            "titles.json": {
                "sha256": Auxiliary_Sha256File(tp),
                "canonicals": nc,
                "aliases": na,
                "updated_at": _now_iso(),
            },
        }
    )


def _patch_canonical_zh(titles_data: dict, canonical_id: str, new_zh: str) -> None:
    cans = titles_data.get("canonicals")
    if type(cans) is not dict:
        cans = {}
        titles_data["canonicals"] = cans
    cid = str(canonical_id)
    rec = cans.get(cid)
    if type(rec) is not dict:
        cans[cid] = {
            "zh": new_zh,
            "en": "",
            "romaji": "",
            "source": "cache_doctor",
            "confidence": 90,
            "locked": False,
            "last_updated": _now_iso(),
        }
    else:
        rec["zh"] = new_zh
        rec["last_updated"] = _now_iso()


def cmd_set_whitelist(
    cache_dir: Path,
    alias: str,
    title_zh: str,
    apply_rename: bool,
    canonical_id: str,
    old_title_zh: str,
    naming_style: str,
    use_title_to_ep: bool,
) -> int:
    from autoanime.cache.v2_data import EMPTY_TITLES
    from autoanime.episode_dst_rename import (
        ApplyEpisodeDstRenames,
        EpisodeDstRenameParams,
        PatchOrganizationRecordPaths,
        PlanEpisodeDstRenames,
    )
    from autoanime.text_utils import Auxiliary_NormalizeAliasKey, Auxiliary_NormalizeApiTitle

    ak = Auxiliary_NormalizeAliasKey(alias)
    tv = Auxiliary_NormalizeApiTitle(title_zh)
    if ak in [None, ""] or tv in [None, ""]:
        print("别名或中文剧名归一后为空，已放弃。", file=sys.stderr)
        return 2
    wpath = cache_dir / "manual_title_whitelist.json"
    wdata = _read_json(wpath)
    if not wdata and wpath.is_file() is False:
        wdata = {}
    wdata[str(ak)] = tv
    with open(wpath, "w", encoding="utf-8") as f:
        json.dump(wdata, f, ensure_ascii=False, indent=2)
    print(f"已写入白名单: {wpath!s}  {ak!r} -> {tv!r}")

    if not apply_rename:
        print("已跳过磁盘重命名（未传 --apply-rename）。")
        return 0

    org_data = _read_json(cache_dir / "organization.json")
    if type(org_data) is not dict:
        org_data = {}
    if not org_data.get("__meta__"):
        org_data["__meta__"] = {"schema_version": 2, "updated_at": _now_iso()}
    tpath = cache_dir / "titles.json"
    titles_data = _read_json(tpath)
    if not titles_data or type(titles_data.get("canonicals")) is not dict:
        titles_data = json.loads(json.dumps(EMPTY_TITLES))
    rkey, rec = _find_org_record(org_data, canonical_id or None, old_title_zh or None)
    if rec is None or rkey is None:
        print(
            "无法解析待重命名条目：请传 --canonical-id，或传能在 organization.records 中唯一命中的 --old-title-zh。",
            file=sys.stderr,
        )
        return 1
    params = EpisodeDstRenameParams(
        naming_style=naming_style,
        use_title_to_ep=use_title_to_ep,
    )
    moves, errs = PlanEpisodeDstRenames(rec, tv, params)
    for e in errs:
        print(f"[plan] {e}", file=sys.stderr)
    if errs:
        return 1
    ok, log_lines = ApplyEpisodeDstRenames(moves, apply=apply_rename)
    for line in log_lines:
        print(line)
    if not ok:
        return 1
    if apply_rename and moves:
        PatchOrganizationRecordPaths(rec, moves)
    rec["title_zh"] = tv
    recs = org_data.get("records") or {}
    if type(recs) is not dict:
        recs = {}
    recs[rkey] = rec
    org_data["records"] = recs
    _patch_canonical_zh(titles_data, str(rec.get("canonical_id", rkey)), tv)
    _write_org_titles_meta(cache_dir, org_data, titles_data)
    print("已同步 organization.json 与 titles.json 中的中文主名。请重启主程序以加载新缓存。")
    return 0


def cmd_set_title_zh(
    cache_dir: Path,
    canonical_id: str,
    title_zh: str,
    apply_rename: bool,
    naming_style: str,
    use_title_to_ep: bool,
) -> int:
    from autoanime.cache.v2_data import EMPTY_TITLES
    from autoanime.episode_dst_rename import (
        ApplyEpisodeDstRenames,
        EpisodeDstRenameParams,
        PatchOrganizationRecordPaths,
        PlanEpisodeDstRenames,
    )
    from autoanime.text_utils import Auxiliary_NormalizeApiTitle

    cid = str(canonical_id or "").strip()
    if cid == "":
        print("需要 --canonical-id", file=sys.stderr)
        return 2
    new_zh = Auxiliary_NormalizeApiTitle(title_zh)
    if new_zh in [None, ""]:
        print("新标题归一后为空", file=sys.stderr)
        return 2
    org_data = _read_json(cache_dir / "organization.json")
    if type(org_data) is not dict:
        org_data = {}
    if type(org_data.get("__meta__")) is not dict:
        org_data["__meta__"] = {"schema_version": 2, "updated_at": _now_iso()}
    titles_data = _read_json(cache_dir / "titles.json")
    if not titles_data or type(titles_data.get("canonicals")) is not dict:
        titles_data = json.loads(json.dumps(EMPTY_TITLES))
    rkey, rec = _find_org_record(org_data, cid, None)
    if rec is None or rkey is None:
        print(f"未找到 canonical_id / 记录键: {cid!r}", file=sys.stderr)
        return 1
    if apply_rename:
        params = EpisodeDstRenameParams(
            naming_style=naming_style,
            use_title_to_ep=use_title_to_ep,
        )
        moves, errs = PlanEpisodeDstRenames(rec, new_zh, params)
        for e in errs:
            print(f"[plan] {e}", file=sys.stderr)
        if errs:
            return 1
        ok, log_lines = ApplyEpisodeDstRenames(moves, apply=True)
        for line in log_lines:
            print(line)
        if not ok:
            return 1
        if moves:
            PatchOrganizationRecordPaths(rec, moves)
    rec["title_zh"] = new_zh
    recs = org_data.get("records") or {}
    if type(recs) is not dict:
        recs = {}
    recs[rkey] = rec
    org_data["records"] = recs
    c_for_title = str(rec.get("canonical_id", rkey))
    _patch_canonical_zh(titles_data, c_for_title, new_zh)
    _write_org_titles_meta(cache_dir, org_data, titles_data)
    print("已更新 titles.json 与 organization.json。请重启主程序以加载新缓存。")
    return 0


def cmd_rename_episodes(
    cache_dir: Path,
    canonical_id: str,
    old_title_zh: str,
    title_zh: str,
    apply_rename: bool,
    naming_style: str,
    use_title_to_ep: bool,
) -> int:
    """
    仅使用 organization 的 episode_last_dst 做与 Sorting 一致的路径计算：
    默认只打印 move 计划、不写任何 JSON、不 move；加 --apply-rename 时执行 move 并回写 organization / titles。
    """
    from autoanime.cache.v2_data import EMPTY_TITLES
    from autoanime.episode_dst_rename import (
        ApplyEpisodeDstRenames,
        EpisodeDstRenameParams,
        PatchOrganizationRecordPaths,
        PlanEpisodeDstRenames,
    )
    from autoanime.text_utils import Auxiliary_NormalizeApiTitle

    new_zh = Auxiliary_NormalizeApiTitle(title_zh)
    if new_zh in [None, ""]:
        print("--zh 归一后为空", file=sys.stderr)
        return 2
    org_data = _read_json(cache_dir / "organization.json")
    if type(org_data) is not dict:
        org_data = {}
    rkey, rec = _find_org_record(org_data, canonical_id or None, old_title_zh or None)
    if rec is None or rkey is None:
        print(
            "未找到记录：请传 --canonical-id，或传能在 organization 中唯一条的 --old-title-zh。",
            file=sys.stderr,
        )
        return 1
    params = EpisodeDstRenameParams(
        naming_style=naming_style,
        use_title_to_ep=use_title_to_ep,
    )
    moves, errs = PlanEpisodeDstRenames(rec, new_zh, params)
    for e in errs:
        print(f"[plan] {e}", file=sys.stderr)
    if errs:
        return 1
    ok, log_lines = ApplyEpisodeDstRenames(moves, apply=bool(apply_rename))
    for line in log_lines:
        print(line)
    if not ok:
        return 1
    if not apply_rename:
        print("# 以上为预览；未加 --apply-rename，未修改 organization / titles / 磁盘。")
        return 0
    titles_data = _read_json(cache_dir / "titles.json")
    if not titles_data or type(titles_data.get("canonicals")) is not dict:
        titles_data = json.loads(json.dumps(EMPTY_TITLES))
    if type(org_data.get("__meta__")) is not dict:
        org_data["__meta__"] = {"schema_version": 2, "updated_at": _now_iso()}
    if moves:
        PatchOrganizationRecordPaths(rec, moves)
    rec["title_zh"] = new_zh
    recs = org_data.get("records") or {}
    if type(recs) is not dict:
        recs = {}
    recs[rkey] = rec
    org_data["records"] = recs
    c_for_title = str(rec.get("canonical_id", rkey))
    _patch_canonical_zh(titles_data, c_for_title, new_zh)
    _write_org_titles_meta(cache_dir, org_data, titles_data)
    print("已重命名并同步 organization / titles。请重启主程序以加载新缓存。")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="AutoAnime Schema v2 缓存诊断")
    p.add_argument("--cache-dir", default="", help="缓存目录（默认项目下 .cache）")
    sub = p.add_mutually_exclusive_group(required=True)
    sub.add_argument("--inspect", action="store_true", help="子文件体积、sha256、条目与污染嫌疑统计")
    sub.add_argument("--export-audit", action="store_true", help="导出审计（需配合 --since）")
    sub.add_argument("--revert", action="store_true", help="按 audit 撤销（需配合 --audit-id）")
    sub.add_argument("--rebuild-from-organization", action="store_true", help="由 organization.json 重建 titles.json")
    sub.add_argument("--set-whitelist", action="store_true", help="写入 manual_title_whitelist（需 --alias 与 --zh）")
    sub.add_argument("--set-title-zh", action="store_true", help="更新识别用中文主名到 titles+organization（需 --canonical-id 与 --zh）")
    sub.add_argument(
        "--rename-episodes",
        action="store_true",
        help="仅按 episode_last_dst 做重命名计划或执行（需 --zh 与 --canonical-id 或 --old-title-zh；默认只预览，不加本开关不移动）",
    )
    p.add_argument("--since", default="", help="仅 --export-audit：YYYY-MM-DD 起")
    p.add_argument("--audit-id", default="", help="仅 --revert：pollution_audit.jsonl 中的 audit_id")
    p.add_argument("--alias", default="", help="仅 --set-whitelist：别名字符串")
    p.add_argument(
        "--zh",
        default="",
        help="set-whitelist / set-title-zh 的中文；rename-episodes 的目标中文主名（新目录/新文件名用）",
    )
    p.add_argument(
        "--canonical-id",
        default="",
        help="canonical id；--set-title-zh 必填；--rename-episodes 与 --set-whitelist+--apply-rename 时可与 --old-title-zh 二选一以定位记录",
    )
    p.add_argument(
        "--old-title-zh",
        default="",
        help="以归一后的 title_zh 在 organization.records 中唯一条；用于 set-whitelist+apply 或 --rename-episodes",
    )
    p.add_argument(
        "--apply-rename",
        action="store_true",
        help="实际执行 shutil.move 并回写 organization/titles；未加时：--rename-episodes 仅打印计划；--set-title-zh 只改 JSON；--set-whitelist 只写白名单",
    )
    p.add_argument("--naming-style", default="default", choices=["default", "emby"], help="与主程序 NAMING_STYLE 一致")
    p.add_argument(
        "--no-use-title-to-ep",
        action="store_true",
        help="对应 USETITLTOEP=False 的文件名（SxxExx 不拼剧名）",
    )
    args = p.parse_args(argv)
    base = _cache_base(args.cache_dir or None)
    use_title_to_ep = not bool(args.no_use_title_to_ep)

    if args.inspect:
        return cmd_inspect(base)
    if args.export_audit:
        if not args.since:
            print("需要 --since YYYY-MM-DD", file=sys.stderr)
            return 2
        return cmd_export_audit(base, args.since)
    if args.revert:
        if not args.audit_id:
            print("需要 --audit-id", file=sys.stderr)
            return 2
        return cmd_revert(base, args.audit_id)
    if args.rebuild_from_organization:
        return cmd_rebuild(base)
    if args.set_whitelist:
        if not str(args.alias or "").strip() or not str(args.zh or "").strip():
            print("--set-whitelist 需要同时指定 --alias 与 --zh", file=sys.stderr)
            return 2
        if args.apply_rename and not str(args.canonical_id or "").strip() and not str(args.old_title_zh or "").strip():
            print("--set-whitelist 与 --apply-rename 时需要 --canonical-id 或 --old-title-zh 之一", file=sys.stderr)
            return 2
        return cmd_set_whitelist(
            base,
            str(args.alias).strip(),
            str(args.zh).strip(),
            bool(args.apply_rename),
            str(args.canonical_id or "").strip(),
            str(args.old_title_zh or "").strip(),
            str(args.naming_style or "default").strip().lower(),
            use_title_to_ep,
        )
    if args.set_title_zh:
        if not str(args.canonical_id or "").strip() or not str(args.zh or "").strip():
            print("--set-title-zh 需要同时指定 --canonical-id 与 --zh", file=sys.stderr)
            return 2
        return cmd_set_title_zh(
            base,
            str(args.canonical_id or "").strip(),
            str(args.zh).strip(),
            bool(args.apply_rename),
            str(args.naming_style or "default").strip().lower(),
            use_title_to_ep,
        )
    if args.rename_episodes:
        if not str(args.zh or "").strip():
            print("--rename-episodes 需要 --zh（目标中文主名）", file=sys.stderr)
            return 2
        if not str(args.canonical_id or "").strip() and not str(args.old_title_zh or "").strip():
            print("--rename-episodes 需要 --canonical-id 或 --old-title-zh 之一", file=sys.stderr)
            return 2
        return cmd_rename_episodes(
            base,
            str(args.canonical_id or "").strip(),
            str(args.old_title_zh or "").strip(),
            str(args.zh).strip(),
            bool(args.apply_rename),
            str(args.naming_style or "default").strip().lower(),
            use_title_to_ep,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
