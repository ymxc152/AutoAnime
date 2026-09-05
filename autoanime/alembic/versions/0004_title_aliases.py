"""add title_aliases table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import cast

from alembic import op
from sqlalchemy import Table

from autoanime.core.models import TitleAlias

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 同 0002/0003 的 metadata 驱动策略：全新链路升级到本版时表已随当前
    # metadata 建出，checkfirst 使从旧库的就地升级也能补建，最终 schema
    # 一致。主键 title_shape_norm 由建表生成主键约束。
    cast(Table, TitleAlias.__table__).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    cast(Table, TitleAlias.__table__).drop(bind, checkfirst=True)
