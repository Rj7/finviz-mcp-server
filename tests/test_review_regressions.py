"""Provider failure and exact-projection regressions from candidate review."""
import asyncio
from datetime import datetime
from unittest.mock import Mock
import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from src import server


def response(body):
    value = Mock()
    value.text = body
    value.content = body.encode()
    return value


@pytest.mark.parametrize('body', [
    '<!DOCTYPE html><html>Please log in</html>',
    '{"error":"Your subscription has expired"}',
    'Your subscription has expired',
    'Unrecognized,Columns\n',
    'Ticker,Price\nNVDA,"unterminated',
])
def test_registered_screener_rejects_invalid_provider_csv(monkeypatch, body):
    monkeypatch.setattr(server.finviz_screener, 'api_key', 'fixture')
    monkeypatch.setattr(server.finviz_screener, '_make_request', lambda *a, **kw: response(body))
    with pytest.raises(ToolError, match='UPSTREAM_|PROVIDER_CONTRACT'):
        asyncio.run(server.server.call_tool('get_relative_volume_stocks', {'min_relative_volume':2}))


def test_registered_screener_accepts_valid_empty_csv(monkeypatch):
    monkeypatch.setattr(server.finviz_screener, 'api_key', 'fixture')
    monkeypatch.setattr(server.finviz_screener, '_make_request', lambda *a, **kw: response('Ticker,Price\n'))
    value = asyncio.run(server.server.call_tool('get_relative_volume_stocks', {'min_relative_volume':2}))
    assert value.structuredContent['stocks'] == []
    assert not value.isError


@pytest.mark.parametrize('body', ['{"error":"Your subscription has expired"}', 'Subscription expired', 'Unexpected,Columns\n'])
def test_registered_news_rejects_non_csv_error_bodies(monkeypatch, body):
    monkeypatch.setattr(server.finviz_news, '_make_request', lambda *a, **kw: response(body))
    with pytest.raises(ToolError, match='UPSTREAM_|PROVIDER_CONTRACT'):
        asyncio.run(server.server.call_tool('get_market_news', {}))


def test_registered_news_accepts_valid_empty_csv(monkeypatch):
    monkeypatch.setattr(server.finviz_news, '_make_request', lambda *a, **kw: response('Title,Date,Source,Link\n'))
    value = asyncio.run(server.server.call_tool('get_market_news', {}))
    assert value.structuredContent['rows'] == []
    assert not value.isError


@pytest.mark.parametrize('body', [
    '<html><title>Access Denied</title><body>Access Denied: log in</body></html>',
    '<html><title>SEC.gov | Your Request Originates from an Undeclared Automated Tool</title><body>Request blocked</body></html>',
    '<html><title>SEC.gov | Request Rate Threshold Exceeded</title><body>Try later</body></html>',
])
def test_registered_filing_rejects_sec_denials(monkeypatch, body):
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '1')
    monkeypatch.setattr(server.edgar_client.session, 'get', lambda *a, **kw: response(body))
    with pytest.raises(ToolError, match='UPSTREAM_AUTH|RATE_LIMITED'):
        asyncio.run(server.server.call_tool('get_edgar_filing_content', {'ticker':'NVDA','accession_number':'0001-26-01','primary_document':'report.htm'}))


def test_legitimate_filing_can_discuss_errors(monkeypatch):
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '1')
    monkeypatch.setattr(server.edgar_client.session, 'get', lambda *a, **kw: response('<html><title>Annual Report</title><body>Risk factors: access denied to facilities; accounting error corrected.</body></html>'))
    value = asyncio.run(server.server.call_tool('get_edgar_filing_content', {'ticker':'NVDA','accession_number':'0001-26-01','primary_document':'report.htm'}))
    assert 'accounting error' in value.structuredContent['content']


@pytest.mark.parametrize('name,args', [
    ('get_stock_fundamentals', {'ticker':'NVDA','data_fields':['target_price']}),
    ('get_multiple_stocks_fundamentals', {'tickers':['NVDA','MSFT'],'data_fields':['target_price']}),
])
@pytest.mark.parametrize('fallback', [False, True])
def test_missing_target_price_is_not_current_price(monkeypatch, name, args, fallback):
    frames = iter([
        pd.DataFrame(columns=['Ticker','Price']),
        pd.DataFrame([{'Ticker':'NVDA','Price':100,'EPS (ttm)':2}]),
        pd.DataFrame([{'Ticker':'MSFT','Price':200,'EPS (ttm)':4}]),
    ])
    def fetch(*args, **kwargs):
        if fallback:
            return next(frames)
        return pd.DataFrame([{'Ticker':'NVDA','Price':100,'EPS (ttm)':2},{'Ticker':'MSFT','Price':200,'EPS (ttm)':4}])
    monkeypatch.setattr(server.finviz_client, '_fetch_csv_from_url', fetch)
    value = asyncio.run(server.server.call_tool(name, args))
    for row in value.structuredContent['stocks']:
        assert row['values'] == {'target_price':None}
        assert row['missing_fields'] == ['target_price']


@pytest.mark.parametrize('name,args', [
    ('get_stock_fundamentals', {'ticker':'NVDA','data_fields':['price']}),
    ('get_multiple_stocks_fundamentals', {'tickers':['NVDA','MSFT'],'data_fields':['price']}),
    ('get_stock_fundamentals', {'ticker':'NVDA'}),
])
@pytest.mark.parametrize('bulk_failure', [False, True])
def test_fundamentals_fallback_preserves_identity_and_values(monkeypatch, name, args, bulk_failure):
    frames = iter([
        ValueError('Bulk CSV parsing failed') if bulk_failure else pd.DataFrame(columns=['Ticker','Price']),
        pd.DataFrame([{'Ticker':'NVDA','Price':100}]),
        pd.DataFrame([{'Ticker':'MSFT','Price':200}]),
    ])
    def fetch(*args, **kwargs):
        value = next(frames)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(server.finviz_client, '_fetch_csv_from_url', fetch)
    value = asyncio.run(server.server.call_tool(name, args))
    rows = value.structuredContent['stocks']
    assert [(row['ticker'], row['values']['price']) for row in rows] == (
        [('NVDA',100),('MSFT',200)] if 'tickers' in args else [('NVDA',100)]
    )
    for row in rows:
        assert row['available_fields'] == 1
        assert 'price' not in row['missing_fields']


@pytest.mark.parametrize('tickers', ['%%%not-a-ticker%%%', '', [], ['NVDA','%%%'], ['']])
@pytest.mark.asyncio
async def test_stock_news_invalid_tickers_are_mcp_errors(monkeypatch, tickers):
    from mcp.shared.memory import create_connected_server_and_client_session
    def no_network(*args, **kwargs):
        raise AssertionError('Invalid tickers must not reach the provider')
    monkeypatch.setattr(server.finviz_news, '_make_request', no_network)
    async with create_connected_server_and_client_session(server.server) as client:
        result = await client.call_tool('get_stock_news', {'tickers':tickers})
        assert result.isError is True
        assert any('INVALID_ARGUMENT' in item.text for item in result.content if item.type == 'text')


@pytest.mark.parametrize('tickers', ['nvda, MSFT', ['nvda','MSFT']])
def test_stock_news_accepts_string_and_list_tickers(monkeypatch, tickers):
    date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = f'Title,Date,Source,Link\nRevenue grows,{date},Fixture,https://example.test/news\n'
    monkeypatch.setattr(server.finviz_news, '_make_request', lambda *a, **kw: response(body))
    result = asyncio.run(server.server.call_tool('get_stock_news', {'tickers':tickers}))
    assert not result.isError
    assert len(result.structuredContent['rows']) == 1
    assert result.structuredContent['rows'][0]['ticker'] == 'NVDA,MSFT'
    assert result.structuredContent['rows'][0]['url'] == 'https://example.test/news'


@pytest.mark.asyncio
async def test_real_mcp_session_preserves_errors_and_partial_filing_batches(monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session
    monkeypatch.setattr(server.finviz_screener, 'api_key', 'fixture')
    monkeypatch.setattr(server.finviz_screener, '_make_request', lambda *a, **kw: response('<html>Log in</html>'))
    monkeypatch.setattr(server.finviz_news, '_make_request', lambda *a, **kw: response('{"error":"subscription expired"}'))
    monkeypatch.setattr(server.edgar_client, '_get_cik_from_ticker', lambda _: '1')
    monkeypatch.setattr(server.edgar_client.session, 'get', lambda url, **kw: response('<html><title>Access Denied</title></html>' if 'blocked.htm' in url else '<html><body>Revenue USD 100 million.</body></html>'))
    blocked = {'accession_number':'0001-26-01','primary_document':'blocked.htm'}
    good = {'accession_number':'0001-26-02','primary_document':'report.htm'}
    async with create_connected_server_and_client_session(server.server) as client:
        await client.list_tools()
        for name, args in [
            ('get_relative_volume_stocks', {'min_relative_volume':2}),
            ('get_market_news', {}),
            ('get_edgar_filing_content', {'ticker':'NVDA', **blocked}),
            ('get_multiple_edgar_filing_contents', {'ticker':'NVDA','filings_data':[blocked]}),
        ]:
            result = await client.call_tool(name, args)
            assert result.isError is True, name
            assert any('UPSTREAM_' in item.text or 'PROVIDER_CONTRACT' in item.text for item in result.content if item.type == 'text')
        result = await client.call_tool('get_multiple_edgar_filing_contents', {'ticker':'NVDA','filings_data':[blocked,good]})
        assert result.isError is False
        assert result.structuredContent['partial'] is True
        assert result.structuredContent['failed'] == 1
        assert result.structuredContent['succeeded'] == 1
        assert result.structuredContent['items'][0]['status'] == 'error'
        assert 'Revenue USD 100 million' in result.structuredContent['items'][1]['content']
