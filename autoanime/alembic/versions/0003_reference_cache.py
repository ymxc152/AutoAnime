"""add reference_cache table

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import cast

from alembic import op
from sqlalchemy import Table

from autoanime.core.models import ReferenceCache

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 同 0002 的 metadata 驱动策略：全新链路升级到本版时表已随当前
    # metadata 建出，checkfirst 使从旧库的就地升级也能补建，最终 schema
    # 一致。(title_shape, provider) 的 unique 约束随建表生成唯一索引。
    cast(Table, ReferenceCache.__table__).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    cast(Table, ReferenceCache.__table__).drop(bind, checkfirst=True)
