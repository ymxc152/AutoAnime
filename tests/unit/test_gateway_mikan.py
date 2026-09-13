"""gateway.mikan 单测：搜索/字幕组解析与失败语义（全部离线）。

- fixture 按 Mikan 真实页面结构裁剪(搜索项 an-text / 字幕组块 subgroup-text);
- 解析宽松性:非法条目跳过、rss_url 去重、HTML 实体解码、相对链接补全;
- 失败语义:无命中 → MikanLookupMiss;非 200/网络错误 → MikanLookupError。
"""

from __future__ import annotations

import httpx
import pytest

from autoanime.gateway.mikan import (
    MikanLookupError,
    MikanLookupMiss,
    fetch_subtitle_groups,
    parse_groups,
    parse_search,
    resolve_subtitle_groups,
    search_bangumi,
)

SEARCH_PAGE = """
<ul class="list-inline an-ul">
  <li><a href="/Home/Bangumi/3141" target="_blank">
    <span data-src="/images/Bangumi/202309/5ce9fed1.jpg" class="b-lazy"></span>
    <div class="an-info"><div class="an-info-group">
      <div class="an-text" title="&#x846C;&#x9001;&#x7684;&#x8299;&#x8389;&#x83B2;">&#x846C;&#x9001;&#x7684;&#x8299;&#x8389;&#x83B2;</div>
    </div></div>
  </a></li>
  <li><a href="/Home/Bangumi/999" target="_blank">
    <div class="an-info"><div class="an-info-group">
      <div class="an-text">第二条</div>
    </div></div>
  </a></li>
  <li><a href="/Home/Bangumi/888" target="_blank"></a></li>
</ul>
"""

GROUPS_PAGE = """
<div class="subgroup-text" id="1254">
  <a href="/Home/PublishGroup/1025" style="color:#3bc0c3;"> 7&#xB3;ACG </a>
  <a href="/RSS/Bangumi?bangumiId=3141&amp;subgroupid=1254" class="mikan-rss"><i class="fa fa-rss-square"></i></a>
</div>
<div class="subgroup-text" id="583">
  <a href="/Home/PublishGroup/583">LoliHouse</a>
  <a href="/RSS/Bangumi?bangumiId=3141&subgroupid=583" class="mikan-rss"></a>
</div>
<div class="subgroup-text" id="583b">
  <a href="/Home/PublishGroup/583">LoliHouse 重复链接应去重</a>
  <a href="/RSS/Bangumi?bangumiId=3141&subgroupid=583" class="mikan-rss"></a>
</div>
<div class="subgroup-text" id="000">
  <a href="/Home/PublishGroup/0">无 RSS 链接应跳过</a>
</div>
"""


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_parse_search_unescapes_and_dedupes() -> None:
    hits = parse_search(SEARCH_PAGE)
    assert [h.bangumi_id for h in hits] == [3141, 999]
    assert hits[0].title == "葬送的芙莉莲"
    assert hits[1].title == "第二条"


async def test_parse_groups_names_links_and_dedupe() -> None:
    options = parse_groups(GROUPS_PAGE)
    assert len(options) == 2
    assert options[0].group_id == "1254"
    # HTML 实体解码 + 首尾空白剥离
    assert options[0].group_name == "7³ACG"
    assert options[0].rss_url == "https://mikanani.me/RSS/Bangumi?bangumiId=3141&subgroupid=1254"
    assert options[1].group_name == "LoliHouse"


async def test_search_bangumi_quotes_title() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text=SEARCH_PAGE)

    hits = await search_bangumi(_client(handler), "葬送的芙莉莲")
    assert hits[0].bangumi_id == 3141
    assert "searchstr=" in seen["url"]
    # 中文标题被百分号编码,不再出现裸中文
    assert "葬送的芙莉莲" not in seen["url"]


async def test_fetch_subtitle_groups_hits_bangumi_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/Home/Bangumi/3141"
        return httpx.Response(200, text=GROUPS_PAGE)

    options = await fetch_subtitle_groups(_client(handler), 3141)
    assert [o.group_id for o in options] == ["1254", "583"]


async def test_resolve_end_to_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Home/Search":
            return httpx.Response(200, text=SEARCH_PAGE)
        return httpx.Response(200, text=GROUPS_PAGE)

    hit, options = await resolve_subtitle_groups(_client(handler), "芙莉莲")
    assert hit.bangumi_id == 3141
    assert len(options) == 2


async def test_resolve_no_hits_raises_miss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>无结果</body></html>")

    with pytest.raises(MikanLookupMiss):
        await resolve_subtitle_groups(_client(handler), "不存在的番剧")


async def test_upstream_500_raises_lookup_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(MikanLookupError) as ei:
        await resolve_subtitle_groups(_client(handler), "芙莉莲")
    assert "http 500" in str(ei.value)


async def test_network_error_raises_lookup_error_without_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(MikanLookupError) as ei:
        await search_bangumi(_client(handler), "x")
    assert "ConnectError" in str(ei.value)
    # 失败文本不带完整 URL(密钥纪律:不泄露查询串)
    assert "https://" not in str(ei.value)
