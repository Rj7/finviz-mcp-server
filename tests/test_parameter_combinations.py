"""Combinations of supported tool parameters and explicit invalid boundaries."""
from itertools import combinations
from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.finviz_client.base import FinvizClient
from src.finviz_client.screener import FinvizScreener
from src.models import StockData
from src.server import server


@pytest.fixture
def combination_stock():
    return StockData("AAPL", "Apple Inc.", "Technology", "Consumer Electronics", price=150.0, rsi=42.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("earnings_date", ["today_after", "today_before", "tomorrow_after", "tomorrow_before", "this_week", "next_week", "within_2_weeks"])
@pytest.mark.parametrize("market_cap", [None, "large", "mid", "small", "mega", "smallover"])
@pytest.mark.parametrize("price_range", [{}, {"min_price": 10.0}, {"max_price": 100.0}, {"min_price": 5.0, "max_price": 200.0}])
async def test_earnings_combinations_forward_filters_and_render_results(combination_stock, earnings_date, market_cap, price_range):
    params = {"earnings_date": earnings_date, "market_cap": market_cap, **price_range}
    with patch.object(FinvizScreener, "earnings_screener", return_value=[combination_stock]) as provider:
        content = await server.call_tool("earnings_screener", params)
    provider.assert_called_once_with(
        earnings_date=earnings_date, market_cap=market_cap,
        min_price=price_range.get("min_price"), max_price=price_range.get("max_price"),
        min_volume=None, sectors=[], premarket_price_change=None, afterhours_price_change=None,
    )
    assert "Ticker: AAPL" in content[0].text
    assert "Price: $150.00" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("sectors", [list(group) for count in (1, 2, 3) for group in combinations(["Technology", "Healthcare", "Financial", "Energy"], count)])
async def test_sector_combinations_and_volume_are_forwarded(sectors):
    with patch.object(FinvizScreener, "earnings_screener", return_value=[]) as provider:
        content = await server.call_tool("earnings_screener", {
            "earnings_date": "today_after", "sectors": sectors, "min_volume": 500000,
        })
    assert provider.call_args.kwargs["sectors"] == sectors
    assert provider.call_args.kwargs["min_volume"] == 500000
    assert content[0].text == "No stocks found matching the criteria."


@pytest.mark.asyncio
@pytest.mark.parametrize("market_cap", ["large", "mid", "small"])
@pytest.mark.parametrize("rsi_max", [25.0, 30.0, 35.0])
async def test_trend_reversion_combines_growth_and_exclusion(combination_stock, market_cap, rsi_max):
    params = dict(market_cap=market_cap, eps_growth_qoq=10.0, revenue_growth_qoq=5.0,
                  rsi_max=rsi_max, sectors=["Technology"], exclude_sectors=["Energy"])
    with patch.object(FinvizScreener, "trend_reversion_screener", return_value=[combination_stock]) as provider:
        content = await server.call_tool("trend_reversion_screener", params)
    provider.assert_called_once_with(**params)
    assert "Ticker: AAPL" in content[0].text
    assert "RSI: 42.00" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("period", [20, 50, 200])
@pytest.mark.parametrize("position", ["above", "below"])
@pytest.mark.parametrize("rsi", [(0, 20), (30, 70), (80, 100)])
async def test_technical_parameters_are_translated_into_provider_filters(combination_stock, period, position, rsi):
    with patch.object(FinvizScreener, "screen_stocks", return_value=[combination_stock]) as provider:
        content = await server.call_tool("technical_analysis_screener", {
            "rsi_min": rsi[0], "rsi_max": rsi[1], f"price_vs_sma{period}": position,
            "min_price": 10, "min_volume": 100000, "sectors": ["Technology"],
        })
    provider.assert_called_once_with({
        "rsi_min": rsi[0], "rsi_max": rsi[1], f"sma{period}_{position}": True,
        "price_min": 10, "volume_min": 100000, "sectors": ["Technology"],
    })
    assert f"RSI: {rsi[0]}-{rsi[1]}" in content[0].text
    assert "Ticker: AAPL" in content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("tickers", [["AAPL"], ["AAPL", "MSFT"], ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"]])
@pytest.mark.parametrize("fields", [["price"], ["pe_ratio", "eps"], ["price", "market_cap", "dividend_yield"]])
async def test_fundamental_field_and_ticker_combinations(tickers, fields):
    rows = [{"ticker": ticker, "price": 150.0, "pe_ratio": 25.5, "eps": 6.0, "market_cap": 2500.0, "dividend_yield": None} for ticker in tickers]
    with patch.object(FinvizClient, "get_multiple_stocks_fundamentals", return_value=rows) as provider:
        result = await server.call_tool("get_multiple_stocks_fundamentals", {"tickers": tickers, "data_fields": fields})
    provider.assert_called_once_with(tickers, fields)
    data = result.structuredContent
    assert data["fields"] == fields
    assert [stock["ticker"] for stock in data["stocks"]] == tickers
    for stock in data["stocks"]:
        assert stock["values"] == {field: {"price": 150.0, "pe_ratio": 25.5, "eps": 6.0, "market_cap": 2500.0, "dividend_yield": None}[field] for field in fields}
        assert stock["missing_fields"] == (["dividend_yield"] if "dividend_yield" in fields else [])
        assert stock["requested_fields"] == len(fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("tickers", [[], ["AAPL"] * 6, ["AAPL"] * 50, ["AAPL", "INVALID TICKER"]])
async def test_oversized_or_invalid_ticker_batches_are_rejected(tickers):
    with patch.object(FinvizClient, "get_multiple_stocks_fundamentals") as provider:
        with pytest.raises(ToolError):
            await server.call_tool("get_multiple_stocks_fundamentals", {"tickers": tickers})
    provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("params, message", [
    ({"min_price": 100, "max_price": 10}, "Invalid price range"),
    ({"min_price": -1}, "Invalid price range"),
    ({"min_volume": -1}, "Invalid min_volume"),
    ({"market_cap": "not_a_cap"}, "Invalid market_cap"),
    ({"sectors": ["not_a_sector"]}, "Invalid sector"),
])
async def test_invalid_earnings_combinations_fail_before_provider(params, message):
    with patch.object(FinvizScreener, "earnings_screener") as provider:
        with pytest.raises(ToolError, match=message):
            await server.call_tool("earnings_screener", {"earnings_date": "today_after", **params})
    provider.assert_not_called()
