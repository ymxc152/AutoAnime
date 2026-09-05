from __future__ import annotations

from autoanime.core.enums import EpisodeState, SeasonState


def test_episode_state_transition_table() -> None:
    assert EpisodeState.MISSING.can_transition(EpisodeState.DOWNLOADING)
    assert EpisodeState.DOWNLOADING.can_transition(EpisodeState.DOWNLOADED)
    assert EpisodeState.DOWNLOADED.can_transition(EpisodeState.ORGANIZED)
    assert EpisodeState.ORGANIZED.can_transition(EpisodeState.UPGRADED)
    assert EpisodeState.UPGRADED.can_transition(EpisodeState.ORGANIZED)
    assert not EpisodeState.MISSING.can_transition(EpisodeState.ORGANIZED)
    assert not EpisodeState.IGNORED.can_transition(EpisodeState.MISSING)


def test_season_state_transition_table() -> None:
    assert SeasonState.UPCOMING.can_transition(SeasonState.AIRING)
    assert SeasonState.AIRING.can_transition(SeasonState.ENDED)
    assert SeasonState.ENDED.can_transition(SeasonState.COLLECTED)
    assert not SeasonState.UPCOMING.can_transition(SeasonState.COLLECTED)
    assert not SeasonState.COLLECTED.can_transition(SeasonState.AIRING)
