# -*- coding: utf-8 -*-
"""Safely plan/apply the explicitly confirmed organize repairs.

Planning only writes --plan-out. Applying requires an unchanged plan, makes
verified backups plus a write-ahead journal, never overwrites collisions, and
atomically replaces cache JSON files.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".webm", ".wmv"}
REQUIRED_CACHE = ("titles.json", "organization.json", "cache_meta.json")
OPTIONAL_CACHE = ("manual_title_whitelist.json",)
PLAN_VERSION = 1

# Closed allow-list: the script never discovers or merges arbitrary folders.
GROUPS = (
    dict(id="kamui", sources=("新地雷战：神勇小子",), target="正后方的神威", keep="正后方的神威", drops=("新地雷战神勇小子",)),
    dict(id="imperfect", sources=("恶女不才，请多关照 ～雏宫蝶鼠换身传～",), target="虽然我是不完美恶女 ～雏宫蝶鼠替换传～", keep="虽然我是不完美恶女雏宫蝶鼠替换传", drops=("恶女不才请多关照雏宫蝶鼠换身传",)),
    dict(id="drawing", sources=("画完这个再去死",), target="描绘直至生命尽头", keep="描绘直至生命尽头", drops=("画完这个再去死",)),
    dict(id="brothers", sources=("Please.Excuse.My.Younger.Brothers",), target="我家的弟弟们真是让您费心了", keep="我家的弟弟们真是让您费心了", drops=("pleaseexcusemyyoungerbrothers",)),
    dict(id="qinling", sources=("盗墓笔记之秦岭神树",), target="最强王图鉴 ～The Ultimate Battles～", keep="最强王图鉴theultimatebattles", drops=("盗墓王theultimatebattles",), zh="最强王图鉴 ～The Ultimate Battles～", qinling=True),
    dict(id="gundam-stale", sources=("机动战士高达AGE",), archive=True),
    dict(id="dara-stale", sources=("令和妖神斑小姐",), archive=True),
    dict(id="hell-mode", sources=("地狱模式~喜欢挑战特殊成就的玩家在废设定的异世界成为无双~", "地狱模式～喜欢挑战特殊成就的玩家在废设定的异世界成为无双～"), target="地狱模式 ～喜欢速通游戏的玩家在废设定异世界无双～", keep="地狱模式喜欢速通游戏的玩家在废设定异世界无双", drops=(), zh="地狱模式 ～喜欢速通游戏的玩家在废设定异世界无双～", tvdb="457532", nfo="地狱模式 ～喜欢速通游戏的玩家在废设定异世界无双～"),
    dict(id="clevatess", sources=("Clevatess II-魔兽之王与虚假的勇者传承", "Clevatess －魔兽之王与虚假的勇者传承－"), target="克雷瓦提斯-魔兽之王与婴儿与尸之勇者-", keep="克雷瓦提斯魔兽之王与婴儿与尸之勇者", drops=("clevatess魔兽之王与虚假的勇者传承",), zh="克雷瓦提斯-魔兽之王与婴儿与尸之勇者-", tvdb="451793", nfo="克雷瓦提斯-魔兽之王与婴儿与尸之勇者-"),
)
GROUP_BY_ID = {g["id"]: g for g in GROUPS}
DANGEROUS_WHITELIST = {"病娇模拟器": "主播女孩重度依赖", "芭比之公主的力量": "主播女孩重度依赖", "魔笛MAGI": "异兽魔都"}
DANGEROUS_TITLE_ALIASES = {"bleach": "死神千年血战篇祸进谭"}
WHITELIST_ADDITIONS_BY_GROUP = {
    "qinling": {"盗墓王": "最强王图鉴 ～The Ultimate Battles～", "toukutsuou": "最强王图鉴 ～The Ultimate Battles～", "tombraiderking": "最强王图鉴 ～The Ultimate Battles～", "daomuwang": "最强王图鉴 ～The Ultimate Battles～"},
    "dara-stale": {"reiwanodarasan": "令和的斑小姐", "令和的达拉桑": "令和的斑小姐", "darasanofreiwa": "令和的斑小姐"},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("created_at must be a canonical timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("created_at must be a canonical timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat(timespec="seconds") != value:
        raise ValueError("created_at must be a canonical timezone-aware timestamp")
    return value


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def state(path):
    if not path.exists():
        return {"exists": False}
    if not path.is_file():
        return {"exists": True, "type": "other"}
    data = path.read_bytes()
    return {"exists": True, "type": "file", "size": len(data), "sha256": digest(data)}


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def absolute(raw, label):
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    return path.resolve()


def beneath(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not beneath(path, root):
        raise ValueError(f"path escapes root: {relative}")
    return path


def alias_cid(value):
    if isinstance(value, str):
        return value
    return str(value.get("canonical_id", value.get("value", ""))) if isinstance(value, dict) else ""


def repoint(value, cid):
    if isinstance(value, str):
        return cid
    if isinstance(value, dict):
        value = copy.deepcopy(value)
        value["canonical_id"] = cid
        return value
    return cid


def cache_states(cache):
    result = {name: state(cache / name) for name in REQUIRED_CACHE + OPTIONAL_CACHE}
    for name in REQUIRED_CACHE:
        if not result[name].get("exists"):
            raise ValueError(f"missing cache file: {name}")
    if load(cache / "cache_meta.json").get("schema_version") != 2:
        raise ValueError("cache schema_version must be 2")
    return result


def exact_nfo(folder, title, tvdb):
    path = folder / "tvshow.nfo"
    if not path.is_file():
        return False
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8-sig", errors="replace"))
    except ET.ParseError:
        return False
    return (root.findtext("title") or "").strip() == title and (root.findtext("tvdbid") or root.findtext("id") or "").strip() == tvdb


def destination_for(group, source, path, library):
    if group.get("archive") or (group.get("qinling") and path.suffix.lower() not in VIDEO_EXTS):
        return None
    target = library / group["target"]
    rel = path.relative_to(source)
    replacement = "盗墓王" if group.get("qinling") else group["target"]
    return target / rel.with_name(rel.name.replace(source.name, replacement))


def normalize_selected_groups(selected_groups):
    if selected_groups is None:
        return [group["id"] for group in GROUPS]
    selected = set(selected_groups)
    if len(selected) != len(selected_groups) or not selected.issubset(GROUP_BY_ID):
        unknown = sorted(selected - set(GROUP_BY_ID))
        duplicate = len(selected) != len(selected_groups)
        detail = f"unknown group IDs: {', '.join(unknown)}" if unknown else "duplicate group IDs"
        raise ValueError(f"invalid selected groups ({detail})")
    return [group["id"] for group in GROUPS if group["id"] in selected]


def build_plan(library, cache, selected_groups=None):
    selected_groups = normalize_selected_groups(selected_groups)
    selected = set(selected_groups)
    operations, blocked, warnings, active = [], [], [], []
    for group in GROUPS:
        if group["id"] not in selected:
            continue
        sources = [library / name for name in group["sources"] if (library / name).is_dir()]
        if not sources:
            continue
        active.append(group["id"])
        target = library / group["target"] if group.get("target") else None
        if target and target.exists() and not target.is_dir():
            blocked.append(f"{group['id']}: target is not a directory")
            continue
        for source in sources:
            if group.get("tvdb") and not exact_nfo(source, group["nfo"], group["tvdb"]):
                blocked.append(f"{group['id']}: {source.name!r} lacks exact NFO title/TVDB evidence")
                continue
            files = sorted((p for p in source.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
            if not files:
                warnings.append(f"{group['id']}: empty source ignored: {source.name}")
            for path in files:
                dst = destination_for(group, source, path, library)
                src_state = state(path)
                dst_state = state(dst) if dst else {"exists": False}
                action = "archive" if dst is None else "move"
                if dst_state.get("exists"):
                    action = "archive-identical" if dst_state == src_state else "blocked-divergent"
                src_rel = path.relative_to(library).as_posix()
                dst_rel = dst.relative_to(library).as_posix() if dst else None
                identity = f"{group['id']}\0{src_rel}\0{dst_rel}\0{src_state.get('sha256')}"
                operations.append({"id": digest(identity.encode())[:20], "group": group["id"], "action": action, "source": src_rel, "source_state": src_state, "destination": dst_rel, "destination_state": dst_state, "archive": (Path(group["id"]) / source.name / path.relative_to(source)).as_posix()})
    by_dst = {}
    for op in operations:
        if op["destination"]:
            by_dst.setdefault(op["destination"], []).append(op)
    for dst, ops in by_dst.items():
        hashes = {(op["source_state"]["size"], op["source_state"]["sha256"]) for op in ops}
        if len(hashes) > 1:
            blocked.append(f"divergent planned collision: {dst}")
            for op in ops:
                op["action"] = "blocked-divergent"
        elif len(ops) > 1 and not ops[0]["destination_state"].get("exists"):
            for op in ops[1:]:
                op["action"] = "archive-identical"
                op["destination_state"] = copy.deepcopy(ops[0]["source_state"])
    for op in operations:
        if op["action"] == "blocked-divergent":
            blocked.append(f"{op['group']}: divergent collision {op['source']} -> {op['destination']}")
    created_at = now()
    semantic = {"plan_schema_version": PLAN_VERSION, "library_root": str(library), "cache_dir": str(cache), "inputs": cache_states(cache), "selected_groups": selected_groups, "active_groups": active, "operations": operations, "warnings": warnings, "blocked_reasons": sorted(set(blocked)), "created_at": created_at}
    return {**semantic, "repair_id": digest(encoded(semantic))[:20], "blocked": bool(blocked)}


def plan_semantic(plan):
    return {key: plan.get(key) for key in ("plan_schema_version", "library_root", "cache_dir", "inputs", "selected_groups", "active_groups", "operations", "warnings", "blocked_reasons", "created_at")}


def plan_evidence(plan):
    semantic = plan_semantic(plan)
    semantic.pop("created_at")
    return semantic


def validate_plan(plan, library, cache):
    if not isinstance(plan, dict) or plan.get("plan_schema_version") != PLAN_VERSION:
        raise ValueError("malformed/unsupported plan")
    canonical_timestamp(plan.get("created_at"))
    if Path(plan.get("library_root", "")).resolve() != library or Path(plan.get("cache_dir", "")).resolve() != cache:
        raise ValueError("CLI roots do not match plan roots")
    if plan.get("blocked") or plan.get("blocked_reasons"):
        raise ValueError("refusing blocked plan")
    selected_groups = normalize_selected_groups(plan.get("selected_groups"))
    if plan.get("selected_groups") != selected_groups:
        raise ValueError("malformed/unsupported selected groups")
    if plan.get("active_groups") and not set(plan["active_groups"]).issubset(selected_groups):
        raise ValueError("plan active groups are not selected groups")
    if cache_states(cache) != plan.get("inputs"):
        raise ValueError("cache inputs changed after planning")
    expected = build_plan(library, cache, selected_groups)
    if plan_evidence(plan) != plan_evidence(expected):
        raise ValueError("plan operations/evidence do not match current approved repair plan")
    expected_repair_id = digest(encoded(plan_semantic(plan)))[:20]
    if plan.get("repair_id") != expected_repair_id:
        raise ValueError("plan contents do not match repair_id/approved repair plan")
    ids = set()
    for op in plan.get("operations", []):
        if op.get("id") in ids or op.get("action") not in {"move", "archive", "archive-identical"}:
            raise ValueError("malformed plan operation")
        ids.add(op["id"])
        source = safe_path(library, op["source"])
        if state(source) != op["source_state"]:
            raise ValueError(f"source changed: {source}")
        if op.get("destination"):
            dst = safe_path(library, op["destination"])
            actual = state(dst)
            expected = op["destination_state"]
            if not (op["action"] == "archive-identical" and expected.get("exists") and not actual.get("exists")) and actual != expected:
                raise ValueError(f"destination changed: {dst}")
        archive = Path(op["archive"])
        if archive.is_absolute() or ".." in archive.parts:
            raise ValueError("unsafe archive path")


def rewrite_path(value, library, group):
    path = Path(str(value))
    for source_name in group["sources"]:
        try:
            rel = path.relative_to(library / source_name)
        except ValueError:
            continue
        replacement = "盗墓王" if group.get("qinling") else group["target"]
        return str(library / group["target"] / rel.with_name(rel.name.replace(source_name, replacement)))
    return str(value)


def merge_record(records, keep, drop, library, group):
    old = records.get(drop)
    if not old:
        return
    current = records.setdefault(keep, {"canonical_id": keep, "episode_last_dst": {}, "organized_episodes": []})
    current["canonical_id"] = keep
    paths = current.setdefault("episode_last_dst", {})
    for episode, value in (old.get("episode_last_dst") or {}).items():
        value = rewrite_path(value, library, group)
        if episode in paths and os.path.normcase(str(paths[episode])) != os.path.normcase(value):
            raise ValueError(f"organization collision: {keep} {episode}")
        paths[episode] = value
    firsts = [v for v in (current.get("first_organized_at"), old.get("first_organized_at")) if v]
    lasts = [v for v in (current.get("last_organized_at"), old.get("last_organized_at")) if v]
    if firsts: current["first_organized_at"] = min(firsts)
    if lasts: current["last_organized_at"] = max(lasts)
    for field in ("title_zh", "title_en", "title_romaji"):
        if not current.get(field) and old.get(field): current[field] = old[field]
    current["organized_episodes"] = sorted(paths)
    records.pop(drop, None)


def transform_cache(cache, library, plan):
    titles, org = copy.deepcopy(load(cache / "titles.json")), copy.deepcopy(load(cache / "organization.json"))
    whitelist_path = cache / "manual_title_whitelist.json"
    whitelist = copy.deepcopy(load(whitelist_path)) if whitelist_path.exists() else None
    canonicals, aliases, records = titles.setdefault("canonicals", {}), titles.setdefault("aliases", {}), org.setdefault("records", {})
    active = set(plan["active_groups"])
    for group_id in active:
        group = GROUP_BY_ID[group_id]
        keep = group.get("keep")
        if keep:
            for drop in group.get("drops", ()):
                if drop in canonicals:
                    canonical = canonicals.setdefault(keep, copy.deepcopy(canonicals[drop]))
                    for field in ("en", "romaji"):
                        if not canonical.get(field) and canonicals[drop].get(field): canonical[field] = canonicals[drop][field]
                    for key, value in list(aliases.items()):
                        if alias_cid(value) == drop: aliases[key] = repoint(value, keep)
                    merge_record(records, keep, drop, library, group)
                    del canonicals[drop]
            canonical = canonicals.setdefault(keep, {"zh": group.get("zh", keep), "en": "", "romaji": "", "source": "cache_repair", "confidence": 95, "locked": False, "created_at": "", "last_updated": ""})
            if group.get("zh"): canonical["zh"] = group["zh"]
            canonical["source"] = "cache_repair"
            if keep in records and group.get("zh"): records[keep]["title_zh"] = group["zh"]
            for source_name in group["sources"]: aliases[source_name] = repoint(aliases.get(source_name, keep), keep)
    if "qinling" in active:
        keep, group = "最强王图鉴theultimatebattles", GROUP_BY_ID["qinling"]
        if "盗墓笔记之秦岭神树" in records:
            old = records.pop("盗墓笔记之秦岭神树")
            current = records.setdefault(keep, {"canonical_id": keep, "episode_last_dst": {}, "organized_episodes": []})
            current["canonical_id"] = keep
            paths = current.setdefault("episode_last_dst", {})
            for episode, value in (old.get("episode_last_dst") or {}).items():
                value = rewrite_path(value, library, group)
                if episode in paths and os.path.normcase(str(paths[episode])) != os.path.normcase(value):
                    raise ValueError(f"organization collision: {keep} {episode}")
                paths[episode] = value
            firsts = [v for v in (current.get("first_organized_at"), old.get("first_organized_at")) if v]
            lasts = [v for v in (current.get("last_organized_at"), old.get("last_organized_at")) if v]
            if firsts: current["first_organized_at"] = min(firsts)
            if lasts: current["last_organized_at"] = max(lasts)
            for field in ("title_zh", "title_en", "title_romaji"):
                if not current.get(field) and old.get(field): current[field] = old[field]
            current["organized_episodes"] = sorted(paths)
        c = canonicals.setdefault(keep, {})
        c.update({"zh": group["zh"], "en": c.get("en") or "Toukutsu Ou", "romaji": c.get("romaji") or "Toukutsu Ou", "source": "cache_repair"})
        for key in ("盗墓王", "tombraiderking", "toukutsuou", "daomuwang"): aliases[key] = {"canonical_id": keep, "trust_level": 100, "source": "manual_repair", "added_at": plan["created_at"]}
    if "gundam-stale" in active:
        c = canonicals.get("机动战士高达age")
        if c: c.update({"en": "Mobile Suit Gundam AGE", "romaji": "Kidou Senshi Gundam AGE", "source": "cache_repair"})
        for key in ("reiwanodarasan", "令和的达拉桑", "darasanofreiwa", "令和的妲拉桑"):
            if alias_cid(aliases.get(key)) == "机动战士高达age": aliases.pop(key, None)
        if "机动战士高达age" in records: records["机动战士高达age"].update({"title_en": "Mobile Suit Gundam AGE", "title_romaji": "Kidou Senshi Gundam AGE", "episode_last_dst": {}, "organized_episodes": []})
    if active & {"gundam-stale", "dara-stale"}:
        for key in ("reiwanodarasan", "令和的达拉桑", "darasanofreiwa"): aliases[key] = {"canonical_id": "令和的斑小姐", "trust_level": 100, "source": "manual_repair", "added_at": plan["created_at"]}
    for group_id in active:
        group = GROUP_BY_ID[group_id]
        if not group.get("target"): continue
        for record in records.values():
            paths = record.get("episode_last_dst") or {}
            for episode, value in list(paths.items()): paths[episode] = rewrite_path(value, library, group)
            record["organized_episodes"] = sorted(paths)
    dangerous_alias_groups = {
        "bleach": {"gundam-stale", "dara-stale"},
    }
    for key, expected in DANGEROUS_TITLE_ALIASES.items():
        if active & dangerous_alias_groups.get(key, set()) and alias_cid(aliases.get(key)) == expected:
            aliases.pop(key, None)
    if whitelist is not None:
        dangerous_whitelist_groups = {
            "病娇模拟器": {"clevatess"},
            "芭比之公主的力量": {"clevatess"},
            "魔笛MAGI": {"clevatess"},
        }
        for key, expected in DANGEROUS_WHITELIST.items():
            if active & dangerous_whitelist_groups.get(key, set()) and whitelist.get(key) == expected:
                del whitelist[key]
        for group_id in active:
            for key, value in WHITELIST_ADDITIONS_BY_GROUP.get(group_id, {}).items():
                if key not in whitelist:
                    whitelist[key] = value
    for key, value in aliases.items():
        if alias_cid(value) not in canonicals: raise ValueError(f"alias references missing canonical: {key}")
    for key, record in records.items():
        if record.get("canonical_id", key) not in canonicals: raise ValueError(f"organization references missing canonical: {key}")
        if record.get("organized_episodes", []) != sorted(record.get("episode_last_dst") or {}): raise ValueError(f"episode index mismatch: {key}")
    stamp = plan["created_at"]
    titles.setdefault("__meta__", {})["updated_at"] = stamp
    org.setdefault("__meta__", {})["updated_at"] = stamp
    outputs = {"titles.json": encoded(titles), "organization.json": encoded(org)}
    if whitelist is not None: outputs["manual_title_whitelist.json"] = encoded(whitelist)
    meta = copy.deepcopy(load(cache / "cache_meta.json")); meta["last_flush_at"] = stamp; sub = meta.setdefault("subfiles", {})
    sub["titles.json"] = {"sha256": digest(outputs["titles.json"]), "canonicals": len(canonicals), "aliases": len(aliases), "updated_at": stamp}
    sub["organization.json"] = {"sha256": digest(outputs["organization.json"]), "records": len(records), "updated_at": stamp}
    if whitelist is not None: sub["manual_title_whitelist.json"] = {"sha256": digest(outputs["manual_title_whitelist.json"]), "entries": len(whitelist), "updated_at": stamp}
    outputs["cache_meta.json"] = encoded(meta)
    return outputs


def journal(path, event, **details):
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"at": now(), "event": event, **details}, ensure_ascii=False, sort_keys=True) + "\n"); f.flush(); os.fsync(f.fileno())


def journal_events(path):
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            break
        if isinstance(event, dict): events.append(event)
    return events


def restore_incomplete_repair(plan, library, cache, backup, manifest, jp):
    if manifest.get("plan_sha256") != digest(encoded(plan)) or manifest.get("inputs") != plan.get("inputs"):
        raise RuntimeError(f"incomplete or mismatched repair exists; inspect {jp}")
    cache_backup = backup / "cache"
    source_backup = backup / "original_sources"
    if not cache_backup.is_dir() or not source_backup.is_dir():
        raise RuntimeError(f"incomplete repair lacks verified backups; inspect {jp}")
    for name, expected in plan["inputs"].items():
        saved = cache_backup / name
        if expected.get("exists") and state(saved) != expected:
            raise RuntimeError(f"incomplete repair has invalid cache backup: {name}")
    for op in plan["operations"]:
        saved = safe_path(source_backup, op["source"])
        if state(saved) != op["source_state"]:
            raise RuntimeError(f"incomplete repair has invalid source backup: {op['source']}")
    for name, expected in plan["inputs"].items():
        live = cache / name
        saved = cache_backup / name
        if expected.get("exists"):
            atomic_write(live, saved.read_bytes())
        else:
            live.unlink(missing_ok=True)
        if state(live) != expected:
            raise RuntimeError(f"cache recovery verification failed: {name}")
    for op in reversed(plan["operations"]):
        original = safe_path(library, op["source"])
        saved = safe_path(source_backup, op["source"])
        original.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(original, saved.read_bytes())
        if state(original) != op["source_state"]:
            raise RuntimeError(f"source recovery verification failed: {op['source']}")
        if op["action"] == "move" and op.get("destination"):
            destination = safe_path(library, op["destination"])
            if destination.is_file() and state(destination) == op["source_state"]:
                destination.unlink()
            elif destination.exists() and state(destination) != op["destination_state"]:
                raise RuntimeError(f"destination recovery conflict: {destination}")
        archive = safe_path(backup / "library", op["archive"])
        if archive.is_file() and state(archive) == op["source_state"]:
            archive.unlink()
    journal(jp, "recovery_complete")
    return backup


def apply_plan(plan, library, cache):
    if not isinstance(plan, dict) or plan.get("plan_schema_version") != PLAN_VERSION:
        raise ValueError("malformed/unsupported plan")
    canonical_timestamp(plan.get("created_at"))
    expected_repair_id = digest(encoded(plan_semantic(plan)))[:20]
    if plan.get("repair_id") != expected_repair_id:
        raise ValueError("plan contents do not match repair_id/approved repair plan")
    if Path(plan.get("library_root", "")).resolve() != library or Path(plan.get("cache_dir", "")).resolve() != cache:
        raise ValueError("CLI roots do not match plan roots")
    backup = library / f".repair_backup_{plan.get('repair_id', '')}"
    if backup.exists():
        jp = backup / "journal.jsonl"
        manifest_path = backup / "manifest.json"
        if jp.exists() and manifest_path.exists():
            manifest = load(manifest_path)
            complete = any(event.get("event") == "repair_complete" for event in journal_events(jp))
            if complete and manifest.get("plan_sha256") == digest(encoded(plan)):
                return backup
            return restore_incomplete_repair(plan, library, cache, backup, manifest, jp)
        raise RuntimeError(f"incomplete repair lacks manifest/journal; inspect {backup}")
    validate_plan(plan, library, cache)
    outputs = transform_cache(cache, library, plan)
    backup.mkdir(); jp = backup / "journal.jsonl"
    atomic_write(backup / "manifest.json", encoded({"repair_id": plan["repair_id"], "plan_sha256": digest(encoded(plan)), "inputs": plan["inputs"]}))
    journal(jp, "repair_started")
    try:
        cache_backup = backup / "cache"; cache_backup.mkdir()
        for name, expected in plan["inputs"].items():
            if expected.get("exists"):
                shutil.copy2(cache / name, cache_backup / name)
                if state(cache_backup / name) != expected: raise RuntimeError(f"backup verification failed: {name}")
        source_backup = backup / "original_sources"; source_backup.mkdir()
        for op in plan["operations"]:
            original = safe_path(library, op["source"])
            saved = safe_path(source_backup, op["source"])
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, saved)
            if state(saved) != op["source_state"]: raise RuntimeError(f"source backup verification failed: {op['source']}")
        journal(jp, "backups_complete")
        for op in plan["operations"]:
            src = safe_path(library, op["source"]); dst = safe_path(library, op["destination"]) if op.get("destination") else None; archive = safe_path(backup / "library", op["archive"])
            journal(jp, "prepared", operation_id=op["id"], action=op["action"])
            if op["action"] == "move":
                if dst.exists(): raise RuntimeError(f"refusing overwrite: {dst}")
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dst)); result = state(dst)
            else:
                if op["action"] == "archive-identical" and state(dst) != op["source_state"]: raise RuntimeError(f"collision changed: {dst}")
                if archive.exists(): raise RuntimeError(f"refusing backup overwrite: {archive}")
                archive.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(archive)); result = state(archive)
            if result != op["source_state"]: raise RuntimeError(f"operation verification failed: {op['id']}")
            journal(jp, "completed", operation_id=op["id"])
        staging = backup / "staging"; staging.mkdir()
        for name, data in outputs.items(): atomic_write(staging / name, data); load(staging / name)
        journal(jp, "cache_staged")
        for name in ("titles.json", "organization.json", "manual_title_whitelist.json", "cache_meta.json"):
            if name not in outputs: continue
            journal(jp, "cache_replace_prepared", name=name); os.replace(staging / name, cache / name)
            if state(cache / name).get("sha256") != digest(outputs[name]): raise RuntimeError(f"cache verification failed: {name}")
            journal(jp, "cache_replace_completed", name=name)
        journal(jp, "repair_complete", hashes={k: digest(v) for k, v in outputs.items()})
        return backup
    except Exception as exc:
        rollback_errors = []
        try:
            for name, expected in plan["inputs"].items():
                saved = backup / "cache" / name
                live = cache / name
                if expected.get("exists") and saved.is_file():
                    atomic_write(live, saved.read_bytes())
                elif not expected.get("exists"):
                    live.unlink(missing_ok=True)
            for op in reversed(plan["operations"]):
                original = safe_path(library, op["source"])
                saved = safe_path(backup / "original_sources", op["source"])
                if saved.is_file():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    if not original.exists():
                        shutil.copy2(saved, original)
                    if state(original) != op["source_state"]:
                        raise RuntimeError(f"source rollback verification failed: {op['source']}")
                if op["action"] == "move" and op.get("destination"):
                    destination = safe_path(library, op["destination"])
                    if destination.is_file() and state(destination) == op["source_state"]:
                        destination.unlink()
            journal(jp, "rollback_complete")
        except Exception as rollback_exc:
            rollback_errors.append(repr(rollback_exc))
            journal(jp, "rollback_failed", error=repr(rollback_exc))
        journal(jp, "repair_failed", error=repr(exc), rollback_errors=rollback_errors); raise


def print_plan(plan):
    print("MODE: DRY-RUN PLAN"); print(f"repair_id: {plan['repair_id']}")
    for op in plan["operations"]: print(f"[{op['action']}] {op['source']} -> {op.get('destination') or 'BACKUP/' + op['archive']}")
    for warning in plan["warnings"]: print(f"[warning] {warning}")
    for reason in plan["blocked_reasons"]: print(f"[blocked] {reason}")
    print(f"summary: groups={len(plan['active_groups'])} operations={len(plan['operations'])} blocked={plan['blocked']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--library-root", required=True); parser.add_argument("--cache-dir", required=True); parser.add_argument("--groups", help="comma-separated group IDs (default: all groups)")
    mode = parser.add_mutually_exclusive_group(required=True); mode.add_argument("--plan-out"); mode.add_argument("--apply-from-plan")
    try:
        args = parser.parse_args(argv); library = absolute(args.library_root, "--library-root"); cache = absolute(args.cache_dir, "--cache-dir")
        selected_groups = None if args.groups is None else [group.strip() for group in args.groups.split(",") if group.strip()]
        if beneath(library, cache) or beneath(cache, library): raise ValueError("library/cache roots must not contain each other")
        if not library.is_dir() or not cache.is_dir(): raise ValueError("library/cache roots must exist")
        if args.plan_out:
            output = absolute(args.plan_out, "--plan-out"); plan = build_plan(library, cache, selected_groups); atomic_write(output, encoded(plan)); print_plan(plan); print(f"plan: {output}"); return 1 if plan["blocked"] else 0
        plan_path = absolute(args.apply_from_plan, "--apply-from-plan"); plan = load(plan_path)
        if selected_groups is not None and plan.get("selected_groups") != normalize_selected_groups(selected_groups):
            raise ValueError("--groups does not match plan selected_groups")
        backup = apply_plan(plan, library, cache); print(f"[done] backup/journal: {backup}"); return 0
    except (ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    except Exception as exc:
        print(f"apply failed: {exc}", file=sys.stderr); return 3


if __name__ == "__main__":
    raise SystemExit(main())
