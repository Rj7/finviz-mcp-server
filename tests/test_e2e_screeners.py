"""Offline MCP boundary tests using provider-shaped fixtures and real handlers."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
import requests
from mcp.server.fastmcp.exceptions import ToolError

from src.finviz_client.base import FinvizClient
from src.finviz_client.news import FinvizNewsClient
from src.finviz_client.screener import FinvizScreener
from src.finviz_client.sector_analysis import FinvizSectorAnalysisClient
from src.models import StockData, UpcomingEarningsData
from src.server import server


@pytest.fixture
def boundary_stock():
    return StockData("AAPL", "Apple Inc.", "Technology", "Consumer Electronics",
                     price=150.0, volume=1000000, relative_volume=2.5, rsi=55.0,
                     dividend_yield=3.0, price_change=2.5, market_cap=2500.0)


PRESETS = [
    ("earnings_screener", {"earnings_date": "today_after"}),
    ("volume_surge_screener", {}),
    ("trend_reversion_screener", {"rsi_max": 30}),
    ("uptrend_screener", {}),
    ("dividend_growth_screener", {"min_dividend_yield": 2.0}),
    ("etf_screener", {"strategy_type": "long", "asset_class": "equity"}),
    ("earnings_premarket_screener", {}),
    ("earnings_afterhours_screener", {}),
    ("earnings_trading_screener", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, params", PRESETS)
async def test_preset_formats_provider_stock(boundary_stock, tool, params):
    with patch.object(FinvizScreener, tool, return_value=[boundary_stock]) as provider:
        content = await server.call_tool(tool, params)
    provider.assert_called_once()
    for key, value in params.items():
        assert provider.call_args.kwargs[key] == value
    assert "AAPL" in content[0].text
    if tool in {"volume_surge_screener", "uptrend_screener", "earnings_trading_screener"}:
        assert "1 stocks found" in content[0].text
        assert content[0].text.rstrip().endswith("AAPL")
        provider.assert_called_once_with()
    else:
        assert "Apple Inc." in content[0].text
        assert "150.00" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, params", PRESETS)
async def test_empty_preset_results_are_reported_as_empty(tool, params):
    with patch.object(FinvizScreener, tool, return_value=[]):
        content = await server.call_tool(tool, params)
    assert "No " in content[0].text
    assert "found" in content[0].text
    assert "Error" not in content[0].text


@pytest.mark.asyncio
async def test_fixed_presets_have_no_advertised_custom_parameters():
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name in ("volume_surge_screener", "uptrend_screener", "earnings_premarket_screener", "earnings_afterhours_screener", "earnings_trading_screener"):
        assert tools[name].inputSchema["properties"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, params", [
    ("get_stock_fundamentals", {"ticker": "AAPL"}),
    ("get_multiple_stocks_fundamentals", {"tickers": ["AAPL"]}),
])
async def test_fundamentals_preserve_values_and_missing_fields(tool, params):
    with patch.object(FinvizClient, "get_multiple_stocks_fundamentals", return_value=[{"ticker": "AAPL", "price": 150.0, "pe_ratio": None, "market_cap": 2500.0}]) as provider:
        result = await server.call_tool(tool, params)
    provider.assert_called_once_with(["AAPL"], ["price", "pe_ratio", "market_cap"])
    assert result.isError is False
    assert result.structuredContent["stocks"] == [{
        "ticker": "AAPL", "values": {"price": 150.0, "pe_ratio": None, "market_cap": 2500.0},
        "missing_fields": ["pe_ratio"], "available_fields": 2, "requested_fields": 3,
    }]
    assert result.structuredContent["units"]["market_cap"] == "USD millions"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, params, args, kwargs", [
    ("get_stock_news", {"tickers": ["AAPL", "MSFT"], "days_back": 5, "news_type": "earnings", "limit": 1}, ("AAPL,MSFT", 5, "earnings"), {}),
    ("get_market_news", {"days_back": 3, "max_items": 1}, (3,), {"max_items": 10000}),
    ("get_sector_news", {"sector": "technology", "days_back": 2, "max_items": 1}, ("technology", 2), {"max_items": 10000}),
])
async def test_news_returns_source_urls_and_bounded_pages(tool, params, args, kwargs):
    rows = [{"title": "Earnings report", "url": "https://example.test/news/1", "source": "Fixture", "timestamp": "2026-01-01"},
            {"title": "Second report", "url": "https://example.test/news/2", "source": "Fixture", "timestamp": "2026-01-02"}]
    with patch.object(FinvizNewsClient, tool, return_value=rows) as provider:
        result = await server.call_tool(tool, params)
    provider.assert_called_once_with(*args, **kwargs)
    assert result.structuredContent["rows"] == [rows[0]]
    assert result.structuredContent["page"]["returned"] == 1
    assert result.structuredContent["page"]["has_more"] is True
    assert result.structuredContent["page"]["next_cursor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, parameter, value", [
    ("get_sector_performance", "sectors", "Technology"),
    ("get_industry_performance", "industries", "Software"),
    ("get_country_performance", "countries", "USA"),
])
async def test_performance_rows_forward_name_filters(tool, parameter, value):
    rows = [{"name": value, "performance": 2.5, "volume": 1000000}]
    with patch.object(FinvizSectorAnalysisClient, tool, return_value=rows) as provider:
        result = await server.call_tool(tool, {parameter: [value]})
    provider.assert_called_once_with(**{parameter: [value]})
    assert result.structuredContent["rows"] == rows
    assert result.structuredContent["page"]["has_more"] is False
    assert result.structuredContent["page"]["returned"] == 1


@pytest.mark.asyncio
async def test_market_overview_combines_actual_provider_components(boundary_stock):
    with ExitStack() as stack:
        stack.enter_context(patch.object(FinvizClient, "get_multiple_stocks_fundamentals", return_value=[{"ticker": "SPY", "price": 500.0}]))
        for method, rows in [("volume_surge_screener", [boundary_stock]), ("uptrend_screener", []), ("earnings_screener", [boundary_stock, boundary_stock])]:
            stack.enter_context(patch.object(FinvizScreener, method, return_value=rows))
        result = await server.call_tool("get_market_overview", {})
    data = result.structuredContent
    assert data["etfs"] == [{"ticker": "SPY", "price": 500.0}]
    assert data["statistics"] == {"volume_surge_count": 1, "uptrend_count": 0, "earnings_this_week_count": 2}
    assert data["partial"] is False
    assert data["errors"] == {}


@pytest.mark.asyncio
async def test_relative_volume_uses_actual_screening_boundary(boundary_stock):
    with patch.object(FinvizScreener, "screen_stocks", return_value=[boundary_stock]) as provider:
        result = await server.call_tool("get_relative_volume_stocks", {"min_relative_volume": 2.0, "min_price": 10, "sectors": ["Technology"]})
    provider.assert_called_once_with({"relative_volume_min": 2.0, "price_min": 10.0, "sectors": ["Technology"]})
    assert result.structuredContent["threshold"] == 2.0
    assert result.structuredContent["stocks"][0]["ticker"] == "AAPL"
    assert result.structuredContent["stocks"][0]["relative_volume"] == 2.5


@pytest.mark.asyncio
async def test_technical_analysis_applies_result_limit(boundary_stock):
    other = StockData("MSFT", "Microsoft", "Technology", "Software", price=200.0)
    with patch.object(FinvizScreener, "screen_stocks", return_value=[boundary_stock, other]):
        content = await server.call_tool("technical_analysis_screener", {"max_results": 1})
    assert "Ticker: AAPL" in content[0].text
    assert "Ticker: MSFT" not in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("period, token", [("next_week", "nextweek"), ("next_2_weeks", "nextdays5"), ("next_month", "thismonth")])
async def test_upcoming_earnings_normalizes_period_and_formats_model(period, token):
    stock = UpcomingEarningsData("AAPL", "Apple Inc.", "Technology", "Consumer Electronics", "2026-10-01", "after", current_price=150.0)
    with patch.object(FinvizScreener, "upcoming_earnings_screener", return_value=[stock]) as provider:
        content = await server.call_tool("upcoming_earnings_screener", {"earnings_period": period, "target_sectors": ["Technology"], "min_avg_volume": "o500"})
    assert provider.call_args.kwargs["earnings_date"] == token
    assert provider.call_args.kwargs["target_sectors"] == ["Technology"]
    assert provider.call_args.kwargs["average_volume"] == "o500"
    assert "AAPL - Apple Inc." in content[0].text
    assert "2026-10-01" in content[0].text
    assert "$150.00" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker", ["", "INVALID TICKER", "AAPL!"])
async def test_invalid_ticker_fails_without_provider_request(ticker):
    with patch.object(FinvizClient, "get_multiple_stocks_fundamentals") as provider:
        with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
            await server.call_tool("get_stock_fundamentals", {"ticker": ticker})
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_network_timeout_is_reported_as_tool_failure():
    with patch.object(FinvizClient, "get_multiple_stocks_fundamentals", side_effect=requests.Timeout("timeout")):
        with pytest.raises(ToolError, match="UPSTREAM_TIMEOUT"):
            await server.call_tool("get_stock_fundamentals", {"ticker": "AAPL"})


@pytest.mark.asyncio
async def test_rate_limit_retains_retry_information():
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "30"
    with patch.object(FinvizClient, "get_multiple_stocks_fundamentals", side_effect=requests.HTTPError(response=response)):
        with pytest.raises(ToolError, match="RATE_LIMITED.*retry_after=30"):
            await server.call_tool("get_stock_fundamentals", {"ticker": "AAPL"})
