"""add llm_cache table and lookup indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op

from autoanime.core.models import BypassList, LlmCacheRow, ParseMemory

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 0001 是 metadata 驱动的 create_all：全新链路升级到 0001 时新表与
    # 索引已随当前 metadata 建出；checkfirst 使从旧 0001 库的就地升级
    # 也能补建。两个路径下最终 schema 一致。
    LlmCacheRow.__table__.create(bind, checkfirst=True)
    for index in (*ParseMemory.__table__.indexes, *BypassList.__table__.indexes):
        index.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for index in (*BypassList.__table__.indexes, *ParseMemory.__table__.indexes):
        index.drop(bind, checkfirst=True)
    LlmCacheRow.__table__.drop(bind, checkfirst=True)
