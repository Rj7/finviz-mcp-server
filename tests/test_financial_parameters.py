"""Financial filter transport through the supported custom screener contract."""
from unittest.mock import patch

import pytest

from src.finviz_client.base import FinvizClient
from src.finviz_client.screener import FinvizScreener
from src.models import StockData
from src.server import server


# These literal tokens replace invented preset kwargs such as pe_ratio='value'.
# This suite verifies transport and output, not upstream filter semantics.
FINANCIAL_FILTERS = [
    ("pe", "fa_pe", "u10 u12 u15 o25 o30 o40 10to25 15to30 8to20 20to50"),
    ("peg", "fa_peg", "u0.5 u1 u2 o2 o3 0.5to1 1to2"),
    ("price_book", "fa_pb", "u1 u2 u3 o3 o5 o10 1to3"),
    ("debt_equity", "fa_de", "u0.1 u0.5 u1 o1 o2 0.1to0.5"),
    ("roe", "fa_roe", "pos neg o5 o10 o15 o20 10to25"),
    ("roa", "fa_roa", "pos neg o5 o10 o15 5to15"),
    ("net_margin", "fa_netmargin", "pos neg o5 o10 o20 10to30"),
    ("operating_margin", "fa_opermargin", "pos neg o10 o20"),
    ("gross_margin", "fa_grossmargin", "pos o20 o40 o60"),
    ("insider", "sh_insiderown", "u5 u10 o10 o20 o30 10to30"),
    ("institutional", "sh_instown", "u10 u30 o50 o70 o90 30to70"),
    ("float", "sh_float", "u1 u5 u10 u50 o100 o500 10to50"),
    ("shares", "sh_outstanding", "u10 u50 u100 o100 o500 o1000 100to500"),
    ("short_interest", "sh_short", "u5 u10 o10 o20 o30 10to20"),
    ("options", "sh_opt", "option short optionshort"),
    ("short_available", "sh_shortavailable", "u100 o100 o1000"),
    ("target_price", "targetprice", "above below a10 a20 b10 b20"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [
    f"{prefix}_{value}" for _, prefix, values in FINANCIAL_FILTERS for value in values.split()
])
async def test_financial_filter_is_forwarded_and_financial_values_are_rendered(token):
    stock = StockData(
        "VALUE", "Value Company", "Financial Services", "Banks", price=80.0,
        market_cap=2500.0, pe_ratio=12.5, dividend_yield=3.25,
    )
    with patch.object(FinvizClient, "screen_stocks_raw", return_value=[stock]) as provider:
        content = await server.call_tool("custom_screener", {"filters": token})
    provider.assert_called_once_with(filters=token, signal=None, order=None, max_results=25)
    text = content[0].text
    assert "VALUE | Value Company" in text
    assert "Market Cap: 2.50B" in text
    assert "P/E: 12.5" in text
    assert "Div Yield: 3.25%" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"min_dividend_yield": 1.0, "max_dividend_yield": 5.0},
    {"min_roe": 15.0, "max_debt_equity": 0.5},
    {"min_payout_ratio": 20.0, "max_payout_ratio": 60.0},
    {"max_pb_ratio": 2.0, "max_pe_ratio": 15.0},
    {"min_dividend_growth": 5.0, "eps_growth_5y_positive": False},
])
async def test_supported_dividend_financial_parameters_reach_provider(params):
    stock = StockData("DIV", "Dividend Company", "Utilities", "Electric", price=40.0, dividend_yield=3.0)
    with patch.object(FinvizScreener, "dividend_growth_screener", return_value=[stock]) as provider:
        content = await server.call_tool("dividend_growth_screener", params)
    provider.assert_called_once()
    for key, value in params.items():
        assert provider.call_args.kwargs[key] == value
    assert "DIV" in content[0].text
    assert "Dividend Company" in content[0].text
    assert "3.00%" in content[0].text
