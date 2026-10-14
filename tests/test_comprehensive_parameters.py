"""Raw filter transport through the supported MCP contract, without live requests.

These cases check transport, not Finviz's interpretation of tokens. Exchange,
industry and similar filters belong to custom_screener, not preset kwargs.
"""
from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.finviz_client.base import FinvizClient
from src.models import StockData
from src.server import server


RAW_FILTER_GROUPS = [
    ("exchange", "exch", "amex nasd nyse"),
    ("index", "idx", "sp500 ndx dji rut"),
    ("sector", "sec", "basicmaterials communicationservices consumercyclical consumerdefensive energy financial healthcare industrials realestate technology utilities"),
    ("industry", "ind", "biotechnology semiconductors softwareapplication softwareinfrastructure oilgasep banksdiversified banksregional pharmaceuticalretailers airlines utilitiesregulatedelectric gold silver solar gambling restaurants"),
    ("country", "geo", "usa notusa asia europe latinamerica bric china japan germany unitedkingdom canada australia india brazil singapore hongkong"),
    ("market_cap", "cap", "mega large mid small micro nano largeover midover smallover microover largeunder midunder smallunder microunder 300to2000"),
    ("price", "sh_price", "u1 u2 u5 u10 u20 u50 o1 o5 o10 o20 o50 o100 1to5 5to10 10to20 50to100"),
    ("dividend", "fa_div", "none pos high veryhigh o1 o3 o5 o8 2to5"),
    ("volume", "sh_avgvol", "u50 u100 u500 o100 o500 o1000 100to500"),
    ("relative_volume", "sh_relvol", "o1 o1.5 o2 o3 o5 o10"),
    ("analyst", "an_recom", "strongbuy buy hold sell strongsell"),
    ("earnings", "earningsdate", "todaybefore todayafter tomorrowbefore tomorrowafter thisweek nextweek yesterdayafter|todaybefore"),
    ("ipo", "ipodate", "today yesterday prevweek prevmonth prevquarter prevyear more1 more5"),
    ("rsi", "ta_rsi", "os30 os40 ob60 ob70 30to70"),
    ("sma20", "ta_sma20", "pa pb"),
    ("sma50", "ta_sma50", "pa pb"),
    ("sma200", "ta_sma200", "pa pb"),
    ("performance", "ta_perf", "1wup 1wdown 4w5o 4w5u 52w20o ytd10o"),
    ("beta", "ta_beta", "u0 u0.5 o1 o1.5 o2 0.5to1.5"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [
    f"{prefix}_{value}" for _, prefix, values in RAW_FILTER_GROUPS for value in values.split()
])
async def test_raw_parameter_reaches_provider_and_formats_stock(token):
    stock = StockData("AAPL", "Apple Inc.", "Technology", "Consumer Electronics", price=150.0)
    with patch.object(FinvizClient, "screen_stocks_raw", return_value=[stock]) as provider:
        content = await server.call_tool("custom_screener", {"filters": token})
    provider.assert_called_once_with(filters=token, signal=None, order=None, max_results=25)
    text = content[0].text
    assert "Custom Screener Results (1 stocks)" in text
    assert f"Filters: {token}" in text
    assert "AAPL | Apple Inc." in text
    assert "Price: $150.00" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [
    "cap_large,sec_technology,fa_pe_u20,sh_price_10to100",
    "cap_small,geo_usa,ind_biotechnology,sh_relvol_o2",
    "fa_div_o3,fa_roe_o15,ta_sma200_pa,earningsdate_thisweek",
])
async def test_multi_category_filters_preserve_all_tokens(filters):
    with patch.object(FinvizClient, "screen_stocks_raw", return_value=[]) as provider:
        content = await server.call_tool("custom_screener", {"filters": filters})
    provider.assert_called_once_with(filters=filters, signal=None, order=None, max_results=25)
    assert content[0].text == f"No stocks found matching filters: {filters}"


@pytest.mark.asyncio
async def test_raw_filter_whitespace_is_normalized_before_request():
    with patch.object(FinvizClient, "screen_stocks_raw", return_value=[]) as provider:
        content = await server.call_tool("custom_screener", {
            "filters": " cap_large , , fa_pe_u20 ", "order": "-marketcap", "max_results": 7,
        })
    provider.assert_called_once_with(filters="cap_large,fa_pe_u20", signal=None, order="-marketcap", max_results=7)
    assert "cap_large,fa_pe_u20" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", ["", " , ", "cap_large&foo=bar", "sec_Technology", "cap_large/../../", ",".join(["cap_large"] * 31)])
async def test_malformed_raw_filters_never_reach_provider(filters):
    with patch.object(FinvizClient, "screen_stocks_raw") as provider:
        with pytest.raises(ToolError, match="INVALID_ARGUMENT: Filter validation error"):
            await server.call_tool("custom_screener", {"filters": filters})
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_presets_do_not_advertise_raw_filter_aliases_or_modal_ui_values():
    tools = {tool.name: tool for tool in await server.list_tools()}
    earnings = tools["earnings_screener"].inputSchema["properties"]
    assert {"earnings_date", "market_cap", "min_price", "max_price", "min_volume", "sectors"} <= earnings.keys()
    assert {"exchange", "index", "industry", "country", "price_filter", "modal"}.isdisjoint(earnings)
    assert "filters" in tools["custom_screener"].inputSchema["properties"]
