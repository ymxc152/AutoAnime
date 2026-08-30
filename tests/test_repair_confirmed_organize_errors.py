import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "repair_confirmed_organize_errors.py"
spec = importlib.util.spec_from_file_location("repair_confirmed", SCRIPT)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def cache_fixture(root, canonicals=None, aliases=None, records=None, whitelist=None):
    cache = root / "cache"
    cache.mkdir()
    write_json(cache / "titles.json", {"__meta__": {}, "canonicals": canonicals or {}, "aliases": aliases or {}})
    write_json(cache / "organization.json", {"__meta__": {}, "records": records or {}})
    write_json(cache / "cache_meta.json", {"schema_version": 2, "subfiles": {}})
    if whitelist is not None:
        write_json(cache / "manual_title_whitelist.json", whitelist)
    return cache


def nfo(folder, title, tvdb):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tvshow.nfo").write_text(f"<tvshow><title>{title}</title><tvdbid>{tvdb}</tvdbid></tvshow>", encoding="utf-8")


def test_build_plan_selects_only_requested_groups(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "地狱模式~喜欢挑战特殊成就的玩家在废设定的异世界成为无双~"
    nfo(source, "地狱模式 ～喜欢速通游戏的玩家在废设定异世界无双～", "457532")
    (source / "S01E01.mkv").write_bytes(b"episode")
    blocked = library / "Clevatess II-魔兽之王与虚假的勇者传承"; blocked.mkdir()

    plan = repair.build_plan(library.resolve(), cache.resolve(), ["hell-mode"])

    assert plan["selected_groups"] == ["hell-mode"]
    assert plan["active_groups"] == ["hell-mode"]
    assert all(op["group"] == "hell-mode" for op in plan["operations"])
    assert not any("clevatess" in reason for reason in plan["blocked_reasons"])


def test_build_plan_rejects_unknown_or_duplicate_groups(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)

    with pytest.raises(ValueError, match="invalid selected groups"):
        repair.build_plan(library.resolve(), cache.resolve(), ["missing"])
    with pytest.raises(ValueError, match="duplicate group IDs"):
        repair.build_plan(library.resolve(), cache.resolve(), ["hell-mode", "hell-mode"])


def test_validate_plan_uses_selected_groups(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    plan = repair.build_plan(library.resolve(), cache.resolve(), ["drawing"])

    repair.validate_plan(plan, library.resolve(), cache.resolve())
    plan["selected_groups"] = ["hell-mode"]
    with pytest.raises(ValueError, match="plan contents do not match repair_id"):
        repair.validate_plan(plan, library.resolve(), cache.resolve())


def test_plan_requires_exact_nfo_and_blocks_divergent_collision(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "Clevatess II-魔兽之王与虚假的勇者传承"
    nfo(source, "克雷瓦提斯-魔兽之王与婴儿与尸之勇者-", "451793")
    season = source / "Season 01"; season.mkdir(); (season / "S01E01.mkv").write_bytes(b"source")
    target = library / "克雷瓦提斯-魔兽之王与婴儿与尸之勇者-" / "Season 01"; target.mkdir(parents=True); (target / "S01E01.mkv").write_bytes(b"other")

    plan = repair.build_plan(library.resolve(), cache.resolve())

    assert plan["blocked"]
    assert any("divergent collision" in reason for reason in plan["blocked_reasons"])
    assert next(op for op in plan["operations"] if op["source"].endswith("S01E01.mkv"))["action"] == "blocked-divergent"


def test_no_nfo_explicit_single_episode_is_blocked(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "元祖！邦邦邦！" / "Season 01"; source.mkdir(parents=True)
    (source / "S01E01.mkv").write_bytes(b"episode")

    plan = repair.build_plan(library.resolve(), cache.resolve())

    assert not plan["blocked"]
    assert plan["active_groups"] == []
    assert plan["operations"] == []


def test_transform_merges_clevatess_and_preserves_alias_metadata_and_whitelist(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    keep, drop = "克雷瓦提斯魔兽之王与婴儿与尸之勇者", "clevatess魔兽之王与虚假的勇者传承"
    cache = cache_fixture(
        tmp_path,
        canonicals={keep: {"zh": "old", "en": "Keep EN", "romaji": ""}, drop: {"zh": "bad", "en": "Drop EN", "romaji": "Drop R"}},
        aliases={"structured": {"canonical_id": drop, "trust_level": 77, "source": "old"}, "bleach": keep},
        records={keep: {"canonical_id": keep, "episode_last_dst": {}, "organized_episodes": [], "first_organized_at": "2026-02-01"}, drop: {"canonical_id": drop, "episode_last_dst": {"S01E01": str(library / "Clevatess II-魔兽之王与虚假的勇者传承" / "Season 01" / "S01E01.mkv")}, "organized_episodes": ["S01E01"], "first_organized_at": "2026-01-01", "last_organized_at": "2026-03-01"}},
        whitelist={"病娇模拟器": "主播女孩重度依赖", "病娇模拟器2": "保留", "needygirloverdose": "主播女孩重度依赖", "魔笛MAGI": "用户已修正", "bleach": "bad"},
    )
    plan = {"created_at": "2026-08-26T00:00:00+00:00", "active_groups": ["clevatess"]}

    outputs = repair.transform_cache(cache, library.resolve(), plan)
    titles = json.loads(outputs["titles.json"]); org = json.loads(outputs["organization.json"]); whitelist = json.loads(outputs["manual_title_whitelist.json"])

    assert drop not in titles["canonicals"]
    assert titles["canonicals"][keep]["zh"] == "克雷瓦提斯-魔兽之王与婴儿与尸之勇者-"
    assert titles["canonicals"][keep]["en"] == "Keep EN"
    assert titles["canonicals"][keep]["romaji"] == "Drop R"
    assert titles["aliases"]["structured"] == {"canonical_id": keep, "trust_level": 77, "source": "old"}
    assert titles["aliases"]["bleach"] == keep
    assert org["records"][keep]["first_organized_at"] == "2026-01-01"
    assert org["records"][keep]["last_organized_at"] == "2026-03-01"
    assert "克雷瓦提斯-魔兽之王与婴儿与尸之勇者-" in org["records"][keep]["episode_last_dst"]["S01E01"]
    assert "病娇模拟器" not in whitelist and whitelist["病娇模拟器2"] == "保留"
    assert whitelist["needygirloverdose"] == "主播女孩重度依赖"
    assert whitelist["魔笛MAGI"] == "用户已修正"
    assert whitelist["bleach"] == "bad"


def test_transform_scopes_whitelist_and_preserves_conflicts(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(
        tmp_path,
        canonicals={"描绘直至生命尽头": {"zh": "描绘直至生命尽头"}},
        whitelist={"病娇模拟器": "主播女孩重度依赖", "盗墓王": "用户值", "honzukinogekokujou": "保留"},
    )

    outputs = repair.transform_cache(cache, library.resolve(), {"created_at": "2026-08-26T00:00:00+00:00", "active_groups": ["drawing"]})
    whitelist = json.loads(outputs["manual_title_whitelist.json"])

    assert whitelist["病娇模拟器"] == "主播女孩重度依赖"
    assert whitelist["盗墓王"] == "用户值"
    assert whitelist["honzukinogekokujou"] == "保留"


def test_transform_qinling_preserves_sentinel_record_and_conflicting_whitelist(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    keep = "最强王图鉴theultimatebattles"
    cache = cache_fixture(
        tmp_path,
        canonicals={keep: {"zh": "最强王图鉴"}},
        records={
            "盗墓笔记之秦岭神树": {"canonical_id": keep, "episode_last_dst": {}, "organized_episodes": []},
            "__qinling_repair__": {"canonical_id": keep, "episode_last_dst": {}, "organized_episodes": [], "title_zh": "保留"},
        },
        whitelist={"盗墓王": "用户值"},
    )

    outputs = repair.transform_cache(cache, library.resolve(), {"created_at": "2026-08-26T00:00:00+00:00", "active_groups": ["qinling"]})
    org = json.loads(outputs["organization.json"])
    whitelist = json.loads(outputs["manual_title_whitelist.json"])

    assert org["records"]["__qinling_repair__"]["title_zh"] == "保留"
    assert whitelist["盗墓王"] == "用户值"
    assert whitelist["toukutsuou"] == "最强王图鉴 ～The Ultimate Battles～"


def test_nested_episode_nfo_does_not_authorize_folder(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "元祖！邦邦邦！" / "Season 01"; source.mkdir(parents=True)
    (source / "episode.nfo").write_text("<episodedetails><title>元祖！BanG Dream Chan</title><tvdbid>469213</tvdbid></episodedetails>", encoding="utf-8")
    (source / "S01E01.mkv").write_bytes(b"episode")

    plan = repair.build_plan(library.resolve(), cache.resolve())

    assert not plan["blocked"]
    assert plan["active_groups"] == []
    assert not plan["operations"]


def test_apply_rejects_tampered_created_at(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "画完这个再去死" / "Season 01"; source.mkdir(parents=True)
    (source / "S01E01.mkv").write_bytes(b"episode")
    plan = repair.build_plan(library.resolve(), cache.resolve())
    plan["created_at"] = "2026-08-26T00:00:00"

    with pytest.raises(ValueError, match="canonical timezone-aware timestamp"):
        repair.apply_plan(plan, library.resolve(), cache.resolve())


def test_apply_rejects_tampered_operation(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "画完这个再去死" / "Season 01"; source.mkdir(parents=True)
    (source / "S01E01.mkv").write_bytes(b"episode")
    unrelated = library / "unrelated.mkv"; unrelated.write_bytes(b"do not move")
    plan = repair.build_plan(library.resolve(), cache.resolve())
    plan["operations"][0]["source"] = "unrelated.mkv"
    plan["operations"][0]["source_state"] = repair.state(unrelated)

    with pytest.raises(ValueError, match="approved repair plan"):
        repair.apply_plan(plan, library.resolve(), cache.resolve())

    assert unrelated.read_bytes() == b"do not move"


def test_apply_preflight_rejects_cache_drift_before_move(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    cache = cache_fixture(tmp_path)
    source = library / "画完这个再去死" / "Season 01"; source.mkdir(parents=True)
    episode = source / "S01E01.mkv"; episode.write_bytes(b"episode")
    plan = repair.build_plan(library.resolve(), cache.resolve())
    titles = json.loads((cache / "titles.json").read_text(encoding="utf-8")); titles["drift"] = True; write_json(cache / "titles.json", titles)

    with pytest.raises(ValueError, match="cache inputs changed"):
        repair.apply_plan(plan, library.resolve(), cache.resolve())

    assert episode.exists()
    assert not any(p.name.startswith(".repair_backup_") for p in library.iterdir())


def test_apply_recovers_incomplete_cache_replacement(tmp_path, monkeypatch):
    library = tmp_path / "library"; library.mkdir()
    keep, drop = "描绘直至生命尽头", "画完这个再去死"
    cache = cache_fixture(tmp_path, canonicals={keep: {"zh": keep}, drop: {"zh": drop}}, aliases={}, records={})
    source = library / drop / "Season 01"; source.mkdir(parents=True)
    episode = source / "S01E01.mkv"; episode.write_bytes(b"episode")
    plan = repair.build_plan(library.resolve(), cache.resolve())
    original_replace = repair.os.replace

    def crash_during_cache_replace(src, dst):
        if Path(dst).name == "organization.json" and Path(src).parent.name == "staging":
            raise KeyboardInterrupt("simulated crash")
        return original_replace(src, dst)

    monkeypatch.setattr(repair.os, "replace", crash_during_cache_replace)
    with pytest.raises(KeyboardInterrupt):
        repair.apply_plan(plan, library.resolve(), cache.resolve())
    monkeypatch.setattr(repair.os, "replace", original_replace)

    backup = repair.apply_plan(plan, library.resolve(), cache.resolve())

    assert episode.read_bytes() == b"episode"
    assert not (library / keep / "Season 01" / "S01E01.mkv").exists()
    assert repair.cache_states(cache) == plan["inputs"]
    assert any(event["event"] == "recovery_complete" for event in repair.journal_events(backup / "journal.jsonl"))


def test_apply_archives_identical_collision_and_writes_journal_atomically(tmp_path):
    library = tmp_path / "library"; library.mkdir()
    keep, drop = "描绘直至生命尽头", "画完这个再去死"
    cache = cache_fixture(tmp_path, canonicals={keep: {"zh": keep}, drop: {"zh": drop}}, aliases={}, records={})
    source = library / "画完这个再去死" / "Season 01"; source.mkdir(parents=True)
    target = library / "描绘直至生命尽头" / "Season 01"; target.mkdir(parents=True)
    (source / "S01E01.mkv").write_bytes(b"same"); (target / "S01E01.mkv").write_bytes(b"same")
    plan = repair.build_plan(library.resolve(), cache.resolve())

    backup = repair.apply_plan(plan, library.resolve(), cache.resolve())

    assert (target / "S01E01.mkv").read_bytes() == b"same"
    assert (backup / "library" / "drawing" / "画完这个再去死" / "Season 01" / "S01E01.mkv").read_bytes() == b"same"
    assert (backup / "original_sources" / "画完这个再去死" / "Season 01" / "S01E01.mkv").read_bytes() == b"same"
    events = [json.loads(line)["event"] for line in (backup / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events.index("prepared") < events.index("completed")
    assert events[-1] == "repair_complete"
    assert repair.apply_plan(plan, library.resolve(), cache.resolve()) == backup
