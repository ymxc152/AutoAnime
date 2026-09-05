"""add llm_cache table and lookup indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import cast

from alembic import op
from sqlalchemy import Table

from autoanime.core.models import BypassList, LlmCacheRow, ParseMemory

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_LOOKUP_INDEX_TABLES = (cast(Table, ParseMemory.__table__), cast(Table, BypassList.__table__))


def upgrade() -> None:
    bind = op.get_bind()
    # 0001 是 metadata 驱动的 create_all：全新链路升级到 0001 时新表与
    # 索引已随当前 metadata 建出；checkfirst 使从旧 0001 库的就地升级
    # 也能补建。两个路径下最终 schema 一致。
    cast(Table, LlmCacheRow.__table__).create(bind, checkfirst=True)
    for table in _LOOKUP_INDEX_TABLES:
        for index in table.indexes:
            index.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_LOOKUP_INDEX_TABLES):
        for index in table.indexes:
            index.drop(bind, checkfirst=True)
    cast(Table, LlmCacheRow.__table__).drop(bind, checkfirst=True)
