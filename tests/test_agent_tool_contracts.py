from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.finviz_client.sec_filings import FinvizSECFilingsClient
from src.finviz_client.edgar_client import EdgarAPIClient

def test_sec_date_formats_and_unparseable_dates():
    client = FinvizSECFilingsClient(api_key='fixture')
    for value in ['9/18/2026','09/18/26','2026-09-18']:
        assert client._parse_date(value).date().isoformat() == '2026-09-18'
    with pytest.raises(ValueError): client._parse_date('not a date')

def test_sec_time_window_and_latest_are_real_source_dates():
    client = FinvizSECFilingsClient(api_key='fixture')
    client.now = lambda: datetime(2026,9,20,tzinfo=timezone.utc)
    client._make_request = lambda *a, **kw: SimpleNamespace(text='Filing Date,Form,Filing,Document\n2/17/2026,10-K,https://sec.gov/a,https://sec.gov/a.htm\n9/18/2026,8-K,https://sec.gov/b,https://sec.gov/b.htm\n8/17/2026,10-Q,https://sec.gov/c,https://sec.gov/c.htm\n')
    assert [f.filing_date for f in client.get_sec_filings('NVDA',days_back=30)] == ['9/18/2026']
    assert client.get_filing_summary('NVDA',days_back=30)['latest_filing_date'] == '9/18/2026'

def test_edgar_filter_precedes_limit():
    client = EdgarAPIClient()
    client._get_cik_from_ticker = lambda ticker: '0001'
    client.client.get_submissions = lambda **kw: {'filings':{'recent':{'form':['4','8-K','10-Q'],'filingDate':['2026-09-19','2026-09-18','2026-08-26'],'accessionNumber':['a','b','c'],'primaryDocument':['a.htm','b.htm','c.htm']}}}
    rows = client.get_company_filings('NVDA',form_types=['10-Q'],max_count=1)
    assert len(rows) == 1
    assert rows[0]['primary_document'] == 'c.htm'

def test_sector_filter_is_not_passed_as_timeframe(monkeypatch):
    from src import server
    def provider(timeframe='1d', sectors=None):
        rows=[{'name':'Technology'},{'name':'Healthcare'}]
        return [r for r in rows if not sectors or r['name'] in sectors]
    monkeypatch.setattr(server.finviz_sector,'get_sector_performance',provider)
    result = server.get_sector_performance(['Technology'])
    assert 'Technology' in str(result.structuredContent)
    assert 'Healthcare' not in str(result.structuredContent)

def test_readable_filing_extraction_before_page_bound(monkeypatch):
    client=EdgarAPIClient()
    client._get_cik_from_ticker=lambda ticker:'0001'
    response=Mock(); response.text='<html><head><script>'+('x'*10000)+'</script></head><body><h1>Revenue</h1><p>Revenue was USD 100 million.</p></body></html>'
    monkeypatch.setattr(client.session,'get',lambda *a,**kw:response)
    result=client.get_filing_document_content('NVDA','0001-26-01','report.htm',max_length=1000)
    assert 'Revenue was USD 100 million.' in result['content']
    assert '<head>' not in result['content']

def test_fundamentals_projection_and_missing_values(monkeypatch):
    from src import server
    monkeypatch.setattr(server.finviz_client,'get_multiple_stocks_fundamentals',lambda *a,**kw:[{'ticker':'NVDA','price':100,'pe_ratio':None,'company':'unused'}])
    result=server.get_multiple_stocks_fundamentals(['NVDA'],['price','pe_ratio'])
    row=result.structuredContent['stocks'][0]
    assert row['values']=={'price':100,'pe_ratio':None}
    assert row['missing_fields']==['pe_ratio']
    assert row['ticker']=='NVDA'

def test_filing_pages_reconstruct_text_and_stale_cursor_fails(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError
    client=EdgarAPIClient(); client._get_cik_from_ticker=lambda ticker:'1'
    response=Mock(); response.text='<body>'+('Revenue USD 100. '*2000)+'</body>'
    monkeypatch.setattr(client.session,'get',lambda *a,**kw:response)
    first=client.get_filing_document_content('NVDA','0001-26-01','report.htm',max_length=1000)
    assert first['page']['has_more'] is True
    cursor=first['page']['next_cursor']; combined=first['content']
    while cursor:
        page=client.get_filing_document_content('NVDA','0001-26-01','report.htm',max_length=1000,cursor=cursor)
        combined+=page['content']; cursor=page['page']['next_cursor']
    assert combined==('Revenue USD 100. '*2000).strip()
    response.text='<body>Changed</body>'
    result=client.get_filing_document_content('NVDA','0001-26-01','report.htm',max_length=1000,cursor=first['page']['next_cursor'])
    assert result['status']=='error'
    assert 'STALE_CURSOR' in result['error']

def test_relative_volume_contains_selection_metric(monkeypatch):
    from src import server
    row=SimpleNamespace(ticker='NVDA',company_name='Nvidia',price=100,price_change=0,volume=123,relative_volume=3.5)
    monkeypatch.setattr(server.finviz_screener,'screen_stocks',lambda *a,**kw:[row])
    result=server.get_relative_volume_stocks(3)
    assert result.structuredContent['stocks'][0]['relative_volume']==3.5
    assert result.structuredContent['stocks'][0]['price_change']==0
    assert result.structuredContent['threshold']==3

@pytest.mark.parametrize('status,code',[(403,'UPSTREAM_AUTH'),(429,'RATE_LIMITED')])
def test_provider_http_failure_is_not_empty_and_does_not_retry(monkeypatch,status,code):
    import requests
    from src.finviz_client.base import FinvizClient
    client=FinvizClient(api_key='fixture'); client.rate_limit_delay=0
    response=requests.Response(); response.status_code=status; response.headers['Retry-After']='60'
    seen=[]
    def request(*a,**kw): seen.append(1); return response
    monkeypatch.setattr(client.session,'get',request)
    with pytest.raises(Exception,match=code): client._fetch_csv_from_url('https://example.test/fixture',{})
    assert len(seen)==1

def test_news_url_preserves_provider_link():
    import pandas as pd
    from src.finviz_client.news import FinvizNewsClient
    client=FinvizNewsClient(api_key='fixture')
    row=pd.Series({'Title':'Headline','Source':'Publisher','Date':'2026-09-18 10:00:00','Link':'https://publisher.test/article'})
    client._parse_news_date_from_csv=lambda _:datetime(2026,9,18)
    news=client._parse_news_from_csv(row,'NVDA',datetime(2026,9,1))
    assert news.url=='https://publisher.test/article'

def test_canonical_fundamental_fields_map_to_actual_csv_columns(monkeypatch):
    import pandas as pd
    from src.finviz_client.base import FinvizClient
    client=FinvizClient(api_key='fixture')
    monkeypatch.setattr(client,'_fetch_csv_from_url',lambda *a,**kw:pd.DataFrame([{'Ticker':'NVDA','P/E':25,'P/B':10,'Price':100}]))
    rows=client.get_multiple_stocks_fundamentals(['NVDA'],['pe_ratio','pb_ratio','price'])
    assert rows[0]['pe_ratio']==25
    assert rows[0]['pb_ratio']==10

def test_catalog_has_english_descriptions_reviewed_annotations_and_described_arguments():
    import asyncio
    from src.server import server
    tools=asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.title
        assert not any('\u3040'<=c<='\u30ff' or '\u4e00'<=c<='\u9fff' for c in tool.description),tool.name
        assert tool.annotations.readOnlyHint is True
        for key,value in tool.inputSchema.get('properties',{}).items():
            assert value.get('description'),(tool.name,key)

def test_field_discovery_pages_cover_the_real_mapping():
    from src.field_discovery.tools import list_available_fields
    from src.constants import FINVIZ_COMPREHENSIVE_FIELD_MAPPING
    seen=set();cursor=None
    while True:
        result=list_available_fields(cursor=cursor)
        data=result.structuredContent
        assert len(data['fields'])<=25
        for field in data['fields']:
            assert field['name'] not in seen
            seen.add(field['name'])
        cursor=data['page']['next_cursor']
        if not cursor:break
    assert seen==set(FINVIZ_COMPREHENSIVE_FIELD_MAPPING)
    assert not any(n.startswith('test_field_') for n in seen)

def test_sector_pages_and_failed_market_stats_are_explicit(monkeypatch):
    from src import server
    from mcp.server.fastmcp.exceptions import ToolError
    monkeypatch.setattr(server.finviz_sector,'get_industry_performance',lambda **kw:[{'industry':f'Industry {i}'} for i in range(160)])
    result=server.get_industry_performance()
    assert len(result.structuredContent['rows'])==25
    assert result.structuredContent['page']['has_more'] is True
    monkeypatch.setattr(server.finviz_client,'get_multiple_stocks_fundamentals',lambda *a,**kw:[{'ticker':'SPY','price':100}])
    def fail(*a,**kw):raise ToolError('UPSTREAM_TIMEOUT: fixture')
    monkeypatch.setattr(server.finviz_screener,'volume_surge_screener',fail)
    monkeypatch.setattr(server.finviz_screener,'uptrend_screener',lambda:[])
    monkeypatch.setattr(server.finviz_screener,'earnings_screener',lambda **kw:[])
    overview=server.get_market_overview().structuredContent
    assert overview['partial'] is True
    assert overview['statistics']['volume_surge_count'] is None
    assert overview['as_of'] is None
