"""Offline integration tests through the registered MCP tool boundary."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from src.finviz_client.base import FinvizClient
from src.finviz_client.news import FinvizNewsClient
from src.finviz_client.screener import FinvizScreener
from src.finviz_client.sector_analysis import FinvizSectorAnalysisClient
from src.models import NewsData, StockData
from src.server import server


EXPECTED_TOOLS = {
    "earnings_screener", "volume_surge_screener", "get_stock_fundamentals",
    "get_multiple_stocks_fundamentals", "trend_reversion_screener", "uptrend_screener",
    "dividend_growth_screener", "etf_screener", "earnings_premarket_screener",
    "earnings_afterhours_screener", "earnings_trading_screener", "get_stock_news",
    "get_market_news", "get_sector_news", "get_sector_performance",
    "get_industry_performance", "get_country_performance",
    "get_sector_specific_industry_performance", "get_capitalization_performance",
    "get_market_overview", "get_relative_volume_stocks", "technical_analysis_screener",
    "earnings_winners_screener", "upcoming_earnings_screener", "get_sec_filings",
    "get_major_sec_filings", "get_insider_sec_filings", "get_sec_filing_summary",
    "get_edgar_filing_content", "get_multiple_edgar_filing_contents",
    "get_edgar_company_filings", "get_edgar_company_facts", "get_edgar_company_concept",
    "list_available_fields", "get_field_categories", "describe_field", "search_fields",
    "validate_fields", "get_moving_average_position", "custom_screener", "get_options_chain",
}


def structured(result):
    assert isinstance(result, CallToolResult)
    assert result.isError is False
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    data = result.structuredContent
    assert json.loads(result.content[0].text.split("\n", 1)[1]) == data
    assert len(result.model_dump_json(exclude_none=True).encode()) <= 32768
    return data


def legacy_text(result):
    # Legacy screeners explicitly advertise text output; typed tools above have
    # a separate contract and may not silently fall back to this representation.
    assert isinstance(result, list)
    assert result and all(isinstance(block, TextContent) for block in result)
    return "\n".join(block.text for block in result)


@pytest.fixture
def stock():
    return StockData(
        ticker="AAPL", company_name="Apple Inc.", sector="Technology",
        industry="Consumer Electronics", price=150.0, volume=50_000_000,
        market_cap=2_400_000, pe_ratio=25.5, eps=6.0, dividend_yield=0.5,
        price_change=2.5, relative_volume=2.75, rsi=55.0,
    )


@pytest.fixture
def news():
    return NewsData(
        ticker="AAPL", title="Apple Reports Strong Quarterly Results",
        source="Example News", date=datetime(2025, 1, 15, 10, tzinfo=timezone.utc),
        url="https://example.com/news1", category="earnings",
    )


class TestMCPServerIntegration:
    def test_server_initialization(self):
        assert isinstance(server, FastMCP)
        assert server.name == "Finviz MCP Server"

    @pytest.mark.asyncio
    async def test_tool_registration(self):
        tools = await server.list_tools()
        names = [tool.name for tool in tools]
        assert set(names) == EXPECTED_TOOLS
        assert len(names) == len(set(names))

    @pytest.mark.asyncio
    async def test_metadata_describes_read_only_tools_and_typed_contracts(self):
        tools = {tool.name: tool for tool in await server.list_tools()}
        for tool in tools.values():
            assert tool.description.strip()
            assert tool.inputSchema["type"] == "object"
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
        assert tools["earnings_screener"].inputSchema["required"] == ["earnings_date"]
        assert tools["volume_surge_screener"].inputSchema["properties"] == {}
        assert tools["get_stock_news"].inputSchema["required"] == ["tickers"]
        assert "rows" in tools["get_stock_news"].outputSchema["properties"]
        assert "stocks" in tools["get_stock_fundamentals"].outputSchema["properties"]
        assert tools["earnings_screener"].outputSchema is None

    @pytest.mark.asyncio
    async def test_earnings_execution_preserves_arguments_and_stock_values(self, stock):
        with patch.object(FinvizScreener, "earnings_screener", return_value=[stock]) as provider:
            result = await server.call_tool("earnings_screener", {
                "earnings_date": "today_after", "market_cap": "large", "min_price": 10.0,
            })
        provider.assert_called_once_with(
            earnings_date="today_after", market_cap="large", min_price=10.0,
            max_price=None, min_volume=None, sectors=[],
            premarket_price_change=None, afterhours_price_change=None,
        )
        text = legacy_text(result)
        for value in ["1 stocks found", "AAPL", "Apple Inc.", "Technology", "$150.00", "2.50%"]:
            assert value in text


class TestMCPToolInterfaces:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,arguments,tickers", [
        ("get_stock_fundamentals", {"ticker": "AAPL"}, ["AAPL"]),
        ("get_multiple_stocks_fundamentals", {"tickers": ["AAPL", "MSFT"]}, ["AAPL", "MSFT"]),
    ])
    async def test_fundamentals_project_requested_fields_and_mark_missing(self, tool, arguments, tickers):
        rows = [{"ticker": "AAPL", "price": 150.0, "volume": 50_000_000, "pe_ratio": 25.5}]
        fields = ["price", "volume", "market_cap"]
        with patch.object(FinvizClient, "get_multiple_stocks_fundamentals", return_value=rows) as provider:
            data = structured(await server.call_tool(tool, {**arguments, "data_fields": fields}))
        provider.assert_called_once_with(tickers, fields)
        assert data["fields"] == fields
        assert data["stocks"][0] == {
            "ticker": "AAPL", "values": {"price": 150.0, "volume": 50_000_000, "market_cap": None},
            "missing_fields": ["market_cap"], "available_fields": 2, "requested_fields": 3,
        }
        assert data["units"]["price"] == "USD per share"
        assert data["as_of"] is None
        if len(tickers) == 2:
            assert data["stocks"][1] == {
                "ticker": "MSFT", "values": dict.fromkeys(fields), "missing_fields": fields,
                "available_fields": 0, "requested_fields": 3,
            }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,arguments,provider_args,provider_kwargs", [
        ("get_stock_news", {"tickers": ["AAPL", "MSFT"], "days_back": 7, "news_type": "earnings"},
         ("AAPL,MSFT", 7, "earnings"), {}),
        ("get_market_news", {"days_back": 3, "max_items": 10}, (3,), {"max_items": 10000}),
        ("get_sector_news", {"sector": "Technology", "days_back": 5},
         ("Technology", 5), {"max_items": 10000}),
    ])
    async def test_news_preserves_real_records_and_source_urls(self, news, tool, arguments, provider_args, provider_kwargs):
        with patch.object(FinvizNewsClient, tool, return_value=[news]) as provider:
            data = structured(await server.call_tool(tool, arguments))
        provider.assert_called_once_with(*provider_args, **provider_kwargs)
        assert data["rows"] == [{
            "ticker": "AAPL", "title": news.title, "source": news.source,
            "date": "2025-01-15T10:00:00Z", "url": news.url, "category": "earnings",
        }]
        assert data["page"]["returned"] == 1
        assert data["page"]["has_more"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,arguments,provider_kwargs", [
        ("get_sector_performance", {"sectors": ["Technology"]}, {"sectors": ["Technology"]}),
        ("get_industry_performance", {"industries": ["Semiconductors"]}, {"industries": ["Semiconductors"]}),
        ("get_country_performance", {"countries": ["USA"]}, {"countries": ["USA"]}),
    ])
    async def test_group_performance_preserves_rows_and_filter_arguments(self, tool, arguments, provider_kwargs):
        rows = [{"name": "Technology", "performance_1d": 2.5, "performance_1w": -1.8}]
        with patch.object(FinvizSectorAnalysisClient, tool, return_value=rows) as provider:
            data = structured(await server.call_tool(tool, arguments))
        provider.assert_called_once_with(**provider_kwargs)
        assert data["rows"] == rows
        assert data["page"]["returned"] == 1
        assert data["page"]["next_cursor"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,arguments", [
        ("volume_surge_screener", {}),
        ("trend_reversion_screener", {"market_cap": "large", "rsi_max": 30}),
        ("uptrend_screener", {}),
        ("dividend_growth_screener", {"min_dividend_yield": 2.0}),
        ("etf_screener", {"strategy_type": "long", "asset_class": "equity"}),
    ])
    async def test_screener_interfaces_render_real_stock_models(self, stock, tool, arguments):
        with patch.object(FinvizScreener, tool, return_value=[stock]) as provider:
            text = legacy_text(await server.call_tool(tool, arguments))
        provider.assert_called_once()
        assert "AAPL" in text
        if tool in {"volume_surge_screener", "uptrend_screener"}:
            assert "1 stocks found" in text
        else:
            assert "Apple Inc." in text
            assert "150.00" in text

    @pytest.mark.asyncio
    async def test_upcoming_earnings_converts_stock_models_for_display(self, stock):
        # This provider converts StockData to UpcomingEarningsData internally.
        with patch.object(FinvizScreener, "screen_stocks", return_value=[stock]) as provider:
            text = legacy_text(await server.call_tool("upcoming_earnings_screener", {
                "earnings_period": "next_week",
            }))
        provider.assert_called_once()
        assert provider.call_args.args[0]["earnings_date"] == "nextweek"
        assert "AAPL" in text and "Apple Inc." in text
        assert "Current Price: $150.00" in text

    @pytest.mark.asyncio
    async def test_technical_filters_reach_provider_and_limit_output(self, stock):
        excluded = StockData("MSFT", "Microsoft", "Technology", "Software", price=300.0)
        with patch.object(FinvizScreener, "screen_stocks", return_value=[stock, excluded]) as provider:
            text = legacy_text(await server.call_tool("technical_analysis_screener", {
                "rsi_min": 30, "rsi_max": 70, "price_vs_sma50": "above", "max_results": 1,
            }))
        provider.assert_called_once_with({"rsi_min": 30, "rsi_max": 70, "sma50_above": True})
        assert "AAPL" in text and "55.00" in text
        assert "MSFT" not in text

    @pytest.mark.asyncio
    async def test_relative_volume_sort_limit_and_measured_ratio(self, stock):
        other = StockData("MSFT", "Microsoft", "Technology", "Software", relative_volume=4.5)
        with patch.object(FinvizScreener, "screen_stocks", return_value=[stock, other]) as provider:
            data = structured(await server.call_tool("get_relative_volume_stocks", {
                "min_relative_volume": 2.0, "min_price": 10.0, "max_results": 1,
            }))
        provider.assert_called_once_with({"relative_volume_min": 2.0, "price_min": 10.0, "sectors": []})
        assert data["threshold"] == 2.0
        assert data["stocks"] == [{
            "ticker": "MSFT", "company": "Microsoft", "price": None,
            "price_change": None, "volume": None, "relative_volume": 4.5,
        }]
        assert data["units"]["relative_volume"] == "ratio"


class TestMCPErrorHandling:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,arguments,message", [
        ("non_existent_tool", {}, "Unknown tool"),
        ("earnings_screener", {}, "earnings_date"),
        ("earnings_screener", {"earnings_date": "today_after", "min_price": []}, "min_price"),
        ("get_stock_news", {"tickers": "AAPL", "limit": 101}, "limit"),
        ("get_stock_fundamentals", {"ticker": "AAPL", "data_fields": ["not_a_field"]}, "INVALID_ARGUMENT"),
    ])
    async def test_invalid_calls_fail_before_provider_access(self, tool, arguments, message):
        with patch.object(FinvizClient, "_make_request") as provider:
            with pytest.raises(ToolError, match=message):
                await server.call_tool(tool, arguments)
        provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_tool_call(self):
        with pytest.raises(ToolError, match="Error executing tool earnings_screener"):
            await server.call_tool("earnings_screener", "invalid_params")

    @pytest.mark.asyncio
    async def test_screener_failure_is_not_rendered_as_success(self):
        with patch.object(FinvizScreener, "earnings_screener", side_effect=RuntimeError("Screener error")):
            with pytest.raises(ToolError, match="Screener error"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})

    @pytest.mark.asyncio
    async def test_provider_timeout_has_actionable_sanitized_error(self):
        error = requests.Timeout("https://provider.example/?auth=SECRET")
        with patch.object(FinvizNewsClient, "get_market_news", side_effect=error):
            with pytest.raises(ToolError, match="UPSTREAM_TIMEOUT") as raised:
                await server.call_tool("get_market_news", {})
        assert "SECRET" not in str(raised.value)


class TestMCPDataSerialization:
    @pytest.mark.asyncio
    async def test_unicode_and_urls_survive_structured_and_text_serialization(self):
        rows = [{"title": "Company™ & Co. €, £, ¥, ©", "url": "https://example.com/news?a=1&b=2"}]
        with patch.object(FinvizNewsClient, "get_market_news", return_value=rows):
            data = structured(await server.call_tool("get_market_news", {}))
        assert data["rows"] == rows

    @pytest.mark.asyncio
    async def test_large_dataset_is_complete_across_bounded_pages(self):
        rows = [{"name": f"Group {i:04d}", "performance_1d": i / 100} for i in range(500)]
        collected, seen = [], set()
        cursor = None
        with patch.object(FinvizSectorAnalysisClient, "get_sector_performance", return_value=rows):
            while True:
                data = structured(await server.call_tool("get_sector_performance", {"limit": 37, "cursor": cursor}))
                assert data["page"]["returned"] == len(data["rows"]) <= 37
                collected.extend(data["rows"])
                if not data["page"]["has_more"]:
                    assert data["page"]["next_cursor"] is None
                    break
                cursor = data["page"]["next_cursor"]
                assert cursor and cursor not in seen
                seen.add(cursor)
                assert len(seen) <= 500
        assert collected == rows

    @pytest.mark.asyncio
    async def test_oversized_legacy_screener_result_is_explicit_error(self, stock):
        with patch.object(FinvizScreener, "earnings_screener", return_value=[stock] * 500):
            with pytest.raises(ToolError, match="RESPONSE_TOO_LARGE"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})


class TestMCPConcurrency:
    @pytest.mark.asyncio
    async def test_calls_preserve_each_requested_ticker(self):
        def fundamentals(tickers, fields):
            return [{"ticker": ticker, "price": 100 + ord(ticker[0])} for ticker in tickers]

        tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "META"]
        with patch.object(FinvizClient, "get_multiple_stocks_fundamentals", side_effect=fundamentals):
            results = await asyncio.gather(*(
                server.call_tool("get_stock_fundamentals", {"ticker": ticker, "data_fields": ["price"]})
                for ticker in tickers
            ))
        for ticker, result in zip(tickers, results):
            projection = structured(result)["stocks"]
            assert len(projection) == 1
            assert projection[0]["ticker"] == ticker
            assert projection[0]["values"] == {"price": 100 + ord(ticker[0])}

    @pytest.mark.asyncio
    async def test_mixed_calls_preserve_each_tools_data(self, stock, news):
        sectors = [{"name": "Technology", "performance_1d": 2.5}]
        with patch.object(FinvizScreener, "earnings_screener", return_value=[stock]), \
             patch.object(FinvizNewsClient, "get_market_news", return_value=[news]), \
             patch.object(FinvizSectorAnalysisClient, "get_sector_performance", return_value=sectors):
            earnings, headlines, performance = await asyncio.gather(
                server.call_tool("earnings_screener", {"earnings_date": "today_after"}),
                server.call_tool("get_market_news", {}),
                server.call_tool("get_sector_performance", {}),
            )
        assert "AAPL" in legacy_text(earnings)
        assert structured(headlines)["rows"][0]["url"] == news.url
        assert structured(performance)["rows"] == sectors
