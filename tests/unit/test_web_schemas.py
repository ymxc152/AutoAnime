"""Web schema 校验单测（E2）：请求模型约束与 token 不回显契约。"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from autoanime.web.schemas import (
    Page,
    PendingCorrectIn,
    RssSourceCreateIn,
    RssSourceOut,
    SubscriptionCreateIn,
)


def test_subscription_create_requires_some_title() -> None:
    with pytest.raises(ValidationError):
        SubscriptionCreateIn()


def test_subscription_create_accepts_romaji_only() -> None:
    body = SubscriptionCreateIn(title_romaji="Senkou no Night Raid")
    assert body.title_romaji == "Senkou no Night Raid"
    assert body.media_type == "tv"
    assert body.season_number == 1


def test_subscription_create_rejects_unknown_media_type() -> None:
    with pytest.raises(ValidationError):
        SubscriptionCreateIn(title_cn="某番", media_type="sitcom")


def test_subscription_create_rejects_non_positive_episode_count() -> None:
    with pytest.raises(ValidationError):
        SubscriptionCreateIn(title_cn="某番", episode_count=0)


def test_pending_correct_requires_non_empty_title() -> None:
    with pytest.raises(ValidationError):
        PendingCorrectIn(title="  ")
    body = PendingCorrectIn(title="Fate/Grand Order")
    assert body.title == "Fate/Grand Order"


def test_rss_source_out_never_carries_token() -> None:
    out = RssSourceOut(
        id=1, url="https://mikanani.me/RSS/MyBangumi?token=x", has_token=True, season_id=3,
        enabled=True, last_polled_at=None,
    )
    dumped = out.model_dump()
    assert "token" not in dumped
    assert dumped["has_token"] is True


def test_rss_source_create_token_is_secret() -> None:
    body = RssSourceCreateIn(url="https://example.invalid/rss", season_id=1)
    assert body.token is None
    body = RssSourceCreateIn(url="https://example.invalid/rss", season_id=1, token=SecretStr("abc"))
    assert body.token is not None
    assert body.token.get_secret_value() == "abc"
    assert "abc" not in body.model_dump_json()


def test_page_envelope_shape() -> None:
    page = Page[int](total=5, limit=2, offset=2, items=[3, 4])
    assert page.model_dump() == {"total": 5, "limit": 2, "offset": 2, "items": [3, 4]}
