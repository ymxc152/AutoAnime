# scripts

This directory mixes two kinds of code:

- `validate_l1_corpus.py` is an active v2 validation tool and is included in
  both Ruff and Pyright.
- Other scripts are legacy v1 operational tools. They are not part of the v2
  architecture baseline and may contain legacy naming such as `Auxiliary_*`.
  Clean them up or move them to `legacy/scripts/` in a dedicated task before
  treating them as active code.
