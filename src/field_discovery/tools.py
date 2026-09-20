"""Complete paginated field discovery using the sole canonical provider mapping."""
from typing import Annotated, Optional
from collections import Counter
import difflib
from pydantic import BaseModel, Field
from mcp.types import TextContent
from mcp.server.fastmcp.exceptions import ToolError
from ..constants import FINVIZ_COMPREHENSIVE_FIELD_MAPPING
from ..agent_contracts import Page, digest, page_position, page_info, tool_result

class FieldInfo(BaseModel):
    name: str
    label: str
    column_id: int
    category: str
    unit: Optional[str]
    value_type: str

def info(name):
    raw=FINVIZ_COMPREHENSIVE_FIELD_MAPPING[name]
    if name in ('ticker','company','sector','industry','country'): category='Basic Information'
    elif name.startswith('performance'):category='Performance Metrics'
    elif any(k in name for k in ('eps','growth','earnings')):category='Earnings & Growth'
    elif any(k in name for k in ('dividend','ratio','pe_','pb_','ps_','peg')):category='Valuation Metrics'
    elif any(k in name for k in ('sma','rsi','volatility','beta','high','low')):category='Technical Indicators'
    elif any(k in name for k in ('volume','float','short','shares')):category='Trading Data'
    elif any(k in name for k in ('etf','aum','expense','fund_')):category='ETF Specific'
    elif any(k in name for k in ('news','analyst','insider')):category='News & Sentiment'
    else:category='Fundamental Data'
    unit={'price':'USD per share','target_price':'USD per share','pe_ratio':'ratio','pb_ratio':'ratio','market_cap':'USD millions','volume':'shares','average_volume':'thousand shares','relative_volume':'ratio'}.get(name)
    kind='number' if unit else 'provider native; not verified'
    if name in ('ticker','company','sector','industry','country'):kind='string'
    return FieldInfo(name=name,label=raw['csv_name'],column_id=raw['column_id'],category=category,unit=unit,value_type=kind)

class FieldPage(BaseModel):
    fields: list[FieldInfo]
    total: int
    page: Page

def list_available_fields(limit:Annotated[int,Field(ge=1,le=100,description='Fields per page, default 25, maximum 100.')]=25,cursor:Annotated[Optional[str],Field(max_length=4096,description='next_cursor from the previous field page.')]=None) -> FieldPage:
    """Discover every supported fundamental field across pages. Use the exact name in data_fields; each record includes the provider CSV label, category and known units. Follow next_cursor until exhausted."""
    return _page('',None,limit,cursor,'fields')

def _page(keyword,category,limit,cursor,tool):
    if not 1<=limit<=100:raise ToolError('INVALID_ARGUMENT: limit must be 1–100')
    fields=[info(n) for n in sorted(FINVIZ_COMPREHENSIVE_FIELD_MAPPING)]
    fields=[f for f in fields if (not category or category.lower()==f.category.lower()) and (keyword.lower() in f.name.lower() or keyword.lower() in f.label.lower())]
    snapshot=digest(FINVIZ_COMPREHENSIVE_FIELD_MAPPING);query={'keyword':keyword,'category':category}
    offset=page_position(tool,query,snapshot,cursor)
    if offset>len(fields):raise ToolError('INVALID_CURSOR: position exceeds field count')
    selected=fields[offset:offset+limit]
    return tool_result(FieldPage(fields=selected,total=len(fields),page=page_info(tool,query,snapshot,offset,len(selected),len(fields))))

class Categories(BaseModel):
    categories: dict[str,int]
    total: int

def get_field_categories() -> Categories:
    """List field categories with complete counts. Every canonical field belongs to exactly one category; use search_fields with category to inspect its members across pages."""
    counts=dict(Counter(info(n).category for n in FINVIZ_COMPREHENSIVE_FIELD_MAPPING))
    return tool_result(Categories(categories=counts,total=sum(counts.values())))

def describe_field(field_name:Annotated[str,Field(description='Exact canonical field name, e.g. price.')]) -> FieldInfo:
    """Describe one canonical field's source column, category, and verified units/type. Unknown units are explicit, not guessed. Use search_fields to find a valid name."""
    if field_name not in FINVIZ_COMPREHENSIVE_FIELD_MAPPING:raise ToolError('UNKNOWN_FIELD: use search_fields to find a canonical field')
    return tool_result(info(field_name))

def search_fields(keyword:Annotated[str,Field(min_length=1,description='Case-insensitive field name or provider-label substring, e.g. price.')],category:Annotated[Optional[str],Field(description='Category from get_field_categories, or null for all.')]=None,limit:Annotated[int,Field(ge=1,le=100,description='Fields per page; default 25, maximum 100.')]=25,cursor:Annotated[Optional[str],Field(max_length=4096,description='next_cursor from the previous search with the same keyword/category.')]=None) -> FieldPage:
    """Find canonical fields by name or provider-label substring. Returns complete paginated matches, not category samples. Use returned names in fundamentals data_fields."""
    return _page(keyword,category,limit,cursor,'field_search')

class Validation(BaseModel):
    all_valid: bool
    valid_fields: list[str]
    invalid_fields: list[str]
    suggestions: dict[str,list[str]]

def validate_fields(field_names:Annotated[list[str],Field(min_length=1,max_length=100,description='One to 100 exact field keys to validate before a fundamentals call.')]) -> Validation:
    """Check canonical field names before requesting fundamentals. Returns valid/invalid lists and spelling suggestions; performs no provider request."""
    valid=[n for n in field_names if n in FINVIZ_COMPREHENSIVE_FIELD_MAPPING]
    invalid=[n for n in field_names if n not in FINVIZ_COMPREHENSIVE_FIELD_MAPPING]
    return tool_result(Validation(all_valid=not invalid,valid_fields=valid,invalid_fields=invalid,suggestions={n:difflib.get_close_matches(n,FINVIZ_COMPREHENSIVE_FIELD_MAPPING,n=3) for n in invalid}))

def register_field_discovery_tools(server):
    for fn in (list_available_fields,get_field_categories,describe_field,search_fields,validate_fields):server.add_tool(fn)
