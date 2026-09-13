"""series 加 include/exclude_keywords（通用 RSS 订阅规则）

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-13

通用 RSS 源（如 M-Team API RSS）一个 feed 混多部番，识别对齐之外还需要
订阅级的关键词规则进一步筛选。两列均可空 TEXT，分号分隔关键词；与 0006/
0008 相同策略：metadata 建表时列已随当前 models 建出，加列前 inspector
探测防重复执行。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    cols = _existing_columns("series")
    if "include_keywords" not in cols:
        op.add_column("series", sa.Column("include_keywords", sa.Text(), nullable=True))
    if "exclude_keywords" not in cols:
        op.add_column("series", sa.Column("exclude_keywords", sa.Text(), nullable=True))


def downgrade() -> None:
    cols = _existing_columns("series")
    if "exclude_keywords" in cols:
        op.drop_column("series", "exclude_keywords")
    if "include_keywords" in cols:
        op.drop_column("series", "include_keywords")
