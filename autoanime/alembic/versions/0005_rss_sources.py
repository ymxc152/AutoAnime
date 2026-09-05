"""add rss_sources table

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import cast

from alembic import op
from sqlalchemy import Table

from autoanime.core.models import RssSource

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 同 0002-0004 的 metadata 驱动策略：全新链路升级到本版时表已随当前
    # metadata 建出，checkfirst 使从旧库的就地升级也能补建，最终 schema
    # 一致。外键 season_id 与 season 建表索引由建表生成。
    cast(Table, RssSource.__table__).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    cast(Table, RssSource.__table__).drop(bind, checkfirst=True)
