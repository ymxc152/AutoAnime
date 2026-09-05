"""release_record 下载任务生命周期（E4 B4）+ episode FLAGGED（B5）

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-06

B4：release_record 加 status/picked_at/finished_at（审核记录 §10.1 B4，
v1 不另建 download_task 表）；存量行统一回填 ``candidate``。
B5 的 ``FLAGGED`` 是 EpisodeState 枚举值：本仓库枚举列全部走
``native_enum=False``（VARCHAR 存储），新增枚举值无需改列，仅更新
models.py 的 can_transition 语义，故本迁移无 episode 列变更。

本仓库 0001 是 metadata 驱动建表（全新链路升级到 head 时表已随当前
models 建出全部列），与 0002-0005 的 ``checkfirst`` 策略一致，加列前
先以 inspector 探测列是否存在，重复执行不报错。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from autoanime.core.enums import ReleaseStatus

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("release_record")
    if "status" not in existing:
        op.add_column(
            "release_record",
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default=ReleaseStatus.CANDIDATE.value,
            ),
        )
        # server_default 只为存量回填兜底，落定后移除，约束语义回归应用层默认。
        op.alter_column("release_record", "status", server_default=None)
    if "picked_at" not in existing:
        op.add_column("release_record", sa.Column("picked_at", sa.DateTime(), nullable=True))
    if "finished_at" not in existing:
        op.add_column("release_record", sa.Column("finished_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    existing = _existing_columns("release_record")
    if "finished_at" in existing:
        op.drop_column("release_record", "finished_at")
    if "picked_at" in existing:
        op.drop_column("release_record", "picked_at")
    if "status" in existing:
        op.drop_column("release_record", "status")
