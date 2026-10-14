"""Offline MCP boundary tests for validation, provider errors and partial data."""

import asyncio
import time
from unittest.mock import patch

import pytest
import pandas as pd
from mcp.server.fastmcp.exceptions import ToolError
from requests.exceptions import ConnectionError, HTTPError, Timeout

from src.finviz_client.base import FinvizClient
from src.finviz_client.screener import FinvizScreener
from src.models import StockData
from src.server import server
from src.utils.validators import validate_ticker


def stock(**values):
    return StockData(**{"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology", "industry": "Consumer Electronics", **values})


def text(result):
    return "\n".join(block.text for block in result)


class TestInputValidation:
    def test_invalid_ticker_formats(self):
        for ticker in ["", " ", "123", "TOOLONGTICKERYMBOL", "IN-VALID", "in valid", "TICKER$", None]:
            assert validate_ticker(ticker) is False
        for ticker in ["A", "aapl", "AAPL"]:
            assert validate_ticker(ticker) is True

    @pytest.mark.asyncio
    async def test_invalid_earnings_dates(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for date in ["", "invalid_date", "yesterday", "next_year", "2024-01-01", None, 123]:
                with pytest.raises(ToolError, match="earnings_date"):
                    await server.call_tool("earnings_screener", {"earnings_date": date})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_market_cap_values(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for market_cap in ["invalid", "tiny", "huge", "LARGE", 123]:
                with pytest.raises(ToolError, match="market_cap"):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after", "market_cap": market_cap})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_price_ranges(self):
        invalid = [{"min_price": -10}, {"max_price": -5}, {"min_price": 100, "max_price": 50}, {"min_price": "invalid"}, {"max_price": "invalid"}]
        with patch.object(FinvizScreener, "earnings_screener", return_value=[]) as provider:
            for values in invalid:
                with pytest.raises(ToolError, match="[Pp]rice"):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after", **values})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_volume_parameters(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for volume in [-1000, "invalid"]:
                with pytest.raises(ToolError, match="min_volume"):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after", "min_volume": volume})
            provider.assert_not_called()
        # Relative volume is configurable on this tool; volume_surge is a fixed preset.
        with patch.object(FinvizScreener, "screen_stocks") as provider:
            for relative_volume in [-1.0, "invalid", 0]:
                with pytest.raises(ToolError, match="min_relative_volume"):
                    await server.call_tool("get_relative_volume_stocks", {"min_relative_volume": relative_volume})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_sector_parameters(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for sectors in [[""], [123], "Technology", ["Invalid Sector"]]:
                with pytest.raises(ToolError, match="sector"):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after", "sectors": sectors})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_data_fields(self):
        with patch.object(FinvizClient, "get_multiple_stocks_fundamentals") as provider:
            for fields in [[], [""], ["invalid_field"], ["pe_ratio", ""], "pe_ratio", [123]]:
                with pytest.raises(ToolError, match="field"):
                    await server.call_tool("get_stock_fundamentals", {"ticker": "AAPL", "data_fields": fields})
            provider.assert_not_called()


class TestNetworkErrorHandling:
    @pytest.mark.asyncio
    async def test_connection_timeout(self):
        with patch.object(FinvizScreener, "earnings_screener", side_effect=Timeout("Connection timeout")) as provider:
            with pytest.raises(ToolError, match="timeout"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            provider.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error(self):
        with patch.object(FinvizScreener, "earnings_screener", side_effect=ConnectionError("Failed to connect")):
            with pytest.raises(ToolError, match="Failed to connect"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})

    @pytest.mark.asyncio
    async def test_http_errors(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for status in [400, 401, 403, 404, 429, 500, 503]:
                provider.side_effect = HTTPError(f"HTTP {status}")
                with pytest.raises(ToolError, match=str(status)):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            assert provider.call_count == 7

    @pytest.mark.asyncio
    async def test_rate_limit_handling(self):
        with patch.object(FinvizScreener, "earnings_screener", side_effect=RuntimeError("Rate limit exceeded")):
            with pytest.raises(ToolError, match="[Rr]ate limit"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})

    @pytest.mark.asyncio
    async def test_malformed_response_handling(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for malformed in [None, "", "Invalid JSON", {"error": "Server error"}, {"incomplete": "data"}, [{"ticker": "AAPL"}]]:
                provider.return_value = malformed
                with pytest.raises(ToolError):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after"})


class TestDataValidation:
    @pytest.mark.asyncio
    async def test_empty_results_handling(self):
        with patch.object(FinvizScreener, "earnings_screener", return_value=[]) as provider:
            result = await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            assert text(result) == "No stocks found matching the criteria."
            provider.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_data_handling(self):
        with patch.object(FinvizScreener, "earnings_screener", return_value=[stock(price=None), stock(ticker="MSFT", company_name=None, price=150)]):
            result = await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            output = text(result)
            assert "2 stocks found" in output
            assert "Ticker: AAPL" in output and "Price: N/A" in output
            assert "Ticker: MSFT" in output and "Price: $150.00" in output

    @pytest.mark.asyncio
    async def test_data_type_conversion(self):
        # Conversion belongs at the CSV provider boundary, before the formatter.
        row = FinvizClient(api_key="test")._parse_stock_data_from_csv(pd.Series({
            "Ticker": "AAPL", "Company": "Apple Inc.", "Sector": "Technology",
            "Industry": "Consumer Electronics", "Price": "150.0",
            "Volume": "1000000", "P/E": "25.5", "Market Cap": "2400000",
        }))
        assert row.price == 150.0 and row.volume == 1000000 and row.pe_ratio == 25.5
        with patch.object(FinvizScreener, "earnings_screener", return_value=[row]):
            result = await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            assert "Price: $150.00" in text(result)


class TestConcurrencyAndPerformance:
    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        with patch.object(FinvizScreener, "earnings_screener", return_value=[]) as provider:
            results = await asyncio.gather(*(server.call_tool("earnings_screener", {"earnings_date": "today_after"}) for _ in range(10)))
            assert all(text(result) == "No stocks found matching the criteria." for result in results)
            assert provider.call_count == 10

    @pytest.mark.asyncio
    async def test_memory_usage_large_datasets(self):
        # The MCP boundary must reject unbounded output instead of sending a huge payload.
        rows = [stock(ticker=f"STOCK{i:04d}", price=100.0+i) for i in range(1000)]
        with patch.object(FinvizScreener, "earnings_screener", return_value=rows):
            with pytest.raises(ToolError, match="RESPONSE_TOO_LARGE"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})

    @pytest.mark.asyncio
    async def test_slow_response_handling(self):
        # The provider is synchronous; returning a coroutine here would be a broken mock.
        def slow_response(**kwargs):
            time.sleep(0.01)
            return [stock(price=150)]
        with patch.object(FinvizScreener, "earnings_screener", side_effect=slow_response) as provider:
            result = await asyncio.wait_for(server.call_tool("earnings_screener", {"earnings_date": "today_after"}), timeout=5)
            assert "Price: $150.00" in text(result)
            provider.assert_called_once()


class TestEdgeCaseScenarios:
    @pytest.mark.asyncio
    async def test_special_character_handling(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for sector in ["Technology & Software", "Finance/Banking", "Energy & Utilities"]:
                with pytest.raises(ToolError, match="Invalid sector"):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after", "sectors": [sector]})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_unicode_handling(self):
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for sector in ["Technology™", "Financé", "Technología"]:
                with pytest.raises(ToolError, match="Invalid sector"):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after", "sectors": [sector]})
            provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_extreme_parameter_values(self):
        cases = [
            ("earnings_screener", {"earnings_date": "today_after", "min_price": 0.0001, "max_price": 999999.99, "min_volume": 1}),
            ("dividend_growth_screener", {"min_dividend_yield": 0.001, "max_dividend_yield": 99.999, "min_dividend_growth": 0.01, "min_roe": 0.01}),
        ]
        for name, arguments in cases:
            with patch.object(FinvizScreener, name, return_value=[]) as provider:
                result = await server.call_tool(name, arguments)
                assert "No " in text(result)
                for key, value in arguments.items():
                    assert provider.call_args.kwargs[key] == value
        with patch.object(FinvizScreener, "screen_stocks", return_value=[]) as provider:
            result = await server.call_tool("get_relative_volume_stocks", {"min_relative_volume": 0.001})
            assert result.structuredContent["stocks"] == []
            assert provider.call_args.args[0]["relative_volume_min"] == 0.001

    @pytest.mark.asyncio
    async def test_null_and_undefined_handling(self):
        cases = [{"market_cap": None}, {"sectors": None}, {"min_price": None}, {"min_price": 0, "min_volume": 0, "sectors": []}, {"market_cap": ""}]
        with patch.object(FinvizScreener, "earnings_screener", return_value=[]) as provider:
            for arguments in cases:
                result = await server.call_tool("earnings_screener", {"earnings_date": "today_after", **arguments})
                assert text(result) == "No stocks found matching the criteria."
            assert provider.call_count == len(cases)


class TestResourceManagement:
    @pytest.mark.asyncio
    async def test_resource_cleanup_on_error(self):
        # A failed request must not poison the next call through the shared client.
        with patch.object(FinvizScreener, "earnings_screener", side_effect=[RuntimeError("Simulated error"), []]) as provider:
            with pytest.raises(ToolError, match="Simulated error"):
                await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            result = await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            assert "No stocks found" in text(result)
            assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_error_scenarios(self):
        errors = [ConnectionError("Network error"), Timeout("Request timeout"), HTTPError("HTTP 500"), ValueError("Invalid data"), KeyError("Missing key")]
        with patch.object(FinvizScreener, "earnings_screener") as provider:
            for error in errors:
                provider.side_effect = error
                with pytest.raises(ToolError, match=str(error).strip("'")):
                    await server.call_tool("earnings_screener", {"earnings_date": "today_after"})
            assert provider.call_count == len(errors)
