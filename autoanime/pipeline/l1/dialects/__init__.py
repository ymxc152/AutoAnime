"""Fixed L1 dialect recognizers (PR3).

Dialect modules are pure functions composed directly by the L1 pipeline;
they are never registered in the external-capability registry. Each module
exports ``parse(raw, context) -> ParseResult | None`` for one release-name
dialect:

- cjk: 方言 D -- 中文字幕组发布名（中文标题、第二季/S2 多重季号、方括号集数、
  END 标记、招募噪声、文件夹补齐中文标题）。
- ep:  方言 E -- ANi 风格繁体发布名（``[ANi] 标题 - 集数 [技术段]``、
  ``2！！``/``2nd Season`` 藏在标题内的季号）。
"""

from autoanime.pipeline.l1.dialects.cjk import parse as parse_cjk
from autoanime.pipeline.l1.dialects.ep import parse as parse_ep

__all__ = ["parse_cjk", "parse_ep"]
