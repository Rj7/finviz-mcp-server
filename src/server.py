#!/usr/bin/env python3
import asyncio
from .agent_tools import get_stock_news, get_market_news, get_sector_news, get_sector_performance, get_industry_performance, get_country_performance, get_sector_specific_industry_performance, get_market_overview
from .agent_tools import get_relative_volume_stocks
from mcp.server.fastmcp.exceptions import ToolError
from .agent_tools import get_stock_fundamentals, get_multiple_stocks_fundamentals, get_edgar_filing_content, get_multiple_edgar_filing_contents, get_options_chain
import json
import logging
import os
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from .tool_policy import ContractFastMCP
from mcp.types import TextContent

from .utils.validators import validate_ticker, validate_tickers, parse_tickers, validate_market_cap, validate_earnings_date, validate_price_range, validate_sector, validate_volume, validate_screening_params, validate_data_fields, validate_and_normalize_raw_filters, validate_raw_sort_order, validate_signal
from .utils.formatters import format_large_number, format_raw_field_label, format_raw_field_value
from .finviz_client.base import FinvizClient
from .finviz_client.screener import FinvizScreener
from .finviz_client.news import FinvizNewsClient
from .finviz_client.sector_analysis import FinvizSectorAnalysisClient
from .finviz_client.sec_filings import FinvizSECFilingsClient
from .finviz_client.options import FinvizOptionsClient
from .field_discovery.tools import register_field_discovery_tools

# The EDGAR client needs `sec-edgar-api` (which in turn needs pyrate-limiter <4;
# see requirements.txt). Import defensively so a broken/absent install degrades
# the six EDGAR tools instead of taking the whole server down at startup.
try:
    from .finviz_client.edgar_client import EdgarAPIClient
    EDGAR_IMPORT_ERROR = None
except Exception as _edgar_import_error:  # ImportError, or a dep raising at import time
    EdgarAPIClient = None
    EDGAR_IMPORT_ERROR = _edgar_import_error

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

server = ContractFastMCP("Finviz MCP Server")

# Initialize Finviz clients
finviz_api_key = os.getenv('FINVIZ_API_KEY')
finviz_client = FinvizClient(api_key=finviz_api_key)
finviz_screener = FinvizScreener(api_key=finviz_api_key)
finviz_news = FinvizNewsClient(api_key=finviz_api_key)
finviz_sector = FinvizSectorAnalysisClient(api_key=finviz_api_key)
finviz_sec = FinvizSECFilingsClient(api_key=finviz_api_key)
finviz_options = FinvizOptionsClient(api_key=finviz_api_key)

# Fallback used only when EdgarAPIClient could not be imported. Every method
# raises rather than returning a sentinel: the previous stub returned None/[]
# and the tool handlers then reported plausible-but-wrong causes (e.g. "Could
# not find CIK ... verify the ticker symbol" for what was really a missing
# package). Handlers wrap calls in `except Exception` and echo str(e), so
# raising surfaces the real reason at the call site.
class EdgarClientUnavailable:
    def __init__(self, reason: Optional[BaseException] = None):
        detail = f" ({reason})" if reason else ""
        self._message = (
            "EDGAR API client is unavailable: the 'sec-edgar-api' package failed "
            f"to import{detail}. Install it with `pip install -r requirements.txt`."
        )

    def _fail(self, *args, **kwargs):
        raise RuntimeError(self._message)

    get_filing_document_content = _fail
    get_multiple_filing_contents = _fail
    get_company_filings = _fail
    _get_cik_from_ticker = _fail
    get_company_concept = _fail
    # Reached via the `client` property below (edgar_client.client.get_company_facts)
    get_company_facts = _fail
    get_submissions = _fail

    @property
    def client(self):
        return self


# Initialize EDGAR API client
if EdgarAPIClient is not None:
    edgar_client = EdgarAPIClient()
else:
    logger.warning(
        "EDGAR tools disabled: could not import sec-edgar-api (%s)", EDGAR_IMPORT_ERROR
    )
    edgar_client = EdgarClientUnavailable(EDGAR_IMPORT_ERROR)

@server.tool()
def earnings_screener(
    earnings_date: str,
    market_cap: Optional[str] = None,
    min_price: Optional[Union[int, float, str]] = None,
    max_price: Optional[Union[int, float, str]] = None,
    min_volume: Optional[Union[int, float, str]] = None,
    sectors: Optional[List[str]] = None,
    premarket_price_change: Optional[Dict[str, Any]] = None,
    afterhours_price_change: Optional[Dict[str, Any]] = None
) -> List[TextContent]:
    """
    決算発表予定銘柄のスクリーニング
    
    Args:
        earnings_date: 決算発表日の指定 (today_after, tomorrow_before, this_week, within_2_weeks)
        market_cap: 時価総額フィルタ (small, mid, large, mega)
        min_price: 最低株価
        max_price: 最高株価
        min_volume: 最低出来高
        sectors: 対象セクター
        premarket_price_change: 寄り付き前価格変動フィルタ
        afterhours_price_change: 時間外価格変動フィルタ
    """
    try:
        # Validate parameters
        if not validate_earnings_date(earnings_date):
            raise ValueError(f"Invalid earnings_date: {earnings_date}")
        
        if market_cap is not None and not validate_market_cap(market_cap):
            raise ValueError(f"Invalid market_cap: {market_cap}")
        
        if not validate_price_range(min_price, max_price):
            raise ValueError("Invalid price range")
        
        if min_volume is not None and not validate_volume(min_volume):
            raise ValueError(f"Invalid min_volume: {min_volume}")
        
        if sectors:
            for sector in sectors:
                if not validate_sector(sector):
                    raise ValueError(f"Invalid sector: {sector}")
        
        # Prepare parameters
        params = {
            'earnings_date': earnings_date,
            'market_cap': market_cap,
            'min_price': min_price,
            'max_price': max_price,
            'min_volume': min_volume,
            'sectors': sectors or [],
            'premarket_price_change': premarket_price_change,
            'afterhours_price_change': afterhours_price_change
        }
        
        results = finviz_screener.earnings_screener(**params)
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching the criteria.")]
        
        output_lines = [
            f"Earnings Screening Results ({len(results)} stocks found):",
            "=" * 60,
            "",
            "Default Screening Conditions Applied:",
            "- Market Cap: Small and above ($300M+)",
            "- Earnings Date: Yesterday after-hours OR today before-market",
            "- EPS Revision: Positive (upward revision)",
            "- Average Volume: 200,000+",
            "- Price: $10+",
            "- Price Trend: Positive change",
            "- 4-Week Performance: 0% to negative (recovery candidates)",
            "- Volatility: 1x and above",
            "- Stocks Only: ETFs excluded",
            "- Sort: EPS Surprise (descending)",
            "",
            "=" * 60,
            ""
        ]
        
        for stock in results:
            output_lines.extend([
                f"Ticker: {stock.ticker}",
                f"Company: {stock.company_name}",
                f"Sector: {stock.sector}",
                f"Price: ${stock.price:.2f}" if stock.price else "Price: N/A",
                f"Change: {stock.price_change:.2f}%" if stock.price_change else "Change: N/A",
                f"EPS Surprise: {stock.eps_surprise:.2f}%" if stock.eps_surprise else "EPS Surprise: N/A",
                f"Revenue Surprise: {stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "Revenue Surprise: N/A",
                f"Volatility: {stock.volatility:.2f}" if stock.volatility else "Volatility: N/A",
                f"1M Performance: {stock.performance_1m:.2f}%" if stock.performance_1m else "1M Performance: N/A",
                "-" * 40,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in earnings_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def volume_surge_screener() -> List[TextContent]:
    """
    出来高急増を伴う上昇銘柄のスクリーニング（固定条件）
    
    固定フィルタ条件（変更不可）：
    f=cap_smallover,ind_stocksonly,sh_avgvol_o100,sh_price_o10,sh_relvol_o1.5,ta_change_u2,ta_sma200_pa&ft=4&o=-change
    
    - 時価総額：スモール以上 ($300M+)
    - 株式のみ：ETF除外
    - 平均出来高：100,000以上
    - 株価：$10以上
    - 相対出来高：1.5倍以上
    - 価格変動：2%以上上昇
    - 200日移動平均線上
    - 価格変動降順ソート
    - 全件取得（制限なし）
    
    パラメーターなし - 全ての条件は固定されています
    """
    try:
        # 固定条件で実行（パラメーターなし）
        results = finviz_screener.volume_surge_screener()
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching the fixed volume surge criteria.")]
        
        # 固定条件の表示
        fixed_conditions = [
            "固定フィルタ条件:",
            "- 時価総額: スモール以上 ($300M+)",
            "- 株式のみ: ETF除外",
            "- 平均出来高: 100,000以上",
            "- 株価: $10以上",
            "- 相対出来高: 1.5倍以上",
            "- 価格変動: 2%以上上昇",
            "- 200日移動平均線上",
            "- 価格変動降順ソート",
            "- 全件取得（制限なし）"
        ]
        
        # 簡潔な出力形式（ティッカーのみ）
        output_lines = [
            f"Volume Surge Screening Results ({len(results)} stocks found):",
            "=" * 60,
            ""
        ] + fixed_conditions + ["", "Detected Tickers:", "-" * 40, ""]
        
        # ティッカーを10個ずつ1行に表示
        tickers = [stock.ticker for stock in results]
        for i in range(0, len(tickers), 10):
            line_tickers = tickers[i:i+10]
            output_lines.append(" | ".join(line_tickers))
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in volume_surge_screener: {str(e)}")
        raise ToolError(str(e)) from e



server.add_tool(get_stock_fundamentals)

server.add_tool(get_multiple_stocks_fundamentals)

@server.tool()
def trend_reversion_screener(
    market_cap: Optional[str] = "mid_large",
    eps_growth_qoq: Optional[float] = None,
    revenue_growth_qoq: Optional[float] = None,
    rsi_max: Optional[float] = None,
    sectors: Optional[List[str]] = None,
    exclude_sectors: Optional[List[str]] = None
) -> List[TextContent]:
    """
    トレンド反転候補銘柄のスクリーニング
    
    Args:
        market_cap: 時価総額フィルタ (mid_large, large, mega)
        eps_growth_qoq: EPS成長率(QoQ) 最低値
        revenue_growth_qoq: 売上成長率(QoQ) 最低値
        rsi_max: RSI上限値
        sectors: 対象セクター
        exclude_sectors: 除外セクター
    """
    try:
        params = {
            'market_cap': market_cap,
            'eps_growth_qoq': eps_growth_qoq,
            'revenue_growth_qoq': revenue_growth_qoq,
            'rsi_max': rsi_max,
            'sectors': sectors or [],
            'exclude_sectors': exclude_sectors or []
        }
        
        results = finviz_screener.trend_reversion_screener(**params)
        
        if not results:
            return [TextContent(type="text", text="No trend reversal candidates found.")]
        
        output_lines = [
            f"Trend Reversal Screening Results ({len(results)} stocks found):",
            "=" * 60,
            ""
        ]
        
        for stock in results:
            output_lines.extend([
                f"Ticker: {stock.ticker}",
                f"Company: {stock.company_name}",
                f"Sector: {stock.sector}",
                f"Price: ${stock.price:.2f}" if stock.price else "Price: N/A",
                f"P/E Ratio: {stock.pe_ratio:.2f}" if stock.pe_ratio else "P/E Ratio: N/A",
                f"RSI: {stock.rsi:.2f}" if stock.rsi else "RSI: N/A",
                f"EPS Growth: {stock.eps_qoq_growth:.2f}%" if stock.eps_qoq_growth else "EPS Growth: N/A",
                f"Revenue Growth: {stock.sales_qoq_growth:.2f}%" if stock.sales_qoq_growth else "Revenue Growth: N/A",
                "-" * 40,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in trend_reversion_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def uptrend_screener() -> List[TextContent]:
    """
    上昇トレンド銘柄のスクリーニング（固定条件）
    
    固定フィルタ条件：
    - 時価総額：マイクロ以上（$50M+）
    - 平均出来高：100K以上
    - 株価：10以上
    - 52週高値から30%以内
    - 4週パフォーマンス上昇
    - 20日移動平均線上
    - 200日移動平均線上
    - 50日移動平均線が200日移動平均線上
    - 株式のみ
    - EPS成長率（年次）降順ソート
    
    パラメーターなし - 全ての条件は固定されています
    """
    try:
        # 固定パラメーターで実行
        results = finviz_screener.uptrend_screener()
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching the fixed uptrend criteria.")]
        
        # 固定条件の表示
        fixed_conditions = [
            "Fixed Filter Criteria:",
            "- Market Cap: Micro+ ($50M+)",
            "- Avg Volume: 100K+",
            "- Price: $10+",
            "- Within 30% of 52W high",
            "- 4W Performance: Up",
            "- Above SMA20",
            "- Above SMA200", 
            "- SMA50 above SMA200",
            "- Stocks only",
            "- Sorted by EPS growth YoY desc"
        ]
        
        # ティッカーのみをコンパクトに表示
        tickers = [stock.ticker for stock in results]
        
        output_lines = [
            f"Uptrend Screening Results ({len(results)} stocks found):",
            "=" * 60,
            ""
        ] + fixed_conditions + [
            "",
            f"Detected Stocks ({len(tickers)} items):",
            "-" * 40,
            ""
        ]
        
        # ティッカーを1行に10個ずつ表示
        ticker_lines = []
        for i in range(0, len(tickers), 10):
            line_tickers = tickers[i:i+10]
            ticker_lines.append("  " + " | ".join(line_tickers))
        
        output_lines.extend(ticker_lines)
        output_lines.append("")
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in uptrend_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def dividend_growth_screener(
    market_cap: Optional[str] = "midover",
    min_dividend_yield: Optional[float] = 2.0,
    max_dividend_yield: Optional[float] = None,
    min_dividend_growth: Optional[float] = None,
    min_payout_ratio: Optional[float] = None,
    max_payout_ratio: Optional[float] = None,
    min_roe: Optional[float] = None,
    max_debt_equity: Optional[float] = None,
    max_pb_ratio: Optional[float] = 5.0,
    max_pe_ratio: Optional[float] = 30.0,
    eps_growth_5y_positive: Optional[bool] = True,
    eps_growth_qoq_positive: Optional[bool] = True,
    eps_growth_yoy_positive: Optional[bool] = True,
    sales_growth_5y_positive: Optional[bool] = True,
    sales_growth_qoq_positive: Optional[bool] = True,
    country: Optional[str] = "USA",
    stocks_only: Optional[bool] = True,
    sort_by: Optional[str] = "sma200",
    sort_order: Optional[str] = "asc",
    max_results: Optional[int] = 100
) -> List[TextContent]:
    """
    配当成長銘柄のスクリーニング
    
    デフォルト条件（変更可能）：
    - 時価総額：ミッド以上 ($2B+)
    - 配当利回り：2%以上
    - EPS 5年成長率：プラス
    - EPS QoQ成長率：プラス
    - EPS YoY成長率：プラス
    - PBR：5以下
    - PER：30以下
    - 売上5年成長率：プラス
    - 売上QoQ成長率：プラス
    - 地域：アメリカ
    - 株式のみ
    - 200日移動平均でソート
    
    Args:
        market_cap: 時価総額フィルタ (デフォルト: midover)
        min_dividend_yield: 最低配当利回り (デフォルト: 2.0)
        max_dividend_yield: 最高配当利回り
        min_dividend_growth: 最低配当成長率
        min_payout_ratio: 最低配当性向
        max_payout_ratio: 最高配当性向
        min_roe: 最低ROE
        max_debt_equity: 最高負債比率
        max_pb_ratio: 最高PBR (デフォルト: 5.0)
        max_pe_ratio: 最高PER (デフォルト: 30.0)
        eps_growth_5y_positive: EPS 5年成長率プラス (デフォルト: True)
        eps_growth_qoq_positive: EPS QoQ成長率プラス (デフォルト: True)
        eps_growth_yoy_positive: EPS YoY成長率プラス (デフォルト: True)
        sales_growth_5y_positive: 売上5年成長率プラス (デフォルト: True)
        sales_growth_qoq_positive: 売上QoQ成長率プラス (デフォルト: True)
        country: 地域 (デフォルト: USA)
        stocks_only: 株式のみ (デフォルト: True)
        sort_by: ソート基準 (デフォルト: sma200)
        sort_order: ソート順序 (デフォルト: asc)
    """
    try:
        params = {
            'market_cap': market_cap,
            'min_dividend_yield': min_dividend_yield,
            'max_dividend_yield': max_dividend_yield,
            'min_dividend_growth': min_dividend_growth,
            'min_payout_ratio': min_payout_ratio,
            'max_payout_ratio': max_payout_ratio,
            'min_roe': min_roe,
            'max_debt_equity': max_debt_equity,
            'max_pb_ratio': max_pb_ratio,
            'max_pe_ratio': max_pe_ratio,
            'eps_growth_5y_positive': eps_growth_5y_positive,
            'eps_growth_qoq_positive': eps_growth_qoq_positive,
            'eps_growth_yoy_positive': eps_growth_yoy_positive,
            'sales_growth_5y_positive': sales_growth_5y_positive,
            'sales_growth_qoq_positive': sales_growth_qoq_positive,
            'country': country,
            'stocks_only': stocks_only,
            'sort_by': sort_by,
            'sort_order': sort_order,
            'max_results': max_results
        }
        
        results = finviz_screener.dividend_growth_screener(**params)
        
        # Debug: log the first few results to check dividend_yield values
        if results:
            logger.info(f"Debug: First 3 results dividend yields: {[(stock.ticker, stock.dividend_yield) for stock in results[:3]]}")
            # Add a unique marker to verify code changes are active
            print(f"CLAUDE_DEBUG_MARKER: First 3 results dividend yields: {[(stock.ticker, stock.dividend_yield) for stock in results[:3]]}")
        
        if not results:
            return [TextContent(type="text", text="No dividend growth stocks found.")]
        
        # デフォルト条件の表示
        default_conditions = [
            "Default Criteria:",
            "- Market Cap: Mid+ ($2B+)",
            "- Dividend Yield: 2%+",
            "- EPS 5Y Growth: Positive",
            "- EPS QoQ Growth: Positive",
            "- EPS YoY Growth: Positive",
            "- P/B Ratio: ≤5",
            "- P/E Ratio: ≤30",
            "- Sales 5Y Growth: Positive",
            "- Sales QoQ Growth: Positive",
            "- Region: USA",
            "- Stocks Only",
            "- Sorted by SMA200"
        ]
        
        output_lines = [
            f"Dividend Growth Screening Results ({len(results)} stocks found):",
            "=" * 60,
            ""
        ]
        
        # デフォルト条件を表示
        output_lines.extend(default_conditions)
        output_lines.extend(["", "=" * 60, ""])
        
        # 結果を最大件数に制限
        limited_results = results[:max_results] if max_results else results
        
        for stock in limited_results:
            output_lines.extend([
                f"Ticker: {stock.ticker}",
                f"Company: {stock.company_name}",
                f"Sector: {stock.sector}",
                f"Price: ${stock.price:.2f}" if stock.price else "Price: N/A",
                f"Dividend Yield: {stock.dividend_yield:.2f}%" if stock.dividend_yield is not None else "Dividend Yield: N/A",
                f"P/E Ratio: {stock.pe_ratio:.2f}" if stock.pe_ratio else "P/E Ratio: N/A",
                f"Market Cap: {stock.market_cap}" if stock.market_cap else "Market Cap: N/A",
                "-" * 40,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in dividend_growth_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def etf_screener(
    strategy_type: Optional[str] = "long",
    asset_class: Optional[str] = "equity",
    min_aum: Optional[float] = None,
    max_expense_ratio: Optional[float] = None
) -> List[TextContent]:
    """
    ETF戦略用スクリーニング
    
    Args:
        strategy_type: 戦略タイプ (long, short)
        asset_class: 資産クラス (equity, bond, commodity, currency)
        min_aum: 最低運用資産額
        max_expense_ratio: 最高経費率
    """
    try:
        params = {
            'strategy_type': strategy_type,
            'asset_class': asset_class,
            'min_aum': min_aum,
            'max_expense_ratio': max_expense_ratio
        }
        
        results = finviz_screener.etf_screener(**params)
        
        if not results:
            return [TextContent(type="text", text="No ETFs found matching criteria.")]
        
        output_lines = [
            f"ETF Screening Results ({len(results)} ETFs found):",
            "=" * 60,
            ""
        ]
        
        for stock in results:
            output_lines.extend([
                f"Ticker: {stock.ticker}",
                f"Name: {stock.company_name}",
                f"Price: ${stock.price:.2f}" if stock.price else "Price: N/A",
                f"Volume: {stock.volume:,}" if stock.volume else "Volume: N/A",
                f"Change: {stock.price_change:.2f}%" if stock.price_change else "Change: N/A",
                "-" * 40,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in etf_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def earnings_premarket_screener() -> List[TextContent]:
    """
    寄り付き前決算発表で上昇している銘柄のスクリーニング（固定条件）
    
    固定フィルタ条件（変更不可）：
    f=cap_smallover,earningsdate_todaybefore,sh_avgvol_o100,sh_price_o10,ta_change_u2&ft=4&o=-change
    
    - 時価総額：スモール以上（$300M+）
    - 決算発表：今日の寄り付き前
    - 平均出来高：100K以上
    - 株価：$10以上
    - 価格変動：2%以上上昇
    - 株式のみ
    - 価格変動降順ソート
    
    パラメーターなし - 全ての条件は固定されています
    """
    try:
        # 固定パラメーターで実行
        results = finviz_screener.earnings_premarket_screener()
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching the fixed premarket earnings criteria.")]
        
        # 固定条件の表示
        fixed_conditions = [
            "Fixed Filter Criteria:",
            "- Market Cap: Small+ ($300M+)",
            "- Earnings: Today premarket",
            "- Avg Volume: 100K+",
            "- Price: $10+",
            "- Price Change: 2%+ up",
            "- Stocks only",
            "- Sorted by price change desc"
        ]
        
        # 詳細フォーマット出力を使用（固定パラメーター）
        params = {'earnings_timing': 'today_before', 'market_cap': 'smallover'}
        formatted_output = _format_earnings_premarket_list(results, params)
        
        return [TextContent(type="text", text="\n".join(fixed_conditions + [""] + formatted_output))]
        
    except Exception as e:
        logger.error(f"Error in earnings_premarket_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def earnings_afterhours_screener() -> List[TextContent]:
    """
    引け後決算発表で時間外取引上昇銘柄のスクリーニング（固定条件）
    
    固定フィルタ条件（変更不可）：
    f=ah_change_u2,cap_smallover,earningsdate_todayafter,sh_avgvol_o100,sh_price_o10&ft=4&o=-afterchange&ar=60
    
    - 時間外変動：2%以上上昇
    - 時価総額：スモール以上（$300M+）
    - 決算発表：今日の引け後
    - 平均出来高：100K以上
    - 株価：$10以上
    - 株式のみ
    - 時間外変動降順ソート
    - 最大結果：60件
    
    パラメーターなし - 全ての条件は固定されています
    """
    try:
        # 固定パラメーターで実行
        results = finviz_screener.earnings_afterhours_screener()
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching the fixed afterhours earnings criteria.")]
        
        # 固定条件の表示
        fixed_conditions = [
            "Fixed Filter Criteria:",
            "- After-hours Change: 2%+ up",
            "- Market Cap: Small+ ($300M+)",
            "- Earnings: Today after hours",
            "- Avg Volume: 100K+",
            "- Price: $10+",
            "- Stocks only",
            "- Sorted by after-hours change desc",
            "- Max results: 60"
        ]
        
        # 詳細フォーマット出力を使用（固定パラメーター）
        params = {'earnings_timing': 'today_after', 'market_cap': 'smallover'}
        formatted_output = _format_earnings_afterhours_list(results, params)
        
        return [TextContent(type="text", text="\n".join(fixed_conditions + [""] + formatted_output))]
        
    except Exception as e:
        logger.error(f"Error in earnings_afterhours_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def earnings_trading_screener() -> List[TextContent]:
    """
    決算トレード対象銘柄のスクリーニング（固定条件）
    
    固定フィルタ条件（変更不可）：
    f=cap_smallover,earningsdate_yesterdayafter|todaybefore,fa_epsrev_ep,sh_avgvol_o200,sh_price_o10,ta_change_u,ta_perf_0to-4w,ta_volatility_1tox&ft=4&o=-epssurprise&ar=60

    - 時価総額：スモール以上 ($300M+)
    - 決算発表：昨日の引け後または今日の寄り付き前
    - EPS予想：上方修正
    - 平均出来高：200,000以上
    - 株価：$10以上
    - 価格変動：上昇トレンド
    - 4週パフォーマンス：月間プラス（Month Above 0%）
    - ボラティリティ：1倍以上
    - 株式のみ
    - EPSサプライズ降順ソート
    - 最大結果件数：60件
    
    パラメーターなし - 全ての条件は固定されています
    """
    try:
        # 固定条件で実行（パラメーターなし）
        results = finviz_screener.earnings_trading_screener()
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching the specified earnings trading criteria.")]
        
        # 固定条件の表示
        fixed_conditions = [
            "Fixed Filter Criteria:",
            "- Market Cap: Small+ ($300M+)",
            "- Earnings: Yesterday after hours or today premarket",
            "- EPS Forecast: Upward revision",
            "- Avg Volume: 200,000+",
            "- Price: $10+",
            "- Price Trend: Upward",
            "- 4W Performance: 0% to down (recovery candidate)",
            "- Volatility: 1x+",
            "- Stocks only",
            "- Sorted by EPS surprise desc",
            "- Max results: 60"
        ]
        
        # 簡潔な出力形式（ティッカーのみ）
        output_lines = [
            f"Earnings Trading Screening Results ({len(results)} stocks found):",
            "=" * 60,
            ""
        ] + fixed_conditions + ["", "Detected Tickers:", "-" * 40, ""]
        
        # ティッカーを10個ずつ1行に表示
        tickers = [stock.ticker for stock in results]
        for i in range(0, len(tickers), 10):
            line_tickers = tickers[i:i+10]
            output_lines.append(" | ".join(line_tickers))
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in earnings_trading_screener: {str(e)}")
        raise ToolError(str(e)) from e



server.add_tool(get_stock_news)

server.add_tool(get_market_news)

server.add_tool(get_sector_news)

server.add_tool(get_sector_performance)

server.add_tool(get_industry_performance)

server.add_tool(get_country_performance)

server.add_tool(get_sector_specific_industry_performance)

@server.tool()
def get_capitalization_performance() -> List[TextContent]:
    """
    時価総額別パフォーマンス分析
    """
    try:
        # Get capitalization performance data
        cap_data = finviz_sector.get_capitalization_performance()
        
        if not cap_data:
            return [TextContent(type="text", text="No capitalization performance data found.")]
        
        # Format output
        output_lines = [
            "Capitalization Performance Analysis:",
            "=" * 70,
            ""
        ]
        
        # ヘッダー行
        output_lines.extend([
            f"{'Capitalization':<30} {'Market Cap':<15} {'P/E':<8} {'Change':<8} {'Stocks':<6}",
            "-" * 70
        ])
        
        # データ行
        for cap in cap_data:
            output_lines.append(
                f"{cap.get('capitalization', 'N/A'):<30} "
                f"{cap.get('market_cap', 'N/A'):<15} "
                f"{cap.get('pe_ratio', 'N/A'):<8} "
                f"{cap.get('change', 'N/A'):<8} "
                f"{cap.get('stocks', 'N/A'):<6}"
            )
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in get_capitalization_performance: {str(e)}")
        raise ToolError(str(e)) from e

server.add_tool(get_market_overview)

server.add_tool(get_relative_volume_stocks)

@server.tool()
def technical_analysis_screener(
    rsi_min: Optional[Union[int, float, str]] = None,
    rsi_max: Optional[Union[int, float, str]] = None,
    price_vs_sma20: Optional[str] = None,
    price_vs_sma50: Optional[str] = None,
    price_vs_sma200: Optional[str] = None,
    min_price: Optional[Union[int, float, str]] = None,
    min_volume: Optional[Union[int, float]] = None,
    sectors: Optional[List[str]] = None,
    max_results: int = 50
) -> List[TextContent]:
    """
    テクニカル分析ベースのスクリーニング
    
    Args:
        rsi_min: RSI最低値
        rsi_max: RSI最高値
        price_vs_sma20: 20日移動平均との関係 (above, below)
        price_vs_sma50: 50日移動平均との関係 (above, below)
        price_vs_sma200: 200日移動平均との関係 (above, below)
        min_price: 最低株価
        min_volume: 最低出来高
        sectors: 対象セクター
        max_results: 最大取得件数
    """
    try:
        # Build screening parameters
        filters = {}
        
        if rsi_min is not None:
            filters['rsi_min'] = rsi_min
        if rsi_max is not None:
            filters['rsi_max'] = rsi_max
        if price_vs_sma20 == "above":
            filters['sma20_above'] = True
        elif price_vs_sma20 == "below":
            filters['sma20_below'] = True
        if price_vs_sma50 == "above":
            filters['sma50_above'] = True
        elif price_vs_sma50 == "below":
            filters['sma50_below'] = True
        if price_vs_sma200 == "above":
            filters['sma200_above'] = True
        elif price_vs_sma200 == "below":
            filters['sma200_below'] = True
        if min_price is not None:
            filters['price_min'] = min_price
        if min_volume is not None:
            filters['volume_min'] = min_volume
        if sectors:
            filters['sectors'] = sectors
        
        results = finviz_screener.screen_stocks(filters)
        results = results[:max_results or 50]
        
        if not results:
            return [TextContent(type="text", text="No stocks found matching technical criteria.")]
        
        # Format output
        criteria_text = []
        if rsi_min is not None and rsi_max is not None:
            criteria_text.append(f"RSI: {rsi_min}-{rsi_max}")
        elif rsi_min is not None:
            criteria_text.append(f"RSI >= {rsi_min}")
        elif rsi_max is not None:
            criteria_text.append(f"RSI <= {rsi_max}")
        
        if price_vs_sma20:
            criteria_text.append(f"Price {price_vs_sma20} SMA20")
        if price_vs_sma50:
            criteria_text.append(f"Price {price_vs_sma50} SMA50")
        if price_vs_sma200:
            criteria_text.append(f"Price {price_vs_sma200} SMA200")
        
        output_lines = [
            f"Technical Analysis Screening Results:",
            f"Criteria: {', '.join(criteria_text) if criteria_text else 'All stocks'}",
            "=" * 60,
            ""
        ]
        
        for stock in results:
            output_lines.extend([
                f"Ticker: {stock.ticker}",
                f"Company: {stock.company_name}",
                f"Sector: {stock.sector}",
                f"Price: ${stock.price:.2f}" if stock.price else "Price: N/A",
                f"RSI: {stock.rsi:.2f}" if stock.rsi else "RSI: N/A",
                f"SMA 20: ${stock.sma_20:.2f}" if stock.sma_20 else "SMA 20: N/A",
                f"SMA 50: ${stock.sma_50:.2f}" if stock.sma_50 else "SMA 50: N/A",
                f"SMA 200: ${stock.sma_200:.2f}" if stock.sma_200 else "SMA 200: N/A",
                f"Volume: {stock.volume:,}" if stock.volume else "Volume: N/A",
                "-" * 40,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in technical_analysis_screener: {str(e)}")
        raise ToolError(str(e)) from e

def cli_main():
    """CLI entry point - supports stdio (default) and sse transport for Docker"""
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport in ("sse", "streamable-http"):
        host = os.getenv("MCP_HOST", "0.0.0.0")
        port = int(os.getenv("MCP_PORT", "8000"))
        logger.info(f"Starting MCP server with {transport} transport on {host}:{port}")
        server.run(transport=transport, host=host, port=port)
    else:
        server.run()

@server.tool()
def earnings_winners_screener(
    earnings_period: Optional[str] = "this_week",
    market_cap: Optional[str] = "smallover",
    min_price: Optional[Union[int, float, str]] = 10.0,
    min_avg_volume: Optional[str] = "o500",
    min_eps_growth_qoq: Optional[float] = 10.0,
    min_eps_revision: Optional[float] = 5.0,
    min_sales_growth_qoq: Optional[float] = 5.0,
    min_weekly_performance: Optional[str] = "5to-1w",
    sma200_filter: Optional[bool] = True,
    target_sectors: Optional[List[str]] = None,
    max_results: int = 50,
    sort_by: Optional[str] = "performance_1w",
    sort_order: Optional[str] = "desc"
) -> List[TextContent]:
    """
    決算勝ち組銘柄のスクリーニング - 週間パフォーマンス、EPSサプライズ、売上サプライズを含む詳細一覧
    
    Finviz URLと同一の条件・データで決算後に上昇した銘柄を検索し、表形式で詳細データを表示します。
    取得データには以下が含まれます：
    - 週間パフォーマンス（Performance Week）
    - EPSサプライズ（EPS Surprise）
    - 売上サプライズ（Revenue Surprise）
    - EPS前四半期比成長率（EPS QoQ Growth）
    - 売上前四半期比成長率（Sales QoQ Growth）
    - 基本的な株価・出来高データ
    
    Args:
        earnings_period: 決算発表期間 ('this_week', 'yesterday', 'today', 'custom')
        market_cap: 時価総額フィルタ ('small', 'mid', 'large', 'mega', 'smallover')
        min_price: 最低株価 (デフォルト: $10)
        min_avg_volume: 最低平均出来高 (数値または文字列形式、デフォルト: "o500" = 500,000以上)
        min_eps_growth_qoq: 最低EPS前四半期比成長率(%) (デフォルト: 10%)
        min_eps_revision: 最低EPS予想改訂率(%) (デフォルト: 5%)
        min_sales_growth_qoq: 最低売上前四半期比成長率(%) (デフォルト: 5%)
        min_weekly_performance: 週次パフォーマンスフィルタ (デフォルト: 5to-1w)
        sma200_filter: 200日移動平均線上のフィルタ (デフォルト: True)
        target_sectors: 対象セクター (デフォルト: 主要6セクター)
        max_results: 最大取得件数 (デフォルト: 50)
        sort_by: ソート基準 ('performance_1w', 'eps_growth_qoq', 'eps_surprise', 'price_change', 'volume')
        sort_order: ソート順序 ('asc', 'desc')
    
    Returns:
        決算勝ち組銘柄の詳細一覧（表形式 + 分析データ + Finviz URL）
        - メインテーブル: 銘柄 | 企業名 | セクター | 株価 | 週間パフォーマンス | EPSサプライズ | 売上サプライズ | 決算日
        - 上位5銘柄の詳細分析
        - EPSサプライズ統計
        - セクター別パフォーマンス分析
        - 元データのFinviz URL（CSV export形式）
    """
    try:
        # パラメータの準備
        params = {
            'earnings_period': earnings_period,
            'market_cap': market_cap,
            'min_price': min_price,
            'min_avg_volume': min_avg_volume,
            'min_eps_growth_qoq': min_eps_growth_qoq,
            'min_eps_revision': min_eps_revision,
            'min_sales_growth_qoq': min_sales_growth_qoq,
            'min_weekly_performance': min_weekly_performance,
            'sma200_filter': sma200_filter,
            'max_results': max_results,
            'sort_by': sort_by,
            'sort_order': sort_order
        }
        
        # セクター設定
        if target_sectors:
            params['target_sectors'] = target_sectors
        else:
            params['target_sectors'] = [
                "Technology", "Industrials", "Healthcare", 
                "Communication Services", "Consumer Cyclical", "Financial Services"
            ]
        
        # earnings_dateパラメータの設定
        if earnings_period == 'this_week':
            params['earnings_date'] = 'thisweek'
        elif earnings_period == 'yesterday':
            params['earnings_date'] = 'yesterday'
        elif earnings_period == 'today':
            params['earnings_date'] = 'today'
        else:
            params['earnings_date'] = 'thisweek'  # デフォルト
        
        logger.info(f"Executing earnings winners screening with params: {params}")
        
        # スクリーニング実行
        try:
            results = finviz_screener.earnings_winners_screener(**params)
        except Exception as e:
            logger.warning(f"earnings_winners_screener failed, trying earnings_screener: {e}")
            # フォールバック: earnings_screenerメソッドを使用
            fallback_params = {
                'earnings_date': params.get('earnings_date', 'thisweek'),
                'market_cap': params.get('market_cap', 'smallover'),
                'min_price': params.get('min_price'),
                'sectors': params.get('target_sectors')
            }
            fallback_params = {k: v for k, v in fallback_params.items() if v is not None}
            results = finviz_screener.earnings_screener(**fallback_params)
        
        if not results:
            return [TextContent(type="text", text="No earnings winners found matching the criteria.")]
        
        # 結果の表示
        output_lines = _format_earnings_winners_list(results, params)
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in earnings_winners_screener: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def upcoming_earnings_screener(
    earnings_period: Optional[str] = "next_week",
    market_cap: Optional[str] = "smallover",
    min_price: Optional[Union[int, float, str]] = 10,
    min_avg_volume: Optional[str] = "o500",  # Support both numeric and string values - converts internally
    target_sectors: Optional[List[str]] = None,
    pre_earnings_analysis: Optional[Dict[str, Any]] = None,
    risk_assessment: Optional[Dict[str, Any]] = None,
    data_fields: Optional[List[str]] = None,
    max_results: int = 100,
    sort_by: Optional[str] = "earnings_date",
    sort_order: Optional[str] = "asc",
    include_chart_view: Optional[bool] = True,
    earnings_calendar_format: Optional[bool] = False,
    custom_date_range: Optional[str] = None,  # 新機能: カスタム日付範囲 (例: "06-30-2025x07-04-2025")
    start_date: Optional[str] = None,  # 新機能: 開始日 (YYYY-MM-DD format)
    end_date: Optional[str] = None     # 新機能: 終了日 (YYYY-MM-DD format)
) -> List[TextContent]:
    """
    来週決算予定銘柄のスクリーニング（決算トレード事前準備用）
    
    Args:
        earnings_period: 決算発表期間 ('next_week', 'next_2_weeks', 'next_month', 'custom_range')
        market_cap: 時価総額フィルタ ('small', 'mid', 'large', 'mega', 'smallover')
        min_price: 最低株価
        min_avg_volume: 最低平均出来高
        target_sectors: 対象セクター（8セクター）
        pre_earnings_analysis: 決算前分析項目の設定
        risk_assessment: リスク評価項目の設定
        data_fields: 取得するデータフィールド
        max_results: 最大取得件数
        sort_by: ソート基準 ('earnings_date', 'market_cap', 'target_price_upside', 'volatility', 'earnings_potential_score')
        sort_order: ソート順序 ('asc', 'desc')
        include_chart_view: 週足チャートビューを含める
        earnings_calendar_format: 決算カレンダー形式で出力
        custom_date_range: カスタム日付範囲（Finviz形式: "MM-DD-YYYYxMM-DD-YYYY"）
        start_date: 開始日（YYYY-MM-DD形式、end_dateと組み合わせて使用）
        end_date: 終了日（YYYY-MM-DD形式、start_dateと組み合わせて使用）
    
    Returns:
        来週決算予定銘柄のスクリーニング結果
    """
    try:
        # パラメータの準備と正規化
        params = {
            'earnings_period': earnings_period,
            'market_cap': market_cap,
            'min_price': min_price,
            'max_results': max_results,
            'sort_by': sort_by,
            'sort_order': sort_order
        }
        
        # 出来高パラメータの正規化 - 数値と文字列両方をサポート
        if min_avg_volume is not None:
            if isinstance(min_avg_volume, (int, float)):
                # 数値の場合はそのまま使用
                params['avg_volume_min'] = min_avg_volume
            elif isinstance(min_avg_volume, str):
                # 文字列の場合はフィルター値として使用
                params['average_volume'] = min_avg_volume
        
        # セクターの正規化 - upcoming_earnings_screenで使用されるパラメータ名に合わせる
        if target_sectors:
            params['target_sectors'] = target_sectors
        else:
            params['target_sectors'] = [
                "Technology", "Industrials", "Healthcare", "Communication Services",
                "Consumer Cyclical", "Financial Services", "Consumer Defensive", "Basic Materials"
            ]
        
        # 決算前分析項目の設定
        if pre_earnings_analysis:
            params.update(pre_earnings_analysis)
        
        # リスク評価項目の設定
        if risk_assessment:
            params.update(risk_assessment)
        
        # データフィールドの設定は無視（新実装では不要）
        
        # earnings_dateパラメータの設定（優先順位順）
        # 1. カスタム日付範囲が指定されている場合
        if custom_date_range:
            params['earnings_date'] = custom_date_range
        # 2. 開始日と終了日が両方指定されている場合
        elif start_date and end_date:
            params['earnings_date'] = {'start': start_date, 'end': end_date}
        # 3. 従来の期間指定
        elif earnings_period == 'next_week':
            params['earnings_date'] = 'nextweek'
        elif earnings_period == 'next_2_weeks':
            params['earnings_date'] = 'nextdays5'
        elif earnings_period == 'next_month':
            params['earnings_date'] = 'thismonth'
        else:
            params['earnings_date'] = 'nextweek'  # デフォルト
        
        # スクリーニング実行 - 新しいadvanced_screenメソッドを使用
        logger.info(f"Executing upcoming earnings screening with params: {params}")
        logger.info(f"Final earnings_date parameter: {params.get('earnings_date')}")
        # upcoming_earnings_screenメソッドを使用
        try:
            results = finviz_screener.upcoming_earnings_screener(**params)
        except Exception as e:
            logger.warning(f"upcoming_earnings_screen failed, trying earnings_screen: {e}")
            # フォールバック: earnings_screenメソッドを使用
            fallback_params = {
                'earnings_date': params.get('earnings_date', 'nextweek'),
                'market_cap': params.get('market_cap', 'smallover'),
                'min_price': params.get('min_price'),
                'sectors': params.get('target_sectors')
            }
            # None値を除去
            fallback_params = {k: v for k, v in fallback_params.items() if v is not None}
            results = finviz_screener.earnings_screener(**fallback_params)
        
        if not results:
            return [TextContent(type="text", text="No upcoming earnings stocks found.")]
        
        # 結果の表示
        if earnings_calendar_format:
            output_lines = _format_earnings_calendar(results, include_chart_view)
        else:
            output_lines = _format_upcoming_earnings_list(results, include_chart_view)
        
        # Finviz CSV制限についての注意書きを追加
        output_lines.extend([
            "",
            "📋 Note: Finviz CSV export does not include earnings date information in the response,",
            "    even when filtering by earnings date. The stocks above match your earnings date",
            f"    criteria ({earnings_period}) but specific dates are not shown in the CSV data.",
            "    For exact earnings dates, please check the Finviz website directly.",
            "",
            f"🔗 Finviz URL with your filters:",
            f"    {_generate_finviz_url(market_cap, params.get('earnings_date', 'nextweek'))}"
        ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except Exception as e:
        logger.error(f"Error in upcoming_earnings_screener: {str(e)}")
        raise ToolError(str(e)) from e

def _format_earnings_winners_list(results: List, params: Dict[str, Any]) -> List[str]:
    """決算後上昇銘柄をリスト形式でフォーマット"""
    
    # 安全に数値を取得するヘルパー関数
    def safe_float(value, default=0.0):
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default
    
    def safe_int(value, default=0):
        try:
            return int(value) if value is not None else default
        except (ValueError, TypeError):
            return default
    
    # パラメータを安全に取得
    min_price = safe_float(params.get('min_price', 10))
    min_eps_growth = safe_float(params.get('min_eps_growth_qoq', 10))
    min_eps_revision = safe_float(params.get('min_eps_revision', 5))
    min_sales_growth = safe_float(params.get('min_sales_growth_qoq', 5))
    
    output_lines = [
        f"📈 決算勝ち組銘柄一覧 - WeeklyパフォーマンスとEPSサプライズ",
        "",
        f"🎯 スクリーニング条件:",
        f"- 決算発表期間: {params.get('earnings_period', 'this_week')}",
        f"- 時価総額: {params.get('market_cap', 'smallover')} ($300M+)", 
        f"- 最低株価: ${min_price:.1f}",
        f"- 最低平均出来高: {params.get('min_avg_volume', 'o500')}",
        f"- 最低EPS QoQ成長率: {min_eps_growth:.1f}%+",
        f"- 最低EPS予想改訂: {min_eps_revision:.1f}%+",
        f"- 最低売上QoQ成長率: {min_sales_growth:.1f}%+",
        f"- SMA200上: {params.get('sma200_filter', True)}",
        "",
        "=" * 120,
        ""
    ]
    
    # テーブルヘッダー
    output_lines.extend([
        "| 銘柄    | 企業名                              | セクター        | 株価    | 週間パフォーマンス | EPSサプライズ | 売上サプライズ | 決算日      |",
        "|---------|-------------------------------------|-----------------|---------|-------------------|---------------|---------------|-------------|"
    ])
    
    for stock in results:
        # データの整理
        ticker = stock.ticker or "N/A"
        company = (stock.company_name or "N/A")[:35]  # 35文字に制限
        sector = (stock.sector or "N/A")[:15]  # 15文字に制限
        price = f"${stock.price:.2f}" if stock.price else "N/A"
        
        # 週間パフォーマンス
        weekly_perf = f"+{safe_float(stock.performance_1w):.1f}%" if stock.performance_1w else "N/A"
        
        # EPSサプライズ
        eps_surprise = f"+{safe_float(stock.eps_surprise):.1f}%" if stock.eps_surprise else "N/A"
        
        # 売上サプライズ
        revenue_surprise = f"+{safe_float(stock.revenue_surprise):.1f}%" if stock.revenue_surprise else "N/A"
        
        # 決算日
        earnings_date = stock.earnings_date or "N/A"
        
        # テーブル行を作成
        row = f"| {ticker:<7} | {company:<35} | {sector:<15} | {price:<7} | {weekly_perf:>17} | {eps_surprise:>13} | {revenue_surprise:>13} | {earnings_date:<11} |"
        output_lines.append(row)
    
    output_lines.extend([
        "",
        "=" * 120,
        "",
        "🎯 パフォーマンス分析:",
        ""
    ])
    
    # 上位パフォーマーの詳細分析
    if results:
        top_performers = sorted([s for s in results if s.performance_1w], 
                               key=lambda x: x.performance_1w, reverse=True)[:5]
        
        output_lines.append("📈 週間パフォーマンス上位5銘柄:")
        for i, stock in enumerate(top_performers, 1):
            output_lines.extend([
                f"",
                f"🏆 #{i} **{stock.ticker}** - {stock.company_name}",
                f"   📊 週間パフォーマンス: **+{safe_float(stock.performance_1w):.1f}%**",
                f"   💰 株価: ${safe_float(stock.price):.2f}" if stock.price else "   💰 株価: N/A",
                f"   🎯 EPSサプライズ: {safe_float(stock.eps_surprise):.1f}%" if stock.eps_surprise else "   🎯 EPSサプライズ: N/A",
                f"   📈 売上サプライズ: {safe_float(stock.revenue_surprise):.1f}%" if stock.revenue_surprise else "   📈 売上サプライズ: N/A",
                f"   🏢 セクター: {stock.sector}",
                f"   📅 決算日: {stock.earnings_date}" if stock.earnings_date else "   📅 決算日: N/A"
            ])
            
            # 追加メトリクス
            metrics = []
            if stock.eps_qoq_growth or stock.eps_growth_qtr:
                eps_growth = safe_float(stock.eps_qoq_growth or stock.eps_growth_qtr)
                metrics.append(f"EPS QoQ: {eps_growth:.1f}%")
            if stock.sales_qoq_growth or stock.sales_growth_qtr:
                sales_growth = safe_float(stock.sales_qoq_growth or stock.sales_growth_qtr)
                metrics.append(f"売上QoQ: {sales_growth:.1f}%")
            if stock.volume and stock.avg_volume and safe_float(stock.avg_volume) > 0:
                rel_vol = safe_float(stock.volume) / safe_float(stock.avg_volume)
                metrics.append(f"相対出来高: {rel_vol:.1f}x")
            if stock.pe_ratio:
                metrics.append(f"PER: {safe_float(stock.pe_ratio):.1f}")
                
            if metrics:
                output_lines.append(f"   📋 財務指標: {' | '.join(metrics)}")
    
    # サプライズ分析
    surprise_stocks = [s for s in results if s.eps_surprise and safe_float(s.eps_surprise) > 0]
    if surprise_stocks:
        avg_eps_surprise = sum(safe_float(s.eps_surprise) for s in surprise_stocks) / len(surprise_stocks)
        max_eps_surprise = max(safe_float(s.eps_surprise) for s in surprise_stocks)
        
        output_lines.extend([
            "",
            "🎯 EPSサプライズ分析:",
            f"   • 平均EPSサプライズ: {avg_eps_surprise:.1f}%",
            f"   • 最大EPSサプライズ: {max_eps_surprise:.1f}%",
            f"   • ポジティブサプライズ銘柄数: {len(surprise_stocks)}件"
        ])
    
    # セクター分析
    sector_performance = {}
    for stock in results:
        if stock.sector and stock.performance_1w:
            perf_value = safe_float(stock.performance_1w)
            if perf_value != 0:  # 有効な値のみ追加
                if stock.sector not in sector_performance:
                    sector_performance[stock.sector] = []
                sector_performance[stock.sector].append(perf_value)
    
    if sector_performance:
        output_lines.extend([
            "",
            "🏢 セクター別パフォーマンス:",
        ])
        
        for sector, performances in sector_performance.items():
            avg_perf = sum(performances) / len(performances)
            count = len(performances)
            output_lines.append(f"   • {sector}: 平均 {avg_perf:.1f}% ({count}銘柄)")
    
    # Finviz URLを追加
    earnings_date_param = params.get('earnings_date', 'thisweek')
    market_cap_param = params.get('market_cap', 'smallover')
    
    # 環境変数からAPIキーを取得
    import os
    api_key = os.getenv('FINVIZ_API_KEY', 'YOUR_API_KEY_HERE')
    
    finviz_url = f"https://elite.finviz.com/export.ashx?v=151&f=cap_{market_cap_param},earningsdate_{earnings_date_param},fa_epsqoq_o{safe_int(params.get('min_eps_growth_qoq', 10))},fa_epsrev_eo{safe_int(params.get('min_eps_revision', 5))},fa_salesqoq_o{safe_int(params.get('min_sales_growth_qoq', 5))},sec_technology|industrials|healthcare|communicationservices|consumercyclical|financial,sh_avgvol_{params.get('min_avg_volume', 'o500')},sh_price_o{safe_int(params.get('min_price', 10))},ta_perf_{params.get('min_weekly_performance', '5to-1w')},ta_sma200_pa&ft=4&o=ticker&ar={safe_int(params.get('max_results', 50))}&c=0,1,2,79,3,4,5,6,7,8,9,10,11,12,13,73,74,75,14,15,16,77,17,18,19,20,21,23,22,82,78,127,128,24,25,85,26,27,28,29,30,31,84,32,33,34,35,36,37,38,39,40,41,90,91,92,93,94,95,96,97,98,99,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,80,83,76,60,61,62,63,64,67,89,69,81,86,87,88,65,66,71,72,103,100,101,104,102,106,107,108,109,110,125,126,59,68,70,111,112,113,114,115,116,117,118,119,120,121,122,123,124,105&auth={api_key}"
    
    output_lines.extend([
        "",
        "🔗 同一結果をFinvizで確認:",
        f"   {finviz_url}",
        "",
        "💡 これらの銘柄は最近決算を発表し、強いパフォーマンスと良好なファンダメンタル指標を示しています。",
        "   モメンタム取引や詳細分析の対象として検討してください。"
    ])
    
    return output_lines

def _generate_finviz_url(market_cap: str, earnings_date) -> str:
    """Finviz URLを生成"""
    base_url = "https://elite.finviz.com/screener.ashx?v=311&f="
    
    # Market cap filter
    cap_filter = f"cap_{market_cap or 'smallover'}"
    
    # Earnings date filter
    if isinstance(earnings_date, dict):
        # 辞書形式の場合（start/end）
        from .finviz_client.base import FinvizClient
        client = FinvizClient()
        start_formatted = client._format_date_for_finviz(earnings_date['start'])
        end_formatted = client._format_date_for_finviz(earnings_date['end'])
        earnings_filter = f"earningsdate_{start_formatted}x{end_formatted}"
    elif isinstance(earnings_date, str) and 'x' in earnings_date:
        # 日付範囲文字列の場合
        earnings_filter = f"earningsdate_{earnings_date}"
    else:
        # 固定期間の場合
        earnings_filter = f"earningsdate_{earnings_date}"
    
    return f"{base_url}{cap_filter},{earnings_filter}"

def _format_upcoming_earnings_list(results: List, include_chart_view: bool = True) -> List[str]:
    """来週決算予定銘柄をリスト形式でフォーマット"""
    output_lines = [
        f"Upcoming Earnings Screening Results ({len(results)} stocks found):",
        "=" * 70,
        ""
    ]
    
    for stock in results:
        output_lines.extend([
            f"📈 {stock.ticker} - {stock.company_name}",
            f"   Sector: {stock.sector} | Industry: {stock.industry}",
            f"   Earnings Date: {stock.earnings_date or 'Not available in CSV'} | Timing: {stock.earnings_timing or 'N/A'}",
            f"   Current Price: ${stock.current_price:.2f}" if stock.current_price else "   Current Price: N/A",
            f"   Market Cap: {format_large_number(stock.market_cap * 1e6)}" if stock.market_cap else "   Market Cap: N/A",
            f"   PE Ratio: {stock.pe_ratio:.2f}" if stock.pe_ratio else "   PE Ratio: N/A",
            f"   Target Price: ${stock.target_price:.2f}" if stock.target_price else "   Target Price: N/A",
            f"   Target Upside: {stock.target_price_upside:.1f}%" if stock.target_price_upside else "   Target Upside: N/A",
            f"   Analyst Recommendation: {stock.analyst_recommendation}" if stock.analyst_recommendation else "   Analyst Recommendation: N/A",
            f"   Volatility: {stock.volatility:.2f}" if stock.volatility else "   Volatility: N/A",
            f"   Short Interest: {stock.short_interest:.1f}%" if stock.short_interest else "   Short Interest: N/A",
            f"   Avg Volume: {format_large_number(stock.avg_volume)}" if stock.avg_volume else "   Avg Volume: N/A",
            ""
        ])
        
        # Additional metrics (if available)
        additional_metrics = []
        if stock.performance_1w is not None:
            additional_metrics.append(f"   • 1W Performance: {stock.performance_1w:.1f}%")
        if stock.performance_1m is not None:
            additional_metrics.append(f"   • 1M Performance: {stock.performance_1m:.1f}%")
        if stock.rsi is not None:
            additional_metrics.append(f"   • RSI: {stock.rsi:.1f}")
        
        if additional_metrics:
            output_lines.extend([
                "   📊 Additional Metrics:",
                *additional_metrics,
                ""
            ])
        
        output_lines.append("-" * 70)
        output_lines.append("")
    
    return output_lines

def _format_earnings_calendar(results: List, include_chart_view: bool = True) -> List[str]:
    """来週決算予定銘柄をカレンダー形式でフォーマット"""
    output_lines = [
        f"📅 Upcoming Earnings Calendar ({len(results)} stocks)",
        "=" * 70,
        ""
    ]
    
    # 日付ごとにグループ化
    by_date = {}
    for stock in results:
        date = stock.earnings_date or "Unknown"
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(stock)
    
    # 日付順でソート
    for date in sorted(by_date.keys()):
        stocks = by_date[date]
        output_lines.extend([
            f"📅 {date}",
            "-" * 30,
            ""
        ])
        
        for stock in stocks:
            upside_str = f"(+{stock.target_price_upside:.1f}%)" if stock.target_price_upside and stock.target_price_upside > 0 else ""
            output_lines.extend([
                f"  • {stock.ticker} - {stock.company_name}",
                f"    ${stock.current_price:.2f} → ${stock.target_price:.2f} {upside_str}" if stock.current_price and stock.target_price else f"    Current: ${stock.current_price:.2f}" if stock.current_price else "    Price: N/A",
                f"    {stock.sector} | PE: {stock.pe_ratio:.1f}" if stock.pe_ratio else f"    {stock.sector}",
                ""
            ])
        
        output_lines.append("")
    
    return output_lines

def _format_earnings_premarket_list(results: List, params: Dict[str, Any]) -> List[str]:
    """寄り付き前決算上昇銘柄の詳細フォーマット"""
    def format_large_number(num):
        if not num:
            return "N/A"
        if num >= 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.0f}"
    
    output_lines = [
        "🔍 Premarket Earnings Screening Results",
        f"📊 Stocks Detected: {len(results)}",
        "=" * 100,
        "",
        "📋 Applied Screening Criteria:",
        f"   • Market Cap: {params.get('market_cap', 'smallover')} (Small+)",
        f"   • Earnings Timing: {params.get('earnings_timing', 'today_before')} (Today Premarket)",
        f"   • Min Price: ${params.get('min_price', 10):.2f}",
        f"   • Min Avg Volume: {format_large_number(params.get('min_avg_volume', 100000))}",
        f"   • Min Price Change: {params.get('min_price_change', 2.0):.1f}%",
        f"   • Sort: {params.get('sort_by', 'price_change')} ({params.get('sort_order', 'desc')})",
        "",
        "=" * 100,
        ""
    ]
    
    # 詳細な銘柄一覧
    output_lines.extend([
        "📈 Detailed Data:",
        "",
        "| Ticker | Company | Sector | Price | Change | PreMkt | EPS Surprise | Revenue Surprise | Perf 1W | Volume |",
        "|--------|---------|--------|-------|--------|--------|--------------|------------------|---------|--------|"
    ])
    
    for i, stock in enumerate(results[:10]):  # 上位10銘柄
        price_str = f"${stock.price:.2f}" if stock.price else "N/A"
        change_str = f"{stock.price_change:.2f}%" if stock.price_change else "N/A"
        premarket_str = f"{stock.premarket_change_percent:.2f}%" if stock.premarket_change_percent else "N/A"
        eps_surprise_str = f"{stock.eps_surprise:.2f}%" if stock.eps_surprise else "N/A"
        revenue_surprise_str = f"{stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "N/A"
        perf_1w_str = f"{stock.performance_1w:.2f}%" if stock.performance_1w else "N/A"
        volume_str = format_large_number(stock.volume) if stock.volume else "N/A"
        
        ticker_display = stock.ticker or "N/A"
        company_display = (stock.company_name[:15] + "...") if stock.company_name and len(stock.company_name) > 15 else (stock.company_name or "N/A")
        sector_display = (stock.sector[:12] + "...") if stock.sector and len(stock.sector) > 12 else (stock.sector or "N/A")
        
        output_lines.append(f"| {ticker_display:<6} | {company_display:<15} | {sector_display:<12} | {price_str:<7} | {change_str:<8} | {premarket_str:<8} | {eps_surprise_str:<12} | {revenue_surprise_str:<16} | {perf_1w_str:<7} | {volume_str:<6} |")
    
    output_lines.extend([
        "",
        "=" * 100,
        "",
        "🏆 上位5銘柄の詳細分析:",
        ""
    ])
    
    # 上位5銘柄の詳細情報
    for i, stock in enumerate(results[:5], 1):
        output_lines.extend([
            f"#{i} 📊 {stock.ticker} - {stock.company_name}",
            f"   📈 Price: ${stock.price:.2f} | Change: {stock.price_change:.2f}%" if stock.price and stock.price_change else f"   📈 Price: {stock.price:.2f} | Change: N/A" if stock.price else "   📈 Price: N/A | Change: N/A",
            f"   🔔 Premarket: {stock.premarket_change_percent:.2f}%" if stock.premarket_change_percent else "   🔔 Premarket: N/A",
            f"   💼 Sector: {stock.sector} | Volume: {format_large_number(stock.volume)}" if stock.sector and stock.volume else f"   💼 Sector: {stock.sector or 'N/A'} | Volume: {format_large_number(stock.volume) if stock.volume else 'N/A'}",
            f"   📊 EPS Surprise: {stock.eps_surprise:.2f}%" if stock.eps_surprise else "   📊 EPS Surprise: N/A",
            f"   💰 Revenue Surprise: {stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "   💰 Revenue Surprise: N/A",
            f"   📈 Performance 1W: {stock.performance_1w:.2f}%" if stock.performance_1w else "   📈 Performance 1W: N/A",
            ""
        ])
    
    # 統計情報
    eps_surprises = [s.eps_surprise for s in results if s.eps_surprise is not None]
    revenue_surprises = [s.revenue_surprise for s in results if s.revenue_surprise is not None]
    
    if eps_surprises:
        avg_eps = sum(eps_surprises) / len(eps_surprises)
        max_eps = max(eps_surprises)
        output_lines.extend([
            "📊 EPSサプライズ統計:",
            f"   • 平均: {avg_eps:.2f}%",
            f"   • 最大: {max_eps:.2f}%",
            f"   • サンプル数: {len(eps_surprises)}",
            ""
        ])
    
    # セクター別分析
    sector_counts = {}
    for stock in results:
        if stock.sector:
            sector_counts[stock.sector] = sector_counts.get(stock.sector, 0) + 1
    
    if sector_counts:
        output_lines.extend([
            "🏢 セクター別分析:",
            *[f"   • {sector}: {count}銘柄" for sector, count in sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
            ""
        ])
    
    return output_lines

def _format_earnings_afterhours_list(results: List, params: Dict[str, Any]) -> List[str]:
    """時間外決算上昇銘柄の詳細フォーマット"""
    def format_large_number(num):
        if not num:
            return "N/A"
        if num >= 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.0f}"
    
    output_lines = [
        "🌙 After-Hours Earnings Screening Results",
        f"📊 Stocks Detected: {len(results)}",
        "=" * 100,
        "",
        "📋 Applied Screening Criteria:",
        f"   • Market Cap: {params.get('market_cap', 'smallover')} (Small+)",
        f"   • Earnings Timing: {params.get('earnings_timing', 'today_after')} (Today After Hours)",
        f"   • Min Price: ${params.get('min_price', 10):.2f}",
        f"   • Min Avg Volume: {format_large_number(params.get('min_avg_volume', 100000))}",
        f"   • Min After-Hours Change: {params.get('min_afterhours_change', 2.0):.1f}%",
        f"   • Sort: {params.get('sort_by', 'afterhours_change')} ({params.get('sort_order', 'desc')})",
        "",
        "=" * 100,
        ""
    ]
    
    # 詳細な銘柄一覧
    output_lines.extend([
        "📈 Detailed Data:",
        "",
        "| Ticker | Company | Sector | Price | Change | AftHrs | EPS Surprise | Revenue Surprise | Perf 1W | Volume |",
        "|--------|---------|--------|-------|--------|--------|--------------|------------------|---------|--------|"
    ])
    
    for i, stock in enumerate(results[:10]):  # 上位10銘柄
        price_str = f"${stock.price:.2f}" if stock.price else "N/A"
        change_str = f"{stock.price_change:.2f}%" if stock.price_change else "N/A"
        afterhours_str = f"{stock.afterhours_change_percent:.2f}%" if stock.afterhours_change_percent else "N/A"
        eps_surprise_str = f"{stock.eps_surprise:.2f}%" if stock.eps_surprise else "N/A"
        revenue_surprise_str = f"{stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "N/A"
        perf_1w_str = f"{stock.performance_1w:.2f}%" if stock.performance_1w else "N/A"
        volume_str = format_large_number(stock.volume) if stock.volume else "N/A"
        
        ticker_display = stock.ticker or "N/A"
        company_display = (stock.company_name[:15] + "...") if stock.company_name and len(stock.company_name) > 15 else (stock.company_name or "N/A")
        sector_display = (stock.sector[:12] + "...") if stock.sector and len(stock.sector) > 12 else (stock.sector or "N/A")
        
        output_lines.append(f"| {ticker_display:<6} | {company_display:<15} | {sector_display:<12} | {price_str:<7} | {change_str:<8} | {afterhours_str:<8} | {eps_surprise_str:<12} | {revenue_surprise_str:<16} | {perf_1w_str:<7} | {volume_str:<6} |")
    
    output_lines.extend([
        "",
        "=" * 100,
        "",
        "🏆 上位5銘柄の詳細分析:",
        ""
    ])
    
    # 上位5銘柄の詳細情報
    for i, stock in enumerate(results[:5], 1):
        output_lines.extend([
            f"#{i} 📊 {stock.ticker} - {stock.company_name}",
            f"   📈 Price: ${stock.price:.2f} | Change: {stock.price_change:.2f}%" if stock.price and stock.price_change else f"   📈 Price: {stock.price:.2f} | Change: N/A" if stock.price else "   📈 Price: N/A | Change: N/A",
            f"   🌙 After Hours: {stock.afterhours_change_percent:.2f}%" if stock.afterhours_change_percent else "   🌙 After Hours: N/A",
            f"   💼 Sector: {stock.sector} | Volume: {format_large_number(stock.volume)}" if stock.sector and stock.volume else f"   💼 Sector: {stock.sector or 'N/A'} | Volume: {format_large_number(stock.volume) if stock.volume else 'N/A'}",
            f"   📊 EPS Surprise: {stock.eps_surprise:.2f}%" if stock.eps_surprise else "   📊 EPS Surprise: N/A",
            f"   💰 Revenue Surprise: {stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "   💰 Revenue Surprise: N/A",
            f"   📈 Performance 1W: {stock.performance_1w:.2f}%" if stock.performance_1w else "   📈 Performance 1W: N/A",
            ""
        ])
    
    # 統計情報
    eps_surprises = [s.eps_surprise for s in results if s.eps_surprise is not None]
    revenue_surprises = [s.revenue_surprise for s in results if s.revenue_surprise is not None]
    
    if eps_surprises:
        avg_eps = sum(eps_surprises) / len(eps_surprises)
        max_eps = max(eps_surprises)
        output_lines.extend([
            "📊 EPSサプライズ統計:",
            f"   • 平均: {avg_eps:.2f}%",
            f"   • 最大: {max_eps:.2f}%",
            f"   • サンプル数: {len(eps_surprises)}",
            ""
        ])
    
    # セクター別分析
    sector_counts = {}
    for stock in results:
        if stock.sector:
            sector_counts[stock.sector] = sector_counts.get(stock.sector, 0) + 1
    
    if sector_counts:
        output_lines.extend([
            "🏢 セクター別分析:",
            *[f"   • {sector}: {count}銘柄" for sector, count in sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
            ""
        ])
    
    return output_lines

def _format_earnings_trading_list(results: List, params: Dict[str, Any]) -> List[str]:
    """決算トレード対象銘柄の詳細フォーマット"""
    def format_large_number(num):
        if not num:
            return "N/A"
        if num >= 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.0f}"
    
    output_lines = [
        "🎯 決算トレード対象銘柄スクリーニング結果",
        f"📊 検出銘柄数: {len(results)}",
        "=" * 100,
        "",
        "📋 適用されたスクリーニング条件:",
        f"   • 時価総額: {params.get('market_cap', 'smallover')} (スモール以上)",
        f"   • 決算期間: {params.get('earnings_window', 'yesterday_after_today_before')} (昨日引け後-今日寄り付き前)",
        f"   • 最低価格: ${params.get('min_price', 10):.2f}",
        f"   • 最低平均出来高: {format_large_number(params.get('min_avg_volume', 200000))}",
        f"   • 決算予想修正: {params.get('earnings_revision', 'eps_revenue_positive')} (EPS/売上上方修正)",
        f"   • 価格トレンド: {params.get('price_trend', 'positive_change')} (ポジティブ)",
        f"   • 4週パフォーマンス: {params.get('performance_4w_range', '0_to_negative_4w')} (回復候補)",
        f"   • 最低ボラティリティ: {params.get('min_volatility', 1.0):.1f}倍",
        f"   • ソート: {params.get('sort_by', 'eps_surprise')} ({params.get('sort_order', 'desc')})",
        "",
        "=" * 100,
        ""
    ]
    
    # 詳細な銘柄一覧
    output_lines.extend([
        "📈 詳細データ:",
        "",
        "| Ticker | Company | Sector | Price | Change | EPS Surprise | Revenue Surprise | Perf 1W | Volatility | Volume |",
        "|--------|---------|--------|-------|--------|--------------|------------------|---------|------------|--------|"
    ])
    
    for i, stock in enumerate(results[:10]):  # 上位10銘柄
        price_str = f"${stock.price:.2f}" if stock.price else "N/A"
        change_str = f"{stock.price_change:.2f}%" if stock.price_change else "N/A"
        eps_surprise_str = f"{stock.eps_surprise:.2f}%" if stock.eps_surprise else "N/A"
        revenue_surprise_str = f"{stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "N/A"
        perf_1w_str = f"{stock.performance_1w:.2f}%" if stock.performance_1w else "N/A"
        volatility_str = f"{stock.volatility:.2f}" if stock.volatility else "N/A"
        volume_str = format_large_number(stock.volume) if stock.volume else "N/A"
        
        ticker_display = stock.ticker or "N/A"
        company_display = (stock.company_name[:15] + "...") if stock.company_name and len(stock.company_name) > 15 else (stock.company_name or "N/A")
        sector_display = (stock.sector[:12] + "...") if stock.sector and len(stock.sector) > 12 else (stock.sector or "N/A")
        
        output_lines.append(f"| {ticker_display:<6} | {company_display:<15} | {sector_display:<12} | {price_str:<7} | {change_str:<8} | {eps_surprise_str:<12} | {revenue_surprise_str:<16} | {perf_1w_str:<7} | {volatility_str:<10} | {volume_str:<6} |")
    
    output_lines.extend([
        "",
        "=" * 100,
        "",
        "🏆 上位5銘柄の詳細分析:",
        ""
    ])
    
    # 上位5銘柄の詳細情報
    for i, stock in enumerate(results[:5], 1):
        output_lines.extend([
            f"#{i} 📊 {stock.ticker} - {stock.company_name}",
            f"   📈 Price: ${stock.price:.2f} | Change: {stock.price_change:.2f}%" if stock.price and stock.price_change else f"   📈 Price: {stock.price:.2f} | Change: N/A" if stock.price else "   📈 Price: N/A | Change: N/A",
            f"   💼 Sector: {stock.sector} | Volume: {format_large_number(stock.volume)}" if stock.sector and stock.volume else f"   💼 Sector: {stock.sector or 'N/A'} | Volume: {format_large_number(stock.volume) if stock.volume else 'N/A'}",
            f"   📊 EPS Surprise: {stock.eps_surprise:.2f}%" if stock.eps_surprise else "   📊 EPS Surprise: N/A",
            f"   💰 Revenue Surprise: {stock.revenue_surprise:.2f}%" if stock.revenue_surprise else "   💰 Revenue Surprise: N/A",
            f"   📈 Performance 1W: {stock.performance_1w:.2f}%" if stock.performance_1w else "   📈 Performance 1W: N/A",
            f"   📊 Volatility: {stock.volatility:.2f}" if stock.volatility else "   📊 Volatility: N/A",
            f"   📈 Performance 1M: {stock.performance_1m:.2f}%" if stock.performance_1m else "   📈 Performance 1M: N/A",
            ""
        ])
    
    # 統計情報
    eps_surprises = [s.eps_surprise for s in results if s.eps_surprise is not None]
    revenue_surprises = [s.revenue_surprise for s in results if s.revenue_surprise is not None]
    volatilities = [s.volatility for s in results if s.volatility is not None]
    
    if eps_surprises:
        avg_eps = sum(eps_surprises) / len(eps_surprises)
        max_eps = max(eps_surprises)
        output_lines.extend([
            "📊 EPSサプライズ統計:",
            f"   • 平均: {avg_eps:.2f}%",
            f"   • 最大: {max_eps:.2f}%",
            f"   • サンプル数: {len(eps_surprises)}",
            ""
        ])
    
    if volatilities:
        avg_volatility = sum(volatilities) / len(volatilities)
        max_volatility = max(volatilities)
        output_lines.extend([
            "📊 ボラティリティ統計:",
            f"   • 平均: {avg_volatility:.2f}",
            f"   • 最大: {max_volatility:.2f}",
            f"   • サンプル数: {len(volatilities)}",
            ""
        ])
    
    # セクター別分析
    sector_counts = {}
    for stock in results:
        if stock.sector:
            sector_counts[stock.sector] = sector_counts.get(stock.sector, 0) + 1
    
    if sector_counts:
        output_lines.extend([
            "🏢 セクター別分析:",
            *[f"   • {sector}: {count}銘柄" for sector, count in sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
            ""
        ])
    
    return output_lines

@server.tool()
def get_sec_filings(
    ticker: str,
    form_types: Optional[List[str]] = None,
    days_back: int = 30,
    max_results: int = 50,
    sort_by: str = "filing_date",
    sort_order: str = "desc"
) -> List[TextContent]:
    """
    指定銘柄のSECファイリングデータを取得
    
    Args:
        ticker: 銘柄ティッカー
        form_types: フォームタイプフィルタ (例: ["10-K", "10-Q", "8-K"])
        days_back: 過去何日分のファイリング (デフォルト: 30日)
        max_results: 最大取得件数 (デフォルト: 50件)
        sort_by: ソート基準 ("filing_date", "report_date", "form")
        sort_order: ソート順序 ("asc", "desc")
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        # Get SEC filings data
        filings = finviz_sec.get_sec_filings(
            ticker=ticker,
            form_types=form_types,
            days_back=days_back,
            max_results=max_results,
            sort_by=sort_by,
            sort_order=sort_order
        )
        
        if not filings:
            return [TextContent(type="text", text=f"No SEC filings found for {ticker} in the last {days_back} days.")]
        
        # Format output
        form_filter_text = f" (Forms: {', '.join(form_types)})" if form_types else ""
        output_lines = [
            f"📄 SEC Filings for {ticker}{form_filter_text}:",
            f"📅 Period: Last {days_back} days | Results: {len(filings)} filings",
            "=" * 80,
            ""
        ]
        
        for filing in filings:
            output_lines.extend([
                f"📅 Filing Date: {filing.filing_date} | Report Date: {filing.report_date}",
                f"📋 Form: {filing.form}",
                f"📝 Description: {filing.description}",
                f"🔗 Filing URL: {filing.filing_url}",
                f"📄 Document URL: {filing.document_url}",
                "-" * 60,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_sec_filings: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_sec_filings: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def get_major_sec_filings(
    ticker: str,
    days_back: int = 90
) -> List[TextContent]:
    """
    主要なSECファイリング（10-K, 10-Q, 8-K等）を取得
    
    Args:
        ticker: 銘柄ティッカー
        days_back: 過去何日分のファイリング (デフォルト: 90日)
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        # Get major filings
        filings = finviz_sec.get_major_filings(ticker, days_back)
        
        if not filings:
            return [TextContent(type="text", text=f"No major SEC filings found for {ticker} in the last {days_back} days.")]
        
        # Format output
        output_lines = [
            f"📊 Major SEC Filings for {ticker}:",
            f"📅 Period: Last {days_back} days | Results: {len(filings)} filings",
            "=" * 80,
            "",
            "📋 Form Types: 10-K (Annual), 10-Q (Quarterly), 8-K (Current), DEF 14A (Proxy), SC 13G/D (Ownership)",
            "",
            "=" * 80,
            ""
        ]
        
        # Group by form type for better organization
        forms_dict = {}
        for filing in filings:
            form_type = filing.form
            if form_type not in forms_dict:
                forms_dict[form_type] = []
            forms_dict[form_type].append(filing)
        
        for form_type, form_filings in forms_dict.items():
            output_lines.extend([
                f"📋 Form {form_type} ({len(form_filings)} filings):",
                "-" * 40,
                ""
            ])
            
            for filing in form_filings:
                output_lines.extend([
                    f"  📅 {filing.filing_date} | Report: {filing.report_date}",
                    f"  📝 {filing.description}",
                    f"  🔗 Filing: {filing.filing_url}",
                    f"  📄 Document: {filing.document_url}",
                    ""
                ])
            
            output_lines.append("")
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_major_sec_filings: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_major_sec_filings: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def get_insider_sec_filings(
    ticker: str,
    days_back: int = 30
) -> List[TextContent]:
    """
    インサイダー取引関連のSECファイリング（フォーム3, 4, 5等）を取得
    
    Args:
        ticker: 銘柄ティッカー
        days_back: 過去何日分のファイリング (デフォルト: 30日)
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        # Get insider filings
        filings = finviz_sec.get_insider_filings(ticker, days_back)
        
        if not filings:
            return [TextContent(type="text", text=f"No insider SEC filings found for {ticker} in the last {days_back} days.")]
        
        # Format output
        output_lines = [
            f"👥 Insider SEC Filings for {ticker}:",
            f"📅 Period: Last {days_back} days | Results: {len(filings)} filings",
            "=" * 80,
            "",
            "📋 Form Types:",
            "  • Form 3: Initial ownership statement",
            "  • Form 4: Statement of changes in beneficial ownership",
            "  • Form 5: Annual statement of changes in beneficial ownership",
            "  • 11-K: Annual reports of employee stock purchase plans",
            "",
            "=" * 80,
            ""
        ]
        
        for filing in filings:
            # Determine filing type explanation
            form_explanation = {
                "3": "Initial ownership statement",
                "4": "Changes in beneficial ownership",
                "5": "Annual ownership changes",
                "11-K": "Employee stock purchase plan report"
            }.get(filing.form, "Insider-related filing")
            
            output_lines.extend([
                f"📋 Form {filing.form} - {form_explanation}",
                f"📅 Filing: {filing.filing_date} | Report: {filing.report_date}",
                f"📝 {filing.description}",
                f"🔗 Filing: {filing.filing_url}",
                f"📄 Document: {filing.document_url}",
                "-" * 60,
                ""
            ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_insider_sec_filings: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_insider_sec_filings: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def get_sec_filing_summary(
    ticker: str,
    days_back: int = 90
) -> List[TextContent]:
    """
    指定期間のSECファイリング概要とサマリーを取得
    
    Args:
        ticker: 銘柄ティッカー
        days_back: 過去何日分の概要 (デフォルト: 90日)
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        # Get filing summary
        summary = finviz_sec.get_filing_summary(ticker, days_back)
        
        if "error" in summary:
            return [TextContent(type="text", text=f"Error getting filing summary for {ticker}: {summary['error']}")]
        
        if summary.get("total_filings", 0) == 0:
            return [TextContent(type="text", text=f"No SEC filings found for {ticker} in the last {days_back} days.")]
        
        # Format output
        output_lines = [
            f"📊 SEC Filing Summary for {ticker}:",
            f"📅 Period: Last {summary['period_days']} days",
            f"📄 Total Filings: {summary['total_filings']}",
            f"📅 Latest Filing: {summary.get('latest_filing_date', 'N/A')} ({summary.get('latest_filing_form', 'N/A')})",
            "=" * 60,
            "",
            "📋 Filing Breakdown by Form Type:",
            "-" * 40
        ]
        
        # Sort forms by count (descending)
        forms = summary.get("forms", {})
        sorted_forms = sorted(forms.items(), key=lambda x: x[1], reverse=True)
        
        for form_type, count in sorted_forms:
            percentage = (count / summary['total_filings'] * 100) if summary['total_filings'] > 0 else 0
            output_lines.append(f"  📋 {form_type}: {count} filings ({percentage:.1f}%)")
        
        output_lines.extend([
            "",
            "📝 Common Form Types:",
            "  • 10-K: Annual report (comprehensive overview)",
            "  • 10-Q: Quarterly report (financial updates)",
            "  • 8-K: Current report (material events)",
            "  • DEF 14A: Proxy statement (shareholder meetings)",
            "  • 4: Insider trading activities",
            "  • SC 13G/D: Beneficial ownership (>5% ownership changes)"
        ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_sec_filing_summary: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_sec_filing_summary: {str(e)}")
        raise ToolError(str(e)) from e

server.add_tool(get_edgar_filing_content)

server.add_tool(get_multiple_edgar_filing_contents)

@server.tool()
def get_edgar_company_filings(
    ticker: str,
    form_types: Optional[List[str]] = None,
    max_count: int = 50,
    days_back: int = 365
) -> List[TextContent]:
    """
    EDGAR API経由で企業のファイリング一覧を取得
    
    Args:
        ticker: 銘柄ティッカー
        form_types: フォームタイプフィルタ (例: ["10-K", "10-Q", "8-K"])
        max_count: 最大取得件数 (デフォルト: 50)
        days_back: 過去何日分 (デフォルト: 365日)
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        logger.info(f"Fetching EDGAR filings for {ticker} via EDGAR API")
        
        # Calculate date range
        from datetime import datetime, timedelta
        date_to = datetime.now().strftime('%Y-%m-%d')
        date_from = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        # Get company filings via EDGAR API
        filings = edgar_client.get_company_filings(
            ticker=ticker,
            form_types=form_types,
            date_from=date_from,
            date_to=date_to,
            max_count=max_count
        )
        
        if not filings:
            form_filter_text = f" (forms: {', '.join(form_types)})" if form_types else ""
            return [TextContent(type="text", text=f"No EDGAR filings found for {ticker}{form_filter_text} in the last {days_back} days.")]
        
        # Format output
        output_lines = [
            f"📊 EDGAR Company Filings for {ticker}:",
            f"📅 Period: {date_from} to {date_to} ({days_back} days)",
            f"📄 Results: {len(filings)} filings",
        ]
        
        if form_types:
            output_lines.append(f"📋 Form Filter: {', '.join(form_types)}")
        
        output_lines.extend([
            "=" * 80,
            "",
            "📋 Available Form Types:",
            "  • 10-K: Annual report",
            "  • 10-Q: Quarterly report", 
            "  • 8-K: Current report (material events)",
            "  • DEF 14A: Proxy statement",
            "  • 4: Statement of changes in beneficial ownership",
            "",
            "=" * 80,
            ""
        ])
        
        for filing in filings:
            output_lines.extend([
                f"📋 Form {filing['form']} - {filing.get('description', 'N/A')}",
                f"📅 Filing: {filing['filing_date']} | Report: {filing['report_date']}",
                f"📄 Document: {filing['accession_number']}/{filing['primary_document']}",
                f"🔗 Filing URL: {filing['filing_url']}",
                f"📄 Document URL: {filing['document_url']}",
                "-" * 60,
                ""
            ])
        
        output_lines.extend([
            "",
            "💡 To get document content, use get_edgar_filing_content with:",
            "   ticker, accession_number, and primary_document from above"
        ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_edgar_company_filings: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_edgar_company_filings: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def get_edgar_company_facts(
    ticker: str
) -> List[TextContent]:
    """
    EDGAR API経由で企業の基本情報とファクトデータを取得
    
    Args:
        ticker: 銘柄ティッカー
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        logger.info(f"Fetching EDGAR company facts for {ticker}")
        
        # Get CIK from ticker first
        cik = edgar_client._get_cik_from_ticker(ticker)
        if not cik:
            return [TextContent(type="text", text=f"Could not find CIK for ticker {ticker}. Please verify the ticker symbol.")]
        
        # Get company facts via EDGAR API
        try:
            company_facts = edgar_client.client.get_company_facts(cik)
        except Exception as e:
            return [TextContent(type="text", text=f"Error fetching company facts for {ticker}: {str(e)}")]
        
        if not company_facts:
            return [TextContent(type="text", text=f"No company facts found for {ticker}.")]
        
        # Extract basic information
        cik = company_facts.get('cik', 'N/A')
        entity_name = company_facts.get('entityName', 'N/A')
        
        # Format output
        output_lines = [
            f"🏢 EDGAR Company Facts for {ticker}:",
            f"📊 Entity Name: {entity_name}",
            f"🔢 CIK: {cik}",
            "=" * 60,
            ""
        ]
        
        # Show available facts/concepts
        facts = company_facts.get('facts', {})
        if facts:
            output_lines.extend([
                "📋 Available Financial Concepts:",
                ""
            ])
            
            # Group by taxonomy
            for taxonomy, concepts in facts.items():
                if concepts:
                    output_lines.extend([
                        f"📊 {taxonomy.upper()} Taxonomy:",
                        f"   📈 Available concepts: {len(concepts)}",
                        ""
                    ])
                    
                    # Show first few concepts as examples
                    concept_names = list(concepts.keys())[:5]
                    for concept in concept_names:
                        concept_data = concepts[concept]
                        description = concept_data.get('description', concept)
                        output_lines.append(f"   • {concept}: {description}")
                    
                    if len(concepts) > 5:
                        output_lines.append(f"   ... and {len(concepts) - 5} more concepts")
                    
                    output_lines.append("")
        
        output_lines.extend([
            "💡 To get specific concept data, use get_edgar_company_concept with:",
            f"   ticker='{ticker}', concept='Assets', taxonomy='us-gaap'"
        ])
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_edgar_company_facts: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_edgar_company_facts: {str(e)}")
        raise ToolError(str(e)) from e

@server.tool()
def get_edgar_company_concept(
    ticker: str,
    concept: str,
    taxonomy: str = "us-gaap"
) -> List[TextContent]:
    """
    EDGAR API経由で企業の特定の財務コンセプトデータを取得
    
    Args:
        ticker: 銘柄ティッカー
        concept: XBRLコンセプト (例: 'Assets', 'Revenues', 'NetIncomeLoss')
        taxonomy: タクソノミー ('us-gaap', 'dei', 'invest')
    """
    try:
        # Validate ticker
        if not validate_ticker(ticker):
            raise ValueError(f"Invalid ticker: {ticker}")
        
        logger.info(f"Fetching EDGAR concept {concept} for {ticker}")
        
        # Get company concept via EDGAR API
        concept_data = edgar_client.get_company_concept(
            ticker=ticker,
            concept=concept,
            taxonomy=taxonomy
        )
        
        if 'error' in concept_data:
            return [TextContent(type="text", text=f"Error: {concept_data['error']}")]
        
        # Extract basic information
        cik = concept_data.get('cik', 'N/A')
        entity_name = concept_data.get('entityName', 'N/A')
        concept_label = concept_data.get('label', concept)
        description = concept_data.get('description', 'N/A')
        
        # Format output
        output_lines = [
            f"📊 EDGAR Company Concept: {ticker} - {concept}",
            f"🏢 Entity: {entity_name} (CIK: {cik})",
            f"📋 Concept: {concept_label}",
            f"📝 Description: {description}",
            f"🏷️ Taxonomy: {taxonomy}",
            "=" * 80,
            ""
        ]
        
        # Show units and values
        units = concept_data.get('units', {})
        if units:
            output_lines.append("📊 Available Data Units:")
            output_lines.append("")
            
            for unit_type, unit_data in units.items():
                output_lines.extend([
                    f"💰 Unit: {unit_type}",
                    f"   📈 Data points: {len(unit_data)}",
                    ""
                ])
                
                # Show recent values
                if unit_data:
                    output_lines.append("   📅 Recent Values:")
                    # Sort by end date (most recent first)
                    sorted_data = sorted(unit_data, key=lambda x: x.get('end', ''), reverse=True)
                    
                    for i, entry in enumerate(sorted_data[:10]):  # Show last 10 entries
                        end_date = entry.get('end', 'N/A')
                        value = entry.get('val', 'N/A')
                        form = entry.get('form', 'N/A')
                        filed = entry.get('filed', 'N/A')
                        
                        # Format large numbers
                        if isinstance(value, (int, float)):
                            if value >= 1_000_000_000:
                                formatted_value = f"${value/1_000_000_000:.2f}B"
                            elif value >= 1_000_000:
                                formatted_value = f"${value/1_000_000:.2f}M"
                            elif value >= 1_000:
                                formatted_value = f"${value/1_000:.2f}K"
                            else:
                                formatted_value = f"${value:,.2f}"
                        else:
                            formatted_value = str(value)
                        
                        output_lines.append(f"   • {end_date}: {formatted_value} ({form} filed: {filed})")
                    
                    if len(sorted_data) > 10:
                        output_lines.append(f"   ... and {len(sorted_data) - 10} more entries")
                
                output_lines.append("")
        else:
            output_lines.append("⚠️ No unit data available for this concept.")
        
        return [TextContent(type="text", text="\n".join(output_lines))]
        
    except (ValueError, TypeError) as e:
        logger.error(f"Validation error in get_edgar_company_concept: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in get_edgar_company_concept: {str(e)}")
        raise ToolError(str(e)) from e


# Register Field Discovery Tools
logger.info("Registering Field Discovery tools...")
register_field_discovery_tools(server)
logger.info("Field Discovery tools registered successfully")

# ---------------------------------------------------------------------------
# Moving Average Position Tool
# ---------------------------------------------------------------------------


@server.tool()
def get_moving_average_position(ticker: str) -> List[TextContent]:
    """Return current price and its percentage distance to 20-, 50-, and 200-day SMAs.

    Args:
        ticker: Stock ticker symbol (e.g. "AAPL").

    Returns:
        Single TextContent with formatted analysis.
    """

    # Validate ticker first
    if not validate_ticker(ticker):
        raise ValueError(f"Invalid ticker: {ticker}")

    # Retrieve fundamentals (full set)
    fundamentals = finviz_client.get_stock_fundamentals(ticker.upper())
    if fundamentals is None:
        return [TextContent(type="text", text=f"No data found for ticker: {ticker.upper()}")]

    # ------------------ ヘルパー: 値取得と float 変換 ------------------------
    def _to_float(val):
        """Convert Finviz numeric string to float.

        Handles:
        • Commas in thousands ("1,234")
        • Percentage signs ("12.3%")
        • Leading/trailing whitespace
        • Literal dash "-" as missing value
        """
        if val in ("-", "", None):
            return None
        try:
            if isinstance(val, (int, float)):
                return float(val)
            str_val = str(val).strip().replace(",", "")
            if str_val.endswith("%"):
                str_val = str_val.rstrip("%")
            return float(str_val)
        except (TypeError, ValueError):
            return None

    def _get_ma(period: int):
        """Return tuple (sma_price, diff_percent) if Finviz provides either.

        Finviz's SMA columns give *percentage distance* of price vs SMA.
        Example: "-3.37%" means price is 3.37 % below the SMA.
        If % is present, convert to absolute SMA value using current price.
        Otherwise assume column already contains SMA price.
        """
        candidate_keys = [
            f"{period}_day_simple_moving_average",
            f"{period}_day_moving_average",
            f"sma_{period}",
            f"sma{period}",
        ]

        raw_value = None
        found_key = None
        for key in candidate_keys:
            if key in fundamentals:
                raw_value = fundamentals.get(key)
                found_key = key
                break
        if raw_value is None:
            # Fallback pattern search
            for key in fundamentals.keys():
                if f"sma{period}" in key.replace("_", ""):
                    raw_value = fundamentals.get(key)
                    found_key = key
                    break

        if raw_value is None:
            return None, None  # not available

        # If the string ends with %, treat as percentage difference
        if isinstance(raw_value, str) and raw_value.strip().endswith('%'):
            diff_percent = _to_float(raw_value)  # after cleaning % we get float
            price_val_local = _to_float(fundamentals.get("price"))  # captured from outer scope – may be None
            if diff_percent is None or price_val_local is None:
                return None, diff_percent
            # Price = SMA * (1 + diff/100)  →  SMA = Price / (1 + diff/100)
            try:
                sma_val = price_val_local / (1 + diff_percent / 100)
            except ZeroDivisionError:
                sma_val = None
            return sma_val, diff_percent

        # Otherwise interpret as absolute SMA price
        sma_val = _to_float(raw_value)
        return sma_val, None

    price_val = _to_float(fundamentals.get("price"))
    ma20_val, diff20 = _get_ma(20)
    ma50_val, diff50 = _get_ma(50)
    ma200_val, diff200 = _get_ma(200)

    def _diff_str(price: Optional[float], ma: Optional[float]):
        if price is None or ma is None or ma == 0:
            return "N/A"
        diff = (price - ma) / ma * 100
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.2f}% {'above' if diff >= 0 else 'below'}"

    # Pre-compute diff text to avoid nested f-strings (Py3.8 compatible)
    def _format_diff(diff_val, price_val_local, ma_val_local):
        if diff_val is None:
            return _diff_str(price_val_local, ma_val_local)
        return f"{diff_val:+.2f}% {'above' if diff_val > 0 else 'below'}"

    diff20_text = _format_diff(diff20, price_val, ma20_val)
    diff50_text = _format_diff(diff50, price_val, ma50_val)
    diff200_text = _format_diff(diff200, price_val, ma200_val)

    lines = [
        f"📐 Moving Average Position for {ticker.upper()}",
        "=" * 60,
        "",
        f"Current Price         : {f'${price_val:.2f}' if price_val is not None else 'N/A'}",
        "-" * 60,
        f"20-Day SMA            : {f'${ma20_val:.2f}' if ma20_val is not None else 'N/A'}",
        f"   → {diff20_text} compared to price",
        "",
        f"50-Day SMA            : {f'${ma50_val:.2f}' if ma50_val is not None else 'N/A'}",
        f"   → {diff50_text} compared to price",
        "",
        f"200-Day SMA           : {f'${ma200_val:.2f}' if ma200_val is not None else 'N/A'}",
        f"   → {diff200_text} compared to price",
    ]

    return [TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Custom Screener Tool
# ---------------------------------------------------------------------------


@server.tool()
def custom_screener(
    filters: str,
    signal: Optional[str] = None,
    order: Optional[str] = None,
    max_results: int = 50,
) -> List[TextContent]:
    """Screen stocks using raw FinViz filter codes for maximum flexibility.

    Unlike the preset screener tools, this accepts raw FinViz filter tokens
    directly so you can combine any filters that FinViz supports.

    Args:
        filters: Comma-separated raw FinViz filter codes.
            Examples:
              "cap_large,fa_div_o3"              - Large cap, dividend > 3%
              "cap_small,fa_pe_u20"              - Small cap, P/E < 20
              "cap_mega,fa_roe_o20,fa_pb_u3"     - Mega cap, ROE > 20%, P/B < 3
              "sec_technology,fa_salesqoq_o25"    - Tech sector, quarterly sales growth > 25%
              "earningsdate_yesterdayafter|todaybefore" - Earnings yesterday after-close through today before-open
            Common filter prefixes:
              cap_  : Market cap (nano/micro/small/mid/large/mega)
              fa_   : Fundamental analysis (pe, div, roe, eps, etc.)
              ta_   : Technical analysis (sma, rsi, pattern, etc.)
              sec_  : Sector
              ind_  : Industry
              geo_  : Country
              sh_   : Share data (price, avgvol, float, etc.)
        signal: Optional FinViz signal identifier (e.g. "ta_topgainers",
            "ta_mostactive", "ta_unusualvolume", "ta_oversold").
        order: Optional sort order. Use a column name for ascending or prefix
            with '-' for descending (e.g. "-marketcap", "change", "-volume").
        max_results: Maximum number of results to return (1-500, default 50).

    Returns:
        List of TextContent with formatted screening results.
    """
    try:
        # --- Validate filters ---
        filter_errors, normalized_filters = validate_and_normalize_raw_filters(filters)
        if filter_errors:
            return [TextContent(type="text", text=f"Filter validation error: {'; '.join(filter_errors)}")]

        # --- Validate optional signal ---
        if signal is not None:
            signal_errors = validate_signal(signal)
            if signal_errors:
                return [TextContent(type="text", text=f"Signal validation error: {'; '.join(signal_errors)}")]

        # --- Validate optional order ---
        if order is not None:
            order_errors = validate_raw_sort_order(order)
            if order_errors:
                return [TextContent(type="text", text=f"Order validation error: {'; '.join(order_errors)}")]

        # --- Validate max_results ---
        if not isinstance(max_results, int) or max_results < 1 or max_results > 500:
            return [TextContent(type="text", text=f"Invalid max_results: {max_results} (must be an integer between 1 and 500)")]

        # --- Execute screening ---
        stocks = finviz_client.screen_stocks_raw(
            filters=normalized_filters,
            signal=signal,
            order=order,
            max_results=max_results,
        )

        if not stocks:
            return [TextContent(type="text", text=f"No stocks found matching filters: {normalized_filters}")]

        # --- Format output ---
        lines = []
        lines.append(f"Custom Screener Results ({len(stocks)} stocks)")
        lines.append("=" * 60)
        lines.append(f"Filters: {normalized_filters}")
        if signal:
            lines.append(f"Signal : {signal}")
        if order:
            lines.append(f"Order  : {order}")
        lines.append("")

        for stock in stocks:
            ticker = getattr(stock, 'ticker', 'N/A')
            company = getattr(stock, 'company_name', 'N/A')
            sector = getattr(stock, 'sector', 'N/A')
            industry = getattr(stock, 'industry', 'N/A')
            price = getattr(stock, 'price', None)
            change = getattr(stock, 'price_change', None)
            volume = getattr(stock, 'volume', None)
            market_cap = getattr(stock, 'market_cap', None)
            pe = getattr(stock, 'pe_ratio', None)
            rel_volume = getattr(stock, 'relative_volume', None)
            dividend_yield = getattr(stock, 'dividend_yield', None)
            eps_surprise = getattr(stock, 'eps_surprise', None)

            price_str = f"${price:.2f}" if price is not None else "N/A"
            change_str = f"{change:+.2f}%" if change is not None else "N/A"
            vol_str = format_large_number(volume) if volume is not None else "N/A"
            mcap_str = format_large_number(market_cap * 1e6) if market_cap is not None else "N/A"
            pe_str = f"{pe:.1f}" if pe is not None else "N/A"
            rv_str = f"{rel_volume:.2f}" if rel_volume is not None else "N/A"

            lines.append(f"{ticker} | {company}")
            lines.append(f"  Sector: {sector} | Industry: {industry}")
            lines.append(f"  Price: {price_str} | Change: {change_str} | Volume: {vol_str}")
            lines.append(f"  Market Cap: {mcap_str} | P/E: {pe_str} | Rel Volume: {rv_str}")

            extras = []
            if dividend_yield is not None:
                extras.append(f"Div Yield: {dividend_yield:.2f}%")
            if eps_surprise is not None:
                extras.append(f"EPS Surprise: {eps_surprise:+.2f}%")
            if extras:
                lines.append(f"  {' | '.join(extras)}")

            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        logger.error(f"Error in custom_screener: {str(e)}")
        raise ToolError(str(e)) from e


server.add_tool(get_options_chain)
