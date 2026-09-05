"""E1 批量 L3 prompt/响应解析纯函数的单元测试（9.3b 数组输出 + 逐项校验）。

契约（事前定死）：

- 批量 prompt：每个 release name 一行 ``Release name {i}: {name}``（与
  单文件 prompt 的 ``Release name: {name}`` 同构，fake/测试可机械提取）；
  schema 数组化，每项多一个 ``index`` 对齐字段；
- ``parse_batch_response`` 永不抛、永不返回 ``None``：整体非法（非
  JSON/非数组）→ 全 ``None``；项级违规（index 越界/缺失/类型错、字段
  schema 违规）→ 该项 ``None``，其余照常；同一 ``index`` 出现多次 →
  该 ``index`` 的全部项都置 ``None``（无法归属的输出宁可丢弃，走单文件
  重试兜底）——「失败项单独重试不连坐」的解析侧语义；
- 单文件 ``parse_llm_response`` 的白名单保持严格：``index`` 不是单文件
  响应的合法字段。
"""

from __future__ import annotations

import json

import pytest

from autoanime.core.enums import Segment
from autoanime.core.interfaces import ParseContext
from autoanime.pipeline.l3.prompt import (
    batch_release_names_from_prompt,
    build_batch_prompt,
    build_prompt,
)
from autoanime.pipeline.l3.schema import (
    L3Draft,
    LlmResponseError,
    parse_batch_response,
    parse_llm_response,
)


def test_batch_prompt_lists_numbered_names_and_array_schema() -> None:
    prompt = build_batch_prompt(["a - 01", "a - 02"], fansub="SubA")

    assert batch_release_names_from_prompt(prompt) == ["a - 01", "a - 02"]
    assert '"index"' in prompt
    assert "JSON array" in prompt
    assert "SubA" in prompt


def test_batch_prompt_includes_parse_context() -> None:
    prompt = build_batch_prompt(
        ["a - 01"], fansub="SubA", context=ParseContext(release_progress=12)
    )

    assert "latest released episode: 12" in prompt


def test_batch_prompt_without_fansub_still_lists_names() -> None:
    prompt = build_batch_prompt(["a - 01"])

    assert batch_release_names_from_prompt(prompt) == ["a - 01"]
    # 无 fansub 上下文时不注入已知字幕组行。
    assert "Known fansub" not in prompt


def test_build_single_prompt_unchanged() -> None:
    # 单文件 prompt 契约不受批量扩展影响。
    prompt = build_prompt("a - 01", None, None)
    assert "Release name: a - 01" in prompt
    assert batch_release_names_from_prompt(prompt) == []


def test_parse_batch_response_all_valid() -> None:
    response = json.dumps(
        [
            {"index": 0, "title": "Show", "season": 1, "episode": 1,
             "segment": "episode", "fansub": "SubA"},
            {"index": 1, "title": "Show", "season": 1, "episode": 2,
             "segment": "episode", "fansub": None},
        ]
    )
    drafts = parse_batch_response(response, 2)
    assert drafts == [
        L3Draft(title="Show", segment=Segment.EPISODE, season=1, episode=1, fansub="SubA"),
        L3Draft(title="Show", segment=Segment.EPISODE, season=1, episode=2, fansub=None),
    ]


def test_parse_batch_response_not_json_yields_all_none() -> None:
    assert parse_batch_response("not json at all", 3) == [None, None, None]


def test_parse_batch_response_not_array_yields_all_none() -> None:
    response = json.dumps({"index": 0, "title": "Show", "segment": "episode"})
    assert parse_batch_response(response, 2) == [None, None]


def test_parse_batch_response_item_schema_violation_is_isolated() -> None:
    # 第 2 项 title 非法：仅该项 None，其余不连坐。
    response = json.dumps(
        [
            {"index": 0, "title": "Show", "segment": "episode"},
            {"index": 1, "title": "", "segment": "episode"},
            {"index": 2, "title": "Show", "segment": "movie"},
        ]
    )
    drafts = parse_batch_response(response, 3)
    assert drafts is not None
    assert drafts[0] is not None
    assert drafts[1] is None
    assert drafts[2] is not None


def test_parse_batch_response_unknown_item_field_is_isolated() -> None:
    response = json.dumps(
        [
            {"index": 0, "title": "A", "segment": "episode", "surprise": 1},
            {"index": 1, "title": "B", "segment": "episode"},
        ]
    )
    drafts = parse_batch_response(response, 2)
    assert drafts is not None
    assert drafts[0] is None
    assert drafts[1] is not None


def test_parse_batch_response_bad_index_items_are_isolated() -> None:
    # index 越界 / 缺失 / 类型错：各自占位 None；合法 index 照常。
    response = json.dumps(
        [
            {"index": 0, "title": "A", "segment": "episode"},
            {"index": 9, "title": "B", "segment": "episode"},  # 越界
            {"title": "C", "segment": "episode"},  # index 缺失，答案丢弃
            {"index": "1", "title": "E", "segment": "episode"},  # 类型错
            {"index": 2, "title": "G", "segment": "episode"},
        ]
    )
    drafts = parse_batch_response(response, 4)
    assert drafts is not None
    assert drafts[0] is not None
    assert drafts[1] is None
    assert drafts[2] is not None
    assert drafts[3] is None


def test_parse_batch_response_duplicate_index_fails_all_copies() -> None:
    # index 0 出现两次：两个位置都置 None（无法归属宁可丢弃），其余照常。
    response = json.dumps(
        [
            {"index": 0, "title": "A", "segment": "episode"},
            {"index": 1, "title": "B", "segment": "episode"},
            {"index": 0, "title": "A2", "segment": "episode"},
        ]
    )
    drafts = parse_batch_response(response, 3)
    assert drafts == [None, drafts[1], None]
    assert drafts[1] is not None


def test_parse_batch_response_missing_items_stay_none() -> None:
    response = json.dumps([{"index": 2, "title": "C", "segment": "episode"}])
    drafts = parse_batch_response(response, 3)
    assert drafts[0] is None
    assert drafts[1] is None
    assert drafts[2] == L3Draft(title="C", segment=Segment.EPISODE)


def test_parse_llm_response_rejects_index_field() -> None:
    # 单文件 schema 白名单保持严格：index 不是单文件响应的合法字段。
    response = json.dumps({"index": 0, "title": "A", "segment": "episode"})
    with pytest.raises(LlmResponseError):
        parse_llm_response(response)
