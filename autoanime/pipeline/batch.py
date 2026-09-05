"""L3 机会主义合批的批次划分纯函数（E1，ARCHITECTURE 9.3b）。

契约（9.3b 专题结论 G 的事前定死口径）：

- **成批键 = 「同目录+同字幕组」**：``folder`` 与 ``fansub`` 都非空且相等
  的项才可能同批。任一为 ``None`` 的项永不参与合批——订阅场景每项只有
  一个文件且无目录堆积（``folder=None``），天然落在单文件快路径；
  ``fansub=None`` 的项不构成「同字幕组」，同样不凑批。
- **机会主义阈值**：一个键的队列自然堆积 ``>= min_batch_size`` 才打包；
  不足的项退回 singles 保持单文件快路径，等后续自然堆积（机会主义等待）。
- **单批上限**：``max_batch_size``（9.3b「上限 20/次」）。超出按 ``max``
  贪心切块；切块后的余数块 ``>= min`` 自成一批，``< min`` 退回 singles
  （下一轮队列堆积时可能凑上）。
- **逐项校验失败不连坐**在批的执行侧（orchestrator/批量 L3）实现；本模块
  只产出「谁和谁一批」的计划，不做任何校验与 I/O。

纯函数：无 I/O、无模块级可变状态；同样输入永远得到同样输出。排序完全
确定：批内与 singles 内保持输入顺序，组之间按首次出现顺序。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_MAX_BATCH_SIZE",
    "DEFAULT_MIN_BATCH_SIZE",
    "BatchGroup",
    "BatchItem",
    "BatchPlan",
    "batch_key",
    "organize_batches",
]

#: 9.3b 默认阈值：队列自然堆积 >=5 才打包（与 config.batch_min_size 默认对齐）。
DEFAULT_MIN_BATCH_SIZE = 5
#: 9.3b 默认单批上限 20（与 config.batch_max_size 默认对齐）。
DEFAULT_MAX_BATCH_SIZE = 20

BatchKey = tuple[str, str] | None


@dataclass(frozen=True)
class BatchItem:
    """一个待处理项的合批决策输入（纯数据，不含解析结果）。

    ``folder`` 是目录上下文（库存导入的目录名 / RawName.folder），
    ``fansub`` 是该项目的字幕组（由调用方从 L1/L2 draft 提取后传入）。
    """

    name: str
    folder: str | None = None
    fansub: str | None = None


@dataclass(frozen=True)
class BatchGroup:
    """一个成批的组：同一「同目录+同字幕组」键下的一个打包批次。"""

    key: BatchKey
    items: tuple[BatchItem, ...]


@dataclass(frozen=True)
class BatchPlan:
    """一次划分的结果：成批组 + 保持单文件快路径的项。"""

    groups: tuple[BatchGroup, ...]
    singles: tuple[BatchItem, ...]


def batch_key(folder: str | None, fansub: str | None) -> BatchKey:
    """成批键：folder 与 fansub 都非空才构成「同目录+同字幕组」资格。

    任一为 ``None``（或空串）返回 ``None``——无合批资格的项直接进 singles。
    """
    if not folder or not fansub:
        return None
    return (folder, fansub)


def organize_batches(
    items: Sequence[BatchItem],
    *,
    min_batch_size: int = DEFAULT_MIN_BATCH_SIZE,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
) -> BatchPlan:
    """把待处理队列划分成打包批次与单文件项（纯函数）。

    同键项按输入顺序贪心切 ``max_batch_size`` 大小的块；不足阈值的首块
    （即整组堆积不足 ``min_batch_size``）与切块余数不足阈值的尾块都退回
    singles。组之间按键的首次出现顺序输出。
    """
    buckets: dict[BatchKey, list[BatchItem]] = {}
    key_order: list[BatchKey] = []
    singles: list[BatchItem] = []
    for item in items:
        key = batch_key(item.folder, item.fansub)
        if key is None:
            singles.append(item)
            continue
        if key not in buckets:
            buckets[key] = []
            key_order.append(key)
        buckets[key].append(item)

    groups: list[BatchGroup] = []
    for key in key_order:
        bucket = buckets[key]
        for start in range(0, len(bucket), max_batch_size):
            chunk = tuple(bucket[start : start + max_batch_size])
            if len(chunk) >= min_batch_size:
                groups.append(BatchGroup(key=key, items=chunk))
            else:
                singles.extend(chunk)
    return BatchPlan(groups=tuple(groups), singles=tuple(singles))
