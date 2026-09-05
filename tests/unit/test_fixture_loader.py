from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.fixtures import FixtureError, load_all, load_case, load_dialects

VALID_ROOT = Path(__file__).parents[1] / "fixtures" / "loader_examples" / "valid"
ERROR_ROOT = Path(__file__).parents[1] / "fixtures" / "loader_examples" / "errors"


def test_load_case_builds_structured_case() -> None:
    case = load_case(VALID_ROOT / "dialect_a" / "A01_happy")

    assert case.id == "A01_happy"
    assert case.dialect == "A"
    assert case.folder == "Anime.Some.Title.S02.1080p.Baha.WEB-DL-MWeb"
    assert case.parent_path == "Z:/Downloads"
    assert [file.name for file in case.files] == [
        "Some.Title.S02E01.1080p.Baha.WEB-DL.mkv"
    ]
    assert case.tags == ("season_pack", "source_baha")
    assert case.notes == "方言 A：MWeb 整季包"

    raw_names = case.to_raw_names()
    assert len(raw_names) == 1
    assert raw_names[0].name == "Some.Title.S02E01.1080p.Baha.WEB-DL.mkv"
    assert raw_names[0].folder == case.folder
    assert raw_names[0].parent_path == case.parent_path


def test_load_dialects_uses_stable_order() -> None:
    cases = load_dialects("A", "b", root=VALID_ROOT)

    assert [(case.dialect, case.id) for case in cases] == [
        ("A", "A01_happy"),
        ("A", "A02_stable"),
        ("B", "B01_single"),
    ]
    assert load_dialects("A", "b", root=VALID_ROOT) == cases


def test_load_all_returns_every_dialect_in_stable_order() -> None:
    cases = load_all(root=VALID_ROOT)

    assert [(case.dialect, case.id) for case in cases] == [
        ("A", "A01_happy"),
        ("A", "A02_stable"),
        ("B", "B01_single"),
    ]


def test_load_case_rejects_missing_context_file() -> None:
    case_dir = ERROR_ROOT / "dialect_a" / "A04_missing_context"

    with pytest.raises(FixtureError, match="missing context.json"):
        load_case(case_dir)


def test_loader_rejects_empty_files() -> None:
    with pytest.raises(FixtureError, match="non-empty list"):
        load_case(ERROR_ROOT / "dialect_b" / "B01_empty_files")


def test_loader_rejects_dialect_mismatch() -> None:
    with pytest.raises(FixtureError, match="dialect mismatch"):
        load_case(ERROR_ROOT / "dialect_a" / "A03_mismatch")
