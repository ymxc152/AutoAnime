import pytest
from autoanime.naming import Auxiliary_UniformOTSTR


def test_uniform_otstr_preserves_exclamation_mark():
    """感叹号应作为剧名有效字符保留，避免 Bangumi 搜索失败。"""
    result = Auxiliary_UniformOTSTR('Ganbare! Nakamura-kun!! - 01.mkv')
    assert 'Ganbare!' in result
    assert 'Nakamura-kun' in result
    # 不应出现 review_report 中记录的过度清洗形态
    assert 'Ganbare=-Nakamura-kun' not in result
