"""audit_log 加 created_at（Logs 页时间显示）

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13

audit_log 此前没有任何时间列，Logs 页无法显示「几点发生了什么」。本迁移
给 audit_log 补可空 ``created_at``（DateTime），**不加 server_default**：
新行由 ORM ``default=datetime.now`` 写入时自动带时间，迁移前的历史行保持
NULL（响应 schema 按可空回传）。

与 0006 相同的策略：本仓库 0001 是 metadata 驱动建表（全新链路升级到
head 时该列已随当前 models 建出），加列前先以 inspector 探测列是否存在，
重复执行不报错。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if "created_at" not in _existing_columns("audit_log"):
        op.add_column("audit_log", sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if "created_at" in _existing_columns("audit_log"):
        op.drop_column("audit_log", "created_at")
