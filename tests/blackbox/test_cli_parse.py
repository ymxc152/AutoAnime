"""Blackbox tests for the ``autoanime parse`` subcommand."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2]


def _run_parse(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "autoanime.cli", "parse", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )


def _output_json(result: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_parse_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autoanime.cli", "parse", "--help"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0
    assert "--name" in result.stdout
    assert "--folder" in result.stdout
    assert "--parent" in result.stdout


def test_parse_dot_release_name_outputs_json() -> None:
    payload = _output_json(
        _run_parse("--name", "Some.Title.S02E01.1080p.Baha.WEB-DL.AAC2.0.H.264-MWeb.mkv")
    )
    assert payload is not None
    assert payload["title"] == "Some Title"
    assert payload["season"] == 2
    assert payload["episode"] == 1
    assert payload["segment"] == "episode"
    assert payload["fansub"] == "MWeb"
    assert payload["level"] == "high"
    assert payload["confidence"] == 1.0


def test_parse_minimal_name_uses_folder_context() -> None:
    payload = _output_json(
        _run_parse(
            "--name",
            "01.mkv",
            "--folder",
            "[SweetSub] Honzuki no Gekokujou S04",
            "--parent",
            "Z:/Downloads",
        )
    )
    assert payload is not None
    assert payload["title"] == "Honzuki no Gekokujou"
    assert payload["season"] == 4
    assert payload["episode"] == 1
    assert payload["fansub"] == "SweetSub"
    assert payload["evidence"]["title"] == "folder"
    assert payload["evidence"]["fansub"] == "folder"


def test_parse_unrecognizable_name_outputs_null() -> None:
    payload = _output_json(_run_parse("--name", "random_text_only"))
    assert payload is None


def test_parse_requires_name() -> None:
    result = _run_parse()
    assert result.returncode != 0
