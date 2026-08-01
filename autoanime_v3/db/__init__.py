"""SQLite persistence for the AutoAnime Web console."""

from .migrations import connect_database, run_migrations

__all__ = ["connect_database", "run_migrations"]

