"""AutoAnime web 包（E2 M3 后端）：FastAPI + SSE 全量 API。

入口：``python -m autoanime.api serve``（见 autoanime/api/__init__.py）。
"""

from __future__ import annotations

from autoanime.web.app import build_reference_chain, create_app

__all__ = ["create_app", "build_reference_chain"]
