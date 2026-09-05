"""E1 batch_organizer 纯函数的单元测试（ARCHITECTURE 9.3b 合批契约）。

决策契约（事前定死）：

- 「同目录+同字幕组」是唯一的成批键：``folder`` 与 ``fansub`` 都非空且
  相等的项才可能同批；任一为 ``None`` 的项永不参与合批（保持单文件
  快路径——订阅场景每项 folder 为 None，天然走快路径）；
- 组内自然堆积 ``>= min_batch_size`` 才打包；单批上限 ``max_batch_size``，
  超出按 ``max`` 贪心切块，余数块 ``>= min`` 自成一批、``< min`` 退回
  singles（机会主义等待后续堆积）；
- 纯函数：同样输入永远得到同样输出，无 I/O、无模块级状态；
- 输入顺序在批内与 singles 内完全保持；组之间按首次出现顺序。
"""

from __future__ import annotations

from collections.abc import Iterable

from autoanime.pipeline.batch import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MIN_BATCH_SIZE,
    BatchItem,
    batch_key,
    organize_batches,
)


def _items(specs: Iterable[tuple[str, str | None, str | None]]) -> list[BatchItem]:
    return [BatchItem(name=name, folder=folder, fansub=fansub) for name, folder, fansub in specs]


def test_defaults_match_config_contract() -> None:
    # 9.3b 默认阈值：>=5 打包，上限 20（config 默认值与之对齐）。
    assert DEFAULT_MIN_BATCH_SIZE == 5
    assert DEFAULT_MAX_BATCH_SIZE == 20


def test_batch_key_requires_folder_and_fansub() -> None:
    # folder/fansub 任一缺失即无合批资格：键为 None。
    assert batch_key("dir", "Sub") == ("dir", "Sub")
    assert batch_key(None, "Sub") is None
    assert batch_key("dir", None) is None
    assert batch_key(None, None) is None


def test_small_queue_stays_single() -> None:
    # 自然堆积 <5：不打包，全部退回 singles（单文件快路径）。
    items = _items(
        [
            ("a - 01", "dir", "Sub"),
            ("a - 02", "dir", "Sub"),
            ("a - 03", "dir", "Sub"),
        ]
    )
    plan = organize_batches(items)
    assert plan.groups == ()
    assert plan.singles == tuple(items)


def test_queue_at_threshold_is_packed() -> None:
    # 自然堆积恰好 5：整批打包，顺序保持。
    items = _items((f"a - {i:02}", "dir", "Sub") for i in range(1, 6))
    plan = organize_batches(items)
    assert len(plan.groups) == 1
    assert plan.groups[0].items == tuple(items)
    assert plan.singles == ()


def test_items_without_folder_or_fansub_never_batch() -> None:
    # 缺 folder（订阅形态）或缺 fansub 的项即使堆积 10 个也不凑批。
    folder_less = _items((f"s - {i:02}", None, "Sub") for i in range(10))
    fansub_less = _items((f"t - {i:02}", "dir", None) for i in range(10))
    plan = organize_batches(folder_less + fansub_less)
    assert plan.groups == ()
    assert plan.singles == tuple(folder_less + fansub_less)


def test_different_fansub_or_folder_split_groups() -> None:
    # 同目录不同字幕组、同字幕组不同目录：各自独立计数，互不凑批。
    items = _items(
        [
            ("x - 01", "dir1", "SubA"),
            ("x - 02", "dir1", "SubB"),
            ("x - 03", "dir2", "SubA"),
        ]
    )
    plan = organize_batches(items)
    assert plan.groups == ()
    assert plan.singles == tuple(items)


def test_two_groups_reach_threshold_independently() -> None:
    # 两个键各自堆积 5 个：各自成批，组间按首次出现顺序。
    group_a = _items((f"a - {i:02}", "dir1", "SubA") for i in range(5))
    group_b = _items((f"b - {i:02}", "dir2", "SubB") for i in range(5))
    plan = organize_batches(group_b + group_a)
    assert [g.key for g in plan.groups] == [("dir2", "SubB"), ("dir1", "SubA")]
    assert plan.groups[0].items == tuple(group_b)
    assert plan.groups[1].items == tuple(group_a)
    assert plan.singles == ()


def test_batch_cap_splits_and_small_remainder_returns_to_singles() -> None:
    # 23 个同组：贪心切 20 一批，余 3 < min 退回 singles（等待后续堆积）。
    items = _items((f"a - {i:02}", "dir", "Sub") for i in range(23))
    plan = organize_batches(items)
    assert len(plan.groups) == 1
    assert len(plan.groups[0].items) == 20
    assert plan.groups[0].items == tuple(items[:20])
    assert plan.singles == tuple(items[20:])


def test_batch_cap_remainder_at_threshold_forms_own_batch() -> None:
    # 45 个同组：20 + 20 + 5（余 5 恰达阈值，自成一批）。
    items = _items((f"a - {i:02}", "dir", "Sub") for i in range(45))
    plan = organize_batches(items, max_batch_size=20)
    assert [len(g.items) for g in plan.groups] == [20, 20, 5]
    assert plan.singles == ()
    packed = [item for g in plan.groups for item in g.items]
    assert packed == items


def test_custom_thresholds() -> None:
    # 阈值可配（config 增量字段接入路径）：min=2/max=3 下 5 个切 3+2。
    items = _items((f"a - {i:02}", "dir", "Sub") for i in range(5))
    plan = organize_batches(items, min_batch_size=2, max_batch_size=3)
    assert [len(g.items) for g in plan.groups] == [3, 2]
    assert plan.singles == ()


def test_pure_function_same_input_same_output() -> None:
    items = _items((f"a - {i:02}", "dir", "Sub") for i in range(7))
    first = organize_batches(items)
    second = organize_batches(items)
    assert first == second


def test_empty_queue() -> None:
    plan = organize_batches([])
    assert plan.groups == ()
    assert plan.singles == ()
