"""Public FastMCP registration hook; no private SDK fields are modified."""
import inspect
import functools
import json
from typing import Annotated, get_origin, get_args, Literal
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, ToolAnnotations
from .agent_contracts import provider_error

LOCAL_FIELDS={'list_available_fields','get_field_categories','describe_field','search_fields','validate_fields'}
PURPOSE={
 'earnings_screener':'Find stocks scheduled to report earnings in a specified period.',
 'get_stock_news':'Read recent news for one or more stock tickers; includes source URLs when supplied.',
 'get_market_news':'Read recent broad-market news, not saved personal research.',
 'get_sector_news':'Read recent provider news associated with one sector.',
 'get_sector_performance':'Compare provider sector performance; sectors filters display names such as Technology.',
 'get_industry_performance':'Compare provider industry performance; optionally filter by industry name.',
 'get_country_performance':'Compare provider country performance; optionally filter by country name.',
 'get_sector_specific_industry_performance':'Compare industries within a sector using a Finviz sector token such as technology. No timeframe parameter is supported.',
 'get_capitalization_performance':'Compare stocks grouped by market-capitalization class.',
 'get_market_overview':'Read an overview of major ETFs and market activity. Retrieval time is not the quote time.',
 'upcoming_earnings_screener':'Find upcoming company earnings reports with optional market-cap and liquidity filters.',
 'get_sec_filings':'Find company SEC filings within a date window, optionally by form; returns source filing/document URLs.',
 'get_major_sec_filings':'Find major SEC reports such as 10-K, 10-Q and 8-K in the requested date window.',
 'get_insider_sec_filings':'Find insider-related SEC forms such as Form 4 in the requested date window.',
 'get_sec_filing_summary':'Summarize the retrieved SEC filings by form and latest source filing date; counts may be capped.',
 'get_edgar_company_filings':'Find SEC EDGAR filings by form and recency; use returned accession_number and primary_document to read a document.',
 'get_edgar_company_facts':'Discover EDGAR financial-statement taxonomies and concepts for a company; use get_edgar_company_concept to read values.',
 'get_edgar_company_concept':'Read reported values for a named EDGAR concept with units and reporting dates. Values from different filings may overlap.',
 'get_moving_average_position':'Compare a stock price with moving averages; returns distances from those averages.',
 'custom_screener':'Screen stocks using validated Finviz raw filters and sorting; use canonical field discovery to interpret output.',
}
PARAM={
 'ticker':'Stock ticker, e.g. NVDA.', 'tickers':'Stock ticker or ticker list, e.g. NVDA and AAPL.',
 'data_fields':'Canonical field names returned by list_available_fields, e.g. price and pe_ratio.',
 'sector':'Finviz sector token, e.g. technology.', 'sectors':'Sector display names, e.g. [Technology, Healthcare].',
 'industries':'Exact industry names returned by the provider, e.g. Semiconductors.', 'countries':'Country display names, e.g. [USA].',
 'days_back':'Lookback in calendar days from the current UTC date, e.g. 30.',
 'form_types':'SEC form identifiers, e.g. [10-K, 10-Q].', 'max_count':'Maximum matching filings, applied after filters.',
 'max_results':'Maximum result rows; default at most 25, maximum 100.', 'max_items':'Maximum news items; default at most 25, maximum 100.',
 'min_price':'Minimum stock price in USD, or a supported Finviz raw price filter.', 'max_price':'Maximum stock price in USD, or a supported Finviz raw price filter.',
 'min_volume':'Minimum trading volume in shares, or a supported Finviz volume filter.',
 'min_avg_volume':'Minimum average volume; raw o500 means over 500 thousand shares.',
 'market_cap':'Finviz market-cap filter, e.g. large or smallover; leave null for no filter.',
 'earnings_date':'Earnings period token, e.g. today_after, tomorrow_before or this_week.',
 'earnings_period':'Earnings period token, e.g. this_week.',
 'news_type':'News category: all, earnings, analyst, insider or general.',
 'filters':'Finviz raw filter string, e.g. cap_largeover,sec_technology.',
 'order':'Finviz sort column with optional descending prefix, e.g. -marketcap.',
 'signal':'Optional Finviz signal token, e.g. unusualvolume.',
 'concept':'SEC XBRL concept, e.g. Assets or Revenues.', 'taxonomy':'SEC XBRL taxonomy, usually us-gaap.',
 'keyword':'Case-insensitive field-name/label substring, e.g. price.', 'category':'Field category from get_field_categories; omit for all categories.',
 'field_name':'Canonical field key, e.g. price.', 'field_names':'Canonical field keys to validate, e.g. [price, pe_ratio].',
}

class ContractFastMCP(FastMCP):
    def add_tool(self,fn,name=None,title=None,description=None,annotations=None,icons=None,meta=None,structured_output=None):
        tool_name=name or fn.__name__
        signature=inspect.signature(fn)
        parameters=[]
        for param in signature.parameters.values():
            annotation=param.annotation
            if annotation is inspect.Parameter.empty: annotation=str
            described=any(getattr(x,'description',None) for x in get_args(annotation)) if get_origin(annotation) is Annotated else False
            if not described:
                explanation=PARAM.get(param.name,f'{param.name.replace("_"," ").capitalize()} filter; use provider-native units. Omit to leave this filter unset.')
                if param.default is not inspect.Parameter.empty: explanation+=f' Default: {param.default!r}.'
                constraints={}
                if param.name in ('max_results','max_items','max_count','limit'): constraints={'ge':1,'le':100}
                if param.name=='days_back':constraints={'ge':1,'le':3650}
                if tool_name=='get_sector_specific_industry_performance' and param.name=='sector':
                    annotation=Literal['basicmaterials','communicationservices','consumercyclical','consumerdefensive','energy','financial','healthcare','industrials','realestate','technology','utilities']
                annotation=Annotated[annotation,Field(description=explanation,**constraints)]
            default=param.default
            if param.name in ('max_results','max_items','max_count','limit') and isinstance(default,int): default=min(default,25)
            parameters.append(param.replace(annotation=annotation,default=default))
        new_signature=signature.replace(parameters=parameters)
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            bound=new_signature.bind(*args,**kwargs);bound.apply_defaults()
            try: result=fn(*bound.args,**bound.kwargs)
            except Exception as exc: raise provider_error(exc) from exc
            if isinstance(result,CallToolResult): return result
            if isinstance(result,list):
                blocks=[b.model_dump(exclude_none=True) for b in result]
                if len(json.dumps({'content':blocks},ensure_ascii=False).encode())>32768:
                    raise ToolError('RESPONSE_TOO_LARGE: reduce max_results/max_items, narrow filters or choose one document')
            return result
        wrapped.__signature__=new_signature
        wrapped.__annotations__={p.name:p.annotation for p in parameters}
        wrapped.__annotations__['return']=signature.return_annotation
        # Legacy TextContent lists are not financial data schemas. New typed
        # handlers advertise their real data model and retain a text fallback.
        legacy=get_origin(signature.return_annotation) is list and 'TextContent' in str(signature.return_annotation)
        purpose=PURPOSE.get(tool_name)
        if not purpose:
            doc=inspect.getdoc(fn) or ''
            purpose=doc if doc.isascii() else f'Screen stocks with the {tool_name.replace("_"," ")} preset. Use custom_screener for explicit filters.'
        if tool_name not in LOCAL_FIELDS and 'freshness' not in purpose.lower(): purpose+=' Read-only provider data; quote freshness may be unavailable.'
        return super().add_tool(wrapped,name=name,title=title or tool_name.replace('_',' ').title(),description=description or purpose,annotations=annotations or ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=tool_name not in LOCAL_FIELDS),icons=icons,meta=meta,structured_output=False if legacy else structured_output)
