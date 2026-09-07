"""add app_settings table

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-07

12-D 配置中心：运行期覆盖项持久化（key TEXT PRIMARY KEY / value TEXT /
updated_at）。同 0002-0005 的 metadata 驱动策略：全新链路升级到本版时表
已随 0001 的当前 metadata 建出，checkfirst 使从旧库的就地升级也能补建，
最终 schema 一致。
"""

from __future__ import annotations

from typing import cast

from alembic import op
from sqlalchemy import Table

from autoanime.core.models import AppSetting

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cast(Table, AppSetting.__table__).create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    cast(Table, AppSetting.__table__).drop(bind, checkfirst=True)
