"""Fixed L1 dialect recognizers (PR3).

Dialect modules are pure functions composed directly by the L1 pipeline;
they are never registered in the external-capability registry. Each module
exports ``parse(raw, context) -> ParseResult | None`` for one release-name
dialect; the aggregate entry points are re-exported here under a uniform
``parse_<dialect>`` name for the L1 aggregator:

- dot:          方言 A -- 点分隔发布名（MWeb 整季包、Baha/friDay 站点源）。
- bracket:      方言 B -- 方括号字幕组发布名。
- pure_bracket: 方言 C -- 纯方括号流（标题/集数都在括号内）。
- cjk:          方言 D -- 中文字幕组发布名（中文标题、第二季/S2 多重季号、
  方括号集数、END 标记、招募噪声、文件夹补齐中文标题）。
- ep:           方言 E -- ANi 风格繁体发布名（``[ANi] 标题 - 集数 [技术段]``、
  ``2！！``/``2nd Season`` 藏在标题内的季号）。
- special:      方言 F -- 边缘发布名（版本噪声/剧场版/双字幕组季包）。
- minimal:      方言 G -- 极简文件名（``01.mkv``，剧名/季号依赖文件夹上下文）。
"""

from autoanime.pipeline.l1.dialects.bracket import parse as parse_bracket
from autoanime.pipeline.l1.dialects.cjk import parse as parse_cjk
from autoanime.pipeline.l1.dialects.dot import parse as parse_dot
from autoanime.pipeline.l1.dialects.ep import parse as parse_ep
from autoanime.pipeline.l1.dialects.minimal import parse_minimal
from autoanime.pipeline.l1.dialects.pure_bracket import parse as parse_pure_bracket
from autoanime.pipeline.l1.dialects.special import parse_special

__all__ = [
    "parse_bracket",
    "parse_cjk",
    "parse_dot",
    "parse_ep",
    "parse_minimal",
    "parse_pure_bracket",
    "parse_special",
]
