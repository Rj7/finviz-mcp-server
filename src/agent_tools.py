"""Data-first replacements for formerly ambiguous or unbounded tool handlers."""
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional
from pydantic import BaseModel, Field
from mcp.server.fastmcp.exceptions import ToolError
from .agent_contracts import Page, digest, page_info, page_position, tool_result, provider_error
from .utils.validators import validate_ticker, validate_tickers, validate_data_fields
from dataclasses import asdict, is_dataclass

Ticker = Annotated[str, Field(description='Stock ticker, e.g. NVDA. This is a read-only data request.')]
Limit = Annotated[int, Field(ge=1,le=100,description='Maximum rows per page; default 25, maximum 100.')]
Cursor = Annotated[Optional[str],Field(max_length=4096,description='next_cursor from the previous result; keep original filters. Omit to restart.')]

class ProviderRows(BaseModel):
    rows: list[dict[str,Any]]
    page: Page
    as_of: Optional[str]=None
    freshness: str='Source timestamps appear on individual records when supplied; quote delay is not established.'

def rows_result(tool,query,rows,limit,cursor):
    if not 1<=limit<=100:raise ToolError('INVALID_ARGUMENT: limit must be 1–100')
    rows=[asdict(r) if is_dataclass(r) else r for r in rows]
    snapshot=digest(rows);offset=page_position(tool,query,snapshot,cursor)
    if offset>len(rows):raise ToolError('INVALID_CURSOR: position exceeds result count')
    for count in range(min(limit,len(rows)-offset),-1,-1):
        try:return tool_result(ProviderRows(rows=rows[offset:offset+count],page=page_info(tool,query,snapshot,offset,count,len(rows))))
        except ToolError:
            if count<=1:raise

def get_sector_performance(sectors:Optional[list[str]]=None,limit:Limit=25,cursor:Cursor=None) -> ProviderRows:
    """Compare sector performance using optional sector display names. Returns provider rows and continuation, not live-price guarantees."""
    from . import server
    return rows_result('sectors',{'sectors':sectors},server.finviz_sector.get_sector_performance(sectors=sectors),limit,cursor)

def get_industry_performance(industries:Optional[list[str]]=None,limit:Limit=25,cursor:Cursor=None) -> ProviderRows:
    """Compare industries by exact provider names. Returns a bounded page; preserve filters when following next_cursor."""
    from . import server
    return rows_result('industries',{'industries':industries},server.finviz_sector.get_industry_performance(industries=industries),limit,cursor)

def get_country_performance(countries:Optional[list[str]]=None,limit:Limit=25,cursor:Cursor=None) -> ProviderRows:
    """Compare provider country aggregates with optional country names and bounded continuation."""
    from . import server
    return rows_result('countries',{'countries':countries},server.finviz_sector.get_country_performance(countries=countries),limit,cursor)

def get_sector_specific_industry_performance(sector:str,limit:Limit=25,cursor:Cursor=None) -> ProviderRows:
    """Compare industries within one Finviz sector token, e.g. technology. Returns paginated provider rows; no timeframe parameter is supported."""
    from . import server
    return rows_result('sector_industries',{'sector':sector},server.finviz_sector.get_sector_specific_industry_performance(sector),limit,cursor)

def get_stock_news(tickers:str|list[str],days_back:int=7,news_type:Literal['all','earnings','analyst','insider','general']='all',limit:Limit=25,cursor:Cursor=None) -> ProviderRows:
    """Read recent stock news with publication dates and exact source URLs where available. Use cursor for additional matching articles."""
    from . import server
    if isinstance(tickers, list):
        if not tickers or any(not validate_ticker(t.strip()) for t in tickers):
            raise ToolError('INVALID_ARGUMENT: supply valid stock tickers')
        provider_tickers = ','.join(tickers)
    else:
        provider_tickers = tickers
    if not validate_tickers(provider_tickers):
        raise ToolError('INVALID_ARGUMENT: supply valid stock tickers')
    return rows_result('stock_news',{'tickers':tickers,'days_back':days_back,'news_type':news_type},server.finviz_news.get_stock_news(provider_tickers,days_back,news_type),limit,cursor)

def get_market_news(days_back:int=3,max_items:Limit=20,cursor:Cursor=None) -> ProviderRows:
    """Read recent broad-market news with source URLs and bounded pages. Missing source URLs are null rather than invented."""
    from . import server
    return rows_result('market_news',{'days_back':days_back},server.finviz_news.get_market_news(days_back,max_items=10000),max_items,cursor)

def get_sector_news(sector:str,days_back:int=5,max_items:Limit=15,cursor:Cursor=None) -> ProviderRows:
    """Read recent provider sector news with publication dates and URLs. Follow next_cursor with the same sector and lookback."""
    from . import server
    return rows_result('sector_news',{'sector':sector,'days_back':days_back},server.finviz_news.get_sector_news(sector,days_back,max_items=10000),max_items,cursor)

class MarketOverview(BaseModel):
    etfs:list[dict[str,Any]]
    statistics:dict[str,Optional[int]]
    errors:dict[str,str]
    partial:bool
    as_of:Optional[str]=None
    retrieved_at:str
    freshness:str='Retrieval time is not quote time. Provider quote timestamps/delays are unavailable.'

def get_market_overview() -> MarketOverview:
    """Read major ETF values and broad market-activity counts. Missing/failed components are explicit, never represented as zero activity. Quote freshness is unknown."""
    from . import server
    errors={};etfs=[];statistics={}
    try:etfs=server.finviz_client.get_multiple_stocks_fundamentals(['SPY','QQQ','DIA','IWM','TLT','GLD'],['ticker','company','price','change','volume','market_cap'])
    except Exception as exc:errors['etfs']=str(provider_error(exc))
    for key,fetch in [('volume_surge_count',server.finviz_screener.volume_surge_screener),('uptrend_count',server.finviz_screener.uptrend_screener),('earnings_this_week_count',lambda:server.finviz_screener.earnings_screener(earnings_date='this_week'))]:
        try:statistics[key]=len(fetch())
        except Exception as exc:statistics[key]=None;errors[key]=str(provider_error(exc))
    if not etfs and all(v is None for v in statistics.values()):raise ToolError('UPSTREAM_UNAVAILABLE: all market overview components failed')
    return tool_result(MarketOverview(etfs=etfs,statistics=statistics,errors=errors,partial=bool(errors),retrieved_at=datetime.now(timezone.utc).isoformat()))

class OptionContract(BaseModel):
    contract: Optional[str] = None
    type: Literal['call','put']
    expiration: str
    strike: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    last_close: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    as_of: Optional[str] = None

class OptionsPage(BaseModel):
    ticker: str
    contracts: list[OptionContract]
    selected_expiration: Optional[str]
    available_expirations: list[str]
    page: Page
    units: dict[str,str]
    freshness: str

def get_options_chain(ticker:Ticker, option_type:Literal['call','put']='call', expiration:Annotated[Optional[str],Field(pattern=r'^\d{4}-\d{2}-\d{2}$',description='Expiry YYYY-MM-DD; default earliest nonexpired expiry present in the provider data.')]=None, limit:Limit=25, cursor:Cursor=None, strike_min:Annotated[Optional[float],Field(ge=0,description='Minimum strike in USD, inclusive.')]=None, strike_max:Annotated[Optional[float],Field(ge=0,description='Maximum strike in USD, inclusive.')]=None) -> OptionsPage:
    """Read a small page of stock option contracts, filtered by verified call/put identity. Defaults to earliest nonexpired expiry. Quote freshness may be unavailable; this is not a trading tool."""
    from . import server
    if not validate_ticker(ticker): raise ToolError('INVALID_ARGUMENT: invalid ticker')
    if not 1<=limit<=100 or option_type not in ('call','put'): raise ToolError('INVALID_ARGUMENT: invalid limit or option_type')
    if strike_min is not None and strike_max is not None and strike_min>strike_max: raise ToolError('INVALID_ARGUMENT: strike_min exceeds strike_max')
    try: rows=server.finviz_options.get_options_chain(ticker,option_type=option_type,expiration=expiration)
    except Exception as exc: raise provider_error(exc) from exc
    expiries=sorted({str(r['expiration']) for r in rows if r.get('expiration')})
    if rows and any(not r.get('expiration') or r.get('type')!=option_type for r in rows): raise ToolError('PROVIDER_CONTRACT: missing expiry or mismatched option side')
    # Expiry-only metadata is small; reject an implausible provider dataset explicitly.
    if len(expiries)>100: raise ToolError('RESPONSE_TOO_LARGE: specify an expiration to narrow more than 100 expiries')
    selected=expiration or next((e for e in expiries if e>=datetime.now(timezone.utc).date().isoformat()),None)
    rows=[r for r in rows if r['expiration']==selected and (strike_min is None or r['strike']>=strike_min) and (strike_max is None or r['strike']<=strike_max)]
    rows.sort(key=lambda r:(r['expiration'],r['strike'],str(r.get('contract') or '')))
    query={'ticker':ticker.upper(),'option_type':option_type,'expiration':expiration,'strike_min':strike_min,'strike_max':strike_max}
    snapshot=digest(rows); offset=page_position('options',query,snapshot,cursor)
    for count in range(min(limit,len(rows)-offset),-1,-1):
        page=OptionsPage(ticker=ticker.upper(),contracts=rows[offset:offset+count],selected_expiration=selected,available_expirations=expiries,page=page_info('options',query,snapshot,offset,count,len(rows)),units={'strike':'USD','bid':'USD per share','ask':'USD per share','volume':'contracts','open_interest':'contracts'},freshness='as_of is the provider last-trade timestamp when supplied; bid/ask quote freshness and delay are not established.')
        try: return tool_result(page)
        except ToolError:
            if count<=1: raise
    raise ToolError('INVALID_CURSOR: page position exceeds current result')

class StockProjection(BaseModel):
    ticker: str
    values: dict[str,Any]
    missing_fields: list[str]
    available_fields: int
    requested_fields: int

class Fundamentals(BaseModel):
    stocks: list[StockProjection]
    fields: list[str]
    units: dict[str,str]
    as_of: Optional[str]=None
    freshness: str='Provider quote timestamps and delays are unavailable.'

def get_stock_fundamentals(ticker:Ticker,data_fields:Annotated[Optional[list[str]],Field(max_length=25,description='Canonical field keys from list_available_fields; default price, pe_ratio and market_cap. Maximum 25.')]=None) -> Fundamentals:
    """Read selected fundamental fields for one stock. Discover canonical keys with list_available_fields; missing values remain null, not zero. Returns requested values and coverage."""
    return get_multiple_stocks_fundamentals([ticker],data_fields)

def get_multiple_stocks_fundamentals(tickers:Annotated[list[str],Field(min_length=1,max_length=5,description='One to five stock tickers, e.g. [NVDA,AAPL].')],data_fields:Annotated[Optional[list[str]],Field(max_length=25,description='Canonical field keys; default price, pe_ratio and market_cap. Maximum 25.')]=None) -> Fundamentals:
    """Compare requested fundamental fields for at most five stocks. Field names govern returned columns and coverage. Null values mean unavailable; source quote freshness is not guaranteed."""
    from . import server
    if not 1<=len(tickers)<=5 or any(not validate_ticker(t) for t in tickers): raise ToolError('INVALID_ARGUMENT: supply one to five valid tickers')
    fields=list(dict.fromkeys(data_fields if data_fields is not None else ['price','pe_ratio','market_cap']))
    if not fields or len(fields)>25 or validate_data_fields(fields): raise ToolError('INVALID_ARGUMENT: choose up to 25 canonical fields from list_available_fields')
    try: rows=server.finviz_client.get_multiple_stocks_fundamentals(tickers,fields)
    except Exception as exc: raise provider_error(exc) from exc
    by_ticker={str(r.get('ticker','')).upper():r for r in rows}
    stocks=[]
    for ticker in tickers:
        row=by_ticker.get(ticker.upper(),{})
        values={f:row.get(f) for f in fields}; missing=[f for f,v in values.items() if v is None]
        stocks.append(StockProjection(ticker=ticker.upper(),values=values,missing_fields=missing,available_fields=len(fields)-len(missing),requested_fields=len(fields)))
    units={f:{'price':'USD per share','pe_ratio':'ratio','market_cap':'USD millions'}.get(f,'provider native; consult describe_field') for f in fields}
    return tool_result(Fundamentals(stocks=stocks,fields=fields,units=units))

class FilingReference(BaseModel):
    accession_number: str = Field(description='Exact accession returned by filing discovery.')
    primary_document: str = Field(description='Exact primary document filename returned by discovery.')
    cursor: Optional[str] = Field(default=None,description='Continuation for this individual document, if any.')

class RelativeVolumeStock(BaseModel):
    ticker: str
    company: Optional[str]
    price: Optional[float]
    price_change: Optional[float]
    volume: Optional[float]
    relative_volume: Optional[float]

class RelativeVolumeResult(BaseModel):
    stocks: list[RelativeVolumeStock]
    threshold: float
    units: dict[str,str]
    as_of: Optional[str]=None
    freshness: str='Provider quote timestamps and delays are unavailable.'

def get_relative_volume_stocks(min_relative_volume:Annotated[float,Field(gt=0,description='Minimum relative volume ratio, e.g. 2 means twice normal volume.')],min_price:Optional[float]=None,sectors:Optional[list[str]]=None,max_results:Limit=25) -> RelativeVolumeResult:
    """Find stocks with elevated relative volume. Returns the measured selection ratio, price, percentage change and volume; missing values are null. Default 25 rows, maximum 100."""
    from . import server
    if not 1<=max_results<=100 or min_relative_volume<=0: raise ToolError('INVALID_ARGUMENT: invalid limit or volume threshold')
    try: rows=server.finviz_screener.screen_stocks({'relative_volume_min':min_relative_volume,'price_min':min_price,'sectors':sectors or []})
    except Exception as exc: raise provider_error(exc) from exc
    rows.sort(key=lambda r:r.relative_volume or 0,reverse=True)
    stocks=[RelativeVolumeStock(ticker=r.ticker,company=r.company_name,price=r.price,price_change=r.price_change,volume=r.volume,relative_volume=r.relative_volume) for r in rows[:max_results]]
    return tool_result(RelativeVolumeResult(stocks=stocks,threshold=min_relative_volume,units={'price':'USD per share','price_change':'percent','volume':'shares','relative_volume':'ratio'}))

class DocumentPage(BaseModel):
    content: str
    metadata: dict[str,Any]
    page: Page
    status: str
    url: str

def get_edgar_filing_content(ticker:Ticker,accession_number:Annotated[str,Field(description='Accession from get_edgar_company_filings, including dashes.')],primary_document:Annotated[str,Field(description='Primary document filename from discovery.')],max_length:Annotated[int,Field(ge=1,le=8000,description='Maximum readable characters per page; default 4000, at most 8000.')]=4000,cursor:Cursor=None) -> DocumentPage:
    """Read a bounded readable SEC filing page. Reuse accession and primary_document from discovery; HTML headers are removed before paging. Follow page.next_cursor to continue."""
    from . import server
    result=server.edgar_client.get_filing_document_content(ticker,accession_number,primary_document,max_length=max_length,cursor=cursor)
    if result.get('status')=='error': raise ToolError(result.get('error','UPSTREAM_UNAVAILABLE'))
    return tool_result(DocumentPage(**result))

class FilingBatch(BaseModel):
    items: list[dict[str,Any]]
    partial: bool
    succeeded: int
    failed: int

def get_multiple_edgar_filing_contents(ticker:Ticker,filings_data:Annotated[list[FilingReference],Field(min_length=1,max_length=5,description='One to five accession/document references from filing discovery; optional per-document cursor.')],max_length:Annotated[int,Field(ge=1,le=1000,description='Readable characters per document page, default/max 1000. Use the single-document tool for larger pages.')]=1000) -> FilingBatch:
    """Read up to five SEC filing pages with per-document errors and continuations. Uses the same readable extraction as single-document reads, not a fixed preview. An all-failed batch is an error."""
    from . import server
    if not 1<=len(filings_data)<=5: raise ToolError('INVALID_ARGUMENT: supply one to five filings')
    items=[]
    for reference in filings_data:
        ref=reference.model_dump() if isinstance(reference,FilingReference) else reference
        value=server.edgar_client.get_filing_document_content(ticker,ref['accession_number'],ref['primary_document'],max_length=max_length,cursor=ref.get('cursor'))
        items.append({**value,'reference':ref})
    failed=sum(i['status']=='error' for i in items)
    if failed==len(items): raise ToolError('UPSTREAM_UNAVAILABLE: all documents failed; '+str(items[0].get('error',''))[:500])
    return tool_result(FilingBatch(items=items,partial=failed>0,succeeded=len(items)-failed,failed=failed))
