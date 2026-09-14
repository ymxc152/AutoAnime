"""rss_sources 聚合源：加 kind/include_keywords/exclude_keywords，season_id 放宽可空

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-13

批次三（聚合 RSS）：一个 feed 混多部番的通用源（如 M-Team API RSS）不绑
季（season_id 为 NULL），轮询时对全部活跃订阅逐个对齐。加三列 + 把
season_id 从 NOT NULL 改为 NULL——SQLite 不能直接 ALTER COLUMN，按官方
alembic 批处理模式 ``batch_alter_table`` 重建表（先 add_column 探测防重复，
再 batch 放宽 NOT NULL）。与 0006/0008/0009 相同策略：metadata 建表时新
列已随当前 models 建出，全量探测防重复执行。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _columns(table: str) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"]: column for column in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _columns("rss_sources")
    if "kind" not in cols:
        op.add_column(
            "rss_sources",
            sa.Column("kind", sa.Text(), nullable=False, server_default="season"),
        )
    if "include_keywords" not in cols:
        op.add_column(
            "rss_sources", sa.Column("include_keywords", sa.Text(), nullable=True)
        )
    if "exclude_keywords" not in cols:
        op.add_column(
            "rss_sources", sa.Column("exclude_keywords", sa.Text(), nullable=True)
        )
    season_id = cols.get("season_id")
    if season_id is not None and not season_id["nullable"]:
        # SQLite 不支持 ALTER COLUMN NOT NULL → 官方批处理模式整表重建。
        with op.batch_alter_table("rss_sources") as batch:
            batch.alter_column(
                "season_id", existing_type=sa.Integer(), nullable=True
            )


def downgrade() -> None:
    cols = _columns("rss_sources")
    # 恢复 NOT NULL 前先清掉聚合源（不绑季的行违反目标约束）。
    op.execute("DELETE FROM rss_sources WHERE kind = 'aggregate' OR season_id IS NULL")
    if cols.get("season_id") is not None and cols["season_id"]["nullable"] is False:
        pass  # 旧库本就 NOT NULL，无需重建
    else:
        with op.batch_alter_table("rss_sources") as batch:
            batch.alter_column(
                "season_id", existing_type=sa.Integer(), nullable=False
            )
    if "exclude_keywords" in cols:
        op.drop_column("rss_sources", "exclude_keywords")
    if "include_keywords" in cols:
        op.drop_column("rss_sources", "include_keywords")
    if "kind" in cols:
        op.drop_column("rss_sources", "kind")
