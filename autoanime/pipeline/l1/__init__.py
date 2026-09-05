"""L1 local recognition: shared contract and infrastructure (PR3).

The L1 pipeline is fixed -- dialect recognizers are composed directly, never
registered in the external-capability registry. This package exposes the
infrastructure every dialect module builds on:

- normalize: extension stripping, whitespace folding, noise removal
- anchors:   structural landmarks (season/episode/resolution/source/bracket)
- fields:    season/episode/fansub/segment extraction from anchors
- confidence: level mapping, missing-field rules, final clamping
- draft:     L1Draft and ParseResult conversion
- context:   filename/folder merge rules and ParseContext downgrades
- anitopy_adapter: typed wrapper around the anitopy parser
"""

from autoanime.pipeline.l1.anchors import (
    AnchorKind,
    AnchorSpan,
    anchor_free_chunks,
    find_anchors,
    find_anchors_of_kind,
)
from autoanime.pipeline.l1.anitopy_adapter import parse_with_anitopy
from autoanime.pipeline.l1.confidence import (
    base_level,
    confidence_for,
    downgrade,
    merge_levels,
    missing_fields_for,
)
from autoanime.pipeline.l1.context import (
    SOURCE_CONTEXT,
    SOURCE_FOLDER,
    SOURCE_NAME,
    SOURCE_NONE,
    apply_release_progress,
    choose_prefer_name,
    merge_folder_draft,
)
from autoanime.pipeline.l1.draft import L1Draft
from autoanime.pipeline.l1.fields import (
    detect_segment,
    extract_episode,
    extract_episode_numbers,
    extract_fansub,
    extract_season,
    is_likely_fansub,
)
from autoanime.pipeline.l1.normalize import (
    VIDEO_EXTENSIONS,
    clean_noise,
    normalize_name,
    normalize_whitespace,
    separators_to_spaces,
    strip_extension,
)

__all__ = [
    "SOURCE_CONTEXT",
    "SOURCE_FOLDER",
    "SOURCE_NAME",
    "SOURCE_NONE",
    "AnchorKind",
    "AnchorSpan",
    "L1Draft",
    "VIDEO_EXTENSIONS",
    "anchor_free_chunks",
    "apply_release_progress",
    "base_level",
    "choose_prefer_name",
    "clean_noise",
    "confidence_for",
    "detect_segment",
    "downgrade",
    "extract_episode",
    "extract_episode_numbers",
    "extract_fansub",
    "extract_season",
    "find_anchors",
    "find_anchors_of_kind",
    "is_likely_fansub",
    "merge_folder_draft",
    "merge_levels",
    "missing_fields_for",
    "normalize_name",
    "normalize_whitespace",
    "parse_with_anitopy",
    "separators_to_spaces",
    "strip_extension",
]
