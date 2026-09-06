"""cli import 单测（收尾接线）：扫描 → 路由分流 → 归档/入队 → dry-run。

全离线：识别走真实 LocalRecognizer（纯函数）；L2 用真实空库（miss → l3 路由）；
LLM 与参考源显式关闭（llm_enabled=false / reference_enabled=false）。归档断言
用 tmp_path 内真实文件（hardlink 同盘语义），不触网、不碰真实媒体库。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from autoanime.cli import _handle_import_outcome, main
from autoanime.config import Settings
from autoanime.core.enums import Confidence, Segment
from autoanime.core.interfaces import ParseResult
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.orchestrator import ROUTE_ARCHIVE, RouteOutcome
from autoanime.scheduler.store import LoopStore

HIGH_NAME = "Bocchi.the.Rock.S01E01.1080p.WEBRip.x264.mkv"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """隔离环境：tmp 库 + tmp 媒体库 + LLM/参考源关闭。"""
    db = tmp_path / "cli.db"
    library = tmp_path / "library"
    monkeypatch.setenv("AUTOANIME_DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    monkeypatch.setenv("AUTOANIME_LIBRARY_PATH", library.as_posix())
    monkeypatch.setenv("AUTOANIME_LLM_ENABLED", "false")
    monkeypatch.setenv("AUTOANIME_REFERENCE_ENABLED", "false")
    return {"db": db, "library": library, "root": tmp_path}


def _run_cli(*args: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def _make_tree(root: Path, names: dict[str, bytes]) -> Path:
    source = root / "downloads"
    source.mkdir(exist_ok=True)
    for name, content in names.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return source


def _pending_rows(db: Path) -> list[tuple[str, str, str, str, str]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT raw_name, stage, reason, status, context FROM pending_queue"
            " ORDER BY id"
        ).fetchall()


# ---------------------------------------------------------------- 端到端冒烟


def test_import_writes_parse_events_rows(env: dict[str, Path]) -> None:
    """回归（R1 验收）：import 每个文件落一行 parse_events（E1 报表写侧）。

    修复前 parse_events 无任何生产写入路径，report 的 llm_call_rate 与
    archived_events 分母恒为 0；修复后由 orchestrator 的 metrics_sink 落库。
    """
    source = _make_tree(env["root"], {HIGH_NAME: b"high", "Frieren - 01.mkv": b"m1"})
    _run_cli("import", str(source), "--dry-run")
    _run_cli("import", str(source))

    with sqlite3.connect(env["db"]) as conn:
        rows = conn.execute(
            "SELECT event_date, level, llm_called, outcome, latency_ms"
            " FROM parse_events ORDER BY id"
        ).fetchall()
    # dry-run 不落库；实跑每个扫描文件一行（HIGH 归档 + MEDIUM 入队）。
    assert len(rows) == 2
    levels = {row[1] for row in rows}
    outcomes = {row[3] for row in rows}
    assert levels == {2, 3}  # HIGH=3，MEDIUM=2
    assert "archive" in outcomes
    assert all(row[4] is not None and row[4] >= 0 for row in rows)


def test_import_archives_high_and_enqueues_medium(env: dict[str, Path]) -> None:
    """1 个 HIGH 名归档 + 2 个 MEDIUM 名入队；原文件保留（D21 hardlink 语义）。"""
    source = _make_tree(
        env["root"],
        {
            HIGH_NAME: b"high",
            "Frieren - 01.mkv": b"m1",
            "Frieren - 02.mkv": b"m2",
        },
    )
    code, out, _err = _run_cli("import", source.as_posix())
    assert code == 0
    payload = json.loads(out)
    assert payload["total"] == 3
    assert payload["scanned"] == 3
    assert payload["routes"] == {"archive": 1, "l3": 2}
    assert payload["archived"] == 1
    assert payload["pending"] == 2
    assert payload["failed"] == 0

    # 归档：D17 命名（质量 token 取自候选名）+ D9 hardlink 优先（同盘 tmp），
    # 原文件仍在（做种侧不动）
    archived_items = [i for i in payload["items"] if i["action"] == "archive"]
    assert len(archived_items) == 1
    assert archived_items[0]["strategy"] == "hardlink"
    dst = Path(str(archived_items[0]["dst"]))
    assert dst.exists()
    assert dst == (
        env["library"] / "Bocchi the Rock" / "Season 01"
        / "Bocchi the Rock - S01E01.1080p.mkv"
    )
    assert (source / HIGH_NAME).exists()

    # pending：context 携带识别草稿契约键（web 确认/纠正流可直接消费）
    rows = _pending_rows(env["db"])
    assert len(rows) == 2
    for raw_name, stage, reason, status, context in rows:
        assert raw_name.startswith("Frieren")
        assert stage == "import"
        assert reason == "l3:medium"
        assert status == "pending"
        draft = json.loads(str(context))
        assert draft["title"] == "Frieren"
        assert draft["episode"] in (1, 2)
        assert draft["segment"] == "episode"
        assert draft["folder"] == source.name
        assert Path(str(draft["parent_path"])) == source
        assert draft["route"] == "l3"
        assert draft["level"] == "medium"

    # 归档簿记：audit 行带 reverse moves（与 E4 归档同口径，可回滚）
    with sqlite3.connect(env["db"]) as conn:
        audits = conn.execute(
            "SELECT action, instruction, reverse FROM audit_log"
            " WHERE action = 'episode.organized'"
        ).fetchall()
    assert len(audits) == 1
    assert "import" in str(audits[0][1])
    assert "moves" in str(audits[0][2])


def test_import_dry_run_plans_without_touching_anything(env: dict[str, Path]) -> None:
    source = _make_tree(
        env["root"],
        {
            HIGH_NAME: b"high",
            "Frieren - 01.mkv": b"m1",
            "Frieren - 02.mkv": b"m2",
        },
    )
    code, out, _err = _run_cli("import", source.as_posix(), "--dry-run")
    assert code == 0
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["archived"] == 1  # 计划数（将发生的动作）
    assert payload["pending"] == 2
    archived_items = [i for i in payload["items"] if i["action"] == "archive"]
    assert len(archived_items) == 1
    assert Path(str(archived_items[0]["dst"])) == (
        env["library"] / "Bocchi the Rock" / "Season 01"
        / "Bocchi the Rock - S01E01.1080p.mkv"
    )
    # 不落库不归档：媒体库目录未创建，pending_queue 无行
    assert not (env["library"] / "Bocchi the Rock").exists()
    assert _pending_rows(env["db"]) == []


def test_import_skips_hidden_temp_and_non_video(env: dict[str, Path]) -> None:
    source = _make_tree(
        env["root"],
        {
            ".hidden.mkv": b"h",
            "~temp clip.mkv": b"t",
            "readme.txt": b"r",
            HIGH_NAME: b"high",
        },
    )
    code, out, _err = _run_cli("import", source.as_posix())
    assert code == 0
    payload = json.loads(out)
    assert payload["total"] == 4
    assert payload["scanned"] == 1
    assert payload["archived"] == 1
    # 唯一入选的视频文件（隐藏/~temp/txt 均被跳过）
    assert [i["file"] for i in payload["items"]] == [str(source / HIGH_NAME)]


def test_import_missing_directory_fails_cleanly(env: dict[str, Path]) -> None:
    code, out, _err = _run_cli("import", (env["root"] / "nope").as_posix())
    assert code == 2
    assert "not a directory" in out


# ------------------------------------------------- 单元：路由结果处理分支


def _db(tmp_path: Path) -> SqliteStorage:
    return SqliteStorage(f"sqlite+aiosqlite:///{(tmp_path / 'unit.db').as_posix()}")


def test_episodeless_high_archive_route_goes_to_pending(tmp_path: Path) -> None:
    """HIGH 但给不出集号（季包等）：不硬归档，按人工处理入队。"""

    async def scenario() -> tuple[dict[str, object], int]:
        storage = _db(tmp_path)
        await storage.create_all()
        try:
            file = tmp_path / "Show.S01.mkv"
            file.write_bytes(b"x")
            result = ParseResult(
                title="Show", season=1, episode=None, segment=Segment.EPISODE,
                fansub=None, level=Confidence.HIGH, confidence=1.0,
            )
            item = await _handle_import_outcome(
                file, RouteOutcome(result, ROUTE_ARCHIVE),
                settings=Settings(library_path=tmp_path / "library"),
                store=LoopStore(storage), governance=MemoryGovernance(storage),
                dry_run=False,
            )
            _rows, total = await LoopStore(storage).list_pending()
            return item, total
        finally:
            await storage.close()

    item, total = asyncio.run(scenario())
    assert item["action"] == "pending"
    assert item["reason"] == "archive route without episode number"
    assert total == 1


def test_movie_segment_high_archives_without_season_template(tmp_path: Path) -> None:
    """剧场版分支：不套 Season/E 模板（naming movie 分支）。"""

    async def scenario() -> dict[str, object]:
        storage = _db(tmp_path)
        await storage.create_all()
        try:
            src_dir = tmp_path / "movie dir"
            src_dir.mkdir()
            file = src_dir / "A Silent Voice Movie.mkv"
            file.write_bytes(b"x")
            result = ParseResult(
                title="A Silent Voice", season=None, episode=None,
                segment=Segment.MOVIE, fansub=None,
                level=Confidence.HIGH, confidence=0.99,
            )
            return await _handle_import_outcome(
                file, RouteOutcome(result, ROUTE_ARCHIVE),
                settings=Settings(library_path=tmp_path / "library"),
                store=LoopStore(storage), governance=MemoryGovernance(storage),
                dry_run=False,
            )
        finally:
            await storage.close()

    item = asyncio.run(scenario())
    assert item["action"] == "archive"
    dst = Path(str(item["dst"]))
    assert dst.exists()
    assert dst.parent.name == "A Silent Voice"
    assert dst.name == "A Silent Voice.SD.mkv"  # 无分辨率 token → SD


def test_dry_run_pending_item_writes_no_row(tmp_path: Path) -> None:
    async def scenario() -> tuple[dict[str, object], int]:
        storage = _db(tmp_path)
        await storage.create_all()
        try:
            file = tmp_path / "Frieren - 01.mkv"
            file.write_bytes(b"x")
            result = ParseResult(
                title="Frieren", season=None, episode=1, segment=Segment.EPISODE,
                fansub=None, level=Confidence.LOW, confidence=0.3,
            )
            item = await _handle_import_outcome(
                file, RouteOutcome(result, "l3"),
                settings=Settings(library_path=tmp_path / "library"),
                store=LoopStore(storage), governance=MemoryGovernance(storage),
                dry_run=True,
            )
            _rows, total = await LoopStore(storage).list_pending()
            return item, total
        finally:
            await storage.close()

    item, total = asyncio.run(scenario())
    assert item["action"] == "pending"
    assert item["reason"] == "l3:low"
    assert "pending_id" not in item
    assert total == 0


# ------------------------------------------------- 单元：重跑幂等（R2 验收）


def test_import_rerun_skips_already_archived_and_pending(env: dict[str, Path]) -> None:
    """R2 验收：同一目录连跑两遍 import，第二遍 scanned 全部 skipped 零新增。

    已归档（audit episode.organized 同源文件名）→ reason=already-archived；
    已在等人工（未决 pending 行）→ reason=already-pending；两遍的 audit 与
    pending 行数一致（零重复 hardlink/audit/pending）。
    """
    source = _make_tree(env["root"], {HIGH_NAME: b"high", "Frieren - 01.mkv": b"m1"})
    first = json.loads(_run_cli("import", source.as_posix())[1])
    assert first["archived"] == 1 and first["pending"] == 1
    second = json.loads(_run_cli("import", source.as_posix())[1])
    assert second["scanned"] == 2
    assert second["archived"] == 0
    assert second["pending"] == 0
    assert second["failed"] == 0
    assert second["skipped"] == 2
    reasons = {Path(str(i["file"])).name: i["reason"] for i in second["items"]}
    assert reasons == {HIGH_NAME: "already-archived", "Frieren - 01.mkv": "already-pending"}

    # 簿记零新增：audit / pending 行数与第一遍一致
    with sqlite3.connect(env["db"]) as conn:
        audit_count = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action = 'episode.organized'"
        ).fetchone()[0]
        pending_count = conn.execute("SELECT count(*) FROM pending_queue").fetchone()[0]
    assert audit_count == 1
    assert pending_count == 1

    # dry-run 的幂等预览同样如实（不写库，只标记）
    preview = json.loads(_run_cli("import", source.as_posix(), "--dry-run")[1])
    assert preview["skipped"] == 2 and preview["archived"] == 0 and preview["pending"] == 0


def test_import_rerun_reprocesses_file_after_pending_resolved(env: dict[str, Path]) -> None:
    """pending 行被人工解决（confirm/correct 置 resolved）后，重跑放行该文件。"""
    source = _make_tree(env["root"], {HIGH_NAME: b"high", "Frieren - 01.mkv": b"m1"})
    _run_cli("import", source.as_posix())
    with sqlite3.connect(env["db"]) as conn:
        conn.execute("UPDATE pending_queue SET status = 'resolved'")
        conn.commit()
    second = json.loads(_run_cli("import", source.as_posix())[1])
    assert second["skipped"] == 1  # 只有已归档文件跳过
    reasons = {Path(str(i["file"])).name: i["reason"] for i in second["items"]}
    assert reasons[HIGH_NAME] == "already-archived"
    # 放行文件重新识别 → 重新入队（L2 仍 miss，LLM 关闭走 l3 草稿）
    assert reasons["Frieren - 01.mkv"] == "l3:medium"
    assert second["pending"] == 1


def test_import_dry_run_creates_no_idempotence_barrier(env: dict[str, Path]) -> None:
    """dry-run 不落库：之后实跑同一目录不触发 already-* 跳过。"""
    source = _make_tree(env["root"], {HIGH_NAME: b"high"})
    _run_cli("import", source.as_posix(), "--dry-run")
    real = json.loads(_run_cli("import", source.as_posix())[1])
    assert real["archived"] == 1
    assert real["skipped"] == 0


# ------------------------------------------------- 单元：目标位冲突守卫（R2 验收）


def _high_result() -> ParseResult:
    return ParseResult(
        title="Show", season=1, episode=1, segment=Segment.EPISODE,
        fansub=None, level=Confidence.HIGH, confidence=1.0,
    )


def test_import_dst_conflict_does_not_overwrite(tmp_path: Path) -> None:
    """R2 实测缺陷回归：不同文件映射同一库内目标位 → 跳过不覆盖。

    修复前 friDay 版 import 会 hardlink 覆盖 LINETV 版（实测三源同集互踩，
    最后导入者获胜）；库内替换只能走 E4 洗版评分闸门（D21）。
    """

    async def scenario() -> tuple[dict[str, object], dict[str, object], Path]:
        storage = _db(tmp_path)
        await storage.create_all()
        try:
            lib = tmp_path / "library"
            src = tmp_path / "dl"
            src.mkdir()
            first = src / "Show.S01E01.1080p.Baha.WEB-DL.x264.mkv"
            first.write_bytes(b"first-content")
            second = src / "Show.S01E01.1080p.LINETV.WEB-DL.x264.mkv"
            second.write_bytes(b"second-content-different")
            settings = Settings(library_path=lib)
            store, gov = LoopStore(storage), MemoryGovernance(storage)
            outcome = RouteOutcome(_high_result(), ROUTE_ARCHIVE)
            item1 = await _handle_import_outcome(
                first, outcome, settings=settings, store=store, governance=gov, dry_run=False
            )
            item2 = await _handle_import_outcome(
                second, outcome, settings=settings, store=store, governance=gov, dry_run=False
            )
            return item1, item2, lib / "Show" / "Season 01" / "Show - S01E01.1080p.mkv"
        finally:
            await storage.close()

    item1, item2, dst = asyncio.run(scenario())
    assert item1["action"] == "archive"
    assert dst.read_bytes() == b"first-content"
    assert item2["action"] == "skip"
    assert item2["reason"] == "dst-exists-upgrade-gated"
    assert dst.read_bytes() == b"first-content"  # 未被第二个版本覆盖


def test_import_same_content_dst_is_noop_skip(tmp_path: Path) -> None:
    """同内容不同名的重放（已 hardlink 进库）→ noop 跳过，不重复链接。"""

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        storage = _db(tmp_path)
        await storage.create_all()
        try:
            lib = tmp_path / "library"
            # CLI 实跑会先建库根（D9 同盘判定需要 stat 到 library 根），保持一致
            lib.mkdir(parents=True)
            src = tmp_path / "dl"
            src.mkdir()
            first = src / "Show.S01E01.1080p.Baha.WEB-DL.x264.mkv"
            first.write_bytes(b"same-content")
            settings = Settings(library_path=lib)
            store, gov = LoopStore(storage), MemoryGovernance(storage)
            outcome = RouteOutcome(_high_result(), ROUTE_ARCHIVE)
            item1 = await _handle_import_outcome(
                first, outcome, settings=settings, store=store, governance=gov, dry_run=False
            )
            # 同 inode 的另一个名字（重命名/硬链接副本），audit 幂等桶不认识它
            renamed = src / "Show.S01E01.1080p.WEB-DL.renamed.mkv"
            os.link(first, renamed)
            item2 = await _handle_import_outcome(
                renamed, outcome, settings=settings, store=store, governance=gov, dry_run=False
            )
            return item1, item2
        finally:
            await storage.close()

    item1, item2 = asyncio.run(scenario())
    assert item1["action"] == "archive"
    assert item2["action"] == "skip"
    assert item2["reason"] == "dst-exists-same-content"


def test_import_dst_exists_skip_counts_as_skipped_not_failed(env: dict[str, Path]) -> None:
    """目标位已占用（同内容重名 replay）→ skipped 计数，failed 只计 error。

    同 inode 不同名文件不进幂等桶（audit 按源文件名），走到归档才发现
    dst 存在：这是"内容已在库"的正常跳过，不是失败（第 4 轮真实测试发现）。
    """
    source = _make_tree(env["root"], {HIGH_NAME: b"high-content"})
    first = json.loads(_run_cli("import", source.as_posix())[1])
    assert first["archived"] == 1 and first["failed"] == 0

    # 同 inode 换名副本：audit 幂等桶不认识，但归档目标位已有同内容文件
    renamed = source / "Bocchi.the.Rock.S01E01.1080p.renamed.mkv"
    os.link(source / HIGH_NAME, renamed)
    second = json.loads(_run_cli("import", source.as_posix())[1])
    assert second["failed"] == 0
    assert second["skipped"] == 2  # already-archived + dst-exists-same-content
    reasons = {Path(str(i["file"])).name: i["reason"] for i in second["items"]}
    assert reasons["Bocchi.the.Rock.S01E01.1080p.renamed.mkv"] == "dst-exists-same-content"
